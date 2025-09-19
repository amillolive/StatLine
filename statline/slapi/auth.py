# slapi/auth.py
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .errors import Forbidden, Unauthorized
from .storage.sqlite import get_conn  # must return sqlite3.Connection (row_factory=sqlite3.Row)

# ──────────────────────────────────────────────────────────────────────────────
# Model
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Principal:
    """Authenticated identity."""
    subject: str           # "env" or token hash
    role: str              # "system" | "user"
    scope: Optional[str]   # bound scope (None for env/system tokens)
    label: Optional[str]   # optional human label for issued tokens

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

# Single privileged env token (system role).
_ENV_TOKEN = os.getenv("ORION_TOKEN") or os.getenv("STATLINE_TOKEN") or ""

# Token appearance and hashing
_TOKEN_PREFIX = "slk_"  # only cosmetic; actual auth uses the full token
_HASH_ALGO = "sha256"

# Storage schema:
#   api_tokens(
#       token_hash TEXT PRIMARY KEY,
#       scope      TEXT NOT NULL,
#       label      TEXT,
#       created_ts INTEGER NOT NULL,
#       revoked_ts INTEGER
#   )
def _ensure_tables() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                token_hash TEXT PRIMARY KEY,
                scope      TEXT NOT NULL,
                label      TEXT,
                created_ts INTEGER NOT NULL,
                revoked_ts INTEGER
            )
            """
        )
        conn.commit()

# ──────────────────────────────────────────────────────────────────────────────
# Low-level helpers
# ──────────────────────────────────────────────────────────────────────────────

def _now() -> int:
    return int(time.time())

def _hash_token(token: str) -> str:
    h = hashlib.new(_HASH_ALGO)
    h.update(token.encode("utf-8"))
    return h.hexdigest()

def _ct_equal(a: str, b: str) -> bool:
    try:
        return hmac.compare_digest(a, b)
    except Exception:
        # Fall back to safe behavior
        if len(a) != len(b):
            return False
        result = 0
        for x, y in zip(a.encode(), b.encode()):
            result |= x ^ y
        return result == 0

def _is_prefixed(token: str) -> bool:
    return token.startswith(_TOKEN_PREFIX)

def _strip_prefix(token: str) -> str:
    return token[len(_TOKEN_PREFIX):] if _is_prefixed(token) else token

# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def parse_bearer(authorization_header: Optional[str]) -> Optional[str]:
    """
    Extract token from an HTTP-style Authorization header.
    Accepts: "Bearer <token>" (case-insensitive). Returns the raw token or None.
    """
    if not authorization_header:
        return None
    s = authorization_header.strip()
    if not s:
        return None
    parts = s.split()
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def issue_token(scope: str, *, label: Optional[str] = None) -> str:
    """
    Create a new API token bound to `scope`. Returns the **plaintext token** (show once).
    The token is stored **hashed**. Callers must persist the plaintext themselves.
    """
    if not scope or not scope.strip():
        raise Unauthorized("Scope is required to issue a token.")
    _ensure_tables()

    # 32 bytes -> 43 urlsafe chars (approx); add a tiny prefix for readability.
    raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    token_hash = _hash_token(_strip_prefix(raw))
    ts = _now()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO api_tokens (token_hash, scope, label, created_ts, revoked_ts)
            VALUES (?, ?, ?, ?, NULL)
            """,
            (token_hash, scope.strip(), (label or None), ts),
        )
        conn.commit()

    return raw


def revoke_token(token: str) -> bool:
    """
    Revoke an issued token (by plaintext). Returns True if it existed and is now revoked.
    """
    if not token:
        return False
    h = _hash_token(_strip_prefix(token))
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.execute("UPDATE api_tokens SET revoked_ts = ? WHERE token_hash = ? AND revoked_ts IS NULL", (_now(), h))
        conn.commit()
        return cur.rowcount > 0


def list_tokens(scope: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List token metadata (never returns plaintext). If `scope` is given, filters to that scope.
    """
    _ensure_tables()
    with get_conn() as conn:
        if scope:
            cur = conn.execute(
                """
                SELECT token_hash, scope, label, created_ts, revoked_ts
                FROM api_tokens
                WHERE scope = ?
                ORDER BY created_ts DESC
                """,
                (scope,),
            )
        else:
            cur = conn.execute(
                """
                SELECT token_hash, scope, label, created_ts, revoked_ts
                FROM api_tokens
                ORDER BY created_ts DESC
                """
            )
        return [dict(r) for r in cur.fetchall()]


def _lookup_token_hash(token_hash: str) -> Optional[Principal]:
    _ensure_tables()
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT token_hash, scope, label, created_ts, revoked_ts
            FROM api_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        )
        row = cur.fetchone()
    if not row:
        return None
    if row["revoked_ts"] is not None:
        return None
    return Principal(subject=row["token_hash"], role="user", scope=row["scope"], label=row["label"])


def validate(token: Optional[str], *, scope: Optional[str] = None, require_system: bool = False) -> Principal:
    """
    Validate a token. Returns a Principal or raises AuthError / Forbidden.

    Rules:
      - If token equals the env token → Principal(role="system").
      - Else, token must match an issued (non-revoked) hash → Principal(role="user").
      - If `scope` is provided and principal.scope is set, they must match.
      - If `require_system` is True, only the env token passes.
    """
    if not token or not token.strip():
        raise Unauthorized("Missing token.")

    # System token (env)
    if _ENV_TOKEN and _ct_equal(token, _ENV_TOKEN):
        if require_system:
            return Principal(subject="env", role="system", scope=None, label="env")
        # system token can act across scopes
        return Principal(subject="env", role="system", scope=None, label="env")

    # Issued tokens (user role)
    principal = _lookup_token_hash(_hash_token(_strip_prefix(token)))
    if principal is None:
        raise Unauthorized("Invalid token.")
    if require_system:
        # caller explicitly required a system principal
        raise Forbidden("Operation requires a system token.")

    # Optional scope check
    if scope is not None and principal.scope is not None and principal.scope != scope:
        raise Forbidden("Token not authorized for this scope.")
    return principal


def require(token: Optional[str], *, scope: Optional[str] = None) -> Principal:
    """
    Validate a token for (optional) scope; raise if not valid.
    """
    return validate(token, scope=scope, require_system=False)


def require_system(token: Optional[str]) -> Principal:
    """
    Validate that `token` is the env/system token.
    """
    return validate(token, scope=None, require_system=True)
