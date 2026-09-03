"""Compatibility surface for terminal presentation helpers.

Canonical implementations live in :mod:`statline.core.presentation` so Core never
depends on the App package. App re-exports them to preserve the existing import path.
"""

from statline.core.presentation import (
    profile_names,
    profile_score,
    profile_tables,
    render_profile_tables,
    render_table_text,
    render_timing,
    save_profile_report,
    slug_profile,
)

__all__ = [
    "profile_names",
    "profile_score",
    "profile_tables",
    "render_profile_tables",
    "render_table_text",
    "render_timing",
    "save_profile_report",
    "slug_profile",
]
