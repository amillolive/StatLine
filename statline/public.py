"""Stable public SDK surface for local StatLine usage.

The goal of this module is to give bots, dashboards, notebooks, and other Python
apps a small, boring API over the richer internal implementation.
"""
from __future__ import annotations

from collections.abc import Iterable as AbcIterable
from collections.abc import Mapping as AbcMapping
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union, cast

from statline.core.adapters import CompiledAdapter, list_names
from statline.core.adapters import load_adapter as _load_adapter
from statline.core.calculator import safe_map_raw, score_row_from_raw, score_rows_from_raw
from statline.datasets import list_datasets, load_dataset

Row = Dict[str, Any]
Rows = List[Row]
AdapterLike = Union[str, CompiledAdapter, Any]
WeightsArg = Optional[Union[str, Dict[str, float]]]
OutputArg = Optional[Dict[str, Any]]


def list_adapters() -> List[str]:
    """Return available adapter keys."""
    return list_names()


def load_adapter(name: str) -> CompiledAdapter:
    """Load a compiled adapter by key or alias."""
    return _load_adapter(name)


def _resolve_adapter(adapter: AdapterLike) -> Any:
    if isinstance(adapter, str):
        return load_adapter(adapter)
    if hasattr(adapter, "map_raw") or hasattr(adapter, "map_raw_to_metrics"):
        return adapter
    raise TypeError("adapter must be an adapter key or a compiled adapter object")


def map_row(adapter: AdapterLike, row: Mapping[str, Any]) -> Row:
    """Map one raw row into adapter canonical metrics."""
    return dict(safe_map_raw(_resolve_adapter(adapter), row))


def map_batch(adapter: AdapterLike, rows: Iterable[Mapping[str, Any]]) -> Rows:
    """Map many raw rows into adapter canonical metrics."""
    adp = _resolve_adapter(adapter)
    return [dict(safe_map_raw(adp, row)) for row in rows]


def score_row(
    adapter: AdapterLike,
    row: Mapping[str, Any],
    *,
    weights: WeightsArg = None,
    weights_override: Optional[Dict[str, float]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: OutputArg = None,
    filters: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> Row:
    """Score one raw row with an adapter."""
    return dict(
        score_row_from_raw(
            row,
            _resolve_adapter(adapter),
            weights=weights,
            weights_override=weights_override,
            penalties_override=penalties_override,
            output=output,
            filters=filters,
            context=context,
            caps_override=caps_override,
        )
    )


def score_batch(
    adapter: AdapterLike,
    rows: Iterable[Mapping[str, Any]],
    *,
    weights: WeightsArg = None,
    weights_override: Optional[Dict[str, float]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: OutputArg = None,
    filters: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> Rows:
    """Score many raw rows with an adapter."""
    return [
        dict(item)
        for item in score_rows_from_raw(
            rows,
            _resolve_adapter(adapter),
            weights=weights,
            weights_override=weights_override,
            penalties_override=penalties_override,
            output=output,
            filters=filters,
            context=context,
            caps_override=caps_override,
        )
    ]


def score(
    adapter: AdapterLike,
    data: Union[Mapping[str, Any], Iterable[Mapping[str, Any]]],
    *,
    mode: str = "auto",
    weights: WeightsArg = None,
    weights_override: Optional[Dict[str, float]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: OutputArg = None,
    filters: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> Union[Row, Rows]:
    """Score raw StatLine data using a compact SDK-style API.

    Examples:
        adapter = load_adapter("eba_players")
        data = load_dataset("EBA_Elevate302/eba_s1_players")
        results = score(adapter, data, mode="batch")

        one = score("demo", {"ppg": 20, "apg": 6}, mode="row")

    ``mode="auto"`` treats mappings as a single row and all other iterables as a
    batch. Use ``mode="row"`` or ``mode="batch"`` when calling from application
    code where predictable return types matter.
    """
    mode_l = str(mode or "auto").strip().lower()
    if mode_l not in {"auto", "row", "single", "batch", "rows"}:
        raise ValueError("mode must be one of: auto, row, single, batch, rows")

    if mode_l == "auto":
        mode_l = "row" if isinstance(data, AbcMapping) else "batch"

    if mode_l in {"row", "single"}:
        if not isinstance(data, AbcMapping):
            raise TypeError("row mode requires a mapping/dict row")
        return score_row(
            adapter,
            cast(Mapping[str, Any], data),
            weights=weights,
            weights_override=weights_override,
            penalties_override=penalties_override,
            output=output,
            filters=filters,
            context=context,
            caps_override=caps_override,
        )

    if isinstance(data, AbcMapping):
        raise TypeError("batch mode requires an iterable of row mappings, not one mapping")

    if not isinstance(data, AbcIterable): # pyright: ignore[reportUnnecessaryIsInstance]
        raise TypeError("batch mode requires an iterable of row mappings")

    return score_batch(
        adapter,
        cast(Iterable[Mapping[str, Any]], data), # pyright: ignore[reportUnnecessaryCast]
        weights=weights,
        weights_override=weights_override,
        penalties_override=penalties_override,
        output=output,
        filters=filters,
        context=context,
        caps_override=caps_override,
    )


__all__ = [
    "CompiledAdapter",
    "Row",
    "Rows",
    "load_adapter",
    "list_adapters",
    "load_dataset",
    "list_datasets",
    "map_row",
    "map_batch",
    "score",
    "score_row",
    "score_batch",
]
