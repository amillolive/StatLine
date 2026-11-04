"""StatLine public Python API."""

from __future__ import annotations

import gc

from statline._version import runtime_version

__version__ = runtime_version(__file__)

from statline.core.datasets import (
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

was_enabled = gc.isenabled()

if was_enabled:
    gc.disable()

__all__ = [
    "CompiledAdapter",
    "Row",
    "Rows",
    "__version__",
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
