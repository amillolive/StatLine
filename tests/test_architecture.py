"""Structural invariants for the StatLine rebase."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import ast
import importlib
import importlib.metadata
from pathlib import Path
from typing import Any

import pytest
import statline
import statline.app.cli.main as cli_main
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


def test_cli_version_and_root_structure_are_clean() -> None:
    runner = CliRunner()
    version_result = runner.invoke(app, ["--version"])
    help_result = runner.invoke(app, ["--mode", "local", "--help"])

    assert version_result.exit_code == 0, version_result.output
    assert "CLI UX r1" in version_result.output
    assert "CLI UX vr1" not in version_result.output

    assert help_result.exit_code == 0, help_result.output
    assert "Core workflows" in help_result.output
    assert "Service & access" in help_result.output
    assert "Advanced" in help_result.output
    assert "os" in help_result.output
    assert "score" in help_result.output
    assert "adapter" in help_result.output
    assert "statpack" in help_result.output
    assert "system" in help_result.output
    assert "tools" in help_result.output

    # rc3 spellings still work, but the root help no longer presents two command languages.
    assert " interactive " not in help_result.output
    assert " adapters " not in help_result.output
    assert " map " not in help_result.output
    assert " calc " not in help_result.output
    assert " storage " not in help_result.output
    assert " weights " not in help_result.output


def test_local_connectivity_message_describes_choice_not_machine_offline() -> None:
    result = CliRunner().invoke(app, ["--mode", "local", "--no-timing", "adapter", "list"])
    assert result.exit_code == 0, result.output
    assert "SLAPI is disabled by --mode local" in result.output
    assert "Offline mode" not in result.output


def test_auto_mode_distinguishes_reachable_from_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tcp_probe(_url: str, _timeout: float = 1.5) -> bool:
        return True

    monkeypatch.setattr(cli_main, "_tcp_probe", fake_tcp_probe)
    monkeypatch.setattr(cli_main, "_has_apikey", lambda: False)

    original_get = cli_main._get_v3

    def fake_get(path: str, *args: object, **kwargs: Any) -> object:
        if path == "/v3/health":
            return {"ok": True}
        return original_get(path, *args, **kwargs)

    monkeypatch.setattr(cli_main, "_get_v3", fake_get)

    result = CliRunner().invoke(
        app,
        ["--mode", "auto", "--no-timing", "adapter", "list"],
    )

    assert result.exit_code == 0, result.output
    assert "SLAPI is reachable" in result.output
    assert "unauthenticated" in result.output
    assert "SLAPI ONLINE" not in result.output


def test_serve_omits_client_connectivity_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeUvicorn:
        @staticmethod
        def run(*args: object, **kwargs: object) -> None:
            calls.append((args, kwargs))

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", FakeUvicorn())

    def fail_tcp_probe(_url: str, _timeout: float = 1.5) -> bool:
        raise AssertionError("serve must not probe SLAPI")

    monkeypatch.setattr(cli_main, "_tcp_probe", fail_tcp_probe)

    result = CliRunner().invoke(
        app,
        [
            "--mode",
            "auto",
            "--no-timing",
            "serve",
            "--workers",
            "1",
            "--foreground",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls
    assert "LOCAL FALLBACK" not in result.output
    assert "SLAPI REMOTE" not in result.output
    assert "SLAPI is reachable" not in result.output
    assert "SLAPI is unavailable" not in result.output


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


def test_installer_strict_mode_keeps_inno_candidates_as_a_collection() -> None:
    text = (ROOT / "scripts" / "build-statline-installer.ps1").read_text(encoding="utf-8")

    find_iscc = text.split("function Find-Iscc", 1)[1]
    assignment = find_iscc.split("$candidates = @(", 1)[1].split("if ($candidates.Count", 1)[0]

    # The candidate pipeline must itself be wrapped in @(...), so a one-result
    # pipeline remains a collection under StrictMode and `.Count` is safe.
    assert assignment.lstrip().startswith("@(")
    assert ") | Where-Object" in assignment
    assert assignment.rstrip().endswith(")")
    assert "-m statline statpack run --pause" in text


def test_source_tree_version_falls_back_to_rc4(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_distribution(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr(importlib.metadata, "version", missing_distribution)
    reloaded = importlib.reload(statline)

    assert PACKAGE_VERSION == "4.0.0rc4"
    assert reloaded.__version__ == PACKAGE_VERSION


def test_moved_modules_resolve_package_paths() -> None:
    from statline.app.cli.main import LOG_DIR
    from statline.core.adapters import list_adapters

    assert LOG_DIR.parent == ROOT / "statline"

    adapters = list_adapters()
    assert "eba.players" in adapters
