# statline/tui/catalog.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import click
import typer
from typer.main import get_command

ParamKind = Literal["text", "number", "boolean", "path", "choice", "multi"]


@dataclass(frozen=True)
class ActionParam:
    name: str
    kind: ParamKind
    required: bool
    default: Any
    help: str
    opts: tuple[str, ...]
    choices: tuple[str, ...]


@dataclass(frozen=True)
class ActionSpec:
    id: str
    title: str
    group: str
    command_path: tuple[str, ...]
    short_help: str
    click_help: str
    params: tuple[ActionParam, ...]


def _param_kind(param: click.Parameter) -> ParamKind:
    if isinstance(param, click.Option) and param.is_bool_flag:
        return "boolean"

    if getattr(param, "multiple", False):
        return "multi"

    click_type = getattr(param, "type", None)

    if isinstance(click_type, click.Choice):
        return "choice"

    if isinstance(click_type, click.Path):
        return "path"

    if click_type in (click.INT, click.FLOAT):
        return "number"

    name = str(getattr(param, "name", "") or "").lower()

    if "path" in name or "file" in name or "dir" in name:
        return "path"

    return "text"


def _param_choices(param: click.Parameter) -> tuple[str, ...]:
    click_type = getattr(param, "type", None)

    if isinstance(click_type, click.Choice):
        return tuple(str(choice) for choice in click_type.choices) # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportUnknownArgumentType]

    return ()


def _param_to_spec(param: click.Parameter) -> ActionParam:
    name = str(getattr(param, "name", "") or "")

    opts: tuple[str, ...] = ()
    help_text = ""

    if isinstance(param, click.Option):
        opts = tuple(param.opts or ())
        help_text = str(param.help or "")

    required = bool(getattr(param, "required", False))

    if isinstance(param, click.Argument):
        required = True

    return ActionParam(
        name=name,
        kind=_param_kind(param),
        required=required,
        default=getattr(param, "default", None),
        help=help_text,
        opts=opts,
        choices=_param_choices(param),
    )


def _click_help(command: click.Command, info_name: str) -> str:
    try:
        ctx = click.Context(command, info_name=info_name)
        return command.get_help(ctx)
    except Exception:
        return command.help or ""


def _walk_command_tree(
    command: click.Command,
    prefix: tuple[str, ...] = (),
    *,
    exclude: set[str],
) -> list[ActionSpec]:
    actions: list[ActionSpec] = []

    if isinstance(command, click.Group):
        ctx = click.Context(command)

        for name in command.list_commands(ctx):
            if name in exclude:
                continue

            child = command.get_command(ctx, name)

            if child is None:
                continue

            actions.extend(
                _walk_command_tree(
                    child,
                    prefix + (name,),
                    exclude=exclude,
                )
            )

        return actions

    if not prefix:
        return actions

    action_id = ".".join(prefix)
    title = " ".join(prefix)
    group = prefix[0]

    params = tuple(_param_to_spec(param) for param in command.params)

    actions.append(
        ActionSpec(
            id=action_id,
            title=title,
            group=group,
            command_path=prefix,
            short_help=str(command.short_help or command.help or ""),
            click_help=_click_help(command, title),
            params=params,
        )
    )

    return actions


def build_action_catalog(
    typer_app: typer.Typer,
    *,
    exclude: set[str] | None = None,
) -> list[ActionSpec]:
    click_root = get_command(typer_app)

    excluded = {
        "interactive",
    }

    if exclude:
        excluded |= exclude

    actions = _walk_command_tree(click_root, exclude=excluded)

    return sorted(actions, key=lambda action: action.id)