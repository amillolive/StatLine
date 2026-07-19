"""StatLine command-line entry point."""

from __future__ import annotations

import sys

import click
import typer

from statline.app.cli.main import app


def main() -> None:
    try:
        app()
    except click.exceptions.Exit:
        raise
    except KeyboardInterrupt:
        raise typer.Exit(code=130)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise typer.Exit(code=1) from error
