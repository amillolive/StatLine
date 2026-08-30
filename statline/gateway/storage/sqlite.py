"""Shared SQLite connection and transaction utilities for the gateway."""

from __future__ import annotations

import os
import platform
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Literal

_SAVEPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MEMORY_URI = "file:statline-gateway-memory?mode=memory&cache=shared"
_MEMORY_LOCK = RLock()
_memory_anchor_conn: sqlite3.Connection | None = None
IsolationLevel = Literal["DEFERRED", "IMMEDIATE", "EXCLUSIVE"] | None


def _default_data_dir() -> Path:
    env = os.getenv("STATLINE_DATA_DIR")
    if env:
        return Path(env).expanduser()

    system = platform.system()

    if system == "Windows":
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "StatLine"

    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "StatLine"

    xdg = os.getenv("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "statline"


_DEFAULT_DB = _default_data_dir() / "statline.db"


def get_db_path() -> Path | str:
    env = os.getenv("STATLINE_DB")
    if not env:
        return _DEFAULT_DB
    if env == ":memory:" or env.startswith("file:"):
        return env
    return Path(env).expanduser()


def _execute_pragma(conn: sqlite3.Connection, statement: str) -> None:
    try:
        conn.execute(statement)
    except sqlite3.DatabaseError:
        # Optional/version-specific PRAGMAs should never prevent a connection.
        return


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool, timeout_s: float) -> None:
    _execute_pragma(conn, "PRAGMA foreign_keys = ON")
    _execute_pragma(conn, f"PRAGMA busy_timeout = {max(0, int(timeout_s * 1000))}")
    _execute_pragma(conn, "PRAGMA temp_store = MEMORY")
    _execute_pragma(conn, "PRAGMA trusted_schema = OFF")
    if read_only:
        _execute_pragma(conn, "PRAGMA query_only = ON")
        return
    _execute_pragma(conn, "PRAGMA journal_mode = WAL")
    _execute_pragma(conn, "PRAGMA synchronous = NORMAL")
    _execute_pragma(conn, f"PRAGMA journal_size_limit = {32 * 1024 * 1024}")


def _append_query(uri: str, query: str) -> str:
    return f"{uri}{'&' if '?' in uri else '?'}{query}"


def _memory_anchor(timeout: float) -> None:
    global _memory_anchor_conn

    with _MEMORY_LOCK:
        if _memory_anchor_conn is not None:
            return

        anchor = sqlite3.connect(
            _MEMORY_URI,
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            timeout=timeout,
        )
        anchor.row_factory = sqlite3.Row
        _apply_pragmas(anchor, read_only=False, timeout_s=timeout)
        _memory_anchor_conn = anchor


def _connection_target(base: Path | str, *, read_only: bool, timeout: float) -> tuple[str, bool]:
    if isinstance(base, str) and base == ":memory:":
        _memory_anchor(timeout)
        return _MEMORY_URI, True

    if isinstance(base, str) and base.startswith("file:"):
        uri = base
        if read_only and "mode=" not in uri:
            uri = _append_query(uri, "mode=ro")
        return uri, True

    path = (base if isinstance(base, Path) else Path(base)).expanduser().resolve()
    if read_only:
        if not path.is_file():
            raise FileNotFoundError(f"SQLite database does not exist: {path}")
        return _append_query(path.as_uri(), "mode=ro"), True

    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path), False


def connect(
    path: Path | str | None = None,
    *,
    read_only: bool = False,
    check_same_thread: bool = True,
    timeout: float = 30.0,
    isolation_level: IsolationLevel = None,
) -> sqlite3.Connection:
    """Create a configured SQLite connection for file, URI, or shared-memory targets."""
    base = path if path is not None else get_db_path()
    target, uri = _connection_target(base, read_only=read_only, timeout=timeout)
    conn = sqlite3.connect(
        target,
        uri=uri,
        isolation_level=isolation_level,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=check_same_thread,
        timeout=timeout,
    )
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn, read_only=read_only, timeout_s=timeout)
    return conn


@contextmanager
def get_conn(
    path: Path | str | None = None,
    *,
    read_only: bool = False,
    check_same_thread: bool = True,
    timeout: float = 30.0,
    isolation_level: IsolationLevel = None,
) -> Generator[sqlite3.Connection, None, None]:
    conn = connect(
        path,
        read_only=read_only,
        check_same_thread=check_same_thread,
        timeout=timeout,
        isolation_level=isolation_level,
    )
    try:
        yield conn
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def transaction(
    conn: sqlite3.Connection,
    name: str | None = None,
) -> Generator[None, None, None]:
    savepoint = name or f"sp_{id(conn)}_{os.getpid()}"
    if not _SAVEPOINT_RE.fullmatch(savepoint):
        raise ValueError("savepoint name must be a SQL identifier")
    conn.execute(f'SAVEPOINT "{savepoint}"')
    try:
        yield
    except Exception:
        conn.execute(f'ROLLBACK TO SAVEPOINT "{savepoint}"')
        conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')
        raise
    else:
        conn.execute(f'RELEASE SAVEPOINT "{savepoint}"')


__all__ = ["connect", "get_conn", "get_db_path", "transaction"]
