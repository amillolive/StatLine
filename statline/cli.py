from __future__ import annotations

# ── stdlib ────────────────────────────────────────────────────────────────────
import contextlib
import csv
import importlib
import io
import os
import re
import sys
from collections.abc import Mapping as AbcMapping  # runtime checks
from pathlib import Path
from typing import (
    Any,
    Callable,
    ContextManager,
    Dict,
    Generator,
    Iterable,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    TextIO,
    Tuple,
    TypedDict,
    cast,
)

# ── third-party ───────────────────────────────────────────────────────────────
import click  # Typer is built on Click
import typer

# ── first-party ───────────────────────────────────────────────────────────────
from statline.core.adapters import list_names
from statline.core.adapters import load as load_adapter
from statline.core.calculator import interactive_mode
from statline.core.scoring import (  # adapter-driven PRI
    calculate_pri,  # adapter-driven PRI
    calculate_pri_single,
)
from statline.utils.timing import StageTimes  # runtime import


# -- local typing view of StageTimes (for Pyright only) -----------------------
class _StageTimesProto(Protocol):
    items: List[tuple[str, float]]
    def stage(self, name: str) -> ContextManager[None]: ...

# ── typing helpers (reduce "Unknown" noise) ───────────────────────────────────
Row = Dict[str, Any]
Rows = List[Row]

class _ViewRow(TypedDict):
    Rank: int
    Name: str
    PRI: int
    Raw: str
    Context: str

# Columns as Literals so TypedDict indexing is type-safe
_COLS = Tuple[
    Literal["Rank"],
    Literal["Name"],
    Literal["PRI"],
    Literal["Raw"],
    Literal["Context"],
]
COLS: _COLS = ("Rank", "Name", "PRI", "Raw", "Context")

Context = Dict[str, Dict[str, float]]
AdapterMappingFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]

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

    # compute widths without starred “unknown” comprehensions
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

class AdapterProto(Protocol):
    """Minimal surface we rely on from adapters."""
    KEY: str

    def map_raw_to_metrics(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def map_raw(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...


# ── optional YAML (prefer C-accelerated loader if present) ───────────────────
class _YamlLike(Protocol):
    CSafeLoader: Any
    SafeLoader: Any
    def load(self, stream: str, *, Loader: Any) -> Any: ...
    def safe_load(self, stream: str) -> Any: ...

yaml_mod: Optional[_YamlLike]
_yaml_loader: Optional[Any]
try:
    import yaml as _yaml_import
    yaml_mod = cast(_YamlLike, _yaml_import)
    _yaml_loader = getattr(_yaml_import, "CSafeLoader", getattr(_yaml_import, "SafeLoader", None))
except Exception:
    yaml_mod = None
    _yaml_loader = None
    
# env switch; global default is set by root option below
STATLINE_DEBUG_TIMING: bool = os.getenv("STATLINE_DEBUG") == "1"

app = typer.Typer(no_args_is_help=True)

# ──────────────────────────────────────────────────────────────────────────────
# Unified banner helpers
# ──────────────────────────────────────────────────────────────────────────────

_BANNER_LINE: str = "=== StatLine — Adapter-Driven Scoring ==="
_BANNER_REGEX = re.compile(r"^===\s*StatLine\b.*===\s*$")


def _print_banner() -> None:
    # typer.colors.* is untyped in some stubs; keep fg as Any to avoid unknown-member noise
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

# ──────────────────────────────────────────────────────────────────────────────
# Root callback (global options + "no subcommand" UX)
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_timing(ctx: typer.Context, local: Optional[bool]) -> bool:
    """Prefer a command-local --timing value; else inherit from root; else env."""
    if local is not None:
        return local
    try:
        root = ctx.find_root()
        if root.obj and "timing" in root.obj:
            return bool(root.obj["timing"])
    except Exception:
        pass
    return STATLINE_DEBUG_TIMING


@app.callback(invoke_without_command=True)
def _root( # pyright: ignore[reportUnusedFunction]
    ctx: typer.Context,
    timing: bool = typer.Option(
        True,  # default ON at the root; subcommands can override
        "--timing/--no-timing",
        help="Show per-stage timing summaries (default: on; use --no-timing to hide).",
    ),
) -> None:
    """Top-level CLI entry. Shows help when run with no subcommand."""
    root = ctx.find_root()
    if root.obj is None:
        root.obj = {}
    root.obj["timing"] = timing

    ensure_banner()

    if ctx.invoked_subcommand is None:
        # Mirror the 'startup CLI' style: show commands/usage and exit 0
        typer.echo(ctx.get_help())
        raise typer.Exit(0)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _maybe_sanity(adp: Any) -> Optional[Callable[[Mapping[str, Any]], None]]:
    """Return adapter.sanity as a typed callable if present, else None."""
    attr = getattr(adp, "sanity", None)
    if callable(attr):
        return cast(Callable[[Mapping[str, Any]], None], attr)
    return None


def _name_for_row(raw: Mapping[str, Any], preferred: Optional[List[str]] = None) -> str:
    """
    Resolve a display name:
      1) first non-empty from --name-col list (case-insensitive)
      2) common fields: display_name, name, player, id (any casing)
      3) first+last / firstname+lastname
      4) team + jersey/number
      5) '(unnamed)'
    """
    # 1) user-preferred columns
    if preferred:
        for key in preferred:
            for variant in (key, key.lower(), key.upper(), key.title()):
                v = raw.get(variant)
                if v:
                    s = str(v).strip()
                    if s:
                        return s

    # 2) common fields
    for key in ("display_name", "name", "player", "id", "DISPLAY_NAME", "Player", "ID"):
        v = raw.get(key)
        if v:
            s = str(v).strip()
            if s:
                return s

    # 3) first + last combos
    first = raw.get("first") or raw.get("First") or raw.get("firstname") or raw.get("Firstname")
    last  = raw.get("last")  or raw.get("Last")  or raw.get("lastname")  or raw.get("Lastname")
    if first or last:
        s = f"{str(first or '').strip()} {str(last or '').strip()}".strip()
        if s:
            return s

    # 4) synthetic label from team + jersey/number
    team = raw.get("team") or raw.get("Team")
    num  = raw.get("jersey") or raw.get("Jersey") or raw.get("number") or raw.get("Number")
    if team or num:
        return f"{team or 'Team'} #{num or '?'}"

    # 5) fallback
    return "(unnamed)"


def _coerce_float(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return 0.0
    return 0.0


def _get_adapter_mapper(adp: AdapterProto) -> AdapterMappingFn:
    # Prefer map_raw_to_metrics when present, else map_raw
    if hasattr(adp, "map_raw_to_metrics") and callable(getattr(adp, "map_raw_to_metrics")):
        return cast(AdapterMappingFn, getattr(adp, "map_raw_to_metrics"))
    if hasattr(adp, "map_raw") and callable(getattr(adp, "map_raw")):
        return cast(AdapterMappingFn, getattr(adp, "map_raw"))
    raise typer.BadParameter(
        f"Adapter '{getattr(adp, 'KEY', adp)}' lacks map_raw/map_raw_to_metrics."
    )


def _map_with_adapter(adp: AdapterProto, row: Mapping[str, Any]) -> Dict[str, float]:
    fn: AdapterMappingFn = _get_adapter_mapper(adp)
    out: Mapping[str, Any] = fn(row)
    safe: Dict[str, float] = {}
    for k, v in out.items():
        safe[str(k)] = _coerce_float(v)
    return safe


def _yaml_load_text(text: str) -> Any:
    if yaml_mod is None:
        raise typer.BadParameter("PyYAML not installed; cannot read YAML.")
    if _yaml_loader is not None:
        return yaml_mod.load(text, Loader=_yaml_loader)
    return yaml_mod.safe_load(text)


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

        # Always build a concretely-typed list for iteration
        src: List[Mapping[str, Any]] = []

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


# Match the real csv._writer signature: positional-only and Iterable[Any]
class _CsvWriter(Protocol):
    def writerow(self, row: Iterable[Any], /) -> Any: ...


def _write_csv(path: Path, rows: Rows, include_headers: bool = True) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fixed_front = [k for k in ("display_name", "group_name") if k in rows[0]]
    all_keys: set[str] = set()
    for r in rows:
        for k in r.keys():
            all_keys.add(str(k))
    for k in fixed_front:
        all_keys.discard(k)
    headers: List[str] = fixed_front + sorted(all_keys)

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        w = cast(_CsvWriter, writer)
        if include_headers:
            w.writerow(headers)
        for r in rows:
            w.writerow([str(r.get(k, "")) for k in headers])


def _load_bucket_weights(
    adapter_obj: AdapterProto,
    weights_path: Optional[Path],
    weights_preset: Optional[str],
) -> Optional[Dict[str, float]]:
    if weights_path and weights_preset:
        raise typer.BadParameter("Specify either --weights or --weights-preset, not both.")

    # ── explicit typing for YAML branch ───────────────────────────────────────
    if weights_path:
        data_any: Any = _yaml_load_text(weights_path.read_text(encoding="utf-8"))
        if not isinstance(data_any, Mapping):
            raise typer.BadParameter("--weights YAML must be a mapping of {bucket: weight}.")
        data_map: Mapping[str, Any] = cast(Mapping[str, Any], data_any)

        out: Dict[str, float] = {}
        for k_any, v_any in data_map.items():
            out[str(k_any)] = _coerce_float(v_any)
        return out

    # ── explicit typing for adapter presets ───────────────────────────────────
    presets: Mapping[str, Mapping[str, Any]] = cast(
        Mapping[str, Mapping[str, Any]], getattr(adapter_obj, "weights", {}) or {}
    )
    if not presets:
        return None

    preset_name = (weights_preset or "pri").lower()
    if preset_name not in presets:
        avail = ", ".join(sorted(presets.keys()))
        raise typer.BadParameter(
            f"Unknown weights preset '{preset_name}'. Available: {avail or '(none)'}"
        )

    weights_map: Mapping[str, Any] = presets[preset_name]
    out2: Dict[str, float] = {}
    for k_any, v_any in weights_map.items():
        out2[str(k_any)] = _coerce_float(v_any)
    return out2


def _lazy_cache_export(guild_id: str) -> Rows:
    try:
        mod = importlib.import_module("statline.core.cache")
        fn = getattr(mod, "get_mapped_rows_for_scoring", None)
        if not callable(fn):
            return []

        rows_obj: Any = fn(guild_id)
        out: Rows = []

        if isinstance(rows_obj, (list, tuple)):
            for r_any in cast(Iterable[Any], rows_obj):
                if isinstance(r_any, Mapping):
                    d = cast(Mapping[str, Any], r_any)
                    out.append({str(k): v for k, v in d.items()})
            return out

        if isinstance(rows_obj, Mapping):
            d = cast(Mapping[str, Any], rows_obj)
            return [{str(k): v for k, v in d.items()}]

        return []
    except Exception:
        return []


def _lazy_cache_context(guild_id: str) -> Optional[Context]:
    try:
        mod = importlib.import_module("statline.core.cache")
        fn = getattr(mod, "get_metric_context_ap", None)
        if not callable(fn):
            return None

        ctx_obj: Any = fn(guild_id)
        if not isinstance(ctx_obj, Mapping):
            return None

        safe: Context = {}
        for k, d in cast(Mapping[str, Any], ctx_obj).items():
            if isinstance(d, Mapping):
                dd: Dict[str, float] = {}
                for mk, mv in cast(Mapping[str, Any], d).items():
                    try:
                        dd[str(mk)] = float(mv) if mv is not None else 0.0
                    except Exception:
                        dd[str(mk)] = 0.0
                safe[str(k)] = dd
        return safe
    except Exception:
        return None


def _lazy_force_refresh(guild_id: str) -> None:
    try:
        mod = importlib.import_module("statline.core.refresh")
        fn = getattr(mod, "sync_guild_if_stale", None)
        if callable(fn):
            fn(guild_id, force=True)
    except Exception:
        return


def _autobuild_stats_csv(
    output_path: Path, guild_id: str, refresh: bool
) -> Rows:
    if refresh:
        _lazy_force_refresh(guild_id)
    rows = _lazy_cache_export(guild_id)
    if not rows:
        raise typer.BadParameter(
            f"No cached rows for guild '{guild_id}'. Run a sync first or provide a CSV/YAML."
        )
    _write_csv(output_path, rows, include_headers=True)
    return rows

# ──────────────────────────────────────────────────────────────────────────────
# Typed shim around calculate_pri
# ──────────────────────────────────────────────────────────────────────────────

def _calc_pri_typed(
    rows: Rows,
    adp: AdapterProto,
    *,
    team_wins: int,
    team_losses: int,
    weights_override: Optional[Dict[str, float]],
    context: Optional[Context],
    _timing: Optional[_StageTimesProto] = None,
    caps_override: Optional[Dict[str, float]] = None,
) -> Rows:
    return calculate_pri(
        rows,
        adapter=adp,
        team_wins=team_wins,
        team_losses=team_losses,
        weights_override=weights_override,
        context=context,
        caps_override=caps_override,
        _timing=cast(Any, _timing),  # runtime accepts StageTimes; proto satisfies type-checker
    )

# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────

@app.command("interactive")
def interactive(
    ctx: typer.Context,
    timing: Optional[bool] = typer.Option(
        None,
        "--timing/--no-timing",
        help="Show per-row timing inside interactive mode (inherits root default).",
    ),
) -> None:
    """Run the interactive calculator UI."""
    ensure_banner()
    show_timing = _resolve_timing(ctx, timing) or STATLINE_DEBUG_TIMING
    try:
        interactive_mode(show_banner=False, show_timing=show_timing)
    except (KeyboardInterrupt, EOFError):
        print("\nExiting StatLine.")
        raise typer.Exit(code=0)


@app.command("adapters")
def adapters_list() -> None:
    """List available adapter keys."""
    ensure_banner()
    names_iter: Iterable[str] = cast(Iterable[str], list_names())
    for name in sorted(names_iter):
        typer.echo(name)


@app.command("export-csv")
def export_csv(
    adapter: str = typer.Option(..., "--adapter", help="Adapter key (for validation only)"),
    guild_id: str = typer.Option(..., "--guild-id", help="Guild to export from"),
    out: Path = typer.Option(Path("stats.csv"), "--out", help="Destination CSV path"),
    include_headers: bool = typer.Option(True, "--headers/--no-headers", help="Include header row"),
    refresh: bool = typer.Option(False, "--refresh/--no-refresh", help="Force a Sheets refresh before export"),
) -> None:
    """Explicitly export the guild's mapped metrics to CSV (no scoring)."""
    ensure_banner()
    _ = load_adapter(adapter)  # validate adapter exists
    rows = _autobuild_stats_csv(out, guild_id=guild_id, refresh=refresh)
    typer.secho(f"Wrote {out} ({len(rows)} rows).", fg=getattr(typer.colors, "GREEN", None))


@app.command("score")
def score(
    ctx: typer.Context,
    adapter: str = typer.Option(..., "--adapter", help="Adapter key (e.g., rbw5, legacy, valorant)"),
    input_path: Path = typer.Argument(
        Path("stats.csv"),
        help="YAML/CSV understood by the adapter. If missing, use --guild-id to auto-build.",
    ),
    guild_id: Optional[str] = typer.Option(
        None, "--guild-id", help="Guild to export from when auto-generating stats.csv"
    ),
    refresh: bool = typer.Option(
        False, "--refresh/--no-refresh", help="Force a Sheets refresh before auto-generating"
    ),
    weights: Optional[Path] = typer.Option(None, "--weights", help="YAML mapping of {bucket: weight}"),
    weights_preset: Optional[str] = typer.Option("pri", "--weights-preset", help="Adapter preset name (default: 'pri')"),
    out: Optional[Path] = typer.Option(None, "--out", help="Write results (format via --fmt)"),
    include_headers: bool = typer.Option(True, "--headers/--no-headers", help="Include header row for CSV output"),
    team_wins: int = typer.Option(0, "--team-wins", help="Team wins for small PRI multiplier"),
    team_losses: int = typer.Option(0, "--team-losses", help="Team losses for small PRI multiplier"),
    timing: Optional[bool] = typer.Option(
        None, "--timing/--no-timing", help="Print per-stage timing summary (inherits root default)."
    ),
    caps_csv: Optional[Path] = typer.Option(
        None, "--caps-csv", help="CSV with per-metric caps (key[,lower,upper,cap])"
    ),
    caps: str = typer.Option("batch", "--caps", help="Cap source: 'batch' or 'clamps'", case_sensitive=False),
    fmt: str = typer.Option("table", "--fmt", help="Output format: csv|table|md|json", case_sensitive=False),
    name_col: List[str] = typer.Option([], "--name-col", help="Preferred name column(s); first non-empty wins."),
    limit: int = typer.Option(0, "--limit", min=0, help="Limit rows shown (0=all)"),
) -> None:
    """
    Batch score via an adapter. Supports name resolution, clamp/batch caps, and multiple output formats.
    """
    ensure_banner()
    show_timing = _resolve_timing(ctx, timing) or STATLINE_DEBUG_TIMING
    fmt_lower = (fmt or "table").lower()
    caps_mode = (caps or "batch").lower()
    if caps_mode not in {"batch", "clamps"}:
        raise typer.BadParameter("--caps must be 'batch' or 'clamps'")
    if fmt_lower not in {"csv", "table", "md", "json"}:
        raise typer.BadParameter("--fmt must be one of: csv, table, md, json")

    T: _StageTimesProto = cast(_StageTimesProto, StageTimes())

    with T.stage("adapter"):
        adp = cast(AdapterProto, load_adapter(adapter))
        bucket_weights = _load_bucket_weights(adp, weights, weights_preset)

    raw_rows: Rows = []
    mapped_rows: Rows = []

    if not input_path.exists() and str(input_path) != "-":
        if guild_id is None:
            raise typer.BadParameter(
                f"{input_path} does not exist. Provide --guild-id to auto-generate, "
                "or pass a YAML/CSV file, or use '-' for stdin."
            )
        with T.stage("autobuild"):
            mapped_rows = _autobuild_stats_csv(input_path, guild_id=guild_id, refresh=refresh)
            typer.secho(
                f"Auto-generated {input_path} from guild '{guild_id}'.",
                fg=getattr(typer.colors, "GREEN", None),
            )
    else:
        with T.stage("read"):
            raw_rows = list(_read_rows(input_path))
        with T.stage("map"):
            sanity = _maybe_sanity(adp)
            for r in raw_rows:
                m = _map_with_adapter(adp, r)
                if sanity:
                    sanity(m)
                mapped_rows.append(m)

    with T.stage("context"):
        context = _lazy_cache_context(guild_id) if guild_id else None

    mapped_rows_list: Rows = mapped_rows

    # ── scoring ───────────────────────────────────────────────────────────────
    results_list: Rows = []  # predeclare for type-checker
    with T.stage("score"):
        if caps_mode == "clamps":
            # Force clamp caps: use single-row scorer (no batch context)
            for m in mapped_rows_list:
                res = calculate_pri_single(
                    m,
                    adp,
                    team_wins=team_wins,
                    team_losses=team_losses,
                    weights_override=bucket_weights,
                    context=None,
                    caps_override=None,  # single-row path prefers adapter clamps
                )
                results_list.append(res)
        else:
            # Batch caps (leaders/floors from batch or external context)
            results_list = _calc_pri_typed(
                mapped_rows_list,
                adp,
                team_wins=team_wins,
                team_losses=team_losses,
                weights_override=bucket_weights,
                context=context,
                _timing=T,
            )

    # ── format rows for output ────────────────────────────────────────────────
    with T.stage("write"):
        out_fields: List[str] = ["name", "pri", "pri_raw", "context_used"]
        rows_out: Rows = []
        have_raw = len(raw_rows) == len(mapped_rows_list) and len(raw_rows) > 0
        for i in range(len(mapped_rows_list)):
            raw_src = raw_rows[i] if have_raw else mapped_rows_list[i]
            res = results_list[i]
            rows_out.append(
                {
                    "name": _name_for_row(raw_src, name_col),
                    "pri": int(res.get("pri", 0)),
                    "pri_raw": f"{float(res.get('pri_raw', 0.0)):.4f}",
                    "context_used": res.get("context_used", ""),
                }
            )

        # Sort for readability
        rows_out.sort(key=lambda r: r.get("pri", 0), reverse=True)
        view = rows_out[: (limit or len(rows_out))]

        # File output
        if out:
            if fmt_lower == "csv":
                with out.open("w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    w = cast(_CsvWriter, writer)
                    if include_headers:
                        w.writerow(out_fields)
                    for row in view:
                        w.writerow([str(row.get(k, "")) for k in out_fields])
            elif fmt_lower == "json":
                import json
                out.write_text(json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8")
            elif fmt_lower == "md":
                lines = ["| Rank | Name | PRI | Raw | Context |", "|---:|:-----|---:|----:|:---|"]
                for i, r in enumerate(view, 1):
                    lines.append(f'| {i} | {r["name"]} | {int(r["pri"])} | {float(r["pri_raw"]):.4f} | {r.get("context_used","")} |')
                out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:  # table
                out.write_text(_render_table(view, 0), encoding="utf-8")
        else:
            # Stdout
            if fmt_lower == "csv":
                writer = csv.writer(sys.stdout)
                w = cast(_CsvWriter, writer)
                if include_headers:
                    w.writerow(out_fields)
                for row in view:
                    w.writerow([str(row.get(k, "")) for k in out_fields])
            elif fmt_lower == "json":
                import json
                print(json.dumps(view, ensure_ascii=False, indent=2))
            elif fmt_lower == "md":
                print("| Rank | Name | PRI | Raw | Context |")
                print("|---:|:-----|---:|----:|:---|")
                for i, r in enumerate(view, 1):
                    print(f'| {i} | {r["name"]} | {int(r["pri"])} | {float(r["pri_raw"]):.4f} | {r.get("context_used","")} |')
            else:
                print(_render_table(view, 0))

    if show_timing:
        total = sum(ms for _, ms in T.items)
        parts = ", ".join(f"{n} {ms:.2f}" for n, ms in T.items)
        print(file=sys.stderr)
        print(f"⏱ {total:.2f} ms total ({parts})", file=sys.stderr)


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
