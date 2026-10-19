"""Bidirectional, non-mutating adapter/dataset compatibility inspection."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from statline.core.adapters.match import dataset_for_adapter, resolve_dataset_path
from statline.core.adapters.registry import list_adapters, load_adapter, load_adapter_spec
from statline.core.datasets import dataset_root


def _headers(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [str(field) for field in (csv.DictReader(handle).fieldnames or [])]


def inspect_dataset(dataset: str | Path) -> dict[str, Any]:
    """Identify declared and sniff-compatible adapters; do not guess a unique match."""
    path = resolve_dataset_path(dataset)
    headers = _headers(path)
    normalized = {header.strip().casefold() for header in headers}
    matches: list[dict[str, Any]] = []
    for name in list_adapters():
        adapter = load_adapter(name)
        spec = load_adapter_spec(name)
        declared = False
        if adapter.metadata.dataset:
            try:
                declared = dataset_for_adapter(adapter) == path
            except (FileNotFoundError, ValueError):
                pass
        sniff = spec.sniff
        required_all = {str(header).strip().casefold() for header in sniff.require_all_headers}
        required_any = {str(header).strip().casefold() for header in sniff.require_any_headers}
        compatible = bool(required_all or required_any) and required_all.issubset(normalized) and (
            not required_any or bool(required_any & normalized)
        )
        if declared or compatible:
            matches.append({"adapter": adapter.key, "declared": declared,
                            "header_compatible": compatible,
                            "missing_headers": sorted(required_all - normalized)})
    declared_count = sum(bool(entry["declared"]) for entry in matches)
    return {
        "dataset": path.relative_to(dataset_root().resolve()).as_posix(),
        "headers": headers,
        "matches": matches,
        "declared_match_count": declared_count,
        "ambiguous": declared_count > 1,
        "note": "Header compatibility is advisory; validate the adapter against representative rows.",
    }


def inspect_adapter(adapter: str) -> dict[str, Any]:
    """Inspect the model, declared dataset and source without reading scoring data."""
    from statline.core.adapters.registry import adapter_source

    compiled = load_adapter(adapter)
    spec = load_adapter_spec(adapter)
    resolved: str | None = None
    error: str | None = None
    if compiled.metadata.dataset:
        try:
            resolved = dataset_for_adapter(compiled).relative_to(dataset_root().resolve()).as_posix()
        except (FileNotFoundError, ValueError) as exc:
            error = str(exc)
    return {
        "adapter": compiled.key,
        "source": str(adapter_source(adapter)),
        "declared_dataset": compiled.metadata.dataset,
        "resolved_dataset": resolved,
        "dataset_error": error,
        "require_all_headers": list(spec.sniff.require_all_headers),
        "require_any_headers": list(spec.sniff.require_any_headers),
    }


__all__ = ["inspect_adapter", "inspect_dataset"]
