"""Native v1 pairwise integration uses the normal StatPack SDK and HTTP gateway."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.relate.errors import PairwiseError
from statline.core.relate.native import read_inputs, score_native_pairwise
from statline.core.statpacks import (
    inspect_statpack,
    manifest_statpack,
    pack_statpack,
    render_statpack,
    unpack_statpack,
)
from statline.sdk import run_statpack

ROOT = Path(__file__).resolve().parents[1] / "examples" / "native_pairwise_v1"


def spec():
    return compile_adapter(load_spec(ROOT / "pairwise-elo.yaml")).native_pairwise


def test_exact_typescript_parity_and_join_independent_of_row_order():
    model = spec()
    assert model is not None
    a, b = read_inputs(model, ROOT)
    expected = list(csv.DictReader((ROOT / "expected.csv").open(encoding="utf-8")))
    results = score_native_pairwise(model, a, b)
    assert [row["playerId"] for row in results] == [row["playerId"] for row in expected]
    for row, reference in zip(results, expected, strict=True):
        assert row == {
            "playerId": reference["playerId"],
            "oldElo": int(reference["oldElo"]),
            "newElo": int(reference["newElo"]),
            "eloChange": float(reference["eloChange"]),
        }
    assert results == score_native_pairwise(model, a, list(reversed(b)))


def test_single_driver_duplicate_and_missing_history():
    model = spec()
    assert model is not None
    a, b = read_inputs(model, ROOT)
    only = score_native_pairwise(model, a[:1], b)
    assert only[0]["newElo"] == 1600
    assert only[0]["eloChange"] == 0
    with pytest.raises(PairwiseError, match="missing from b"):
        score_native_pairwise(model, a, b[:-1])
    with pytest.raises(PairwiseError, match="Duplicate identity.*in a"):
        score_native_pairwise(model, [*a, a[0]], b)
    with pytest.raises(PairwiseError, match="Duplicate identity.*in b"):
        score_native_pairwise(model, a, [*b, b[0]])
    with pytest.raises(PairwiseError, match="limit"):
        score_native_pairwise(model, a, b, max_pairs=2)


def test_statpack_manifest_unpack_render_run_and_csv(tmp_path: Path):
    pack = manifest_statpack(ROOT / "pairwise-elo.yaml", tmp_path / "compare.statpack")
    contents = inspect_statpack(pack)["files"]
    assert "inputs/next-match.statpack" in contents
    assert "inputs/previous-match.statpack" in contents
    assert "schema/sections/expressions.yaml" in contents
    root = unpack_statpack(pack, tmp_path / "unpacked")
    assert pack_statpack(root, tmp_path / "roundtrip.statpack").exists()
    assert render_statpack(pack, tmp_path / "rendered.yaml").exists()
    stdout, stderr = io.StringIO(), io.StringIO()
    status = run_statpack(root, ["--format", "csv", "--no-timing"], stdout=stdout, stderr=stderr)
    assert status == 0, stderr.getvalue()
    assert stdout.getvalue().replace("\r\n", "\n") == (ROOT / "expected.csv").read_text().replace(
        "\r\n", "\n"
    )


def test_profile_can_name_native_result_without_changing_math(tmp_path: Path):
    text = (ROOT / "pairwise-elo.yaml").read_text(encoding="utf-8")
    text += "\nscore_profiles:\n  ELO: {kind: pairwise, model: native, value: newElo}\n"
    target = tmp_path / "adapter.yaml"
    target.write_text(text, encoding="utf-8")
    adapter = compile_adapter(load_spec(target))
    assert adapter.native_pairwise is not None
    a, b = read_inputs(adapter.native_pairwise, ROOT)
    rows = score_native_pairwise(adapter.native_pairwise, a, b, profiles={"ELO": "newElo"})
    assert rows[0]["ELO"] == rows[0]["newElo"] == 1623


def test_path_escape_and_invalid_native_structure_rejected(tmp_path: Path):
    text = (ROOT / "pairwise-elo.yaml").read_text(encoding="utf-8")
    for change in ["../previous-match.statpack", "/etc/passwd", "C:/secrets.statpack"]:
        corrupted = text.replace("inputs/previous-match.statpack", change)
        file = tmp_path / "invalid.yaml"
        file.write_text(corrupted, encoding="utf-8")
        with pytest.raises(
            (ValueError, PairwiseError), match="path|escapes|not found|Unsafe|Invalid"
        ):
            manifest_statpack(file, tmp_path / "invalid.statpack")
    file = tmp_path / "unknown.yaml"
    file.write_text(text.replace("  aggregate: sum", "  aggregate: eval"), encoding="utf-8")
    with pytest.raises(ValueError, match="aggregate"):
        load_spec(file)


def test_http_gateway_native_model_is_adapter_controlled(monkeypatch):
    from statline.gateway.http import routes_api
    from statline.gateway.http.models import PairwiseIn
    from statline.gateway.http.routes_api import _pairwise_request

    adapter = compile_adapter(load_spec(ROOT / "pairwise-elo.yaml"))
    native_spec = adapter.native_pairwise
    assert native_spec is not None
    a, b = read_inputs(native_spec, ROOT)
    monkeypatch.setattr(routes_api, "get_adapter", lambda name: adapter)
    result = _pairwise_request(PairwiseIn(adapter=adapter.key, a=a, b=b))
    assert result["new_count"] == 6
    assert result["results"][0]["newElo"] == 1623
