# slapi/storage/csv.py
from __future__ import annotations

import csv
from contextlib import contextmanager
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    TextIO,
    Tuple,
    Union,
)

# Public aliases
Row = Dict[str, Any]
Rows = List[Row]

_PathLike = Union[str, Path]
DialectLike = Union[str, csv.Dialect]  # name or instance (never a class)

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_path(p: _PathLike) -> Path:
    return p if isinstance(p, Path) else Path(p)

def _maybe_normalize_header(name: str, *, normalize_headers: bool) -> str:
    if not normalize_headers:
        return name
    s = name.strip().lower()
    out: List[str] = []
    prev_us = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        else:
            if not prev_us:
                out.append("_")
            prev_us = True
    return "".join(out).strip("_")

def _coerce_cell(x: str, *, coerce_numbers: bool, strip_cells: bool) -> Any:
    if strip_cells:
        x = x.strip()
    if not coerce_numbers:
        return x
    if x and (x.isdigit() or (x.startswith("-") and x[1:].isdigit())):
        try:
            return int(x)
        except Exception:
            pass
    try:
        if x and any(c.isdigit() for c in x):
            return float(x)
    except Exception:
        pass
    return x

# ──────────────────────────────────────────────────────────────────────────────
# Dialect sniffing (always returns name or instance)
# ──────────────────────────────────────────────────────────────────────────────

def sniff_dialect_name_or_instance(
    sample: Union[bytes, str],
    *,
    delimiters: Sequence[str] = (",", ";", "\t", "|"),
) -> DialectLike:
    """
    Return a csv.Dialect *instance* if Sniffer succeeds; otherwise return the
    registered name 'excel'. Never returns a Dialect class.
    """
    sniffer = csv.Sniffer()
    text = sample.decode("utf-8", "ignore") if isinstance(sample, bytes) else sample
    try:
        dial = sniffer.sniff(text, delimiters="".join(delimiters))  # instance in practice
        return dial if isinstance(dial, csv.Dialect) else "excel"
    except Exception:
        return "excel"

# ──────────────────────────────────────────────────────────────────────────────
# File opening
# ──────────────────────────────────────────────────────────────────────────────

@contextmanager
def _open_text(
    path: _PathLike,
    *,
    encoding: str = "utf-8",
) -> Iterator[TextIO]:
    """Open text file for CSV with BOM handling and newline discipline."""
    p = _ensure_path(path)
    with p.open("rb") as rb:
        head = rb.read(64 * 1024)
    enc = "utf-8-sig" if head.startswith(b"\xef\xbb\xbf") else encoding
    f = p.open("r", encoding=enc, newline="")
    try:
        yield f
    finally:
        f.close()

def _iter_from_file(
    f: TextIO,
    *,
    has_header: Optional[bool],
    normalize_headers: bool,
    coerce_numbers: bool,
    strip_cells: bool,
    dialect: Optional[DialectLike],
    delimiters: Sequence[str],
) -> Iterator[Row]:
    """Core iterator that operates on an already-open TextIO handle."""
    # Read a sample regardless (used by has_header and sniff)
    pos: int = f.tell()
    sample: str = f.read(64 * 1024)
    f.seek(pos)

    # Decide dialect for csv.reader (never Optional)
    dialect_used: DialectLike = (
        sniff_dialect_name_or_instance(sample, delimiters=delimiters)
        if dialect is None
        else dialect
    )

    sniffer = csv.Sniffer()
    header_present = has_header
    if header_present is None:
        try:
            header_present = sniffer.has_header(sample)
        except Exception:
            header_present = True

    reader = csv.reader(f, dialect=dialect_used)
    header: List[str]

    # Read header or first row
    try:
        first = next(reader)
    except StopIteration:
        return  # empty file

    if header_present:
        header = [
            _maybe_normalize_header(h, normalize_headers=normalize_headers) for h in first
        ]
    else:
        header = [f"col_{i+1}" for i in range(len(first))]
        row_vals = [
            _coerce_cell(v, coerce_numbers=coerce_numbers, strip_cells=strip_cells) for v in first
        ]
        yield dict(zip(header, row_vals))

    # Stream the rest
    for row in reader:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        elif len(row) > len(header):
            row = row[:len(header)]
        vals = [
            _coerce_cell(v, coerce_numbers=coerce_numbers, strip_cells=strip_cells) for v in row
        ]
        yield dict(zip(header, vals))

# ──────────────────────────────────────────────────────────────────────────────
# Public readers
# ──────────────────────────────────────────────────────────────────────────────

def iter_csv_rows(
    src: Union[_PathLike, TextIO],
    *,
    has_header: Optional[bool] = None,
    normalize_headers: bool = True,
    coerce_numbers: bool = True,
    strip_cells: bool = True,
    dialect: Optional[DialectLike] = None,
    delimiters: Sequence[str] = (",", ";", "\t", "|"),
) -> Iterator[Row]:
    """
    Stream rows from a CSV file path or a TextIO object.
    """
    if isinstance(src, (str, Path)):
        with _open_text(src) as f:
            yield from _iter_from_file(
                f,
                has_header=has_header,
                normalize_headers=normalize_headers,
                coerce_numbers=coerce_numbers,
                strip_cells=strip_cells,
                dialect=dialect,
                delimiters=delimiters,
            )
    else:
        # Assume caller passed a correctly-opened text handle (newline="")
        yield from _iter_from_file(
            src,
            has_header=has_header,
            normalize_headers=normalize_headers,
            coerce_numbers=coerce_numbers,
            strip_cells=strip_cells,
            dialect=dialect,
            delimiters=delimiters,
        )

def read_csv_rows(
    path: _PathLike,
    **kwargs: Any,
) -> Rows:
    """Read entire CSV into memory as List[Dict[str, Any]]."""
    return list(iter_csv_rows(path, **kwargs))

# ──────────────────────────────────────────────────────────────────────────────
# Writers
# ──────────────────────────────────────────────────────────────────────────────

def write_csv_rows(
    path: _PathLike,
    rows: Iterable[Mapping[str, Any]],
    *,
    fieldnames: Optional[Sequence[str]] = None,
    include_header: bool = True,
    encoding: str = "utf-8",
    newline: str = "",
) -> Tuple[int, List[str]]:
    """
    Write rows (mapping-like) to CSV.
    Returns (row_count, fieldnames_used).
    """
    p = _ensure_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    it = iter(rows)
    try:
        first: Mapping[str, Any] = next(it)
    except StopIteration:
        if fieldnames is None:
            fieldnames = []
        with p.open("w", encoding=encoding, newline=newline) as f:
            writer = csv.DictWriter(f, fieldnames=list(fieldnames))
            if include_header and fieldnames:
                writer.writeheader()
        return 0, list(fieldnames)

    if fieldnames is None:
        fieldnames = list(first.keys())

    count = 0
    with p.open("w", encoding=encoding, newline=newline) as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
        if include_header and fieldnames:
            writer.writeheader()

        def _coerce_out(v: Any) -> Any:
            return "" if v is None else v

        writer.writerow({k: _coerce_out(first.get(k)) for k in fieldnames})
        count += 1
        for r in it:
            writer.writerow({k: _coerce_out(r.get(k)) for k in fieldnames})
            count += 1
    return count, list(fieldnames)

# ──────────────────────────────────────────────────────────────────────────────
# Preview helpers
# ──────────────────────────────────────────────────────────────────────────────

def peek_headers(
    path: _PathLike,
    *,
    normalize_headers: bool = True,
    delimiters: Sequence[str] = (",", ";", "\t", "|"),
    encoding: str = "utf-8",
) -> List[str]:
    """Read just the header row and return normalized names."""
    p = _ensure_path(path)
    with p.open("rb") as rb:
        head = rb.read(64 * 1024)
    enc = "utf-8-sig" if head.startswith(b"\xef\xbb\xbf") else encoding
    sample = head.decode(enc, "ignore")
    dialect_used = sniff_dialect_name_or_instance(sample, delimiters=delimiters)
    with p.open("r", encoding=enc, newline="") as f:
        reader = csv.reader(f, dialect=dialect_used)
        for row in reader:
            if not row:
                continue
            return [_maybe_normalize_header(h, normalize_headers=normalize_headers) for h in row]
    return []
