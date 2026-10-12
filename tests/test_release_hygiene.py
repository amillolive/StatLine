"""Archive checks detect private files without exposing their contents."""

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.check_release import scan_archive, sensitive_path


@pytest.mark.parametrize(
    "path",
    [
        "StatLine/.git/config",
        "StatLine/.db/auth.db",
        "StatLine/statline/secrets/DEVICEKEY",
        "StatLine/statline/.keys.json",
        "StatLine/.env.local",
        "StatLine/credentials.json",
        "StatLine/node_modules/a.js",
        "../outside",
        "StatLine/private.key",
    ],
)
def test_sensitive_paths_are_rejected(path: str) -> None:
    assert sensitive_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "StatLine/portal/.env.example",
        "StatLine/statline/gateway/auth/service.py",
        "StatLine/portal/public/downloads/openapi.json",
        "StatLine/statline/core/adapters/schemas/current/eba_players.yaml",
    ],
)
def test_source_and_public_examples_are_allowed(path: str) -> None:
    assert not sensitive_path(path)


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_scanner_catches_material_outside_the_expected_secret_folder(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / ("fixture." + kind)
    contents = {
        "StatLine/README.md": b"Public source",
        "StatLine/.db/auth.db": b"synthetic",
        "StatLine/public/config.txt": b"-----BEGIN PRIVATE KEY-----\nABCDEFGHIJKLMNOP0123456789\n-----END PRIVATE KEY-----\n",
    }
    if kind == "zip":
        with zipfile.ZipFile(path, "w") as zipped:
            for name, content in contents.items():
                zipped.writestr(name, content)
    else:
        with tarfile.open(path, "w") as archive:
            for name, content in contents.items():
                item = tarfile.TarInfo(name)
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
    assert scan_archive(path) == [
        "StatLine/.db/auth.db",
        "StatLine/public/config.txt (private-key content)",
    ]


def test_cli_reader_expands_compact_details_without_changing_mapping() -> None:
    from statline.app.cli.main import _score_response_data

    response = {
        "shared": {"weights": {"a": 1}},
        "results": [{"pri": 99, "source_index": 4}],
        "mapped": [{"a": 3}],
    }
    assert _score_response_data(response, one=True) == {
        "weights": {"a": 1},
        "pri": 99,
        "source_index": 4,
    }
    assert _score_response_data(response, one=False, mapped=True) == [{"a": 3}]
