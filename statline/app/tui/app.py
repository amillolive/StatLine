"""Textual front end for the persistent StatLine OS session."""

from __future__ import annotations

import json
from typing import ClassVar, cast

from textual import work
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static

from statline.app.session import StatLineSession


class StatLineOS(App[None]):
    """Persistent REPL/shell/TUI combination over one StatLine session."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #session-status {
        height: auto;
        padding: 0 1;
        border-bottom: solid $primary;
    }

    #output {
        height: 1fr;
        padding: 1;
    }

    #score-table {
        height: 35%;
        min-height: 6;
        border: round $primary;
        display: none;
    }

    #command {
        dock: bottom;
        margin: 0 1 1 1;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+l", "clear_output", "Clear"),
    ]

    def __init__(self, *, session: StatLineSession | None = None) -> None:
        super().__init__()
        self.session = session or StatLineSession()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("Starting StatLine OS…", id="session-status")
            yield RichLog(id="output", wrap=True, highlight=False, markup=False)
            yield DataTable(id="score-table", zebra_stripes=True, cursor_type="row")
            yield Input(
                placeholder="statline> paste or type any StatLine CLI command", id="command"
            )
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "StatLine OS"
        status = await self.session.start()
        self.query_one("#session-status", Static).update(status)
        output = self.query_one("#output", RichLog)
        output.write("StatLine OS")
        output.write("Paste any normal `statline ...` command, or type `help` to browse.")
        output.write("Ctrl+C / Ctrl+V use Textual's normal selection and clipboard behavior.")
        self.query_one("#command", Input).focus()

    async def on_unmount(self) -> None:
        await self.session.close()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "command":
            return
        command = event.value.strip()
        event.input.value = ""
        if not command:
            return
        output = self.query_one("#output", RichLog)
        output.write(f"statline> {command}")
        self._execute_command(command)

    @work(exclusive=True, group="scoring-command")
    async def _execute_command(self, command: str) -> None:
        """Keep the UI event loop live while the CLI scores in its worker thread."""
        status = self.query_one("#session-status", Static)
        status.update("Working · scoring / command in progress…")
        try:
            result = await self.session.execute(command)
        except Exception as error:
            self.query_one("#output", RichLog).write(f"Command failed: {error}")
            self._refresh_status()
            return
        output = self.query_one("#output", RichLog)
        if result.clear:
            output.clear()
        elif result.text:
            if not self._render_json_table(result.text):
                output.write(result.text)
        self._refresh_status()
        if result.quit:
            self.exit()

    def _render_json_table(self, text: str) -> bool:
        """Display machine-readable scoring rows in a responsive DataTable.

        Non-JSON and unstructured output keeps the usual RichLog path. The
        untruncated command result is still available through normal CLI exports.
        """
        try:
            payload: object = json.loads(text)
        except (ValueError, TypeError):
            return False
        if isinstance(payload, dict):
            rows: object = cast(dict[str, object], payload).get("results")
        else:
            rows = payload
        if not isinstance(rows, list) or not rows:
            return False
        items: list[dict[str, object]] = []
        for row in cast(list[object], rows):
            if not isinstance(row, dict):
                return False
            items.append(cast(dict[str, object], row))
        names = list(items[0])
        if not names:
            return False
        table = cast(DataTable[str], self.query_one("#score-table", DataTable))
        table.clear(columns=True)
        table.add_columns(*names)
        table.add_rows([self._table_cell(item.get(name)) for name in names] for item in items[:500])
        table.display = True
        self.query_one("#output", RichLog).write(
            f"Rendered {min(len(items), 500)} of {len(items)} scoring rows. "
            "Use --out to export the full result."
        )
        return True

    @staticmethod
    def _table_cell(value: object) -> str:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value) if value is not None else ""

    def action_clear_output(self) -> None:
        self.query_one("#output", RichLog).clear()

    def _refresh_status(self) -> None:
        latency = (
            "-"
            if self.session.last_latency_ms is None
            else f"{self.session.last_latency_ms:.1f} ms"
        )
        if self.session.mode == "local":
            slapi_state = "disabled"
            auth_state = "not checked"
        elif self.session.slapi_reachable is False:
            slapi_state = "unavailable"
            auth_state = "not checked"
        elif self.session.slapi_reachable is True:
            slapi_state = "reachable"
            auth_state = "authenticated" if self.session.authenticated else "unauthenticated"
        else:
            slapi_state = "not checked"
            auth_state = "not checked"
        status = (
            f"mode={self.session.mode}  slapi={slapi_state}  auth={auth_state}  latency={latency}"
        )
        self.query_one("#session-status", Static).update(status)


# Backwards import compatibility for callers that referenced the old HomeShell name.
StatLineHomeShell = StatLineOS

__all__ = ["StatLineHomeShell", "StatLineOS"]
