# statline/core/calculator.py
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, cast

from statline.utils.timing import StageTimes  # optional: pass in for timing breakdowns

from .scoring import calculate_pri

# ──────────────────────────────────────────────────────────────────────────────
# Adapter surface we rely on
# ──────────────────────────────────────────────────────────────────────────────

class AdapterProto(Protocol):
    """Minimal surface used by the calculator."""
    KEY: str

    # Some adapters expose `metrics` (iterable of objects with a `key` attr)
    metrics: Sequence[Any] | Any

    # Adapters may provide either of these mapping functions.
    def map_raw_to_metrics(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def map_raw(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...

    # Optional sanity hook some adapters provide:
    def sanity(self, metrics: Mapping[str, Any]) -> None: ...  # pragma: no cover


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
    """Return adapter's mapping function (prefers map_raw_to_metrics)."""
    if hasattr(adapter, "map_raw_to_metrics") and callable(getattr(adapter, "map_raw_to_metrics")):
        return cast(Callable[[Mapping[str, Any]], Mapping[str, Any]], getattr(adapter, "map_raw_to_metrics"))
    if hasattr(adapter, "map_raw") and callable(getattr(adapter, "map_raw")):
        return cast(Callable[[Mapping[str, Any]], Mapping[str, Any]], getattr(adapter, "map_raw"))
    raise RuntimeError("Adapter has neither map_raw nor map_raw_to_metrics.")


def safe_map_raw(adapter: AdapterProto, raw_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Map a row through the adapter with numeric sanitization.
    Raises any adapter error transparently, but prints helpful details for SyntaxError.
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
    # NOTE: This is bucket → weight (adapter buckets), not per-metric.
    weights_override: Optional[Dict[str, float]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[StageTimes] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience: sanitize → adapter.map_raw* → calculate_pri, for a batch.
    - No team wins/losses: if that signal matters, the adapter should derive it as a metric.
    - All precedence (caps, weights, efficiency, clamps) is adapter/spec driven.
    """
    # Optional timing for the mapping stage
    if timing:
        with timing.stage("map_raw"):
            mapped_rows: List[Dict[str, Any]] = [safe_map_raw(adapter, r) for r in raw_rows]
    else:
        mapped_rows = [safe_map_raw(adapter, r) for r in raw_rows]

    # Score using the core PRI implementation
    return calculate_pri(
        mapped_rows,
        adapter=adapter,
        weights_override=weights_override,
        context=context,
        caps_override=caps_override,
        _timing=timing,
    )


def score_row_from_raw(
    raw_row: Mapping[str, Any],
    adapter: AdapterProto,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[StageTimes] = None,
) -> Dict[str, Any]:
    """Single-row convenience wrapper."""
    rows = score_rows_from_raw(
        [raw_row],
        adapter,
        weights_override=weights_override,
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
