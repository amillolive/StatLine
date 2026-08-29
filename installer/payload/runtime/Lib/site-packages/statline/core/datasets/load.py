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
    offset: int = 0,
    limit: Optional[int] = None,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
    encoding: str = "utf-8-sig",
    allow_external: bool = True,
) -> Iterable[Row]:
    """Stream CSV rows with stable offset/limit pagination."""
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if limit is not None and limit < 0:
        raise ValueError("limit must be >= 0")
    if limit == 0:
        return

    path = resolve_dataset(str(name), root=root, allow_external=allow_external)
    with path.open("r", encoding=encoding, newline="") as handle:
        emitted = 0
        for index, row in enumerate(csv.DictReader(handle)):
            if index < offset:
                continue
            yield {
                str(key): _coerce_cell(
                    str(value), coerce_numbers=coerce_numbers, strip_cells=strip_cells
                )
                for key, value in row.items()
                if key is not None
            }
            emitted += 1
            if limit is not None and emitted >= limit:
                break


def load_dataset(
    name: PathLike,
    *,
    root: Optional[PathLike] = None,
    offset: int = 0,
    limit: Optional[int] = None,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
    allow_external: bool = True,
) -> Rows:
    return list(
        iter_dataset(
            name,
            root=root,
            offset=offset,
            limit=limit,
            coerce_numbers=coerce_numbers,
            strip_cells=strip_cells,
            allow_external=allow_external,
        )
    )


__all__ = ["Row", "Rows", "iter_dataset", "load_dataset"]
