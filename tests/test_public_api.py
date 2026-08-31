from __future__ import annotations

from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[1]
DEPRECATED = ROOT / "statline" / "core" / "adapters" / "schemas" / "deprecated"
DEMO_ADAPTER = DEPRECATED / "demo.yaml"
VALORANT_ADAPTER = DEPRECATED / "valorant.yaml"


def test_public_api_scores_demo_row() -> None:
    row = load_dataset("DEMO/demo", limit=1)[0]
    adapter = load_adapter(str(DEMO_ADAPTER))

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
        score_row(str(DEMO_ADAPTER), row, filters={"games_played_gte": 999})


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

    results = score_batch(str(VALORANT_ADAPTER), rows, filters={"min_rounds": 10})

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
        score_row(str(VALORANT_ADAPTER), row, filters={"min_rounds": 10})


def test_deprecated_adapters_are_hidden_but_explicit_paths_still_work() -> None:
    names = list_adapters()

    assert "demo" not in names
    assert "example" not in names
    assert "valorant" not in names
    assert load_adapter(str(DEMO_ADAPTER)).key == "demo"

    with pytest.raises(ValueError, match="Unknown adapter 'demo'"):
        load_adapter("demo")


def test_profile_selection_is_opt_in_and_preserves_primary_score() -> None:
    rows = load_dataset("EBA_Elevate302/eba_s1_players", limit=8)

    full = score_batch("eba_players", rows)
    primary_only = score_batch("eba_players", rows, profiles=["PRI"])

    assert [item["pri"] for item in primary_only] == [item["pri"] for item in full]
    assert [item["pri_raw"] for item in primary_only] == [item["pri_raw"] for item in full]
    assert all(set(item["scores"]) == {"PRI"} for item in primary_only)
    assert any(len(item["scores"]) > 1 for item in full)


def test_lean_output_skips_disabled_diagnostics_without_changing_score() -> None:
    rows = load_dataset("EBA_Elevate302/eba_s1_players", limit=8)
    full = score_batch("eba_players", rows)
    lean = score_batch(
        "eba_players",
        rows,
        profiles=["PRI"],
        output={
            "show_weights": False,
            "hide_pri_raw": False,
            "show_components": False,
            "show_buckets": False,
            "show_context_used": True,
        },
    )

    assert [item["pri"] for item in lean] == [item["pri"] for item in full]
    assert [item["pri_raw"] for item in lean] == [item["pri_raw"] for item in full]
    for item in lean:
        assert "weights" not in item
        assert "components" not in item
        assert "buckets" not in item
        assert "context_used" in item
        assert item["scores"].keys() == {"PRI"}
