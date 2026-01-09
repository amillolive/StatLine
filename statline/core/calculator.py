# statline/core/calculator.py
from __future__ import annotations

from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Union,
    cast,
)

from statline.utils.timing import StageTimes  # optional: pass in for timing breakdowns

from .scoring import calculate_pri

WeightsArg = Optional[Union[str, Dict[str, float]]]
OutputArg = Optional[Dict[str, Any]]


# ──────────────────────────────────────────────────────────────────────────────
# Adapter surface we rely on (compiled YAML adapters compatible)
# ──────────────────────────────────────────────────────────────────────────────

class AdapterProto(Protocol):
    """
    Minimal surface used by the calculator.

    IMPORTANT:
    - Compiled YAML adapters expose `.key` (not `KEY`)
    - They always expose `map_raw(...)`
    - `map_raw_to_metrics(...)` may exist for legacy adapters, so we probe for it at runtime
    """
    @property
    def key(self) -> str: ...

    # Some adapters expose `metrics` (iterable of objects with a `key` attr)
    @property
    def metrics(self) -> Sequence[Any] | Any: ...

    # Required mapping entrypoint for compiled adapters
    def map_raw(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...


# ──────────────────────────────────────────────────────────────────────────────
# Mapping helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sanitize_numeric_metrics(raw_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Coerce string numbers (including '1,23') to float; blank → 0.0.
    Leave non-numeric fields as-is (adapter can ignore them).
    """
    numeric_metrics: Dict[str, Any] = {}
    for k, v in raw_metrics.items():
        if isinstance(v, str):
            s = v.strip()
            if s == "":
                numeric_metrics[k] = 0.0
                continue
            try:
                numeric_metrics[k] = float(s.replace(",", "."))
                continue
            except ValueError:
                # keep original; adapter may treat as non-numeric
                pass
        numeric_metrics[k] = v
    return numeric_metrics


def _get_mapper(adapter: AdapterProto) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """
    Return adapter's mapping function.
    Prefers map_raw_to_metrics if present (legacy), otherwise uses map_raw (compiled adapters).
    """
    fn = getattr(adapter, "map_raw_to_metrics", None)
    if callable(fn):
        return cast(Callable[[Mapping[str, Any]], Mapping[str, Any]], fn)

    fn = getattr(adapter, "map_raw", None)
    if callable(fn):
        return cast(Callable[[Mapping[str, Any]], Mapping[str, Any]], fn)

    raise RuntimeError("Adapter has neither map_raw nor map_raw_to_metrics.")


def safe_map_raw(adapter: AdapterProto, raw_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Map a row through the adapter with numeric sanitization.
    Raises adapter errors transparently, but prints helpful details for SyntaxError.
    """
    mapper = _get_mapper(adapter)
    numeric_metrics = _sanitize_numeric_metrics(raw_metrics)
    try:
        mapped_any = mapper(numeric_metrics)
        mapped = dict(mapped_any)

        # Optional per-adapter sanity
        sanity = getattr(adapter, "sanity", None)
        if callable(sanity):
            sanity(mapped)

        return mapped

    except SyntaxError as se:
        # Provide richer context for adapter expression issues
        print("\n=== Mapping Syntax Error ===")
        print(f"Error: {se}")
        print("Raw metrics (sanitized):", numeric_metrics)
        eval_expr = getattr(adapter, "eval_expr", None)
        if eval_expr:
            print("Eval expression:", eval_expr)
        print("============================\n")
        raise


# ──────────────────────────────────────────────────────────────────────────────
# Pure call/response scoring (no CLI, no I/O)
# ──────────────────────────────────────────────────────────────────────────────

def score_rows_from_raw(
    raw_rows: Iterable[Mapping[str, Any]],
    adapter: AdapterProto,
    *,
    # NOTE: bucket → weight (adapter buckets), not per-metric.
    # Keep this for v2.0 compatibility:
    weights_override: Optional[Dict[str, float]] = None,

    # v2.1-friendly inputs (pass-through to core scoring):
    weights: WeightsArg = None,                          # preset name (e.g. "pri") OR bucket->weight dict
    penalties_override: Optional[Dict[str, float]] = None,
    output: OutputArg = None,

    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[StageTimes] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience: sanitize → adapter.map_raw* → calculate_pri, for a batch.

    - All precedence (caps, weights, efficiency, clamps, penalties) is adapter/spec driven.
    """
    if timing:
        with timing.stage("map_raw"):
            mapped_rows: List[Dict[str, Any]] = [safe_map_raw(adapter, r) for r in raw_rows]
    else:
        mapped_rows = [safe_map_raw(adapter, r) for r in raw_rows]

    return calculate_pri(
        mapped_rows,
        adapter=adapter,
        weights_override=weights_override,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
        _timing=timing,
    )


def score_row_from_raw(
    raw_row: Mapping[str, Any],
    adapter: AdapterProto,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    weights: WeightsArg = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: OutputArg = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[StageTimes] = None,
) -> Dict[str, Any]:
    """Single-row convenience wrapper."""
    rows = score_rows_from_raw(
        [raw_row],
        adapter,
        weights_override=weights_override,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
        timing=timing,
    )
    return rows[0]


__all__ = [
    "AdapterProto",
    "safe_map_raw",
    "score_rows_from_raw",
    "score_row_from_raw",
]
