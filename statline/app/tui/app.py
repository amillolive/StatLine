"""Textual front end for the persistent StatLine OS session."""

from __future__ import annotations

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

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
            yield RichLog(id="output", wrap=True, highlight=True, markup=True)
            yield Input(placeholder="statline> type 'help'", id="command")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "StatLine OS"
        status = await self.session.start()
        self.query_one("#session-status", Static).update(status)
        output = self.query_one("#output", RichLog)
        output.write("[bold]StatLine OS[/bold]")
        output.write("Persistent session ready. Type [bold]help[/bold] for commands.")
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
        output.write(f"[bold cyan]statline>[/bold cyan] {command}")
        result = await self.session.execute(command)
        if result.clear:
            output.clear()
        elif result.text:
            output.write(result.text)
        self._refresh_status()
        if result.quit:
            self.exit()

    def action_clear_output(self) -> None:
        self.query_one("#output", RichLog).clear()

    def _refresh_status(self) -> None:
        latency = (
            "-"
            if self.session.last_latency_ms is None
            else f"{self.session.last_latency_ms:.1f} ms"
        )
        status = (
            f"mode={self.session.mode}  "
            f"adapter={self.session.adapter_name or '-'}  "
            f"dataset={self.session.dataset_name or '-'}  "
            f"rows={len(self.session.rows)}  "
            f"profile={self.session.profile}  "
            f"latency={latency}"
        )
        self.query_one("#session-status", Static).update(status)


# Backwards import compatibility for callers that referenced the old HomeShell name.
StatLineHomeShell = StatLineOS

__all__ = ["StatLineHomeShell", "StatLineOS"]
