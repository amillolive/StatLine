"""TUI catalog definitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ParamKind = Literal["text", "number", "boolean", "path", "choice", "multi"]


@dataclass(frozen=True)
class ActionParam:
    name: str
    kind: ParamKind
    required: bool
    default: Any
    help: str
    opts: tuple[str, ...]
    choices: tuple[str, ...]


@dataclass(frozen=True)
class ActionSpec:
    id: str
    title: str
    group: str
    command_path: tuple[str, ...]
    short_help: str
    click_help: str
    params: tuple[ActionParam, ...]


__all__ = ["ActionParam", "ActionSpec", "ParamKind"]
