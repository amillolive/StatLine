"""Gateway scoring façade over the process-wide core adapter registry."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from typing import Any, Literal, cast

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
Weights = dict[str, float]
WeightsArg = str | Weights
Penalties = dict[str, float]
Output = dict[str, Any]
Filters = dict[str, Any]
Context = dict[str, dict[str, float]]
Caps = dict[str, float]
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


def _ensure_rows(rows: object) -> list[Mapping[str, Any]]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise BadRequest("rows must be a JSON array of objects")
    checked: list[Mapping[str, Any]] = []
    for row in cast(Sequence[object], rows):
        if not isinstance(row, Mapping):
            raise BadRequest("each row must be a JSON object")
        checked.append(cast(Mapping[str, Any], row))
    return checked


def _score_mapped_rows(
    rows: list[dict[str, Any]],
    adapter: CompiledAdapter,
    *,
    weights: WeightsArg | None,
    penalties_override: Penalties | None,
    output: Output | None,
    context: Context | None,
    caps_override: Caps | None,
    caps_mode: CapsMode,
    timing: StageTimes | None,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    if caps_mode == "row":
        results: list[dict[str, Any]] = []

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
    weights: WeightsArg | None = None,
    penalties_override: Penalties | None = None,
    output: Output | None = None,
    filters: Filters | None = None,
    context: Context | None = None,
    caps_override: Caps | None = None,
    caps_mode: CapsMode = "batch",
    timing: StageTimes | None = None,
) -> tuple[CompiledAdapter, list[dict[str, Any]], list[dict[str, Any]]]:
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


def score_row(req: ScoreRowRequest, *, timing: StageTimes | None = None) -> ScoreRowResponse:
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


def score_batch(req: ScoreBatchRequest, *, timing: StageTimes | None = None) -> ScoreBatchResponse:
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


def adapters_available() -> list[str]:
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
