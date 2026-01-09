# statline/slapi/cache.py
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

# Connection factory (must return sqlite3.Connection with row_factory=sqlite3.Row)
from .storage.sqlite import get_conn

# ──────────────────────────────────────────────────────────────────────────────
# Scope config (replaces guild-centric config)
#   Table we manage:
#     scope_config(scope TEXT PRIMARY KEY, last_sync_ts INTEGER)
#   Legacy read-only (if present):
#     guild_config(guild_id TEXT PRIMARY KEY, last_sync_ts INTEGER)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScopeConfig:
    scope: str
    last_sync_ts: Optional[int]  # UNIX seconds; None if never synced


def _ensure_scope_config_table() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scope_config (
                scope         TEXT PRIMARY KEY,
                last_sync_ts  INTEGER
            )
            """
        )
        conn.commit()


def now_ts() -> int:
    return int(time.time())


def _legacy_guild_config_lookup(scope: str) -> Optional[ScopeConfig]:
    """
    If a legacy guild_config exists, read from it (no writes).
    """
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "SELECT guild_id, last_sync_ts FROM guild_config WHERE guild_id = ?",
                (scope,),
            )
            row = cur.fetchone()
        except Exception:
            return None
    if not row:
        return None
    ts = row["last_sync_ts"]
    return ScopeConfig(scope=row["guild_id"], last_sync_ts=int(ts) if ts is not None else None)


def get_scope_config(scope: str) -> Optional[ScopeConfig]:
    _ensure_scope_config_table()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT scope, last_sync_ts FROM scope_config WHERE scope = ?",
            (scope,),
        )
        row = cur.fetchone()
    if row:
        ts = row["last_sync_ts"]
        return ScopeConfig(scope=row["scope"], last_sync_ts=int(ts) if ts is not None else None)
    # fall back to legacy, if present
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
        conn.commit()


def iterate_scopes() -> Iterable[str]:
    """
    Iterate known scopes. Prefer scope_config; if empty, fall back to entities.
    Works with either modern `entities.scope` or legacy `entities.guild_id`.
    """
    _ensure_scope_config_table()
    with get_conn() as conn:
        rows = conn.execute("SELECT scope FROM scope_config ORDER BY scope").fetchall()
        if rows:
            for r in rows:
                yield r["scope"]
            return

        # Fallback: discover from entities (modern or legacy)
        try:
            rows2 = conn.execute(
                """
                SELECT DISTINCT COALESCE(scope, guild_id) AS scope_coerced
                FROM entities
                WHERE scope IS NOT NULL OR guild_id IS NOT NULL
                ORDER BY scope_coerced
                """
            ).fetchall()
            for r in rows2:
                scope = r["scope_coerced"]
                if scope:
                    yield scope
        except Exception:
            return


# ──────────────────────────────────────────────────────────────────────────────
# Sheets sync resolution (optional layer)
#   Prefer scope-based hooks; fall back to legacy guild-based names.
#   Expected signature after coercion: (scope: str) -> int
# ──────────────────────────────────────────────────────────────────────────────

SyncFunc = Callable[[str], int]


def _coerce_sync(fn: Callable[..., Any]) -> SyncFunc:
    def runner(scope: str) -> int:
        result = fn(scope)
        try:
            return int(result if result is not None else 0)
        except Exception:
            return -1
    return runner


def _resolve_sync_func() -> Optional[SyncFunc]:
    candidates: List[Tuple[str, str]] = [
        # new-style modules
        ("statline.slapi.ingest.sheets", "sync_scope"),
        ("statline.slapi.sync.sheets", "sync_scope"),
        ("slapi.ingest.sheets", "sync_scope"),
        ("slapi.sync.sheets", "sync_scope"),
        ("slapi.sheets", "sync_scope"),
        # legacy (guild) names for back-compat
        ("statline.slapi.ingest.sheets", "sync_guild"),
        ("statline.slapi.sync.sheets", "sync_guild"),
        ("slapi.ingest.sheets", "sync_guild"),
        ("slapi.sync.sheets", "sync_guild"),
        ("slapi.sheets", "sync_guild"),
        ("slapi.sheets", "sync_guild_sheets"),
    ]
    for mod_name, attr in candidates:
        try:
            mod = importlib.import_module(mod_name)
            fn = getattr(mod, attr, None)
            if callable(fn):
                return _coerce_sync(fn)
        except Exception:
            continue
    return None


_SYNC_FUNC: Optional[SyncFunc] = _resolve_sync_func()

# Default TTL: 24 hours
DEFAULT_SHEETS_TTL_SEC = 24 * 60 * 60


# ──────────────────────────────────────────────────────────────────────────────
# Freshness / TTL
# ──────────────────────────────────────────────────────────────────────────────

def _stale_since(last_sync_ts: Optional[int], ttl_sec: int) -> bool:
    if not last_sync_ts:
        return True
    return (now_ts() - int(last_sync_ts)) >= int(ttl_sec)


def should_sync_scope(scope: str, *, ttl_sec: int = DEFAULT_SHEETS_TTL_SEC) -> bool:
    cfg = get_scope_config(scope)
    if cfg is None:
        return False
    return _stale_since(cfg.last_sync_ts, ttl_sec)


def sync_scope_if_stale(
    scope: str,
    *,
    ttl_sec: int = DEFAULT_SHEETS_TTL_SEC,
    force: bool = False,
) -> int:
    """
    If due (or force=True), call sync hook and stamp freshness.
    Returns upsert count (0 skipped, -1 if sync unavailable).
    """
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


# ──────────────────────────────────────────────────────────────────────────────
# Adapter-agnostic reads from local cache
#   Preferred schema:
#     entities(scope, fuzzy_key, display_name, group_name)
#     metrics(scope, fuzzy_key, metric_key, metric_value)
#   Legacy support (read-only):
#     entities(guild_id, …) and metrics(guild_id, …)
# ──────────────────────────────────────────────────────────────────────────────

def get_entities_for_scope(scope: str) -> List[Dict[str, Any]]:
    """
    Return all entities for a scope. If only legacy guild columns exist, they are used.
    Sorted: group_name present first, then group_name, then display_name.
    """
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT
                COALESCE(scope, guild_id) AS scope_coerced,
                fuzzy_key,
                display_name,
                group_name
            FROM entities
            WHERE COALESCE(scope, guild_id) = ?
            ORDER BY (group_name IS NULL) ASC, group_name ASC, display_name ASC
            """,
            (scope,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_metrics_for_entity(scope: str, fuzzy_key: str) -> Dict[str, float]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT metric_key, metric_value
            FROM metrics
            WHERE COALESCE(scope, guild_id) = ? AND fuzzy_key = ?
            """,
            (scope, fuzzy_key),
        )
        return {row["metric_key"]: float(row["metric_value"]) for row in cur.fetchall()}


def get_metrics_for_scope(scope: str) -> List[Dict[str, Any]]:
    """
    Flattened view of all metrics for a scope; joins modern or legacy columns.
    """
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT
                e.fuzzy_key,
                e.display_name,
                e.group_name,
                m.metric_key,
                m.metric_value
            FROM entities e
            JOIN metrics m
              ON COALESCE(e.scope, e.guild_id) = COALESCE(m.scope, m.guild_id)
             AND e.fuzzy_key = m.fuzzy_key
            WHERE COALESCE(e.scope, e.guild_id) = ?
            ORDER BY (e.group_name IS NULL) ASC, e.group_name ASC, e.display_name ASC, m.metric_key ASC
            """,
            (scope,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_distinct_metric_keys(scope: str) -> List[str]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT DISTINCT metric_key
            FROM metrics
            WHERE COALESCE(scope, guild_id) = ?
            ORDER BY metric_key ASC
            """,
            (scope,),
        )
        return [row["metric_key"] for row in cur.fetchall()]


# ──────────────────────────────────────────────────────────────────────────────
# Back-compat wrappers (deprecated): guild_* → scope_*
#   Keep these briefly to ease migration; callers should switch to scope- APIs.
# ──────────────────────────────────────────────────────────────────────────────

# Config / freshness
def get_guild_config(guild_id: str) -> Optional[ScopeConfig]:  # DEPRECATED
    return get_scope_config(guild_id)

def update_guild_config(guild_id: str, *, last_sync_ts: Optional[int]) -> None:  # DEPRECATED
    update_scope_config(guild_id, last_sync_ts=last_sync_ts)

def iterate_guilds() -> Iterable[str]:  # DEPRECATED
    return iterate_scopes()

def should_sync_guild(guild_id: str, *, ttl_sec: int = DEFAULT_SHEETS_TTL_SEC) -> bool:  # DEPRECATED
    return should_sync_scope(guild_id, ttl_sec=ttl_sec)

def sync_guild_if_stale(guild_id: str, *, ttl_sec: int = DEFAULT_SHEETS_TTL_SEC, force: bool = False) -> int:  # DEPRECATED
    return sync_scope_if_stale(guild_id, ttl_sec=ttl_sec, force=force)

def refresh_all_guilds(*, ttl_sec: int = DEFAULT_SHEETS_TTL_SEC, force: bool = False) -> Dict[str, int]:  # DEPRECATED
    return refresh_all_scopes(ttl_sec=ttl_sec, force=force)

# Reads
def get_entities_for_guild(guild_id: str) -> List[Dict[str, Any]]:  # DEPRECATED
    return get_entities_for_scope(guild_id)

def get_metrics_for_guild(guild_id: str) -> List[Dict[str, Any]]:  # DEPRECATED
    return get_metrics_for_scope(guild_id)

def get_distinct_metric_keys_for_guild(guild_id: str) -> List[str]:  # DEPRECATED
    return get_distinct_metric_keys(guild_id)
