"""StatLine command-line entry point."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        import click

        from statline.app.cli.main import app
    except ModuleNotFoundError as error:
        missing = (error.name or "").split(".", 1)[0]

        if missing in {"click", "httpx2", "typer", "textual"}:
            print(
                "StatLine App is not installed. Install it with: pip install 'statline[app]'",
                file=sys.stderr,
            )
            raise SystemExit(2) from None

        raise

    try:
        app()
    except click.exceptions.Exit as error:
        raise SystemExit(error.exit_code) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:  # noqa: BLE001 - CLI boundary converts failures to exit code 1
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
