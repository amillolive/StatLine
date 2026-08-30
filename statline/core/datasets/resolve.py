"""Dataset discovery and safe path resolution functions."""

from __future__ import annotations

from pathlib import Path

PathLike = str | Path
_DATASET_ROOT = Path(__file__).resolve().parent


def dataset_root() -> Path:
    return _DATASET_ROOT


def _resolved_root(root: PathLike | None) -> Path:
    return (Path(root) if root is not None else _DATASET_ROOT).expanduser().resolve()


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def list_datasets(*, root: PathLike | None = None) -> list[str]:
    base = _resolved_root(root)
    if not base.exists():
        return []
    return sorted(
        path.relative_to(base).as_posix() for path in base.rglob("*.csv") if path.is_file()
    )


def _candidate_dataset_paths(
    name: str,
    *,
    root: PathLike | None = None,
    allow_external: bool = True,
) -> list[Path]:
    base = _resolved_root(root)
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("dataset name/path is required")

    candidates: list[Path] = []
    explicit = Path(raw).expanduser()
    if explicit.is_file():
        resolved_explicit = explicit.resolve()
        if allow_external or _inside_root(resolved_explicit, base):
            candidates.append(resolved_explicit)

    relative = Path(raw.replace("\\", "/"))
    packaged = (base / relative).resolve()
    if _inside_root(packaged, base):
        candidates.append(packaged)
        if relative.suffix.lower() != ".csv":
            candidates.append((base / f"{relative.as_posix()}.csv").resolve())

    normalized = relative.as_posix().casefold().removesuffix(".csv").strip("/")
    for item in base.rglob("*.csv"):
        relative_name = item.relative_to(base).as_posix()
        if (
            relative_name.casefold().removesuffix(".csv") == normalized
            or item.stem.casefold() == normalized
        ):
            candidates.append(item.resolve())

    seen: set[Path] = set()
    matches: list[Path] = []
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        if not allow_external and not _inside_root(candidate, base):
            continue
        seen.add(candidate)
        matches.append(candidate)
    return matches


def resolve_dataset(
    name: str,
    *,
    root: PathLike | None = None,
    allow_external: bool = True,
) -> Path:
    """Resolve a dataset name, with optional packaged-root confinement."""
    matches = _candidate_dataset_paths(name, root=root, allow_external=allow_external)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        shown = ", ".join(str(path) for path in matches[:8])
        raise ValueError(
            f"Ambiguous dataset '{name}'. Matches: {shown}{'…' if len(matches) > 8 else ''}"
        )
    available = list_datasets(root=root)
    shown = ", ".join(available[:12])
    raise FileNotFoundError(
        f"Dataset not found: {name}. Available: {shown}{'…' if len(available) > 12 else ''}"
    )


__all__ = ["PathLike", "dataset_root", "list_datasets", "resolve_dataset"]
