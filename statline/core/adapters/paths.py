"""Canonical adapter schema path and discovery functions."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

import yaml

_SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas"
_CURRENT_ROOT = _SCHEMA_ROOT / "current"
_DEPRECATED_ROOT = _SCHEMA_ROOT / "deprecated"
_SEPARATOR_RE = re.compile(r"[._\-\s/\\]+")


def normalize_adapter_name(value: object) -> str:
    """Normalize adapter IDs, file stems, and aliases to one lookup form."""
    raw = str(value or "").strip().casefold()
    return _SEPARATOR_RE.sub(".", raw).strip(".")


def adapter_schema_root() -> Path:
    """Return the root containing current and deprecated adapter schemas."""
    return _SCHEMA_ROOT


def current_adapter_dir() -> Path:
    """Return the canonical generated runtime adapter directory."""
    return _CURRENT_ROOT


def deprecated_adapter_dir() -> Path:
    """Return the compatibility adapter directory."""
    return _DEPRECATED_ROOT


def adapter_schema_dirs(*, include_deprecated: bool = False) -> tuple[Path, ...]:
    """Return discoverable adapter search roots in precedence order.

    Deprecated schemas are intentionally excluded from normal discovery.  They
    remain loadable only by supplying an explicit filesystem path.
    """
    roots = [_CURRENT_ROOT]
    if include_deprecated:
        roots.append(_DEPRECATED_ROOT)
    return tuple(roots)


def iter_adapter_paths(*, include_deprecated: bool = False) -> Iterable[Path]:
    """Yield discoverable packaged adapter YAML files in deterministic order."""
    seen: set[Path] = set()
    for root in adapter_schema_dirs(include_deprecated=include_deprecated):
        if not root.exists():
            continue
        for path in sorted((*root.glob("*.yaml"), *root.glob("*.yml"))):
            resolved = path.resolve()
            if resolved not in seen and path.is_file():
                seen.add(resolved)
                yield path


def _metadata_names(path: Path) -> set[str]:
    names = {normalize_adapter_name(path.stem)}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return names
    if not isinstance(loaded, Mapping):
        return names
    root = cast(Mapping[object, object], loaded)
    metadata = root.get("metadata")
    if not isinstance(metadata, Mapping):
        return names
    meta = cast(Mapping[object, object], metadata)
    names.add(normalize_adapter_name(meta.get("id")))
    aliases = meta.get("aliases")
    if isinstance(aliases, str):
        names.add(normalize_adapter_name(aliases))
    elif isinstance(aliases, Sequence):
        names.update(normalize_adapter_name(alias) for alias in cast(Sequence[object], aliases))
    return {name for name in names if name}


def resolve_adapter_path(name_or_path: str | Path) -> Path:
    """Resolve an explicit YAML path, adapter ID, file stem, or declared alias."""
    candidate = Path(name_or_path).expanduser()
    if candidate.is_file():
        return candidate.resolve()

    raw = str(name_or_path or "").strip()
    if not raw:
        raise ValueError("adapter name/path is required")
    wanted = normalize_adapter_name(raw)

    direct_names = {raw, f"{raw}.yaml", f"{raw}.yml"}
    for root in adapter_schema_dirs():
        for direct in direct_names:
            path = root / direct
            if path.is_file():
                return path.resolve()

    matches = [path for path in iter_adapter_paths() if wanted in _metadata_names(path)]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        shown = ", ".join(path.name for path in matches)
        raise ValueError(f"Ambiguous adapter '{raw}'. Matches: {shown}")
    raise FileNotFoundError(f"Adapter spec not found: {raw}")


__all__ = [
    "adapter_schema_dirs",
    "adapter_schema_root",
    "current_adapter_dir",
    "deprecated_adapter_dir",
    "iter_adapter_paths",
    "normalize_adapter_name",
    "resolve_adapter_path",
]
