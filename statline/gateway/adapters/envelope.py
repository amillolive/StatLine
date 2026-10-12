"""Lossless compact wire representation; local scoring dictionaries stay intact."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SHARED_FIELDS = ("context_used", "weights", "explanation_context")


def compact_results(
    results: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Hoist only fields present and exactly equal in every row, including one row.

    Unequal or partially present metadata remains on the affected rows. Empty
    runs have no inferred metadata. Inputs are never mutated.
    """
    rows = [dict(result) for result in results]
    shared: dict[str, Any] = {}
    if not rows:
        return shared, rows
    for key in SHARED_FIELDS:
        if key in rows[0] and all(key in row and row[key] == rows[0][key] for row in rows):
            shared[key] = rows[0][key]
            for row in rows:
                del row[key]
    return shared, rows


def expand_results(
    shared: Mapping[str, Any], results: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Restore full dictionaries; row-specific fields take precedence."""
    return [{**shared, **result} for result in results]
