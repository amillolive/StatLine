"""Structured, opt-in stage diagnostics with a stable terminal summary."""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Generator
from time import perf_counter
from typing import TypedDict


class PipelineEvent(TypedDict):
    stage: str
    phase: str
    elapsed_ms: float
    sequence: int


class StageTimes:
    """Track stage durations; subscribers receive events without affecting stdout."""

    def __init__(self, on_event: Callable[[PipelineEvent], None] | None = None) -> None:
        self.items: list[tuple[str, float]] = []
        self.events: list[PipelineEvent] = []
        self._on_event = on_event

    def _emit(self, stage: str, phase: str, elapsed_ms: float) -> None:
        event: PipelineEvent = {
            "stage": stage, "phase": phase, "elapsed_ms": elapsed_ms,
            "sequence": len(self.events),
        }
        self.events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    @contextlib.contextmanager
    def stage(self, name: str) -> Generator[None, None, None]:
        """Measure a stage, including when it fails."""
        started = perf_counter()
        self._emit(name, "start", 0.0)
        try:
            yield
        except BaseException:
            self._emit(name, "error", (perf_counter() - started) * 1000.0)
            raise
        finally:
            elapsed = (perf_counter() - started) * 1000.0
            self.items.append((name, elapsed))
            self._emit(name, "end", elapsed)

    def as_dict(self) -> dict[str, object]:
        """JSON-safe diagnostic payload for telemetry, tests and CLI integrations."""
        return {
            "stages": [{"name": name, "duration_ms": ms} for name, ms in self.items],
            "total_ms": sum(ms for _, ms in self.items),
            "events": list(self.events),
        }

    def print_summary(self) -> None:
        total = sum(ms for _, ms in self.items)
        for stage_name, ms in self.items:
            print(f"{stage_name:18s} {ms:7.2f} ms")
        print("-" * 28)
        print(f"{'TOTAL':18s} {total:7.2f} ms")
