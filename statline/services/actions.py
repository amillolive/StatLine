# statline/services/actions.py
from __future__ import annotations

from dataclasses import dataclass  # pyright: ignore[reportUnusedImport]
from typing import Any, Callable, Literal, Mapping

ParamKind = Literal["text", "number", "boolean", "path", "choice", "multi"]

@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: ParamKind
    required: bool = False
    default: Any = None
    help: str = ""
    choices: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

@dataclass(frozen=True)
class ActionResult:
    title: str
    data: Any
    kind: Literal["table", "json", "markdown", "text", "status"] = "json"
    message: str = ""

@dataclass(frozen=True)
class ActionSpec:
    id: str
    title: str
    group: str
    help: str
    params: tuple[ParamSpec, ...]
    run: Callable[[Mapping[str, Any]], ActionResult]