"""Source-tree fallback package version.

Distribution metadata remains authoritative when StatLine is installed.  This
constant exists so an extracted source tree can still report the release it was
built from instead of falling back to ``0+unknown``.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PACKAGE_VERSION = "4.0.0rc7"


def runtime_version(module_file: str) -> str:
    """Prefer the source checkout version over unrelated installed package metadata.

    A developer may have an older StatLine wheel or editable install on sys.path.
    Distribution metadata is authoritative only for an installed wheel, not when
    importing this checkout directly.
    """
    if (Path(module_file).resolve().parent.parent / "pyproject.toml").is_file():
        return PACKAGE_VERSION
    try:
        return version("statline")
    except PackageNotFoundError:
        return PACKAGE_VERSION


__all__ = ["PACKAGE_VERSION", "runtime_version"]
