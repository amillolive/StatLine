"""FastAPI application assembly for the StatLine v4 gateway."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any

try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
except ModuleNotFoundError as error:
    raise RuntimeError(
        "StatLine Gateway requires optional dependencies. Install with: "
        "pip install 'statline[gateway]'  (or)  pip install -e 'statline[gateway]'"
    ) from error

from statline import __version__
from statline.core.adapters import list_adapters
from statline.gateway.http.errors import SlapiError, to_http_exception
from statline.gateway.http.portal import configure_portal_cors
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



@app.get("/rtfm", include_in_schema=False)
def styled_reference() -> HTMLResponse:
    """Branded landing around the native interactive reference and ReDoc routes."""
    return HTMLResponse("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StatLine · API documentation</title>
<style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}body{margin:0;background:#0b1410;color:#e3f3e8}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:18px;padding:16px clamp(18px,5vw,80px);border-bottom:1px solid #2f4938;background:#111f17}
a{color:#b0ffcc;text-decoration:none}a:hover,a:focus-visible{text-decoration:underline}
.brand{font-size:24px;font-weight:800;letter-spacing:-1px}.brand b{color:#a4ffc2}
nav{display:flex;flex-wrap:wrap;gap:19px;font-size:14px}main{padding:16px clamp(18px,5vw,80px)}
p{max-width:900px;line-height:1.55;color:#b2cbbb}
iframe{display:block;width:100%;min-height:80vh;background:white;border:1px solid #2f4938;border-radius:8px}
@media (max-width:600px){header{align-items:flex-start}iframe{min-height:80vh}}
</style></head><body><header><a href="https://statline.dev" class="brand">statline<b>.</b></a>
<nav aria-label="API resources"><a href="/rtfm" aria-current="page">API docs</a>
<a href="/docs">Swagger · Authorize</a><a href="/redoc">ReDoc</a>
<a href="/openapi.json">OpenAPI JSON</a><a href="https://statline.dev/docs/api?topic=authentication">Access guide</a>
</nav></header><main><h1>StatLine API reference</h1>
<p>Use the built-in Swagger Authorize control with an approved bearer API key. The interactive
reference below remains the native FastAPI/Swagger UI; its security and scopes are enforced by the Gateway.</p>
<iframe title="Interactive StatLine API documentation" src="/docs" loading="eager"></iframe>
</main></body></html>""", headers={"Cache-Control": "no-store"})

configure_portal_cors(app)


@app.middleware("http")
async def server_timing_middleware(
    request: Any,
    call_next: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Expose end-to-end origin processing time without changing response bodies."""
    started = perf_counter()
    response = await call_next(request)
    elapsed_ms = (perf_counter() - started) * 1000.0
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.2f}"
    return response


app.include_router(public_router)
app.include_router(api_router)
app.include_router(auth_router)
app.include_router(mod_router)
app.include_router(admin_router)


__all__ = ["app"]
