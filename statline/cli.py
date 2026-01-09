# statline/cli.py
from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import io
import json
import os
import platform
import re
import secrets
import sys
import time

# ── stdlib ────────────────────────────────────────────────────────────────────
from dataclasses import dataclass
from os import getenv
from pathlib import Path
from typing import (
    Any,
    Dict,
    Generator,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    TextIO,
    Tuple,
    TypedDict,
    Union,
    cast,
)
from urllib.parse import urlencode

# ── third-party ───────────────────────────────────────────────────────────────
import click  # Typer is built on Click
import typer

from statline.core.adapters import load as load_adapter
from statline.core.calculator import score_rows_from_raw

# ── CLI versioning ────────────────────────────────────────────────────────────

CLI_VERSION = "3.0.0"
CLI_NAME = "StatLine"

# ── HTTP backend (quiet for type checkers) ────────────────────────────────────

# Avoid mypy "no-redef": import into distinct names, then pick one alias.
_http: Any  # single module-like alias we treat as Any to keep linters quiet
try:
    import httpx as _httpx

    _http = _httpx
    _http_lib = "httpx"
except Exception:  # pragma: no cover
    try:
        import requests as _requests  # pyright: ignore[reportMissingModuleSource]
    except Exception as _e:  # extremely defensive; shouldn't happen in prod
        raise RuntimeError("Neither httpx nor requests is available") from _e
    _http = _requests
    _http_lib = "requests"

# ── banner & timing defaults ──────────────────────────────────────────────────

STATLINE_DEBUG_TIMING: bool = os.getenv("STATLINE_DEBUG") == "1"
DEFAULT_SLAPI_URL: str = os.getenv("SLAPI_URL", "http://127.0.0.1:8000").rstrip("/")

# Legacy env (v2) — still honored if you talk to an older SLAPI.
DEFAULT_SLAPI_KEY: Optional[str] = os.getenv("SLAPI_KEY")

# mutable runtime config (don’t mutate ALL-CAPS constants)
_slapi_url: str = DEFAULT_SLAPI_URL
_slapi_key: Optional[str] = DEFAULT_SLAPI_KEY

# Connectivity/auth state decided once per process in the root callback.
_reachable: bool = False
_online: bool = False  # reachable + authenticated for guarded endpoints

# Explicit mode selection:
#   - auto   : probe; use SLAPI if reachable+authed else local
#   - local  : never talk to network; always local StatLine scoring
#   - remote : require SLAPI reachable+authed; error otherwise
Mode = Literal["auto", "local", "remote"]
_mode: Mode = cast(Mode, os.getenv("STATLINE_MODE", "auto").strip().lower() or "auto")
_mode = "auto" if _mode not in ("auto", "local", "remote") else _mode

app = typer.Typer(no_args_is_help=True)

# Subcommands
auth_app = typer.Typer(no_args_is_help=True, help="Device enrollment + API key management (v3+)")
mod_app = typer.Typer(no_args_is_help=True, help="Moderation tools (requires 'moderation' scope)")
admin_app = typer.Typer(no_args_is_help=True, help="Admin tools (requires 'admin' scope)")
sys_app = typer.Typer(no_args_is_help=True, help="System/status helpers")
app.add_typer(auth_app, name="auth")
app.add_typer(mod_app, name="mod")
app.add_typer(admin_app, name="admin")
app.add_typer(sys_app, name="sys")

_BANNER_LINE: str = f"=== {CLI_NAME} v{CLI_VERSION} — Adapter-Driven Scoring ==="
_BANNER_REGEX = re.compile(r"^===\s*StatLine\b.*===\s*$")


def _print_banner() -> None:
    fg: Any = getattr(typer.colors, "CYAN", None)
    typer.secho(_BANNER_LINE, fg=fg, bold=True)


def ensure_banner() -> None:
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        _print_banner()
        return
    root = ctx.find_root()
    if root.obj is None:
        root.obj = {}
    if not root.obj.get("_statline_banner_shown"):
        _print_banner()
        root.obj["_statline_banner_shown"] = True


@contextlib.contextmanager
def suppress_duplicate_banner_stdout() -> Generator[None, None, None]:
    class _Filter(io.TextIOBase):
        def __init__(self, underlying: TextIO) -> None:
            self._u: TextIO = underlying
            self._swallowed: bool = False
            self._buf: str = ""

        def write(self, s: str) -> int:
            self._buf += s
            out: List[str] = []
            while True:
                if "\n" not in self._buf:
                    break
                line, self._buf = self._buf.split("\n", 1)
                if not self._swallowed and _BANNER_REGEX.match(line.strip()):
                    self._swallowed = True
                    continue
                out.append(line + "\n")
            if out:
                return self._u.write("".join(out))
            return 0

        def flush(self) -> None:
            if self._buf:
                chunk = self._buf
                self._buf = ""
                self._u.write(chunk)
            self._u.flush()

        def fileno(self) -> int:
            return self._u.fileno()

        def isatty(self) -> bool:
            try:
                return self._u.isatty()
            except Exception:
                return False

    orig: TextIO = sys.stdout
    filt = _Filter(orig)
    try:
        sys.stdout = cast(TextIO, filt)
        yield
    finally:
        try:
            filt.flush()
        except Exception:
            pass
        sys.stdout = orig


# ── secrets locations ---------------------------------------------------------

_STATLINE_DIR = Path(__file__).resolve().parent
LOG_DIR: Path = _STATLINE_DIR / "log"
TAMPER_NOTES: Path = LOG_DIR / "tamper-notes.log"
BUG_NOTES: Path = LOG_DIR / "bug-notes.log"


def _log_note(path: Path, line: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        # Logging must never crash the CLI
        pass


def _local_adapter_names() -> List[str]:
    """List locally-available adapters for demo/fallback."""
    try:
        from statline.core.adapters import list_names as _L

        names = _L()
        return [str(n) for n in names if str(n).strip()]
    except Exception as e:
        _log_note(BUG_NOTES, f"[local_adapter_names] error: {e!r}")
        # Last-ditch demo set
        return ["rbw5", "demo"]


def _fallback_banner(reason: str) -> None:
    typer.secho(
        f"Warning: {reason}. Defaulting to demo/local adapters.",
        fg=typer.colors.YELLOW,
        bold=True,
    )


def _candidate_secret_dirs() -> List[Path]:
    env = getenv("STATLINE_SECRETS")
    home = Path.home()
    dirs: List[Path] = []
    if env:
        dirs.append(Path(env))
    dirs += [
        Path.cwd() / "statline" / "secrets",
        Path.cwd() / "secrets",
        _STATLINE_DIR / "secrets",
        home / ".config" / "statline",
        home / ".statline",
    ]
    return dirs


def _resolve_secrets_dir() -> Path:
    for p in _candidate_secret_dirs():
        if p.exists():
            return p
    return _STATLINE_DIR / "secrets"


SECRETS_DIR: Path = _resolve_secrets_dir()

# v3+ auth secrets
DEVICEKEY_PATH: Path = SECRETS_DIR / "DEVICEKEY"  # Ed25519 private key (PEM)
DEVICEID_PATH: Path = SECRETS_DIR / "DEVICEID"  # UUID assigned by /v3/auth/enroll
APIKEY_PATH: Path = SECRETS_DIR / "APIKEY"  # api_ token (bearer)

# legacy v2 token (reg_) still optionally supported for older servers
REGKEY_PATH: Path = SECRETS_DIR / "REGKEY"

# legacy admin helper file (not required by v3; still useful for local tooling)
DEVKEY_PATH: Path = SECRETS_DIR / "DEVKEY"

KEYS_DIR: Path = SECRETS_DIR / "keys"


def _read_text(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def _has_regkey() -> bool:
    try:
        return REGKEY_PATH.exists() and bool((_read_text(REGKEY_PATH) or "").strip())
    except Exception:
        return False


def _describe_regkey() -> str:
    try:
        s = (_read_text(REGKEY_PATH) or "").strip()
        return f"{REGKEY_PATH}: {'present' if s else 'empty'}"
    except Exception as e:
        return f"{REGKEY_PATH}: error reading ({e!r})"


def _describe_device() -> str:
    try:
        did = (_read_text(DEVICEID_PATH) or "").strip()
        return f"{DEVICEID_PATH}: {'present' if did else 'empty'}"
    except Exception as e:
        return f"{DEVICEID_PATH}: error reading ({e!r})"


def _describe_apikey() -> str:
    try:
        k = (_read_text(APIKEY_PATH) or "").strip()
        return f"{APIKEY_PATH}: {'present' if k else 'empty'}"
    except Exception as e:
        return f"{APIKEY_PATH}: error reading ({e!r})"


def _describe_auth_state() -> str:
    parts = [_describe_device(), _describe_apikey()]
    if _has_regkey():
        parts.append(_describe_regkey())
    return "\n".join(parts)


# ── base64url helpers ─────────────────────────────────────────────────────────

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


# ── v3 device proof headers ───────────────────────────────────────────────────

HDR_DEVICE_ID = "X-SL-Device"
HDR_TIMESTAMP = "X-SL-Timestamp"
HDR_NONCE = "X-SL-Nonce"
HDR_SIGNATURE = "X-SL-Signature"


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _has_device() -> bool:
    return DEVICEKEY_PATH.exists() and bool((_read_text(DEVICEID_PATH) or "").strip())


def _has_apikey() -> bool:
    s = (_read_text(APIKEY_PATH) or "").strip()
    return bool(s) and s.startswith("api_")


def _has_device_id() -> bool:
    try:
        return bool((_read_text(DEVICEID_PATH) or "").strip())
    except Exception:
        return False


def _read_device_id() -> str:
    s = (_read_text(DEVICEID_PATH) or "").strip()
    if not s:
        raise typer.BadParameter(f"Missing DEVICEID at {DEVICEID_PATH}. Run: statline auth enroll")
    return s


def _read_apikey() -> str:
    s = (_read_text(APIKEY_PATH) or "").strip()
    if not s:
        raise typer.BadParameter(f"Missing APIKEY at {APIKEY_PATH}. Run: statline auth apikey-request")
    if s.startswith("SLAPI_KEY="):
        s = s.split("=", 1)[1].strip()
    if not s.startswith("api_"):
        raise typer.BadParameter(f"{APIKEY_PATH} doesn’t look like an api_ token.")
    return s


def _read_regkey() -> str:
    s = _read_text(REGKEY_PATH)
    if not s:
        tried = "\n  - " + "\n  - ".join(str(x / "REGKEY") for x in _candidate_secret_dirs())
        raise typer.BadParameter(
            "Legacy access key missing. Paste the gifted reg_ token into a file named REGKEY.\n"
            f"Checked:{tried}\n"
            f"Recommended location: {REGKEY_PATH}"
        )
    s = s.strip()
    if s.startswith("SLAPI_KEY="):
        s = s.split("=", 1)[1].strip()
    if not s.startswith("reg_"):
        raise typer.BadParameter(f"{REGKEY_PATH} doesn’t look like a reg_ token.")
    return s


def _load_ed25519_private() -> Any:
    """Load the Ed25519 private key from DEVICEKEY_PATH (PEM)."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as e:
        raise typer.BadParameter(
            "Missing dependency: cryptography (required for v3 auth).\n"
            "Install with: pip install 'statline[api]'  (or)  pip install cryptography"
        ) from e

    if not DEVICEKEY_PATH.exists():
        raise typer.BadParameter(f"Missing DEVICEKEY at {DEVICEKEY_PATH}. Run: statline auth device-init")

    key_bytes = DEVICEKEY_PATH.read_bytes()
    try:
        priv = serialization.load_pem_private_key(key_bytes, password=None)
    except Exception as e:
        raise typer.BadParameter(f"Failed to read DEVICEKEY PEM: {e}") from e

    if not isinstance(priv, ed25519.Ed25519PrivateKey):
        raise typer.BadParameter("DEVICEKEY must be an Ed25519 private key.")

    return priv


def _ensure_ed25519_keypair(*, force: bool = False) -> Any:
    """Ensure an Ed25519 DEVICEKEY exists on disk and return the private key."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception as e:
        raise typer.BadParameter(
            "Missing dependency: cryptography (required for v3 auth).\n"
            "Install with: pip install 'statline[api]'  (or)  pip install cryptography"
        ) from e

    if DEVICEKEY_PATH.exists() and not force:
        return _load_ed25519_private()

    priv = ed25519.Ed25519PrivateKey.generate()
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    DEVICEKEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEVICEKEY_PATH.write_bytes(pem)
    return priv


def _device_pub_b64_from_priv(priv: Any) -> str:
    """
    Return base64url(raw_public_key_bytes) (no padding) expected by v3 /auth/enroll.
    IMPORTANT: server uses urlsafe b64 decoding.
    """
    from cryptography.hazmat.primitives import serialization

    pub = priv.public_key()
    raw = pub.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return _b64url(raw)


def _device_proof_headers(method: str, target: str, body: bytes) -> Dict[str, str]:
    """Build v3+ device proof headers (Ed25519 signature over canonical envelope)."""
    priv = _load_ed25519_private()
    device_id = _read_device_id()
    ts = str(int(time.time()))
    nonce = secrets.token_urlsafe(18)
    body_hash = _sha256_hex(body)
    envelope = f"{method.upper()}\n{target}\n{ts}\n{nonce}\n{body_hash}".encode("utf-8")
    sig = priv.sign(envelope)
    return {
        HDR_DEVICE_ID: device_id,
        HDR_TIMESTAMP: ts,
        HDR_NONCE: nonce,
        HDR_SIGNATURE: _b64url(sig),
    }


def _best_auth_mode(*, guarded: bool) -> Literal["principal", "regkey", "none"]:
    """
    Choose the best auth scheme available on this machine.

    v3 servers:
      - guarded endpoints require principal (device + api key)
    legacy v2 servers:
      - may accept reg_ token as bearer
    """
    if guarded and _has_device() and _has_apikey() and _has_device_id():
        return "principal"
    if _has_regkey():
        return "regkey"
    return "none"


def _headers(
    method: str,
    target: str,
    body: bytes,
    *,
    extra: Optional[Dict[str, str]] = None,
    auth: Literal["none", "device", "principal", "regkey", "admin"] = "principal",
) -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json"}

    # Legacy v2 "admin" mode (if you talk to an older server that still expects local admin headers).
    if auth == "admin":
        if extra:
            h.update(extra)
        return h

    if auth in {"device", "principal"}:
        h.update(_device_proof_headers(method, target, body))

    if auth == "principal":
        api = _read_apikey()
        h["Authorization"] = f"Bearer {api}"
    elif auth == "regkey":
        key = _read_regkey()
        h["Authorization"] = f"Bearer {key}"
        h["X-StatLine-Key"] = key

    if extra:
        h.update(extra)
    return h


@dataclass
class SLAPIHttpError(Exception):
    status_code: int
    message: str
    detail: Any = None

    def __str__(self) -> str:
        base = f"SLAPI {self.status_code}: {self.message}"
        if self.detail is None:
            return base
        return f"{base} :: {self.detail}"


def _pretty_detail(detail: Any) -> str:
    """
    Normalize FastAPI/Pydantic-ish error shapes into something readable.
    Handles:
      - {"detail": "..."}
      - {"detail": [{"loc":..., "msg":..., "type":...}, ...]}
      - plain strings / lists
    """
    try:
        if isinstance(detail, dict) and "detail" in detail:
            d = detail["detail"] # pyright: ignore[reportUnknownVariableType]
        else:
            d = detail # pyright: ignore[reportUnknownVariableType]

        if isinstance(d, str):
            return d.strip()

        if isinstance(d, list):
            # Pydantic validation errors often arrive as list[dict]
            parts: List[str] = []
            for it in d: # pyright: ignore[reportUnknownVariableType]
                if isinstance(it, dict):
                    loc = it.get("loc") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    msg = it.get("msg") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    typ = it.get("type") # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    loc_s = ""
                    if isinstance(loc, (list, tuple)):
                        loc_s = ".".join(str(x) for x in loc if str(x)) # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                    elif loc is not None:
                        loc_s = str(loc) # pyright: ignore[reportUnknownArgumentType]
                    blob = " / ".join([x for x in [loc_s, str(msg or "").strip(), str(typ or "").strip()] if x]) # pyright: ignore[reportUnknownArgumentType]
                    if blob:
                        parts.append(blob)
                else:
                    s = str(it).strip() # pyright: ignore[reportUnknownArgumentType]
                    if s:
                        parts.append(s)
            return "; ".join(parts) if parts else str(d) # pyright: ignore[reportUnknownArgumentType]

        if isinstance(d, dict):
            # sometimes "detail" is a dict
            return json.dumps(d, ensure_ascii=False)

        return str(d) # pyright: ignore[reportUnknownArgumentType]
    except Exception:
        try:
            return str(detail) # pyright: ignore[reportUnknownArgumentType]
        except Exception:
            return "unknown error"


def _raise_for_status(resp: Any) -> None:
    code = getattr(resp, "status_code", None)
    if code is None or (200 <= code < 300):
        return
    try:
        detail = resp.json()
    except Exception:
        detail = getattr(resp, "text", "")

    # Make error messages actually useful.
    dpretty = _pretty_detail(detail)

    if code in (401, 403):
        raise PermissionError(f"Unauthorized ({code}): {dpretty}")
    if code == 422:
        # Validation error: very often caused by hitting the wrong endpoint shape.
        raise typer.BadParameter(f"Request rejected (422): {dpretty}")
    if code in (404, 502, 503, 504):
        raise ConnectionError(f"Server/network error ({code}): {dpretty}")

    raise SLAPIHttpError(status_code=int(code), message="Request failed", detail=dpretty)


def _is_http_404(err: BaseException) -> bool:
    s = str(err)
    return ("SLAPI 404" in s) or (" 404" in s) or ("Not Found" in s)


# ── HTTP client helpers ───────────────────────────────────────────────────────

def _http_get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Any:
    q = urlencode(params or {}, doseq=True) if params else ""
    target = f"{path}?{q}" if q else path
    url = f"{_slapi_url}{target}"

    # Route-level auth selection (v3 first; v2 legacy supported)
    if path.startswith("/v3/admin") or path.startswith("/v3/mod"):
        auth: Literal["none", "device", "principal", "regkey", "admin"] = "principal"
    elif path.startswith("/v3/auth/enroll") or path.startswith("/v3/health") or path == "/":
        auth = "none"
    elif path.startswith("/v3/auth/whoami"):
        auth = "principal"
    elif (
        path.startswith("/v3/auth/apikey-requests")
        or path.startswith("/v3/auth/apikeys")
        or path.startswith("/v3/auth/device")
    ):
        auth = "device"
    elif path.startswith("/v3/"):
        auth = _best_auth_mode(guarded=True)
    # ---- legacy v2 ----
    elif path.startswith("/v2/admin"):
        auth = "admin"
    elif path.startswith("/v2/auth/enroll") or path.startswith("/v2/health"):
        auth = "none"
    elif path.startswith("/v2/auth/whoami"):
        auth = "principal"
    elif path.startswith("/v2/auth/apikey-requests") or path.startswith("/v2/auth/apikeys"):
        auth = "device"
    else:
        auth = _best_auth_mode(guarded=True)

    headers = _headers("GET", target, b"", extra=extra_headers, auth=auth)
    try:
        if _http_lib == "httpx" and hasattr(_http, "Client"):
            with _http.Client(timeout=60.0) as c:
                r = c.get(url, headers=headers)
                _raise_for_status(r)
                return r.json()
        else:
            r = _http.get(url, headers=headers, timeout=60.0)
            _raise_for_status(r)
            return r.json()
    except Exception as e:
        etxt = repr(e)
        if "ConnectError" in etxt or "ConnectionError" in etxt or "Connection refused" in etxt:
            raise ConnectionError(f"Connection failed to {url}: {e}") from e
        raise


def _http_post(
    path: str,
    payload: Any,
    *,
    extra_headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    q = urlencode(params or {}, doseq=True) if params else ""
    target = f"{path}?{q}" if q else path
    url = f"{_slapi_url}{target}"

    # Route-level auth selection (v3 first; v2 legacy supported)
    if path.startswith("/v3/admin") or path.startswith("/v3/mod"):
        auth: Literal["none", "device", "principal", "regkey", "admin"] = "principal"
    elif path.startswith("/v3/auth/enroll") or path.startswith("/v3/health") or path == "/":
        auth = "none"
    elif path.startswith("/v3/auth/whoami"):
        auth = "principal"
    elif (
        path.startswith("/v3/auth/apikey-requests")
        or path.startswith("/v3/auth/apikeys")
        or path.startswith("/v3/auth/device")
    ):
        auth = "device"
    elif path.startswith("/v3/"):
        auth = _best_auth_mode(guarded=True)
    # ---- legacy v2 ----
    elif path.startswith("/v2/admin"):
        auth = "admin"
    elif path.startswith("/v2/auth/enroll") or path.startswith("/v2/health"):
        auth = "none"
    elif path.startswith("/v2/auth/whoami"):
        auth = "principal"
    elif path.startswith("/v2/auth/apikey-requests") or path.startswith("/v2/auth/apikeys"):
        auth = "device"
    else:
        auth = _best_auth_mode(guarded=True)

    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = _headers("POST", target, body, extra=extra_headers, auth=auth)
    try:
        if _http_lib == "httpx" and hasattr(_http, "Client"):
            with _http.Client(timeout=300.0) as c:
                r = c.post(url, headers=headers, content=body)
                _raise_for_status(r)
                return r.json()
        else:
            r = _http.post(url, headers=headers, data=body, timeout=300.0)
            _raise_for_status(r)
            return r.json()
    except Exception as e:
        etxt = repr(e)
        if "ConnectError" in etxt or "ConnectionError" in etxt or "Connection refused" in etxt:
            raise ConnectionError(f"Connection failed to {url}: {e}") from e
        raise


def _http_delete(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Any:
    q = urlencode(params or {}, doseq=True) if params else ""
    target = f"{path}?{q}" if q else path
    url = f"{_slapi_url}{target}"

    # Route-level auth selection (v3 first; v2 legacy supported)
    if path.startswith("/v3/admin") or path.startswith("/v3/mod"):
        auth: Literal["none", "device", "principal", "regkey", "admin"] = "principal"
    elif path.startswith("/v3/auth/enroll") or path.startswith("/v3/health") or path == "/":
        auth = "none"
    elif path.startswith("/v3/auth/whoami"):
        auth = "principal"
    elif (
        path.startswith("/v3/auth/apikey-requests")
        or path.startswith("/v3/auth/apikeys")
        or path.startswith("/v3/auth/device")
    ):
        auth = "device"
    elif path.startswith("/v3/"):
        auth = _best_auth_mode(guarded=True)
    # ---- legacy v2 ----
    elif path.startswith("/v2/admin"):
        auth = "admin"
    elif path.startswith("/v2/auth/enroll") or path.startswith("/v2/health"):
        auth = "none"
    elif path.startswith("/v2/auth/whoami"):
        auth = "principal"
    elif path.startswith("/v2/auth/apikey-requests") or path.startswith("/v2/auth/apikeys"):
        auth = "device"
    else:
        auth = _best_auth_mode(guarded=True)

    headers = _headers("DELETE", target, b"", extra=extra_headers, auth=auth)
    try:
        if _http_lib == "httpx" and hasattr(_http, "Client"):
            with _http.Client(timeout=60.0) as c:
                r = c.delete(url, headers=headers)
                _raise_for_status(r)
                return r.json() if getattr(r, "content", b"") else {}
        else:
            r = _http.delete(url, headers=headers, timeout=60.0)
            _raise_for_status(r)
            try:
                return r.json()
            except Exception:
                return {}
    except Exception as e:
        etxt = repr(e)
        if "ConnectError" in etxt or "ConnectionError" in etxt or "Connection refused" in etxt:
            raise ConnectionError(f"Connection failed to {url}: {e}") from e
        raise


# ── v3 wrappers with v2 fallback ──────────────────────────────────────────────

def _get_v3(path_v3: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    try:
        return _http_get(path_v3, params=params)
    except Exception as e:
        if _is_http_404(e) and path_v3.startswith("/v3/"):
            return _http_get("/v2/" + path_v3[len("/v3/") :], params=params)
        raise


def _post_v3(path_v3: str, payload: Any, *, params: Optional[Dict[str, Any]] = None) -> Any:
    try:
        return _http_post(path_v3, payload, params=params)
    except Exception as e:
        if _is_http_404(e) and path_v3.startswith("/v3/"):
            return _http_post("/v2/" + path_v3[len("/v3/") :], payload, params=params)
        raise


def _delete_v3(path_v3: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    try:
        return _http_delete(path_v3, params=params)
    except Exception as e:
        if _is_http_404(e) and path_v3.startswith("/v3/"):
            return _http_delete("/v2/" + path_v3[len("/v3/") :], params=params)
        raise


# ── Reachability probe & runtime banner ───────────────────────────────────────

def _tcp_probe(base_url: str, timeout: float = 1.5) -> bool:
    """Best-effort TCP reachability check to decide online vs local mode."""
    try:
        import socket
        from urllib.parse import urlparse

        u = urlparse(base_url if "://" in base_url else f"http://{base_url}")
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if (u.scheme or "http") == "https" else 80)

        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _print_mode_banner(*, reachable: bool, authed: bool, url: str, mode: Mode) -> None:
    if mode == "local":
        typer.secho(
            "[SLAPI LOCAL] Offline mode — using StatLine core scoring (no network).",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        return

    if not reachable:
        if mode == "remote":
            typer.secho(
                f"[SLAPI REMOTE] Required SLAPI unreachable at {url}.",
                fg=typer.colors.RED,
                bold=True,
            )
            return
        typer.secho(
            "[SLAPI LOCAL] SLAPI unreachable — using local adapters.",
            fg=typer.colors.YELLOW,
            bold=True,
        )
        return

    if authed:
        tag = "[SLAPI REMOTE]" if mode == "remote" else "[SLAPI ONLINE]"
        typer.secho(f"{tag} Using SLAPI at {url}", fg=typer.colors.GREEN, bold=True)
        return

    if mode == "remote":
        typer.secho(
            f"[SLAPI REMOTE] SLAPI reachable at {url}, but you're not authenticated.",
            fg=typer.colors.RED,
            bold=True,
        )
        typer.echo("Run: statline auth status  (then enroll / request / claim an API key).")
        return

    typer.secho(
        f"[SLAPI REACHABLE] SLAPI at {url} is reachable, but you're not authenticated.",
        fg=typer.colors.YELLOW,
        bold=True,
    )
    typer.echo("Run: statline auth status  (then enroll / request / claim an API key).")


# ── dataset picker ────────────────────────────────────────────────────────────

def api_list_datasets() -> List[Dict[str, str]]:
    """
    v3: GET /v3/datasets -> {"datasets": ["file.csv", ...]}
    v2 legacy: may return {"datasets": [{"name":..., "path":...}, ...]}
    """
    try:
        data = _get_v3("/v3/datasets")
        ds = data.get("datasets", [])
        out: List[Dict[str, str]] = []

        if isinstance(ds, list):
            if ds and isinstance(ds[0], dict):
                # legacy format
                for it in ds:  # pyright: ignore[reportUnknownVariableType]
                    name = str(it.get("name", "") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                    path = str(it.get("path", "") or "")  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                    if name and path:
                        out.append({"name": name, "path": path})
                return out
            # v3 format (list[str])
            for name in ds:  # pyright: ignore[reportUnknownVariableType]
                s = str(name).strip()  # pyright: ignore[reportUnknownArgumentType]
                if s:
                    out.append({"name": s, "path": s})
        return out
    except Exception:
        return []


def local_list_datasets() -> List[Dict[str, str]]:
    """
    Fallback if server-side datasets can't be listed or we want local view.
    CLI lives at statline/cli.py => datasets are at statline/data/stats/*.csv
    """
    out: List[Dict[str, str]] = []
    try:
        base = Path(__file__).resolve().parent  # statline/
        d = base / "data" / "stats"
        if d.exists():
            for p in sorted(d.glob("*.csv")):
                out.append({"name": p.name, "path": str(p)})
    except Exception:
        pass
    return out


def _pick_dataset_via_menu(title: str) -> Optional[str]:
    candidates: List[Dict[str, str]] = []
    if _mode != "local" and _online:
        candidates = api_list_datasets()
    if not candidates:
        candidates = local_list_datasets()

    if not candidates:
        p = typer.prompt(f"{title} (enter a CSV path)", default="stats.csv").strip()
        return p or None

    typer.secho(title, fg=typer.colors.MAGENTA, bold=True)
    for i, c in enumerate(candidates, 1):
        typer.echo(f"  {i}. {c['name']}")
    other_idx = len(candidates) + 1
    typer.echo(f"  {other_idx}. Other (enter path)")

    while True:
        raw = str(typer.prompt("Select", default="1")).strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]["path"]
            if idx == other_idx - 1:
                p = typer.prompt("CSV path", default="stats.csv").strip()
                return p or None
        typer.secho("  Invalid selection.", fg=typer.colors.RED)


# ── typing helpers ────────────────────────────────────────────────────────────

Row = Dict[str, Any]
Rows = List[Row]


class _ViewRow(TypedDict):  # pyright: ignore[reportUnusedClass]
    Rank: int
    Name: str
    PRI: int
    Raw: str
    Context: str


# ── YAML support (optional) ───────────────────────────────────────────────────

class _YamlLikeProtocol:
    CSafeLoader: Any
    SafeLoader: Any

    def load(self, stream: str, *, Loader: Any) -> Any: ...
    def safe_load(self, stream: str) -> Any: ...


yaml_mod: Optional[_YamlLikeProtocol]
_yaml_loader: Optional[Any]
try:
    import yaml as _yaml_import

    yaml_mod = cast(_YamlLikeProtocol, _yaml_import)
    _yaml_loader = getattr(_yaml_import, "CSafeLoader", getattr(_yaml_import, "SafeLoader", None))
except Exception:
    yaml_mod = None
    _yaml_loader = None


def _yaml_load_text(text: str) -> Any:
    if yaml_mod is None:
        raise typer.BadParameter("PyYAML not installed; cannot read YAML.")
    if _yaml_loader is not None:
        return yaml_mod.load(text, Loader=_yaml_loader)
    return yaml_mod.safe_load(text)


# ── IO helpers ────────────────────────────────────────────────────────────────

def _read_rows(input_path: Path) -> Iterable[Row]:
    if str(input_path) == "-":
        reader = csv.DictReader(sys.stdin)
        for row in reader:
            yield {str(k): v for k, v in row.items()}
        return
    if not input_path.exists():
        raise typer.BadParameter(f"Input file not found: {input_path}. Pass a YAML/CSV or use '-' for stdin.")
    suffix = input_path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data_text = input_path.read_text(encoding="utf-8")
        data: Any = _yaml_load_text(data_text)
        src: List[Mapping[str, Any]] = []
        from collections.abc import Mapping as AbcMapping

        if isinstance(data, AbcMapping):
            data_map = cast(Mapping[str, Any], data)
            rows_val_obj: Any = data_map.get("rows")
            if not isinstance(rows_val_obj, list):
                raise typer.BadParameter("YAML must be a list[dict] or {rows: list[dict]}.")
            rows_val: List[object] = cast(List[object], rows_val_obj)
            for r_any in rows_val:
                if isinstance(r_any, AbcMapping):
                    src.append(cast(Mapping[str, Any], r_any))
        elif isinstance(data, list):
            data_list: List[object] = cast(List[object], data)
            for r_any in data_list:
                if isinstance(r_any, AbcMapping):
                    src.append(cast(Mapping[str, Any], r_any))
        else:
            raise typer.BadParameter("YAML must be a list[dict] or {rows: list[dict]}.")
        for r in src:
            yield {str(k): v for k, v in r.items()}
        return
    if suffix == ".csv":
        with input_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield {str(k): v for k, v in row.items()}
        return
    raise typer.BadParameter("Input must be .yaml/.yml or .csv (JSON not supported).")


def _name_for_row(raw: Mapping[str, Any], preferred: Optional[List[str]] = None) -> str:
    if preferred:
        for key in preferred:
            for variant in (key, key.lower(), key.upper(), key.title()):
                v = raw.get(variant)
                if v:
                    s = str(v).strip()
                    if s:
                        return s

    candidates = [
        "display_name",
        "name",
        "player",
        "id",
        "username",
        "user",
        "handle",
        "gamertag",
        "tag",
        "ign",
        "alias",
        "nick",
        "nickname",
        "DISPLAY_NAME",
        "Player",
        "ID",
    ]
    for key in candidates:
        v = raw.get(key)
        if v:
            s = str(v).strip()
            if s:
                return s

    first = raw.get("first") or raw.get("First") or raw.get("firstname") or raw.get("Firstname")
    last = raw.get("last") or raw.get("Last") or raw.get("lastname") or raw.get("Lastname")
    if first or last:
        s = f"{str(first or '').strip()} {str(last or '').strip()}".strip()
        if s:
            return s

    team = raw.get("team") or raw.get("Team")
    num = raw.get("jersey") or raw.get("Jersey") or raw.get("number") or raw.get("Number")
    if team or num:
        return f"{team or 'Team'} #{num or '?'}"

    return "(unnamed)"


# ── Formatting helpers ────────────────────────────────────────────────────────

def _slug_profile_key(name: str) -> str:
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def _profile_header(name: str) -> str:
    u = str(name).strip().upper()
    if u == "PRI":
        return "PRI"
    if u == "PRI-AF":
        return "AF"
    if u == "PRI-AR":
        return "AR"
    if u == "PRI-AP":
        return "AP"
    return str(name).strip()


def _extract_profile_score(res: Mapping[str, Any], profile: str) -> Optional[int]:
    p = str(profile).strip()
    if not p:
        return None

    if p.upper() == "PRI":
        try:
            return int(res.get("pri", 0))
        except Exception:
            return 0

    def _as_int(x: object, default: int = 0) -> int:
        if x is None:
            return default
        try:
            return int(x)  # type: ignore[arg-type]
        except Exception:
            return default

    slug = _slug_profile_key(p)
    if slug in res:
        try:
            return _as_int(res.get(slug))
        except Exception:
            return None

    scores = res.get("scores")
    if isinstance(scores, Mapping) and p in scores:
        try:
            return _as_int(scores.get(p))  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        except Exception:
            return None

    return None


def _detect_profiles_from_results(results: List[Mapping[str, Any]]) -> List[str]:
    found: List[str] = ["PRI"]

    for r in results:
        scores = r.get("scores")
        if isinstance(scores, Mapping):
            keys = [str(k).strip() for k in scores.keys() if str(k).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            for k in keys:
                if k.upper() == "PRI":
                    continue
                if k not in found:
                    found.append(k)
            return found

    for p in ("PRI-AF", "PRI-AR", "PRI-AP"):
        slug = _slug_profile_key(p)
        if any(slug in r for r in results):
            if p not in found:
                found.append(p)

    return found


def _midrank_percentiles(values: List[float]) -> List[float]:
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [50.0]

    pairs = sorted((v, i) for i, v in enumerate(values))
    out = [0.0] * n

    pos = 0
    while pos < n:
        v = pairs[pos][0]
        start = pos
        while pos < n and pairs[pos][0] == v:
            pos += 1
        end = pos
        less = start
        equal = end - start
        pct = 100.0 * (less + 0.5 * equal) / n
        for _, idx in pairs[start:end]:
            out[idx] = pct

    return out


def _split_csvish(items: List[str]) -> List[str]:
    out: List[str] = []
    for it in items:
        s = str(it).strip()
        if not s:
            continue
        parts = [p.strip() for p in s.split(",")]
        out.extend([p for p in parts if p])
    return out


def _format_cell(key: str, v: Any) -> str:
    if v is None:
        return ""
    if key == "pri_raw":
        try:
            return f"{float(v):.4f}"
        except Exception:
            return str(v)
    if key == "percentile":
        try:
            return f"{float(v):.1f}"
        except Exception:
            return str(v)
    return str(v)


class _CsvWriterProtocol:
    def writerow(self, row: Iterable[Any], /) -> Any: ...


def _render_table(rows: Rows, cols: List[Tuple[str, str]], limit: int = 0) -> str:
    view = rows[: (limit or len(rows))]

    matrix: List[Dict[str, str]] = []
    for i, r in enumerate(view, 1):
        out: Dict[str, str] = {}
        for hdr, key in cols:
            if key == "__rank__":
                out[hdr] = str(i)
            else:
                v = r.get(key, "")
                out[hdr] = _format_cell(key, v)
        matrix.append(out)

    widths: Dict[str, int] = {hdr: len(hdr) for hdr, _ in cols}
    for row in matrix:
        for hdr, _ in cols:
            w = len(row.get(hdr, ""))
            if w > widths[hdr]:
                widths[hdr] = w

    def line(ch: str) -> str:
        parts: List[str] = []
        for hdr, _ in cols:
            parts.append(ch * (widths[hdr] + 2))
        return "+" + "+".join(parts) + "+"

    out_lines: List[str] = []
    out_lines.append(line("-"))
    out_lines.append("| " + " | ".join(hdr.ljust(widths[hdr]) for hdr, _ in cols) + " |")
    out_lines.append(line("="))
    for row in matrix:
        out_lines.append("| " + " | ".join(row.get(hdr, "").ljust(widths[hdr]) for hdr, _ in cols) + " |")
    out_lines.append(line("-"))
    return "\n".join(out_lines)


def _render_md(rows: Rows, cols: List[Tuple[str, str]], limit: int = 0) -> str:
    view = rows[: (limit or len(rows))]
    headers = [hdr for hdr, _ in cols]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|"
        + "|".join(
            [
                "---:"
                if (
                    hdr == "Rank"
                    or hdr in {"PRI", "RAW01", "Pct", "AF", "AR", "AP"}
                    or (hdr.isupper() and len(hdr) <= 3)
                )
                else "---"
                for hdr in headers
            ]
        )
        + "|",
    ]
    for i, r in enumerate(view, 1):
        parts: List[str] = []
        for hdr, key in cols:  # pyright: ignore[reportUnusedVariable]
            if key == "__rank__":
                parts.append(str(i))
            else:
                v = r.get(key, "")
                parts.append(_format_cell(key, v))
        lines.append("| " + " | ".join(parts) + " |")
    return "\n".join(lines) + "\n"


# ── Filters/dimensions (adapter-defined; best-effort introspection) ───────────

def _as_str_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i).strip() for i in x if str(i).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(x, tuple):
        return [str(i).strip() for i in x if str(i).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return []


def api_adapter_traits(adapter: str) -> Dict[str, Any]:
    """
    Best-effort adapter-defined knobs.
    The server may or may not expose these; we probe multiple shapes.

    Expected shapes (any of these):
      - GET /v3/adapter/{adapter}/traits -> {"filters": {...}, "dimensions": {...}}
      - GET /v3/adapter/{adapter}/filters -> {"filters": {...}} or {"keys":[...]}
      - GET /v3/adapter/{adapter}/dimensions -> {"dimensions": {...}} or {"keys":[...]}
      - GET /v3/adapter/{adapter}/spec -> may include 'filters'/'dimensions' in some builds
    """
    if not _online or _mode == "local":
        try:
            adp = load_adapter(adapter)
            out: Dict[str, Any] = {}
            for k in ("filters", "dimensions", "dims", "traits"):
                v = getattr(adp, k, None)
                if v:
                    out[k] = v
            return out
        except Exception:
            return {}

    def _try_get(path: str) -> Optional[Dict[str, Any]]:
        try:
            d = _get_v3(path)
            if isinstance(d, dict):
                return cast(Dict[str, Any], d)
        except Exception:
            return None
        return None

    for p in (
        f"/v3/adapter/{adapter}/traits",
        f"/v3/adapter/{adapter}/filters",
        f"/v3/adapter/{adapter}/dimensions",
        f"/v3/adapter/{adapter}/dims",
        f"/v3/adapter/{adapter}/spec",
    ):
        d = _try_get(p)
        if d:
            return d
    return {}


def _coerce_filter_keys(traits: Dict[str, Any]) -> List[str]:
    # Accept several shapes
    for k in ("filter_keys", "filters", "keys"):
        v = traits.get(k)
        if isinstance(v, dict):
            return [str(x).strip() for x in v.keys() if str(x).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return []


def _parse_kv_items(items: List[str]) -> Dict[str, Any]:
    """
    Parse:
      --filter key=value
      --filter key=a,b,c
    into dict.
    """
    out: Dict[str, Any] = {}
    for raw in items:
        s = str(raw).strip()
        if not s:
            continue
        if "=" not in s:
            # allow "key" -> True
            out[s] = True
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if "," in v:
            out[k] = [p.strip() for p in v.split(",") if p.strip()]
        else:
            # numeric if possible
            if v.lower() in {"true", "false"}:
                out[k] = (v.lower() == "true")
            else:
                try:
                    out[k] = int(v)
                except Exception:
                    try:
                        out[k] = float(v)
                    except Exception:
                        out[k] = v
    return out


# ── API facades (v3-first, v2 fallback) ───────────────────────────────────────

def api_adapter_metric_keys(adapter: str) -> List[str]:
    if not _online or _mode == "local":
        try:
            adp = load_adapter(adapter)
            metrics = getattr(adp, "metrics", None)

            seen: set[str] = set()
            out: List[str] = []

            if isinstance(metrics, (list, tuple)):
                for m in metrics:  # pyright: ignore[reportUnknownVariableType]
                    key = getattr(m, "key", None)  # pyright: ignore[reportUnknownArgumentType]
                    if key is None and isinstance(m, dict):
                        key = m.get("key")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    if key is None:
                        continue
                    ks = str(key).strip()  # pyright: ignore[reportUnknownArgumentType]
                    if ks and ks not in seen:
                        seen.add(ks)
                        out.append(ks)

            return out
        except Exception as e:
            _log_note(BUG_NOTES, f"[api_adapter_metric_keys local] {adapter}: {e!r}")
            return []

    try:
        data = _get_v3(f"/v3/adapter/{adapter}/metric-keys")
        items = data.get("keys", [])
        return [str(x).strip() for x in items if isinstance(x, (str, int, float)) and str(x).strip()]
    except Exception:
        return []


def api_adapter_weight_presets(adapter: str) -> List[str]:
    if not _online or _mode == "local":
        try:
            adp = load_adapter(adapter)
            w = getattr(adp, "weights", None)
            if isinstance(w, dict):
                return sorted(str(k) for k in w.keys())  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        except Exception as e:
            _log_note(BUG_NOTES, f"[api_adapter_weight_presets local] {adapter}: {e!r}")
            return []

    try:
        data = _get_v3(f"/v3/adapter/{adapter}/weights")
        w = data.get("weights") or {}  # pyright: ignore[reportUnknownVariableType]
        if isinstance(w, dict):
            return sorted([str(k) for k in w.keys()])  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    except Exception:
        pass
    return []


def _resolve_local_weights(adp: Any, w: Optional[Union[Dict[str, Any], str]]) -> Optional[Dict[str, float]]:
    if w is None:
        return None
    if isinstance(w, str):
        weights_map = getattr(adp, "weights", {}) or {}
        preset = weights_map.get(w)
        if isinstance(preset, Mapping):
            return {str(k): float(v) for k, v in preset.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return None
    if isinstance(w, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
        return {str(k): float(v) for k, v in w.items()}
    return None


def _local_fallback_score_batch(
    adapter: str,
    rows: Rows,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
    filters: Optional[Dict[str, Any]],
) -> Rows:
    # Local core scoring currently doesn't take "filters" at the CLI layer;
    # we pass through if calculator supports it via kwargs (best-effort).
    adp = load_adapter(adapter)
    w = _resolve_local_weights(adp, weights_override)

    try:
        res = score_rows_from_raw(  # pyright: ignore[reportUnknownVariableType]
            rows,
            adp,
            weights_override=w,
            context=context,
            caps_override=caps_override,
            timing=None,
            filters=filters,  # type: ignore[call-arg]
        )
    except TypeError:
        # Older core versions: no filters kwarg
        res = score_rows_from_raw(
            rows,
            adp,
            weights_override=w,
            context=context,
            caps_override=caps_override,
            timing=None,
        )

    return cast(Rows, res)  # pyright: ignore[reportUnnecessaryCast]


def _local_fallback_score_row(
    adapter: str,
    row: Row,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
    filters: Optional[Dict[str, Any]],
) -> Row:
    res = _local_fallback_score_batch(adapter, [row], weights_override, context, caps_override, filters)
    return res[0] if res else {"pri": 99, "pri_raw": 1.0, "context_used": "local-fallback"}


def api_list_adapters() -> List[str]:
    if not _online or _mode == "local":
        return _local_adapter_names()

    try:
        data = _get_v3("/v3/adapters")
        adapters = data.get("adapters", [])
        out = [str(x) for x in adapters if str(x).strip()]
        if out:
            return out
        _fallback_banner("Server returned no adapters")
        return _local_adapter_names()
    except PermissionError as e:
        has_key = _has_regkey()
        desc = _describe_regkey()
        if has_key:
            _log_note(TAMPER_NOTES, f"[auth-reject] {desc} :: {e}")
            _fallback_banner("Auth to host refused (legacy regkey rejected?)")
        else:
            _fallback_banner("Not authenticated")
        typer.secho(
            f"Auth failed: {e}\n{desc}\n"
            "Continuing in demo/local mode.",
            fg=typer.colors.YELLOW,
        )
        return _local_adapter_names()
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[connect-fail] {_slapi_url} :: {e}")
        _fallback_banner("Connection to SLAPI failed")
        return _local_adapter_names()
    except Exception as e:
        _log_note(BUG_NOTES, f"[api_list_adapters] unexpected: {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_adapter_names()


def api_score_batch(
    adapter: str,
    rows: Rows,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
    filters: Optional[Dict[str, Any]],
) -> Rows:
    if not _online or _mode == "local":
        return _local_fallback_score_batch(adapter, rows, weights_override, context, caps_override, filters)

    payload = {
        "adapter": adapter,
        "rows": rows,
        "weights_override": weights_override,
        "context": context,
        "caps_override": caps_override,
        "filters": filters,
    }
    try:
        data = _post_v3("/v3/score/batch", payload)
        if isinstance(data, list):
            return cast(Rows, data)
        return cast(Rows, data.get("results", []))
    except PermissionError as e:
        _log_note(BUG_NOTES, f"[score-batch auth] {_describe_auth_state()} :: {e}")
        _fallback_banner("Auth to host refused")
        return _local_fallback_score_batch(adapter, rows, weights_override, context, caps_override, filters)
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[score-batch connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        return _local_fallback_score_batch(adapter, rows, weights_override, context, caps_override, filters)
    except Exception as e:
        _log_note(BUG_NOTES, f"[score-batch unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_fallback_score_batch(adapter, rows, weights_override, context, caps_override, filters)


def api_score_row(
    adapter: str,
    row: Row,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
    filters: Optional[Dict[str, Any]],
) -> Row:
    if not _online or _mode == "local":
        return _local_fallback_score_row(adapter, row, weights_override, context, caps_override, filters)

    payload = {
        "adapter": adapter,
        "row": row,
        "weights_override": weights_override,
        "context": context,
        "caps_override": caps_override,
        "filters": filters,
    }
    try:
        data = _post_v3("/v3/score/row", payload)
        return cast(Row, data)
    except PermissionError as e:
        _log_note(BUG_NOTES, f"[score-row auth] {_describe_auth_state()} :: {e}")
        _fallback_banner("Auth to host refused")
        return _local_fallback_score_row(adapter, row, weights_override, context, caps_override, filters)
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[score-row connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        return _local_fallback_score_row(adapter, row, weights_override, context, caps_override, filters)
    except Exception as e:
        _log_note(BUG_NOTES, f"[score-row unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_fallback_score_row(adapter, row, weights_override, context, caps_override, filters)


def api_calc_pri_single(
    adapter: str,
    row: Row,
    weights_override: Optional[Union[Dict[str, Any], str]],
    filters: Optional[Dict[str, Any]],
) -> Row:
    if not _online or _mode == "local":
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)

    try:
        # IMPORTANT:
        # /v3/calc/pri is a mapped-metrics endpoint and may NOT accept "filters".
        # If filters are present, or the row is raw-ish, use /v3/pri/row instead
        # (RAW -> MAPPED -> PRI in one call) which *does* accept ScoreRowIn shape.
        if filters:
            payload = {"adapter": adapter, "row": row, "weights_override": weights_override, "filters": filters}
            data = _post_v3("/v3/pri/row", payload)
            return cast(Row, data)

        payload2 = {"adapter": adapter, "row": row, "weights_override": weights_override}
        data2 = _post_v3("/v3/calc/pri", payload2)
        return cast(Row, data2)
    except PermissionError as e:
        _log_note(BUG_NOTES, f"[calc-pri auth] {_describe_auth_state()} :: {e}")
        _fallback_banner("Auth to host refused")
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[calc-pri connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)
    except Exception as e:
        _log_note(BUG_NOTES, f"[calc-pri unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)


def api_pri_row(
    adapter: str,
    row: Row,
    weights_override: Optional[Union[Dict[str, Any], str]],
    filters: Optional[Dict[str, Any]],
) -> Row:
    """
    Correct RAW -> MAPPED -> PRI path (server-side).
    Uses /v3/pri/row which takes ScoreRowIn (supports filters, weights_override, etc).
    """
    if not _online or _mode == "local":
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)

    payload = {
        "adapter": adapter,
        "row": row,
        "weights_override": weights_override,
        "filters": filters,
    }
    try:
        data = _post_v3("/v3/pri/row", payload)
        return cast(Row, data)
    except PermissionError as e:
        _log_note(BUG_NOTES, f"[pri-row auth] {_describe_auth_state()} :: {e}")
        _fallback_banner("Auth to host refused")
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[pri-row connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)
    except Exception as e:
        _log_note(BUG_NOTES, f"[pri-row unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)


def api_pri_batch(
    adapter: str,
    rows: Rows,
    weights_override: Optional[Union[Dict[str, Any], str]],
    filters: Optional[Dict[str, Any]],
    *,
    caps_mode: str = "batch",
) -> Rows:
    """
    Correct RAW -> MAPPED -> PRI batch path.
    POST /v3/pri/batch?caps_mode=batch|clamps
    """
    caps = (caps_mode or "batch").strip().lower()
    caps = "clamps" if caps == "clamps" else "batch"

    if not _online or _mode == "local":
        if caps == "clamps":
            return [_local_fallback_score_row(adapter, r, weights_override, None, None, filters) for r in rows]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)

    payload = {
        "adapter": adapter,
        "rows": rows,
        "weights_override": weights_override,
        "filters": filters,
    }
    try:
        data = _post_v3("/v3/pri/batch", payload, params={"caps_mode": caps})
        if isinstance(data, list):
            return cast(Rows, data)
        return cast(Rows, data.get("results", []))
    except PermissionError as e:
        _log_note(BUG_NOTES, f"[pri-batch auth] {_describe_auth_state()} :: {e}")
        _fallback_banner("Auth to host refused")
        if caps == "clamps":
            return [_local_fallback_score_row(adapter, r, weights_override, None, None, filters) for r in rows]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[pri-batch connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        if caps == "clamps":
            return [_local_fallback_score_row(adapter, r, weights_override, None, None, filters) for r in rows]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)
    except Exception as e:
        _log_note(BUG_NOTES, f"[pri-batch unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        if caps == "clamps":
            return [_local_fallback_score_row(adapter, r, weights_override, None, None, filters) for r in rows]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)


# ── root options & helpers ────────────────────────────────────────────────────

def _resolve_timing(ctx: typer.Context, local: Optional[bool]) -> bool:
    if local is not None:
        return local
    try:
        root = ctx.find_root()
        if root.obj and "timing" in root.obj:
            return bool(root.obj["timing"])
    except Exception:
        pass
    return STATLINE_DEBUG_TIMING


def _eager_version_callback(value: bool) -> None:
    if value:
        typer.echo(f"{CLI_NAME} v{CLI_VERSION}")
        raise typer.Exit(0)


def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Print CLI version and exit.",
        callback=_eager_version_callback,
        is_eager=True,
    ),
    mode: str = typer.Option(
        _mode,
        "--mode",
        envvar="STATLINE_MODE",
        help="Runtime mode: auto | local | remote. (local=offline StatLine, remote=require SLAPI)",
    ),
    timing: bool = typer.Option(
        True,
        "--timing/--no-timing",
        help="Show per-stage timing summaries (default: on; use --no-timing to hide).",
    ),
    url: str = typer.Option(
        DEFAULT_SLAPI_URL,
        "--url",
        envvar="SLAPI_URL",
        help="Base URL for StatLine API (default env SLAPI_URL).",
    ),
) -> None:
    global _slapi_url, _reachable, _online, _mode

    mode_norm = (mode or "auto").strip().lower()
    _mode = cast(Mode, mode_norm if mode_norm in ("auto", "local", "remote") else "auto")  # pyright: ignore[reportUnnecessaryCast]

    _slapi_url = (url or DEFAULT_SLAPI_URL).rstrip("/")

    root = ctx.find_root()
    if root.obj is None:
        root.obj = {}
    root.obj["timing"] = timing
    root.obj["mode"] = _mode

    ensure_banner()

    # Mode behavior:
    # - local: never probe, never auth, never use SLAPI
    if _mode == "local":
        _reachable = False
        _online = False
        _print_mode_banner(reachable=False, authed=False, url=_slapi_url, mode=_mode)
        if ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())
            raise typer.Exit(0)
        return

    # auto/remote: probe
    _reachable = _tcp_probe(_slapi_url)
    authed = False

    if _reachable:
        # prove server is alive (public endpoints)
        try:
            _http_get("/v3/health")
        except Exception:
            try:
                _http_get("/v2/health")
            except Exception as e:
                _log_note(BUG_NOTES, f"[startup-health] {e!r}")

        # authenticated? (requires principal)
        if _has_device() and _has_apikey() and _has_device_id():
            try:
                _http_get("/v3/auth/whoami")
                authed = True
            except Exception as e:
                if _is_http_404(e):
                    try:
                        _http_get("/v2/auth/whoami")
                        authed = True
                    except Exception as e2:
                        _log_note(BUG_NOTES, f"[startup-whoami v2] {e2!r}")
                        authed = False
                else:
                    _log_note(BUG_NOTES, f"[startup-whoami] {e!r}")
                    authed = False
        else:
            authed = False

    _online = bool(_reachable and authed)
    _print_mode_banner(reachable=_reachable, authed=_online, url=_slapi_url, mode=_mode)

    if _mode == "remote":
        if not _reachable:
            raise typer.BadParameter(f"SLAPI remote mode requires a reachable server at {_slapi_url}.")
        if not _online:
            raise typer.BadParameter(
                "SLAPI remote mode requires a fully authenticated principal.\n"
                f"{_describe_auth_state()}\n"
                "Fix: statline auth device-init  -> statline auth enroll --token reg_... --user <name>\n"
                "Then have an admin approve, request an API key, and claim it."
            )

    if _reachable and not _online and _mode == "auto":
        typer.secho(
            "SLAPI reachable but not authenticated.\n"
            f"{_describe_auth_state()}\n"
            "Use: statline auth status (then enroll / request / claim) to enable SLAPI.\n"
            "Tip: use --mode local to silence SLAPI entirely.",
            fg=typer.colors.YELLOW,
            bold=True,
        )

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


app.callback(invoke_without_command=True)(_root)

# ─────────────────────────────────────────────────────────────────────────────
# Sys helpers
# ─────────────────────────────────────────────────────────────────────────────

@sys_app.command("status")
def sys_status() -> None:
    """Print runtime mode, SLAPI reachability, and auth material paths."""
    ensure_banner()
    typer.secho("Runtime", bold=True)
    typer.echo(f"mode:      {_mode}")
    typer.echo(f"slapi_url:  {_slapi_url}")
    typer.echo(f"reachable:  {_reachable}")
    typer.echo(f"online:     {_online}")
    typer.secho("\nSecrets", bold=True)
    typer.echo(f"SECRETS_DIR: {SECRETS_DIR}")
    typer.echo(_describe_auth_state())
    typer.secho("\nLogging", bold=True)
    typer.echo(f"LOG_DIR:     {LOG_DIR}")
    typer.echo(f"BUG_NOTES:   {BUG_NOTES}")
    typer.echo(f"TAMPER:      {TAMPER_NOTES}")


# ─────────────────────────────────────────────────────────────────────────────
# Auth (v3+)
# ─────────────────────────────────────────────────────────────────────────────

@auth_app.command("status")
def auth_status() -> None:
    """Show local auth material and (if possible) server principal info."""
    ensure_banner()
    typer.secho("Local auth state", bold=True)
    typer.echo(_describe_auth_state())
    if _mode == "local" or not _reachable:
        return
    if _has_apikey() and _has_device() and _has_device_id():
        try:
            me = _get_v3("/v3/auth/whoami")
            typer.secho("\nPrincipal", bold=True)
            typer.echo(f"org: {me.get('org')}")
            typer.echo(f"subject: {me.get('subject')}")
            typer.echo(f"device_id: {me.get('device_id')}")
            typer.echo(f"api_prefix: {me.get('api_prefix')}")
            typer.echo(f"scopes: {me.get('scopes')}")
        except Exception as e:
            typer.secho(f"\nwhoami failed: {e}", fg=typer.colors.YELLOW)


@auth_app.command("device-init")
def auth_device_init(force: bool = typer.Option(False, "--force", help="Overwrite existing DEVICEKEY.")) -> None:
    """Create an Ed25519 device keypair and store it in secrets/DEVICEKEY."""
    ensure_banner()
    priv = _ensure_ed25519_keypair(force=force)
    pub = _device_pub_b64_from_priv(priv)
    typer.secho("Device key ready.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  private: {DEVICEKEY_PATH}")
    typer.echo(f"  public_b64url: {pub}")


@auth_app.command("enroll")
def auth_enroll(
    reg_token: Optional[str] = typer.Option(None, "--token", help="Registration token (reg_...)."),
    token_file: Optional[Path] = typer.Option(None, "--file", help="File containing a reg_ token."),
    user: str = typer.Option(..., "--user", help="User handle for this principal (e.g., conner)."),
    email: Optional[str] = typer.Option(None, "--email", help="Email for the principal."),
) -> None:
    """Enroll this device using a server-minted reg token (creates PENDING enrollment request)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Enroll requires SLAPI. Re-run with --mode auto or --mode remote.")
    if not _reachable:
        raise typer.BadParameter(f"SLAPI not reachable at {_slapi_url}.")

    if reg_token is None and token_file is not None:
        reg_token = (_read_text(token_file) or "").strip()
    if not reg_token:
        reg_token = str(typer.prompt("Registration token (reg_...)", default="")).strip()
    if not reg_token.startswith("reg_"):
        raise typer.BadParameter("--token must start with reg_.")

    priv = _ensure_ed25519_keypair(force=False)
    device_pub_b64 = _device_pub_b64_from_priv(priv)

    meta = {
        "hostname": platform.node(),
        "os": platform.platform(),
        "cli_version": f"{CLI_NAME}/{CLI_VERSION}",
    }

    payload: Dict[str, Any] = {
        "reg_token": reg_token,
        "user": user,
        "device_pub_b64": device_pub_b64,
        "meta": meta,
    }
    if email:
        payload["email"] = email

    data = _post_v3("/v3/auth/enroll", payload)
    device_id = str(data.get("device_id", "")).strip()
    if not device_id:
        raise typer.BadParameter("Enroll succeeded but no device_id returned.")
    _write_text(DEVICEID_PATH, device_id)

    typer.secho("Enrollment request created (PENDING).", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  device_id: {device_id}")
    typer.echo(f"  saved: {DEVICEID_PATH}")
    typer.echo("Next: ask an admin to approve the enrollment.")


@auth_app.command("device")
def auth_device_info() -> None:
    """Device-proof sanity check: returns the server-side device record."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Device info requires SLAPI. Re-run with --mode auto or --mode remote.")
    data = _get_v3("/v3/auth/device")
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@auth_app.command("apikey-request")
def auth_apikey_request(
    owner: Optional[str] = typer.Option(None, "--owner", help="Optional owner label (defaults to host)."),
    scopes: List[str] = typer.Option([], "--scope", help="Scope (repeatable)."),
    ttl_days: Optional[int] = typer.Option(None, "--ttl-days", help="Requested TTL in days."),
) -> None:
    """Create an API key request (requires enrolled ACTIVE device; device-proof only)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("API key request requires SLAPI. Re-run with --mode auto or --mode remote.")
    if not _has_device() or not _has_device_id():
        raise typer.BadParameter("Device not enrolled. Run: statline auth device-init  (then)  statline auth enroll ...")

    payload: Dict[str, Any] = {
        "owner": owner or platform.node(),
        "scopes": scopes or None,
        "ttl_days": ttl_days,
    }
    data = _post_v3("/v3/auth/apikey-requests", payload)
    rid = data.get("request_id")
    typer.secho("API key request created.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  request_id: {rid}")
    typer.echo("Ask an admin to approve this request, then claim it with: statline auth apikey-claim --request-id <id>")


@auth_app.command("apikey-claim")
def auth_apikey_claim(
    request_id: str = typer.Option(..., "--request-id", help="Request id from apikey-request."),
    activate: bool = typer.Option(True, "--activate/--no-activate", help="Write secrets/APIKEY."),
) -> None:
    """Claim an approved API key (requires enrolled device; device-proof only)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("API key claim requires SLAPI. Re-run with --mode auto or --mode remote.")
    if not _has_device() or not _has_device_id():
        raise typer.BadParameter("Device not enrolled.")

    data = _post_v3(f"/v3/auth/apikey-requests/{request_id}/claim", None)
    tok = str(data.get("token", "")).strip()
    if not tok.startswith("api_"):
        raise typer.BadParameter("Claim failed: no api_ token returned.")

    if activate:
        _write_text(APIKEY_PATH, tok)

    typer.secho("API key claimed.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  api_key: {tok[:12]}…")
    if activate:
        typer.echo(f"  saved: {APIKEY_PATH}")


@auth_app.command("whoami")
def auth_whoami() -> None:
    """Show server principal info for the active api key."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("whoami requires SLAPI. Re-run with --mode auto or --mode remote.")
    data = _get_v3("/v3/auth/whoami")
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@auth_app.command("apikeys")
def auth_apikeys() -> None:
    """List API keys for the active device (device-proof only)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("apikeys requires SLAPI. Re-run with --mode auto or --mode remote.")
    data = _get_v3("/v3/auth/apikeys")
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Moderation (v3/mod/*) — requires moderation scope
# ─────────────────────────────────────────────────────────────────────────────

@mod_app.command("audit")
def mod_audit(
    limit: int = typer.Option(200, "--limit"),
    event: Optional[str] = typer.Option(None, "--event"),
    org: Optional[str] = typer.Option(None, "--org"),
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    params: Dict[str, Any] = {"limit": limit}
    if event:
        params["event"] = event
    if org:
        params["org"] = org
    data = _get_v3("/v3/mod/audit", params=params)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@mod_app.command("apikeys")
def mod_apikeys(org: Optional[str] = typer.Option(None, "--org")) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    data = _get_v3("/v3/mod/apikeys", params={"org": org} if org else None)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@mod_app.command("apikey-access")
def mod_apikey_access(prefix: str = typer.Argument(...), value: bool = typer.Option(..., "--value")) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    data = _post_v3(f"/v3/mod/apikeys/{prefix}/access", None, params={"value": value})
    if not data.get("ok"):
        raise typer.BadParameter(f"set access failed: {data}")
    typer.secho("Access updated.", fg=typer.colors.GREEN, bold=True)


@mod_app.command("revoke-apikey")
def mod_revoke_apikey(prefix: str = typer.Argument(...)) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    data = _delete_v3(f"/v3/mod/apikeys/{prefix}")
    if not data.get("ok", True):
        raise typer.BadParameter(f"revoke failed: {data}")
    typer.secho("API key revoked.", fg=typer.colors.GREEN, bold=True)


@mod_app.command("revoke-device")
def mod_revoke_device(device_id: str = typer.Argument(...), note: str = typer.Option("", "--note")) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    data = _post_v3(f"/v3/mod/devices/{device_id}/revoke", None, params={"note": note} if note else None)
    if not data.get("ok"):
        raise typer.BadParameter(f"revoke failed: {data}")
    typer.secho("Device revoked.", fg=typer.colors.GREEN, bold=True)


# ─────────────────────────────────────────────────────────────────────────────
# Admin (v3/admin/*) — requires admin scope
# ─────────────────────────────────────────────────────────────────────────────

@admin_app.command("mint-regtoken")
def admin_mint_regtoken(
    org: str = typer.Option("statline", "--org"),
    scopes: List[str] = typer.Option([], "--scope", help="Scope (repeatable)."),
    ttl_days: Optional[int] = typer.Option(14, "--ttl-days"),
    save: bool = typer.Option(True, "--save/--no-save", help="Write token file into secrets/keys."),
) -> None:
    """Mint a registration token used for device enrollment."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    params: Dict[str, Any] = {"org": org, "ttl_days": ttl_days, "scopes": scopes or None}
    data = _post_v3("/v3/admin/mint-regtoken", None, params=params)
    tok = str(data.get("token") or data.get("reg_token") or "").strip()
    if not tok.startswith("reg_"):
        raise typer.BadParameter("No reg_ token returned.")

    typer.secho("Registration token minted.", fg=typer.colors.GREEN, bold=True)
    typer.echo(tok)

    if save:
        KEYS_DIR.mkdir(parents=True, exist_ok=True)
        prefix = tok.split("_", 1)[0] + "_" + tok.split("_", 1)[1][:8]
        path = KEYS_DIR / f"{prefix}.regt"
        _write_text(path, tok)
        typer.echo(f"saved: {path}")


@admin_app.command("enrollments")
def admin_enrollments(status: str = typer.Option("PENDING", "--status")) -> None:
    """List enrollment requests."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    data = _get_v3("/v3/admin/enrollments", params={"status": status})
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@admin_app.command("approve-enrollment")
def admin_approve_enrollment_cmd(
    request_id: str = typer.Argument(...),
    decided_by: str = typer.Option("admin", "--by"),
    note: str = typer.Option("", "--note"),
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    data = _post_v3(
        f"/v3/admin/enrollments/{request_id}/approve",
        None,
        params={"decided_by": decided_by, "note": note} if (decided_by or note) else None,
    )
    if not data.get("ok"):
        raise typer.BadParameter(f"Approve failed: {data}")
    typer.secho("Enrollment approved.", fg=typer.colors.GREEN, bold=True)


@admin_app.command("deny-enrollment")
def admin_deny_enrollment_cmd(
    request_id: str = typer.Argument(...),
    decided_by: str = typer.Option("admin", "--by"),
    note: str = typer.Option("", "--note"),
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    data = _post_v3(
        f"/v3/admin/enrollments/{request_id}/deny",
        None,
        params={"decided_by": decided_by, "note": note} if (decided_by or note) else None,
    )
    if not data.get("ok"):
        raise typer.BadParameter(f"Deny failed: {data}")
    typer.secho("Enrollment denied.", fg=typer.colors.GREEN, bold=True)


@admin_app.command("apikey-requests")
def admin_apikey_requests_cmd(
    status: str = typer.Option("PENDING", "--status"),
    org: Optional[str] = typer.Option(None, "--org"),
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    params: Dict[str, Any] = {"status": status}
    if org:
        params["org"] = org
    data = _get_v3("/v3/admin/apikey-requests", params=params)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@admin_app.command("approve-apikey-request")
def admin_approve_apikey_request_cmd(
    request_id: str = typer.Argument(...),
    decided_by: str = typer.Option("admin", "--by"),
    note: str = typer.Option("", "--note"),
    scopes: List[str] = typer.Option([], "--scope", help="Optional scope narrowing at approval (repeatable)."),
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    payload: Dict[str, Any] = {"decided_by": decided_by, "note": note}
    if scopes:
        payload["scopes"] = scopes
    data = _post_v3(f"/v3/admin/apikey-requests/{request_id}/approve", payload)
    if not data.get("ok"):
        raise typer.BadParameter(f"Approve failed: {data}")
    typer.secho("API key request approved.", fg=typer.colors.GREEN, bold=True)


@admin_app.command("interactive")
def admin_interactive() -> None:
    """
    OS-like admin shell:
      - DEVKEY init/info
      - Mint regtoken
      - Enrollment approvals
      - API key request approvals (+ optional scope narrowing)
      - (Best-effort) moderation views (audit/apikeys) if principal has moderation scope
    """
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin interactive requires SLAPI. Re-run with --mode remote.")
    if not _reachable:
        raise typer.BadParameter(f"SLAPI not reachable at {_slapi_url}.")

    def menu(title: str, options: List[str], default_idx: int = 0) -> str:
        typer.secho("\n" + title, fg=typer.colors.MAGENTA, bold=True)
        for i, opt in enumerate(options, 1):
            typer.echo(f"  {i}. {opt}")
        while True:
            raw = str(typer.prompt("Select", default=str(default_idx + 1))).strip()
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            if raw in options:
                return raw
            typer.secho("  Invalid selection.", fg=typer.colors.RED)

    def safe_json(x: Any) -> str:
        try:
            return json.dumps(x, ensure_ascii=False, indent=2)
        except Exception:
            return str(x)

    def try_call(fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except PermissionError as e:
            typer.secho(f"Permission error: {e}", fg=typer.colors.RED, bold=True)
        except ConnectionError as e:
            typer.secho(f"Connection error: {e}", fg=typer.colors.RED, bold=True)
        except typer.BadParameter as e:
            typer.secho(f"Bad request: {e}", fg=typer.colors.RED, bold=True)
        except Exception as e:
            typer.secho(f"Unexpected error: {e!r}", fg=typer.colors.RED, bold=True)
        return None

    while True:
        top = menu(
            "Admin Shell",
            [
                "DEVKEY: info",
                "DEVKEY: init (generate files)",
                "Mint registration token (reg_...)",
                "Enrollments: list + approve/deny",
                "API key requests: list + approve/deny",
                "Moderation (best-effort): list apikeys",
                "Moderation (best-effort): audit log",
                "Exit",
            ],
            0,
        )

        if top == "Exit":
            return

        if top == "DEVKEY: info":
            data = try_call(_get_v3, "/v3/admin/devkey")
            if data is not None:
                typer.echo(safe_json(data))
            continue

        if top == "DEVKEY: init (generate files)":
            ow = typer.confirm("Overwrite existing DEVKEY files?", default=False)
            data = try_call(_post_v3, "/v3/admin/devkey/init", None, params={"overwrite": ow})
            if data is not None:
                typer.echo(safe_json(data))
            continue

        if top == "Mint registration token (reg_...)":
            org = str(typer.prompt("org", default="statline")).strip() or "statline"
            ttl = int(str(typer.prompt("ttl_days", default="14")).strip() or "14")
            scopes_raw = str(typer.prompt("scopes (comma sep) [blank=userbase]", default="")).strip()
            scopes2 = [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else []
            params: Dict[str, Any] = {"org": org, "ttl_days": ttl, "scopes": scopes2 or None}
            data = try_call(_post_v3, "/v3/admin/mint-regtoken", None, params=params)
            if data is not None:
                typer.secho("Minted:", fg=typer.colors.GREEN, bold=True)
                typer.echo(safe_json(data))
            continue

        if top == "Enrollments: list + approve/deny":
            status = str(typer.prompt("status", default="PENDING")).strip() or "PENDING"
            data = try_call(_get_v3, "/v3/admin/enrollments", params={"status": status})
            if not data:
                continue
            items = data.get("enrollments", []) if isinstance(data, dict) else [] # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            typer.echo(safe_json(items))
            if not items:
                continue
            if not typer.confirm("Take action on an enrollment?", default=False):
                continue
            rid = str(typer.prompt("request_id")).strip()
            action = menu("Action", ["approve", "deny", "cancel"], 0)
            if action == "cancel":
                continue
            decided_by = str(typer.prompt("decided_by", default="admin")).strip() or "admin"
            note = str(typer.prompt("note (optional)", default="")).strip() or None
            if action == "approve":
                res = try_call(
                    _post_v3,
                    f"/v3/admin/enrollments/{rid}/approve",
                    None,
                    params={"decided_by": decided_by, "note": note} if (decided_by or note) else None,
                )
            else:
                res = try_call(
                    _post_v3,
                    f"/v3/admin/enrollments/{rid}/deny",
                    None,
                    params={"decided_by": decided_by, "note": note} if (decided_by or note) else None,
                )
            if res is not None:
                typer.echo(safe_json(res))
            continue

        if top == "API key requests: list + approve/deny":
            status = str(typer.prompt("status", default="PENDING")).strip() or "PENDING"
            org = str(typer.prompt("org (blank=all)", default="")).strip() or None
            params3: Dict[str, Any] = {"status": status}
            if org:
                params3["org"] = org
            data = try_call(_get_v3, "/v3/admin/apikey-requests", params=params3)
            if not data:
                continue
            items = data.get("requests", []) if isinstance(data, dict) else [] # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            typer.echo(safe_json(items))
            if not items:
                continue
            if not typer.confirm("Take action on an API key request?", default=False):
                continue
            rid = str(typer.prompt("request_id")).strip()
            action = menu("Action", ["approve", "deny", "cancel"], 0)
            if action == "cancel":
                continue
            decided_by = str(typer.prompt("decided_by", default="admin")).strip() or "admin"
            note = str(typer.prompt("note (optional)", default="")).strip() or None
            if action == "approve":
                scopes_raw = str(typer.prompt("narrow scopes (comma sep) [blank=no change]", default="")).strip()
                scopes4 = [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else None
                payload: Dict[str, Any] = {"decided_by": decided_by, "note": note}
                if scopes4 is not None:
                    payload["scopes"] = scopes4
                res = try_call(_post_v3, f"/v3/admin/apikey-requests/{rid}/approve", payload)
            else:
                payload2: Dict[str, Any] = {"decided_by": decided_by, "note": note}
                res = try_call(_post_v3, f"/v3/admin/apikey-requests/{rid}/deny", payload2)
            if res is not None:
                typer.echo(safe_json(res))
            continue

        if top == "Moderation (best-effort): list apikeys":
            org = str(typer.prompt("org (blank=all)", default="")).strip() or None
            data = try_call(_get_v3, "/v3/mod/apikeys", params={"org": org} if org else None)
            if data is not None:
                typer.echo(safe_json(data))
            continue

        if top == "Moderation (best-effort): audit log":
            limit = int(str(typer.prompt("limit", default="200")).strip() or "200")
            event = str(typer.prompt("event (blank=all)", default="")).strip() or None
            org = str(typer.prompt("org (blank=all)", default="")).strip() or None
            params2: Dict[str, Any] = {"limit": limit}
            if event:
                params2["event"] = event
            if org:
                params2["org"] = org
            data = try_call(_get_v3, "/v3/mod/audit", params=params2)
            if data is not None:
                typer.echo(safe_json(data))
            continue


@admin_app.command("deny-apikey-request")
def admin_deny_apikey_request_cmd(
    request_id: str = typer.Argument(...),
    decided_by: str = typer.Option("admin", "--by"),
    note: str = typer.Option("", "--note"),
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    payload = {"decided_by": decided_by, "note": note}
    data = _post_v3(f"/v3/admin/apikey-requests/{request_id}/deny", payload)
    if not data.get("ok"):
        raise typer.BadParameter(f"Deny failed: {data}")
    typer.secho("API key request denied.", fg=typer.colors.GREEN, bold=True)


@admin_app.command("apikeys")
def admin_apikeys_cmd(org: Optional[str] = typer.Option(None, "--org")) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Admin requires SLAPI. Re-run with --mode remote.")
    data = _get_v3("/v3/mod/apikeys", params={"org": org} if org else None)
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Userbase commands
# ─────────────────────────────────────────────────────────────────────────────

@app.command("adapters")
def adapters_list() -> None:
    """List available adapter keys (via SLAPI or local)."""
    ensure_banner()
    try:
        for name in sorted(api_list_adapters()):
            typer.echo(name)
    except Exception as e:
        raise typer.BadParameter(f"Failed to list adapters ({_slapi_url}): {e}")


@app.command("interactive")
def interactive(
    ctx: typer.Context,
    timing: Optional[bool] = typer.Option(
        None,
        "--timing/--no-timing",
        help="Show per-row timing inside interactive mode (inherits root default).",
    ),
) -> None:
    """Run an in-CLI interactive session (adapter-driven, filters aware if exposed)."""
    ensure_banner()
    _ = _resolve_timing(ctx, timing) or STATLINE_DEBUG_TIMING

    # ── “OS-like” shell ───────────────────────────────────────────────────────
    def menu_select(title: str, options: List[str], default_index: int = 0) -> str:
        if not options:
            raise typer.BadParameter(f"No options for {title}")
        typer.secho(title, fg=typer.colors.MAGENTA, bold=True)
        for i, opt in enumerate(options, 1):
            typer.echo(f"  {i}. {opt}")
        while True:
            raw_any = typer.prompt("Select", default=str(default_index + 1))
            raw = str(raw_any).strip()
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            if raw in options:
                return raw
            typer.secho("  Invalid selection.", fg=typer.colors.RED)

    def prompt_filters(adapter_key: str) -> Optional[Dict[str, Any]]:
        traits = api_adapter_traits(adapter_key)
        keys = _coerce_filter_keys(traits)
        # Adapter-only: if adapter doesn't declare filters, don't offer them.
        if not keys:
            return None

        typer.secho("\nAdapter filters/dimensions (adapter-defined):", fg=typer.colors.BLUE, bold=True)
        out: Dict[str, Any] = {}
        for k in keys:
            # If adapter exposes options (dict shape), show a menu; else prompt raw.
            options: List[str] = []
            if isinstance(traits.get("filters"), dict) and k in cast(Dict[str, Any], traits["filters"]):
                options = _as_str_list(cast(Dict[str, Any], traits["filters"]).get(k))
            elif isinstance(traits.get("dimensions"), dict) and k in cast(Dict[str, Any], traits["dimensions"]):
                options = _as_str_list(cast(Dict[str, Any], traits["dimensions"]).get(k))

            if options:
                chosen = menu_select(f"{k}:", ["(skip)"] + options, 0)
                if chosen != "(skip)":
                    out[k] = chosen
            else:
                v = str(typer.prompt(f"{k} (blank=skip)", default="")).strip()
                if v:
                    out[k] = v

        return out or None

    # ── Start session ─────────────────────────────────────────────────────────
    names = api_list_adapters()
    if not names:
        typer.secho("No adapters available.", fg=typer.colors.RED)
        raise typer.Exit(1)

    adapter_key = menu_select("Adapters:", names, 0)

    presets = api_adapter_weight_presets(adapter_key)
    weights_override: Optional[Union[Dict[str, float], str]] = None
    if presets:
        chosen = menu_select("Weight presets:", presets, 0)
        weights_override = chosen
    else:
        weights_override = None

    filters = prompt_filters(adapter_key)

    mode = menu_select("Mode:", ["batch", "single", "inspect", "exit"], 0)
    if mode == "exit":
        raise typer.Exit(0)

    if mode == "inspect":
        typer.secho("\nAdapter", bold=True)
        typer.echo(f"key: {adapter_key}")
        typer.echo(f"weight_presets: {', '.join(presets) if presets else '(none)'}")
        mk = api_adapter_metric_keys(adapter_key)
        typer.echo(f"metric_keys: {', '.join(mk) if mk else '(none)'}")
        if filters:
            typer.echo(f"filters: {json.dumps(filters, ensure_ascii=False)}")
        else:
            typer.echo("filters: (none / not declared by adapter)")
        return

    if mode == "batch":
        csv_path = _pick_dataset_via_menu("Datasets:")
        if not csv_path:
            typer.secho("No dataset selected.", fg=typer.colors.RED)
            raise typer.Exit(1)

        raw_rows: Rows = list(_read_rows(Path(csv_path)))
        if not raw_rows:
            typer.secho("Selected CSV has no rows.", fg=typer.colors.RED)
            raise typer.Exit(1)

        results = api_score_batch(adapter_key, raw_rows, weights_override, None, None, filters)
        prof_list = _detect_profiles_from_results(cast(List[Mapping[str, Any]], results))

        rows_out: Rows = []
        for i in range(len(raw_rows)):
            src = raw_rows[i]
            res = results[i]
            row_out: Row = {
                "name": _name_for_row(src, []),
                "pri": int(res.get("pri", 0)),
                "pri_raw": float(res.get("pri_raw", 0.0)),
                "context_used": str(res.get("context_used", "")),
                "_i": i,
            }
            for p in prof_list:
                if str(p).strip().upper() == "PRI":
                    continue
                val = _extract_profile_score(res, p)
                if val is not None:
                    row_out[_slug_profile_key(p)] = int(val)
            rows_out.append(row_out)

        rows_out.sort(key=lambda r: (-float(r["pri_raw"]), -int(r["pri"]), int(r["_i"])))
        for r in rows_out:
            r.pop("_i", None)

        profile_cols: List[Tuple[str, str]] = []
        for p in prof_list:
            if str(p).strip().upper() == "PRI":
                continue
            profile_cols.append((_profile_header(p), _slug_profile_key(p)))

        cols = [
            ("Rank", "__rank__"),
            ("Name", "name"),
            ("PRI", "pri"),
            *profile_cols,
            ("RAW01", "pri_raw"),
            ("Context", "context_used"),
        ]

        typer.secho("\nBatch results", bold=True)
        print(_render_table(rows_out, cols, 0))
        return

    # single
    raw_row: Dict[str, Any] = {}
    player_name = str(typer.prompt("Player name (for display)", default="")).strip()
    if player_name:
        raw_row["display_name"] = player_name

    prompt_keys = api_adapter_metric_keys(adapter_key)
    if prompt_keys:
        typer.secho(
            "\nEnter values for adapter metrics (Enter = 0, 'skip' to skip):",
            fg=typer.colors.BLUE,
            bold=True,
        )
        for key in prompt_keys:
            val = typer.prompt(f"value for {key.upper()}", default="0")
            sv = str(val).strip()
            if not sv or sv.lower() == "skip":
                raw_row[key] = 0.0
            else:
                try:
                    raw_row[key] = float(sv.replace(",", "."))
                except ValueError:
                    raw_row[key] = 0.0

        typer.secho("\nAdd any extra stats (blank key to finish):", fg=typer.colors.BLUE, bold=True)
        while True:
            k = str(typer.prompt("extra stat/key", default="")).strip()
            if not k:
                break
            v = typer.prompt(f"value for {k}", default="0")
            try:
                raw_row[k] = float(str(v).strip().replace(",", "."))
            except ValueError:
                raw_row[k] = 0.0
    else:
        typer.secho("\nAdapter did not report metrics; enter values (blank = 0):", fg=typer.colors.BLUE, bold=True)
        while True:
            k = str(typer.prompt("stat/key (blank to finish)", default="")).strip()
            if not k:
                break
            v = typer.prompt(f"value for {k}", default="0")
            try:
                raw_row[k] = float(str(v).strip().replace(",", "."))
            except ValueError:
                raw_row[k] = 0.0

    use_csv = typer.confirm("Scale this row against a CSV dataset? (y/N)", default=False)
    if use_csv:
        csv_path = _pick_dataset_via_menu("Datasets:")
        if csv_path:
            batch_rows = list(_read_rows(Path(csv_path)))
            batch_rows.append(raw_row)
            results = api_score_batch(adapter_key, batch_rows, weights_override, None, None, filters)
            my_res = results[-1]
        else:
            typer.secho("No dataset selected; falling back to clamps.", fg=typer.colors.YELLOW)
            my_res = api_pri_row(adapter_key, raw_row, weights_override, filters)
    else:
        my_res = api_pri_row(adapter_key, raw_row, weights_override, filters)

    name = _name_for_row(raw_row, preferred=["display_name", "name"])
    pri = int(my_res.get("pri", 0))
    pri_raw = float(my_res.get("pri_raw", 0.0))
    ctx_used = str(my_res.get("context_used", ""))

    typer.secho("\nResult", bold=True)
    typer.echo(f"Name: {name}")
    typer.echo(f"PRI:  {pri} / 99 (raw {pri_raw:.4f}, context {ctx_used})")

    extra_profiles = _detect_profiles_from_results([cast(Mapping[str, Any], my_res)])
    for p in extra_profiles:
        if str(p).strip().upper() == "PRI":
            continue
        val = _extract_profile_score(cast(Mapping[str, Any], my_res), p)
        if val is not None:
            typer.echo(f"{_profile_header(p)}:  {val}")


@app.command("score")
def score(
    ctx: typer.Context,
    adapter: str = typer.Option(..., "--adapter", help="Adapter key (e.g., rbw5 or name@1.2.3)"),
    input_path: Path = typer.Argument(
        Path("stats.csv"),
        help="YAML/CSV understood by your adapter mapping (server-side), or '-' for CSV from stdin.",
    ),
    weights: Optional[Path] = typer.Option(None, "--weights", help="YAML mapping of {bucket: weight}"),
    weights_preset: Optional[str] = typer.Option(None, "--weights-preset", help="Preset name you want to send"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write results (format via --fmt)"),
    include_headers: bool = typer.Option(True, "--headers/--no-headers", help="Include header row for CSV output"),
    timing: Optional[bool] = typer.Option(None, "--timing/--no-timing", help="(Client flag only) — server may ignore."),
    caps: str = typer.Option(
        "batch",
        "--caps",
        "--context",
        help="Normalization context: 'batch' or 'clamps'",
        case_sensitive=False,
    ),
    fmt: str = typer.Option(
        "table",
        "--fmt",
        help="Output format: csv|table|md|json|jsonl",
        case_sensitive=False,
    ),
    name_col: List[str] = typer.Option([], "--name-col", help="Preferred name column(s); first non-empty wins."),
    limit: int = typer.Option(0, "--limit", min=0, help="Limit rows shown (0=all)"),
    profiles: List[str] = typer.Option(
        [],
        "--profile",
        "--profiles",
        help="Profiles to include (repeatable or comma-separated). Use 'all' to include all detected.",
    ),
    percentile: bool = typer.Option(
        False,
        "--percentile/--no-percentile",
        help="Add percentile (computed client-side from RAW01; stable with ties).",
    ),
    sort_by: str = typer.Option(
        "pri_raw",
        "--sort",
        help="Sort key: pri_raw|pri|pri_af|pri_ar|pri_ap|percentile|<any_profile_name>",
    ),
    asc: bool = typer.Option(False, "--asc/--desc", help="Sort order (default: desc)."),
    details: bool = typer.Option(
        False,
        "--details/--no-details",
        help="For json/jsonl: include full result payload from API under 'details'.",
    ),
    pretty: bool = typer.Option(False, "--pretty/--no-pretty", help="For json output: pretty-print JSON."),
    filters: List[str] = typer.Option(
        [],
        "--filter",
        "--filters",
        help="Adapter-defined filter/dimension (repeatable): key=value or key=a,b,c",
    ),
) -> None:
    """Batch score via SLAPI (remote) or StatLine core (local)."""
    ensure_banner()
    _ = _resolve_timing(ctx, timing) or STATLINE_DEBUG_TIMING

    fmt_lower = (fmt or "table").lower()
    caps_mode = (caps or "batch").lower()
    if caps_mode not in {"batch", "clamps"}:
        raise typer.BadParameter("--caps/--context must be 'batch' or 'clamps'")
    if fmt_lower not in {"csv", "table", "md", "json", "jsonl"}:
        raise typer.BadParameter("--fmt must be one of: csv, table, md, json, jsonl")

    raw_rows: Rows = list(_read_rows(input_path))

    weights_override: Optional[Union[Dict[str, float], str]] = None
    if weights and weights_preset:
        raise typer.BadParameter("Specify either --weights or --weights-preset, not both.")
    if weights:
        data_any: Any = _yaml_load_text(weights.read_text(encoding="utf-8"))
        if not isinstance(data_any, Mapping):
            raise typer.BadParameter("--weights YAML must be a mapping of {bucket: weight}.")
        weights_override = {str(k): float(v) for k, v in cast(Mapping[str, Any], data_any).items()}
    elif weights_preset:
        weights_override = str(weights_preset)

    filters_dict = _parse_kv_items(_split_csvish(filters))
    # Adapter-only enforcement: if adapter doesn't declare filters, silently drop them in interactive;
    # for CLI flags we keep them (power-user), but if we can detect declared keys, validate.
    declared_keys = _coerce_filter_keys(api_adapter_traits(adapter))
    if declared_keys and filters_dict:
        unknown = sorted([k for k in filters_dict.keys() if k not in set(declared_keys)])
        if unknown:
            typer.secho(
                f"Warning: adapter '{adapter}' did not declare filter(s): {', '.join(unknown)} (sending anyway).",
                fg=typer.colors.YELLOW,
            )

    if caps_mode == "clamps":
        # Correct endpoint path for RAW->MAPPED->PRI with clamp behavior:
        results = api_pri_batch(adapter, raw_rows, weights_override, filters_dict or None, caps_mode="clamps")
    else:
        results = api_score_batch(adapter, raw_rows, weights_override, None, None, filters_dict or None)

    prof_in = _split_csvish(profiles)
    prof_norm = [p for p in prof_in if p.strip()]
    want_all = any(p.strip().lower() == "all" for p in prof_norm)

    detected = _detect_profiles_from_results(cast(List[Mapping[str, Any]], results))
    if want_all:
        prof_list = detected
    else:
        prof_list = prof_norm or ["PRI"]
        if not any(p.strip().upper() == "PRI" for p in prof_list):
            prof_list = ["PRI"] + prof_list

    rows_out: Rows = []
    for i in range(len(raw_rows)):
        src = raw_rows[i]
        res = results[i]

        row_out: Row = {
            "name": _name_for_row(src, name_col),
            "pri": int(res.get("pri", 0)),
            "pri_raw": float(res.get("pri_raw", 0.0)),
            "context_used": str(res.get("context_used", "")),
            "_i": i,
        }

        for p in prof_list:
            if str(p).strip().upper() == "PRI":
                continue
            val = _extract_profile_score(res, p)
            if val is not None:
                row_out[_slug_profile_key(p)] = int(val)

        if details and fmt_lower in {"json", "jsonl"}:
            row_out["details"] = dict(res)

        rows_out.append(row_out)

    if percentile:
        pcts = _midrank_percentiles([float(r.get("pri_raw", 0.0)) for r in rows_out])
        for r, pct in zip(rows_out, pcts):
            r["percentile"] = float(pct)

    sort_key_raw = (sort_by or "pri_raw").strip()
    sort_key = sort_key_raw
    if "-" in sort_key and not sort_key.startswith("pri_"):
        sort_key = _slug_profile_key(sort_key)

    def _num(v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return 0.0

    def _sort_val(r: Row) -> float:
        return _num(r.get(sort_key, r.get(sort_key_raw, 0.0)))

    rows_out.sort(
        key=lambda r: (
            _sort_val(r),
            _num(r.get("pri_raw", 0.0)),
            _num(r.get("pri", 0)),
            int(r.get("_i", 0)),
        ),
        reverse=(not asc),
    )

    for r in rows_out:
        r.pop("_i", None)

    view = rows_out[: (limit or len(rows_out))]

    profile_cols: List[Tuple[str, str]] = []
    for p in prof_list:
        if str(p).strip().upper() == "PRI":
            continue
        hdr = _profile_header(p)
        key = _slug_profile_key(p)
        profile_cols.append((hdr, key))

    cols_table: List[Tuple[str, str]] = [
        ("Rank", "__rank__"),
        ("Name", "name"),
        ("PRI", "pri"),
        *profile_cols,
        ("RAW01", "pri_raw"),
    ]
    if percentile:
        cols_table.append(("Pct", "percentile"))
    cols_table.append(("Context", "context_used"))

    out_fields: List[str] = ["name", "pri"]
    for p in prof_list:
        if str(p).strip().upper() == "PRI":
            continue
        out_fields.append(_slug_profile_key(p))
    out_fields += ["pri_raw"]
    if percentile:
        out_fields.append("percentile")
    out_fields.append("context_used")
    if details and fmt_lower in {"json", "jsonl"}:
        out_fields.append("details")

    def _write_out_text(s: str) -> None:
        if out:
            out.write_text(s, encoding="utf-8")
        else:
            sys.stdout.write(s)

    if fmt_lower == "table":
        _write_out_text(_render_table(view, cols_table, 0) + "\n")
        return

    if fmt_lower == "md":
        _write_out_text(_render_md(view, cols_table, 0))
        return

    if fmt_lower == "csv":
        target = out.open("w", newline="", encoding="utf-8") if out else sys.stdout
        with target if out else contextlib.nullcontext(target):  # type: ignore[arg-type]
            writer = csv.writer(target)  # type: ignore[arg-type]
            w = cast(_CsvWriterProtocol, writer)
            if include_headers:
                w.writerow(out_fields)
            for row in view:
                w.writerow([str(row.get(k, "")) for k in out_fields])
        return

    if fmt_lower in {"json", "jsonl"}:
        if fmt_lower == "jsonl":
            lines = []
            for row in view:
                payload = {k: row.get(k, None) for k in out_fields if k in row}
                lines.append(json.dumps(payload, ensure_ascii=False))  # pyright: ignore[reportUnknownMemberType]
            _write_out_text("\n".join(lines) + ("\n" if lines else ""))  # pyright: ignore[reportUnknownArgumentType]
            return

        payload_list = [{k: row.get(k, None) for k in out_fields if k in row} for row in view]
        if pretty:
            _write_out_text(json.dumps(payload_list, ensure_ascii=False, indent=2) + "\n")
        else:
            _write_out_text(json.dumps(payload_list, ensure_ascii=False) + "\n")
        return


def main() -> None:
    try:
        app()
    except click.exceptions.Exit:
        raise
    except KeyboardInterrupt:
        raise typer.Exit(code=130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    main()
