from __future__ import annotations

import pytest
from statline import (
    list_adapters,
    list_datasets,
    load_adapter,
    load_dataset,
    map_row,
    score,
    score_batch,
    score_row,
)


def test_public_api_scores_demo_row() -> None:
    row = load_dataset("DEMO/demo", limit=1)[0]
    adapter = load_adapter("demo")

    mapped = map_row(adapter, row)
    assert "ppg" in mapped

    result = score_row(adapter, row)
    assert isinstance(result["pri"], int)
    assert "scores" in result


def test_public_api_scores_eba_batch() -> None:
    adapter = load_adapter("eba_players")
    rows = load_dataset("EBA_Elevate302/eba_s1_players")

    results = score(adapter, rows, mode="batch")

    assert isinstance(results, list)
    assert len(results) == len(rows)
    assert "PRI" in results[0]["scores"]


def test_string_adapter_and_batch_helper() -> None:
    rows = load_dataset("EBA_Elevate302/eba_s1_teams")
    results = score_batch("eba_teams", rows)

    assert len(results) == len(rows)
    assert "standings" in results[0]["scores"]


def test_every_registered_adapter_loads() -> None:
    names = list_adapters()
    assert names
    for name in names:
        adapter = load_adapter(name)
        assert adapter.key
        assert adapter.score_profiles, f"{adapter.key} must define score_profiles"


def test_dataset_listing_and_resolution() -> None:
    datasets = list_datasets()
    assert "DEMO/demo.csv" in datasets
    assert load_dataset("demo", limit=1)


def test_row_filtered_out_has_clear_error() -> None:
    row = load_dataset("DEMO/demo", limit=1)[0]
    with pytest.raises(ValueError, match="did not match filters"):
        score_row("demo", row, filters={"games_played_gte": 999})
