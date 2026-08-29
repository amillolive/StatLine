"""CLI definitions."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, Iterable, TextIO, TypedDict


@dataclass
class SLAPIHttpError(Exception):
    status_code: int
    message: str
    detail: Any = None

    def __str__(self) -> str:
        base = f"SLAPI {self.status_code}: {self.message}"
        return base if self.detail is None else f"{base} :: {self.detail}"


class ViewRow(TypedDict):
    Rank: int
    Name: str
    PRI: int
    Raw: str
    Context: str


class YamlLikeProtocol:
    CSafeLoader: Any
    SafeLoader: Any

    def load(self, stream: str, *, Loader: Any) -> Any: ...
    def safe_load(self, stream: str) -> Any: ...


class CsvWriterProtocol:
    def writerow(self, row: Iterable[Any], /) -> Any: ...


class BannerFilter(io.TextIOBase):
    def __init__(self, underlying: TextIO, banner_pattern: re.Pattern[str]) -> None:
        self._underlying = underlying
        self._banner_pattern = banner_pattern
        self._swallowed = False
        self._buffer = ""

    def write(self, value: str) -> int:
        self._buffer += value
        output: list[str] = []
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if not self._swallowed and self._banner_pattern.match(line.strip()):
                self._swallowed = True
                continue
            output.append(line + "\n")
        return self._underlying.write("".join(output)) if output else 0

    def flush(self) -> None:
        if self._buffer:
            chunk, self._buffer = self._buffer, ""
            self._underlying.write(chunk)
        self._underlying.flush()

    def fileno(self) -> int:
        return self._underlying.fileno()

    def isatty(self) -> bool:
        try:
            return self._underlying.isatty()
        except Exception:
            return False


__all__ = ["BannerFilter", "CsvWriterProtocol", "SLAPIHttpError", "ViewRow", "YamlLikeProtocol"]
