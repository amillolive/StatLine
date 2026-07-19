"""Canonical scoring namespace."""
from statline.core.scoring.map import safe_map_raw, score_row_from_raw, score_rows_from_raw
from statline.core.scoring.normalize import clamp01, norm
from statline.core.scoring.score import calculate_pri, passes_mapped_filters, passes_raw_filters
from statline.core.scoring.weights import normalize_weights, pick_profile, resolve_weights

__all__ = [
    "calculate_pri", "clamp01", "norm", "normalize_weights", "passes_mapped_filters",
    "passes_raw_filters", "pick_profile", "resolve_weights", "safe_map_raw",
    "score_row_from_raw", "score_rows_from_raw",
]
