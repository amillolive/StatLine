"""Dataset CSV loading functions."""
from __future__ import annotations

import csv
from typing import Any, Dict, Iterable, List, Optional

from statline.core.datasets.resolve import PathLike, resolve_dataset

Row = Dict[str, Any]
Rows = List[Row]


def _coerce_cell(value: str, *, coerce_numbers: bool, strip_cells: bool) -> Any:
    cell = value.strip() if strip_cells else value
    if not coerce_numbers:
        return cell
    if cell == "":
        return 0.0
    try:
        if cell.isdigit() or (cell.startswith("-") and cell[1:].isdigit()):
            return int(cell)
        if any(character.isdigit() for character in cell):
            return float(cell)
    except Exception:
        pass
    return cell


def iter_dataset(
    name: PathLike,
    *,
    root: Optional[PathLike] = None,
    limit: Optional[int] = None,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
    encoding: str = "utf-8-sig",
) -> Iterable[Row]:
    path = resolve_dataset(str(name), root=root)
    with path.open("r", encoding=encoding, newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle), start=1):
            yield {
                str(key): _coerce_cell(str(value), coerce_numbers=coerce_numbers, strip_cells=strip_cells)
                for key, value in row.items()
                if key is not None
            }
            if limit is not None and index >= limit:
                break


def load_dataset(
    name: PathLike,
    *,
    root: Optional[PathLike] = None,
    limit: Optional[int] = None,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
) -> Rows:
    return list(iter_dataset(name, root=root, limit=limit, coerce_numbers=coerce_numbers, strip_cells=strip_cells))


__all__ = ["Row", "Rows", "iter_dataset", "load_dataset"]
