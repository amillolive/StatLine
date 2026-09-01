"""Source-tree fallback package version.

Distribution metadata remains authoritative when StatLine is installed.  This
constant exists so an extracted source tree can still report the release it was
built from instead of falling back to ``0+unknown``.
"""

PACKAGE_VERSION = "4.0.0rc5"

__all__ = ["PACKAGE_VERSION"]
