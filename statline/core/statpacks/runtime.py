"""Runnable StatPack SDK functions."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import Any, TextIO, cast

import yaml

from statline.app.cli.presentation import (
    profile_names,
    profile_score,
    render_profile_tables,
    render_table_text,
    render_timing,
    save_profile_report,
)
from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.datasets import load_dataset
from statline.core.scoring.map import score_rows_from_raw
from statline.core.statpacks.package import dump_yaml, load_statpack_tree
from statline.core.types.timing import StageTimes


def _portable_dataset(root: Path, value: object) -> Path:
    raw = str(value or "").strip().replace("\\", "/")
    logical = PurePosixPath(raw)
    if not raw or logical.is_absolute() or ".." in logical.parts or ":" in logical.parts[0]:
        raise ValueError("StatPack dataset must be a portable relative path")
    dataset_root = (root / "dataset").resolve()
    path = (dataset_root / Path(*logical.parts)).resolve()
    try:
        path.relative_to(dataset_root)
    except ValueError as error:
        raise ValueError("StatPack dataset escapes the package dataset root") from error
    if not path.is_file():
        raise FileNotFoundError(f"StatPack dataset does not exist: {logical.as_posix()}")
    return path


def _parse_scalar(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def _parse_filters(items: Sequence[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"Filter must use key=value: {item!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Filter key cannot be empty: {item!r}")
        output[key] = _parse_scalar(value.strip())
    return output


def _flatten_result(raw: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for raw_key, raw_value in raw.items():
        if isinstance(raw_value, (str, int, float, bool)) and raw_value not in ("", None):
            flattened[str(raw_key)] = raw_value
    scores = result.get("scores")
    if isinstance(scores, Mapping):
        for score_key, score_value in cast(Mapping[object, object], scores).items():
            flattened[str(score_key)] = score_value
    elif "pri" in result:
        flattened["PRI"] = result["pri"]
    if "pri_raw" in result:
        flattened["RAW01"] = result["pri_raw"]
    return flattened


def _wide_table(
    raw_rows: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    *,
    profiles: Sequence[str],
    sort_profile: str | None,
    ascending: bool,
    limit: int,
) -> str:
    selected = profile_names(results, profiles)
    rows = [_flatten_result(raw, result) for raw, result in zip(raw_rows, results, strict=False)]
    sort_name = sort_profile or (selected[0] if selected else "PRI")

    indexed = list(enumerate(zip(rows, results, strict=False)))
    indexed.sort(
        key=lambda item: (
            profile_score(item[1][1], sort_name) or 0.0,
            float(item[1][1].get("pri_raw", 0.0) or 0.0),
            -item[0],
        ),
        reverse=not ascending,
    )
    sorted_rows = [pair[0] for _, pair in indexed]

    string_columns: list[str] = []
    for row in sorted_rows:
        for key, value in row.items():
            if isinstance(value, str) and value.strip() and key not in string_columns:
                string_columns.append(key)
            if len(string_columns) >= 3:
                break
        if len(string_columns) >= 3:
            break

    columns: list[tuple[str, str]] = [("Rank", "__rank__")]
    columns.extend((name, name) for name in string_columns)
    columns.extend((name, name) for name in selected)
    if any("RAW01" in row for row in sorted_rows):
        columns.append(("RAW01", "RAW01"))
    return render_table_text(sorted_rows, columns, title=f"Sorted by {sort_name}", limit=limit)


def _write_machine_output(
    rows: Sequence[Mapping[str, Any]],
    *,
    format_name: str,
    output: TextIO,
    include_headers: bool,
) -> None:
    format_lower = format_name.casefold()
    if format_lower == "json":
        json.dump(list(rows), output, ensure_ascii=False, indent=2)
        output.write("\n")
        return
    if format_lower == "jsonl":
        output.writelines(json.dumps(dict(row), ensure_ascii=False) + "\n" for row in rows)
        return
    if format_lower == "csv":
        fields: list[str] = []
        for row in rows:
            for key in row:
                if str(key) not in fields:
                    fields.append(str(key))
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        if include_headers:
            writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
        return
    raise ValueError("format must be table, json, jsonl, or csv")


def _parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Run a StatLine StatPack")
    parser.add_argument("--dataset", help="Logical dataset path inside the StatPack")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows shown per ranking")
    parser.add_argument(
        "--profile",
        "--profiles",
        action="append",
        default=[],
        help="Score profile to show; repeat, comma-separate, or use 'all' (default: all)",
    )
    parser.add_argument(
        "--weights-profile",
        help="Override the primary score profile's weights preset",
    )
    parser.add_argument("--filter", action="append", default=[], help="Filter key=value")
    parser.add_argument("--format", choices=("table", "json", "jsonl", "csv"), default="table")
    parser.add_argument(
        "--layout",
        choices=("split", "wide"),
        default="split",
        help="Table layout: one ranking per profile or one wide table",
    )
    parser.add_argument("--sort", help="Profile used to sort the wide layout")
    parser.add_argument("--asc", action="store_true", help="Sort rankings in ascending order")
    parser.add_argument("--output", type=Path, help="Write output to a file")
    parser.add_argument("--headers", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--timing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Show per-stage timing on stderr",
    )
    return parser


def run_statpack(
    root: str | Path,
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run an extracted StatPack through the canonical local StatLine SDK."""
    package_root = Path(root).expanduser().resolve()
    out = stdout or sys.stdout
    err = stderr or sys.stderr

    args = _parser(package_root.name).parse_args(list(argv) if argv is not None else None)
    times = StageTimes() if args.timing else None

    try:
        with times.stage("load_statpack") if times else nullcontext():
            data = load_statpack_tree(package_root)

            metadata = data.get("metadata")
            if not isinstance(metadata, Mapping):
                raise TypeError("StatPack metadata must be a mapping")

            dataset_value = args.dataset or cast(Mapping[object, object], metadata).get("dataset")
            if not dataset_value:
                raise ValueError("No dataset selected; set metadata.dataset or pass --dataset")

            dataset_path = _portable_dataset(
                package_root,
                dataset_value,
            )

        with (
            times.stage("compile_adapter") if times else nullcontext(),
            tempfile.TemporaryDirectory(prefix="statline-statpack-sdk-") as temporary,
        ):
            adapter_path = Path(temporary) / "adapter.yaml"
            adapter_path.write_text(
                dump_yaml(data),
                encoding="utf-8",
            )
            adapter = compile_adapter(load_spec(adapter_path))

        with times.stage("load_dataset") if times else nullcontext():
            raw_rows = load_dataset(dataset_path)

        filters = _parse_filters(args.filter)

        results = score_rows_from_raw(
            raw_rows,
            adapter,
            weights=args.weights_profile,
            filters=filters or None,
            timing=times,
        )

        rendered = ""
        machine_rows: list[dict[str, Any]] = []

        with times.stage("render_output") if times else nullcontext():
            if args.format == "table":
                if args.layout == "split":
                    rendered = render_profile_tables(
                        raw_rows,
                        results,
                        profiles=args.profile,
                        limit=args.limit,
                        ascending=args.asc,
                        width=shutil.get_terminal_size((120, 30)).columns,
                    )
                else:
                    rendered = _wide_table(
                        raw_rows,
                        results,
                        profiles=args.profile,
                        sort_profile=args.sort,
                        ascending=args.asc,
                        limit=args.limit,
                    )
            else:
                machine_rows = [
                    _flatten_result(raw, result)
                    for raw, result in zip(
                        raw_rows,
                        results,
                        strict=False,
                    )
                ]

                if args.limit > 0:
                    machine_rows = machine_rows[: args.limit]

        with times.stage("write_output") if times else nullcontext():
            if args.output is not None:
                target = args.output.expanduser().resolve()
                target.parent.mkdir(
                    parents=True,
                    exist_ok=True,
                )

                suffix = target.suffix.casefold()
                rich_report = args.format == "table" and suffix in {
                    ".html",
                    ".htm",
                    ".svg",
                    ".ansi",
                }

                if rich_report:
                    if args.layout != "split":
                        raise ValueError(
                            "Rich report export requires --layout split. "
                            "Use a .txt output for the wide layout."
                        )

                    save_profile_report(
                        raw_rows,
                        results,
                        target,
                        profiles=args.profile,
                        limit=args.limit,
                        ascending=args.asc,
                    )

                else:
                    with target.open(
                        "w",
                        encoding="utf-8",
                        newline="",
                    ) as handle:
                        if args.format == "table":
                            handle.write(rendered)
                        else:
                            _write_machine_output(
                                machine_rows,
                                format_name=args.format,
                                output=handle,
                                include_headers=args.headers,
                            )

            elif args.format == "table":
                out.write(rendered)

            else:
                _write_machine_output(
                    machine_rows,
                    format_name=args.format,
                    output=out,
                    include_headers=args.headers,
                )

        if times is not None:
            err.write("\n")
            err.write(render_timing(times))

        return 0

    except Exception as error:  # noqa: BLE001 - StatPack runner is an execution boundary
        err.write(f"Error: {error}\n")

        if times is not None and times.items:
            err.write("\n")
            err.write(render_timing(times))

        return 1


__all__ = ["run_statpack"]
