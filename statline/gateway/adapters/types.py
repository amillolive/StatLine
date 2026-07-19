"""Gateway adapter discovery definitions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

AdapterConfig = Dict[str, Any]


class YamlError(RuntimeError):
    """Raised on YAML loading or decoding problems."""


@dataclass(frozen=True)
class ResolvedYaml:
    name: str
    path: Path
    config: AdapterConfig


__all__ = ["AdapterConfig", "ResolvedYaml", "YamlError"]
