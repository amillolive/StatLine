from __future__ import annotations

# ── stdlib ────────────────────────────────────────────────────────────────────
import contextlib
import csv
import io
import os
import re
import sys
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

# ── third-party ───────────────────────────────────────────────────────────────
import click  # Typer is built on Click
import typer

# ── HTTP backend (quiet for type checkers) ────────────────────────────────────
_http: Any  # single alias we treat as Any to avoid Pylance noise
try:
    import httpx as _http
    _http_lib = "httpx"
except Exception:  # pragma: no cover
    import requests as _http
    _http_lib = "requests"

# ── banner & timing defaults ──────────────────────────────────────────────────

STATLINE_DEBUG_TIMING: bool = os.getenv("STATLINE_DEBUG") == "1"
DEFAULT_SLAPI_URL: str = os.getenv("SLAPI_URL", "http://127.0.0.1:8000").rstrip("/")
DEFAULT_SLAPI_KEY: Optional[str] = os.getenv("SLAPI_KEY")

# mutable runtime config (don’t mutate ALL-CAPS constants)
_slapi_url: str = DEFAULT_SLAPI_URL
_slapi_key: Optional[str] = DEFAULT_SLAPI_KEY

app = typer.Typer(no_args_is_help=True)

_BANNER_LINE: str = "=== StatLine — Adapter-Driven Scoring (via SLAPI) ==="
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

def _pick_dataset_via_menu(title: str) -> Optional[str]:
    """
    Server-first dataset list (via /v2/datasets), fallback to local scan.
    Returns a file path or None.
    """
    candidates = api_list_datasets()
    if not candidates:
        candidates = local_list_datasets()

    if not candidates:
        # No known datasets: let user type a path
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

# ── HTTP client helpers ───────────────────────────────────────────────────────

# ── secrets locations ---------------------------------------------------------

_STATLINE_DIR = Path(__file__).resolve().parent

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
REGKEY_PATH: Path = SECRETS_DIR / "REGKEY"
KEYS_DIR:   Path = SECRETS_DIR / "keys"
DEVKEY_PATH: Path = SECRETS_DIR / "DEVKEY"

def _read_text(p: Path) -> Optional[str]:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None

def _write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")

def _read_devkey() -> bytes:
    if not DEVKEY_PATH.exists():
        tried = "\n  - " + "\n  - ".join(str(x) for x in _candidate_secret_dirs() if x)
        raise typer.BadParameter(
            f"Missing DEVKEY. Checked:{tried}\n"
            f"Put your DEVKEY at: {DEVKEY_PATH}"
        )
    return DEVKEY_PATH.read_bytes()

def _host_fp() -> str:
    import hashlib
    return hashlib.sha256(_read_devkey()).hexdigest()[:16]


def _auto_provision_regkey(owner: Optional[str] = None) -> str:
    fp = _host_fp()
    if owner is None:
        owner = getenv("STATLINE_OWNER") or os.uname().nodename

    data = _http_post(
        "/v2/admin/generate-key",
        [],  # body must be a list
        extra_headers={"X-Host-FP": fp},
        params={"owner": owner},  # owner must be in query
    )
    token = str(data["token"]).strip()
    prefix = str(data.get("prefix", token[4:12]))

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    _write_text(KEYS_DIR / f"{prefix}.reg", token)
    _write_text(REGKEY_PATH, token)
    return token

def _read_regkey() -> str:
    s = _read_text(REGKEY_PATH)
    if not s:
        if DEVKEY_PATH.exists():
            try:
                return _auto_provision_regkey()
            except Exception:
                pass
        tried = "\n  - " + "\n  - ".join(str(x / "REGKEY") for x in _candidate_secret_dirs())
        raise typer.BadParameter(
            "Access key missing. Paste the gifted regkey into a file named REGKEY.\n"
            f"Checked:{tried}\n"
            f"Recommended location: {REGKEY_PATH}"
        )
    s = s.strip()
    if s.startswith("SLAPI_KEY="):
        s = s.split("=", 1)[1].strip()
    if not s.startswith("reg_"):
        raise typer.BadParameter(f"{REGKEY_PATH} doesn’t look like a reg_ token.")
    return s

def api_list_datasets() -> List[Dict[str, str]]:
    try:
        data = _http_get("/v2/datasets")
        ds = data.get("datasets", [])
        out = []
        for it in ds:
            name = str(it.get("name", "") or "")
            path = str(it.get("path", "") or "")
            if name and path:
                out.append({"name": name, "path": path}) # pyright: ignore[reportUnknownMemberType]
        return out # pyright: ignore[reportUnknownVariableType]
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

def api_adapter_prompt_keys(adapter: str) -> List[str]:
    # Prefer strict metric prompt keys
    try:
        data = _http_get(f"/v2/adapter/{adapter}/prompt-keys")
        items = data.get("keys", [])
        keys = [str(x).strip() for x in items if isinstance(x, (str, int, float)) and str(x).strip()]
        if keys:
            return keys
    except Exception:
        pass
    # Fallback to the older, broader inputs (may include efficiency refs)
    try:
        data = _http_get(f"/v2/adapter/{adapter}/inputs")
        items = data.get("inputs", [])
        return [str(x).strip() for x in items if isinstance(x, (str, int, float)) and str(x).strip()]
    except Exception:
        return []

def _headers(*, extra: Optional[Dict[str, str]] = None, use_regkey: bool = True) -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json"}
    if use_regkey:
        key = _read_regkey()
        h["Authorization"] = f"Bearer {key}"
        h["X-StatLine-Key"] = key
    if extra:
        h.update(extra)
    return h


# ── HTTP client helpers ───────────────────────────────────────────────────────

def _http_get(path: str, params: Optional[Dict[str, Any]] = None, *,
              extra_headers: Optional[Dict[str, str]] = None) -> Any:
    url = f"{_slapi_url}{path}"
    use_reg = not path.startswith("/v2/admin")
    if _http_lib == "httpx" and hasattr(_http, "Client"):
        with _http.Client(timeout=60.0) as c:
            r = c.get(url, headers=_headers(extra=extra_headers, use_regkey=use_reg), params=params)
            _raise_for_status(r)
            return r.json()
    else:
        r = _http.get(url, headers=_headers(extra=extra_headers, use_regkey=use_reg), params=params, timeout=60.0)
        _raise_for_status(r)
        return r.json()

def _http_post(
    path: str,
    payload: Any,
    *,
    extra_headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Any:
    url = f"{_slapi_url}{path}"
    use_reg = not path.startswith("/v2/admin")
    if _http_lib == "httpx" and hasattr(_http, "Client"):
        with _http.Client(timeout=300.0) as c:
            r = c.post(
                url,
                headers=_headers(extra=extra_headers, use_regkey=use_reg),
                params=params,
                json=payload,  # send proper JSON
            )
            _raise_for_status(r)
            return r.json()
    else:
        r = _http.post(
            url,
            headers=_headers(extra=extra_headers, use_regkey=use_reg),
            params=params,
            json=payload,
            timeout=300.0,
        )
        _raise_for_status(r)
        return r.json()

def _raise_for_status(resp: Any) -> None:
    code = getattr(resp, "status_code", None)
    if code is None or (200 <= code < 300):
        return
    try:
        detail = resp.json()
    except Exception:
        detail = getattr(resp, "text", "")
    raise typer.BadParameter(f"SLAPI {code}: {detail}")

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

# ── typing helpers ────────────────────────────────────────────────────────────

Row = Dict[str, Any]
Rows = List[Row]
WeightsOverride = Dict[str, Any]

class _ViewRow(TypedDict):
    Rank: int
    Name: str
    PRI: int
    Raw: str
    Context: str

_COLS = Tuple[
    Literal["Rank"],
    Literal["Name"],
    Literal["PRI"],
    Literal["Raw"],
    Literal["Context"],
]
COLS: _COLS = ("Rank", "Name", "PRI", "Raw", "Context")

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

def _name_for_row(raw: Mapping[str, Any], preferred: Optional[List[str]] = None) -> str:
    if preferred:
        for key in preferred:
            for variant in (key, key.lower(), key.upper(), key.title()):
                v = raw.get(variant)
                if v:
                    s = str(v).strip()
                    if s:
                        return s
    for key in ("display_name", "name", "player", "id", "DISPLAY_NAME", "Player", "ID"):
        v = raw.get(key)
        if v:
            s = str(v).strip()
            if s:
                return s
    first = raw.get("first") or raw.get("First") or raw.get("firstname") or raw.get("Firstname")
    last  = raw.get("last")  or raw.get("Last")  or raw.get("lastname")  or raw.get("Lastname")
    if first or last:
        s = f"{str(first or '').strip()} {str(last or '').strip()}".strip()
        if s:
            return s
    team = raw.get("team") or raw.get("Team")
    num  = raw.get("jersey") or raw.get("Jersey") or raw.get("number") or raw.get("Number")
    if team or num:
        return f"{team or 'Team'} #{num or '?'}"
    return "(unnamed)"

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
                from collections.abc import Mapping as AbcMapping
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

# ── formatting helpers ────────────────────────────────────────────────────────

class _CsvWriterProtocol:
    def writerow(self, row: Iterable[Any], /) -> Any: ...

def _render_table(rows: Rows, limit: int = 0) -> str:
    data: List[_ViewRow] = []
    for i, r in enumerate(rows, 1):
        vr = cast(_ViewRow, {
            "Rank": i,
            "Name": str(r.get("name", "(unnamed)")),
            "PRI": int(r.get("pri", 0)),
            "Raw": f'{float(r.get("pri_raw", 0.0)):.4f}',
            "Context": str(r.get("context_used", "")),
        })
        data.append(vr)
    if limit and limit > 0:
        data = data[:limit]
    widths: Dict[str, int] = {c: len(c) for c in COLS}
    for row in data:
        for c in COLS:
            w = len(str(row[c]))
            if w > widths[c]:
                widths[c] = w
    def line(ch: str) -> str:
        parts: List[str] = []
        for c in COLS:
            parts.append(ch * (widths[c] + 2))
        return "+" + "+".join(parts) + "+"
    out: List[str] = []
    out.append(line("-"))
    out.append("| " + " | ".join(c.ljust(widths[c]) for c in COLS) + " |")
    out.append(line("="))
    for row in data:
        out.append("| " + " | ".join(str(row[c]).ljust(widths[c]) for c in COLS) + " |")
    out.append(line("-"))
    return "\n".join(out)

# ── API facades ───────────────────────────────────────────────────────────────

def api_adapter_metric_keys(adapter: str) -> List[str]:
    try:
        data = _http_get(f"/v2/adapter/{adapter}/metric-keys")
        items = data.get("keys", [])
        return [str(x).strip() for x in items if isinstance(x, (str, int, float)) and str(x).strip()]
    except Exception:
        return []

def api_adapter_weight_presets(adapter: str) -> List[str]:
    try:
        data = _http_get(f"/v2/adapter/{adapter}/weights")
        w = data.get("weights") or {} # pyright: ignore[reportUnknownVariableType]
        if isinstance(w, dict):
            # server already returns spec.weights mapping
            return sorted([str(k) for k in w.keys()]) # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    except Exception:
        pass
    return []


def api_list_adapters() -> List[str]:
    data = _http_get("/v2/adapters")
    adapters = data.get("adapters", [])
    return [str(x) for x in adapters]

def api_adapter_inputs(adapter: str) -> List[str]:
    try:
        data = _http_get(f"/v2/adapter/{adapter}/inputs")
        items = data.get("inputs", [])
        return [str(x) for x in items if isinstance(x, (str, int, float))]
    except Exception:
        return []

def api_score_batch(
    adapter: str,
    rows: Rows,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
) -> Rows:
    payload = {
        "adapter": adapter,
        "rows": rows,
        "weights_override": weights_override,
        "context": context,
        "caps_override": caps_override,
    }
    try:
        data = _http_post("/v2/score/batch", payload)
        if isinstance(data, list):
            return cast(Rows, data)
        return cast(Rows, data.get("results", []))
    except typer.BadParameter as e:
        if "404" in str(e) or "Not Found" in str(e):
            out: Rows = []
            for r in rows:
                out.append(api_score_row(adapter, r, weights_override, context, caps_override))
            return out
        raise

def api_score_row(
    adapter: str,
    row: Row,
    weights_override: Optional[Union[Dict[str, Any], str]],
    context: Optional[Dict[str, Dict[str, float]]],
    caps_override: Optional[Dict[str, float]],
) -> Row:
    payload = {
        "adapter": adapter,
        "row": row,
        "weights_override": weights_override,
        "context": context,
        "caps_override": caps_override,
    }
    try:
        data = _http_post("/v2/score/row", payload)
        return cast(Row, data)
    except typer.BadParameter as e:
        if "404" in str(e) or "Not Found" in str(e):
            pri_payload = {"adapter": adapter, "row": row, "weights_override": weights_override}
            data = _http_post("/v2/calc/pri", pri_payload)
            return cast(Row, data)
        raise

def api_calc_pri_single(adapter: str, row: Row, weights_override: Optional[Union[Dict[str, Any], str]]) -> Row:
    payload = {"adapter": adapter, "row": row, "weights_override": weights_override}
    try:
        data = _http_post("/v2/calc/pri", payload)
        return cast(Row, data)
    except typer.BadParameter as e:
        if "404" in str(e) or "Not Found" in str(e):
            return api_score_row(adapter, row, weights_override, None, None)
        raise

# ── commands ─────────────────────────────────────────────────────────────────
def _root(
    ctx: typer.Context,
    timing: bool = typer.Option(
        True, "--timing/--no-timing",
        help="Show per-stage timing summaries (default: on; use --no-timing to hide).",
    ),
    url: str = typer.Option(
        DEFAULT_SLAPI_URL, "--url", envvar="SLAPI_URL",
        help="Base URL for StatLine API (default env SLAPI_URL).",
    ),
) -> None:
    global _slapi_url
    _slapi_url = (url or DEFAULT_SLAPI_URL).rstrip("/")

    # sanity: fail fast if REGKEY is absent for non-admin commands
    # (admin commands call with extra_headers, so they can run even if REGKEY absent)
    # We won’t exit here because you might run an admin command first; runtime calls
    # that need REGKEY will read it via _headers() and error clearly if missing.

    root = ctx.find_root()
    if root.obj is None:
        root.obj = {}
    root.obj["timing"] = timing

    ensure_banner()

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)

app.callback(invoke_without_command=True)(_root)

@app.command("adapters")
def adapters_list() -> None:
    """List available adapter keys (via SLAPI)."""
    ensure_banner()
    try:
        for name in sorted(api_list_adapters()):
            typer.echo(name)
    except Exception as e:
        raise typer.BadParameter(f"Failed to list adapters from SLAPI ({_slapi_url}): {e}")

@app.command("interactive")
def interactive(
    ctx: typer.Context,
    timing: Optional[bool] = typer.Option(
        None,
        "--timing/--no-timing",
        help="Show per-row timing inside interactive mode (inherits root default).",
    ),
) -> None:
    """Run an in-CLI interactive session using SLAPI endpoints."""
    ensure_banner()
    _ = _resolve_timing(ctx, timing) or STATLINE_DEBUG_TIMING

    # 1) choose adapter
    names = api_list_adapters()
    if not names:
        typer.secho("No adapters available from SLAPI.", fg=typer.colors.RED)
        raise typer.Exit(1)

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

    adapter_key = menu_select("Available adapters:", names, 0)

    # 2) choose weights preset (server returns adapter weights profiles)
    presets = api_adapter_weight_presets(adapter_key)
    weights_override: Optional[Union[Dict[str, float], str]] = None
    if presets:
        chosen = menu_select("Weight presets:", presets, 0)
        weights_override = chosen  # send preset name string
    else:
        weights_override = None

    # 3) choose mode (Batch default)
    mode = menu_select("Mode:", ["batch", "single"], 0)

    # ===== Batch mode =====
    if mode == "batch":
        csv_path = _pick_dataset_via_menu("Datasets:")
        if not csv_path:
            typer.secho("No dataset selected.", fg=typer.colors.RED)
            raise typer.Exit(1)

        raw_rows: Rows = list(_read_rows(Path(csv_path)))
        if not raw_rows:
            typer.secho("Selected CSV has no rows.", fg=typer.colors.RED)
            raise typer.Exit(1)

        results = api_score_batch(adapter_key, raw_rows, weights_override, None, None)

        # Render a compact table like `score` command
        rows_out: Rows = []
        for i in range(len(raw_rows)):
            src = raw_rows[i]
            res = results[i]
            rows_out.append(
                {
                    "name": _name_for_row(src, []),
                    "pri": int(res.get("pri", 0)),
                    "pri_raw": f"{float(res.get('pri_raw', 0.0)):.4f}",
                    "context_used": res.get("context_used", ""),
                }
            )
        rows_out.sort(key=lambda r: r.get("pri", 0), reverse=True)

        typer.secho("\nBatch results", bold=True)
        print(_render_table(rows_out, 0))
        return

    # ===== Single mode =====
    # Prompt for display name first
    raw_row: Dict[str, Any] = {}
    player_name = str(typer.prompt("Player name (for display)", default="")).strip()
    if player_name:
        raw_row["display_name"] = player_name

    # Strict metric prompts (no efficiency/bucket bleed-through)
    prompt_keys = api_adapter_metric_keys(adapter_key)
    if prompt_keys:
        typer.secho("\nEnter values for adapter metrics (Enter = 0, 'skip' to skip):",
                    fg=typer.colors.BLUE, bold=True)
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
        # Optional extras (ad-hoc stats)
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
        typer.secho("\nAdapter did not report metrics; enter values (blank = 0):",
                    fg=typer.colors.BLUE, bold=True)
        while True:
            k = str(typer.prompt("stat/key (blank to finish)", default="")).strip()
            if not k:
                break
            v = typer.prompt(f"value for {k}", default="0")
            try:
                raw_row[k] = float(str(v).strip().replace(",", "."))
            except ValueError:
                raw_row[k] = 0.0

    # Scaling choice for single: dataset (55..99) vs clamps (single-row → 99)
    use_csv = typer.confirm("Scale this row against a CSV dataset? (y/N)", default=False)
    if use_csv:
        csv_path = _pick_dataset_via_menu("Datasets:")
        if csv_path:
            # Combine selected CSV + this single row; score as a batch to get 55..99 scaling
            batch_rows = list(_read_rows(Path(csv_path)))
            batch_rows.append(raw_row)
            results = api_score_batch(adapter_key, batch_rows, weights_override, None, None)
            my_res = results[-1]
        else:
            typer.secho("No dataset selected; falling back to clamps.", fg=typer.colors.YELLOW)
            my_res = api_calc_pri_single(adapter_key, raw_row, weights_override)
    else:
        # Adapter clamps path (single-row ⇒ PRI=99 by design)
        my_res = api_calc_pri_single(adapter_key, raw_row, weights_override)

    name = _name_for_row(raw_row, preferred=["display_name", "name"])
    pri = int(my_res.get("pri", 0))
    pri_raw = float(my_res.get("pri_raw", 0.0))
    ctx_used = str(my_res.get("context_used", ""))
    typer.secho("\nResult", bold=True)
    typer.echo(f"Name: {name}")
    typer.echo(f"PRI:  {pri} / 99 (raw {pri_raw:.4f}, context {ctx_used})")

@app.command("generate-key")
def cli_generate_key(
    owner: str,
    activate: bool = typer.Option(False, "--activate", help="Also set secrets/REGKEY to this new key on this machine.")
) -> None:
    ensure_banner()
    fp = _host_fp()
    data = _http_post(
        "/v2/admin/generate-key",
        [],  # body must be a list
        extra_headers={"X-Host-FP": fp},
        params={"owner": owner},
    )
    token = str(data["token"]).strip()
    prefix = str(data.get("prefix", token[4:12]))
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    gift_path = KEYS_DIR / f"{prefix}.reg"
    _write_text(gift_path, token)
    if activate:
        _write_text(REGKEY_PATH, token)

    typer.secho("Key generated.", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  owner:  {data.get('owner')}")
    typer.echo(f"  prefix: {prefix}")
    typer.echo(f"  file:   {gift_path}")
    if activate:
        typer.echo(f"  active: {REGKEY_PATH}")
    typer.echo("\nGift this file to a user. They should place its contents into statline/secrets/REGKEY on their machine.")

@app.command("keys")
def cli_keys() -> None:
    ensure_banner()
    fp = _host_fp()
    data = _http_get("/v2/admin/keys", extra_headers={"X-Host-FP": fp})
    for k in data.get("keys", []):
        typer.echo(f"{k['prefix']}  {k['owner']}  access={k['access']}  scopes={k['scopes']}  host_fp={k['host_fp']}")

@app.command("use-key")
def cli_use_key(prefix: str) -> None:
    """Activate a gifted key file (writes secrets/REGKEY)."""
    gift = KEYS_DIR / f"{prefix}.reg"
    if not gift.exists():
        raise typer.BadParameter(f"Gifted file not found: {gift}")
    token = _read_text(gift) or ""
    token = token.strip()
    if not token.startswith("reg_"):
        raise typer.BadParameter(f"{gift} does not contain a reg_ token.")
    _write_text(REGKEY_PATH, token)
    typer.secho(f"Activated key {prefix}. REGKEY updated.", fg=typer.colors.GREEN)

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
    timing: Optional[bool] = typer.Option(
        None, "--timing/--no-timing", help="(Client flag only) — server may ignore."
    ),
    caps: str = typer.Option("batch", "--caps", help="Cap source: 'batch' or 'clamps'", case_sensitive=False),
    fmt: str = typer.Option("table", "--fmt", help="Output format: csv|table|md", case_sensitive=False),
    name_col: List[str] = typer.Option([], "--name-col", help="Preferred name column(s); first non-empty wins."),
    limit: int = typer.Option(0, "--limit", min=0, help="Limit rows shown (0=all)"),
) -> None:
    """Batch score via SLAPI. Reads rows locally, sends to server, and renders a table/CSV/MD."""
    ensure_banner()
    _ = _resolve_timing(ctx, timing) or STATLINE_DEBUG_TIMING

    fmt_lower = (fmt or "table").lower()
    caps_mode = (caps or "batch").lower()
    if caps_mode not in {"batch", "clamps"}:
        raise typer.BadParameter("--caps must be 'batch' or 'clamps'")
    if fmt_lower not in {"csv", "table", "md"}:
        raise typer.BadParameter("--fmt must be one of: csv, table, md")

    raw_rows: Rows = list(_read_rows(input_path))

    # IMPORTANT: send a string for presets, not {"__preset__": "..."}
    weights_override: Optional[Union[Dict[str, float], str]]
    weights_override = None
    if weights and weights_preset:
        raise typer.BadParameter("Specify either --weights or --weights-preset, not both.")
    if weights:
        data_any: Any = _yaml_load_text(weights.read_text(encoding="utf-8"))
        if not isinstance(data_any, Mapping):
            raise typer.BadParameter("--weights YAML must be a mapping of {bucket: weight}.")
        weights_override = {str(k): float(v) for k, v in cast(Mapping[str, Any], data_any).items()}
    elif weights_preset:
        weights_override = str(weights_preset)  # ← send the preset name directly

    if caps_mode == "clamps":
        results: Rows = [api_calc_pri_single(adapter, m, weights_override) for m in raw_rows]
    else:
        results = api_score_batch(adapter, raw_rows, weights_override, None, None)

    out_fields: List[str] = ["name", "pri", "pri_raw", "context_used"]
    rows_out: Rows = []
    for i in range(len(raw_rows)):
        src = raw_rows[i]
        res = results[i]
        rows_out.append(
            {
                "name": _name_for_row(src, name_col),
                "pri": int(res.get("pri", 0)),
                "pri_raw": f"{float(res.get('pri_raw', 0.0)):.4f}",
                "context_used": res.get("context_used", ""),
            }
        )
    rows_out.sort(key=lambda r: r.get("pri", 0), reverse=True)
    view = rows_out[: (limit or len(rows_out))]

    if out:
        if fmt_lower == "csv":
            with out.open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                w = cast(_CsvWriterProtocol, writer)
                if include_headers:
                    w.writerow(out_fields)
                for row in view:
                    w.writerow([str(row.get(k, "")) for k in out_fields])
        elif fmt_lower == "md":
            lines = ["| Rank | Name | PRI | Raw | Context |", "|---:|:-----|---:|----:|:---|"]
            for i, r in enumerate(view, 1):
                lines.append(
                    f'| {i} | {r["name"]} | {int(r["pri"])} | {float(r["pri_raw"]):.4f} | {r.get("context_used","")} |'
                )
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            out.write_text(_render_table(view, 0), encoding="utf-8")
    else:
        if fmt_lower == "csv":
            writer = csv.writer(sys.stdout)
            w = cast(_CsvWriterProtocol, writer)
            if include_headers:
                w.writerow(out_fields)
            for row in view:
                w.writerow([str(row.get(k, "")) for k in out_fields])
        elif fmt_lower == "md":
            print("| Rank | Name | PRI | Raw | Context |")
            print("|---:|:-----|---:|----:|:---|")
            for i, r in enumerate(view, 1):
                print(
                    f'| {i} | {r["name"]} | {int(r["pri"])} | {float(r["pri_raw"]):.4f} | {r.get("context_used","")} |'
                )
        else:
            print(_render_table(view, 0))

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
