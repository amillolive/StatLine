"""Lossless envelope, reconciliation, and real-run attribution regressions."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import replace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from statline import load_adapter, load_dataset, map_batch, score
from statline.core.scoring import calculate_pri
from statline.core.types.adapters import CompiledAdapter
from statline.gateway.adapters.envelope import compact_results, expand_results
from statline.gateway.auth.types import Principal
from statline.gateway.http.app import app
from statline.gateway.http.dependencies import principal_dependency
from statline.gateway.http.models import ScoreIn
from statline.gateway.http.routes_api import _score_request


@pytest.mark.parametrize("count", [0, 1, 2, 103])
def test_compact_round_trip_and_no_input_mutation(count: int) -> None:
    source = [
        {"pri": index, "weights": {"a": 1.0}, "context_used": {"a": {"floor": 0, "leader": 3}}}
        for index in range(count)
    ]
    original = copy.deepcopy(source)
    shared, rows = compact_results(source)
    assert expand_results(shared, rows) == original
    assert source == original
    assert shared == (
        {}
        if not count
        else {"weights": {"a": 1.0}, "context_used": {"a": {"floor": 0, "leader": 3}}}
    )
    assert all("weights" not in row and "context_used" not in row for row in rows)


@pytest.mark.parametrize(
    "results",
    [
        [{"weights": {"a": 1}}, {"weights": {"a": 2}}],
        [{"weights": {"a": 1}}, {}],
        [{"context_used": {"a": 1}}, {"context_used": {"a": 2}}],
        [{"explanation_context": {"profile": 1}}, {"explanation_context": {"profile": 2}}],
    ],
)
def test_unequal_and_partial_metadata_stays_on_rows(results: list[dict[str, Any]]) -> None:
    shared, rows = compact_results(results)
    assert shared == {}
    assert rows == results


def test_row_specific_metadata_wins_when_expanding() -> None:
    assert expand_results({"weights": {"a": 1}}, [{"weights": {"a": 2}}]) == [{"weights": {"a": 2}}]


@pytest.mark.parametrize("count", [0, 1, 4, 103])
@pytest.mark.parametrize("profiles", [None, ["PRI"], ["PRI_W", "PRI_ALT"]])
def test_gateway_compact_is_mathematically_identical(
    count: int, profiles: list[str] | None
) -> None:
    rows = load_dataset("EBA_Elevate302/eba_players_gp3_plus.csv")[:count]
    legacy = _score_request(
        ScoreIn(adapter="eba.players", rows=rows, profiles=profiles, include_mapped=True)
    )
    compact = _score_request(
        ScoreIn(
            adapter="eba.players",
            rows=rows,
            profiles=profiles,
            include_mapped=True,
            response_format="compact",
        )
    )
    assert expand_results(compact["shared"], compact["results"]) == legacy["results"]
    assert compact["mapped"] == legacy["mapped"]
    assert compact["input_count"] == compact["mapped_count"] == compact["scored_count"] == count
    assert [row["source_index"] for row in compact["results"]] == list(range(count))


def test_identity_survives_both_filters_and_duplicate_names() -> None:
    seed = load_dataset("EBA_Elevate302/eba_players_gp3_plus.csv", limit=1)[0]
    rows: list[dict[str, Any]] = [
        dict(seed, PLAYER="Same Name", TEAM="Excluded", GP=12),
        dict(seed, PLAYER="Same Name", TEAM="Eligible", GP=0),
        dict(seed, PLAYER="Same Name", TEAM="Eligible", GP=12),
        dict(seed, PLAYER="Same Name", TEAM="Eligible", GP=18),
    ]
    payload = _score_request(
        ScoreIn(
            adapter="eba.players",
            rows=rows,
            filters={"team": "Eligible", "min_gp": 3},
            response_format="compact",
            include_mapped=True,
        )
    )
    assert payload["input_count"] == 4
    assert payload["mapped_count"] == payload["scored_count"] == 2
    assert [row["source_index"] for row in payload["results"]] == [2, 3]
    assert [row["source_id"] for row in payload["results"]] == ["Same Name", "Same Name"]
    sorted_rows = sorted(
        cast(list[dict[str, Any]], payload["results"]), key=lambda row: -row["pri"]
    )
    for row in sorted_rows:
        assert rows[int(row["source_index"])]["PLAYER"] == row["source_id"]


def test_mapped_input_without_names_retains_source_position() -> None:
    rows = map_batch(
        "eba.players", load_dataset("EBA_Elevate302/eba_players_gp3_plus.csv", limit=2)
    )
    payload = _score_request(
        ScoreIn(adapter="eba.players", rows=rows, input_kind="mapped", response_format="compact")
    )
    assert [row["source_index"] for row in payload["results"]] == [0, 1]
    assert all("source_id" not in row for row in payload["results"])


def test_lean_output_does_not_gain_diagnostics() -> None:
    payload = _score_request(
        ScoreIn(
            adapter="eba.players",
            rows=load_dataset("EBA_Elevate302/eba_players_gp3_plus.csv", limit=2),
            response_format="compact",
            profiles=["PRI"],
            output={"show_components": False, "show_buckets": False},
        )
    )
    assert payload["shared"] == {}
    assert payload["mapped"] is None
    assert all(
        set(row) == {"pri", "scores", "primary_profile", "source_id", "source_index"}
        for row in payload["results"]
    )


@pytest.mark.parametrize(
    "adapter_name,dataset",
    [
        ("eba.players", "EBA_Elevate302/eba_players_gp3_plus.csv"),
        ("eba.teams", "EBA_Elevate302/eba_s1_teams.csv"),
        ("urrl.players", "URRL/S20/RB/FINAL.csv"),
    ],
)
@pytest.mark.parametrize(
    "weights", [None, {"scoring_gravity": -1.0, "offball": 0.5, "offense": -1.0}]
)
def test_explanations_reconcile_with_actual_kernel(
    adapter_name: str, dataset: str, weights: dict[str, float] | None
) -> None:
    rows = load_dataset(dataset, limit=8)
    adapter = load_adapter(adapter_name)
    mapped = map_batch(adapter, rows)
    plain = calculate_pri(mapped, adapter, weights=weights)
    explained = calculate_pri(
        mapped,
        adapter,
        weights=weights,
        output={
            "show_weights": True,
            "show_context_used": True,
            "show_components": True,
            "show_buckets": True,
            "hide_pri_raw": False,
            "show_explanation": True,
        },
    )
    assert [
        {
            key: value
            for key, value in row.items()
            if key not in {"explanation", "explanation_context"}
        }
        for row in explained
    ] == plain
    for row in explained:
        context = row["explanation_context"]
        details = row["explanation"]
        for profile, attribution in details["profiles"].items():
            weights_used = context["profile_weights"][profile]
            assert math.isclose(
                attribution["weighted_sum"],
                sum(attribution["contributions"].values()),
                rel_tol=1e-6,
                abs_tol=1e-14,
            )
            assert (
                attribution["final_score"]
                == row["scores"][profile]
                == round(attribution["final_unrounded"])
            )
            for metric, weight in weights_used.items():
                component = details["metrics"][metric]["normalized_value"]
                assert attribution["contributions"][metric] == component * weight
                assert attribution["relative_influence"][metric] == (component - 0.5) * weight
            assert all(attribution["relative_influence"][key] > 0 for key in attribution["helped"])
            assert all(attribution["relative_influence"][key] < 0 for key in attribution["limited"])
            if not attribution["unscoreable_override"]:
                assert attribution["raw_aggregate"] == max(0, min(1, attribution["weighted_sum"]))
            if profile == row["primary_profile"]:
                assert attribution["raw_aggregate"] == row["pri_raw"]


def test_caps_and_unscoreable_explanation_remain_explicit() -> None:
    adapter = load_adapter("eba.players")
    mapped = map_batch(adapter, load_dataset("EBA_Elevate302/eba_players_gp3_plus.csv", limit=1))
    mapped[0]["scoreable"] = 0
    result = calculate_pri(
        mapped, adapter, caps_override={"gp": 1}, output={"show_explanation": True}
    )[0]
    assert result["explanation_context"]["normalization_source"] == "caps_override"
    assert result["explanation_context"]["context_used"]["gp"] == {"leader": 1, "floor": 0}
    assert all(
        profile["unscoreable_override"] and profile["raw_aggregate"] == 0
        for profile in result["explanation"]["profiles"].values()
    )


def test_row_caps_preserve_individual_attributions() -> None:
    payload = _score_request(
        ScoreIn(
            adapter="eba.players",
            dataset="EBA_Elevate302/eba_players_gp3_plus.csv",
            dataset_limit=2,
            caps_mode="row",
            response_format="compact",
            output={"show_explanation": True},
        )
    )
    assert "explanation_context" in payload["shared"]
    assert all(
        row["explanation"]["profiles"]["PRI"]["final_score"] == row["pri"]
        for row in payload["results"]
    )


def test_adapter_identity_override_is_generic(monkeypatch: pytest.MonkeyPatch) -> None:
    import statline.gateway.adapters.scoring as pipeline

    original = load_adapter("eba.players")
    metadata = replace(original.metadata, meta={"source_id_field": "CUSTOM_ID"})

    def overridden_adapter(_name: str) -> CompiledAdapter:
        return replace(original, metadata=metadata)

    monkeypatch.setattr(pipeline, "get_adapter", overridden_adapter)
    seed = load_dataset("EBA_Elevate302/eba_players_gp3_plus.csv", limit=1)[0]
    payload = _score_request(
        ScoreIn(adapter="eba.players", row=dict(seed, CUSTOM_ID="record-9", source_index=99))
    )
    assert payload["results"][0]["source_index"] == 0
    assert payload["results"][0]["source_id"] == "record-9"


def test_openapi_and_serialized_contract_are_coordinated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        app.dependency_overrides,
        principal_dependency,
        lambda: Principal("test", "test", "", "", {"userbase"}),
    )
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()
        assert (
            spec["components"]["schemas"]["ScoreIn"]["properties"]["response_format"]["default"]
            == "legacy"
        )
        assert "source_index" in spec["components"]["schemas"]["ScoredResult"]["required"]
        response = client.post(
            "/v4/score",
            json={
                "adapter": "eba.players",
                "dataset": "EBA_Elevate302/eba_players_gp3_plus.csv",
                "dataset_limit": 2,
                "response_format": "compact",
            },
        )
        assert response.status_code == 200
        assert "weights" in response.json()["shared"]
        assert all("weights" not in row for row in response.json()["results"])
        legacy = client.post("/v4/score", json={"adapter": "eba.players", "rows": []}).json()
        assert "response_format" not in legacy and "shared" not in legacy


def test_json_encoding_savings_use_actual_responses() -> None:
    legacy = _score_request(
        ScoreIn(adapter="eba.players", dataset="EBA_Elevate302/eba_players_gp3_plus.csv")
    )
    compact = _score_request(
        ScoreIn(
            adapter="eba.players",
            dataset="EBA_Elevate302/eba_players_gp3_plus.csv",
            response_format="compact",
        )
    )

    def encode(value: object) -> bytes:
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()

    assert len(encode(compact)) < len(encode(legacy)) * 0.55


def portable_fixture(value: Any) -> Any:
    """Ignore sub-1e-10 float representation drift between supported interpreters."""
    if isinstance(value, float):
        return round(value, 10)
    if isinstance(value, list):
        return [portable_fixture(item) for item in cast(list[Any], value)]
    if isinstance(value, dict):
        return {key: portable_fixture(item) for key, item in cast(dict[str, Any], value).items()}
    return value


@pytest.mark.parametrize(
    "name,dataset,expected",
    [
        (
            "eba.players",
            "EBA_Elevate302/eba_players_gp3_plus.csv",
            "a403d551583ef19680259c99105048e2260dc471f67f2697ee02a98cbffc8cca",
        ),
        (
            "eba.teams",
            "EBA_Elevate302/eba_s1_teams.csv",
            "91e553575aac699426c6f89640516f0c930c9ef1bc1adb876e4690ab33a81bca",
        ),
    ],
)
def test_original_rc6_full_output_is_unchanged(name: str, dataset: str, expected: str) -> None:
    results = score(name, load_dataset(dataset), mode="batch", weights="pri")
    encoded = json.dumps(
        portable_fixture(results), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected
