"""Build a sanitized source ZIP or TAR with the verified static portal."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path

# Running `python scripts/build_release.py` puts scripts/ rather than the
# repository root on sys.path. Support that entry point and module execution.
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_release import scan_archive, sensitive_path  # noqa: E402

DIRECTORIES = {
    "statline",
    "scripts",
    "tests",
    "portal",
    "assets",
    "legal",
    "integrations",
    "installer",
    ".github",
    "deployment",
}
ROOT_FILES = {
    ".gitignore",
    "README.md",
    "HOWTO.md",
    "ARCHITECTURE.md",
    "LICENSE",
    "CLA.md",
    "TRADEMARK_POLICY.md",
    "pyproject.toml",
    "uv.lock",
    "mypy.ini",
    "mypy-core.ini",
    "mypy-nice.ini",
    "launch.sh",
    "launch.ps1",
    "RC6_CHANGES.md",
    "RC6_VERIFICATION.md",
    "RC6_BENCHMARKS.json",
}
BUILD_DIRECTORIES = {
    "dist",
    "build",
    "payload",
    "runtime",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    "reports",
    "test-results",
}


def release_files(source: Path) -> list[Path]:
    accepted: list[Path] = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if not path.is_file() or path.is_symlink() or sensitive_path(relative.as_posix()):
            continue
        if len(relative.parts) == 1:
            if relative.name in ROOT_FILES:
                accepted.append(path)
            continue
        if relative.parts[0] not in DIRECTORIES:
            continue
        # Only the freshly verified portal static export is a release build output.
        portal_build = relative.parts[:3] == ("portal", "dist", "client")
        if any(part in BUILD_DIRECTORIES for part in relative.parts[:-1]) and not portal_build:
            continue
        if path.suffix.lower() in {".exe", ".msi", ".whl", ".gz", ".tar", ".zip", ".prof"}:
            continue
        accepted.append(path)
    return sorted(accepted)


def main() -> int:
    source = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    target = args.output.resolve()
    if target.is_relative_to(source):
        parser.error("Write the release archive outside the source tree.")
    if not (source / "portal/dist/client/index.html").is_file():
        parser.error("Build the portal first.")
    paths = release_files(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix == ".tar":
        with tarfile.open(target, "w") as archive:
            for path in paths:
                item = tarfile.TarInfo("StatLine/" + path.relative_to(source).as_posix())
                item.size = path.stat().st_size
                item.mode = 0o755 if path.suffix == ".sh" else 0o644
                with path.open("rb") as content:
                    archive.addfile(item, content)
    else:
        with zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as zip_archive:
            for path in paths:
                relative = path.relative_to(source)
                zip_item = zipfile.ZipInfo(
                    "StatLine/" + relative.as_posix(), date_time=(2026, 1, 1, 0, 0, 0)
                )
                zip_item.compress_type = zipfile.ZIP_DEFLATED
                zip_item.external_attr = (0o100755 if path.suffix == ".sh" else 0o100644) << 16
                zip_archive.writestr(zip_item, path.read_bytes())
    problems = scan_archive(target)
    if problems:
        target.unlink()
        raise SystemExit("Release rejected: " + ", ".join(problems[:10]))
    print(f"Built and scanned {target.name}: {len(paths)} files, {target.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
