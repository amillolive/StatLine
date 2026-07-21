"""End-to-end StatPack lifecycle tests."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from statline.app.cli.main import app
from statline.core.adapters.load import load_spec
from statline.core.statpacks import (
    inspect_statpack,
    manifest_statpack,
    pack_statpack,
    render_statpack,
    unpack_statpack,
)
from statline.core.statpacks.runtime import run_statpack
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]
EBA_PLAYERS = ROOT / "statline" / "core" / "adapters" / "schemas" / "current" / "eba_players.yaml"


def test_manifest_render_unpack_and_run_round_trip(tmp_path: Path) -> None:
    pack = manifest_statpack(EBA_PLAYERS, tmp_path / "eba_players.statpack")
    info = inspect_statpack(pack)

    assert info["metadata"]["id"] == "eba.players"
    assert info["runnable"] is True
    assert "EBA_Elevate302/eba_hybrid_s1_players.csv" in info["datasets"]
    assert info["sections"] == sorted(info["sections"])

    source = unpack_statpack(pack, tmp_path / "source")
    rendered = render_statpack(pack, tmp_path / "rendered.yaml")
    text = rendered.read_text(encoding="utf-8")

    assert "AUTO-GENERATED RUNTIME ADAPTER — DO NOT EDIT DIRECTLY" in text
    assert "# Version: 3.0.1" in text
    assert load_spec(rendered).metadata.id == load_spec(EBA_PLAYERS).metadata.id == "eba.players"

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run_statpack(
        source,
        ["--limit", "1", "--format", "json"],
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue())

    assert exit_code == 0, stderr.getvalue()
    assert len(payload) == 1
    assert "PRI" in payload[0]


def test_edited_source_directory_can_be_repacked(tmp_path: Path) -> None:
    pack = manifest_statpack(EBA_PLAYERS, tmp_path / "initial.statpack")
    source = unpack_statpack(pack, tmp_path / "editable")
    metadata = source / "metadata.yaml"
    metadata.write_text(
        metadata.read_text(encoding="utf-8").replace(
            "title: EBA/Elevate302 Players", "title: Edited Players"
        ),
        encoding="utf-8",
    )

    rebuilt = pack_statpack(source, tmp_path / "rebuilt.statpack")

    assert inspect_statpack(rebuilt)["metadata"]["title"] == "Edited Players"


def test_unpack_rejects_path_traversal(tmp_path: Path) -> None:
    pack = tmp_path / "unsafe.statpack"
    with zipfile.ZipFile(pack, "w") as archive:
        archive.writestr(
            "metadata.yaml", "metadata: {id: unsafe, title: Unsafe, version: 1, author: Test}\n"
        )
        archive.writestr("runner.py", "pass\n")
        archive.writestr("schema/sections/buckets.yaml", "buckets: {x: {}}\n")
        archive.writestr("../escape.txt", "nope")

    with pytest.raises(ValueError, match="Unsafe StatPack (path|entry)"):
        unpack_statpack(pack, tmp_path / "out")


def test_cli_scores_dotted_adapter_id_and_exposes_statpack_commands() -> None:
    runner = CliRunner()
    score = runner.invoke(
        app,
        ["--mode", "local", "score", "--adapter", "eba.players", "--limit", "1", "--fmt", "json"],
    )
    help_result = runner.invoke(app, ["--mode", "local", "statpack", "--help"])

    assert score.exit_code == 0, score.output
    assert '"pri"' in score.output
    assert help_result.exit_code == 0, help_result.output
    for command in ("manifest", "render", "unpack", "pack", "inspect"):
        assert command in help_result.output
