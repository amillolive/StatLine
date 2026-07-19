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


def test_metric_filters_apply_after_mapping_before_scoring_context() -> None:
    rows = [
        {
            "rounds_played": 1,
            "kills": 20,
            "deaths": 1,
            "assists": 0,
            "damage_total": 4000,
            "headshots": 20,
            "shots": 40,
            "first_kills": 5,
            "first_deaths": 0,
            "plants": 0,
            "defuses": 0,
            "clutches_played": 1,
            "clutches_won": 1,
        },
        {
            "rounds_played": 12,
            "kills": 20,
            "deaths": 10,
            "assists": 5,
            "damage_total": 3000,
            "headshots": 10,
            "shots": 50,
            "first_kills": 3,
            "first_deaths": 2,
            "plants": 1,
            "defuses": 1,
            "clutches_played": 2,
            "clutches_won": 1,
        },
    ]

    results = score_batch("valorant", rows, filters={"min_rounds": 10})

    assert len(results) == 1
    assert "scores" in results[0]
    assert results[0]["scores"]["pri"] == results[0]["pri"]


def test_metric_filtered_out_row_has_clear_error() -> None:
    row = {
        "rounds_played": 1,
        "kills": 20,
        "deaths": 1,
        "assists": 0,
        "damage_total": 4000,
        "headshots": 20,
        "shots": 40,
        "first_kills": 5,
        "first_deaths": 0,
        "plants": 0,
        "defuses": 0,
        "clutches_played": 1,
        "clutches_won": 1,
    }

    with pytest.raises(ValueError, match="did not match filters"):
        score_row("valorant", row, filters={"min_rounds": 10})
