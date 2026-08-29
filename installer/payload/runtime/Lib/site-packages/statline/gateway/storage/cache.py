"""Adapter-agnostic gateway cache and optional sync functions."""

from __future__ import annotations

import importlib
import sqlite3
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from statline.gateway.storage.sqlite import get_conn
from statline.gateway.storage.types import ScopeConfig


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row["name"]) for row in rows}


def _scope_expression(
    conn: sqlite3.Connection,
    table: str,
    *,
    alias: Optional[str] = None,
) -> Optional[str]:
    columns = _table_columns(conn, table)
    prefix = f"{alias}." if alias else ""
    scope = f'{prefix}"scope"'
    guild = f'{prefix}"guild_id"'
    if "scope" in columns and "guild_id" in columns:
        return f"COALESCE({scope}, {guild})"
    if "scope" in columns:
        return scope
    if "guild_id" in columns:
        return guild
    return None


def _ensure_scope_config_table() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scope_config (
                scope        TEXT PRIMARY KEY,
                last_sync_ts INTEGER
            )
            """
        )


def now_ts() -> int:
    return int(time.time())


def _legacy_guild_config_lookup(scope: str) -> Optional[ScopeConfig]:
    with get_conn(read_only=True) as conn:
        columns = _table_columns(conn, "guild_config")
        if not {"guild_id", "last_sync_ts"}.issubset(columns):
            return None
        row = conn.execute(
            "SELECT guild_id, last_sync_ts FROM guild_config WHERE guild_id = ?",
            (scope,),
        ).fetchone()
    if row is None:
        return None
    timestamp = row["last_sync_ts"]
    return ScopeConfig(
        scope=str(row["guild_id"]),
        last_sync_ts=int(timestamp) if timestamp is not None else None,
    )


def get_scope_config(scope: str) -> Optional[ScopeConfig]:
    _ensure_scope_config_table()
    with get_conn(read_only=True) as conn:
        row = conn.execute(
            "SELECT scope, last_sync_ts FROM scope_config WHERE scope = ?",
            (scope,),
        ).fetchone()
    if row is not None:
        timestamp = row["last_sync_ts"]
        return ScopeConfig(
            scope=str(row["scope"]),
            last_sync_ts=int(timestamp) if timestamp is not None else None,
        )
    return _legacy_guild_config_lookup(scope)


def update_scope_config(scope: str, *, last_sync_ts: Optional[int]) -> None:
    _ensure_scope_config_table()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO scope_config (scope, last_sync_ts)
            VALUES (?, ?)
            ON CONFLICT(scope) DO UPDATE SET last_sync_ts = excluded.last_sync_ts
            """,
            (scope, last_sync_ts),
        )


def iterate_scopes() -> Iterable[str]:
    _ensure_scope_config_table()
    with get_conn(read_only=True) as conn:
        configured = conn.execute("SELECT scope FROM scope_config ORDER BY scope").fetchall()
        if configured:
            scopes = [str(row["scope"]) for row in configured]
        else:
            expression = _scope_expression(conn, "entities")
            if expression is None:
                scopes = []
            else:
                rows = conn.execute(
                    f"""
                    SELECT DISTINCT {expression} AS scope_value
                    FROM entities
                    WHERE {expression} IS NOT NULL
                    ORDER BY scope_value
                    """
                ).fetchall()
                scopes = [str(row["scope_value"]) for row in rows if row["scope_value"]]
    yield from scopes


SyncFunc = Callable[[str], int]


def _coerce_sync(function: Callable[..., Any]) -> SyncFunc:
    def runner(scope: str) -> int:
        result = function(scope)
        try:
            return int(result if result is not None else 0)
        except (TypeError, ValueError):
            return -1

    return runner


def _resolve_sync_func() -> Optional[SyncFunc]:
    candidates: List[Tuple[str, str]] = [
        ("statline.gateway.ingest.sheets", "sync_scope"),
        ("statline.gateway.sync.sheets", "sync_scope"),
    ]
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
            function = getattr(module, attribute, None)
            if callable(function):
                return _coerce_sync(function)
        except (ImportError, AttributeError):
            continue
    return None


_SYNC_FUNC = _resolve_sync_func()
DEFAULT_SHEETS_TTL_SEC = 24 * 60 * 60


def _stale_since(last_sync_ts: Optional[int], ttl_sec: int) -> bool:
    return not last_sync_ts or (now_ts() - int(last_sync_ts)) >= int(ttl_sec)


def should_sync_scope(scope: str, *, ttl_sec: int = DEFAULT_SHEETS_TTL_SEC) -> bool:
    config = get_scope_config(scope)
    return config is not None and _stale_since(config.last_sync_ts, ttl_sec)


def sync_scope_if_stale(
    scope: str,
    *,
    ttl_sec: int = DEFAULT_SHEETS_TTL_SEC,
    force: bool = False,
) -> int:
    if not force and not should_sync_scope(scope, ttl_sec=ttl_sec):
        return 0
    if _SYNC_FUNC is None:
        return -1
    upserted = _SYNC_FUNC(scope)
    if upserted >= 0:
        update_scope_config(scope, last_sync_ts=now_ts())
    return upserted


def refresh_all_scopes(
    *,
    ttl_sec: int = DEFAULT_SHEETS_TTL_SEC,
    force: bool = False,
) -> Dict[str, int]:
    results: Dict[str, int] = {}
    for scope in iterate_scopes():
        try:
            results[scope] = sync_scope_if_stale(scope, ttl_sec=ttl_sec, force=force)
        except Exception:
            results[scope] = -1
    return results


def get_entities_for_scope(scope: str) -> List[Dict[str, Any]]:
    with get_conn(read_only=True) as conn:
        columns = _table_columns(conn, "entities")
        scope_expression = _scope_expression(conn, "entities")
        if scope_expression is None or "fuzzy_key" not in columns:
            return []
        display = '"display_name"' if "display_name" in columns else '"fuzzy_key"'
        group = '"group_name"' if "group_name" in columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT
                {scope_expression} AS scope,
                fuzzy_key,
                {display} AS display_name,
                {group} AS group_name
            FROM entities
            WHERE {scope_expression} = ?
            ORDER BY (group_name IS NULL) ASC, group_name ASC, display_name ASC
            """,
            (scope,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_metrics_for_entity(scope: str, fuzzy_key: str) -> Dict[str, float]:
    with get_conn(read_only=True) as conn:
        columns = _table_columns(conn, "metrics")
        scope_expression = _scope_expression(conn, "metrics")
        required = {"fuzzy_key", "metric_key", "metric_value"}
        if scope_expression is None or not required.issubset(columns):
            return {}
        rows = conn.execute(
            f"""
            SELECT metric_key, metric_value
            FROM metrics
            WHERE {scope_expression} = ? AND fuzzy_key = ?
            """,
            (scope, fuzzy_key),
        ).fetchall()
    return {str(row["metric_key"]): float(row["metric_value"]) for row in rows}


def get_metrics_for_scope(scope: str) -> List[Dict[str, Any]]:
    with get_conn(read_only=True) as conn:
        entity_columns = _table_columns(conn, "entities")
        metric_columns = _table_columns(conn, "metrics")
        entity_scope = _scope_expression(conn, "entities", alias="e")
        metric_scope = _scope_expression(conn, "metrics", alias="m")
        entity_required = {"fuzzy_key"}
        metric_required = {"fuzzy_key", "metric_key", "metric_value"}
        if (
            entity_scope is None
            or metric_scope is None
            or not entity_required.issubset(entity_columns)
            or not metric_required.issubset(metric_columns)
        ):
            return []
        display = 'e."display_name"' if "display_name" in entity_columns else 'e."fuzzy_key"'
        group = 'e."group_name"' if "group_name" in entity_columns else "NULL"
        rows = conn.execute(
            f"""
            SELECT
                e.fuzzy_key,
                {display} AS display_name,
                {group} AS group_name,
                m.metric_key,
                m.metric_value
            FROM entities AS e
            JOIN metrics AS m
              ON {entity_scope} = {metric_scope}
             AND e.fuzzy_key = m.fuzzy_key
            WHERE {entity_scope} = ?
            ORDER BY (group_name IS NULL) ASC, group_name ASC, display_name ASC, m.metric_key ASC
            """,
            (scope,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_distinct_metric_keys(scope: str) -> List[str]:
    with get_conn(read_only=True) as conn:
        columns = _table_columns(conn, "metrics")
        scope_expression = _scope_expression(conn, "metrics")
        if scope_expression is None or "metric_key" not in columns:
            return []
        rows = conn.execute(
            f"""
            SELECT DISTINCT metric_key
            FROM metrics
            WHERE {scope_expression} = ?
            ORDER BY metric_key ASC
            """,
            (scope,),
        ).fetchall()
    return [str(row["metric_key"]) for row in rows]


__all__ = [
    "DEFAULT_SHEETS_TTL_SEC",
    "get_distinct_metric_keys",
    "get_entities_for_scope",
    "get_metrics_for_entity",
    "get_metrics_for_scope",
    "get_scope_config",
    "iterate_scopes",
    "now_ts",
    "refresh_all_scopes",
    "should_sync_scope",
    "sync_scope_if_stale",
    "update_scope_config",
]
