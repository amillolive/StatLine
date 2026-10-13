"""Optional YAML-authored pairwise evaluation for StatLine v4."""

from statline.core.relate.engine import PairwiseError, score_pairwise
from statline.core.relate.native import read_inputs, score_native_pairwise
from statline.core.relate.sources import load_pairwise_rows

__all__ = [
    "PairwiseError",
    "load_pairwise_rows",
    "read_inputs",
    "score_native_pairwise",
    "score_pairwise",
]
