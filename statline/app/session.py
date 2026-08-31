"""Persistent StatLine application session shared by interactive front ends."""

from __future__ import annotations

import asyncio
import os
import shlex
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import httpx2

from statline import __version__
from statline.app.cli.presentation import render_table_text
from statline.core.adapters import list_adapters, load_adapter
from statline.core.datasets import load_dataset
from statline.public import score_batch

SessionMode = Literal["local", "remote"]


class SessionCommandResult:
    """One REPL command result plus UI control signals."""

    def __init__(self, text: str = "", *, quit: bool = False, clear: bool = False) -> None:
        self.text = text
        self.quit = quit
        self.clear = clear


class StatLineSession:
    """Long-lived local/remote StatLine state with a pooled async SLAPI client."""

    @staticmethod
    def _json_object(value: object, *, context: str) -> dict[str, object]:
        """Validate one decoded JSON object at the HTTP boundary."""
        if not isinstance(value, dict):
            raise TypeError(
                f"Invalid {context} response type: expected object, got {type(value).__name__}"
            )
        return cast(dict[str, object], value)

    @staticmethod
    def _float_or_default(value: object, default: float = 0.0) -> float:
        try:
            return float(cast(Any, value))
        except (TypeError, ValueError, OverflowError):
            return default

    def __init__(self, *, base_url: str | None = None, mode: str | None = None) -> None:
        configured_mode = (mode or os.getenv("STATLINE_MODE") or "remote").strip().casefold()
        self.mode: SessionMode = "local" if configured_mode == "local" else "remote"
        self.base_url = (base_url or os.getenv("SLAPI_URL") or "https://api.statline.dev").rstrip(
            "/"
        )
        self.adapter_name: str | None = None
        self.dataset_name: str | None = None
        self.rows: list[dict[str, object]] = []
        self.profile = "PRI"
        self.last_latency_ms: float | None = None
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
        """Warm the connection pool and return a concise startup status."""
        try:
            payload = self._json_object(
                await self._request_json("GET", "/v4/health", authenticated=False),
                context="health",
            )
        except Exception as error:  # noqa: BLE001 - interactive shell should stay usable offline
            return f"StatLine OS · v{__version__} · SLAPI unavailable: {error}"
        version = str(payload.get("version", __version__))
        latency = (
            f"{self.last_latency_ms:.1f} ms" if self.last_latency_ms is not None else "connected"
        )
        return f"StatLine OS · v{version} · SLAPI {latency}"

    async def close(self) -> None:
        await self._client.aclose()

    async def execute(self, command: str) -> SessionCommandResult:
        """Execute one session-native REPL command without spawning a subprocess."""
        try:
            parts = shlex.split(command)
        except ValueError as error:
            return SessionCommandResult(f"Parse error: {error}")
        if not parts:
            return SessionCommandResult()

        verb = parts[0].casefold()
        args = parts[1:]
        try:
            if verb in {"quit", "exit"}:
                return SessionCommandResult("Closing StatLine OS.", quit=True)
            if verb == "clear":
                return SessionCommandResult(clear=True)
            if verb in {"help", "?"}:
                return SessionCommandResult(self._help_text())
            if verb == "status":
                return SessionCommandResult(self._status_text())
            if verb == "health":
                return SessionCommandResult(await self._health_text())
            if verb == "mode":
                return SessionCommandResult(self._set_mode(args))
            if verb in {"adapters", "adapter-list"}:
                return SessionCommandResult(await self._adapters_text())
            if verb == "use":
                return SessionCommandResult(await self._use_adapter(args))
            if verb == "profile":
                return SessionCommandResult(self._set_profile(args))
            if verb == "profiles":
                return SessionCommandResult(await self._profiles_text())
            if verb == "load":
                return SessionCommandResult(await self._load_rows(args))
            if verb == "dataset":
                return SessionCommandResult(self._dataset_text())
            if verb == "score":
                return SessionCommandResult(await self._score(args))
        except Exception as error:  # noqa: BLE001 - REPL reports errors instead of terminating
            return SessionCommandResult(f"Error: {error}")
        return SessionCommandResult(f"Unknown command: {parts[0]}\nType 'help' for commands.")

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

    def _help_text(self) -> str:
        return (
            "Commands:\n"
            "  status                      Show current session state\n"
            "  health                      Ping SLAPI using the pooled connection\n"
            "  mode local|remote           Switch execution mode\n"
            "  adapters                    List visible/current adapters\n"
            "  use <adapter-or-path>       Select adapter (paths are local-only)\n"
            "  profiles                    List profiles for selected adapter\n"
            "  profile <name>              Select score profile\n"
            "  load <csv-or-dataset>       Load rows into the session\n"
            "  dataset                     Show loaded dataset state\n"
            "  score [csv-or-dataset]      Score loaded or supplied rows\n"
            "  clear                       Clear the output pane\n"
            "  exit | quit                 Close StatLine OS"
        )

    def _status_text(self) -> str:
        latency = "-" if self.last_latency_ms is None else f"{self.last_latency_ms:.1f} ms"
        return (
            f"Mode: {self.mode}\n"
            f"SLAPI: {self.base_url}\n"
            f"Last request: {latency}\n"
            f"Adapter: {self.adapter_name or '-'}\n"
            f"Dataset: {self.dataset_name or '-'}\n"
            f"Rows: {len(self.rows)}\n"
            f"Profile: {self.profile}"
        )

    async def _health_text(self) -> str:
        payload = self._json_object(
            await self._request_json("GET", "/v4/health", authenticated=False),
            context="health",
        )
        latency = f"{self.last_latency_ms:.1f} ms" if self.last_latency_ms is not None else "-"
        version = payload.get("version", "?")
        adapters = payload.get("adapters", "?")
        return f"SLAPI healthy · {latency} · v{version} · {adapters} adapters"

    def _set_mode(self, args: Sequence[str]) -> str:
        if len(args) != 1 or args[0].casefold() not in {"local", "remote"}:
            return "Usage: mode local|remote"
        self.mode = cast(SessionMode, args[0].casefold())
        return f"Mode set to {self.mode}."

    async def _adapters_text(self) -> str:
        names: list[str]
        if self.mode == "local":
            local_names = await asyncio.to_thread(list_adapters)
            names = [str(name) for name in local_names]
        else:
            payload = self._json_object(
                await self._request_json("GET", "/v4/adapters"),
                context="adapters",
            )
            raw_adapters = payload.get("adapters", [])
            names = []
            if isinstance(raw_adapters, list):
                for raw_item in cast(list[object], raw_adapters):
                    if not isinstance(raw_item, dict):
                        continue
                    item = cast(dict[str, object], raw_item)
                    key = str(item.get("key", "")).strip()
                    if key:
                        names.append(key)
        return "Adapters:\n  " + "\n  ".join(names) if names else "No visible adapters."

    async def _use_adapter(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "Usage: use <adapter-or-path>"
        name = args[0]
        if self.mode == "local":
            adapter = await asyncio.to_thread(load_adapter, name)
            self.adapter_name = name if Path(name).is_file() else adapter.key
            return f"Using {adapter.key} ({adapter.version})."

        if Path(name).suffix.casefold() in {".yaml", ".yml"}:
            raise ValueError(
                "Explicit adapter paths are local-only; the API exposes current adapters only."
            )
        payload = self._json_object(
            await self._request_json("GET", f"/v4/adapters/{name}"),
            context="adapter",
        )
        self.adapter_name = str(payload.get("key", name))
        return f"Using {self.adapter_name} ({payload.get('version', '?')})."

    def _set_profile(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "Usage: profile <name>"
        self.profile = args[0]
        return f"Profile set to {self.profile}."

    async def _profiles_text(self) -> str:
        if not self.adapter_name:
            return "Select an adapter first: use <adapter>"
        if self.mode == "local":
            adapter = await asyncio.to_thread(load_adapter, self.adapter_name)
            names = list(adapter.score_profiles)
        else:
            payload = self._json_object(
                await self._request_json("GET", f"/v4/adapters/{self.adapter_name}"),
                context="adapter",
            )
            raw_profiles = payload.get("score_profiles", {})
            if isinstance(raw_profiles, dict):
                profile_map = cast(dict[str, object], raw_profiles)
                names = list(profile_map)
            else:
                names = []
        return "Profiles: " + ", ".join(names) if names else "No profiles found."

    async def _load_rows(self, args: Sequence[str]) -> str:
        if len(args) != 1:
            return "Usage: load <csv-or-dataset>"
        source = args[0]
        rows = await asyncio.to_thread(load_dataset, source)
        self.rows = [{str(key): cast(object, value) for key, value in row.items()} for row in rows]
        self.dataset_name = source
        return f"Loaded {len(self.rows)} rows from {source}."

    def _dataset_text(self) -> str:
        if not self.dataset_name:
            return "No dataset loaded."
        return f"{self.dataset_name} · {len(self.rows)} rows"

    async def _score(self, args: Sequence[str]) -> str:
        if len(args) > 1:
            return "Usage: score [csv-or-dataset]"
        if args:
            await self._load_rows(args)
        if not self.adapter_name:
            return "Select an adapter first: use <adapter>"
        if not self.rows:
            return "Load a dataset first: load <csv-or-dataset>"

        results: list[dict[str, object]] = []
        if self.mode == "local":
            raw_local_results = await asyncio.to_thread(
                score_batch,
                self.adapter_name,
                self.rows,
                profiles=[self.profile],
                output={
                    "show_weights": False,
                    "hide_pri_raw": False,
                    "show_components": False,
                    "show_buckets": False,
                    "show_context_used": True,
                },
            )
            for local_result in raw_local_results:
                results.append(
                    {str(key): cast(object, value) for key, value in local_result.items()}
                )
        else:
            payload = self._json_object(
                await self._request_json(
                    "POST",
                    "/v4/score",
                    json_body={
                        "adapter": self.adapter_name,
                        "rows": self.rows,
                        "profiles": [self.profile],
                        "output": {
                            "show_weights": False,
                            "hide_pri_raw": False,
                            "show_components": False,
                            "show_buckets": False,
                            "show_context_used": True,
                        },
                    },
                ),
                context="score",
            )
            raw_results = payload.get("results", [])
            if isinstance(raw_results, list):
                for remote_result in cast(list[object], raw_results):
                    if isinstance(remote_result, dict):
                        results.append(cast(dict[str, object], remote_result))

        display_rows: list[dict[str, Any]] = []
        for raw, result in zip(self.rows, results, strict=False):
            display_rows.append(
                {
                    "name": self._row_name(raw),
                    "score": self._profile_score(result),
                    "pri_raw": result.get("pri_raw", 0.0),
                }
            )
        display_rows.sort(
            key=lambda item: (float(item.get("score", 0.0)), float(item.get("pri_raw", 0.0))),
            reverse=True,
        )
        table = render_table_text(
            display_rows,
            (("Rank", "__rank__"), ("Name", "name"), (self.profile, "score"), ("RAW01", "pri_raw")),
            title=f"{self.profile} · {len(display_rows)} rows",
        )
        latency = "local" if self.mode == "local" else f"SLAPI {self.last_latency_ms:.1f} ms"
        return table.rstrip() + f"\n{latency}"

    def _row_name(self, row: Mapping[str, object]) -> str:
        folded = {str(key).casefold(): value for key, value in row.items()}
        for key in ("player", "name", "display_name", "team_name", "team", "username", "id"):
            value = folded.get(key)
            if value not in (None, ""):
                return str(value)
        return "-"

    def _profile_score(self, result: Mapping[str, object]) -> float:
        scores = result.get("scores")
        if isinstance(scores, Mapping):
            score_map = cast(Mapping[object, object], scores)
            wanted = self.profile.casefold().replace("-", "_").replace(" ", "_")
            for key, value in score_map.items():
                normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
                if normalized == wanted:
                    return self._float_or_default(value)
        return self._float_or_default(result.get("pri", 0.0))


__all__ = ["SessionCommandResult", "SessionMode", "StatLineSession"]
