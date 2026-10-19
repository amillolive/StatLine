"""Phase A safety, discovery, documentation and diagnostics regressions."""

from __future__ import annotations

import importlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from statline.app.cli.main import app as cli_app
from statline.core.adapters.inspect import inspect_adapter, inspect_dataset
from statline.core.adapters.registry import adapter_cache_info
from statline.core.statpacks import unpack_statpack
from statline.core.tools import yaml_codec
from statline.core.types.timing import PipelineEvent, StageTimes
from statline.gateway.http.app import app
from statline.gateway.http.portal import configure_portal_cors
from statline.gateway.http.resources import dataset_catalog
from typer.testing import CliRunner


def test_yaml_codec_is_safe_and_supports_python_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    assert yaml_codec.SAFE_LOADER in {yaml.SafeLoader, getattr(yaml, "CSafeLoader", yaml.SafeLoader)}
    assert yaml_codec.SAFE_DUMPER in {yaml.SafeDumper, getattr(yaml, "CSafeDumper", yaml.SafeDumper)}
    assert yaml_codec.safe_load("key: [one, two]") == {"key": ["one", "two"]}
    with pytest.raises(yaml.YAMLError):
        yaml_codec.safe_load("!!python/object/apply:os.system ['echo unsafe']")
    monkeypatch.setattr(yaml_codec, "SAFE_LOADER", yaml.SafeLoader)
    monkeypatch.setattr(yaml_codec, "SAFE_DUMPER", yaml.SafeDumper)
    assert yaml_codec.safe_load(yaml_codec.safe_dump({"safe": [1, 2]})) == {"safe": [1, 2]}


def test_reverse_catalog_resolves_adapter_metadata_aliases() -> None:
    catalog = dataset_catalog()
    targets = {entry["path"]: entry["adapters"] for entry in catalog["datasets"]}
    selected = "basketball/eba/data/players/EBA_Elevate302/eba_hybrid_s1_players.csv"
    assert set(targets[selected]) == {"e302.players", "eba.players"}
    report = inspect_dataset(selected)
    assert report["ambiguous"] is True
    assert report["declared_match_count"] == 2
    assert "PLAYER" in report["headers"]
    assert inspect_adapter("eba.players")["resolved_dataset"] == selected


def test_dataset_inspection_rejects_unpackaged_path(tmp_path: Path) -> None:
    local = tmp_path / "private.csv"
    local.write_text("secret\nvalue\n", encoding="utf-8")
    with pytest.raises((FileNotFoundError, ValueError)):
        inspect_dataset(local)


def test_cache_diagnostics_and_structured_stage_events() -> None:
    cache = adapter_cache_info()
    assert cache["compiled"] >= 1
    assert cache["hits"] >= 0
    assert cache["last_rebuild_ms"] >= 0
    observed: list[PipelineEvent] = []
    times = StageTimes(observed.append)
    with times.stage("prepare"):
        pass
    assert [event["phase"] for event in observed] == ["start", "end"]
    assert times.as_dict()["stages"]
    with pytest.raises(RuntimeError):
        with times.stage("failing"):
            raise RuntimeError("expected")
    assert [event["phase"] for event in observed[-3:]] == ["start", "error", "end"]


def test_cache_cli_does_not_misformat_numeric_hits_as_timestamps() -> None:
    result = CliRunner().invoke(cli_app, ["--mode", "local", "adapter", "cache-info"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout[result.stdout.index("{"):])
    assert isinstance(data["adapters"]["hits"], int)
    assert isinstance(data["adapters"]["misses"], int)
    assert isinstance(data["adapters"]["last_rebuild_ms"], float)


def test_duplicate_statpack_entries_are_rejected(tmp_path: Path) -> None:
    pack = tmp_path / "duplicates.statpack"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr("metadata.yaml", "metadata:\n  id: sample\n")
        archive.writestr("runner.py", "")
        archive.writestr("schema/sections/metrics.yaml", "metrics: {}\n")
        with pytest.warns(UserWarning):
            archive.writestr("metadata.yaml", "metadata:\n  id: another\n")
    with pytest.raises(ValueError, match="Duplicate StatPack entry"):
        unpack_statpack(pack, tmp_path / "out")


def test_official_browser_origins_have_cors_without_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STATLINE_PORTAL_ORIGINS", raising=False)
    api = FastAPI()
    configure_portal_cors(api)
    with TestClient(api) as client:
        preflight = client.options(
            "/v4/score",
            headers={
                "Origin": "https://statline.dev",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "https://statline.dev"
        assert "access-control-allow-credentials" not in preflight.headers


def test_api_styled_docs_preserve_native_authorize() -> None:
    with TestClient(app) as client:
        response = client.get("/rtfm")
        assert response.status_code == 200
        assert 'src="/docs"' in response.text
        assert 'href="/redoc"' in response.text
        assert 'href="/openapi.json"' in response.text
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_inspection_api_still_requires_userbase_scope() -> None:
    with TestClient(app) as client:
        response = client.get("/v4/inspect/dataset", params={"dataset": "eba_s1_players.csv"})
        assert response.status_code == 401


def test_portal_generator_ignores_missing_optional_documentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing ARCHITECTURE.md must not prevent a portal rebuild."""
    scripts = Path(__file__).resolve().parents[1] / "portal" / "scripts"
    monkeypatch.setattr(sys, "path", [str(scripts), *sys.path])
    generator = importlib.import_module("generate")
    (tmp_path / "README.md").write_text("statline\n", encoding="utf-8")
    source_docs = generator.source_documents(tmp_path)
    assert [document["path"] for document in source_docs] == ["README.md"]
    assert len(source_docs[0]["sha256"]) == 64
