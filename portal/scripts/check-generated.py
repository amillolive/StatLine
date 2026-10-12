"""Fail when bundled contracts were generated from another source revision."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from source_contract import source_fingerprint


def main() -> int:
    portal = Path(__file__).resolve().parents[1]
    source = portal.parent
    product = json.loads((portal / "data/product.json").read_text())
    actual = source_fingerprint(source)
    if product["source_sha256"] != actual:
        print("Saved portal contract is stale. Run pnpm generate, then rebuild.")
        return 1
    for name in ("product.json", "openapi.json", "docs-manifest.json"):
        if (portal / "data" / name).read_bytes() != (
            portal / "public/downloads" / name
        ).read_bytes():
            print("Saved/downloadable contract mismatch: " + name)
            return 1
    spec = json.loads((portal / "data/openapi.json").read_text())
    assert spec["x-statline-snapshot"]["source_sha256"] == actual
    assert spec["info"]["version"] == product["engine_version"]
    assert "response_format" in spec["components"]["schemas"]["ScoreIn"]["properties"]
    for name in ("eba-players-results.json", "eba-teams-results.json"):
        bundle = json.loads((portal / "public/downloads" / name).read_text())
        assert bundle["source_sha256"] == actual
        response = bundle["api_response"]
        assert response["response_format"] == "compact"
        assert all(
            "weights" not in row and "context_used" not in row for row in response["results"]
        )
        assert [row["source_index"] for row in response["results"]] == list(
            range(len(bundle["rows"]))
        )
        csv_path = portal / "public/downloads" / name.replace("results.json", "scored.csv")
        with csv_path.open(encoding="utf-8", newline="") as stream:
            exported = list(csv.DictReader(stream))
        assert len(exported) == len(response["results"])
        for flat, result in zip(exported, response["results"], strict=True):
            assert int(flat["source_index"]) == result["source_index"]
            assert flat["source_id"] == str(result.get("source_id"))
    print(
        "Source fingerprint, saved OpenAPI, compact examples, JSON/CSV identity and downloads verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
