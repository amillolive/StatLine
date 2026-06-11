"""StatLine public Python API."""
from __future__ import annotations

__version__ = "3.0.0"

from statline.datasets import (
    dataset_root,
    iter_dataset,
    list_datasets,
    load_dataset,
    resolve_dataset,
)
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
    "__version__",
    "CompiledAdapter",
    "Row",
    "Rows",
    "dataset_root",
    "iter_dataset",
    "list_adapters",
    "list_datasets",
    "load_adapter",
    "load_dataset",
    "map_batch",
    "map_row",
    "resolve_dataset",
    "score",
    "score_batch",
    "score_row",
]
