# statline/cli.py
from __future__ import annotations

import ast
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
import subprocess
import sys
import time
from collections import defaultdict

# ── stdlib ────────────────────────────────────────────────────────────────────
from dataclasses import dataclass
from datetime import datetime, timezone, tzinfo
from os import getenv
from pathlib import Path
from typing import (
    Any,
    DefaultDict,
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
CLI_NAME = "StatLine UX"

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


# mutable runtime config (don’t mutate ALL-CAPS constants)
_slapi_url: str = DEFAULT_SLAPI_URL

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


def emit(s: str) -> None:
    print(s, end="" if s.endswith("\n") else "\n")


def _normalize_ip(ip: Any) -> str:
    if not ip:
        return "-"
    s = str(ip).strip()

    # If proxy chain, take the first IP
    if "," in s:
        s = s.split(",", 1)[0].strip()

    # If it looks like ip:port (but avoid breaking IPv6 too aggressively)
    if ":" in s and s.count(":") == 1:
        host, _port = s.rsplit(":", 1)
        if _port.isdigit():
            s = host

    return s


def _collapse_audit_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    i = 0

    def keyish(r: Dict[str, Any]) -> Tuple[Any, Any, Any, Any]:
        return (r.get("ts"), r.get("subject"), r.get("ip"), r.get("event"))

    while i < len(rows):
        r = rows[i]
        nxt = rows[i + 1] if i + 1 < len(rows) else None

        # Collapse device+api handshake into one "auth.ok" row
        if nxt:
            same_triplet = (
                r.get("ts") == nxt.get("ts")
                and r.get("subject") == nxt.get("subject")
                and r.get("ip") == nxt.get("ip")
            )
            if (
                same_triplet
                and r.get("event") == "auth.device.ok"
                and nxt.get("event") == "auth.api.ok"
            ):
                merged = dict(nxt)  # keep api_prefix etc.
                merged["event"] = "auth.ok"
                # keep device_id if present on either
                if not merged.get("device_id") and r.get("device_id"):
                    merged["device_id"] = r.get("device_id")
                out.append(merged)
                i += 2
                continue

        # Drop exact duplicates that occur back-to-back (common with retries)
        if out and keyish(out[-1]) == keyish(r) and out[-1].get("ok") == r.get("ok"):
            i += 1
            continue

        out.append(r)
        i += 1

    return out


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
SLAPI_PID_FILE: Path = LOG_DIR / "slapi.pid"
SLAPI_OUT_LOG: Path = LOG_DIR / "slapi.out.log"
SLAPI_ERR_LOG: Path = LOG_DIR / "slapi.err.log"


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
    return "\n".join([_describe_device(), _describe_apikey()])


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
        raise typer.BadParameter(
            f"Missing APIKEY at {APIKEY_PATH}. Run: statline auth apikey-request"
        )
    if not s.startswith("api_"):
        raise typer.BadParameter(f"{APIKEY_PATH} doesn’t look like an api_ token.")
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
        raise typer.BadParameter(
            f"Missing DEVICEKEY at {DEVICEKEY_PATH}. Run: statline auth device-init"
        )

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
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
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


def _best_auth_mode(*, guarded: bool) -> Literal["principal", "none"]:
    """Choose the auth scheme available on this machine for SLAPI v3."""
    if guarded and _has_device() and _has_apikey() and _has_device_id():
        return "principal"
    return "none"


def _auth_for_path(path: str) -> Literal["none", "device", "principal"]:
    """Select the SLAPI v3 auth mode for a request path."""
    if path.startswith("/v2/"):
        raise RuntimeError("SLAPI v2 endpoints are no longer supported; use /v3 endpoints.")
    if path.startswith("/v3/admin") or path.startswith("/v3/mod"):
        return "principal"
    if path.startswith("/v3/auth/enroll") or path.startswith("/v3/health") or path == "/":
        return "none"
    if path.startswith("/v3/auth/whoami"):
        return "principal"
    if (
        path.startswith("/v3/auth/apikey-requests")
        or path.startswith("/v3/auth/apikeys")
        or path.startswith("/v3/auth/device")
    ):
        return "device"
    if path.startswith("/v3/"):
        return _best_auth_mode(guarded=True)
    return _best_auth_mode(guarded=True)


def _headers(
    method: str,
    target: str,
    body: bytes,
    *,
    extra: Optional[Dict[str, str]] = None,
    auth: Literal["none", "device", "principal"] = "principal",
) -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json"}

    if auth in {"device", "principal"}:
        h.update(_device_proof_headers(method, target, body))

    if auth == "principal":
        api = _read_apikey()
        h["Authorization"] = f"Bearer {api}"

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
            d = detail["detail"]  # pyright: ignore[reportUnknownVariableType]
        else:
            d = detail  # pyright: ignore[reportUnknownVariableType]

        if isinstance(d, str):
            return d.strip()

        if isinstance(d, list):
            # Pydantic validation errors often arrive as list[dict]
            parts: List[str] = []
            for it in d:  # pyright: ignore[reportUnknownVariableType]
                if isinstance(it, dict):
                    loc = it.get("loc")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    msg = it.get("msg")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    typ = it.get("type")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    loc_s = ""
                    if isinstance(loc, (list, tuple)):
                        loc_s = ".".join(str(x) for x in loc if str(x))  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                    elif loc is not None:
                        loc_s = str(loc)  # pyright: ignore[reportUnknownArgumentType]
                    blob = " / ".join(
                        [x for x in [loc_s, str(msg or "").strip(), str(typ or "").strip()] if x]  # pyright: ignore[reportUnknownArgumentType]
                    )  # pyright: ignore[reportUnknownArgumentType]
                    if blob:
                        parts.append(blob)
                else:
                    s = str(it).strip()  # pyright: ignore[reportUnknownArgumentType]
                    if s:
                        parts.append(s)
            return "; ".join(parts) if parts else str(d)  # pyright: ignore[reportUnknownArgumentType]

        if isinstance(d, dict):
            # sometimes "detail" is a dict
            return json.dumps(d, ensure_ascii=False)

        return str(d)  # pyright: ignore[reportUnknownArgumentType]
    except Exception:
        try:
            return str(detail)  # pyright: ignore[reportUnknownArgumentType]
        except Exception:
            return "unknown error"


_TS_KEY_RE = re.compile(
    r"(ts|time|timestamp|created|updated|issued|expires|exp|iat|nbf)",
    re.IGNORECASE,
)


def _local_tz() -> tzinfo:
    # Local system tz (handles EST/EDT correctly if OS is configured)
    return datetime.now().astimezone().tzinfo or timezone.utc


def _try_parse_iso(s: str) -> Optional[datetime]:
    s2 = s.strip()
    if not s2:
        return None
    # Handle trailing Z
    if s2.endswith("Z"):
        s2 = s2[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s2)
        # If naive, assume UTC? (choose your policy)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _format_dt_local(dt: datetime) -> str:
    loc = dt.astimezone(_local_tz())
    # Example: 2026-01-28 13:07:42 EST
    return loc.strftime("%Y-%m-%d %H:%M:%S %Z")


def _maybe_format_timestamp(key: str, value: Any) -> Any:
    # Only rewrite if key strongly looks like a timestamp-ish field.
    if not _TS_KEY_RE.search(str(key)):
        return value

    # Epoch seconds (int/float)
    if isinstance(value, (int, float)):
        # Heuristic: treat large ints as ms
        v = float(value)
        if v > 1e12:  # ms since epoch
            v = v / 1000.0
        if v > 0:
            try:
                dt = datetime.fromtimestamp(v, tz=timezone.utc)
                return _format_dt_local(dt)
            except Exception:
                return value

    # ISO-ish string
    if isinstance(value, str):
        parsed_dt = _try_parse_iso(value)

        if parsed_dt is None:
            return value

        dt = parsed_dt

    return value


def _group_audit(rows: List[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:  # pyright: ignore[reportMissingTypeArgument, reportUnknownParameterType]
    groups: DefaultDict[tuple, List[Dict[str, Any]]] = defaultdict(list)  # pyright: ignore[reportMissingTypeArgument, reportUnknownVariableType]
    for r in rows:
        org = r.get("org") or "-"
        sub = r.get("subject") or "-"
        dev = r.get("device_id") or "-"
        groups[(org, sub, dev)].append(r)
    return dict(groups)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


def _normalize_for_display(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():  # pyright: ignore[reportUnknownVariableType]
            kk = str(k)  # pyright: ignore[reportUnknownArgumentType]
            vv = _normalize_for_display(v)
            vv = _maybe_format_timestamp(kk, vv)

            if kk == "ip":
                vv = _normalize_ip(vv)

            out[kk] = vv
        return out
    if isinstance(obj, list):
        return [_normalize_for_display(x) for x in obj]  # pyright: ignore[reportUnknownVariableType]
    return obj


def _dump_json_clean(obj: Any) -> str:
    cleaned = _normalize_for_display(obj)
    return json.dumps(
        cleaned,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        default=str,  # last-resort for odd types
    )


def echo_clean(obj: Any, *, pager: bool = True) -> None:
    """
    Pretty-print dict/list/anything as JSON.
    Uses a pager (arrow keys) if output is longer than a screenful.
    """
    s = _dump_json_clean(obj)
    # Always ensure trailing newline
    if not s.endswith("\n"):
        s += "\n"

    if pager and supports_internal_pager():
        click.echo_via_pager(s)
    else:
        typer.echo(s, nl=False)


def supports_internal_pager() -> bool:
    return not sys.platform.startswith("win")


def render_apikeys_view(data: Dict[str, Any]) -> None:
    keys = data.get("keys", [])
    if not keys:
        typer.secho("No API keys found.", fg=typer.colors.YELLOW)
        return

    rows = []
    for k in keys:
        rows.append(  # pyright: ignore[reportUnknownMemberType]
            {  # pyright: ignore[reportUnknownMemberType]
                "Prefix": k.get("prefix", "-"),
                "Owner": k.get("owner", "-"),
                "Org": k.get("org", "-"),
                "Scopes": ",".join(k.get("scopes", [])),
                "Device": k.get("device_id", "-")[:8],
                "Access": "✓" if k.get("access") else "✗",
                "Expires": k.get("expires_at", "-"),
                "Last Used": k.get("last_used_at", "-"),
            }
        )

    cols = [
        ("Prefix", "Prefix"),
        ("Owner", "Owner"),
        ("Org", "Org"),
        ("Scopes", "Scopes"),
        ("Device", "Device"),
        ("Access", "Access"),
        ("Expires", "Expires"),
        ("Last Used", "Last Used"),
    ]

    print(_render_table(rows, cols))  # pyright: ignore[reportUnknownArgumentType]


def echo_clean_auto(obj: Any) -> None:
    norm = _normalize_for_display(obj)
    if isinstance(norm, dict) and isinstance(norm.get("audit"), list) and norm["audit"]:  # pyright: ignore[reportUnknownMemberType]
        audit_rows = cast(List[Dict[str, Any]], norm["audit"])
        text = _render_audit_pages(audit_rows, per_page=50)
        click.echo_via_pager(text)
        return
    echo_clean(norm, pager=True)


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


def _is_http_404(err: BaseException) -> bool:  # pyright: ignore[reportUnusedFunction]
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

    auth = _auth_for_path(path)

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

    auth = _auth_for_path(path)

    body = (
        b""
        if payload is None
        else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
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

    auth = _auth_for_path(path)

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


# ── v3 request wrappers ───────────────────────────────────────────────────────


def _get_v3(path_v3: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    return _http_get(path_v3, params=params)


def _post_v3(path_v3: str, payload: Any, *, params: Optional[Dict[str, Any]] = None) -> Any:
    return _http_post(path_v3, payload, params=params)


def _delete_v3(path_v3: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    return _http_delete(path_v3, params=params)


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
    """GET /v3/datasets -> {"datasets": ["file.csv", ...]}."""
    try:
        data = _get_v3("/v3/datasets")
        ds = data.get("datasets", [])
        out: List[Dict[str, str]] = []

        if isinstance(ds, list):
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
            for p in sorted(d.rglob("*.csv")):
                out.append({"name": str(p.relative_to(d)), "path": str(p)})
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
        raise typer.BadParameter(
            f"Input file not found: {input_path}. Pass a YAML/CSV or use '-' for stdin."
        )
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
        if x is None or isinstance(x, bool):
            return default
        if isinstance(x, int):
            return x
        if isinstance(x, (float, str)):
            try:
                return int(x)
            except ValueError:
                return default
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


def _context_label(value: Any, fallback: str) -> str:
    """Keep CLI tables readable when the scorer returns the full context map."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


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


def render_table(rows: list[dict], cols: list[str]) -> str:  # pyright: ignore[reportMissingTypeArgument, reportUnknownParameterType]
    # compute widths
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType]

    def fmt_row(r):  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
        return "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]

    out = []
    out.append("  ".join(c.ljust(widths[c]) for c in cols))  # pyright: ignore[reportUnknownMemberType]
    out.append("  ".join("-" * widths[c] for c in cols))  # pyright: ignore[reportUnknownMemberType]
    for r in rows:  # pyright: ignore[reportUnknownVariableType]
        out.append(fmt_row(r))  # pyright: ignore[reportUnknownMemberType]
    return "\n".join(out)  # pyright: ignore[reportUnknownArgumentType]


def _render_audit_pages(rows: List[Dict[str, Any]], *, per_page: int = 50) -> str:
    # Normalize & collapse
    rows = _collapse_audit_rows(rows)

    # Group
    groups = _group_audit(rows)  # pyright: ignore[reportUnknownVariableType]

    # Stable ordering: org, subject, device, newest first in each group
    ordered_keys = sorted(groups.keys(), key=lambda k: (str(k[0]), str(k[1]), str(k[2])))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType, reportUnknownVariableType]

    parts: List[str] = []
    cols = [
        "ts",
        "event",
        "ok",
        "api_prefix",
        "ip",
        "id",
    ]  # keep tight; subject/org/device go in header

    for org, sub, dev in ordered_keys:  # pyright: ignore[reportUnknownVariableType]
        g = groups[(org, sub, dev)]

        # newest first if ts is sortable string; if not, keep as-is
        g_sorted = list(g)[::-1]

        header = f"=== org={org} | subject={sub} | device={dev} | events={len(g_sorted)} ==="
        parts.append(header)

        # Chunk into “pages” of 40-50 within the section if needed
        for chunk_i in range(0, len(g_sorted), per_page):
            chunk = g_sorted[chunk_i : chunk_i + per_page]
            if chunk_i > 0:
                parts.append(f"-- continued ({chunk_i}/{len(g_sorted)}) --")

            parts.append(render_table(chunk, cols))
            parts.append("")  # spacer line

        parts.append("")  # extra spacer between principals

    return "\n".join(parts).rstrip() + "\n"


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
        out_lines.append(
            "| " + " | ".join(row.get(hdr, "").ljust(widths[hdr]) for hdr, _ in cols) + " |"
        )
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
                out[k] = v.lower() == "true"
            else:
                try:
                    out[k] = int(v)
                except Exception:
                    try:
                        out[k] = float(v)
                    except Exception:
                        out[k] = v
    return out


# ── API facades (v3-only remote, local fallback) ─────────────────────────────────


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
        return [
            str(x).strip() for x in items if isinstance(x, (str, int, float)) and str(x).strip()
        ]
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


def _resolve_local_weights(
    adp: Any, w: Optional[Union[Dict[str, Any], str]]
) -> Optional[Dict[str, float]]:
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
    res = _local_fallback_score_batch(
        adapter, [row], weights_override, context, caps_override, filters
    )
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
        desc = _describe_auth_state()
        _log_note(TAMPER_NOTES, f"[auth-reject] {desc} :: {e}")
        _fallback_banner("Not authenticated")
        typer.secho(
            f"Auth failed: {e}\n{desc}\nContinuing in demo/local mode.",
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
        return _local_fallback_score_batch(
            adapter, rows, weights_override, context, caps_override, filters
        )

    payload = {
        "adapter": adapter,
        "rows": rows,
        "weights": weights_override,
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
        return _local_fallback_score_batch(
            adapter, rows, weights_override, context, caps_override, filters
        )
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[score-batch connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        return _local_fallback_score_batch(
            adapter, rows, weights_override, context, caps_override, filters
        )
    except Exception as e:
        _log_note(BUG_NOTES, f"[score-batch unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_fallback_score_batch(
            adapter, rows, weights_override, context, caps_override, filters
        )


def api_score_row(
    adapter: str,
    row: Row,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
    filters: Optional[Dict[str, Any]],
) -> Row:
    if not _online or _mode == "local":
        return _local_fallback_score_row(
            adapter, row, weights_override, context, caps_override, filters
        )

    payload = {
        "adapter": adapter,
        "row": row,
        "weights": weights_override,
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
        return _local_fallback_score_row(
            adapter, row, weights_override, context, caps_override, filters
        )
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[score-row connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        return _local_fallback_score_row(
            adapter, row, weights_override, context, caps_override, filters
        )
    except Exception as e:
        _log_note(BUG_NOTES, f"[score-row unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        return _local_fallback_score_row(
            adapter, row, weights_override, context, caps_override, filters
        )


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
            payload = {
                "adapter": adapter,
                "row": row,
                "weights": weights_override,
                "filters": filters,
            }
            data = _post_v3("/v3/pri/row", payload)
            return cast(Row, data)

        payload2 = {"adapter": adapter, "row": row, "weights": weights_override}
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
    Uses /v3/pri/row which takes ScoreRowIn (supports filters and weights).
    """
    if not _online or _mode == "local":
        return _local_fallback_score_row(adapter, row, weights_override, None, None, filters)

    payload = {
        "adapter": adapter,
        "row": row,
        "weights": weights_override,
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
            return [
                _local_fallback_score_row(adapter, r, weights_override, None, None, filters)
                for r in rows
            ]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)

    payload = {
        "adapter": adapter,
        "rows": rows,
        "weights": weights_override,
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
            return [
                _local_fallback_score_row(adapter, r, weights_override, None, None, filters)
                for r in rows
            ]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)
    except ConnectionError as e:
        _log_note(BUG_NOTES, f"[pri-batch connect] {_slapi_url} :: {e}")
        _fallback_banner("Connection failed; treating as offline")
        if caps == "clamps":
            return [
                _local_fallback_score_row(adapter, r, weights_override, None, None, filters)
                for r in rows
            ]
        return _local_fallback_score_batch(adapter, rows, weights_override, None, None, filters)
    except Exception as e:
        _log_note(BUG_NOTES, f"[pri-batch unexpected] {e!r}")
        _fallback_banner("Unexpected API error")
        if caps == "clamps":
            return [
                _local_fallback_score_row(adapter, r, weights_override, None, None, filters)
                for r in rows
            ]
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
        # prove server is alive (public endpoint)
        try:
            _http_get("/v3/health")
        except Exception as e:
            _log_note(BUG_NOTES, f"[startup-health] {e!r}")

        # authenticated? (requires principal)
        if _has_device() and _has_apikey() and _has_device_id():
            try:
                _http_get("/v3/auth/whoami")
                authed = True
            except Exception as e:
                _log_note(BUG_NOTES, f"[startup-whoami] {e!r}")
                authed = False
        else:
            authed = False

    _online = bool(_reachable and authed)
    _print_mode_banner(reachable=_reachable, authed=_online, url=_slapi_url, mode=_mode)

    if _mode == "remote":
        if not _reachable:
            raise typer.BadParameter(
                f"SLAPI remote mode requires a reachable server at {_slapi_url}."
            )
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
            echo_clean_auto(me)
        except Exception as e:
            typer.secho(f"\nwhoami failed: {e}", fg=typer.colors.YELLOW)


@auth_app.command("device-init")
def auth_device_init(
    force: bool = typer.Option(False, "--force", help="Overwrite existing DEVICEKEY."),
) -> None:
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
        raise typer.BadParameter(
            "Device info requires SLAPI. Re-run with --mode auto or --mode remote."
        )
    data = _get_v3("/v3/auth/device")
    render_apikeys_view(data)


@auth_app.command("apikey-request")
def auth_apikey_request(
    owner: Optional[str] = typer.Option(
        None, "--owner", help="Optional owner label (defaults to host)."
    ),
    scopes: List[str] = typer.Option([], "--scope", help="Scope (repeatable)."),
    ttl_days: Optional[int] = typer.Option(None, "--ttl-days", help="Requested TTL in days."),
) -> None:
    """Create an API key request (requires enrolled ACTIVE device; device-proof only)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter(
            "API key request requires SLAPI. Re-run with --mode auto or --mode remote."
        )
    if not _has_device() or not _has_device_id():
        raise typer.BadParameter(
            "Device not enrolled. Run: statline auth device-init  (then)  statline auth enroll ..."
        )

    payload: Dict[str, Any] = {
        "owner": owner or platform.node(),
        "scopes": scopes or None,
        "ttl_days": ttl_days,
    }
    data = _post_v3("/v3/auth/apikey-requests", payload)
    rid = data.get("request_id")
    typer.secho("API key request created.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  request_id: {rid}")
    typer.echo(
        "Ask an admin to approve this request, then claim it with: statline auth apikey-claim --request-id <id>"
    )


@auth_app.command("apikey-claim")
def auth_apikey_claim(
    request_id: str = typer.Option(..., "--request-id", help="Request id from apikey-request."),
    activate: bool = typer.Option(True, "--activate/--no-activate", help="Write secrets/APIKEY."),
) -> None:
    """Claim an approved API key (requires enrolled device; device-proof only)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter(
            "API key claim requires SLAPI. Re-run with --mode auto or --mode remote."
        )
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
    echo_clean_auto(data)


@auth_app.command("apikeys")
def auth_apikeys() -> None:
    """List API keys for the active device (device-proof only)."""
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter(
            "apikeys requires SLAPI. Re-run with --mode auto or --mode remote."
        )
    data = _get_v3("/v3/auth/apikeys")
    render_apikeys_view(data)


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
    echo_clean_auto(data)


@mod_app.command("apikeys")
def mod_apikeys(org: Optional[str] = typer.Option(None, "--org")) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    data = _get_v3("/v3/mod/apikeys", params={"org": org} if org else None)
    render_apikeys_view(data)


@mod_app.command("apikey-access")
def mod_apikey_access(
    prefix: str = typer.Argument(...), value: bool = typer.Option(..., "--value")
) -> None:
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
def mod_revoke_device(
    device_id: str = typer.Argument(...), note: str = typer.Option("", "--note")
) -> None:
    ensure_banner()
    if _mode == "local":
        raise typer.BadParameter("Moderation requires SLAPI. Re-run with --mode remote.")
    data = _post_v3(
        f"/v3/mod/devices/{device_id}/revoke", None, params={"note": note} if note else None
    )
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
    echo_clean_auto(data)


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
    echo_clean_auto(data)


@admin_app.command("approve-apikey-request")
def admin_approve_apikey_request_cmd(
    request_id: str = typer.Argument(...),
    decided_by: str = typer.Option("admin", "--by"),
    note: str = typer.Option("", "--note"),
    scopes: List[str] = typer.Option(
        [], "--scope", help="Optional scope narrowing at approval (repeatable)."
    ),
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

    def show(x: Any) -> None:
        echo_clean_auto(x)

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
                render_apikeys_view(data)
            continue

        if top == "DEVKEY: init (generate files)":
            ow = typer.confirm("Overwrite existing DEVKEY files?", default=False)
            data = try_call(_post_v3, "/v3/admin/devkey/init", None, params={"overwrite": ow})
            if data is not None:
                render_apikeys_view(data)
            continue

        if top == "Mint registration token (reg_...)":
            org = str(typer.prompt("org", default="statline")).strip() or "statline"
            ttl = int(str(typer.prompt("ttl_days", default="14")).strip() or "14")
            scopes_raw = str(
                typer.prompt("scopes (comma sep) [blank=userbase]", default="")
            ).strip()
            scopes2 = [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else []
            params: Dict[str, Any] = {"org": org, "ttl_days": ttl, "scopes": scopes2 or None}
            data = try_call(_post_v3, "/v3/admin/mint-regtoken", None, params=params)
            if data is not None:
                typer.secho("Minted:", fg=typer.colors.GREEN, bold=True)
                show(data)
            continue

        if top == "Enrollments: list + approve/deny":
            status = str(typer.prompt("status", default="PENDING")).strip() or "PENDING"
            data = try_call(_get_v3, "/v3/admin/enrollments", params={"status": status})
            if not data:
                continue
            items = data.get("enrollments", []) if isinstance(data, dict) else []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            show(items)
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
                    params={"decided_by": decided_by, "note": note}
                    if (decided_by or note)
                    else None,
                )
            else:
                res = try_call(
                    _post_v3,
                    f"/v3/admin/enrollments/{rid}/deny",
                    None,
                    params={"decided_by": decided_by, "note": note}
                    if (decided_by or note)
                    else None,
                )
            if res is not None:
                show(res)
            continue

        if top == "API key requests: list + approve/deny":
            status = str(typer.prompt("status", default="PENDING")).strip() or "PENDING"
            org_raw = str(typer.prompt("org (blank=all)", default="")).strip()
            org_filter: Optional[str] = org_raw or None
            params3: Dict[str, Any] = {"status": status}
            if org_filter:
                params3["org"] = org_filter
            data = try_call(_get_v3, "/v3/admin/apikey-requests", params=params3)
            if not data:
                continue
            items = data.get("requests", []) if isinstance(data, dict) else []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            show(items)
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
                scopes_raw = str(
                    typer.prompt("narrow scopes (comma sep) [blank=no change]", default="")
                ).strip()
                scopes4 = (
                    [s.strip() for s in scopes_raw.split(",") if s.strip()] if scopes_raw else None
                )
                payload: Dict[str, Any] = {"decided_by": decided_by, "note": note}
                if scopes4 is not None:
                    payload["scopes"] = scopes4
                res = try_call(_post_v3, f"/v3/admin/apikey-requests/{rid}/approve", payload)
            else:
                payload2: Dict[str, Any] = {"decided_by": decided_by, "note": note}
                res = try_call(_post_v3, f"/v3/admin/apikey-requests/{rid}/deny", payload2)
            if res is not None:
                show(res)
            continue

        if top == "Moderation (best-effort): list apikeys":
            org_raw = str(typer.prompt("org (blank=all)", default="")).strip()
            org_filter: Optional[str] = org_raw or None
            data = try_call(
                _get_v3,
                "/v3/mod/apikeys",
                params={"org": org_filter} if org_filter else None,
            )
            if data is not None:
                show(data)
            continue

        if top == "Moderation (best-effort): audit log":
            limit = int(str(typer.prompt("limit", default="200")).strip() or "200")
            event = str(typer.prompt("event (blank=all)", default="")).strip() or None
            org_raw = str(typer.prompt("org (blank=all)", default="")).strip()
            org_filter: Optional[str] = org_raw or None

            params2: Dict[str, Any] = {"limit": limit}
            if event:
                params2["event"] = event
            if org_filter:
                params2["org"] = org_filter
            data = try_call(_get_v3, "/v3/mod/audit", params=params2)
            if data is not None:
                show(data)
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
    render_apikeys_view(data)


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

        typer.secho(
            "\nAdapter filters/dimensions (adapter-defined):", fg=typer.colors.BLUE, bold=True
        )
        out: Dict[str, Any] = {}
        for k in keys:
            # If adapter exposes options (dict shape), show a menu; else prompt raw.
            options: List[str] = []
            if isinstance(traits.get("filters"), dict) and k in cast(
                Dict[str, Any], traits["filters"]
            ):
                options = _as_str_list(cast(Dict[str, Any], traits["filters"]).get(k))
            elif isinstance(traits.get("dimensions"), dict) and k in cast(
                Dict[str, Any], traits["dimensions"]
            ):
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
                "context_used": _context_label(res.get("context_used"), "batch"),
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
        typer.secho(
            "\nAdapter did not report metrics; enter values (blank = 0):",
            fg=typer.colors.BLUE,
            bold=True,
        )
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
            results = api_score_batch(
                adapter_key, batch_rows, weights_override, None, None, filters
            )
            my_res = results[-1]
        else:
            typer.secho("No dataset selected; falling back to clamps.", fg=typer.colors.YELLOW)
            my_res = api_pri_row(adapter_key, raw_row, weights_override, filters)
    else:
        my_res = api_pri_row(adapter_key, raw_row, weights_override, filters)

    name = _name_for_row(raw_row, preferred=["display_name", "name"])
    pri = int(my_res.get("pri", 0))
    pri_raw = float(my_res.get("pri_raw", 0.0))
    ctx_used = _context_label(my_res.get("context_used"), "batch" if use_csv else "clamps")

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


@app.command("launch")
def launch() -> None:
    """Open the StatLine HomeShell."""
    from statline.tui.app import LauncherConfig, StatLineHomeShell

    StatLineHomeShell(
        typer_app=app,
        config=LauncherConfig(title="StatLine UX"),
    ).run()


@app.command("score")
def score(
    ctx: typer.Context,
    adapter: str = typer.Option(..., "--adapter", help="Adapter key (e.g., rbw5 or name@1.2.3)"),
    input_path: Path = typer.Argument(
        Path("stats.csv"),
        help="YAML/CSV understood by your adapter mapping (server-side), or '-' for CSV from stdin.",
    ),
    weights: Optional[Path] = typer.Option(
        None, "--weights", help="YAML mapping of {bucket: weight}"
    ),
    weights_preset: Optional[str] = typer.Option(
        None, "--weights-preset", help="Preset name you want to send"
    ),
    out: Optional[Path] = typer.Option(None, "--out", help="Write results (format via --fmt)"),
    include_headers: bool = typer.Option(
        True, "--headers/--no-headers", help="Include header row for CSV output"
    ),
    timing: Optional[bool] = typer.Option(
        None, "--timing/--no-timing", help="(Client flag only) — server may ignore."
    ),
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
    name_col: List[str] = typer.Option(
        [], "--name-col", help="Preferred name column(s); first non-empty wins."
    ),
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
    pretty: bool = typer.Option(
        False, "--pretty/--no-pretty", help="For json output: pretty-print JSON."
    ),
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
        results = api_pri_batch(
            adapter, raw_rows, weights_override, filters_dict or None, caps_mode="clamps"
        )
    else:
        results = api_score_batch(
            adapter, raw_rows, weights_override, None, None, filters_dict or None
        )

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
            "context_used": _context_label(res.get("context_used"), caps_mode),
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


# ─────────────────────────────────────────────────────────────────────────────
# Expanded v3 CLI surface — generated from project review
# ─────────────────────────────────────────────────────────────────────────────

adapter_app = typer.Typer(
    no_args_is_help=True, help="Adapter metadata, registry, sniffing, and spec inspection"
)
map_app = typer.Typer(no_args_is_help=True, help="Map raw rows through adapters without scoring")
calc_app = typer.Typer(no_args_is_help=True, help="Score already-mapped metric rows")
cache_app = typer.Typer(
    no_args_is_help=True, help="Local SLAPI cache inspection and refresh helpers"
)
storage_app = typer.Typer(no_args_is_help=True, help="CSV/storage utilities")
weights_app = typer.Typer(no_args_is_help=True, help="Weight utilities")

app.add_typer(adapter_app, name="adapter")
app.add_typer(map_app, name="map")
app.add_typer(calc_app, name="calc")
app.add_typer(cache_app, name="cache")
app.add_typer(storage_app, name="storage")
app.add_typer(weights_app, name="weights")


def _print_payload(data: Any, *, fmt: str = "json", pretty: bool = True) -> None:
    fmt_l = (fmt or "json").lower()
    if fmt_l == "json":
        if pretty:
            typer.echo(json.dumps(_normalize_for_display(data), ensure_ascii=False, indent=2))
        else:
            typer.echo(
                json.dumps(_normalize_for_display(data), ensure_ascii=False, separators=(",", ":"))
            )
    elif fmt_l == "jsonl":
        if isinstance(data, list):
            for row in data:  # pyright: ignore[reportUnknownVariableType]
                typer.echo(json.dumps(_normalize_for_display(row), ensure_ascii=False))
        else:
            typer.echo(json.dumps(_normalize_for_display(data), ensure_ascii=False))
    else:
        echo_clean_auto(data)


def _read_jsonish_arg(value: Optional[str], *, default: Any = None, name: str = "value") -> Any:
    if value is None or value == "":
        return default
    s = value.strip()
    try:
        return json.loads(s)
    except Exception:
        try:
            return _yaml_load_text(s)
        except Exception as e:
            raise typer.BadParameter(f"Could not parse {name} as JSON/YAML: {e}") from e


def _read_jsonish_file(path: Optional[Path], *, default: Any = None, name: str = "file") -> Any:
    if path is None:
        return default
    try:
        return _yaml_load_text(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise typer.BadParameter(f"Could not read {name} {path}: {e}") from e


def _merge_row_items(row_json: Optional[str], kv: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if row_json:
        parsed = _read_jsonish_arg(row_json, default={}, name="--row")
        if not isinstance(parsed, Mapping):
            raise typer.BadParameter("--row must parse to an object/mapping.")
        out.update({str(k): v for k, v in cast(Mapping[str, Any], parsed).items()})
    if kv:
        out.update(_parse_kv_items(_split_csvish(kv)))
    return out


def _load_weights_arg(
    weights: Optional[Path], preset: Optional[str]
) -> Optional[Union[Dict[str, float], str]]:
    if weights and preset:
        raise typer.BadParameter("Specify either --weights or --weights-preset, not both.")
    if preset:
        return preset
    if weights:
        data_any = _yaml_load_text(weights.read_text(encoding="utf-8"))
        if not isinstance(data_any, Mapping):
            raise typer.BadParameter("--weights must be a JSON/YAML mapping of {bucket: weight}.")
        return {str(k): float(v) for k, v in cast(Mapping[str, Any], data_any).items()}
    return None


def _score_output_options(  # pyright: ignore[reportUnusedFunction]
    *,
    show_weights: bool,
    hide_pri_raw: bool,
    show_components: bool,
    show_buckets: bool,
    show_context: bool,
    percentiles: bool,
) -> Dict[str, Any]:
    return {
        "show_weights": show_weights,
        "hide_pri_raw": hide_pri_raw,
        "show_components": show_components,
        "show_buckets": show_buckets,
        "show_context_used": show_context,
        "percentiles": percentiles,
    }


def _expr_identifiers(expr: Any) -> List[str]:
    """Return variable-like identifiers referenced by a safe adapter expression."""
    try:
        tree = ast.parse(str(expr), mode="eval")
    except Exception:
        return []

    called: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return sorted(n for n in names if n not in called)


def _local_adapter_spec_payload(adapter: str) -> Dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    from statline.core.adapters.loader import load_spec as _load_spec_local

    spec = _load_spec_local(adapter)
    raw = asdict(spec) if is_dataclass(spec) else dict(spec)  # type: ignore[arg-type]
    return cast(Dict[str, Any], _normalize_for_display(raw))


def _local_adapter_traits_payload(adapter: str) -> Dict[str, Any]:
    from statline.core.adapters.loader import load_spec as _load_spec_local

    spec = _load_spec_local(adapter)

    def metric_keys() -> List[str]:
        out: List[str] = []
        for m in getattr(spec, "metrics", []) or []:
            k = getattr(m, "key", None)
            if k:
                out.append(str(k))
        for e in getattr(spec, "efficiency", []) or []:
            k = getattr(e, "key", None)
            if k:
                out.append(str(k))
        return _as_str_list(out)

    def inputs() -> List[str]:
        out: List[str] = []
        for m in getattr(spec, "metrics", []) or []:
            src = getattr(m, "source", None)
            field = getattr(src, "field", None) if src is not None else None
            expr = getattr(src, "expr", None) if src is not None else None
            if field:
                out.append(str(field))
            elif expr:
                out.extend(_expr_identifiers(expr))
        for d in (getattr(spec, "dimensions", {}) or {}).keys():
            out.append(str(d))
        for f in (getattr(spec, "filters", {}) or {}).values():
            fld = getattr(f, "field", None)
            if fld:
                out.append(str(fld))
        return _as_str_list(out)

    dimensions: Dict[str, Any] = {}
    for key, dim in (getattr(spec, "dimensions", {}) or {}).items():
        dimensions[str(key)] = list(getattr(dim, "values", ()) or ())

    filters: Dict[str, Any] = {}
    for key, flt in (getattr(spec, "filters", {}) or {}).items():
        filters[str(key)] = {
            "type": getattr(flt, "type", "metric"),
            "field": getattr(flt, "field", str(key)),
            "accepts": list(getattr(flt, "accepts", ()) or ()),
            "modes": list(getattr(flt, "modes", ()) or ()),
            "description": getattr(flt, "description", ""),
        }

    return {
        "key": getattr(spec, "key", adapter),
        "title": getattr(spec, "title", adapter),
        "version": getattr(spec, "version", None),
        "aliases": list(getattr(spec, "aliases", ()) or ()),
        "inputs": inputs(),
        "metric_keys": metric_keys(),
        "filters": filters,
        "filter_keys": list(filters.keys()),
        "dimensions": dimensions,
        "weights": _normalize_for_display(getattr(spec, "weights", {}) or {}),
        "penalties": _normalize_for_display(getattr(spec, "penalties", {}) or {}),
        "score_profiles": list((getattr(spec, "score_profiles", {}) or {}).keys()),
    }


def _local_map_batch(adapter: str, rows: Rows) -> List[Dict[str, Any]]:
    from statline.core.calculator import safe_map_raw

    adp = load_adapter(adapter)
    return [safe_map_raw(adp, r) for r in rows]


def _local_map_row(adapter: str, row: Row) -> Dict[str, Any]:
    return _local_map_batch(adapter, [row])[0]


def _local_calc_batch(
    adapter: str,
    rows: Rows,
    *,
    weights_arg: Optional[Union[Dict[str, float], str]] = None,
    penalties_override: Optional[Dict[str, float]] = None,
    output: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Dict[str, float]]] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    from statline.core.scoring import calculate_pri

    adp = load_adapter(adapter)
    res = calculate_pri(
        [dict(r) for r in rows],
        adp,
        weights=weights_arg,
        penalties_override=penalties_override,
        output=output,
        context=context,
        caps_override=caps_override,
    )
    if isinstance(res, Mapping):
        return [dict(res)]
    return [dict(x) for x in res]


def _local_calc_row(adapter: str, row: Row, **kwargs: Any) -> Dict[str, Any]:
    return _local_calc_batch(adapter, [row], **kwargs)[0]


def _wire_format_to_path(path: Path, data: Any, *, fmt: str, include_headers: bool = True) -> None:
    fmt_l = (fmt or "json").lower()
    if fmt_l == "json":
        path.write_text(
            json.dumps(_normalize_for_display(data), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if fmt_l == "jsonl":
        rows = data if isinstance(data, list) else [data]  # pyright: ignore[reportUnknownVariableType]
        path.write_text(
            "".join(json.dumps(_normalize_for_display(r), ensure_ascii=False) + "\n" for r in rows),  # pyright: ignore[reportUnknownVariableType]
            encoding="utf-8",
        )  # pyright: ignore[reportUnknownVariableType]
        return
    if fmt_l == "csv":
        rows = data if isinstance(data, list) else [data]  # pyright: ignore[reportUnknownVariableType]
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fields: List[str] = []
        for r in rows:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(r, Mapping):
                for k in r.keys():  # pyright: ignore[reportUnknownVariableType]
                    ks = str(k)  # pyright: ignore[reportUnknownArgumentType]
                    if ks not in fields:
                        fields.append(ks)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if include_headers:
                w.writeheader()
            for r in rows:  # pyright: ignore[reportUnknownVariableType]
                w.writerow(dict(r) if isinstance(r, Mapping) else {"value": r})  # pyright: ignore[reportUnknownArgumentType]
        return
    raise typer.BadParameter("--fmt must be json, jsonl, or csv when --out is used.")


@adapter_app.command("list")
def adapter_list(
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
    fast: bool = typer.Option(
        False, "--fast", help="Remote only: ask server for fast YAML discovery."
    ),
    fmt: str = typer.Option("table", "--fmt", help="table|json", case_sensitive=False),
) -> None:
    """List available adapters from local registry or SLAPI."""
    ensure_banner()
    source_l = source.lower()
    if source_l not in {"auto", "local", "remote"}:
        raise typer.BadParameter("--source must be auto, local, or remote.")
    if source_l == "remote" or (source_l == "auto" and _online):
        data = _get_v3("/v3/adapters", params={"fast": fast})
    else:
        data = {"adapters": _local_adapter_names()}
    if fmt.lower() == "json":
        _print_payload(data)
    else:
        names = list(data.get("adapters", []) or []) if isinstance(data, Mapping) else []  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        for name in names:
            typer.echo(str(name))


@adapter_app.command("refresh")
def adapter_refresh() -> None:
    """Force the local adapter registry to rescan YAML defs."""
    ensure_banner()
    from statline.core.adapters import refresh

    refresh()
    typer.secho("Local adapter registry refreshed.", fg=typer.colors.GREEN, bold=True)


@adapter_app.command("spec")
def adapter_spec_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
    full: bool = typer.Option(
        False,
        "--full/--summary",
        help="Local only: emit full dataclass spec instead of server-style summary.",
    ),
    fmt: str = typer.Option("json", "--fmt", help="json|clean", case_sensitive=False),
) -> None:
    """Show adapter spec metadata, including buckets, inputs, filters, weights, and score profiles."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _get_v3(f"/v3/adapter/{adapter}/spec")
    else:
        data = (
            _local_adapter_spec_payload(adapter) if full else _local_adapter_traits_payload(adapter)
        )
    _print_payload(data, fmt=fmt)


@adapter_app.command("traits")
def adapter_traits_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
) -> None:
    """Show the operational trait bundle used by guided clients."""
    ensure_banner()
    data = (
        _get_v3(f"/v3/adapter/{adapter}/traits")
        if (source.lower() == "remote" or (source.lower() == "auto" and _online))
        else _local_adapter_traits_payload(adapter)
    )
    _print_payload(data)


@adapter_app.command("weights")
def adapter_weights_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
) -> None:
    """Show adapter weight presets."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _get_v3(f"/v3/adapter/{adapter}/weights")
    else:
        data = {"weights": _local_adapter_traits_payload(adapter).get("weights", {})}
    _print_payload(data)


@adapter_app.command("metrics")
def adapter_metrics_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
    probe: bool = typer.Option(False, "--probe"),
) -> None:
    """Show adapter metric keys. Use --probe for mapper-like keys on SLAPI."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        path = (
            f"/v3/adapter/{adapter}/metric-keys/probe"
            if probe
            else f"/v3/adapter/{adapter}/metric-keys"
        )
        data = _get_v3(path)
    else:
        data = {"keys": _local_adapter_traits_payload(adapter).get("metric_keys", [])}
    _print_payload(data)


@adapter_app.command("inputs")
def adapter_inputs_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
    raw: bool = typer.Option(False, "--raw"),
) -> None:
    """Show raw input/prompt keys expected by an adapter."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        path = f"/v3/adapter/{adapter}/inputs/raw" if raw else f"/v3/adapter/{adapter}/inputs"
        data = _get_v3(path)
    else:
        data = {"inputs": _local_adapter_traits_payload(adapter).get("inputs", [])}
    _print_payload(data)


@adapter_app.command("prompt-keys")
def adapter_prompt_keys_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
) -> None:
    """Show preferred prompt keys for manual row entry."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _get_v3(f"/v3/adapter/{adapter}/prompt-keys")
    else:
        data = {"keys": _local_adapter_traits_payload(adapter).get("inputs", [])}
    _print_payload(data)


@adapter_app.command("dimensions")
def adapter_dimensions_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
) -> None:
    """Show adapter dimensions and allowed values."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _get_v3(f"/v3/adapter/{adapter}/dimensions")
    else:
        data = {"dimensions": _local_adapter_traits_payload(adapter).get("dimensions", {})}
    _print_payload(data)


@adapter_app.command("filters")
def adapter_filters_cmd(
    adapter: str = typer.Argument(...),
    source: str = typer.Option("auto", "--source", case_sensitive=False),
) -> None:
    """Show declared adapter filters, operations, and modes."""
    ensure_banner()
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _get_v3(f"/v3/adapter/{adapter}/filters")
    else:
        traits = _local_adapter_traits_payload(adapter)
        data = {"filters": traits.get("filters", {}), "keys": traits.get("filter_keys", [])}
    _print_payload(data)


@adapter_app.command("sniff")
def adapter_sniff_cmd(
    headers: List[str] = typer.Argument(None, help="Header names, or omit and use --file."),
    file: Optional[Path] = typer.Option(
        None, "--file", "-f", help="CSV/YAML/JSON file whose first row/header should be sniffed."
    ),
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
) -> None:
    """Detect matching adapters from headers."""
    ensure_banner()
    header_list = _split_csvish(headers or [])
    if file is not None:
        try:
            rows = list(_read_rows(file))
            if rows:
                header_list.extend(str(k) for k in rows[0].keys())
        except Exception:
            from statline.slapi.storage.csv import peek_headers

            header_list.extend(peek_headers(file))
    header_list = _as_str_list(header_list)
    if not header_list:
        raise typer.BadParameter("Provide headers as arguments or pass --file.")
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _post_v3("/v3/adapters/sniff", {"headers": header_list})
    else:
        # The packaged sniff helper only considers require_any_headers in this rc;
        # the CLI handles both require_any_headers and require_all_headers so
        # adapters like eba_players/valorant are discoverable from real CSVs.
        matches: List[str] = []
        hset = {str(h).strip().lower() for h in header_list if str(h).strip()}
        for name in _local_adapter_names():
            try:
                adp = load_adapter(name)
                sniff = getattr(adp, "sniff", None)
                any_headers = list(getattr(sniff, "require_any_headers", ()) or [])
                all_headers = list(getattr(sniff, "require_all_headers", ()) or [])
                any_set = {str(h).strip().lower() for h in any_headers if str(h).strip()}
                all_set = {str(h).strip().lower() for h in all_headers if str(h).strip()}
                if (any_set and (any_set & hset)) or (all_set and all_set.issubset(hset)):
                    matches.append(str(getattr(adp, "key", name)))
            except Exception:
                continue
        data = {"adapters": _as_str_list(matches), "headers": header_list}
    _print_payload(data)


@map_app.command("row")
def map_row_cmd(
    adapter: str = typer.Option(..., "--adapter", "-a"),
    row: Optional[str] = typer.Option(
        None, "--row", help="JSON/YAML object containing raw fields."
    ),
    kv: List[str] = typer.Option(
        [], "--set", "-s", help="Raw field assignment, e.g. ppg=25. Repeatable or comma-separated."
    ),
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
    fmt: str = typer.Option("json", "--fmt", help="json|clean", case_sensitive=False),
) -> None:
    """Map one raw row through an adapter and print mapped metrics."""
    ensure_banner()
    raw = _merge_row_items(row, kv)
    if not raw:
        raise typer.BadParameter("Provide --row JSON/YAML or one or more --set key=value options.")
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _post_v3("/v3/map/row", {"adapter": adapter, "row": raw})
    else:
        data = _local_map_row(adapter, raw)
    _print_payload(data, fmt=fmt)


@map_app.command("batch")
def map_batch_cmd(
    input_path: Path = typer.Argument(..., help="CSV/YAML/JSON rows, or '-' for CSV stdin."),
    adapter: str = typer.Option(..., "--adapter", "-a"),
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
    out: Optional[Path] = typer.Option(None, "--out"),
    fmt: str = typer.Option("json", "--fmt", help="json|jsonl|csv|clean", case_sensitive=False),
    include_headers: bool = typer.Option(True, "--headers/--no-headers"),
) -> None:
    """Map raw rows through an adapter without scoring."""
    ensure_banner()
    rows = list(_read_rows(input_path))
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _post_v3("/v3/map/batch", {"adapter": adapter, "rows": rows})
    else:
        data = _local_map_batch(adapter, rows)
    if out:
        _wire_format_to_path(out, data, fmt=fmt, include_headers=include_headers)
    else:
        _print_payload(data, fmt=fmt)


@calc_app.command("row")
def calc_row_cmd(
    adapter: str = typer.Option(..., "--adapter", "-a"),
    row: Optional[str] = typer.Option(
        None, "--row", help="JSON/YAML object containing mapped metrics."
    ),
    kv: List[str] = typer.Option(
        [], "--set", "-s", help="Mapped metric assignment. Repeatable or comma-separated."
    ),
    weights: Optional[Path] = typer.Option(None, "--weights"),
    weights_preset: Optional[str] = typer.Option(None, "--weights-preset"),
    penalties: Optional[str] = typer.Option(
        None, "--penalties", help="JSON/YAML bucket penalty mapping."
    ),
    output: Optional[str] = typer.Option(None, "--output", help="JSON/YAML output toggle mapping."),
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
    fmt: str = typer.Option("json", "--fmt", help="json|clean", case_sensitive=False),
) -> None:
    """Score one already-mapped metric row. This mirrors /v3/calc/pri."""
    ensure_banner()
    mapped = _merge_row_items(row, kv)
    if not mapped:
        raise typer.BadParameter("Provide --row JSON/YAML or --set metric=value options.")
    weights_arg = _load_weights_arg(weights, weights_preset)
    penalties_override = cast(
        Optional[Dict[str, float]], _read_jsonish_arg(penalties, default=None, name="--penalties")
    )
    output_dict = cast(
        Optional[Dict[str, Any]], _read_jsonish_arg(output, default=None, name="--output")
    )
    payload: Dict[str, Any] = {
        "adapter": adapter,
        "row": mapped,
        "weights": weights_arg,
        "penalties_override": penalties_override,
        "output": output_dict,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _post_v3("/v3/calc/pri", payload)
    else:
        data = _local_calc_row(
            adapter,
            mapped,
            weights_arg=weights_arg,
            penalties_override=penalties_override,
            output=output_dict,
        )
    _print_payload(data, fmt=fmt)


@calc_app.command("batch")
def calc_batch_cmd(
    input_path: Path = typer.Argument(..., help="CSV/YAML/JSON mapped metric rows."),
    adapter: str = typer.Option(..., "--adapter", "-a"),
    weights: Optional[Path] = typer.Option(None, "--weights"),
    weights_preset: Optional[str] = typer.Option(None, "--weights-preset"),
    penalties: Optional[str] = typer.Option(
        None, "--penalties", help="JSON/YAML bucket penalty mapping."
    ),
    output: Optional[str] = typer.Option(None, "--output", help="JSON/YAML output toggle mapping."),
    caps_mode: str = typer.Option(
        "batch", "--caps-mode", help="batch|clamps", case_sensitive=False
    ),
    source: str = typer.Option("auto", "--source", help="auto|local|remote", case_sensitive=False),
    out: Optional[Path] = typer.Option(None, "--out"),
    fmt: str = typer.Option("json", "--fmt", help="json|jsonl|csv|clean", case_sensitive=False),
) -> None:
    """Score already-mapped metric rows. This mirrors /v3/calc/pri/batch."""
    ensure_banner()
    rows = list(_read_rows(input_path))
    weights_arg = _load_weights_arg(weights, weights_preset)
    penalties_override = cast(
        Optional[Dict[str, float]], _read_jsonish_arg(penalties, default=None, name="--penalties")
    )
    output_dict = cast(
        Optional[Dict[str, Any]], _read_jsonish_arg(output, default=None, name="--output")
    )
    payload: Dict[str, Any] = {
        "adapter": adapter,
        "rows": rows,
        "weights": weights_arg,
        "penalties_override": penalties_override,
        "output": output_dict,
        "caps_mode": caps_mode,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if source.lower() == "remote" or (source.lower() == "auto" and _online):
        data = _post_v3("/v3/calc/pri/batch", payload)
    else:
        if (caps_mode or "batch").lower() == "clamps":
            data = [
                _local_calc_row(
                    adapter,
                    r,
                    weights_arg=weights_arg,
                    penalties_override=penalties_override,
                    output=output_dict,
                )
                for r in rows
            ]
        else:
            data = _local_calc_batch(
                adapter,
                rows,
                weights_arg=weights_arg,
                penalties_override=penalties_override,
                output=output_dict,
            )
    if out:
        _wire_format_to_path(out, data, fmt=fmt)
    else:
        _print_payload(data, fmt=fmt)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
    reload: bool = typer.Option(False, "--reload/--no-reload"),
    background: bool = typer.Option(
        False,
        "--background/--foreground",
        "-b/-f",
        help="Run SLAPI in the background and return to the shell.",
    ),
    detached_child: bool = typer.Option(
        False,
        "--detached-child",
        help="Internal flag used by --background.",
        hidden=True,
    ),
) -> None:
    """Run SLAPI locally with uvicorn, optionally in the background."""
    ensure_banner()

    if background and not detached_child:
        if reload:
            raise typer.BadParameter("--background does not support --reload. Use --no-reload.")

        LOG_DIR.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-m",
            "statline.cli",
            "--mode",
            "local",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
            "--no-reload",
            "--foreground",
            "--detached-child",
        ]

        out_f = SLAPI_OUT_LOG.open("ab")
        err_f = SLAPI_ERR_LOG.open("ab")

        creationflags = 0
        start_new_session = False

        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "DETACHED_PROCESS", 0
            )
        else:
            start_new_session = True

        proc = subprocess.Popen(
            cmd,
            stdout=out_f,
            stderr=err_f,
            stdin=subprocess.DEVNULL,
            cwd=str(Path.cwd()),
            creationflags=creationflags,
            start_new_session=start_new_session,
            close_fds=(os.name != "nt"),
        )

        _write_text(SLAPI_PID_FILE, str(proc.pid))

        typer.secho("SLAPI started in background.", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  url:  http://{host}:{port}")
        typer.echo(f"  pid:  {proc.pid}")
        typer.echo(f"  pidfile: {SLAPI_PID_FILE}")
        typer.echo(f"  stdout:  {SLAPI_OUT_LOG}")
        typer.echo(f"  stderr:  {SLAPI_ERR_LOG}")
        return

    try:
        import uvicorn
    except Exception as e:
        raise typer.BadParameter(
            "SLAPI serving requires uvicorn. Install with: pip install 'statline[api]'"
        ) from e

    def _install_rc_scoring_compat() -> None:
        """Patch missing rc compatibility names before statline.slapi.scoring imports them."""
        import statline.core.scoring as core_scoring
        from statline.core.calculator import score_row_from_raw, score_rows_from_raw

        class _CompatPRIResult:
            def __init__(self, payload: Mapping[str, Any]) -> None:
                self.pri = payload.get("pri")
                self.details = {k: v for k, v in payload.items() if k != "pri"}

        def calculate_pri_single(
            *,
            adapter: Any,
            row: Mapping[str, Any],
            weights: Any = None,
            output: Any = None,
            filters: Any = None,
            penalties_override: Any = None,
            context: Any = None,
            caps_override: Any = None,
            timing: Any = None,
        ) -> Any:
            return _CompatPRIResult(
                score_row_from_raw(
                    row,
                    adapter,
                    weights=weights,
                    output=output,
                    filters=filters,
                    penalties_override=penalties_override,
                    context=context,
                    caps_override=caps_override,
                    timing=timing,
                )
            )

        def calculate_pri_batch(
            *,
            adapter: Any,
            rows: Iterable[Mapping[str, Any]],
            weights: Any = None,
            output: Any = None,
            filters: Any = None,
            penalties_override: Any = None,
            context: Any = None,
            caps_override: Any = None,
            timing: Any = None,
        ) -> List[Any]:
            return [
                _CompatPRIResult(r)
                for r in score_rows_from_raw(
                    rows,
                    adapter,
                    weights=weights,
                    output=output,
                    filters=filters,
                    penalties_override=penalties_override,
                    context=context,
                    caps_override=caps_override,
                    timing=timing,
                )
            ]

        if not hasattr(core_scoring, "calculate_pri_single"):
            setattr(core_scoring, "calculate_pri_single", calculate_pri_single)
        if not hasattr(core_scoring, "calculate_pri_batch"):
            setattr(core_scoring, "calculate_pri_batch", calculate_pri_batch)

    _install_rc_scoring_compat()

    uvicorn.run(
        "statline.slapi.app:app",
        host=host,
        port=port,
        reload=reload,
    )


@auth_app.command("apikey-requests")
def auth_apikey_requests_cmd() -> None:
    """List API-key requests for the active enrolled device."""
    ensure_banner()
    data = _get_v3("/v3/auth/apikey-requests")
    echo_clean_auto(data)


@auth_app.command("revoke-apikey")
def auth_revoke_apikey_cmd(
    prefix: str = typer.Argument(..., help="API key prefix8 to revoke for this device."),
) -> None:
    """Revoke one API key belonging to the active device."""
    ensure_banner()
    data = _delete_v3(f"/v3/auth/apikeys/{prefix}")
    echo_clean_auto(data)


@admin_app.command("devkey-init")
def admin_devkey_init_cmd(
    overwrite: bool = typer.Option(False, "--overwrite/--no-overwrite"),
) -> None:
    """Generate DEVKEY + DEVKEY.pub on the SLAPI host."""
    ensure_banner()
    data = _post_v3("/v3/admin/devkey/init", None, params={"overwrite": overwrite})
    echo_clean_auto(data)


@admin_app.command("devkey")
def admin_devkey_info_cmd() -> None:
    """Show SLAPI DEVKEY fingerprint."""
    ensure_banner()
    data = _get_v3("/v3/admin/devkey")
    echo_clean_auto(data)


@admin_app.command("inspect-regtoken")
def admin_inspect_regtoken_cmd(
    token: str = typer.Argument(..., help="reg_ token to inspect."),
) -> None:
    """Inspect a registration token payload."""
    ensure_banner()
    data = _post_v3("/v3/admin/regtoken/inspect", None, params={"token": token})
    echo_clean_auto(data)


@admin_app.command("enrollment")
def admin_enrollment_get_cmd(request_id: str = typer.Argument(...)) -> None:
    """Fetch one enrollment request by id."""
    ensure_banner()
    data = _get_v3(f"/v3/admin/enrollments/{request_id}")
    echo_clean_auto(data)


@admin_app.command("debug-core-adapters")
def admin_debug_core_adapters_cmd() -> None:
    """Admin-only debug view of adapter YAML files visible to SLAPI."""
    ensure_banner()
    data = _get_v3("/v3/admin/debug/core-adapters")
    echo_clean_auto(data)


@admin_app.command("debug-registry-list")
def admin_debug_registry_list_cmd() -> None:
    """Admin-only debug view of the server adapter registry."""
    ensure_banner()
    data = _get_v3("/v3/admin/debug/registry-list")
    echo_clean_auto(data)


@cache_app.command("db-path")
def cache_db_path_cmd() -> None:
    """Print the local SLAPI SQLite cache path."""
    ensure_banner()
    from statline.slapi.storage.sqlite import get_db_path

    typer.echo(str(get_db_path()))


@cache_app.command("scopes")
def cache_scopes_cmd() -> None:
    """List known cache scopes."""
    ensure_banner()
    from statline.slapi.cache import iterate_scopes

    _print_payload({"scopes": list(iterate_scopes())})


@cache_app.command("config")
def cache_config_cmd(scope: str = typer.Argument(...)) -> None:
    """Show cache sync config for a scope."""
    ensure_banner()
    from statline.slapi.cache import get_scope_config

    cfg = get_scope_config(scope)
    _print_payload(None if cfg is None else {"scope": cfg.scope, "last_sync_ts": cfg.last_sync_ts})


@cache_app.command("touch")
def cache_touch_cmd(
    scope: str = typer.Argument(...),
    last_sync_ts: Optional[int] = typer.Option(None, "--last-sync-ts"),
) -> None:
    """Create/update cache sync config for a scope."""
    ensure_banner()
    from statline.slapi.cache import now_ts, update_scope_config

    update_scope_config(scope, last_sync_ts=now_ts() if last_sync_ts is None else last_sync_ts)
    typer.secho("Cache scope config updated.", fg=typer.colors.GREEN, bold=True)


@cache_app.command("should-sync")
def cache_should_sync_cmd(
    scope: str = typer.Argument(...), ttl_sec: int = typer.Option(86400, "--ttl-sec")
) -> None:
    """Return whether a scope is stale under the given TTL."""
    ensure_banner()
    from statline.slapi.cache import should_sync_scope

    _print_payload(
        {
            "scope": scope,
            "should_sync": should_sync_scope(scope, ttl_sec=ttl_sec),
            "ttl_sec": ttl_sec,
        }
    )


@cache_app.command("refresh")
def cache_refresh_cmd(
    scope: str = typer.Argument(...),
    ttl_sec: int = typer.Option(86400, "--ttl-sec"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Refresh one scope via optional sheets sync hook if stale/forced."""
    ensure_banner()
    from statline.slapi.cache import sync_scope_if_stale

    _print_payload(
        {"scope": scope, "upserted": sync_scope_if_stale(scope, ttl_sec=ttl_sec, force=force)}
    )


@cache_app.command("refresh-all")
def cache_refresh_all_cmd(
    ttl_sec: int = typer.Option(86400, "--ttl-sec"), force: bool = typer.Option(False, "--force")
) -> None:
    """Refresh all known scopes via optional sheets sync hook."""
    ensure_banner()
    from statline.slapi.cache import refresh_all_scopes

    _print_payload(refresh_all_scopes(ttl_sec=ttl_sec, force=force))


@cache_app.command("entities")
def cache_entities_cmd(
    scope: str = typer.Argument(...), limit: int = typer.Option(0, "--limit")
) -> None:
    """List cached entities for a scope."""
    ensure_banner()
    from statline.slapi.cache import get_entities_for_scope

    rows = get_entities_for_scope(scope)
    _print_payload(rows[:limit] if limit else rows)


@cache_app.command("metrics")
def cache_metrics_cmd(
    scope: str = typer.Argument(...),
    fuzzy_key: Optional[str] = typer.Option(None, "--fuzzy-key"),
    limit: int = typer.Option(0, "--limit"),
) -> None:
    """List cached metrics for a scope or one entity."""
    ensure_banner()
    from statline.slapi.cache import get_metrics_for_entity, get_metrics_for_scope

    data: Any = (
        get_metrics_for_entity(scope, fuzzy_key) if fuzzy_key else get_metrics_for_scope(scope)
    )
    if isinstance(data, list) and limit:
        data = data[:limit]  # pyright: ignore[reportUnknownVariableType]
    _print_payload(data)


@cache_app.command("metric-keys")
def cache_metric_keys_cmd(scope: str = typer.Argument(...)) -> None:
    """List distinct cached metric keys for a scope."""
    ensure_banner()
    from statline.slapi.cache import get_distinct_metric_keys

    _print_payload({"keys": get_distinct_metric_keys(scope)})


@storage_app.command("csv-peek")
def storage_csv_peek_cmd(path: Path = typer.Argument(...)) -> None:
    """Print normalized CSV headers."""
    ensure_banner()
    from statline.slapi.storage.csv import peek_headers

    _print_payload({"headers": peek_headers(path)})


@storage_app.command("csv-read")
def storage_csv_read_cmd(
    path: Path = typer.Argument(...), limit: int = typer.Option(20, "--limit")
) -> None:
    """Read a CSV using StatLine's tolerant CSV reader."""
    ensure_banner()
    from statline.slapi.storage.csv import read_csv_rows

    rows = read_csv_rows(path)
    _print_payload(rows[:limit] if limit else rows)


@storage_app.command("csv-write")
def storage_csv_write_cmd(
    input_path: Path = typer.Argument(..., help="JSON/YAML rows to write."),
    out: Path = typer.Option(..., "--out"),
    fields: List[str] = typer.Option(
        [], "--field", help="Field order; repeatable or comma-separated."
    ),
    include_headers: bool = typer.Option(True, "--headers/--no-headers"),
) -> None:
    """Write JSON/YAML rows to CSV using StatLine's CSV writer."""
    ensure_banner()
    from statline.slapi.storage.csv import write_csv_rows

    data = _read_jsonish_file(input_path, default=[], name="input")
    if isinstance(data, Mapping):
        rows = [cast(Mapping[str, Any], data)]
    elif isinstance(data, list):
        rows = [cast(Mapping[str, Any], x) for x in data if isinstance(x, Mapping)]  # pyright: ignore[reportUnknownVariableType]
    else:
        raise typer.BadParameter("input must be an object or list of objects.")
    count, used_fields = write_csv_rows(
        out, rows, fieldnames=_split_csvish(fields) or None, include_header=include_headers
    )
    _print_payload({"rows_written": count, "fields": used_fields, "out": str(out)})


@weights_app.command("normalize")
def weights_normalize_cmd(
    items: List[str] = typer.Argument(..., help="key=value weight pairs."),
) -> None:
    """L1-normalize arbitrary weights using statline.core.weights.normalize_weights."""
    ensure_banner()
    from statline.core.weights import normalize_weights

    raw = _parse_kv_items(_split_csvish(items))
    weights = {k: float(v) for k, v in raw.items()}
    _print_payload(normalize_weights(weights))


@weights_app.command("resolve")
def weights_resolve_cmd(
    adapter: str = typer.Option(..., "--adapter", "-a"),
    preset: Optional[str] = typer.Option(None, "--preset"),
    override: List[str] = typer.Option(
        [], "--override", help="bucket=value override; repeatable or comma-separated."
    ),
) -> None:
    """Resolve and normalize a weight profile/override against an adapter's buckets."""
    ensure_banner()
    from statline.core.weights import normalize_weights, pick_profile

    adp = load_adapter(adapter)
    profiles = getattr(adp, "weights", {}) or {}
    base = dict(pick_profile(profiles, preset))
    base.update({k: float(v) for k, v in _parse_kv_items(_split_csvish(override)).items()})
    _print_payload({"weights": base, "normalized": normalize_weights(base)})


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
