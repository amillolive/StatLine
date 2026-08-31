"""StatLine public Python API."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from statline._version import PACKAGE_VERSION

try:
    __version__ = version("statline")
except PackageNotFoundError:
    __version__ = PACKAGE_VERSION

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
