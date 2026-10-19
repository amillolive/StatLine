"""Deprecated entrypoint shim; implementation lives in scripts.serve."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> int:
    """Delegate after adding the repository root to the import path."""
    from scripts.serve import main as serve_main

    return serve_main()


if __name__ == "__main__":
    raise SystemExit(main())
