# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from statline.app.cli.main import (
    _as_str_list,
    _ci_get_local,
    _coerce_filter_keys,
    _collapse_audit_rows,
    _context_label,
    _detect_profiles_from_results,
    _extract_profile_score,
    _format_cell,
    _group_audit,
    _midrank_percentiles,
    _name_for_row,
    _normalize_for_display,
    _normalize_ip,
    _parse_kv_items,
    _passes_display_filters,
    _pretty_detail,
    _profile_header,
    _slug_profile_key,
    _split_csvish,
    _to_float_or_none,
    _try_parse_iso,
)


def _approx(expected: float) -> object:
    """Typed float-only wrapper around pytest.approx for strict Pyright."""
    return pytest.approx(expected)  # pyright: ignore[reportUnknownMemberType]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "-"),
        ("", "-"),
        (" 203.0.113.8 ", "203.0.113.8"),
        ("203.0.113.8:443", "203.0.113.8"),
        ("203.0.113.8, 10.0.0.1", "203.0.113.8"),
        ("2001:db8::1", "2001:db8::1"),
    ],
)
def test_normalize_ip(value: object, expected: str) -> None:
    assert _normalize_ip(value) == expected


def test_case_insensitive_lookup() -> None:
    row = {"GP": 12, "Team": "Terror"}
    assert _ci_get_local(row, "gp") == 12
    assert _ci_get_local(row, "TEAM") == "Terror"
    assert _ci_get_local(row, "missing") is None


def test_collapse_audit_handshake_and_duplicates() -> None:
    rows = [
        {
            "ts": "1",
            "subject": "user",
            "ip": "127.0.0.1",
            "event": "auth.device.ok",
            "device_id": "dev-1",
            "ok": 1,
        },
        {
            "ts": "1",
            "subject": "user",
            "ip": "127.0.0.1",
            "event": "auth.api.ok",
            "api_prefix": "api_1234",
            "ok": 1,
        },
        {"ts": "2", "subject": "user", "ip": "127.0.0.1", "event": "score", "ok": 1},
        {"ts": "2", "subject": "user", "ip": "127.0.0.1", "event": "score", "ok": 1},
    ]

    collapsed = _collapse_audit_rows(rows)

    assert len(collapsed) == 2
    assert collapsed[0]["event"] == "auth.ok"
    assert collapsed[0]["device_id"] == "dev-1"
    assert collapsed[0]["api_prefix"] == "api_1234"
    assert collapsed[1]["event"] == "score"


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ({"detail": " bad request "}, "bad request"),
        ({"detail": {"reason": "nope"}}, '{"reason": "nope"}'),
        (["one", "two"], "one; two"),
        (
            {"detail": [{"loc": ["body", "rows"], "msg": "required", "type": "missing"}]},
            "body.rows / required / missing",
        ),
        (123, "123"),
    ],
)
def test_pretty_detail(detail: object, expected: str) -> None:
    assert _pretty_detail(detail) == expected


def test_iso_parser_handles_z_naive_and_invalid() -> None:
    zulu = _try_parse_iso("2026-08-31T18:00:00Z")
    assert zulu == datetime(2026, 8, 31, 18, 0, tzinfo=timezone.utc)

    naive = _try_parse_iso("2026-08-31T18:00:00")
    assert naive is not None
    assert naive.tzinfo == timezone.utc

    assert _try_parse_iso("") is None
    assert _try_parse_iso("not-a-date") is None


def test_normalize_for_display_recurses_and_normalizes_ip() -> None:
    value = {
        "ip": "203.0.113.9:8000",
        "nested": [{"ip": "198.51.100.4, 10.0.0.1"}],
        "plain": "value",
    }
    normalized = _normalize_for_display(value)
    assert normalized["ip"] == "203.0.113.9"
    assert normalized["nested"][0]["ip"] == "198.51.100.4"
    assert normalized["plain"] == "value"


def test_group_audit_uses_fallbacks() -> None:
    grouped = _group_audit(
        [
            {"org": "A", "subject": "u", "device_id": "d", "event": "one"},
            {"org": "A", "subject": "u", "device_id": "d", "event": "two"},
            {"event": "unknown"},
        ]
    )
    assert len(grouped[("A", "u", "d")]) == 2
    assert grouped[("-", "-", "-")][0]["event"] == "unknown"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"display_name": " Player One "}, "Player One"),
        ({"FIRST": "Ada", "LAST": "Lovelace"}, "Ada Lovelace"),
        ({"TEAM": "Toronto", "jersey": 7}, "Toronto #7"),
        ({"team": "Toronto"}, "Toronto"),
        ({"number": 12}, "#12"),
        ({}, "(unnamed)"),
    ],
)
def test_name_for_row(row: dict[str, object], expected: str) -> None:
    assert _name_for_row(row) == expected


@pytest.mark.parametrize(
    ("name", "slug", "header"),
    [
        ("PRI", "pri", "PRI"),
        ("PRI-AF", "pri_af", "AF"),
        ("PRI AR", "pri_ar", "PRI AR"),
        ("custom-profile", "custom_profile", "custom-profile"),
    ],
)
def test_profile_name_helpers(name: str, slug: str, header: str) -> None:
    assert _slug_profile_key(name) == slug
    assert _profile_header(name) == header


def test_extract_profile_score_supports_primary_slug_and_scores_map() -> None:
    assert _extract_profile_score({"pri": "91"}, "PRI") == 91
    assert _extract_profile_score({"pri_af": 88}, "PRI-AF") == 88
    assert _extract_profile_score({"scores": {"Custom": 77}}, "Custom") == 77
    assert _extract_profile_score({"pri": "bad"}, "PRI") == 0
    assert _extract_profile_score({}, "missing") is None
    assert _extract_profile_score({}, "") is None


def test_detect_profiles_prefers_scores_map_then_slugs() -> None:
    assert _detect_profiles_from_results([{"scores": {"PRI": 90, "PRI-AF": 80}}]) == [
        "PRI",
        "PRI-AF",
    ]
    assert _detect_profiles_from_results([{"pri_ar": 70}, {"pri_ap": 60}]) == [
        "PRI",
        "PRI-AR",
        "PRI-AP",
    ]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([], []),
        ([5.0], [50.0]),
        ([1.0, 2.0], [25.0, 75.0]),
        (
            [1.0, 1.0, 3.0],
            [
                _approx(33.333333),
                _approx(33.333333),
                _approx(83.333333),
            ],
        ),
    ],
)
def test_midrank_percentiles(values: list[float], expected: list[object]) -> None:
    assert _midrank_percentiles(values) == expected


def test_csvish_context_and_cell_formatting_helpers() -> None:
    assert _split_csvish(["PRI, PRI-AF", "", "PRI-AR"]) == ["PRI", "PRI-AF", "PRI-AR"]
    assert _context_label(" batch ", "fallback") == "batch"
    assert _context_label({}, "fallback") == "fallback"
    assert _format_cell("pri_raw", "0.123456") == "0.1235"
    assert _format_cell("percentile", 91.234) == "91.2"
    assert _format_cell("name", None) == ""
    assert _format_cell("name", "Ada") == "Ada"


def test_string_list_and_filter_key_coercion() -> None:
    assert _as_str_list(None) == []
    assert _as_str_list([" a ", "", 2]) == ["a", "2"]
    assert _as_str_list(("x", " y ")) == ["x", "y"]
    assert _as_str_list("x") == []

    assert _coerce_filter_keys({"filters": {"GP": {}, "TEAM": {}}}) == ["GP", "TEAM"]
    assert _coerce_filter_keys({"keys": [" GP ", "", "TEAM"]}) == ["GP", "TEAM"]
    assert _coerce_filter_keys({}) == []


def test_parse_kv_items_types_values_and_lists() -> None:
    parsed = _parse_kv_items(
        [
            "min_gp=5",
            "ratio=1.25",
            "enabled=true",
            "disabled=false",
            "teams=A,B, C",
            "present",
            "name=Toronto",
            "=ignored",
            "",
        ]
    )
    assert parsed == {
        "min_gp": 5,
        "ratio": 1.25,
        "enabled": True,
        "disabled": False,
        "teams": ["A", "B", "C"],
        "present": True,
        "name": "Toronto",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, None),
        ("", None),
        (" 5 ", 5.0),
        (3.25, 3.25),
        ("bad", None),
    ],
)
def test_to_float_or_none(value: object, expected: float | None) -> None:
    assert _to_float_or_none(value) == expected


def test_display_filters_cover_min_gp_lists_bare_and_equality() -> None:
    row = {"GP": "6", "TEAM": "Toronto", "ACTIVE": "yes", "EMPTY": ""}

    assert _passes_display_filters(row, {})
    assert _passes_display_filters(row, {"min_gp": 5})
    assert not _passes_display_filters(row, {"min_gp": 7})
    assert _passes_display_filters(row, {"team": "toronto"})
    assert _passes_display_filters(row, {"TEAM": ["Boston", "Toronto"]})
    assert _passes_display_filters(row, {"ACTIVE": True})
    assert not _passes_display_filters(row, {"EMPTY": True})
    assert not _passes_display_filters(row, {"MISSING": "x"})
