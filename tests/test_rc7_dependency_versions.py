"""rc7 dependency and version-source regression coverage."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx2
import pytest
import statline
from statline import _version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def test_rc7_bundles_httpx2_as_a_base_dependency() -> None:
    source = Path(__file__).resolve().parents[1]
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert "httpx2>=2.0,<3.0" in project["dependencies"]
    assert hasattr(httpx2, "Client")


def test_rc7_source_version_is_consistent() -> None:
    source = Path(__file__).resolve().parents[1]
    project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert _version.PACKAGE_VERSION == project["version"]
    assert statline.__version__ == project["version"]


def test_checkout_version_wins_over_older_installed_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stale_version(_name: str) -> str:
        return "3.0.0"

    monkeypatch.setattr(_version, "version", stale_version)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").write_text("[project]\nname='statline'\n", encoding="utf-8")
    assert (
        _version.runtime_version(str(checkout / "statline" / "__init__.py"))
        == _version.PACKAGE_VERSION
    )
    assert (
        _version.runtime_version(str(tmp_path / "site-packages" / "statline" / "__init__.py"))
        == "3.0.0"
    )
