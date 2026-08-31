from __future__ import annotations

import csv
import io
from pathlib import Path

from statline.gateway.storage.csv import (
    iter_csv_rows,
    peek_headers,
    read_csv_rows,
    sniff_dialect_name_or_instance,
    write_csv_rows,
)


def test_sniff_dialect_success_and_fallback() -> None:
    dialect = sniff_dialect_name_or_instance("a;b\n1;2\n")
    if isinstance(dialect, csv.Dialect):
        assert dialect.delimiter == ";"
    else:
        assert dialect == "excel"

    assert sniff_dialect_name_or_instance("") == "excel"


def test_iter_csv_normalizes_headers_and_coerces_values() -> None:
    source = io.StringIO(" Player Name ,Games-Played,Ratio,Note\n Alice ,12,1.5, hi \n")
    rows = list(iter_csv_rows(source, has_header=True))
    assert rows == [{"player_name": "Alice", "games_played": 12, "ratio": 1.5, "note": "hi"}]


def test_iter_csv_can_preserve_strings_and_headers() -> None:
    source = io.StringIO("Player Name,GP\n Alice ,012\n")
    rows = list(
        iter_csv_rows(
            source,
            has_header=True,
            normalize_headers=False,
            coerce_numbers=False,
            strip_cells=False,
            dialect="excel",
        )
    )
    assert rows == [{"Player Name": " Alice ", "GP": "012"}]


def test_iter_csv_without_header_pads_and_truncates_rows() -> None:
    source = io.StringIO("a,b\n1\n2,3,4\n")
    rows = list(iter_csv_rows(source, has_header=False, coerce_numbers=False, dialect="excel"))
    assert rows == [
        {"col_1": "a", "col_2": "b"},
        {"col_1": "1", "col_2": ""},
        {"col_1": "2", "col_2": "3"},
    ]


def test_empty_csv_returns_no_rows() -> None:
    assert list(iter_csv_rows(io.StringIO(""), has_header=True, dialect="excel")) == []


def test_read_and_write_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "players.csv"
    count, fields = write_csv_rows(
        path,
        [
            {"name": "Ada", "score": 99, "optional": None, "ignored": "x"},
            {"name": "Grace", "score": 95, "optional": "yes", "ignored": "y"},
        ],
        fieldnames=["name", "score", "optional"],
    )

    assert count == 2
    assert fields == ["name", "score", "optional"]
    assert read_csv_rows(path, has_header=True, dialect="excel") == [
        {"name": "Ada", "score": 99, "optional": ""},
        {"name": "Grace", "score": 95, "optional": "yes"},
    ]


def test_write_empty_rows_with_and_without_fields(tmp_path: Path) -> None:
    with_fields = tmp_path / "with.csv"
    count, fields = write_csv_rows(with_fields, [], fieldnames=["name", "score"])
    assert (count, fields) == (0, ["name", "score"])
    assert with_fields.read_text(encoding="utf-8") == "name,score\n"

    no_fields = tmp_path / "empty.csv"
    count2, fields2 = write_csv_rows(no_fields, [])
    assert (count2, fields2) == (0, [])
    assert no_fields.read_text(encoding="utf-8") == ""


def test_write_infers_fields_from_first_row(tmp_path: Path) -> None:
    path = tmp_path / "auto.csv"
    count, fields = write_csv_rows(path, [{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    assert count == 2
    assert fields == ["a", "b"]
    assert read_csv_rows(path, has_header=True, dialect="excel") == [
        {"a": 1, "b": 2},
        {"a": 3, "b": 4},
    ]


def test_bom_is_handled_and_headers_can_be_peeked(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbfPlayer Name,GP\nAda,4\n")

    assert peek_headers(path) == ["player_name", "gp"]
    assert peek_headers(path, normalize_headers=False) == ["Player Name", "GP"]
    assert read_csv_rows(path, has_header=True, dialect="excel")[0]["player_name"] == "Ada"


def test_peek_headers_skips_blank_rows_and_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "blank-first.csv"
    path.write_text("\nName,Score\nAda,10\n", encoding="utf-8")
    assert peek_headers(path) == ["name", "score"]

    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert peek_headers(empty) == []
