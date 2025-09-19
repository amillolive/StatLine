# statline/slapi/scoring.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, cast

from statline.core.adapters import load as load_adapter
from statline.core.calculator import score_row_from_raw as _core_score_row_from_raw
from statline.core.calculator import score_rows_from_raw as _core_score_rows_from_raw
from statline.utils.timing import StageTimes  # optional: callers may pass in

Row = Mapping[str, Any]
Rows = List[Row]
Weights = Dict[str, float]
Context = Dict[str, Dict[str, float]]
Caps = Dict[str, float]


# ──────────────────────────────────────────────────────────────────────────────
# Public request/response shapes (plain Python, framework-agnostic)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoreRowRequest:
    adapter: str
    row: Row
    weights_override: Optional[Weights] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


@dataclass(frozen=True)
class ScoreBatchRequest:
    adapter: str
    rows: Rows  # list of row-like mappings
    weights_override: Optional[Weights] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


# Minimal response aliases so FastAPI (or any web layer) can JSONify directly
ScoreRowResponse = Dict[str, Any]
ScoreBatchResponse = List[Dict[str, Any]]


# ──────────────────────────────────────────────────────────────────────────────
# Adapter resolution
# ──────────────────────────────────────────────────────────────────────────────

_adapter_cache: Dict[str, Any] = {}


def _get_adapter(adapter_key: str) -> Any:
    key = (adapter_key or "").strip()
    if not key:
        raise ValueError("adapter key is required")
    cached = _adapter_cache.get(key.lower())
    if cached is not None:
        return cached
    adp = load_adapter(key)
    _adapter_cache[key.lower()] = adp
    return adp


# ──────────────────────────────────────────────────────────────────────────────
# Scoring entry points (pure call/response)
# ──────────────────────────────────────────────────────────────────────────────


def _ensure_rows(rows: object) -> List[Mapping[str, Any]]:
    """
    Runtime guard that keeps API errors human-friendly without tripping Pylance.
    Accepts 'object' on purpose so isinstance checks are meaningful.
    """
    if not isinstance(rows, list):
        raise TypeError("rows must be a List[Mapping[str, Any]] (e.g., a list of dicts)")

    # Pylance wants a concrete element type; mypy hates redundant List[Any] casts.
    rows_obj = cast(List[object], rows)

    for r in rows_obj:
        if not isinstance(r, Mapping):
            raise TypeError("each row must be a Mapping[str, Any]")

    # Safe narrowing after validation
    return cast(List[Mapping[str, Any]], rows_obj)

def score_row(
    req: ScoreRowRequest,
    *,
    timing: Optional[StageTimes] = None,
) -> ScoreRowResponse:
    """
    Score a single raw row using the given adapter.
    - Adapter is responsible for mapping and derived metrics.
    - weights_override/context/caps_override are optional and adapter-agnostic.
    """
    adp = _get_adapter(req.adapter)
    return _core_score_row_from_raw(
        req.row,
        adp,
        weights_override=req.weights_override,
        context=req.context,
        caps_override=req.caps_override,
        timing=timing,
    )


def score_batch(
    req: ScoreBatchRequest,
    *,
    timing: Optional[StageTimes] = None,
) -> ScoreBatchResponse:
    """
    Score a batch of raw rows using the given adapter.
    Returns a list of per-row results in the same order as input.
    """
    rows_checked = _ensure_rows(req.rows)
    adp = _get_adapter(req.adapter)
    return _core_score_rows_from_raw(
        rows_checked,
        adp,
        weights_override=req.weights_override,
        context=req.context,
        caps_override=req.caps_override,
        timing=timing,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Convenience helpers (lightweight validation, preview)
# ──────────────────────────────────────────────────────────────────────────────

def adapters_available() -> List[str]:
    """
    Useful for a health/metadata endpoint: the keys you can pass as `adapter`.
    Prefers a registry if present; otherwise returns cached keys.
    """
    # Delay import to avoid circulars and keep function cheap in hot paths
    from statline.core.adapters import registry as _registry

    try:
        names_fn = getattr(_registry, "list_names", None)
        if callable(names_fn):
            # names_fn can return many shapes; normalize defensively.
            obj: Any = names_fn()
            if isinstance(obj, (list, tuple, set)):
                return [str(n) for n in cast(Iterable[Any], obj)]
            if isinstance(obj, dict):
                return [str(k) for k in cast(Mapping[Any, Any], obj).keys()]
            if isinstance(obj, str):
                return [obj]
            if hasattr(obj, "__iter__"):
                return [str(n) for n in cast(Iterable[Any], obj)]
            # Unknown shape → fall through to cache below
    except Exception:
        pass

    # Fallback: discovery not available; expose whatever is cached.
    return sorted(set(_adapter_cache.keys()))
