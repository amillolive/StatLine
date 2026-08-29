"""FastAPI application assembly for the StatLine v4 gateway."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
except ModuleNotFoundError as error:
    raise RuntimeError(
        "StatLine Gateway requires optional dependencies. Install with: "
        "pip install 'statline[remote]'  (or)  pip install -e '.[remote]'"
    ) from error

from statline import __version__
from statline.core.adapters import list_adapters
from statline.gateway.http.errors import SlapiError, to_http_exception
from statline.gateway.http.routes_api import api_router, public_router
from statline.gateway.http.routes_auth import admin_router, auth_router, mod_router

_DESCRIPTION = """
StatLine v4 exposes adapters, packaged datasets, and one scoring pipeline.

## Two-step dataset scoring

1. Read CSV data as JSON with `GET /v4/datasets/{dataset}`.
2. Send the returned `rows` to `POST /v4/score` with an adapter.

For server-side convenience, `POST /v4/score` can also receive the packaged
`dataset` path directly. The scorer accepts exactly one of `row`, `rows`, or
`dataset`; raw mapping and PRI calculation are internal stages rather than
separate endpoints.
"""

_TAGS = [
    {"name": "service", "description": "Public discovery and health endpoints."},
    {"name": "adapters", "description": "Cached adapter discovery and metadata."},
    {"name": "datasets", "description": "Packaged CSV datasets exposed as JSON."},
    {"name": "scoring", "description": "Unified raw or mapped scoring pipeline."},
    {"name": "authentication", "description": "Device enrollment and API-key ownership."},
    {"name": "moderation", "description": "Credential and device moderation."},
    {"name": "administration", "description": "Signing, approvals, and cache control."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Parse and compile every packaged adapter once before serving traffic.
    list_adapters()
    yield


def _json_error_response(exception: Exception) -> JSONResponse:
    mapped = to_http_exception(exception)
    if isinstance(mapped, tuple):
        status, detail = mapped
    else:
        status = int(getattr(mapped, "status_code", 500))
        detail = getattr(mapped, "detail", "Internal Server Error")
    return JSONResponse(status_code=status, content={"detail": detail})


async def _gateway_error_handler(_request: object, exception: Exception) -> JSONResponse:
    return _json_error_response(exception)


async def _sqlite_error_handler(_request: object, _exception: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "Gateway storage is temporarily unavailable"},
    )


app = FastAPI(
    title="StatLine API",
    summary="Adapter-driven dataset scoring",
    description=_DESCRIPTION,
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=_TAGS,
    lifespan=lifespan,
    swagger_ui_parameters={
        "defaultModelsExpandDepth": 1,
        "displayRequestDuration": True,
        "filter": True,
        "tryItOutEnabled": True,
    },
    exception_handlers={
        SlapiError: _gateway_error_handler,
        FileNotFoundError: _gateway_error_handler,
        KeyError: _gateway_error_handler,
        TypeError: _gateway_error_handler,
        ValueError: _gateway_error_handler,
        sqlite3.Error: _sqlite_error_handler,
    },
)

app.include_router(public_router)
app.include_router(api_router)
app.include_router(auth_router)
app.include_router(mod_router)
app.include_router(admin_router)


__all__ = ["app"]
