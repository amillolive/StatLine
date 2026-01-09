# statline/slapi/dep.py
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Tuple

from fastapi import HTTPException
from starlette.requests import Request

from .auth import Principal, require_device, require_principal


def _has_any_scope(p: Principal, scopes: Tuple[str, ...]) -> bool:
    """True if principal has any requested scope or '*'."""
    return any(s == "*" or s in p.scopes for s in scopes)


def require_any(*scopes: str) -> Callable[[Request], Awaitable[Principal]]:
    """
    FastAPI dependency factory.

    Auth model (v2.1+):
      - Requires BOTH device proof headers and Authorization: Bearer api_...
      - Produces a Principal
      - Enforces at least one of the provided scopes (or '*')
    """
    async def dep(request: Request) -> Principal:
        try:
            principal = await require_principal(request)
        except HTTPException as e:
            # Normalize any auth failure to 401 for callers of this dep.
            raise HTTPException(status_code=401, detail="Unauthorized") from e

        if not _has_any_scope(principal, scopes):
            raise HTTPException(status_code=403, detail="insufficient scope")

        return principal

    return dep


def require_device_only() -> Callable[[Request], Awaitable[Dict[str, Any]]]:
    """
    Device-proof-only dependency.

    Useful for endpoints like:
      - redeem/enroll flows
      - minting api keys for an already ACTIVE device

    NOTE: This does NOT check an api_ token.
    """
    async def dep(request: Request) -> Dict[str, Any]:
        try:
            return await require_device(request)
        except HTTPException as e:
            raise HTTPException(status_code=401, detail="Unauthorized") from e

    return dep


# Convenience deps
require_score = require_any("score")
require_any_scope = require_any("*")
