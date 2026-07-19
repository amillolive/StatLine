"""Canonical adapter namespace."""

from statline.core.adapters.compile import compile_adapter, map_raw
from statline.core.adapters.hooks import available, clear, get, register
from statline.core.adapters.registry import (
    list_adapters,
    load_adapter,
    refresh_adapters,
    supported_adapters,
)
from statline.core.adapters.sniff import sniff_adapters
from statline.core.adapters.validate import validate_adapter
from statline.core.types.adapters import (
    AdapterHooks,
    AdapterSpec,
    AdapterValidationError,
    CompiledAdapter,
    NoOpHooks,
    ValidationIssue,
)

__all__ = [
    "AdapterHooks",
    "AdapterSpec",
    "AdapterValidationError",
    "CompiledAdapter",
    "NoOpHooks",
    "ValidationIssue",
    "available",
    "clear",
    "compile_adapter",
    "get",
    "list_adapters",
    "load_adapter",
    "map_raw",
    "refresh_adapters",
    "register",
    "sniff_adapters",
    "supported_adapters",
    "validate_adapter",
]
