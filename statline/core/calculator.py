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

from statline.utils.timing import StageTimes

from .scoring import calculate_pri
from .scoring import passes_raw_filters as _passes_raw_filters

WeightsArg = Optional[Union[str, Dict[str, float]]]
OutputArg = Optional[Dict[str, Any]]


class AdapterProto(Protocol):
    """Minimal adapter surface used by the raw-row calculator."""

    @property
    def key(self) -> str: ...

    @property
    def metrics(self) -> Sequence[Any] | Any: ...

    def map_raw(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _sanitize_numeric_metrics(raw_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Coerce string numbers, including comma decimals, to float; blank strings to 0.0.
    Non-numeric fields are preserved for adapter dimensions/filters.
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
                pass
        numeric_metrics[k] = v
    return numeric_metrics


def _get_mapper(adapter: AdapterProto) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """
    Return the adapter's mapping function.
    Prefer legacy map_raw_to_metrics when present; otherwise use compiled-adapter map_raw.
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
    Map a raw row through the adapter after tolerant numeric sanitization.
    """
    mapper = _get_mapper(adapter)
    numeric_metrics = _sanitize_numeric_metrics(raw_metrics)
    try:
        mapped_any = mapper(numeric_metrics)
        mapped = dict(mapped_any)

        sanity = getattr(adapter, "sanity", None)
        if callable(sanity):
            sanity(mapped)

        return mapped

    except SyntaxError as se:
        print("\n=== Mapping Syntax Error ===")
        print(f"Error: {se}")
        print("Raw metrics (sanitized):", numeric_metrics)
        eval_expr = getattr(adapter, "eval_expr", None)
        if eval_expr:
            print("Eval expression:", eval_expr)
        print("============================\n")
        raise


def score_rows_from_raw(
    raw_rows: Iterable[Mapping[str, Any]],
    adapter: AdapterProto,
    *,
    weights_override: Optional[Dict[str, float]] = None,
    weights: WeightsArg = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: OutputArg = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
    timing: Optional[StageTimes] = None,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Convenience API: raw rows -> adapter mapping -> canonical PRI scoring.
    """
    raw_list: List[Mapping[str, Any]] = list(raw_rows)
    if filters:
        raw_list = [r for r in raw_list if _passes_raw_filters(r, filters, adapter=adapter)]

    if timing:
        with timing.stage("map_raw"):
            mapped_rows: List[Dict[str, Any]] = [safe_map_raw(adapter, r) for r in raw_list]
    else:
        mapped_rows = [safe_map_raw(adapter, r) for r in raw_list]

    return cast(
        List[Dict[str, Any]],
        calculate_pri(
            mapped_rows,
            adapter=adapter,
            weights_override=weights_override,
            weights=weights,
            penalties_override=penalties_override,
            output=output,
            context=context,
            caps_override=caps_override,
            timing=timing,
        ),
    ) # pyright: ignore[reportUnnecessaryCast]


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
    filters: Optional[Dict[str, Any]] = None,
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
        filters=filters,
    )
    if not rows:
        raise ValueError("row did not match filters; no score was produced")
    return rows[0]


__all__ = [
    "AdapterProto",
    "safe_map_raw",
    "score_rows_from_raw",
    "score_row_from_raw",
]
