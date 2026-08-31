# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false

from __future__ import annotations

from pathlib import Path

import pytest
from statline.gateway.storage import cache
from statline.gateway.storage.sqlite import get_conn


@pytest.fixture
def cache_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("STATLINE_DB", str(db_path))
    cache._ensure_scope_config_table()
    return db_path


def test_scope_config_round_trip_and_iteration(cache_db: Path) -> None:
    assert cache.get_scope_config("league-a") is None

    cache.update_scope_config("league-a", last_sync_ts=123)
    cache.update_scope_config("league-b", last_sync_ts=None)

    config = cache.get_scope_config("league-a")
    assert config is not None
    assert config.scope == "league-a"
    assert config.last_sync_ts == 123
    assert list(cache.iterate_scopes()) == ["league-a", "league-b"]


def test_legacy_guild_config_fallback(cache_db: Path) -> None:
    with get_conn() as conn:
        conn.execute("CREATE TABLE guild_config (guild_id TEXT PRIMARY KEY, last_sync_ts INTEGER)")
        conn.execute("INSERT INTO guild_config VALUES (?, ?)", ("legacy", 456))

    config = cache.get_scope_config("legacy")
    assert config is not None
    assert config.scope == "legacy"
    assert config.last_sync_ts == 456


def test_iterate_scopes_falls_back_to_entity_table(cache_db: Path) -> None:
    with get_conn() as conn:
        conn.execute("CREATE TABLE entities (scope TEXT, fuzzy_key TEXT)")
        conn.executemany(
            "INSERT INTO entities(scope, fuzzy_key) VALUES (?, ?)",
            [("b", "two"), ("a", "one"), ("a", "three"), (None, "none")],
        )

    assert list(cache.iterate_scopes()) == ["a", "b"]


def test_stale_logic_and_should_sync(cache_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "now_ts", lambda: 1_000)
    cache.update_scope_config("fresh", last_sync_ts=950)
    cache.update_scope_config("stale", last_sync_ts=800)

    assert not cache.should_sync_scope("fresh", ttl_sec=100)
    assert cache.should_sync_scope("stale", ttl_sec=100)
    assert not cache.should_sync_scope("missing", ttl_sec=100)
    assert cache._stale_since(None, 100)


def test_sync_scope_if_stale_success_skip_missing_and_failure(
    cache_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "now_ts", lambda: 1_000)
    cache.update_scope_config("fresh", last_sync_ts=990)
    cache.update_scope_config("stale", last_sync_ts=1)

    calls: list[str] = []

    def sync(scope: str) -> int:
        calls.append(scope)
        return 3

    monkeypatch.setattr(cache, "_SYNC_FUNC", sync)

    assert cache.sync_scope_if_stale("fresh", ttl_sec=100) == 0
    assert calls == []
    assert cache.sync_scope_if_stale("stale", ttl_sec=100) == 3
    assert calls == ["stale"]
    stale = cache.get_scope_config("stale")
    assert stale is not None and stale.last_sync_ts == 1_000

    monkeypatch.setattr(cache, "_SYNC_FUNC", None)
    assert cache.sync_scope_if_stale("stale", force=True) == -1


def test_refresh_all_scopes_isolates_failures(
    cache_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache.update_scope_config("a", last_sync_ts=1)
    cache.update_scope_config("b", last_sync_ts=1)

    def fake_sync(scope: str, *, ttl_sec: int, force: bool) -> int:
        assert ttl_sec == 10
        assert force
        if scope == "b":
            raise RuntimeError("boom")
        return 7

    monkeypatch.setattr(cache, "sync_scope_if_stale", fake_sync)
    assert cache.refresh_all_scopes(ttl_sec=10, force=True) == {"a": 7, "b": -1}


def test_entity_and_metric_queries(cache_db: Path) -> None:
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE entities (scope TEXT, fuzzy_key TEXT, display_name TEXT, group_name TEXT)"
        )
        conn.execute(
            "CREATE TABLE metrics (scope TEXT, fuzzy_key TEXT, metric_key TEXT, metric_value REAL)"
        )
        conn.executemany(
            "INSERT INTO entities VALUES (?, ?, ?, ?)",
            [
                ("league", "ada", "Ada", "A"),
                ("league", "grace", "Grace", None),
                ("other", "other", "Other", "Z"),
            ],
        )
        conn.executemany(
            "INSERT INTO metrics VALUES (?, ?, ?, ?)",
            [
                ("league", "ada", "PRI", 99.0),
                ("league", "ada", "GP", 12.0),
                ("league", "grace", "PRI", 95.0),
                ("other", "other", "PRI", 50.0),
            ],
        )

    entities = cache.get_entities_for_scope("league")
    assert {row["fuzzy_key"] for row in entities} == {"ada", "grace"}

    assert cache.get_metrics_for_entity("league", "ada") == {"PRI": 99.0, "GP": 12.0}

    metrics = cache.get_metrics_for_scope("league")
    assert len(metrics) == 3
    assert {row["fuzzy_key"] for row in metrics} == {"ada", "grace"}
    assert cache.get_distinct_metric_keys("league") == ["GP", "PRI"]


def test_missing_storage_shapes_return_empty(cache_db: Path) -> None:
    with get_conn() as conn:
        conn.execute("CREATE TABLE entities (unrelated TEXT)")
        conn.execute("CREATE TABLE metrics (unrelated TEXT)")

    assert cache.get_entities_for_scope("x") == []
    assert cache.get_metrics_for_entity("x", "y") == {}
    assert cache.get_metrics_for_scope("x") == []
    assert cache.get_distinct_metric_keys("x") == []


def test_coerce_sync_normalizes_result_types() -> None:
    def string_result(_scope: str) -> object:
        return "4"

    def none_result(_scope: str) -> object:
        return None

    def bad_result(_scope: str) -> object:
        return "bad"

    assert cache._coerce_sync(string_result)("x") == 4
    assert cache._coerce_sync(none_result)("x") == 0
    assert cache._coerce_sync(bad_result)("x") == -1
