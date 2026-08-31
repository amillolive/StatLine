"""Gateway concurrency and visibility regressions for the v4 release candidate."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from statline.core.datasets import load_dataset
from statline.gateway.http.app import app
from statline.gateway.http.models import ScoreIn
from statline.gateway.http.resources import adapter_catalog, adapter_document
from statline.gateway.http.routes_api import _score_request, score

ROOT = Path(__file__).resolve().parents[1]
DEPRECATED = ROOT / "statline" / "core" / "adapters" / "schemas" / "deprecated"


def test_gateway_catalog_hides_deprecated_adapters_and_paths() -> None:
    catalog = adapter_catalog()
    names = {str(item["key"]) for item in catalog["adapters"]}

    assert "demo" not in names
    assert "example" not in names
    assert "valorant" not in names

    with pytest.raises(KeyError, match="not discoverable"):
        adapter_document(str(DEPRECATED / "demo.yaml"))


def test_gateway_score_profiles_are_explicit_and_lean() -> None:
    rows = load_dataset("EBA_Elevate302/eba_s1_players", limit=4)
    body = ScoreIn(
        adapter="eba.players",
        rows=rows,
        profiles=["PRI"],
        output={
            "show_weights": False,
            "hide_pri_raw": False,
            "show_components": False,
            "show_buckets": False,
            "show_context_used": True,
        },
    )

    payload = _score_request(body)

    assert payload["scored_count"] == 4
    assert all(set(item["scores"]) == {"PRI"} for item in payload["results"])


def test_score_route_is_async_and_origin_exposes_server_timing() -> None:
    assert inspect.iscoroutinefunction(score)

    with TestClient(app) as client:
        response = client.get("/v4/health")

    assert response.status_code == 200
    assert response.headers["server-timing"].startswith("app;dur=")
