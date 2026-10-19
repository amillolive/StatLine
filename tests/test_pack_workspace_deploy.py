"""Canonical org deployments and privacy are rc6 release blockers."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.datasets import dataset_root, list_datasets, resolve_dataset
from statline.core.statpacks import (
    deploy_statpack,
    inspect_statpack,
    manifest_statpack,
)
from statline.sdk import run_statpack

ROOT = Path(__file__).resolve().parents[1]


def test_org_data_catalog_ignores_workspace_and_pack_internal_datasets() -> None:
    paths = list_datasets()
    assert paths and all("/data/" in f"/{name}/" for name in paths)
    assert not any("/workspace/" in name or "/dataset/" in name for name in paths)
    canonical = resolve_dataset("basketball/eba/data/players/EBA_Elevate302/eba_s1_players.csv")
    assert canonical == resolve_dataset("EBA_Elevate302/eba_s1_players.csv")
    assert canonical.is_relative_to(dataset_root())


def test_manifest_and_deploy_non_pairwise_preserve_workspace(tmp_path: Path) -> None:
    source = ROOT / "packs/basketball/eba/players/eba_players.yaml"
    archive = manifest_statpack(source, tmp_path / "players.statpack")
    base = tmp_path / "packs"
    workspace = base / "basketball/eba/workspace/players"
    workspace.mkdir(parents=True)
    sentinel = workspace / "scratch.yaml"
    sentinel.write_text("intentionally unvalidated\n")

    installed = deploy_statpack(archive, "basketball", "eba", "players", packs_root=base)
    assert (installed / "players.yaml").exists()
    assert (installed / "schema/sections/metrics.yaml").exists()
    assert (base / "basketball/eba/players.statpack").is_file()
    assert (base / "basketball/eba/data/players/EBA_Elevate302/eba_hybrid_s1_players.csv").is_file()
    assert compile_adapter(load_spec(installed / "players.yaml")).key == "eba.players"
    assert sentinel.read_text() == "intentionally unvalidated\n"
    with pytest.raises(FileExistsError):
        deploy_statpack(archive, "basketball", "eba", "players", packs_root=base)
    assert deploy_statpack(archive, "basketball", "eba", "players", overwrite=True, packs_root=base) == installed
    assert sentinel.exists()


def test_native_pairwise_pack_deploy_preserves_portability(tmp_path: Path) -> None:
    source = ROOT / "examples/native_pairwise_v1/pairwise-elo.yaml"
    archive = manifest_statpack(source, tmp_path / "elo.statpack")
    installed = deploy_statpack(archive, "trackmania", "sample", "elo", packs_root=tmp_path / "packs")
    assert inspect_statpack(archive)["metadata"]["id"] == "example.pairwise_elo"
    assert (installed / "inputs/next-match.statpack").is_file()
    assert (installed / "inputs/previous-match.statpack").is_file()
    assert compile_adapter(load_spec(installed / "elo.yaml")).native_pairwise is not None
    stdout, stderr = io.StringIO(), io.StringIO()
    assert run_statpack(installed, ["--format", "csv", "--no-timing"], stdout=stdout, stderr=stderr) == 0, stderr.getvalue()
    assert "newElo" in stdout.getvalue()


@pytest.mark.parametrize("slug", ["../escape", "/abs", "WORKSPACE", "bad.name", "data", "workspace", "x/y"])
def test_deploy_rejects_unsafe_layout_segments(slug: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="slug|reserved"):
        deploy_statpack(tmp_path / "missing.statpack", "trackmania", "league", slug, packs_root=tmp_path)


def test_portal_no_device_prompts_and_real_brand_icon() -> None:
    portal = ROOT / "portal"
    # Browser-side code never exposes the prior private-key file picker or
    # device signing pathway (no OS integration, local network or app access).
    for filename in ("components/statline/dashboard.tsx", "lib/gateway-client.ts", "lib/api-policy.ts"):
        source = (portal / filename).read_text()
        assert 'type="file"' not in source
        assert "importDeviceKey(" not in source
        assert "deviceProof(" not in source
        for forbidden in ("navigator.usb", "navigator.bluetooth", "navigator.hid", "navigator.serial", "getUserMedia("):
            assert forbidden not in source
    # There must be no bypass API request and no browser Clipboard permission.
    assert "gatewayRequest<unknown>" in (portal / "components/statline/live-score.tsx").read_text()
    assert "navigator." not in (portal / "components/statline/contact.tsx").read_text()
    assert "navigator." not in (portal / "components/statline/code-block.tsx").read_text()
    policy = (portal / "lib/api-policy.ts").read_text()
    assert 'url.origin === "https://api.statline.dev"' in policy
    assert "localPortal && localApi" in policy
    assert (portal / "public/favicon.png").read_bytes() == (ROOT / "assets/statline-icon-main.png").read_bytes()
    assert '"/favicon.png"' in (portal / "app/layout.tsx").read_text()
