import contextlib
from time import perf_counter
from typing import Generator, List, Tuple


class StageTimes:
    # List of (stage_name, elapsed_ms)
    items: List[Tuple[str, float]]

    def __init__(self) -> None:
        self.items = []

    @contextlib.contextmanager
    def stage(self, name: str) -> Generator[None, None, None]:
        """Measure a block and record (name, elapsed_ms) in items."""
        t0 = perf_counter()
        try:
            yield
        finally:
            self.items.append((name, (perf_counter() - t0) * 1000.0))

    def print_summary(self) -> None:
        total = sum(ms for _, ms in self.items)
        for stage_name, ms in self.items:
            print(f"{stage_name:18s} {ms:7.2f} ms")
        print("-" * 28)
        print(f"{'TOTAL':18s} {total:7.2f} ms")
