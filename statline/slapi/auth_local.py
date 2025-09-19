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
from starlette.requests import Request  # <- use Starlette's Request for typing
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

# ---------- paths ----------
BASE_DIR: Path = Path(__file__).resolve().parents[1]          # statline/
SECRETS_DIR: Path = BASE_DIR / "secrets"
DEVKEY_PATH: Path = SECRETS_DIR / "DEVKEY"
KEYS_PATH: Path = (BASE_DIR / ".keys.json").resolve()
KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)

# ---------- DEVKEY ----------
def _read_devkey() -> bytes:
    if not DEVKEY_PATH.exists():
        raise RuntimeError("DEVKEY missing. Create statline/secrets/DEVKEY before starting SLAPI.")
    return DEVKEY_PATH.read_bytes()

def _fp_devkey(raw: bytes) -> str:
    # bind keys to this host by fingerprinting DEVKEY
    return hashlib.sha256(raw).hexdigest()[:16]

# ---------- models ----------
@dataclass
class RegKey:
    prefix: str                 # first 8 chars of token after "reg_"
    reg_hash: str               # sha256 of full token
    owner: str                  # free-form (e.g., "guild:1234", "user:abc")
    scopes: List[str]           # e.g., ["score", "map", "calc"]
    access: bool                # master toggle (true by default)
    host_fp: str                # fingerprint of DEVKEY on creation
    created_at: float
    last_used_at: float = 0.0
    expires_at: Optional[float] = None

@dataclass
class KeyStore:
    version: int
    keys: List[RegKey]

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

# ---------- token helpers ----------
def _new_regkey() -> str:
    return "reg_" + secrets.token_urlsafe(32)

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _find_by_prefix(store: KeyStore, prefix8: str) -> Optional[RegKey]:
    for k in store.keys:
        if k.prefix == prefix8:
            return k
    return None

# ---------- public admin ops ----------
def admin_generate_key(
    owner: str,
    scopes: Optional[List[str]] = None,
    ttl_days: Optional[int] = None,
) -> Tuple[str, RegKey]:
    devkey: bytes = _read_devkey()
    fp: str = _fp_devkey(devkey)
    tok: str = _new_regkey()
    rec = RegKey(
        prefix=tok[4:12],
        reg_hash=_sha256(tok),
        owner=owner,
        scopes=scopes or ["score", "map", "calc"],
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
    out: List[Dict[str, Any]] = []
    for k in _load_store().keys:
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
    k = _find_by_prefix(store, prefix8[:8])
    if not k:
        return False
    k.access = bool(value)
    _save_store(store)
    return True

def admin_revoke(prefix8: str) -> bool:
    store = _load_store()
    before = len(store.keys)
    store.keys = [k for k in store.keys if k.prefix != prefix8[:8]]
    _save_store(store)
    return len(store.keys) < before

# ---------- request auth ----------
class AuthZ:
    def __init__(self, subject: str, scopes: List[str], reg: Optional[RegKey] = None):
        self.subject: str = subject
        self.scopes: Set[str] = set(scopes)
        self.reg: Optional[RegKey] = reg

def require_regkey(request: Request) -> AuthZ:
    """
    Client presents Authorization: Bearer reg_xxx
    We verify:
      - token exists (prefix + hash)
      - bound to THIS host's DEVKEY fingerprint
      - access == True
      - not expired
    """
    # headers is a Mapping[str, str]; keep types explicit for checkers
    auth: str = str(request.headers.get("Authorization", ""))
    if not auth.startswith("Bearer "):
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Missing Authorization Bearer regkey")

    token: str = auth[7:].strip()
    if not token.startswith("reg_"):
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid token type")

    store: KeyStore = _load_store()
    prefix8: str = token[4:12]
    recs: List[RegKey] = [k for k in store.keys if k.prefix == prefix8]

    devkey: bytes = _read_devkey()
    host_fp: str = _fp_devkey(devkey)

    # must match reg_hash and host_fp
    for k in recs:
        if hmac.compare_digest(k.reg_hash, _sha256(token)):
            # bound to this host?
            if k.host_fp != host_fp:
                raise HTTPException(HTTP_401_UNAUTHORIZED, "Host mismatch")
            # blacklisted?
            if not k.access:
                raise HTTPException(HTTP_403_FORBIDDEN, "blacklisted")
            # expired?
            now: float = time.time()
            if k.expires_at is not None and now > k.expires_at:
                raise HTTPException(HTTP_403_FORBIDDEN, "expired")
            # update last_used
            k.last_used_at = now
            _save_store(store)
            return AuthZ(subject=k.owner, scopes=k.scopes, reg=k)

    raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid credentials")
def host_fp() -> str:
    """Public helper for this host's DEVKEY fingerprint."""
    return _fp_devkey(_read_devkey())

def need(scope: str, a: AuthZ) -> None:
    if scope == "*" or scope in a.scopes:
        return
    raise HTTPException(HTTP_403_FORBIDDEN, "insufficient scope")
