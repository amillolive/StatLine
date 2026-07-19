"""Dataset discovery and path resolution functions."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

PathLike = Union[str, Path]
_DATASET_ROOT = Path(__file__).resolve().parent / "data" / "stats"


def dataset_root() -> Path:
    return _DATASET_ROOT


def list_datasets(*, root: Optional[PathLike] = None) -> List[str]:
    base = Path(root) if root is not None else _DATASET_ROOT
    if not base.exists():
        return []
    return sorted(
        path.relative_to(base).as_posix() for path in base.rglob("*.csv") if path.is_file()
    )


def _candidate_dataset_paths(name: str, *, root: Optional[PathLike] = None) -> List[Path]:
    base = Path(root) if root is not None else _DATASET_ROOT
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("dataset name/path is required")
    explicit = Path(raw).expanduser()
    if explicit.exists():
        return [explicit]
    relative = Path(raw)
    candidates = [base / relative]
    if relative.suffix.lower() != ".csv":
        candidates.append(base / f"{raw}.csv")
    normalized = raw.replace("\\", "/").lower().removesuffix(".csv")
    for item in base.rglob("*.csv"):
        relative_name = item.relative_to(base).as_posix()
        if (
            relative_name.lower().removesuffix(".csv") == normalized
            or item.stem.lower() == normalized
        ):
            candidates.append(item)
    seen: set[Path] = set()
    matches: List[Path] = []
    for candidate in candidates:
        identity = candidate.resolve() if candidate.exists() else candidate
        if identity not in seen and candidate.is_file():
            seen.add(identity)
            matches.append(candidate)
    return matches


def resolve_dataset(name: str, *, root: Optional[PathLike] = None) -> Path:
    matches = _candidate_dataset_paths(name, root=root)
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
