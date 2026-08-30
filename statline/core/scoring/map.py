# statline/core/calculator.py
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, cast

from statline.core.adapters.compile import build_dataset_context
from statline.core.types.scoring import AdapterProtocol
from statline.core.types.timing import StageTimes

from .score import calculate_pri
from .score import passes_mapped_filters as _passes_mapped_filters
from .score import passes_raw_filters as _passes_raw_filters

WeightsArg = str | dict[str, float] | None
OutputArg = dict[str, Any] | None


def _sanitize_numeric_metrics(raw_metrics: Mapping[str, Any]) -> dict[str, Any]:
    """
    Coerce string numbers, including comma decimals, to float; blank strings to 0.0.
    Non-numeric fields are preserved for adapter dimensions/filters.
    """
    numeric_metrics: dict[str, Any] = {}
    for k, v in raw_metrics.items():
        if isinstance(v, str):
            s = v.strip()
            if s == "":
                numeric_metrics[k] = 0.0
                continue
            try:
                parsed = float(s.replace(",", "."))
            except ValueError:
                parsed = None
            if parsed is not None:
                numeric_metrics[k] = parsed
                continue
        numeric_metrics[k] = v
    return numeric_metrics


def _get_mapper(adapter: AdapterProtocol) -> Callable[..., Mapping[str, Any]]:
    mapper = getattr(adapter, "map_raw", None)
    if callable(mapper):
        return cast(Callable[..., Mapping[str, Any]], mapper)
    raise RuntimeError("Adapter has no map_raw function.")


def safe_map_raw(
    adapter: AdapterProtocol,
    raw_metrics: Mapping[str, Any],
    *,
    dataset_context: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Map one raw row after tolerant numeric sanitization.

    If no batch context is supplied, the row is treated as a one-row dataset so
    dataset aggregate expressions remain well-defined.
    """
    mapper = _get_mapper(adapter)
    aggregate_context = dataset_context or build_dataset_context([raw_metrics])
    numeric_metrics = _sanitize_numeric_metrics(raw_metrics)
    try:
        mapped_any = mapper(numeric_metrics, dataset_context=aggregate_context)
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


def safe_map_batch(
    adapter: AdapterProtocol, raw_rows: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Map a raw batch with one shared, precomputed dataset aggregate context."""
    rows = list(raw_rows)
    dataset_context = build_dataset_context(rows)
    return [safe_map_raw(adapter, row, dataset_context=dataset_context) for row in rows]


def score_rows_from_raw(
    raw_rows: Iterable[Mapping[str, Any]],
    adapter: AdapterProtocol,
    *,
    weights_override: dict[str, float] | None = None,
    weights: WeightsArg = None,
    penalties_override: dict[str, float] | None = None,
    output: OutputArg = None,
    context: dict[str, dict[str, float]] | None = None,
    caps_override: dict[str, float] | None = None,
    timing: StageTimes | None = None,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Convenience API: raw rows -> adapter mapping -> canonical PRI scoring.
    """
    raw_list: list[Mapping[str, Any]] = list(raw_rows)
    if filters:
        raw_list = [r for r in raw_list if _passes_raw_filters(r, filters, adapter=adapter)]

    if timing:
        with timing.stage("map_raw"):
            mapped_rows = safe_map_batch(adapter, raw_list)
    else:
        mapped_rows = safe_map_batch(adapter, raw_list)

    if filters:
        mapped_rows = [
            r for r in mapped_rows if _passes_mapped_filters(r, filters, adapter=adapter)
        ]
    return calculate_pri(
        mapped_rows,
        adapter=adapter,
        weights_override=weights_override,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
        timing=timing,
    )


def score_row_from_raw(
    raw_row: Mapping[str, Any],
    adapter: AdapterProtocol,
    *,
    weights_override: dict[str, float] | None = None,
    weights: WeightsArg = None,
    penalties_override: dict[str, float] | None = None,
    output: OutputArg = None,
    context: dict[str, dict[str, float]] | None = None,
    caps_override: dict[str, float] | None = None,
    timing: StageTimes | None = None,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    "safe_map_batch",
    "safe_map_raw",
    "score_row_from_raw",
    "score_rows_from_raw",
]
