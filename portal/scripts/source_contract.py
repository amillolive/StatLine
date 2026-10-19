"""Fingerprint source inputs for saved contracts without including generated output."""

from __future__ import annotations

import hashlib
from pathlib import Path


def source_fingerprint(source: Path) -> str:
    files = {
        source / name
        for name in (
            "pyproject.toml",
            "README.md",
            "HOWTO.md",
            "ARCHITECTURE.md",
            "launch.sh",
            "launch.ps1",
            "portal/package.json",
            "portal/pnpm-lock.yaml",
        )
    }
    extensions = {
        ".py",
        ".pyi",
        ".yaml",
        ".yml",
        ".csv",
        ".md",
        ".ts",
        ".tsx",
        ".mjs",
        ".sh",
        ".ps1",
        ".css",
    }
    for directory in (
        "statline",
        "packs",
        "scripts",
        "portal/app",
        "portal/components",
        "portal/lib",
        "portal/scripts",
    ):
        files.update(
            path
            for path in (source / directory).rglob("*")
            if path.is_file()
            and path.suffix in extensions
            and not {"secrets", "log", "__pycache__", "workspace", "retired"}.intersection(
                path.relative_to(source).parts
            )
        )
    fingerprint = hashlib.sha256()
    for path in sorted(files):
        if path.is_file():
            name = path.relative_to(source).as_posix().encode()
            data = path.read_bytes()
            fingerprint.update(len(name).to_bytes(8, "big") + name)
            fingerprint.update(len(data).to_bytes(8, "big") + data)
    return fingerprint.hexdigest()
