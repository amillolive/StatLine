"""Gateway scoring façade over the process-wide core adapter registry."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Union, cast

from statline.core.adapters import list_adapters as _list_adapters
from statline.core.adapters import load_adapter as _load_adapter
from statline.core.scoring import (
    calculate_pri,
    passes_mapped_filters,
    passes_raw_filters,
    safe_map_batch,
)
from statline.core.types.adapters import CompiledAdapter
from statline.core.types.timing import StageTimes
from statline.gateway.adapters.score_types import (
    ScoreBatchRequest,
    ScoreBatchResponse,
    ScoreRowRequest,
    ScoreRowResponse,
)
from statline.gateway.http.errors import BadRequest, NotFound

Row = Mapping[str, Any]
Weights = Dict[str, float]
WeightsArg = Union[str, Weights]
Penalties = Dict[str, float]
Output = Dict[str, Any]
Filters = Dict[str, Any]
Context = Dict[str, Dict[str, float]]
Caps = Dict[str, float]
InputKind = Literal["raw", "mapped"]
CapsMode = Literal["batch", "row"]


def get_adapter(adapter_key: str) -> CompiledAdapter:
    """Resolve one adapter from the core registry; no gateway-local cache exists."""
    key = (adapter_key or "").strip()
    if not key:
        raise BadRequest("adapter key is required")
    try:
        return _load_adapter(key)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise NotFound(f"Unknown adapter: {key}", detail=str(error)) from error


def _ensure_rows(rows: object) -> List[Mapping[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise BadRequest("rows must be a JSON array of objects")
    checked: List[Mapping[str, Any]] = []
    for row in cast(Sequence[object], rows):
        if not isinstance(row, Mapping):
            raise BadRequest("each row must be a JSON object")
        checked.append(cast(Mapping[str, Any], row))
    return checked


def _score_mapped_rows(
    rows: List[Dict[str, Any]],
    adapter: CompiledAdapter,
    *,
    weights: Optional[WeightsArg],
    penalties_override: Optional[Penalties],
    output: Optional[Output],
    context: Optional[Context],
    caps_override: Optional[Caps],
    caps_mode: CapsMode,
    timing: Optional[StageTimes],
) -> List[Dict[str, Any]]:
    if not rows:
        return []

    if caps_mode == "row":
        results: List[Dict[str, Any]] = []

        for row in rows:
            row_result = calculate_pri(
                row,
                adapter,
                weights=weights,
                penalties_override=penalties_override,
                output=output,
                context=context,
                caps_override=caps_override,
                timing=timing,
            )
            results.append(dict(row_result))

        return results

    batch_results = calculate_pri(
        rows,
        adapter,
        weights=weights,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
        timing=timing,
    )

    return [dict(item) for item in batch_results]


def score_rows(
    adapter_key: str,
    rows: object,
    *,
    input_kind: InputKind = "raw",
    weights: Optional[WeightsArg] = None,
    penalties_override: Optional[Penalties] = None,
    output: Optional[Output] = None,
    filters: Optional[Filters] = None,
    context: Optional[Context] = None,
    caps_override: Optional[Caps] = None,
    caps_mode: CapsMode = "batch",
    timing: Optional[StageTimes] = None,
) -> tuple[CompiledAdapter, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Map/filter/score rows through one canonical gateway pipeline."""
    checked = _ensure_rows(rows)
    adapter = get_adapter(adapter_key)

    try:
        if input_kind == "raw":
            raw_rows = checked
            if filters:
                raw_rows = [
                    row for row in raw_rows if passes_raw_filters(row, filters, adapter=adapter)
                ]
            stage = timing.stage("map_raw") if timing else nullcontext()
            with stage:
                mapped = [dict(row) for row in safe_map_batch(adapter, raw_rows)]
        elif input_kind == "mapped":
            mapped = [dict(row) for row in checked]
        else:
            raise BadRequest("input_kind must be 'raw' or 'mapped'")

        if filters:
            mapped = [row for row in mapped if passes_mapped_filters(row, filters, adapter=adapter)]

        results = _score_mapped_rows(
            mapped,
            adapter,
            weights=weights,
            penalties_override=penalties_override,
            output=output,
            context=context,
            caps_override=caps_override,
            caps_mode=caps_mode,
            timing=timing,
        )
    except BadRequest:
        raise
    except (KeyError, ValueError, TypeError) as error:
        raise BadRequest("Could not score input", detail=str(error)) from error

    return adapter, mapped, results


def score_row(req: ScoreRowRequest, *, timing: Optional[StageTimes] = None) -> ScoreRowResponse:
    _adapter, _mapped, results = score_rows(
        req.adapter,
        [req.row],
        weights=req.weights,
        penalties_override=req.penalties_override,
        output=req.output,
        filters=req.filters,
        context=req.context,
        caps_override=req.caps_override,
        caps_mode="row",
        timing=timing,
    )
    if not results:
        raise BadRequest("row did not match filters; no score was produced")
    return results[0]


def score_batch(
    req: ScoreBatchRequest, *, timing: Optional[StageTimes] = None
) -> ScoreBatchResponse:
    _adapter, _mapped, results = score_rows(
        req.adapter,
        req.rows,
        weights=req.weights,
        penalties_override=req.penalties_override,
        output=req.output,
        filters=req.filters,
        context=req.context,
        caps_override=req.caps_override,
        caps_mode="batch",
        timing=timing,
    )
    return results


def adapters_available() -> List[str]:
    return [str(name) for name in _list_adapters()]


__all__ = [
    "CapsMode",
    "InputKind",
    "adapters_available",
    "get_adapter",
    "score_batch",
    "score_row",
    "score_rows",
]
