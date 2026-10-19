"""Declarative pairwise tests: no handwritten Elo Python, old PRI isolated."""

from __future__ import annotations

from pathlib import Path

import pytest
from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.relate import PairwiseError, load_pairwise_rows, score_pairwise
from statline.core.scoring.score import calculate_pri
from statline.core.statpacks import manifest_statpack, render_statpack
from statline.core.types.adapters import CompiledAdapter

ROOT = Path(__file__).resolve().parents[1] / "examples" / "pairwise_race"


def adapter() -> CompiledAdapter:
    return compile_adapter(load_spec(ROOT / "race_elo.yaml"))


def source_rows() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return (load_pairwise_rows(ROOT / "ratings.json"), load_pairwise_rows(ROOT / "results.json"))


def test_evaluator_reproduces_six_driver_javascript_elo() -> None:
    old, new = source_rows()
    out = score_pairwise(adapter(), old=old, new=new)
    assert [round(r["output"]["eloChange"], 2) for r in out] == [
        23.23,
        8.82,
        7.92,
        -7.92,
        -17.13,
        -45.13,
    ]
    assert [r["output"]["newElo"] for r in out] == [1623, 1609, 1508, 1492, 1383, 1355]
    assert [r["scores"]["ELO"] for r in out] == [1623, 1609, 1508, 1492, 1383, 1355]


def test_selected_head_to_head_does_not_change_group_math() -> None:
    old, new = source_rows()
    selected = score_pairwise(
        adapter(), old=old, new=new, selected_ids=["DriverA", "DriverB"], include_pairs=True
    )
    assert len(selected) == 2
    assert selected[0]["output"]["eloChange"] == 23.23
    assert len(selected[0]["pairs"]) == 5


def test_missing_old_and_duplicate_ids_rejected() -> None:
    old, new = source_rows()
    with pytest.raises(PairwiseError, match="previous-state"):
        score_pairwise(adapter(), old=old[:-1], new=new)
    with pytest.raises(PairwiseError, match="Duplicate old"):
        score_pairwise(adapter(), old=old + old[:1], new=new)
    with pytest.raises(PairwiseError, match="Duplicate entity"):
        score_pairwise(adapter(), old=old, new=new + new[:1])


def test_finite_group_limits_and_single_participant() -> None:
    old, new = source_rows()
    with pytest.raises(PairwiseError, match="limit"):
        score_pairwise(adapter(), old=old, new=new, max_pairs=3)
    one = score_pairwise(adapter(), old=old, new=new[:1])
    assert one[0]["output"]["newElo"] == 1600
    assert one[0]["output"]["eloChange"] == 0


def test_pairwise_only_adapter_rejects_ordinary_pri_path() -> None:
    with pytest.raises(ValueError, match="score_pairwise"):
        calculate_pri([{}], adapter())


def test_statpack_section_survives_manifest_and_render(tmp_path: Path) -> None:
    # manifest expects dataset metadata; pairwise source defaults remain external.
    example = ROOT / "race_elo.yaml"
    pack = manifest_statpack(example, tmp_path / "elo.statpack")
    rendered = render_statpack(pack, tmp_path / "elo_rendered.yaml")
    spec = load_spec(rendered)
    assert spec.score_profiles["ELO"].kind == "pairwise"
    assert "race_elo" in spec.pairwise


def test_bad_formula_fails_on_execution() -> None:
    from dataclasses import replace

    compiled = adapter()
    spec = compiled.pairwise["race_elo"]
    bad = replace(spec, calculate={"pair_delta": "__import__('os')"})
    compiled = replace(compiled, pairwise={"race_elo": bad})
    old, new = source_rows()
    with pytest.raises(PairwiseError, match="Function is not permitted"):
        score_pairwise(compiled, old=old, new=new)


def test_profile_may_name_pairwise_output_pri() -> None:
    from dataclasses import replace

    prepared = adapter()
    current = prepared.score_profiles["ELO"]
    prepared = replace(prepared, score_profiles={"PRI": replace(current, value="newElo")})
    old, new = source_rows()
    result = score_pairwise(prepared, old=old, new=new[:1])
    assert result[0]["scores"]["PRI"] == 1600
    assert result[0]["pri"] == 1600


def test_mixed_adapter_preserves_ordinary_affine() -> None:
    from dataclasses import replace

    from statline.core.adapters.compile import compile_adapter
    from statline.core.types.adapters import ScoreProfileSpec

    legacy = compile_adapter(
        load_spec(
            Path(__file__).resolve().parents[1]
            / "packs"
            / "basketball"
            / "eba"
            / "players"
            / "eba_players.yaml"
        )
    )
    prior = calculate_pri(
        {
            **{metric.key: 10 for metric in legacy.metrics},
            **{eff.key: 10 for eff in legacy.efficiency},
        },
        legacy,
    )
    model = adapter()
    mixed = replace(
        legacy,
        pairwise=model.pairwise,
        score_profiles={
            **legacy.score_profiles,
            "ELO": ScoreProfileSpec(kind="pairwise", model="race_elo", value="newElo"),
        },
    )
    now = calculate_pri(
        {
            **{metric.key: 10 for metric in legacy.metrics},
            **{eff.key: 10 for eff in legacy.efficiency},
        },
        mixed,
    )
    assert prior == now
    with pytest.raises(ValueError, match="score_pairwise"):
        calculate_pri(
            {
                **{metric.key: 10 for metric in legacy.metrics},
                **{eff.key: 10 for eff in legacy.efficiency},
            },
            mixed,
            profiles=["ELO"],
        )


def test_two_distinct_race_groups_are_evaluated_independently() -> None:
    old, new = source_rows()
    other = [dict(x, race_id="2") for x in new]
    out = score_pairwise(adapter(), old=old, new=new + other)
    assert len(out) == 12
    assert out[0]["output"] == out[6]["output"]


def test_statpack_dataset_used_without_running_embedded_python(tmp_path: Path) -> None:
    import zipfile

    file = tmp_path / "ratings.statpack"
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("metadata.yaml", "metadata: {id: x, dataset: rating.csv}\n")
        z.writestr("dataset/rating.csv", "id,eloScore,roundsPlayed\nDriverA,1600,50\n")
        z.writestr("runner.py", "raise RuntimeError('must never run')\n")
    assert load_pairwise_rows(file) == [{"id": "DriverA", "eloScore": 1600, "roundsPlayed": 50}]
