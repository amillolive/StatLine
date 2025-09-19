# statline/slapi/dep.py
from __future__ import annotations

from typing import Callable, Tuple

from fastapi import HTTPException
from starlette.requests import Request

from .auth import Principal, require_regkey


def _has_any_scope(p: Principal, scopes: Tuple[str, ...]) -> bool:
    """True if principal has any requested scope or '*'."""
    return any(s == "*" or s in p.scopes for s in scopes)


def require_any(*scopes: str) -> Callable[[Request], Principal]:
    """
    FastAPI dependency factory.
    Validates a Bearer reg_… token via `require_regkey(request)`
    and enforces that the principal has at least one of `scopes`.
    """
    def dep(request: Request) -> Principal:
        # Delegate header parsing + validation to unified auth.
        try:
            principal = require_regkey(request)
        except HTTPException:
            # Normalize any auth failure to 401 for callers of this dep.
            raise HTTPException(status_code=401, detail="Unauthorized")

        if not _has_any_scope(principal, scopes):
            raise HTTPException(status_code=403, detail="insufficient scope")

        return principal

    return dep


# Convenience deps
require_score = require_any("score")
require_any_scope = require_any("*")
