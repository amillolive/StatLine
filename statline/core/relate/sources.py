"""Load pairwise inputs from records, CSV, JSON, or a StatPack's dataset.

This loader reads StatPack archives as data only. It never executes runner.py.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import cast

from statline.core.tools.yaml_codec import safe_load


def _csv_rows(text: str) -> list[dict[str, object]]:
    reader = csv.DictReader(io.StringIO(text))
    result: list[dict[str, object]] = []
    for row in reader:
        converted: dict[str, object] = {}
        for k, v in row.items():
            if k is None:
                continue
            s = str(v or "").strip()
            try:
                # Preserve leading-zero identifiers such as "00123".
                converted[k] = (
                    s
                    if len(s) > 1 and s[0] == "0" and s.isdigit()
                    else float(s)
                    if ("." in s or "e" in s.lower())
                    else int(s)
                )
            except ValueError:
                converted[k] = s
        result.append(converted)
    return result


def _json_rows(text: str) -> list[dict[str, object]]:
    payload: object = json.loads(text)
    if not isinstance(payload, list):
        raise ValueError("Pairwise JSON must contain an array of row objects")  # noqa: TRY004
    items = cast(list[object], payload)
    if any(not isinstance(item, Mapping) for item in items):
        raise ValueError("Pairwise JSON must contain an array of row objects")
    return [dict(cast(Mapping[str, object], item)) for item in items]


def load_pairwise_rows(
    source: str | Path, *, dataset: str | None = None
) -> list[dict[str, object]]:
    """Load a source without executing arbitrary code inside a StatPack."""
    path = Path(source).expanduser().resolve()
    if path.suffix.casefold() in (".csv", ".json"):
        text = path.read_text(encoding="utf-8-sig")
        return _csv_rows(text) if path.suffix.casefold() == ".csv" else _json_rows(text)
    if path.suffix.casefold() != ".statpack":
        raise ValueError(f"Unsupported pairwise input {source!s}; use CSV, JSON or .statpack")
    with zipfile.ZipFile(path) as archive:
        if "metadata.yaml" not in archive.namelist():
            raise ValueError("StatPack is missing metadata.yaml")
        metadata_doc: object = safe_load(archive.read("metadata.yaml"))
        if not isinstance(metadata_doc, Mapping):
            raise ValueError("StatPack metadata must be a mapping")  # noqa: TRY004
        metadata_value = cast(Mapping[str, object], metadata_doc).get("metadata")
        if not isinstance(metadata_value, Mapping):
            raise ValueError("StatPack metadata must be a mapping")  # noqa: TRY004
        metadata = cast(Mapping[str, object], metadata_value)
        logical = str(dataset or metadata.get("dataset") or "").replace("\\", "/")
        member_path = PurePosixPath(logical)
        if (
            not logical
            or member_path.is_absolute()
            or ".." in member_path.parts
            or ":" in member_path.parts[0]
        ):
            raise ValueError("Unsafe or missing StatPack dataset reference")
        member = "dataset/" + member_path.as_posix()
        if member not in archive.namelist():
            raise ValueError(f"StatPack is missing {member}")
        suffix = member_path.suffix.casefold()
        if suffix not in (".csv", ".json"):
            raise ValueError("Pairwise StatPack dataset must be CSV or JSON")
        entry = archive.getinfo(member)
        if entry.file_size > 32 * 1024 * 1024:
            raise ValueError("StatPack dataset exceeds 32 MiB limit")
        text = archive.read(member).decode("utf-8-sig")
        return _csv_rows(text) if suffix == ".csv" else _json_rows(text)
