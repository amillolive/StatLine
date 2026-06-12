# statline/tui/app.py
from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable

import typer
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from statline.tui.catalog import ActionSpec, build_action_catalog


@dataclass(frozen=True)
class LauncherConfig:
    title: str = "StatLine HomeShell"


class ActionItem(ListItem):
    def __init__(self, action: ActionSpec) -> None:
        super().__init__(Label(f"{action.title}  —  {action.short_help or action.group}"))
        self.action = action


class StatLineHomeShell(App[None]):
    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #left {
        width: 42%;
        border: solid $primary;
        padding: 1;
    }

    #right {
        width: 58%;
        border: solid $secondary;
        padding: 1;
    }

    #search {
        margin-bottom: 1;
    }

    #actions {
        height: 1fr;
    }

    #help {
        height: 1fr;
        overflow-y: auto;
        margin-bottom: 1;
    }

    #args {
        margin-top: 1;
    }

    #run {
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "focus_search", "Search"),
        ("r", "run_selected", "Run"),
    ]

    def __init__(
        self,
        *,
        typer_app: typer.Typer,
        config: LauncherConfig | None = None,
    ) -> None:
        super().__init__()
        self.typer_app = typer_app
        self.config = config or LauncherConfig()
        self.actions: list[ActionSpec] = []
        self.filtered: list[ActionSpec] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Static("Search actions")
                yield Input(
                    placeholder="Type: score, adapter, auth, cache, storage...",
                    id="search",
                )
                yield ListView(id="actions")

            with Vertical(id="right"):
                yield Static("Command help")
                yield Static("", id="help")
                yield Input(
                    placeholder="Extra args for now, example: --source local --fmt json",
                    id="args",
                )
                yield Button("Run selected action", id="run", variant="primary")

        yield Footer()

    def on_mount(self) -> None:
        self.title = self.config.title
        self.actions = build_action_catalog(
            self.typer_app,
            exclude={
                "launch",
            },
        )
        self.filtered = list(self.actions)
        self._refresh_actions()
        self._show_action(self.filtered[0] if self.filtered else None)

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def _refresh_actions(self) -> None:
        list_view = self.query_one("#actions", ListView)
        list_view.clear()

        for action in self.filtered:
            list_view.append(ActionItem(action))

    def _selected_action(self) -> ActionSpec | None:
        list_view = self.query_one("#actions", ListView)

        if list_view.index is None:
            return self.filtered[0] if self.filtered else None

        if 0 <= list_view.index < len(self.filtered):
            return self.filtered[list_view.index]

        return None

    def _show_action(self, action: ActionSpec | None) -> None:
        help_panel = self.query_one("#help", Static)

        if action is None:
            help_panel.update("No action selected.")
            return

        params = "\n".join(
            f"  - {param.name}"
            f"{' required' if param.required else ''}"
            f" [{param.kind}]"
            f"{' default=' + repr(param.default) if param.default not in (None, (), []) else ''}"
            for param in action.params
        )

        text = (
            f"{action.title}\n"
            f"{'=' * len(action.title)}\n\n"
            f"Command path: {' '.join(action.command_path)}\n"
            f"Group: {action.group}\n\n"
            f"Parameters:\n{params or '  none'}\n\n"
            f"Click/Typer help:\n\n"
            f"{action.click_help}"
        )

        help_panel.update(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return

        query = event.value.strip().lower()

        if not query:
            self.filtered = list(self.actions)
        else:
            self.filtered = [
                action
                for action in self.actions
                if query in action.id.lower()
                or query in action.title.lower()
                or query in action.group.lower()
                or query in action.short_help.lower()
                or query in action.click_help.lower()
            ]

        self._refresh_actions()
        self._show_action(self.filtered[0] if self.filtered else None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item

        if isinstance(item, ActionItem):
            self._show_action(item.action)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_run_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run":
            self.action_run_selected()

    def action_run_selected(self) -> None:
        action = self._selected_action()

        if action is None:
            return

        args_box = self.query_one("#args", Input)
        extra_args = shlex.split(args_box.value.strip()) if args_box.value.strip() else []

        command_args = [*action.command_path, *extra_args]

        self._run_command(command_args)

    def _run_command(self, command_args: Iterable[str]) -> None:
        args = list(command_args)

        # Leave the TUI temporarily so the normal CLI output still looks like
        # Typer/Click/Rich output. Then return to the HomeShell.
        with self.suspend():
            print()
            print(f"$ statline {' '.join(args)}")
            print()

            completed = subprocess.run(
                [sys.executable, "-m", "statline.cli", *args],
                check=False,
            )

            print()
            print(f"Command exited with code {completed.returncode}.")
            input("Press Enter to return to StatLine UX...")
