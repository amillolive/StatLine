"""Persistent StatLine OS session backed by the real CLI command tree."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
from pathlib import Path
from typing import Literal, cast

import httpx2
from typer.testing import CliRunner

from statline import __version__

SessionMode = Literal["auto", "local", "remote"]


class SessionCommandResult:
    """One OS command result plus UI control signals."""

    def __init__(self, text: str = "", *, quit: bool = False, clear: bool = False) -> None:
        self.text = text
        self.quit = quit
        self.clear = clear


class StatLineSession:
    """Long-lived StatLine OS state which delegates commands to the canonical CLI."""

    @staticmethod
    def _json_object(value: object, *, context: str) -> dict[str, object]:
        """Validate one decoded JSON object at the HTTP boundary."""
        if not isinstance(value, dict):
            raise TypeError(
                f"Invalid {context} response type: expected object, got {type(value).__name__}"
            )
        return cast(dict[str, object], value)

    def __init__(self, *, base_url: str | None = None, mode: str | None = None) -> None:
        configured_mode = (mode or os.getenv("STATLINE_MODE") or "auto").strip().casefold()
        self.mode: SessionMode = cast(
            SessionMode,
            configured_mode if configured_mode in {"auto", "local", "remote"} else "auto",
        )
        self.base_url = (base_url or os.getenv("SLAPI_URL") or "https://api.statline.dev").rstrip(
            "/"
        )
        self.last_latency_ms: float | None = None
        self.slapi_reachable: bool | None = None
        self.authenticated: bool | None = None
        self.last_command = ""
        self._client = httpx2.AsyncClient(
            base_url=self.base_url,
            timeout=httpx2.Timeout(30.0),
            limits=httpx2.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=60.0,
            ),
        )

    async def start(self) -> str:
        """Warm SLAPI state once and report reachability and authentication separately."""
        if self.mode == "local":
            self.slapi_reachable = None
            self.authenticated = None
            self.last_latency_ms = None
            return f"StatLine OS · v{__version__} · local core · SLAPI disabled"

        try:
            payload = self._json_object(
                await self._request_json("GET", "/v4/health", authenticated=False),
                context="health",
            )
        except Exception as error:  # noqa: BLE001 - OS stays useful without SLAPI
            self.slapi_reachable = False
            self.authenticated = None
            return f"StatLine OS · v{__version__} · SLAPI unavailable: {error}"

        self.slapi_reachable = True
        version = str(payload.get("version", __version__))
        latency = (
            f"{self.last_latency_ms:.1f} ms" if self.last_latency_ms is not None else "reachable"
        )

        if not self._api_key():
            self.authenticated = False
            return f"StatLine OS · v{version} · SLAPI {latency} · unauthenticated"

        try:
            await self._request_json("GET", "/v4/auth/whoami", authenticated=True)
        except Exception:  # noqa: BLE001 - health already proved SLAPI is reachable
            self.authenticated = False
            return f"StatLine OS · v{version} · SLAPI {latency} · unauthenticated"

        self.authenticated = True
        return f"StatLine OS · v{version} · SLAPI {latency} · authenticated"

    async def close(self) -> None:
        await self._client.aclose()

    async def execute(self, command: str) -> SessionCommandResult:
        """Execute an OS control or the same command accepted by the regular CLI."""
        try:
            parts = self._split_command(command)
        except ValueError as error:
            return SessionCommandResult(f"Parse error: {error}")
        if not parts:
            return SessionCommandResult()

        parts = self._strip_cli_prefix(parts)
        if not parts:
            return SessionCommandResult(self._help_text())

        verb = parts[0].casefold()
        if verb in {"quit", "exit"}:
            return SessionCommandResult("Closing StatLine OS.", quit=True)
        if verb == "clear":
            return SessionCommandResult(clear=True)
        if verb == "mode":
            text, changed = self._set_mode(parts[1:])
            if changed:
                return SessionCommandResult(f"{text}\n{await self.start()}")
            return SessionCommandResult(text)
        if verb in {"help", "?"}:
            help_args = parts[1:] + ["--help"] if len(parts) > 1 else ["--help"]
            return SessionCommandResult(await self._run_cli(help_args))
        if verb == "status" and len(parts) == 1:
            return SessionCommandResult(await self._run_cli(["system", "status"]))

        self.last_command = " ".join(parts)
        return SessionCommandResult(await self._run_cli(parts))

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        authenticated: bool = True,
    ) -> object:
        headers: dict[str, str] = {}
        if authenticated:
            api_key = self._api_key()
            if not api_key:
                raise PermissionError(
                    "No API key configured. Set STATLINE_API_KEY/SLAPI_API_KEY or use mode local."
                )
            headers["Authorization"] = f"Bearer {api_key}"

        started = time.perf_counter()
        response = await self._client.request(method, path, headers=headers, json=json_body)
        self.last_latency_ms = (time.perf_counter() - started) * 1000.0
        if response.is_error:
            detail: object
            try:
                detail = cast(object, response.json())
            except ValueError:
                detail = response.text
            raise RuntimeError(f"SLAPI {response.status_code}: {detail}")
        if not response.content:
            return {}
        return cast(object, response.json())

    def _api_key(self) -> str:
        env_key = (os.getenv("STATLINE_API_KEY") or os.getenv("SLAPI_API_KEY") or "").strip()
        if env_key:
            return env_key

        package_root = Path(__file__).resolve().parents[1]
        configured = os.getenv("STATLINE_SECRETS")
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(configured).expanduser())
        candidates.extend(
            [
                Path.cwd() / "statline" / "secrets",
                Path.cwd() / "secrets",
                package_root / "secrets",
                Path.home() / ".config" / "statline",
                Path.home() / ".statline",
            ]
        )
        for directory in candidates:
            path = directory / "APIKEY"
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if value:
                return value
        return ""

    @staticmethod
    def _split_command(command: str) -> list[str]:
        """Split terminal input while preserving Windows backslashes and quoted paths."""
        text = command.strip()
        text = re.sub(r"^statline>\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^PS\s+[^>]+>\s*", "", text, flags=re.IGNORECASE)
        if not text:
            return []

        if os.name == "nt":
            lexer = shlex.shlex(text, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = ""
            lexer.escape = ""
            return list(lexer)
        return shlex.split(text)

    @staticmethod
    def _strip_cli_prefix(parts: list[str]) -> list[str]:
        """Accept commands copied from a normal shell with their executable prefix intact."""
        if not parts:
            return []

        first = Path(parts[0].strip('"')).name.casefold()
        if first in {"statline", "statline.exe", "statline.cmd"}:
            return parts[1:]

        if len(parts) >= 3:
            python_name = first in {
                "python",
                "python.exe",
                "python3",
                "python3.exe",
                "py",
                "py.exe",
            }
            if python_name and parts[1] == "-m" and parts[2].casefold() == "statline":
                return parts[3:]

        return parts

    def _set_mode(self, args: list[str]) -> tuple[str, bool]:
        if len(args) != 1 or args[0].casefold() not in {"auto", "local", "remote"}:
            return "Usage: mode auto|local|remote", False
        self.mode = cast(SessionMode, args[0].casefold())
        return f"Mode set to {self.mode}.", True

    def _help_text(self) -> str:
        return (
            "StatLine OS accepts the regular StatLine CLI command tree.\n"
            "Type `help` for root help or `help <command>` for command help.\n"
            "You can paste commands with or without the leading `statline`.\n"
            "OS controls: mode auto|local|remote, clear, exit, quit."
        )

    def _status_text(self) -> str:
        if self.mode == "local":
            slapi = "disabled"
            auth = "not checked"
        elif self.slapi_reachable is False:
            slapi = "unavailable"
            auth = "not checked"
        elif self.slapi_reachable is True:
            slapi = "reachable"
            auth = "authenticated" if self.authenticated else "unauthenticated"
        else:
            slapi = "not checked"
            auth = "not checked"
        latency = "-" if self.last_latency_ms is None else f"{self.last_latency_ms:.1f} ms"
        return (
            f"Mode: {self.mode}\n"
            f"SLAPI: {self.base_url}\n"
            f"SLAPI state: {slapi}\n"
            f"Auth: {auth}\n"
            f"Last request: {latency}"
        )

    async def _run_cli(self, args: list[str]) -> str:
        """Invoke the canonical Typer app in-process so OS and CLI cannot drift."""
        from statline.app.cli.main import app

        inherited = [
            "--mode",
            self.mode,
            "--url",
            self.base_url,
            "--no-timing",
        ]

        def invoke() -> str:
            result = CliRunner().invoke(
                app,
                inherited + args,
                env={"STATLINE_OS_SESSION": "1"},
                catch_exceptions=True,
                prog_name="statline",
            )
            text = result.output.rstrip()
            if result.exit_code != 0 and not text:
                if result.exception is not None:
                    return f"Error: {result.exception}"
                return f"Command failed with exit code {result.exit_code}."
            return text

        return await asyncio.to_thread(invoke)
