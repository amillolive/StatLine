"""Release migration contracts for resource paths, formula caching and browser privacy."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.adapters.paths import adapter_schema_root, iter_adapter_paths
from statline.core.datasets import dataset_root, list_datasets
from statline.core.formula import get_formula_engine

ROOT = Path(__file__).resolve().parents[1]


def test_single_formula_engine_reuses_plans_but_never_caches_row_values() -> None:
    engine = get_formula_engine()
    assert engine is get_formula_engine()
    formula = engine.compile("x * 2")
    assert formula is engine.compile("x * 2")
    assert formula({"x": 2}) == 4
    assert formula({"x": 9}) == 18
    variables = {"amount": "x * 2", "result": "amount + 1"}
    plan = engine.compile_variables(variables)
    assert plan is engine.compile_variables(dict(variables))
    assert plan({"x": 1}) == {"amount": 2, "result": 3}
    assert plan({"x": 8}) == {"amount": 16, "result": 17}


def test_pack_discovery_excludes_workspace_and_retired_but_direct_paths_work(
    tmp_path: Path,
) -> None:
    assert adapter_schema_root() == ROOT / "packs"
    assert dataset_root() == ROOT / "packs"
    assert list_datasets()
    paths = list(iter_adapter_paths())
    assert len(paths) >= 4
    assert not any({"retired", "workspace", "db"} & set(path.parts) for path in paths)
    assert load_spec(ROOT / "packs/retired/demo.yaml").metadata.id
    workspace = tmp_path / "custom.yaml"
    workspace.write_text(
        (ROOT / "packs/basketball/eba/teams/eba_teams.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    assert compile_adapter(load_spec(workspace)).key == "eba.teams"


def test_bad_adapter_formula_is_rejected_at_compilation() -> None:
    from dataclasses import replace

    from statline.core.types.adapters import SourceSpec

    spec = load_spec("eba.teams")
    invalid_metric = replace(spec.metrics[0], source=SourceSpec(kind="expr", expr="(1+2"))
    bad = replace(spec, metrics=[invalid_metric, *spec.metrics[1:]])
    with pytest.raises(ValueError, match="Invalid adapter expression"):
        compile_adapter(bad)


def test_all_active_yaml_numeric_formulas_are_parseable() -> None:
    for path in iter_adapter_paths():
        adapter = load_spec(path)
        for efficiency in adapter.efficiency:
            ast.parse(efficiency.make, mode="eval")
            ast.parse(efficiency.attempt, mode="eval")


def test_public_portal_does_not_auto_probe_gateway_or_local_network() -> None:
    content = (ROOT / "portal/components/statline/api-context.tsx").read_text()
    assert "window.setInterval" not in content
    assert "Local API access is blocked on a public site" in content
    assert "void refresh();\n    const interval" not in content
    demo = (ROOT / "portal/components/statline/engine-demo.tsx").read_text()
    assert "selectedProfile={activeProfile}" in demo
    assert "onProfileChange={setProfile}" in demo
    assert "Why {rating}?" in demo
