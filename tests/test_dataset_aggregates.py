from __future__ import annotations

import pytest
from statline import map_batch
from statline.core.adapters.compile import build_dataset_context
from statline.core.adapters.compile import compile_expr as _compile_expr


def _approx(expected: float) -> object:
    """Typed float-only wrapper around pytest.approx for strict Pyright."""
    return pytest.approx(expected)  # pyright: ignore[reportUnknownMemberType]


def test_dataset_aggregates_accept_any_header_case_insensitively() -> None:
    context = build_dataset_context(
        [
            {"Custom Header": 1, "PLAYER": "alpha"},
            {"custom header": "3", "player": "beta"},
            {"CUSTOM HEADER": "not-numeric", "PLAYER": ""},
        ]
    )

    assert _compile_expr('dataset_max("CUSTOM HEADER")')(context, 0.0) == _approx(3.0)
    assert _compile_expr('dataset_min("custom header")')(context, 0.0) == _approx(1.0)
    assert _compile_expr('dataset_mean("Custom Header")')(context, 0.0) == _approx(2.0)
    assert _compile_expr('dataset_median("Custom Header")')(context, 0.0) == _approx(2.0)
    assert _compile_expr('dataset_sum("Custom Header")')(context, 0.0) == _approx(4.0)
    assert _compile_expr('dataset_count("player")')(context, 0.0) == _approx(2.0)


def test_eba_rel_gp_tracks_65_percent_of_batch_max_gp() -> None:
    mapped = map_batch(
        "eba.players",
        [{"GP": 3}, {"GP": 8}, {"GP": 11}],
    )

    assert mapped[0]["rel_gp"] == _approx(3.0 / (11.0 * 0.65))
    assert mapped[1]["rel_gp"] == _approx(1.0)
    assert mapped[2]["rel_gp"] == _approx(1.0)


def test_eba_rel_gp_moves_when_batch_max_gp_moves() -> None:
    mapped = map_batch(
        "eba.players",
        [{"GP": 7}, {"GP": 12}],
    )

    assert mapped[0]["rel_gp"] == _approx(7.0 / (12.0 * 0.65))
    assert mapped[1]["rel_gp"] == _approx(1.0)
