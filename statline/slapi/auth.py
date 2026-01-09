# statline/slapi/auth.py
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from fastapi import HTTPException
from starlette.requests import Request
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from statline.slapi.permissions import expand_scopes, validate_scopes

# =============================================================================
# High-level model
# =============================================================================
# This module implements a file-based, non-cloud auth system:
#
# - DEVKEY (Ed25519) is the "admin authority" used to mint enrollment tokens.
# - Enrollment tokens ("reg_...") are one-time redeemable and create PENDING enrollments.
# - Admin approval activates a device (stores its public key + metadata).
# - API access requires BOTH:
#     (1) Authorization: Bearer api_...
#     (2) Device proof headers (device_id + ed25519 signature) from an approved device file.
#
# Devices are NOT hard-locked to hardware: the "device identity" is a file (private key)
# that can be copied intentionally. Gating is done via server-side approval + revocation.
#
# Inactivity policy:
# - If a device has not been seen for INACTIVE_UNENROLL_SECONDS, it becomes UNENROLLED.
# - UNENROLLED devices cannot access the API until re-enrolled and re-approved.

# =============================================================================
# Config / Paths
# =============================================================================

INACTIVE_UNENROLL_SECONDS: int = 30 * 86400  # 30 days
SIG_SKEW_SECONDS: int = 120  # allowed clock skew for device signatures
NONCE_TTL_SECONDS: int = 300  # replay cache window

# Recommended header names for device proof
HDR_DEVICE_ID = "X-SL-Device"
HDR_TIMESTAMP = "X-SL-Timestamp"
HDR_NONCE = "X-SL-Nonce"
HDR_SIGNATURE = "X-SL-Signature"


def _find_project_root(start: Path) -> Optional[Path]:
    """
    Heuristic repo root detector.
    Prefers a directory containing:
      - pyproject.toml OR
      - .git OR
      - statline/__init__.py
    """
    start = start.resolve()
    for p in [start, *start.parents]:
        if (p / "pyproject.toml").exists():
            return p
        if (p / ".git").exists():
            return p
        if (p / "statline" / "__init__.py").exists():
            return p
    return None


def _default_project_root() -> Path:
    # 1) Prefer current working directory if it looks like the project
    cwd = Path.cwd().resolve()
    root = _find_project_root(cwd)
    if root:
        return root

    # 2) Otherwise, try resolving from this file location (dev/editable installs)
    here = Path(__file__).resolve()
    root2 = _find_project_root(here.parent)
    if root2:
        return root2

    # 3) Last resort
    return cwd


def _default_data_dir() -> Path:
    # Prefer explicit env var
    env = os.environ.get("STATLINE_DATA_DIR") or os.environ.get("STATLINE_SLAPI_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()

    # Prefer project-local data dir when running from source
    project_root = _default_project_root()
    return (project_root / ".statline").resolve()


PROJECT_ROOT: Path = _default_project_root()

DATA_DIR: Path = _default_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH: Path = (DATA_DIR / "auth.db").resolve()

# Secrets directory:
# Prefer explicit env override; else default to project/statline/secrets (matches CLI expectation)
_secrets_env = os.environ.get("STATLINE_SECRETS_DIR")
if _secrets_env:
    secrets_dir: Path = Path(_secrets_env).expanduser().resolve()
else:
    # Default to repo-local secrets if layout exists; else fallback to DATA_DIR/secrets
    candidate = (PROJECT_ROOT / "statline" / "secrets").resolve()
    if (PROJECT_ROOT / "statline").exists():
        secrets_dir = candidate
    else:
        secrets_dir = (DATA_DIR / "secrets").resolve()

secrets_dir.mkdir(parents=True, exist_ok=True)

DEVKEY_PATH: Path = (secrets_dir / "DEVKEY").resolve()
DEVKEY_PUB_PATH: Path = (secrets_dir / "DEVKEY.pub").resolve()

# =============================================================================
# Utilities: base64url, hashing, json canon
# =============================================================================


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    s = s.strip()
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _sha256_hex(raw: Union[str, bytes]) -> str:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _json_canon(obj: Any) -> bytes:
    # Canonical JSON for signing/verifying
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


# =============================================================================
# SQLite store
# =============================================================================


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=3000;")
    return conn


def _init_db() -> None:
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reg_redemptions (
                rid TEXT PRIMARY KEY,
                redeemed_at REAL NOT NULL,
                request_id TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS enrollments (
                request_id TEXT PRIMARY KEY,
                org TEXT NOT NULL,
                user TEXT NOT NULL,
                email TEXT,
                scopes_json TEXT NOT NULL,
                device_id TEXT NOT NULL,
                device_pub_b64 TEXT NOT NULL,
                meta_json TEXT NOT NULL,
                status TEXT NOT NULL, -- PENDING|APPROVED|DENIED
                created_at REAL NOT NULL,
                decided_at REAL,
                decided_by TEXT,
                decision_note TEXT
            );

            CREATE TABLE IF NOT EXISTS devices (
                device_id TEXT PRIMARY KEY,
                org TEXT NOT NULL,
                user TEXT NOT NULL,
                email TEXT,
                scopes_json TEXT NOT NULL,
                device_pub_b64 TEXT NOT NULL,

                status TEXT NOT NULL, -- ACTIVE|UNENROLLED|REVOKED
                created_at REAL NOT NULL,
                approved_at REAL,
                last_seen_at REAL,
                unenrolled_at REAL,
                revoked_at REAL,

                hostname TEXT,
                os TEXT,
                cli_version TEXT,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS apikey_requests (
                request_id TEXT PRIMARY KEY,
                org TEXT NOT NULL,
                device_id TEXT NOT NULL,
                owner TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                ttl_days INTEGER,
                status TEXT NOT NULL, -- PENDING|APPROVED|DENIED|CLAIMED
                created_at REAL NOT NULL,
                decided_at REAL,
                decided_by TEXT,
                decision_note TEXT,
                approved_scopes_json TEXT,
                claimed_at REAL,
                api_prefix TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_apikey_requests_device_status
                ON apikey_requests(device_id, status);

            CREATE INDEX IF NOT EXISTS idx_apikey_requests_org_status
                ON apikey_requests(org, status);

            CREATE TABLE IF NOT EXISTS apikeys (
                prefix8 TEXT PRIMARY KEY,
                key_hash TEXT NOT NULL,
                org TEXT NOT NULL,
                owner TEXT NOT NULL,
                device_id TEXT NOT NULL,
                scopes_json TEXT NOT NULL,
                access INTEGER NOT NULL, -- 0/1
                created_at REAL NOT NULL,
                last_used_at REAL NOT NULL,
                expires_at REAL
            );

            CREATE TABLE IF NOT EXISTS nonces (
                device_id TEXT NOT NULL,
                nonce TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (device_id, nonce)
            );

            CREATE TABLE IF NOT EXISTS audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                event TEXT NOT NULL,
                ok INTEGER NOT NULL,
                org TEXT,
                subject TEXT,
                device_id TEXT,
                api_prefix TEXT,
                ip TEXT,
                ua TEXT,
                detail TEXT
            );
            """
        )
    finally:
        conn.close()


_init_db()

# =============================================================================
# DEVKEY (Ed25519) load + helpers
# =============================================================================


def _load_dev_private() -> Optional[Ed25519PrivateKey]:
    if not DEVKEY_PATH.exists():
        return None
    raw = DEVKEY_PATH.read_bytes()
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except Exception:
        return None
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeError("DEVKEY is not an Ed25519 private key")
    return key


def _load_dev_public() -> Ed25519PublicKey:
    # Prefer deriving from private key if present
    priv = _load_dev_private()
    if priv is not None:
        return priv.public_key()

    # Otherwise require DEVKEY.pub
    if not DEVKEY_PUB_PATH.exists():
        raise RuntimeError(
            "DEVKEY missing or not a private key, and DEVKEY.pub not found. "
            f"Expected {DEVKEY_PATH} (private) or {DEVKEY_PUB_PATH} (public)."
        )
    raw = DEVKEY_PUB_PATH.read_bytes()
    key = serialization.load_pem_public_key(raw)
    if not isinstance(key, Ed25519PublicKey):
        raise RuntimeError("DEVKEY.pub is not an Ed25519 public key")
    return key


def devkey_fingerprint() -> str:
    pub = _load_dev_public()
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(pub_raw).hexdigest()[:16]


def admin_generate_devkey_files(overwrite: bool = False) -> Dict[str, str]:
    """
    Convenience helper (for admin/dev):
    Generates DEVKEY (private) and DEVKEY.pub (public) PEM files.

    NOTE: Not called automatically. You can use this from a CLI command.
    """
    if DEVKEY_PATH.exists() and not overwrite:
        raise RuntimeError(f"Refusing to overwrite existing DEVKEY at {DEVKEY_PATH}")

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    DEVKEY_PATH.write_bytes(priv_pem)
    DEVKEY_PUB_PATH.write_bytes(pub_pem)
    return {
        "devkey_path": str(DEVKEY_PATH),
        "devkey_pub_path": str(DEVKEY_PUB_PATH),
        "fingerprint": devkey_fingerprint(),
    }


# =============================================================================
# Data models
# =============================================================================


@dataclass(frozen=True)
class Principal:
    org: str
    subject: str  # user/owner string
    device_id: str
    api_prefix: str
    scopes: Set[str]


# =============================================================================
# Audit helpers
# =============================================================================


def _audit(
    *,
    event: str,
    ok: bool,
    org: Optional[str] = None,
    subject: Optional[str] = None,
    device_id: Optional[str] = None,
    api_prefix: Optional[str] = None,
    request: Optional[Request] = None,
    detail: Optional[str] = None,
) -> None:
    ip = None
    ua = None
    if request is not None:
        try:
            ip = request.client.host if request.client else None
        except Exception:
            ip = None
        ua = str(request.headers.get("User-Agent", ""))[:512]

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO audit (ts, event, ok, org, subject, device_id, api_prefix, ip, ua, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                event,
                1 if ok else 0,
                org,
                subject,
                device_id,
                api_prefix,
                ip,
                ua,
                detail,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# Enrollment tokens ("reg_...") signed by DEVKEY
# =============================================================================


def _reg_payload(org: str, scopes: Sequence[str], ttl_days: Optional[int]) -> Dict[str, Any]:
    now = int(time.time())
    exp = None
    if ttl_days is not None:
        exp = now + int(ttl_days) * 86400
    return {
        "v": 1,
        "rid": uuid.uuid4().hex,  # redemption id for one-time tracking
        "org": org,
        "scopes": list(scopes),
        "iat": now,
        "exp": exp,
        "kid": devkey_fingerprint(),  # human-friendly linkage
    }


def admin_mint_regtoken(
    *,
    org: str,
    scopes: Optional[Sequence[str]] = None,
    ttl_days: Optional[int] = 14,
) -> str:
    """
    Mint an enrollment token that can be redeemed ONCE to create a PENDING enrollment request.
    Requires DEVKEY private key on this machine.
    """
    priv = _load_dev_private()
    if priv is None:
        raise RuntimeError("Cannot mint reg tokens: DEVKEY private key not available")

    payload = _reg_payload(org=org, scopes=scopes or ["score"], ttl_days=ttl_days)
    msg = b"statline-regtoken-v1\n" + _json_canon(payload)
    sig = priv.sign(msg)

    tok = "reg_" + _b64u_encode(_json_canon(payload)) + "." + _b64u_encode(sig)
    return tok


def verify_regtoken(token: str) -> Dict[str, Any]:
    """
    Verify a reg token signature and expiry (does NOT mark redeemed).
    Raises HTTPException on invalid token.
    """
    if not token.startswith("reg_"):
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid token type")

    try:
        body = token[4:]
        p_b64, s_b64 = body.split(".", 1)
        payload_raw = _b64u_decode(p_b64)
        sig = _b64u_decode(s_b64)
        payload = cast(Dict[str, Any], json.loads(payload_raw.decode("utf-8")))
    except Exception:
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Malformed reg token")

    if int(payload.get("v", 0)) != 1:
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Unsupported reg token version")

    exp = payload.get("exp")
    now = int(time.time())
    if exp is not None and now > int(exp):
        raise HTTPException(HTTP_403_FORBIDDEN, "reg token expired")

    pub = _load_dev_public()
    msg = b"statline-regtoken-v1\n" + _json_canon(payload)
    try:
        pub.verify(sig, msg)
    except InvalidSignature:
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid reg token signature")

    # Basic shape validation
    if not isinstance(payload.get("rid"), str) or len(payload["rid"]) < 16:
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid reg token payload")
    if not isinstance(payload.get("org"), str) or not payload["org"]:
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid reg token payload")
    scopes = payload.get("scopes")
    if not isinstance(scopes, list) or not all(isinstance(x, str) for x in scopes):  # pyright: ignore[reportUnknownVariableType]
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid reg token payload")

    return payload


def _reg_is_redeemed(rid: str) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT rid FROM reg_redemptions WHERE rid = ?", (rid,)).fetchone()
        return row is not None
    finally:
        conn.close()


def _mark_reg_redeemed(rid: str, request_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO reg_redemptions (rid, redeemed_at, request_id) VALUES (?, ?, ?)",
            (rid, time.time(), request_id),
        )
        conn.commit()
    finally:
        conn.close()


# =============================================================================
# Enrollment (apply pool) + approval
# =============================================================================


def _pubkey_raw_from_b64(pub_b64: str) -> bytes:
    raw = _b64u_decode(pub_b64)
    if len(raw) != 32:
        raise HTTPException(HTTP_400_BAD_REQUEST, "Invalid device public key")
    return raw


def create_enrollment_request(
    *,
    reg_token: str,
    user: str,
    email: Optional[str],
    device_pub_b64: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    Redeem a reg token ONCE and create a PENDING enrollment.
    This does not activate the device; admin must approve.

    Returns: {"request_id": "...", "device_id": "...", "org": "..."}
    """
    payload = verify_regtoken(reg_token)
    rid = cast(str, payload["rid"])
    org = cast(str, payload["org"])
    scopes = cast(List[str], payload["scopes"])

    if _reg_is_redeemed(rid):
        raise HTTPException(HTTP_403_FORBIDDEN, "reg token already redeemed")

    if not user or not isinstance(user, str):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise HTTPException(HTTP_400_BAD_REQUEST, "user required")

    # Validate pubkey
    pub_raw = _pubkey_raw_from_b64(device_pub_b64)
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw)
    except Exception:
        raise HTTPException(HTTP_400_BAD_REQUEST, "Invalid device public key")

    request_id = uuid.uuid4().hex
    device_id = uuid.uuid4().hex

    meta_json = json.dumps(meta or {}, separators=(",", ":"), sort_keys=True)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO enrollments
              (request_id, org, user, email, scopes_json, device_id, device_pub_b64, meta_json, status, created_at)
            VALUES
              (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (
                request_id,
                org,
                user,
                email,
                json.dumps(scopes),
                device_id,
                device_pub_b64,
                meta_json,
                time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Mark redemption AFTER enrollment record is written
    _mark_reg_redeemed(rid, request_id)

    return {"request_id": request_id, "device_id": device_id, "org": org}


def admin_list_enrollments(status: str = "PENDING") -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT request_id, org, user, email, scopes_json, device_id, meta_json, status, created_at, decided_at, decided_by, decision_note
            FROM enrollments
            WHERE status = ?
            ORDER BY created_at ASC
            """,
            (status,),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "request_id": r["request_id"],
                    "org": r["org"],
                    "user": r["user"],
                    "email": r["email"],
                    "scopes": json.loads(r["scopes_json"]),
                    "device_id": r["device_id"],
                    "meta": json.loads(r["meta_json"] or "{}"),
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "decided_at": r["decided_at"],
                    "decided_by": r["decided_by"],
                    "decision_note": r["decision_note"],
                }
            )
        return out
    finally:
        conn.close()


def admin_approve_enrollment(
    *,
    request_id: str,
    decided_by: str = "dev",
    decision_note: Optional[str] = None,
) -> bool:
    """
    Approve a pending enrollment request. This activates the device.
    """
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT request_id, org, user, email, scopes_json, device_id, device_pub_b64, meta_json, status
            FROM enrollments
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            return False
        if row["status"] != "PENDING":
            return False

        now = time.time()

        conn.execute(
            """
            UPDATE enrollments
            SET status = 'APPROVED', decided_at = ?, decided_by = ?, decision_note = ?
            WHERE request_id = ?
            """,
            (now, decided_by, decision_note, request_id),
        )

        meta = {}
        try:
            meta = json.loads(row["meta_json"] or "{}")
            if not isinstance(meta, dict):
                meta = {}
        except Exception:
            meta = {}

        # Upsert device record
        conn.execute(
            """
            INSERT INTO devices
              (device_id, org, user, email, scopes_json, device_pub_b64, status, created_at, approved_at, last_seen_at,
               hostname, os, cli_version, notes)
            VALUES
              (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 0,
               ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
              org = excluded.org,
              user = excluded.user,
              email = excluded.email,
              scopes_json = excluded.scopes_json,
              device_pub_b64 = excluded.device_pub_b64,
              status = 'ACTIVE',
              approved_at = excluded.approved_at
            """,
            (
                row["device_id"],
                row["org"],
                row["user"],
                row["email"],
                row["scopes_json"],
                row["device_pub_b64"],
                now,
                now,
                cast(Optional[str], meta.get("hostname")), # pyright: ignore[reportUnknownMemberType]
                cast(Optional[str], meta.get("os")), # pyright: ignore[reportUnknownMemberType]
                cast(Optional[str], meta.get("cli_version")), # pyright: ignore[reportUnknownMemberType]
                None,
            ),
        )

        conn.commit()
        return True
    finally:
        conn.close()


def admin_deny_enrollment(
    *,
    request_id: str,
    decided_by: str = "dev",
    decision_note: Optional[str] = None,
) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT status FROM enrollments WHERE request_id = ?", (request_id,)).fetchone()
        if row is None or row["status"] != "PENDING":
            return False
        conn.execute(
            """
            UPDATE enrollments
            SET status = 'DENIED', decided_at = ?, decided_by = ?, decision_note = ?
            WHERE request_id = ?
            """,
            (time.time(), decided_by, decision_note, request_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def admin_revoke_device(device_id: str, note: Optional[str] = None) -> bool:
    conn = _connect()
    try:
        row = conn.execute("SELECT device_id FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if row is None:
            return False
        conn.execute(
            """
            UPDATE devices
            SET status = 'REVOKED', revoked_at = ?, notes = COALESCE(notes,'') || ?
            WHERE device_id = ?
            """,
            (time.time(), f"\nREVOKE: {note or ''}", device_id),
        )
        # Disable API keys for that device
        conn.execute("UPDATE apikeys SET access = 0 WHERE device_id = ?", (device_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# =============================================================================
# API key issuance (server-side) + validation
# =============================================================================


def _new_apikey() -> str:
    return "api_" + secrets.token_urlsafe(32)


def create_api_key_for_device(
    *,
    device_id: str,
    owner: str,
    scopes: Optional[Sequence[str]] = None,
    ttl_days: Optional[int] = 30,
) -> Tuple[str, Dict[str, Any]]:
    """
    Create an api_ token tied to a device. Intended for an authenticated flow
    (e.g., device-proof + admin policy), not for anonymous issuance.
    """
    conn = _connect()
    try:
        d = conn.execute(
            "SELECT org, user, scopes_json, status FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if d is None:
            raise HTTPException(HTTP_403_FORBIDDEN, "Unknown device")
        if d["status"] != "ACTIVE":
            raise HTTPException(HTTP_403_FORBIDDEN, f"Device not active: {d['status']}")

        org = cast(str, d["org"])
        device_scopes_raw = set(json.loads(d["scopes_json"]))
        device_scopes = expand_scopes(validate_scopes(device_scopes_raw))

        req_scopes_raw = set(scopes or list(device_scopes_raw))
        req_scopes = validate_scopes(req_scopes_raw)

        # req must be subset of *expanded* device scopes
        if not req_scopes.issubset(device_scopes):
            raise HTTPException(HTTP_403_FORBIDDEN, "Requested scopes exceed device approval")


        token = _new_apikey()
        prefix8 = token[4:12]
        key_hash = _sha256_hex(token)

        now = time.time()
        exp = (now + int(ttl_days) * 86400) if ttl_days is not None else None

        conn.execute(
            """
            INSERT INTO apikeys
              (prefix8, key_hash, org, owner, device_id, scopes_json, access, created_at, last_used_at, expires_at)
            VALUES
              (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                prefix8,
                key_hash,
                org,
                owner,
                device_id,
                json.dumps(sorted(req_scopes)),
                now,
                now,
                exp,
            ),
        )
        conn.commit()

        rec = {
            "prefix": prefix8,
            "org": org,
            "owner": owner,
            "device_id": device_id,
            "scopes": sorted(req_scopes),
            "created_at": now,
            "expires_at": exp,
        }
        return token, rec
    finally:
        conn.close()


def admin_list_apikeys(org: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        if org:
            rows = conn.execute(
                """
                SELECT prefix8, org, owner, device_id, scopes_json, access, created_at, last_used_at, expires_at
                FROM apikeys
                WHERE org = ?
                ORDER BY created_at DESC
                """,
                (org,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT prefix8, org, owner, device_id, scopes_json, access, created_at, last_used_at, expires_at
                FROM apikeys
                ORDER BY created_at DESC
                """
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "prefix": r["prefix8"],
                    "org": r["org"],
                    "owner": r["owner"],
                    "device_id": r["device_id"],
                    "scopes": json.loads(r["scopes_json"]),
                    "access": bool(r["access"]),
                    "created_at": r["created_at"],
                    "last_used_at": r["last_used_at"],
                    "expires_at": r["expires_at"],
                }
            )
        return out
    finally:
        conn.close()


def admin_set_apikey_access(prefix8: str, value: bool) -> bool:
    conn = _connect()
    try:
        cur = conn.execute("UPDATE apikeys SET access = ? WHERE prefix8 = ?", (1 if value else 0, prefix8[:8]))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def admin_revoke_apikey(prefix8: str) -> bool:
    return admin_set_apikey_access(prefix8, False)


# =============================================================================
# Device proof verification (Ed25519) + replay protection + inactivity unenroll
# =============================================================================


def _purge_expired_nonces(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM nonces WHERE expires_at < ?", (now,))


def _nonce_seen_or_store(conn: sqlite3.Connection, device_id: str, nonce: str, now: float) -> bool:
    _purge_expired_nonces(conn, now)
    try:
        conn.execute(
            "INSERT INTO nonces (device_id, nonce, expires_at) VALUES (?, ?, ?)",
            (device_id, nonce, now + NONCE_TTL_SECONDS),
        )
        return False
    except sqlite3.IntegrityError:
        return True


def _canonical_target(request: Request) -> str:
    # Include path + raw query. Avoid host/scheme to keep stable behind proxies.
    path = request.url.path
    q = request.url.query
    return path + (("?" + q) if q else "")


async def require_device(request: Request) -> Dict[str, Any]:
    """
    Validate device proof headers:
      X-SL-Device, X-SL-Timestamp, X-SL-Nonce, X-SL-Signature

    Envelope signed by device private key:
      METHOD \\n TARGET \\n TIMESTAMP \\n NONCE \\n BODY_SHA256

    Returns device row as dict-like fields.
    """
    device_id = str(request.headers.get(HDR_DEVICE_ID, "")).strip()
    ts_s = str(request.headers.get(HDR_TIMESTAMP, "")).strip()
    nonce = str(request.headers.get(HDR_NONCE, "")).strip()
    sig_b64 = str(request.headers.get(HDR_SIGNATURE, "")).strip()

    if not device_id or not ts_s or not nonce or not sig_b64:
        _audit(event="auth.device.missing_headers", ok=False, request=request, detail="missing device proof headers")
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Missing device proof headers")

    try:
        ts = int(ts_s)
    except Exception:
        _audit(event="auth.device.bad_timestamp", ok=False, request=request, detail="bad timestamp")
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid device timestamp")

    now = int(time.time())
    if abs(now - ts) > SIG_SKEW_SECONDS:
        _audit(event="auth.device.timestamp_skew", ok=False, request=request, detail=f"skew={abs(now-ts)}")
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Device timestamp out of range")

    try:
        sig = _b64u_decode(sig_b64)
        if len(sig) != 64:
            raise ValueError("bad sig len")
    except Exception:
        _audit(event="auth.device.bad_signature_encoding", ok=False, request=request, detail="bad signature b64")
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid device signature encoding")

    body = await request.body()
    body_hash = _sha256_hex(body)

    target = _canonical_target(request)
    method = request.method.upper()

    envelope = f"{method}\n{target}\n{ts}\n{nonce}\n{body_hash}".encode("utf-8")

    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT device_id, org, user, email, scopes_json, device_pub_b64, status,
                   created_at, approved_at, last_seen_at, unenrolled_at, revoked_at
            FROM devices
            WHERE device_id = ?
            """,
            (device_id,),
        ).fetchone()

        if row is None:
            _audit(event="auth.device.unknown", ok=False, request=request, device_id=device_id, detail="unknown device")
            raise HTTPException(HTTP_403_FORBIDDEN, "Unknown device")

        status = cast(str, row["status"])
        last_seen_at = float(row["last_seen_at"] or 0.0)

        # Auto-unenroll on inactivity (applies only if previously seen)
        if status == "ACTIVE" and last_seen_at > 0 and (time.time() - last_seen_at) > INACTIVE_UNENROLL_SECONDS:
            conn.execute(
                """
                UPDATE devices
                SET status='UNENROLLED', unenrolled_at=?
                WHERE device_id=?
                """,
                (time.time(), device_id),
            )
            conn.execute("UPDATE apikeys SET access=0 WHERE device_id=?", (device_id,))
            conn.commit()

            _audit(
                event="auth.device.auto_unenroll",
                ok=False,
                request=request,
                org=row["org"],
                subject=row["user"],
                device_id=device_id,
                detail="inactive > 30 days",
            )
            raise HTTPException(HTTP_403_FORBIDDEN, "Device unenrolled due to inactivity")

        if status != "ACTIVE":
            _audit(
                event="auth.device.not_active",
                ok=False,
                request=request,
                org=row["org"],
                subject=row["user"],
                device_id=device_id,
                detail=f"status={status}",
            )
            raise HTTPException(HTTP_403_FORBIDDEN, f"Device not active: {status}")

        # Replay protection
        if _nonce_seen_or_store(conn, device_id, nonce, time.time()):
            conn.commit()
            _audit(
                event="auth.device.replay",
                ok=False,
                request=request,
                org=row["org"],
                subject=row["user"],
                device_id=device_id,
                detail="nonce replay",
            )
            raise HTTPException(HTTP_401_UNAUTHORIZED, "Replay detected")

        # Verify signature
        pub_raw = _pubkey_raw_from_b64(cast(str, row["device_pub_b64"]))
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
        try:
            pub.verify(sig, envelope)
        except InvalidSignature:
            conn.commit()
            _audit(
                event="auth.device.bad_signature",
                ok=False,
                request=request,
                org=row["org"],
                subject=row["user"],
                device_id=device_id,
                detail="invalid signature",
            )
            raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid device signature")

        # Update last_seen_at
        conn.execute(
            "UPDATE devices SET last_seen_at=? WHERE device_id=?",
            (time.time(), device_id),
        )
        conn.commit()

        _audit(
            event="auth.device.ok",
            ok=True,
            request=request,
            org=row["org"],
            subject=row["user"],
            device_id=device_id,
        )

        return dict(row)
    finally:
        conn.close()


def require_api_key(request: Request, *, expect_org: Optional[str] = None, expect_device_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate Authorization: Bearer api_xxx against the keystore.
    Optionally enforce org/device binding.
    """
    auth = str(request.headers.get("Authorization", ""))
    if not auth.startswith("Bearer "):
        _audit(event="auth.api.missing", ok=False, request=request, detail="missing bearer")
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Missing Authorization Bearer api key")

    token = auth[7:].strip()
    if not token.startswith("api_"):
        _audit(event="auth.api.bad_type", ok=False, request=request, detail="bad token type")
        raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid token type")

    prefix8 = token[4:12]
    key_hash = _sha256_hex(token)

    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT prefix8, key_hash, org, owner, device_id, scopes_json, access, created_at, last_used_at, expires_at
            FROM apikeys
            WHERE prefix8 = ?
            """,
            (prefix8,),
        ).fetchone()

        if row is None:
            _audit(event="auth.api.unknown_prefix", ok=False, request=request, api_prefix=prefix8, detail="unknown prefix")
            raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid credentials")

        if not hmac.compare_digest(cast(str, row["key_hash"]), key_hash):
            _audit(event="auth.api.hash_mismatch", ok=False, request=request, api_prefix=prefix8, detail="hash mismatch")
            raise HTTPException(HTTP_401_UNAUTHORIZED, "Invalid credentials")

        if not bool(row["access"]):
            _audit(event="auth.api.disabled", ok=False, request=request, api_prefix=prefix8, detail="disabled")
            raise HTTPException(HTTP_403_FORBIDDEN, "API key disabled")

        now = time.time()
        exp = row["expires_at"]
        if exp is not None and now > float(exp):
            _audit(event="auth.api.expired", ok=False, request=request, api_prefix=prefix8, detail="expired")
            raise HTTPException(HTTP_403_FORBIDDEN, "API key expired")

        if expect_org is not None and cast(str, row["org"]) != expect_org:
            _audit(
                event="auth.api.org_mismatch",
                ok=False,
                request=request,
                api_prefix=prefix8,
                detail=f"expect_org={expect_org}",
            )
            raise HTTPException(HTTP_403_FORBIDDEN, "API key org mismatch")

        if expect_device_id is not None and cast(str, row["device_id"]) != expect_device_id:
            _audit(
                event="auth.api.device_mismatch",
                ok=False,
                request=request,
                api_prefix=prefix8,
                detail=f"expect_device={expect_device_id}",
            )
            raise HTTPException(HTTP_403_FORBIDDEN, "API key device mismatch")

        # Update last_used_at
        conn.execute("UPDATE apikeys SET last_used_at=? WHERE prefix8=?", (now, prefix8))
        conn.commit()

        _audit(
            event="auth.api.ok",
            ok=True,
            request=request,
            org=row["org"],
            subject=row["owner"],
            device_id=row["device_id"],
            api_prefix=prefix8,
        )

        return dict(row)
    finally:
        conn.close()


# =============================================================================
# Combined auth: require BOTH device proof and api key
# =============================================================================


async def require_principal(request: Request) -> Principal:
    d = await require_device(request)
    a = require_api_key(request, expect_org=str(d["org"]), expect_device_id=str(d["device_id"]))

    raw_scopes = set(json.loads(str(a["scopes_json"])))
    raw_scopes = validate_scopes(raw_scopes)
    scopes = expand_scopes(raw_scopes)


    return Principal(
        org=str(a["org"]),
        subject=str(a["owner"]),
        device_id=str(a["device_id"]),
        api_prefix=str(a["prefix8"]),
        scopes=scopes,
    )

def need(scope: str, p: Principal) -> None:
    if scope == "*" or scope in p.scopes:
        return
    raise HTTPException(HTTP_403_FORBIDDEN, "insufficient scope")

def need_any(scopes: list[str], p: Principal) -> None:
    if any(s in p.scopes for s in scopes):
        return
    raise HTTPException(HTTP_403_FORBIDDEN, "insufficient scope")

def need_all(scopes: list[str], p: Principal) -> None:
    if all(s in p.scopes for s in scopes):
        return
    raise HTTPException(HTTP_403_FORBIDDEN, "insufficient scope")


# =============================================================================
# Path 2: API key request/approve/claim workflow
# =============================================================================


def inspect_regtoken(token: str) -> Dict[str, Any]:
    """Return verified reg token payload without redeeming it."""
    return verify_regtoken(token)


def get_enrollment_request(request_id: str) -> Optional[Dict[str, Any]]:
    conn = _connect()
    try:
        r = conn.execute(
            """
            SELECT request_id, org, user, email, scopes_json, device_id, device_pub_b64, meta_json,
                   status, created_at, decided_at, decided_by, decision_note
            FROM enrollments
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if r is None:
            return None
        return {
            "request_id": r["request_id"],
            "org": r["org"],
            "user": r["user"],
            "email": r["email"],
            "scopes": json.loads(r["scopes_json"]),
            "device_id": r["device_id"],
            "device_pub_b64": r["device_pub_b64"],
            "meta": json.loads(r["meta_json"] or "{}"),
            "status": r["status"],
            "created_at": r["created_at"],
            "decided_at": r["decided_at"],
            "decided_by": r["decided_by"],
            "decision_note": r["decision_note"],
        }
    finally:
        conn.close()


def create_apikey_request(
    *,
    device_id: str,
    owner: str,
    scopes: Optional[Sequence[str]] = None,
    ttl_days: Optional[int] = 30,
) -> Dict[str, Any]:
    """
    Create a PENDING api key request for an ACTIVE device.
    Token is minted ONLY when the device claims after admin approval.
    """
    conn = _connect()
    try:
        d = conn.execute(
            "SELECT org, scopes_json, status FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if d is None:
            raise HTTPException(HTTP_403_FORBIDDEN, "Unknown device")
        if d["status"] != "ACTIVE":
            raise HTTPException(HTTP_403_FORBIDDEN, f"Device not active: {d['status']}")

        org = cast(str, d["org"])
        device_scopes = set(json.loads(cast(str, d["scopes_json"])))

        device_scopes_raw = set(json.loads(cast(str, d["scopes_json"])))
        device_scopes = expand_scopes(validate_scopes(device_scopes_raw))

        req_scopes_raw = set(scopes or list(device_scopes_raw))
        req_scopes = validate_scopes(req_scopes_raw)

        if not req_scopes.issubset(device_scopes):
            raise HTTPException(HTTP_403_FORBIDDEN, "Requested scopes exceed device approval")

        request_id = uuid.uuid4().hex
        now = time.time()

        conn.execute(
            """
            INSERT INTO apikey_requests
              (request_id, org, device_id, owner, scopes_json, ttl_days, status, created_at)
            VALUES
              (?, ?, ?, ?, ?, ?, 'PENDING', ?)
            """,
            (
                request_id,
                org,
                device_id,
                owner,
                json.dumps(sorted(req_scopes)),
                int(ttl_days) if ttl_days is not None else None,
                now,
            ),
        )
        conn.commit()

        return {
            "request_id": request_id,
            "org": org,
            "device_id": device_id,
            "owner": owner,
            "scopes": sorted(req_scopes),
            "ttl_days": ttl_days,
            "status": "PENDING",
            "created_at": now,
        }
    finally:
        conn.close()


def admin_list_apikey_requests(status: str = "PENDING", org: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        if org:
            rows = conn.execute(
                """
                SELECT request_id, org, device_id, owner, scopes_json, ttl_days,
                       status, created_at, decided_at, decided_by, decision_note,
                       approved_scopes_json, claimed_at, api_prefix
                FROM apikey_requests
                WHERE status = ? AND org = ?
                ORDER BY created_at ASC
                """,
                (status, org),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT request_id, org, device_id, owner, scopes_json, ttl_days,
                       status, created_at, decided_at, decided_by, decision_note,
                       approved_scopes_json, claimed_at, api_prefix
                FROM apikey_requests
                WHERE status = ?
                ORDER BY created_at ASC
                """,
                (status,),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "request_id": r["request_id"],
                    "org": r["org"],
                    "device_id": r["device_id"],
                    "owner": r["owner"],
                    "requested_scopes": json.loads(r["scopes_json"]),
                    "ttl_days": r["ttl_days"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "decided_at": r["decided_at"],
                    "decided_by": r["decided_by"],
                    "decision_note": r["decision_note"],
                    "approved_scopes": json.loads(r["approved_scopes_json"]) if r["approved_scopes_json"] else None,
                    "claimed_at": r["claimed_at"],
                    "api_prefix": r["api_prefix"],
                }
            )
        return out
    finally:
        conn.close()


def admin_approve_apikey_request(
    *,
    request_id: str,
    decided_by: str = "dev",
    decision_note: Optional[str] = None,
    scopes: Optional[Sequence[str]] = None,
) -> bool:
    """
    Approve a PENDING request. Does NOT mint token.
    Scopes must be subset of requested AND device-approved scopes.
    """
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT request_id, device_id, scopes_json, status FROM apikey_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if r is None or r["status"] != "PENDING":
            return False

        device_id = cast(str, r["device_id"])
        d = conn.execute("SELECT scopes_json, status FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        if d is None or d["status"] != "ACTIVE":
            return False

        device_scopes_raw = set(json.loads(cast(str, d["scopes_json"])))
        device_scopes = expand_scopes(validate_scopes(device_scopes_raw))

        requested_raw = set(json.loads(cast(str, r["scopes_json"])))
        requested = validate_scopes(requested_raw)

        approved_raw = set(scopes) if scopes is not None else set(requested)
        approved = validate_scopes(approved_raw)

        if not approved.issubset(requested) or not approved.issubset(device_scopes):
            return False


        now = time.time()
        conn.execute(
            """
            UPDATE apikey_requests
            SET status='APPROVED',
                decided_at=?,
                decided_by=?,
                decision_note=?,
                approved_scopes_json=?
            WHERE request_id=?
            """,
            (now, decided_by, decision_note, json.dumps(sorted(approved)), request_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def admin_deny_apikey_request(
    *,
    request_id: str,
    decided_by: str = "dev",
    decision_note: Optional[str] = None,
) -> bool:
    conn = _connect()
    try:
        r = conn.execute("SELECT status FROM apikey_requests WHERE request_id = ?", (request_id,)).fetchone()
        if r is None or r["status"] != "PENDING":
            return False
        conn.execute(
            """
            UPDATE apikey_requests
            SET status='DENIED', decided_at=?, decided_by=?, decision_note=?
            WHERE request_id=?
            """,
            (time.time(), decided_by, decision_note, request_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_apikey_requests_for_device(device_id: str) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT request_id, org, device_id, owner, scopes_json, ttl_days,
                   status, created_at, decided_at, decided_by, decision_note,
                   approved_scopes_json, claimed_at, api_prefix
            FROM apikey_requests
            WHERE device_id = ?
            ORDER BY created_at DESC
            """,
            (device_id,),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "request_id": r["request_id"],
                    "org": r["org"],
                    "device_id": r["device_id"],
                    "owner": r["owner"],
                    "requested_scopes": json.loads(r["scopes_json"]),
                    "ttl_days": r["ttl_days"],
                    "status": r["status"],
                    "created_at": r["created_at"],
                    "decided_at": r["decided_at"],
                    "decided_by": r["decided_by"],
                    "decision_note": r["decision_note"],
                    "approved_scopes": json.loads(r["approved_scopes_json"]) if r["approved_scopes_json"] else None,
                    "claimed_at": r["claimed_at"],
                    "api_prefix": r["api_prefix"],
                }
            )
        return out
    finally:
        conn.close()


def claim_apikey_request(*, request_id: str, device_id: str) -> Tuple[str, Dict[str, Any]]:
    """
    Atomically claim an APPROVED request (mint api_ token + mark CLAIMED).
    This is intentionally a single-shot operation.
    """
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE;")  # serialize claims

        r = conn.execute(
            """
            SELECT request_id, org, device_id, owner, scopes_json, ttl_days, status, approved_scopes_json
            FROM apikey_requests
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()

        if r is None:
            raise HTTPException(HTTP_403_FORBIDDEN, "Unknown api key request")
        if cast(str, r["device_id"]) != device_id:
            raise HTTPException(HTTP_403_FORBIDDEN, "api key request device mismatch")
        if r["status"] != "APPROVED":
            raise HTTPException(HTTP_403_FORBIDDEN, f"api key request not approved: {r['status']}")

        d = conn.execute(
            "SELECT org, scopes_json, status FROM devices WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        if d is None or d["status"] != "ACTIVE":
            raise HTTPException(HTTP_403_FORBIDDEN, "Device not active")

        org = cast(str, d["org"])
        if org != cast(str, r["org"]):
            raise HTTPException(HTTP_403_FORBIDDEN, "org mismatch")

        device_scopes_raw = set(json.loads(cast(str, d["scopes_json"])))
        device_scopes = expand_scopes(validate_scopes(device_scopes_raw))

        scopes_src = cast(str, r["approved_scopes_json"] or r["scopes_json"])
        req_scopes_raw = set(json.loads(scopes_src))
        req_scopes = validate_scopes(req_scopes_raw)

        if not req_scopes.issubset(device_scopes):
            raise HTTPException(HTTP_403_FORBIDDEN, "Approved scopes exceed device approval")


        ttl_days = r["ttl_days"]
        ttl = int(ttl_days) if ttl_days is not None else 30

        # Mint token (retry on prefix collision)
        token: Optional[str] = None
        prefix8: Optional[str] = None
        now = time.time()
        exp = now + ttl * 86400
        owner = cast(str, r["owner"])

        for _ in range(5):
            t = _new_apikey()
            pfx = t[4:12]
            try:
                conn.execute(
                    """
                    INSERT INTO apikeys
                      (prefix8, key_hash, org, owner, device_id, scopes_json, access, created_at, last_used_at, expires_at)
                    VALUES
                      (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        pfx,
                        _sha256_hex(t),
                        org,
                        owner,
                        device_id,
                        json.dumps(sorted(req_scopes)),
                        now,
                        now,
                        exp,
                    ),
                )
                token, prefix8 = t, pfx
                break
            except sqlite3.IntegrityError:
                token, prefix8 = None, None

        if token is None or prefix8 is None:
            raise HTTPException(HTTP_400_BAD_REQUEST, "Failed to mint api key (prefix collision)")

        cur = conn.execute(
            """
            UPDATE apikey_requests
            SET status='CLAIMED', claimed_at=?, api_prefix=?
            WHERE request_id=? AND status='APPROVED'
            """,
            (time.time(), prefix8, request_id),
        )
        if cur.rowcount != 1:
            raise HTTPException(HTTP_403_FORBIDDEN, "Request already claimed")

        conn.commit()

        rec = {
            "prefix": prefix8,
            "org": org,
            "owner": owner,
            "device_id": device_id,
            "scopes": sorted(req_scopes),
            "created_at": now,
            "expires_at": exp,
        }
        return token, rec

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_apikeys_for_device(device_id: str) -> List[Dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT prefix8, org, owner, device_id, scopes_json, access, created_at, last_used_at, expires_at
            FROM apikeys
            WHERE device_id = ?
            ORDER BY created_at DESC
            """,
            (device_id,),
        ).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "prefix": r["prefix8"],
                    "org": r["org"],
                    "owner": r["owner"],
                    "device_id": r["device_id"],
                    "scopes": json.loads(r["scopes_json"]),
                    "access": bool(r["access"]),
                    "created_at": r["created_at"],
                    "last_used_at": r["last_used_at"],
                    "expires_at": r["expires_at"],
                }
            )
        return out
    finally:
        conn.close()


def revoke_apikey_for_device(device_id: str, prefix8: str) -> bool:
    conn = _connect()
    try:
        cur = conn.execute(
            "UPDATE apikeys SET access=0 WHERE device_id=? AND prefix8=?",
            (device_id, prefix8[:8]),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def admin_list_audit(
    *,
    limit: int = 200,
    event: Optional[str] = None,
    org: Optional[str] = None,
) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    conn = _connect()
    try:
        q = """
            SELECT id, ts, event, ok, org, subject, device_id, api_prefix, ip, ua, detail
            FROM audit
        """
        params: List[Any] = []
        wh: List[str] = []
        if event:
            wh.append("event = ?")
            params.append(event)
        if org:
            wh.append("org = ?")
            params.append(org)
        if wh:
            q += " WHERE " + " AND ".join(wh)
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(q, tuple(params)).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "event": r["event"],
                    "ok": bool(r["ok"]),
                    "org": r["org"],
                    "subject": r["subject"],
                    "device_id": r["device_id"],
                    "api_prefix": r["api_prefix"],
                    "ip": r["ip"],
                    "ua": r["ua"],
                    "detail": r["detail"],
                }
            )
        return out
    finally:
        conn.close()


# =============================================================================
# Client-side helpers (CLI can import these)
# =============================================================================


def generate_device_keypair() -> Tuple[str, str]:
    """
    Generate an Ed25519 device keypair.

    Returns:
      (private_pem_str, public_raw_b64url)
    """
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()

    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_b64 = _b64u_encode(pub_raw)
    return priv_pem, pub_b64


def load_device_private_key(pem_text: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem_text.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("Not an Ed25519 private key")
    return key


def sign_envelope(
    *,
    device_private_pem: str,
    method: str,
    target: str,
    timestamp: int,
    nonce: str,
    body_bytes: bytes,
) -> str:
    """
    Produce X-SL-Signature value (base64url) for the canonical envelope.
    """
    priv = load_device_private_key(device_private_pem)
    body_hash = _sha256_hex(body_bytes)
    env = f"{method.upper()}\n{target}\n{timestamp}\n{nonce}\n{body_hash}".encode("utf-8")
    sig = priv.sign(env)
    return _b64u_encode(sig)
