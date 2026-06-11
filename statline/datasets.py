"""Dataset discovery and loading helpers for StatLine's bundled/local CSV data.

This module is intentionally small and dependency-light so external consumers can do:

    from statline import load_dataset
    rows = load_dataset("DEMO/demo")

Dataset names resolve flexibly against ``statline/data/stats`` while still allowing
an explicit filesystem path for user-provided CSV files.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

Row = Dict[str, Any]
Rows = List[Row]
PathLike = Union[str, Path]

_DATASET_ROOT = Path(__file__).resolve().parent / "data" / "stats"


def dataset_root() -> Path:
    """Return the package's bundled dataset root directory."""
    return _DATASET_ROOT


def list_datasets(*, root: Optional[PathLike] = None) -> List[str]:
    """Return bundled/local CSV dataset names relative to the dataset root.

    Examples:
        ["DEMO/demo.csv", "EBA_Elevate302/eba_s1_players.csv"]
    """
    base = Path(root) if root is not None else _DATASET_ROOT
    if not base.exists():
        return []
    return sorted(str(p.relative_to(base)) for p in base.rglob("*.csv") if p.is_file())


def _candidate_dataset_paths(name: str, *, root: Optional[PathLike] = None) -> List[Path]:
    base = Path(root) if root is not None else _DATASET_ROOT
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("dataset name/path is required")

    explicit = Path(raw).expanduser()
    if explicit.exists():
        return [explicit]

    candidates: List[Path] = []
    rel = Path(raw)
    candidates.append(base / rel)
    if rel.suffix.lower() != ".csv":
        candidates.append(base / f"{raw}.csv")

    # Case-insensitive convenience lookups by relative path and stem.
    raw_norm = raw.replace("\\", "/").lower().removesuffix(".csv")
    for item in base.rglob("*.csv"):
        rel_name = str(item.relative_to(base)).replace("\\", "/")
        rel_norm = rel_name.lower().removesuffix(".csv")
        if rel_norm == raw_norm or item.stem.lower() == raw_norm:
            candidates.append(item)

    seen: set[Path] = set()
    out: List[Path] = []
    for p in candidates:
        rp = p.resolve() if p.exists() else p
        if rp in seen:
            continue
        seen.add(rp)
        if p.exists() and p.is_file():
            out.append(p)
    return out


def resolve_dataset(name: str, *, root: Optional[PathLike] = None) -> Path:
    """Resolve a dataset name, relative path, stem, or explicit CSV path."""
    matches = _candidate_dataset_paths(name, root=root)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        shown = ", ".join(str(p) for p in matches[:8])
        more = "…" if len(matches) > 8 else ""
        raise ValueError(f"Ambiguous dataset '{name}'. Matches: {shown}{more}")

    available = ", ".join(list_datasets(root=root)[:12])
    more = "…" if len(list_datasets(root=root)) > 12 else ""
    raise FileNotFoundError(f"Dataset not found: {name}. Available: {available}{more}")


def _coerce_cell(value: str, *, coerce_numbers: bool, strip_cells: bool) -> Any:
    x = value.strip() if strip_cells else value
    if not coerce_numbers:
        return x
    if x == "":
        return 0.0
    try:
        if x.isdigit() or (x.startswith("-") and x[1:].isdigit()):
            return int(x)
        if any(ch.isdigit() for ch in x):
            return float(x)
    except Exception:
        return x
    return x


def iter_dataset(
    name: PathLike,
    *,
    root: Optional[PathLike] = None,
    limit: Optional[int] = None,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
    encoding: str = "utf-8-sig",
) -> Iterable[Row]:
    """Stream rows from a bundled dataset name or an explicit CSV path.

    Headers are preserved exactly by default. This matters because adapters such
    as ``eba_players`` intentionally reference uppercase source fields like
    ``PLAYER`` and ``PPG``.
    """
    path = resolve_dataset(str(name), root=root)
    with path.open("r", encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            clean: Row = {
                str(k): _coerce_cell(str(v), coerce_numbers=coerce_numbers, strip_cells=strip_cells)
                for k, v in row.items()
                if k is not None
            }
            yield clean
            count += 1
            if limit is not None and count >= limit:
                break


def load_dataset(
    name: PathLike,
    *,
    root: Optional[PathLike] = None,
    limit: Optional[int] = None,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
) -> Rows:
    """Load a CSV dataset into a list of row dictionaries."""
    return list(
        iter_dataset(
            name,
            root=root,
            limit=limit,
            coerce_numbers=coerce_numbers,
            strip_cells=strip_cells,
        )
    )


__all__ = [
    "Row",
    "Rows",
    "dataset_root",
    "list_datasets",
    "resolve_dataset",
    "iter_dataset",
    "load_dataset",
]
