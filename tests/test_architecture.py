"""Structural invariants for the StatLine rebase."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import ast
import importlib
import importlib.metadata
from pathlib import Path

import pytest
import statline
from statline._version import PACKAGE_VERSION
from statline.app.cli.main import app
from statline.core.datasets import dataset_root, list_datasets
from typer.testing import CliRunner

ROOT = Path(__file__).parent.parent
PYTHON_ROOTS = (ROOT / "statline", ROOT / "scripts", ROOT / "tests", ROOT / "typings")
REMOVED_NAMESPACES = ("slapi", "tui", "utils", "services", "data")


def _python_files() -> list[Path]:
    return sorted(
        path
        for root in PYTHON_ROOTS
        for pattern in ("*.py", "*.pyi")
        for path in root.rglob(pattern)
        if "__pycache__" not in path.parts
    )


def _top_level_owners(
    tree: ast.Module,
) -> tuple[list[ast.FunctionDef | ast.AsyncFunctionDef], list[ast.ClassDef]]:
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    return functions, classes


def test_every_python_file_has_one_ownership_kind() -> None:
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions, classes = _top_level_owners(tree)
        assert not (functions and classes), f"mixed definitions/functions: {path.relative_to(ROOT)}"
        if not functions and not classes:
            assert not any(isinstance(node, ast.Lambda) for node in ast.walk(tree)), (
                f"code-only file creates a function: {path.relative_to(ROOT)}"
            )


def test_cli_entrypoint_stays_under_fifty_lines() -> None:
    lines = (ROOT / "statline" / "cli.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 50


def test_removed_namespaces_are_not_present() -> None:
    package = ROOT / "statline"
    for name in REMOVED_NAMESPACES:
        assert not (package / name).exists(), f"legacy namespace remains: statline/{name}"


def test_packaged_dataset_registry_is_canonical() -> None:
    names = list_datasets()
    assert dataset_root() == ROOT / "statline" / "core" / "datasets"
    assert names
    assert all((dataset_root() / name).is_file() for name in names)


def test_local_cli_prints_main_banner_once() -> None:
    result = CliRunner().invoke(app, ["--mode", "local", "adapters"])
    assert result.exit_code == 0, result.output
    assert result.output.count("CLI UX") == 1


def test_cli_version_and_os_surface_names_are_clean() -> None:
    runner = CliRunner()
    version_result = runner.invoke(app, ["--version"])
    help_result = runner.invoke(app, ["--mode", "local", "--help"])

    assert version_result.exit_code == 0, version_result.output
    assert "CLI UX r1" in version_result.output
    assert "CLI UX vr1" not in version_result.output
    assert help_result.exit_code == 0, help_result.output
    assert " os " in help_result.output
    assert " launch " not in help_result.output


def test_cli_hides_deprecated_adapters_but_allows_explicit_local_path() -> None:
    runner = CliRunner()
    listed = runner.invoke(app, ["--mode", "local", "--no-timing", "adapters"])
    deprecated = ROOT / "statline" / "core" / "adapters" / "schemas" / "deprecated" / "demo.yaml"
    scored = runner.invoke(
        app,
        [
            "--mode",
            "local",
            "--no-timing",
            "score",
            "--adapter",
            str(deprecated),
            "DEMO/demo.csv",
            "--limit",
            "1",
            "--fmt",
            "json",
        ],
    )

    assert listed.exit_code == 0, listed.output
    assert "demo" not in listed.output.casefold()
    assert "valorant" not in listed.output.casefold()
    assert scored.exit_code == 0, scored.output
    assert '"pri"' in scored.output


def test_source_tree_version_falls_back_to_rc3(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_distribution(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing_distribution)
    reloaded = importlib.reload(statline)

    assert PACKAGE_VERSION == "4.0.0rc3"
    assert reloaded.__version__ == PACKAGE_VERSION


def test_moved_modules_resolve_package_paths() -> None:
    from statline.app.cli.main import LOG_DIR
    from statline.core.adapters import list_adapters

    assert LOG_DIR.parent == ROOT / "statline"

    adapters = list_adapters()
    assert "eba.players" in adapters
