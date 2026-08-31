# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from statline.app.session import StatLineSession


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def make_session(*, mode: str = "local") -> StatLineSession:
    return StatLineSession(base_url="http://127.0.0.1:8000", mode=mode)


def close_session(session: StatLineSession) -> None:
    run(session.close())


def test_json_and_float_boundary_helpers() -> None:
    assert StatLineSession._json_object({"ok": True}, context="test") == {"ok": True}
    with pytest.raises(TypeError, match="Invalid test response type"):
        StatLineSession._json_object([], context="test")

    assert StatLineSession._float_or_default("1.5") == 1.5
    assert StatLineSession._float_or_default("bad", 7.0) == 7.0
    assert StatLineSession._float_or_default(None, 3.0) == 3.0


def test_session_initial_state_status_and_mode() -> None:
    session = make_session()
    try:
        assert session.mode == "local"
        assert session.base_url == "http://127.0.0.1:8000"
        assert "Mode: local" in session._status_text()
        assert "Rows: 0" in session._status_text()
        assert session._set_mode([]) == "Usage: mode local|remote"
        assert session._set_mode(["REMOTE"]) == "Mode set to remote."
        assert str(session.mode) == "remote"
    finally:
        close_session(session)


def test_execute_control_commands_parse_and_unknown() -> None:
    session = make_session()
    try:
        assert run(session.execute("")).text == ""
        assert run(session.execute('"unterminated')).text.startswith("Parse error:")

        help_result = run(session.execute("help"))
        assert "Commands:" in help_result.text

        clear = run(session.execute("clear"))
        assert clear.clear and not clear.quit

        quit_result = run(session.execute("exit"))
        assert quit_result.quit
        assert "Closing StatLine OS" in quit_result.text

        unknown = run(session.execute("wat"))
        assert "Unknown command: wat" in unknown.text
    finally:
        close_session(session)


def test_profile_dataset_and_row_helpers() -> None:
    session = make_session()
    try:
        assert session._set_profile([]) == "Usage: profile <name>"
        assert session._set_profile(["PRI-AF"]) == "Profile set to PRI-AF."
        assert session.profile == "PRI-AF"
        assert session._dataset_text() == "No dataset loaded."

        session.dataset_name = "demo"
        session.rows = [{"PLAYER": "Ada"}, {"team": "Team B"}, {}]
        assert session._dataset_text() == "demo · 3 rows"
        assert session._row_name(session.rows[0]) == "Ada"
        assert session._row_name(session.rows[1]) == "Team B"
        assert session._row_name(session.rows[2]) == "-"

        assert session._profile_score({"scores": {"PRI AF": "88"}}) == 88.0
        assert session._profile_score({"pri": "73"}) == 73.0
        assert session._profile_score({"pri": "bad"}) == 0.0
    finally:
        close_session(session)


def test_local_adapter_profile_and_load_commands() -> None:
    session = make_session()
    try:
        adapters = run(session._adapters_text())
        assert adapters.startswith("Adapters:\n")
        assert "eba.players" in adapters

        using = run(session._use_adapter(["eba.players"]))
        assert using.startswith("Using eba.players")
        assert session.adapter_name == "eba.players"

        profiles = run(session._profiles_text())
        assert profiles.startswith("Profiles: ")
        assert "PRI" in profiles

        loaded = run(session._load_rows(["EBA_Elevate302/eba_s1_players"]))
        assert loaded.startswith("Loaded ")
        assert session.rows
        assert session.dataset_name == "EBA_Elevate302/eba_s1_players"
    finally:
        close_session(session)


def test_score_usage_and_prerequisites() -> None:
    session = make_session()
    try:
        assert run(session._score(["a", "b"])) == "Usage: score [csv-or-dataset]"
        assert run(session._score([])) == "Select an adapter first: use <adapter>"
        session.adapter_name = "eba.players"
        assert run(session._score([])) == "Load a dataset first: load <csv-or-dataset>"
    finally:
        close_session(session)


def test_local_scoring_returns_table() -> None:
    session = make_session()
    try:
        session.adapter_name = "eba.players"
        run(session._load_rows(["EBA_Elevate302/eba_s1_players"]))
        output = run(session._score([]))
        assert "PRI" in output
        assert "RAW01" in output
        assert output.endswith("local")
    finally:
        close_session(session)


def test_remote_helpers_use_request_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session(mode="remote")

    async def fake_request(
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        authenticated: bool = True,
    ) -> object:
        if path == "/v4/health":
            session.last_latency_ms = 12.5
            return {"version": "4.test", "adapters": 3}
        if path == "/v4/adapters":
            return {"adapters": [{"key": "one"}, {"key": "two"}, "ignored"]}
        if path == "/v4/adapters/one":
            return {"key": "one", "version": "1", "score_profiles": {"PRI": {}, "ALT": {}}}
        if path == "/v4/score":
            assert method == "POST"
            assert json_body is not None
            return {"results": [{"pri": 91, "pri_raw": 0.5, "scores": {"PRI": 91}}]}
        raise AssertionError(path)

    monkeypatch.setattr(session, "_request_json", fake_request)
    try:
        assert run(session.start()).endswith("SLAPI 12.5 ms")
        assert run(session._health_text()) == "SLAPI healthy · 12.5 ms · v4.test · 3 adapters"
        assert run(session._adapters_text()) == "Adapters:\n  one\n  two"
        assert run(session._use_adapter(["one"])) == "Using one (1)."
        assert run(session._profiles_text()) == "Profiles: PRI, ALT"

        session.rows = [{"name": "Ada"}]
        session.profile = "PRI"
        scored = run(session._score([]))
        assert "Ada" in scored
        assert "SLAPI 12.5 ms" in scored
    finally:
        close_session(session)


def test_remote_path_adapter_is_rejected() -> None:
    session = make_session(mode="remote")
    try:
        with pytest.raises(ValueError, match="local-only"):
            run(session._use_adapter(["custom.yaml"]))
    finally:
        close_session(session)


def test_api_key_prefers_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STATLINE_API_KEY", " api_test ")
    session = make_session()
    try:
        assert session._api_key() == "api_test"
    finally:
        close_session(session)


def test_execute_reports_command_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session()

    async def broken() -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(session, "_health_text", broken)
    try:
        result = run(session.execute("health"))
        assert result.text == "Error: boom"
    finally:
        close_session(session)


def test_start_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    session = make_session(mode="remote")

    async def broken_request(*args: object, **kwargs: object) -> object:
        raise RuntimeError("offline")

    monkeypatch.setattr(session, "_request_json", broken_request)
    try:
        assert "SLAPI unavailable: offline" in run(session.start())
    finally:
        close_session(session)
