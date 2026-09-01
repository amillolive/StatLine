# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import Any, Literal

import pytest
from statline.app.session import StatLineSession


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_session(*, mode: str = "local") -> StatLineSession:
    return StatLineSession(base_url="http://127.0.0.1:8000", mode=mode)


def close_session(session: StatLineSession) -> None:
    run(session.close())


def test_json_boundary_helper() -> None:
    assert StatLineSession._json_object({"ok": True}, context="test") == {"ok": True}
    with pytest.raises(TypeError, match="Invalid test response type"):
        StatLineSession._json_object([], context="test")


def assert_mode(
    session: StatLineSession,
    expected: Literal["auto", "local", "remote"],
) -> None:
    assert session.mode == expected


def test_session_initial_state_status_and_mode() -> None:
    session = make_session()
    try:
        assert_mode(session, "local")
        assert session.base_url == "http://127.0.0.1:8000"
        assert "Mode: local" in session._status_text()
        assert "SLAPI state: disabled" in session._status_text()

        assert session._set_mode([]) == ("Usage: mode auto|local|remote", False)
        assert session._set_mode(["AUTO"]) == ("Mode set to auto.", True)

        assert_mode(session, "auto")
    finally:
        close_session(session)


def test_cli_prefix_stripping_supports_copied_shell_commands() -> None:
    assert StatLineSession._strip_cli_prefix(["statline", "adapter", "list"]) == [
        "adapter",
        "list",
    ]
    assert StatLineSession._strip_cli_prefix(["statline.cmd", "score", "x.csv"]) == [
        "score",
        "x.csv",
    ]
    assert StatLineSession._strip_cli_prefix(["python", "-m", "statline", "--help"]) == ["--help"]
    assert StatLineSession._strip_cli_prefix(["adapter", "list"]) == ["adapter", "list"]


def test_split_command_accepts_os_prompt_and_powershell_prompt() -> None:
    assert StatLineSession._split_command("statline> statline adapter list") == [
        "statline",
        "adapter",
        "list",
    ]
    assert StatLineSession._split_command("PS C:\\repo> statline adapter list") == [
        "statline",
        "adapter",
        "list",
    ]
    assert (
        StatLineSession._split_command(
            'statline score --adapter eba.players "C:\\Users\\amillo\\stats file.csv"'
        )[-1]
        == "C:\\Users\\amillo\\stats file.csv"
    )


def test_execute_controls_and_cli_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()
    calls: list[list[str]] = []

    async def fake_run_cli(args: list[str]) -> str:
        calls.append(args)
        return "ok"

    monkeypatch.setattr(session, "_run_cli", fake_run_cli)
    try:
        assert run(session.execute("")).text == ""
        assert run(session.execute('"unterminated')).text.startswith("Parse error:")

        clear = run(session.execute("clear"))
        assert clear.clear and not clear.quit

        quit_result = run(session.execute("exit"))
        assert quit_result.quit
        assert "Closing StatLine OS" in quit_result.text

        assert run(session.execute("help")).text == "ok"
        assert calls[-1] == ["--help"]

        assert run(session.execute("help score")).text == "ok"
        assert calls[-1] == ["score", "--help"]

        assert run(session.execute("status")).text == "ok"
        assert calls[-1] == ["system", "status"]

        assert run(session.execute("statline adapter list")).text == "ok"
        assert calls[-1] == ["adapter", "list"]
    finally:
        close_session(session)


def test_mode_control_rechecks_startup_state(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()

    async def fake_start() -> str:
        return "startup state"

    monkeypatch.setattr(session, "start", fake_start)
    try:
        result = run(session.execute("mode remote"))
        assert result.text == "Mode set to remote.\nstartup state"
        assert session.mode == "remote"
    finally:
        close_session(session)


def test_local_start_does_not_probe_network() -> None:
    session = make_session(mode="local")
    try:
        text = run(session.start())
        assert "local core" in text
        assert "SLAPI disabled" in text
        assert session.slapi_reachable is None
        assert session.authenticated is None
    finally:
        close_session(session)


def test_remote_start_distinguishes_reachable_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(mode="remote")

    async def fake_request(
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        authenticated: bool = True,
    ) -> object:
        assert method == "GET"
        assert path == "/v4/health"
        assert not authenticated
        session.last_latency_ms = 12.5
        return {"version": "4.test"}

    monkeypatch.setattr(session, "_request_json", fake_request)
    monkeypatch.setattr(session, "_api_key", lambda: "")
    try:
        text = run(session.start())
        assert text == "StatLine OS · v4.test · SLAPI 12.5 ms · unauthenticated"
        assert session.slapi_reachable is True
        assert session.authenticated is False
    finally:
        close_session(session)


def test_remote_start_reports_authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session(mode="remote")
    calls: list[tuple[str, bool]] = []

    async def fake_request(
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        authenticated: bool = True,
    ) -> object:
        _ = method, json_body
        calls.append((path, authenticated))
        session.last_latency_ms = 8.0
        if path == "/v4/health":
            return {"version": "4.test"}
        if path == "/v4/auth/whoami":
            return {"subject": "test"}
        raise AssertionError(path)

    monkeypatch.setattr(session, "_request_json", fake_request)
    monkeypatch.setattr(session, "_api_key", lambda: "api_test")
    try:
        text = run(session.start())
        assert text == "StatLine OS · v4.test · SLAPI 8.0 ms · authenticated"
        assert calls == [("/v4/health", False), ("/v4/auth/whoami", True)]
        assert session.authenticated is True
    finally:
        close_session(session)


def test_start_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session(mode="remote")

    async def broken_request(*args: object, **kwargs: object) -> object:
        _ = args, kwargs
        raise RuntimeError("offline")

    monkeypatch.setattr(session, "_request_json", broken_request)
    try:
        assert "SLAPI unavailable: offline" in run(session.start())
        assert session.slapi_reachable is False
        assert session.authenticated is None
    finally:
        close_session(session)


def test_run_cli_inherits_session_mode_and_suppresses_repeated_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = make_session(mode="local")
    captured: dict[str, object] = {}

    class FakeResult:
        output = "adapter output\n"
        exit_code = 0
        exception = None

    class FakeRunner:
        def invoke(self, app: object, args: list[str], **kwargs: object) -> FakeResult:
            captured["app"] = app
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr("statline.app.session.CliRunner", FakeRunner)
    try:
        assert run(session._run_cli(["adapter", "list"])) == "adapter output"
        assert captured["args"] == [
            "--mode",
            "local",
            "--url",
            "http://127.0.0.1:8000",
            "--no-timing",
            "adapter",
            "list",
        ]
        kwargs = captured["kwargs"]
        assert isinstance(kwargs, dict)
        assert kwargs["env"] == {"STATLINE_OS_SESSION": "1"}
        assert kwargs["prog_name"] == "statline"
    finally:
        close_session(session)
