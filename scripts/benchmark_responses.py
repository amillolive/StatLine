"""Measure scoring, compaction, encoding and gzip separately on bundled EBA data."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from statline import load_dataset, score
from statline.gateway.adapters.envelope import compact_results, expand_results
from statline.gateway.http.models import ScoreIn
from statline.gateway.http.routes_api import _score_request


def benchmark(adapter: str, dataset: str, repeats: int) -> dict[str, object]:
    rows = load_dataset(dataset)
    score(adapter, rows, mode="batch", weights="pri")
    scoring_ms: list[float] = []
    for _ in range(repeats):
        started = perf_counter()
        score(adapter, rows, mode="batch", weights="pri")
        scoring_ms.append((perf_counter() - started) * 1000)
    legacy = _score_request(ScoreIn(adapter=adapter, rows=rows, weights="pri"))
    compaction_ms: list[float] = []
    shared, results = compact_results(legacy["results"])
    for _ in range(repeats):
        started = perf_counter()
        shared, results = compact_results(legacy["results"])
        compaction_ms.append((perf_counter() - started) * 1000)
    compact = {**legacy, "response_format": "compact", "shared": shared, "results": results}
    assert expand_results(shared, results) == legacy["results"]
    measures: dict[str, dict[str, int | float]] = {}
    for name, payload in (("legacy", legacy), ("compact", compact)):
        encoding_ms: list[float] = []
        encoded = b""
        for _ in range(repeats):
            started = perf_counter()
            encoded = json.dumps(
                payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
            encoding_ms.append((perf_counter() - started) * 1000)
        measures[name] = {
            "uncompressed_bytes": len(encoded),
            "gzip_bytes": len(gzip.compress(encoded, compresslevel=6, mtime=0)),
            "median_json_encoding_ms": statistics.median(encoding_ms),
        }
    return {
        "adapter": adapter,
        "dataset": dataset,
        "row_count": len(rows),
        "median_local_map_and_scorer_ms": statistics.median(scoring_ms),
        "median_compaction_ms": statistics.median(compaction_ms),
        **measures,
        "uncompressed_reduction_percent": 100
        * (
            1 - measures["compact"]["uncompressed_bytes"] / measures["legacy"]["uncompressed_bytes"]
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    report = {
        "repeats": args.repeats,
        "python": sys.version.split()[0],
        "note": "Local fixture measurements; no production latency or compute speedup claim. Map/scorer time, compaction, and JSON encoding are independent. Gzip level 6 is reported separately.",
        "responses": [
            benchmark("eba.players", "EBA_Elevate302/eba_players_gp3_plus.csv", args.repeats),
            benchmark("eba.teams", "EBA_Elevate302/eba_s1_teams.csv", args.repeats),
        ],
    }
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text)


if __name__ == "__main__":
    main()
