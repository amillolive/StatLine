from __future__ import annotations


def main() -> None:
    try:
        from statline.app.tui.app import StatLineOS
    except ModuleNotFoundError as error:
        if error.name == "textual":
            raise SystemExit(
                "StatLine OS requires Textual. Install with: "
                "pip install 'statline[os]' (or 'statline[extras]')."
            ) from None
        raise

    StatLineOS().run()


if __name__ == "__main__":
    main()
