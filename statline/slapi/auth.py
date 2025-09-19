# statline/slapi/auth.py
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, cast

from fastapi import HTTPException
from starlette.requests import Request
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

# ──────────────────────────────────────────────────────────────────────────────
# Paths / constants
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR: Path = Path(__file__).resolve().parents[1]
SECRETS_DIR: Path = BASE_DIR / "secrets"
DEVKEY_PATH: Path = SECRETS_DIR / "DEVKEY"
KEYS_PATH: Path = (BASE_DIR / ".keys.json").resolve()
KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# DEVKEY helpers
# ──────────────────────────────────────────────────────────────────────────────

def _read_devkey() -> bytes:
    if not DEVKEY_PATH.exists():
        raise RuntimeError("DEVKEY missing at statline/secrets/DEVKEY")
    return DEVKEY_PATH.read_bytes()


def _fp_devkey(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


# ──────────────────────────────────────────────────────────────────────────────
# Models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RegKey:
    prefix: str
    reg_hash: str
    owner: str
    scopes: List[str]
    access: bool
    host_fp: str
    created_at: float
    last_used_at: float = 0.0
    expires_at: Optional[float] = None


@dataclass
class KeyStore:
    version: int
    keys: List[RegKey]


# ──────────────────────────────────────────────────────────────────────────────
# Store IO
# ──────────────────────────────────────────────────────────────────────────────

def _load_store() -> KeyStore:
    if not KEYS_PATH.exists():
        return KeyStore(version=1, keys=[])

    data: Dict[str, Any] = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    raw_keys: List[Dict[str, Any]] = cast(List[Dict[str, Any]], data.get("keys", []))
    return KeyStore(
        version=int(data.get("version", 1)),
        keys=[RegKey(**k) for k in raw_keys],
    )


def _save_store(store: KeyStore) -> None:
    KEYS_PATH.write_text(
        json.dumps(
            {"version": store.version, "keys": [asdict(k) for k in store.keys]},
            indent=2,
        ),
        encoding="utf-8",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Token helpers
# ──────────────────────────────────────────────────────────────────────────────

def _new_regkey() -> str:
    return "reg_" + secrets.token_urlsafe(32)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ──────────────────────────────────────────────────────────────────────────────
# Admin ops
# ──────────────────────────────────────────────────────────────────────────────

def admin_generate_key(
    owner: str,
    scopes: Optional[List[str]] = None,
    ttl_days: Optional[int] = None,
) -> Tuple[str, RegKey]:
    tok = _new_regkey()
    dev = _read_devkey()
    fp = _fp_devkey(dev)

    rec = RegKey(
        prefix=tok[4:12],
        reg_hash=_sha256(tok),
        owner=owner,
        scopes=scopes or ["score"],
        access=True,
        host_fp=fp,
        created_at=time.time(),
        expires_at=(time.time() + ttl_days * 86400) if ttl_days else None,
    )

    store = _load_store()
    store.keys.append(rec)
    _save_store(store)
    return tok, rec


def admin_list_keys() -> List[Dict[str, Any]]:
    store = _load_store()
    out: List[Dict[str, Any]] = []
    for k in store.keys:
        out.append(
            {
                "prefix": k.prefix,
                "owner": k.owner,
                "scopes": list(k.scopes),
                "access": k.access,
                "host_fp": k.host_fp,
                "created_at": k.created_at,
                "last_used_at": k.last_used_at,
                "expires_at": k.expires_at,
            }
        )
    return out


def admin_set_access(prefix8: str, value: bool) -> bool:
    store = _load_store()
    for k in store.keys:
        if k.prefix == prefix8[:8]:
            k.access = bool(value)
            _save_store(store)
            return True
    return False


def admin_revoke(prefix8: str) -> bool:
    store = _load_store()
    before = len(store.keys)
    store.keys = [k for k in store.keys if k.prefix != prefix8[:8]]
    _save_store(store)
    return len(store.keys) < before


# ──────────────────────────────────────────────────────────────────────────────
# Principal / auth
# ──────────────────────────────────────────────────────────────────────────────

class Principal:
    def __init__(self, subject: str, scopes: List[str], reg: Optional[RegKey] = None):
        self.subject: str = subject
        self.scopes: Set[str] = set(scopes)
        self.reg: Optional[RegKey] = reg


def host_fp() -> str:
    return _fp_devkey(_read_devkey())


def require_regkey(request: Request) -> Principal:
    """
    Validate Authorization: Bearer reg_xxx against the local keystore.
    Enforces host fingerprint, blacklist, and optional expiry.
    """
    auth: str = str(request.headers.get("Authorization", ""))
    if not auth.startswith("Bearer "):
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Missing Authorization Bearer regkey")

    token = auth[7:].strip()
    if not token.startswith("reg_"):
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid token type")

    store = _load_store()
    dev = _read_devkey()
    fp = _fp_devkey(dev)

    prefix8 = token[4:12]
    candidates = [k for k in store.keys if k.prefix == prefix8]

    for k in candidates:
        if hmac.compare_digest(k.reg_hash, _sha256(token)):
            if k.host_fp != fp:
                raise HTTPException(HTTP_401_UNAUTHORIZED, "Host mismatch")

            if not k.access:
                raise HTTPException(HTTP_403_FORBIDDEN, "blacklisted")

            now = time.time()
            if k.expires_at and now > k.expires_at:
                raise HTTPException(HTTP_403_FORBIDDEN, "expired")

            k.last_used_at = now
            _save_store(store)
            return Principal(subject=k.owner, scopes=k.scopes, reg=k)

    raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid credentials")


def need(scope: str, p: Principal) -> None:
    """
    Ensure the principal has the given scope; raise 403 if not.
    """
    if scope == "*" or scope in p.scopes:
        return
    raise HTTPException(HTTP_403_FORBIDDEN, "insufficient scope")
