# statline/slapi/scoring.py
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union, cast

from statline.core.adapters import load_adapter as _load_adapter
from statline.core.scoring import calculate_pri_batch as _core_pri_batch
from statline.core.scoring import calculate_pri_single as _core_pri_single
from statline.slapi.errors import BadRequest, NotFound
from statline.utils.timing import StageTimes  # optional: callers may pass in

Row = Mapping[str, Any]
Rows = List[Row]

Weights = Dict[str, float]  # bucket -> weight
WeightsArg = Union[str, Weights]  # preset name OR bucket->weight override
Penalties = Dict[str, float]  # bucket -> penalty
Output = Dict[str, Any]
Filters = Dict[str, Any]

Context = Dict[str, Dict[str, float]]
Caps = Dict[str, float]


@dataclass(frozen=True)
class ScoreRowRequest:
    adapter: str
    row: Row
    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


@dataclass(frozen=True)
class ScoreBatchRequest:
    adapter: str
    rows: Rows
    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


ScoreRowResponse = Dict[str, Any]
ScoreBatchResponse = List[Dict[str, Any]]


_adapter_cache: Dict[str, Any] = {}
_adapter_lock = RLock()


def _cache_key(adapter_key: str) -> str:
    return (adapter_key or "").strip().lower()


def _get_adapter(adapter_key: str) -> Any:
    key_raw = (adapter_key or "").strip()
    if not key_raw:
        raise BadRequest("adapter key is required")

    key = _cache_key(key_raw)
    with _adapter_lock:
        cached = _adapter_cache.get(key)
        if cached is not None:
            return cached

    try:
        adp = _load_adapter(key_raw)
    except Exception as e:
        # Treat adapter load failures as a 404 at the SLAPI layer.
        raise NotFound(f"Unknown adapter: {key_raw}", detail=str(e)) from e

    with _adapter_lock:
        _adapter_cache[key] = adp
    return adp


def _ensure_rows(rows: object) -> List[Mapping[str, Any]]:
    # Be strict: our API contract is "list of objects" (JSON array of dict-like rows).
    if not isinstance(rows, list):
        raise BadRequest("rows must be a List[Mapping[str, Any]] (e.g., a list of dicts)")

    rows_obj = cast(List[object], rows)
    for r in rows_obj:
        if not isinstance(r, Mapping):
            raise BadRequest("each row must be a Mapping[str, Any]")

    return cast(List[Mapping[str, Any]], rows_obj)


def score_row(req: ScoreRowRequest, *, timing: Optional[StageTimes] = None) -> ScoreRowResponse:
    adp = _get_adapter(req.adapter)

    try:
        res = _core_pri_single(
            adapter=adp,
            row=req.row,
            weights=req.weights,
            output=req.output,
            filters=req.filters,
            penalties_override=req.penalties_override,
            context=req.context,
            caps_override=req.caps_override,
            timing=timing,
        )
    except (KeyError, ValueError, TypeError) as e:
        # Scoring failures that are almost always caller input issues
        raise BadRequest("Could not score row", detail=str(e)) from e
    except Exception:
        # Let truly unexpected exceptions bubble; app can map to 500.
        raise

    # PRIResult -> dict
    return {"pri": res.pri, **dict(res.details)}


def score_batch(req: ScoreBatchRequest, *, timing: Optional[StageTimes] = None) -> ScoreBatchResponse:
    rows_checked = _ensure_rows(req.rows)
    adp = _get_adapter(req.adapter)

    try:
        res_list = _core_pri_batch(
            adapter=adp,
            rows=rows_checked,
            weights=req.weights,
            output=req.output,
            filters=req.filters,
            penalties_override=req.penalties_override,
            context=req.context,
            caps_override=req.caps_override,
            timing=timing,
        )
    except (KeyError, ValueError, TypeError) as e:
        raise BadRequest("Could not score batch", detail=str(e)) from e
    except Exception:
        raise

    return [{"pri": r.pri, **dict(r.details)} for r in res_list]


def adapters_available() -> List[str]:
    """
    Best-effort list of adapter names. If the registry fails for any reason,
    fall back to whatever we've already loaded into the cache.
    """
    try:
        from statline.core.adapters import list_names as _list_names  # runtime import
        names = _list_names()
        if isinstance(names, (list, tuple, set)): # pyright: ignore[reportUnnecessaryIsInstance]
            return [str(n) for n in cast(Iterable[Any], names)]
        if isinstance(names, dict): # pyright: ignore[reportUnnecessaryIsInstance]
            return [str(k) for k in cast(Mapping[Any, Any], names).keys()]
        if isinstance(names, str): # pyright: ignore[reportUnnecessaryIsInstance]
            return [names]
        if hasattr(names, "__iter__"):
            return [str(n) for n in cast(Iterable[Any], names)]
    except Exception:
        pass

    with _adapter_lock:
        return sorted(_adapter_cache.keys())
