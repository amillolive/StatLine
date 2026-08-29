"""Shared FastAPI authentication and scope dependencies."""

from __future__ import annotations  # noqa: I001

from typing import Annotated, Any, Awaitable, Callable, Dict

from fastapi import (  # pyright: ignore[reportUnknownVariableType]
    Depends,
    Header,  # pyright: ignore[reportUnknownVariableType]
    HTTPException,
    Security,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import Request

from statline.gateway.auth.service import (
    HDR_DEVICE_ID,
    HDR_NONCE,
    HDR_SIGNATURE,
    HDR_TIMESTAMP,
    need,
    require_device,
    require_principal,
)
from statline.gateway.auth.types import Principal

SCOPE_USERBASE = "userbase"
SCOPE_MODERATION = "moderation"
SCOPE_ADMIN = "admin"

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="StatLineApiKey",
    description="Portable `api_...` bearer credential.",
)


async def principal_dependency(
    request: Request,
    _credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(_bearer),
    ] = None,
) -> Principal:
    return await require_principal(request)


async def device_dependency(
    request: Request,
    _device_id: Annotated[str | None, Header(alias=HDR_DEVICE_ID)] = None,
    _timestamp: Annotated[str | None, Header(alias=HDR_TIMESTAMP)] = None,
    _nonce: Annotated[str | None, Header(alias=HDR_NONCE)] = None,
    _signature: Annotated[str | None, Header(alias=HDR_SIGNATURE)] = None,
) -> DeviceRow:
    return await require_device(request)


AuthDep = Annotated[Principal, Depends(principal_dependency)]
DeviceRow = Dict[str, Any]
DeviceRowDep = Annotated[DeviceRow, Depends(device_dependency)]


def require_scope(scope: str) -> Callable[..., Principal]:
    def dependency(principal: AuthDep) -> Principal:
        need(scope, principal)
        return principal

    return dependency


def require_any(*scopes: str) -> Callable[[Request], Awaitable[Principal]]:
    async def dependency(request: Request) -> Principal:
        try:
            principal = await principal_dependency(request)
        except HTTPException as error:
            raise HTTPException(status_code=401, detail="Unauthorized") from error

        if not any(scope == "*" or scope in principal.scopes for scope in scopes):
            raise HTTPException(status_code=403, detail="insufficient scope")
        return principal

    return dependency


def require_device_only() -> Callable[[Request], Awaitable[DeviceRow]]:
    async def dependency(request: Request) -> DeviceRow:
        try:
            return await device_dependency(request)
        except HTTPException as error:
            raise HTTPException(status_code=401, detail="Unauthorized") from error

    return dependency


require_score = require_any(SCOPE_USERBASE)
require_any_scope = require_any("*")


__all__ = [
    "AuthDep",
    "device_dependency",
    "DeviceRow",
    "DeviceRowDep",
    "SCOPE_ADMIN",
    "SCOPE_MODERATION",
    "SCOPE_USERBASE",
    "principal_dependency",
    "require_any",
    "require_any_scope",
    "require_device_only",
    "require_scope",
    "require_score",
]
