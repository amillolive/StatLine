"""Stable StatLine SDK, including runnable StatPack support."""

from statline.core.datasets import list_datasets, load_dataset
from statline.core.relate import (
    load_pairwise_rows,
    read_inputs,
    score_native_pairwise,
    score_pairwise,
)
from statline.core.statpacks.runtime import run_statpack
from statline.public import (
    CompiledAdapter,
    Row,
    Rows,
    list_adapters,
    load_adapter,
    map_batch,
    map_row,
    score,
    score_batch,
    score_row,
)

__all__ = [
    "CompiledAdapter",
    "Row",
    "Rows",
    "list_adapters",
    "list_datasets",
    "load_adapter",
    "load_dataset",
    "load_pairwise_rows",
    "map_batch",
    "map_row",
    "read_inputs",
    "run_statpack",
    "score",
    "score_batch",
    "score_native_pairwise",
    "score_pairwise",
    "score_row",
]
