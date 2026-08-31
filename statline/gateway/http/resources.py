"""JSON-safe adapter and dataset resource builders for the v4 HTTP layer."""

from __future__ import annotations

import ast
import csv
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from statline.core.adapters import (
    adapter_cache_info,
    list_adapters,
    load_adapter,
    load_adapter_spec,
    supported_adapters,
)
from statline.core.adapters.paths import normalize_adapter_name
from statline.core.datasets import dataset_root, list_datasets, load_dataset, resolve_dataset


def _sorted_unique(values: Iterable[object]) -> list[str]:
    return list(dict.fromkeys(text for value in values if (text := str(value).strip())))


def _discoverable_adapter_name(adapter_name: str) -> str:
    """Resolve only adapters intentionally exposed through discovery/API surfaces."""
    normalized = normalize_adapter_name(adapter_name)
    exposed = supported_adapters()
    try:
        return exposed[normalized]
    except KeyError as error:
        raise KeyError(f"Adapter is not discoverable: {adapter_name}") from error


def _expr_identifiers(expr: object) -> list[str]:
    try:
        tree = ast.parse(str(expr), mode="eval")
    except (SyntaxError, TypeError, ValueError):
        return []

    called: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return sorted(names.difference(called))


def _adapter_inputs(adapter_name: str) -> list[str]:
    spec = load_adapter_spec(_discoverable_adapter_name(adapter_name))
    keys: list[str] = []
    for metric in spec.metrics:
        source = metric.source
        if source is None:
            continue
        if source.field:
            keys.append(source.field)
        elif source.expr:
            keys.extend(_expr_identifiers(source.expr))
    keys.extend(spec.dimensions)
    keys.extend(filter_spec.field for filter_spec in spec.filters.values())
    return _sorted_unique(keys)


def adapter_summary(adapter_name: str) -> dict[str, Any]:
    adapter = load_adapter(_discoverable_adapter_name(adapter_name))
    return {
        "key": adapter.key,
        "title": adapter.title,
        "version": adapter.version,
        "aliases": list(adapter.aliases),
        "dataset": adapter.metadata.dataset,
    }


def adapter_catalog() -> dict[str, Any]:
    return {
        "adapters": [adapter_summary(name) for name in list_adapters()],
        "cache": adapter_cache_info(),
    }


def adapter_document(adapter_name: str) -> dict[str, Any]:
    canonical = _discoverable_adapter_name(adapter_name)
    spec = load_adapter_spec(canonical)
    metadata = spec.metadata
    return {
        "key": metadata.id,
        "title": metadata.title,
        "version": metadata.version,
        "author": metadata.author,
        "aliases": list(metadata.aliases),
        "dataset": metadata.dataset,
        "inputs": _adapter_inputs(canonical),
        "metrics": [metric.key for metric in spec.metrics]
        + [efficiency.key for efficiency in spec.efficiency],
        "buckets": {
            key: {
                "title": bucket.title,
                "description": bucket.description,
                "tags": list(bucket.tags),
                "hidden": bucket.hidden,
                "meta": dict(bucket.meta),
            }
            for key, bucket in spec.buckets.items()
        },
        "filters": {
            key: {
                "type": filter_spec.type,
                "field": filter_spec.field,
                "accepts": list(filter_spec.accepts),
                "modes": list(filter_spec.modes),
                "description": filter_spec.description,
                "meta": dict(filter_spec.meta),
            }
            for key, filter_spec in spec.filters.items()
        },
        "dimensions": {
            key: {
                "values": list(dimension.values),
                "description": dimension.description,
                "strict": dimension.strict,
                "meta": dict(dimension.meta),
            }
            for key, dimension in spec.dimensions.items()
        },
        "weights": {
            profile: {bucket: float(value) for bucket, value in values.items()}
            for profile, values in spec.weights.items()
        },
        "penalties": {
            profile: {bucket: float(value) for bucket, value in values.items()}
            for profile, values in spec.penalties.items()
        },
        "score_profiles": {key: asdict(profile) for key, profile in spec.score_profiles.items()},
    }


def _canonical_dataset_name(path: Path) -> str:
    return path.resolve().relative_to(dataset_root().resolve()).as_posix()


def dataset_catalog() -> dict[str, Any]:
    linked: dict[str, list[str]] = {}
    for adapter_name in list_adapters():
        adapter = load_adapter(adapter_name)
        dataset = adapter.metadata.dataset
        if dataset:
            linked.setdefault(dataset.replace("\\", "/").casefold(), []).append(adapter.key)

    return {
        "datasets": [
            {
                "path": name,
                "adapters": sorted(linked.get(name.casefold(), [])),
            }
            for name in list_datasets()
        ]
    }


def _csv_columns(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.reader(handle), None)
        return [] if row is None else list(row)


def dataset_page(
    dataset_name: str,
    *,
    offset: int,
    limit: int,
    coerce_numbers: bool,
) -> dict[str, Any]:
    path = resolve_dataset(dataset_name, allow_external=False)
    canonical = _canonical_dataset_name(path)
    page = load_dataset(
        canonical,
        offset=offset,
        limit=limit + 1,
        coerce_numbers=coerce_numbers,
        allow_external=False,
    )
    has_more = len(page) > limit
    rows = page[:limit]
    columns = list(rows[0]) if rows else _csv_columns(path)
    return {
        "dataset": canonical,
        "columns": columns,
        "offset": offset,
        "limit": limit,
        "count": len(rows),
        "has_more": has_more,
        "rows": rows,
    }


def dataset_rows(
    dataset_name: str, *, limit: int | None = None
) -> tuple[str, list[dict[str, Any]]]:
    path = resolve_dataset(dataset_name, allow_external=False)
    canonical = _canonical_dataset_name(path)
    rows = load_dataset(canonical, limit=limit, allow_external=False)
    return canonical, rows


__all__ = [
    "adapter_catalog",
    "adapter_document",
    "dataset_catalog",
    "dataset_page",
    "dataset_rows",
]
