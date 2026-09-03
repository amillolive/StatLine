"""Shared Rich terminal presentation functions for StatLine commands."""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from rich import box
from rich.console import Console, RenderableType
from rich.measure import measure_renderables
from rich.table import Table
from rich.terminal_theme import MONOKAI

from statline.core.types.timing import StageTimes


def slug_profile(name: str) -> str:
    """Return the canonical flattened key for a score profile."""
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def profile_names(
    results: Sequence[Mapping[str, Any]],
    requested: Sequence[str] = (),
) -> list[str]:
    """Resolve requested profile names while preserving adapter declaration order."""
    discovered: list[str] = []
    for result in results:
        scores = result.get("scores")
        if not isinstance(scores, Mapping):
            continue
        for raw_name in cast(Mapping[object, object], scores):
            name = str(raw_name).strip()
            if name and name not in discovered:
                discovered.append(name)

    selected: list[str] = []
    for item in requested:
        for piece in str(item).split(","):
            name = piece.strip()
            if name:
                selected.append(name)

    if not selected or any(name.casefold() == "all" for name in selected):
        return discovered or ["PRI"]

    by_slug = {slug_profile(name): name for name in discovered}
    resolved: list[str] = []
    for name in selected:
        match = by_slug.get(slug_profile(name), name)
        if match not in resolved:
            resolved.append(match)
    return resolved


def profile_score(result: Mapping[str, Any], profile: str) -> float | None:
    """Read one score profile from either the scores map or flattened result fields."""
    scores = result.get("scores")
    if isinstance(scores, Mapping):
        wanted = slug_profile(profile)
        for raw_name, raw_value in cast(Mapping[object, object], scores).items():
            if slug_profile(str(raw_name)) == wanted:
                try:
                    return float(cast(Any, raw_value))
                except (TypeError, ValueError):
                    return None

    key = slug_profile(profile)
    if key == "pri":
        value = result.get("pri")
    else:
        value = result.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _identity_columns(rows: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    available: dict[str, str] = {}
    for row in rows:
        for raw_key, value in row.items():
            if isinstance(value, str) and value.strip():
                key = str(raw_key)
                available.setdefault(key.casefold(), key)

    name_candidates = (
        "player",
        "name",
        "display_name",
        "team_name",
        "team",
        "id",
        "username",
        "handle",
    )
    name_key = next((available[key] for key in name_candidates if key in available), None)

    columns: list[tuple[str, str]] = []
    if name_key is not None:
        columns.append(("Name", name_key))

    for header, candidates in (
        ("Team", ("team", "team_name")),
        ("Conference", ("conference",)),
        ("Division", ("division",)),
    ):
        selected_key = next(
            (available[item] for item in candidates if item in available),
            None,
        )
        if (
            selected_key is not None
            and selected_key != name_key
            and all(existing != selected_key for _, existing in columns)
        ):
            columns.append((header, selected_key))
        if len(columns) >= 3:
            break

    if columns:
        return columns

    for row in rows:
        for raw_key, value in row.items():
            if isinstance(value, (str, int, float)) and value not in ("", None):
                key = str(raw_key)
                columns.append((key, key))
                if len(columns) >= 3:
                    return columns
    return [("Row", "__row__")]


def _build_table(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[tuple[str, str]],
    *,
    title: str | None = None,
    limit: int = 0,
) -> Table:
    """Build one rounded Rich table without choosing an output destination."""
    view = list(rows[: limit or len(rows)])
    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        pad_edge=True,
        collapse_padding=False,
        expand=False,
    )
    for header, key in columns:
        numeric = key in {"__rank__", "pri", "pri_raw", "percentile", "score"}
        table.add_column(header, justify="right" if numeric else "left", no_wrap=numeric)

    for index, row in enumerate(view, 1):
        cells: list[str] = []
        for _header, key in columns:
            if key == "__rank__":
                value: Any = index
            elif key == "__row__":
                value = index
            else:
                value = row.get(key, "")
            if key == "pri_raw":
                try:
                    rendered = f"{float(value):.4f}"
                except (TypeError, ValueError):
                    rendered = None
                if rendered is not None:
                    cells.append(rendered)
                    continue
            if key == "percentile":
                try:
                    rendered = f"{float(value):.1f}"
                except (TypeError, ValueError):
                    rendered = None
                if rendered is not None:
                    cells.append(rendered)
                    continue
            if key == "score":
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    numeric_value = None
                if numeric_value is not None:
                    cells.append(
                        str(int(numeric_value))
                        if numeric_value.is_integer()
                        else f"{numeric_value:.3f}"
                    )
                    continue
            cells.append("" if value is None else str(value))
        table.add_row(*cells)
    return table


def _ranked_profile_rows(
    raw_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    profile: str,
    *,
    ascending: bool,
) -> list[dict[str, Any]]:
    """Return rows independently ranked for one score profile."""
    ranked: list[dict[str, Any]] = []
    for index, (raw, result) in enumerate(zip(raw_rows, results, strict=False)):
        score = profile_score(result, profile)
        if score is None:
            continue
        item = {str(key): value for key, value in raw.items()}
        item["score"] = score
        item["_index"] = index
        try:
            item["_primary_raw"] = float(result.get("pri_raw", 0.0))
        except (TypeError, ValueError):
            item["_primary_raw"] = 0.0
        ranked.append(item)

    ranked.sort(
        key=lambda item: (
            float(item.get("score", 0.0)),
            float(item.get("_primary_raw", 0.0)),
            -int(item.get("_index", 0)),
        ),
        reverse=not ascending,
    )
    for item in ranked:
        item.pop("_index", None)
        item.pop("_primary_raw", None)
    return ranked


def profile_tables(
    raw_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    profiles: Sequence[str] = (),
    limit: int = 0,
    ascending: bool = False,
) -> list[Table]:
    """Build one independently sorted Rich table per score profile."""
    identity = _identity_columns(raw_rows)
    tables: list[Table] = []
    for profile in profile_names(results, profiles):
        ranked = _ranked_profile_rows(
            raw_rows,
            results,
            profile,
            ascending=ascending,
        )
        columns = [("Rank", "__rank__"), *identity, (profile, "score")]
        tables.append(
            _build_table(
                ranked,
                columns,
                title=f"{profile} ranking",
                limit=limit,
            )
        )
    return tables


def save_profile_report(
    raw_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    output: str | Path,
    *,
    profiles: Sequence[str] = (),
    limit: int = 0,
    ascending: bool = False,
    width: int = 140,
) -> Path:
    """Write independently ranked Rich tables as HTML, SVG, ANSI, or text."""
    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    suffix = destination.suffix.casefold()
    supported = {".html", ".htm", ".svg", ".ansi", ".txt"}

    if suffix not in supported:
        raise ValueError("Rich report output must end in .html, .htm, .svg, .ansi, or .txt")

    tables = profile_tables(
        raw_rows,
        results,
        profiles=profiles,
        limit=limit,
        ascending=ascending,
    )

    # Treat width as the maximum permitted width, not the forced output width.
    maximum_width = max(20, width)

    measuring_console = Console(
        width=maximum_width,
        force_terminal=True,
        color_system="truecolor",
        soft_wrap=False,
    )

    if tables:
        measurement = measure_renderables(
            measuring_console,
            measuring_console.options,
            tables,
        )
        fitted_width = max(
            1,
            min(measurement.maximum, maximum_width),
        )
    else:
        fitted_width = min(20, maximum_width)

    stream = io.StringIO()
    console = Console(
        file=stream,
        record=True,
        force_terminal=True,
        color_system="truecolor",
        width=fitted_width,
        soft_wrap=False,
    )

    if not tables:
        console.print("No rows scored.", end="")
    else:
        for index, table in enumerate(tables):
            if index:
                console.print()
            console.print(table, end="")

    if suffix in {".html", ".htm"}:
        console.save_html(
            str(destination),
            theme=MONOKAI,
            clear=False,
            inline_styles=True,
        )

    elif suffix == ".svg":
        chromeless_svg_format = """\
<svg
    class="rich-table"
    viewBox="0 0 {terminal_width} {terminal_height}"
    width="{terminal_width}"
    height="{terminal_height}"
    xmlns="http://www.w3.org/2000/svg"
    preserveAspectRatio="xMinYMin meet"
>
    <style>
        @font-face {{
            font-family: "Fira Code";
            src: local("FiraCode-Regular");
            font-style: normal;
            font-weight: 400;
        }}

        @font-face {{
            font-family: "Fira Code";
            src: local("FiraCode-Bold");
            font-style: normal;
            font-weight: 700;
        }}

        .{unique_id}-matrix {{
            font-family: "Fira Code", Consolas, monospace;
            font-size: {char_height}px;
            line-height: {line_height}px;
            font-variant-east-asian: full-width;
        }}

        {styles}
    </style>

    <defs>
        {lines}
    </defs>

    <rect
        x="0"
        y="0"
        width="{terminal_width}"
        height="{terminal_height}"
        fill="#0c0c0c"
    />

    {backgrounds}

    <g class="{unique_id}-matrix">
        {matrix}
    </g>
</svg>
"""

        console.save_svg(
            str(destination),
            title="",
            theme=MONOKAI,
            clear=False,
            code_format=chromeless_svg_format,
        )

    else:
        console.save_text(
            str(destination),
            styles=suffix == ".ansi",
            clear=False,
        )

    return destination


def render_table_text(
    rows: Sequence[Mapping[str, Any]],
    columns: Sequence[tuple[str, str]],
    *,
    title: str | None = None,
    limit: int = 0,
    width: int = 120,
) -> str:
    """Render a rounded Rich table as plain terminal text."""
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=False,
        color_system=None,
        width=max(60, width),
        soft_wrap=False,
    )
    console.print(_build_table(rows, columns, title=title, limit=limit))
    return stream.getvalue()


def render_profile_tables(
    raw_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    profiles: Sequence[str] = (),
    limit: int = 0,
    ascending: bool = False,
    width: int = 120,
) -> str:
    """Render one independently ranked table per score profile."""
    blocks: list[str] = []
    for table in profile_tables(
        raw_rows,
        results,
        profiles=profiles,
        limit=limit,
        ascending=ascending,
    ):
        stream = io.StringIO()
        console = Console(
            file=stream,
            force_terminal=False,
            color_system=None,
            width=max(60, width),
            soft_wrap=False,
        )
        console.print(table)
        blocks.append(stream.getvalue().rstrip())

    if not blocks:
        return "No rows scored.\n"
    return "\n\n".join(blocks) + "\n"


def render_timing(times: StageTimes, *, width: int = 72) -> str:
    """Render collected stage timing as a rounded table."""
    total = sum(milliseconds for _, milliseconds in times.items)
    rows: list[dict[str, Any]] = []
    for name, milliseconds in times.items:
        rows.append(
            {
                "stage": name,
                "milliseconds": f"{milliseconds:.2f}",
                "share": f"{(milliseconds / total * 100.0) if total else 0.0:.1f}%",
            }
        )
    rows.append({"stage": "TOTAL", "milliseconds": f"{total:.2f}", "share": "100.0%"})
    return render_table_text(
        rows,
        (("Stage", "stage"), ("Time (ms)", "milliseconds"), ("Share", "share")),
        title="Timing",
        width=width,
    )


TABLE_SVG_FORMAT = """\
<svg
    class="rich-table"
    viewBox="0 0 {terminal_width} {terminal_height}"
    width="{terminal_width}"
    height="{terminal_height}"
    xmlns="http://www.w3.org/2000/svg"
>
    <style>
        @font-face {{
            font-family: "Fira Code";
            src:
                local("FiraCode-Regular"),
                url("https://cdnjs.cloudflare.com/ajax/libs/firacode/6.2.0/woff2/FiraCode-Regular.woff2")
                    format("woff2");
            font-style: normal;
            font-weight: 400;
        }}

        @font-face {{
            font-family: "Fira Code";
            src:
                local("FiraCode-Bold"),
                url("https://cdnjs.cloudflare.com/ajax/libs/firacode/6.2.0/woff2/FiraCode-Bold.woff2")
                    format("woff2");
            font-style: normal;
            font-weight: 700;
        }}

        .{unique_id}-matrix {{
            font-family: "Fira Code", monospace;
            font-size: {char_height}px;
            line-height: {line_height}px;
            font-variant-east-asian: full-width;
        }}

        {styles}
    </style>

    <defs>
        {lines}
    </defs>

    <rect
        x="0"
        y="0"
        width="{terminal_width}"
        height="{terminal_height}"
        fill="#0c0c0c"
    />

    {backgrounds}

    <g class="{unique_id}-matrix">
        {matrix}
    </g>
</svg>
"""


def save_table_svg(
    renderables: Sequence[RenderableType],
    destination: Path,
    *,
    max_width: int = 240,
) -> Path:
    """Render Rich objects to a tightly fitted, chrome-free SVG."""
    output = destination.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if not renderables:
        raise ValueError("At least one Rich renderable is required.")

    # Give Rich plenty of space while determining the natural width.
    measuring_console = Console(
        width=max_width,
        force_terminal=True,
        color_system="truecolor",
    )

    measurement = measure_renderables(
        measuring_console,
        measuring_console.options,
        renderables,
    )

    fitted_width = max(1, min(measurement.maximum, max_width))

    export_console = Console(
        record=True,
        width=fitted_width,
        force_terminal=True,
        color_system="truecolor",
    )

    for index, renderable in enumerate(renderables):
        if index:
            export_console.print()
        export_console.print(renderable, end="")

    export_console.save_svg(
        str(output),
        title="",
        theme=MONOKAI,
        clear=True,
        code_format=TABLE_SVG_FORMAT,
    )

    return output


__all__ = [
    "profile_names",
    "profile_score",
    "profile_tables",
    "render_profile_tables",
    "render_table_text",
    "render_timing",
    "save_profile_report",
    "slug_profile",
]
