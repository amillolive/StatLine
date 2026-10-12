"""Reject private runtime artifacts in source archives, sdists, and wheels."""

from __future__ import annotations

import argparse
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PRIVATE_DIRECTORIES = {
    ".git",
    ".db",
    "secrets",
    ".secrets",
    "secret",
    ".secret",
    ".device",
    ".state",
    ".statline",
    ".local-state",
    "node_modules",
    ".venv",
    "__pycache__",
    ".next",
    ".wrangler",
}
PRIVATE_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".duckdb",
    ".pyc",
    ".log",
    ".pid",
}
SECRET_NAME = re.compile(
    r"(?:credentials|secrets|apikey|api[_-]key|device[_-]?key|reg[_-]?key|registration|enroll|token).*(?:\.json|\.toml|\.yaml|\.yml|\.ini)$",
    re.IGNORECASE,
)
PRIVATE_KEY = re.compile(rb"(?m)^-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{16,}")


def sensitive_path(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return True
    if any(part.lower() in PRIVATE_DIRECTORIES for part in path.parts):
        return True
    base = path.name.lower()
    if base in {".keys.json", ".coverage", "pypi.ini"} or base.endswith(".tsbuildinfo"):
        return True
    if (base == ".env" or base.startswith(".env.")) and base not in {".env.example", ".env.sample"}:
        return True
    return path.suffix.lower() in PRIVATE_SUFFIXES or SECRET_NAME.search(base) is not None


def scan_archive(path: Path) -> list[str]:
    problems: list[str] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for item in archive.infolist():
                if (
                    sensitive_path(item.filename)
                    or (item.external_attr >> 16) & 0o170000 == 0o120000
                ):
                    problems.append(item.filename)
                elif (
                    not item.is_dir()
                    and item.file_size < 5_000_000
                    and PRIVATE_KEY.search(archive.read(item))
                ):
                    problems.append(item.filename + " (private-key content)")
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as tar_archive:
            for tar_member in tar_archive:
                if (
                    sensitive_path(tar_member.name)
                    or tar_member.issym()
                    or tar_member.islnk()
                    or tar_member.isdev()
                ):
                    problems.append(tar_member.name)
                elif tar_member.isfile() and tar_member.size < 5_000_000:
                    content = tar_archive.extractfile(tar_member)
                    if content is not None and PRIVATE_KEY.search(content.read()):
                        problems.append(tar_member.name + " (private-key content)")
    else:
        raise ValueError("Unsupported archive: " + str(path))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args()
    failed = False
    for path in args.archives:
        problems = scan_archive(path)
        if problems:
            failed = True
            print(f"REJECTED {path.name}: {len(problems)} sensitive or unsafe entries")
            for name in problems[:20]:
                print("  " + name)
        else:
            print(
                "PASS " + path.name + ": no sensitive runtime paths or private-key material found"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
