"""Discover named adapters from the canonical packs tree or installed wheel."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import cast

import yaml

from statline.core.tools.yaml_codec import safe_load

_SEPARATOR_RE = re.compile(r"[._\-\s/\\]+")
_ROOT = Path(__file__).resolve().parents[3]
_CHECKOUT_PACKS = _ROOT / "packs"
_PACKAGED_PACKS = Path(__file__).resolve().parents[2] / "packs"


def normalize_adapter_name(value: object) -> str:
    return _SEPARATOR_RE.sub(".", str(value or "").strip().casefold()).strip(".")


def adapter_schema_root() -> Path:
    return _CHECKOUT_PACKS if _CHECKOUT_PACKS.is_dir() else _PACKAGED_PACKS


def current_adapter_dir() -> Path:
    """Target for locally generated adapters; active by name after creation."""
    return adapter_schema_root() / "custom" / "local" / "default"


def deprecated_adapter_dir() -> Path:
    return adapter_schema_root() / "retired"


def adapter_schema_dirs(*, include_deprecated: bool = False) -> tuple[Path, ...]:
    root = adapter_schema_root()
    roots = (
        tuple(
            p
            for p in sorted(root.iterdir())
            if p.is_dir() and p.name not in {"db", "retired", "workspace", "data"}
        )
        if root.exists()
        else ()
    )
    return (*roots, root / "retired") if include_deprecated else roots


def iter_adapter_paths(*, include_deprecated: bool = False) -> Iterable[Path]:
    """Walk active packs, ignoring workspace, datasets and retired models."""
    seen: set[Path] = set()
    for root in adapter_schema_dirs(include_deprecated=include_deprecated):
        for path in sorted((*root.rglob("*.yaml"), *root.rglob("*.yml"))):
            # StatPack components and authoring workspaces are not runtime adapters.
            # Only explicit paths can load a draft in an organization's workspace.
            relative = path.relative_to(adapter_schema_root())
            forbidden = {"workspace", "data", "schema", "dataset", "inputs", "db"}
            if not include_deprecated:
                forbidden.add("retired")
            if (
                forbidden & set(relative.parts)
                or any(part.startswith(".") for part in relative.parts)
                or path.name == "metadata.yaml"
            ):
                continue
            resolved = path.resolve()
            if path.is_file() and resolved not in seen:
                seen.add(resolved)
                yield path


def _metadata_names(path: Path) -> set[str]:
    names = {normalize_adapter_name(path.stem)}
    try:
        loaded = safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return names
    if not isinstance(loaded, Mapping):
        return names
    metadata = cast(Mapping[object, object], loaded).get("metadata")
    if not isinstance(metadata, Mapping):
        return names
    meta = cast(Mapping[object, object], metadata)
    names.add(normalize_adapter_name(meta.get("id")))
    aliases = meta.get("aliases")
    if isinstance(aliases, str):
        names.add(normalize_adapter_name(aliases))
    elif isinstance(aliases, Sequence):
        names.update(normalize_adapter_name(x) for x in cast(Sequence[object], aliases))
    return {name for name in names if name}


def resolve_adapter_path(name_or_path: str | Path) -> Path:
    candidate = Path(name_or_path).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    raw = str(name_or_path or "").strip()
    if not raw:
        raise ValueError("adapter name/path is required")
    wanted = normalize_adapter_name(raw)
    matches = [path for path in iter_adapter_paths() if wanted in _metadata_names(path)]
    if len(matches) == 1:
        return matches[0].resolve()
    if len(matches) > 1:
        shown = ", ".join(str(path) for path in matches)
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
