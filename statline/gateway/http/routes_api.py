"""Condensed public and user-facing v4 HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from statline import __version__
from statline.core.adapters import adapter_cache_info, sniff_adapters
from statline.gateway.adapters.scoring import score_rows
from statline.gateway.http.dependencies import SCOPE_USERBASE, require_scope
from statline.gateway.http.errors import NotFound
from statline.gateway.http.models import (
    AdapterCatalog,
    AdapterOut,
    ApiIndexOut,
    DatasetCatalog,
    DatasetPage,
    HealthOut,
    ScoreIn,
    ScoreOut,
    ScoreSource,
    SniffIn,
    SniffOut,
)
from statline.gateway.http.resources import (
    adapter_catalog,
    adapter_document,
    dataset_catalog,
    dataset_page,
    dataset_rows,
)

public_router = APIRouter(tags=["service"])
api_router = APIRouter(
    prefix="/v4",
    dependencies=[Depends(require_scope(SCOPE_USERBASE))],
)


@public_router.get(
    "/",
    response_model=ApiIndexOut,
    summary="API index",
    operation_id="api_index",
)
def root() -> dict[str, Any]:
    return {
        "name": "StatLine API",
        "version": __version__,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/v4/health",
        "resources": {
            "adapters": "/v4/adapters",
            "datasets": "/v4/datasets",
            "score": "/v4/score",
        },
    }


@public_router.get(
    "/v4/health",
    response_model=HealthOut,
    summary="Health check",
    operation_id="health_v4",
)
def health() -> dict[str, Any]:
    cache = adapter_cache_info()
    return {"ok": True, "version": __version__, "adapters": cache["adapters"]}


@api_router.get(
    "/adapters",
    response_model=AdapterCatalog,
    tags=["adapters"],
    summary="List adapters",
    description="Returns every canonical adapter once plus process-cache diagnostics.",
    operation_id="list_adapters_v4",
)
def adapters() -> dict[str, Any]:
    return adapter_catalog()


@api_router.get(
    "/adapters/{adapter}",
    response_model=AdapterOut,
    tags=["adapters"],
    summary="Describe an adapter",
    description=(
        "Replaces the former weights, metric-keys, inputs, dimensions, filters, traits, "
        "and spec endpoints with one document."
    ),
    operation_id="get_adapter_v4",
)
def adapter(adapter: str) -> dict[str, Any]:
    try:
        return adapter_document(adapter)
    except (FileNotFoundError, KeyError, ValueError) as error:
        raise NotFound(f"Unknown adapter: {adapter}", detail=str(error)) from error


@api_router.post(
    "/adapters/sniff",
    response_model=SniffOut,
    tags=["adapters"],
    summary="Match adapters from headers",
    operation_id="sniff_adapters_v4",
)
def sniff(body: SniffIn) -> dict[str, list[str]]:
    return {"adapters": sniff_adapters(body.headers)}


@api_router.get(
    "/datasets",
    response_model=DatasetCatalog,
    tags=["datasets"],
    summary="List packaged datasets",
    description="Lists CSV resources and the adapters that declare each dataset.",
    operation_id="list_datasets_v4",
)
def datasets() -> dict[str, Any]:
    return dataset_catalog()


@api_router.get(
    "/datasets/{dataset:path}",
    response_model=DatasetPage,
    tags=["datasets"],
    summary="Read a dataset as JSON",
    description=(
        "Returns a safe, paginated JSON view of a packaged CSV. Use the returned rows "
        "as the `rows` value in POST /v4/score for the documented two-step flow."
    ),
    operation_id="get_dataset_v4",
)
def dataset(
    dataset: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=50000),
    coerce_numbers: bool = Query(default=True),
) -> dict[str, Any]:
    try:
        return dataset_page(
            dataset,
            offset=offset,
            limit=limit,
            coerce_numbers=coerce_numbers,
        )
    except FileNotFoundError as error:
        raise NotFound(f"Unknown dataset: {dataset}", detail=str(error)) from error


def _score_request(body: ScoreIn) -> dict[str, Any]:
    """Run synchronous dataset I/O and CPU scoring away from the async event loop."""
    source: ScoreSource
    rows: list[dict[str, Any]]

    if body.row is not None:
        source = ScoreSource(kind="row")
        rows = [dict(body.row)]
    elif body.rows is not None:
        source = ScoreSource(kind="rows")
        rows = [dict(row) for row in body.rows]
    else:
        assert body.dataset is not None
        try:
            canonical, rows = dataset_rows(body.dataset, limit=body.dataset_limit)
        except FileNotFoundError as error:
            raise NotFound(f"Unknown dataset: {body.dataset}", detail=str(error)) from error
        source = ScoreSource(kind="dataset", dataset=canonical)

    adapter, mapped, results = score_rows(
        body.adapter,
        rows,
        input_kind=body.input_kind,
        weights=body.weights,
        penalties_override=body.penalties_override,
        output=body.output,
        profiles=body.profiles,
        filters=body.filters,
        context=body.context,
        caps_override=body.caps_override,
        caps_mode=body.caps_mode,
    )

    return {
        "adapter": adapter.key,
        "adapter_version": adapter.version,
        "source": source.model_dump(),
        "input_count": len(rows),
        "mapped_count": len(mapped),
        "scored_count": len(results),
        "results": results,
        "mapped": mapped if body.include_mapped else None,
    }


@api_router.post(
    "/score",
    response_model=ScoreOut,
    tags=["scoring"],
    summary="Score a row, batch, or dataset",
    description=(
        "The v4 scoring pipeline replaces /map, /calc/pri, /pri, /score/row, and "
        "/score/batch. Supply exactly one of `row`, `rows`, or `dataset`. Raw input is "
        "mapped internally; set `input_kind` to `mapped` only when sending adapter metrics."
    ),
    operation_id="score_v4",
)
async def score(body: ScoreIn) -> dict[str, Any]:
    return await run_in_threadpool(_score_request, body)


__all__ = ["api_router", "public_router"]
