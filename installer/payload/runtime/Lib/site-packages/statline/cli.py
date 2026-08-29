"""StatLine command-line entry point."""

from __future__ import annotations

import sys

import click

from statline.app.cli.main import app


def main() -> None:
    try:
        app()
    except click.exceptions.Exit as error:
        raise SystemExit(error.exit_code) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
