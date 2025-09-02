from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


# -----------------------------------------------------------------------------
# Platform-aware default path (no extra deps)
# -----------------------------------------------------------------------------
def _default_data_dir() -> Path:
    # Respect override first
    env = os.getenv("STATLINE_DATA_DIR")
    if env:
        return Path(env).expanduser()

    if sys.platform.startswith("win"):
        base = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "StatLine"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StatLine"
    else:
        # Linux / *nix: XDG if set, else ~/.local/share
        xdg = os.getenv("XDG_DATA_HOME")
        base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
        return base / "statline"

_DEFAULT_DIR = _default_data_dir()
_DEFAULT_DB = _DEFAULT_DIR / "statline.db"

def get_db_path() -> Path:
    """Resolve the database path, honoring $STATLINE_DB and expanding ~."""
    env = os.getenv("STATLINE_DB")
    return Path(env).expanduser() if env else _DEFAULT_DB

def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool, timeout_s: float) -> None:
    # Safety & performance; gate writes for read-only handles.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout_s * 1000)}")
    if not read_only:
        # WAL is great for concurrent readers/writers; only valid on writable DB.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    # Keep temp data in memory; harmless in RO too.
    conn.execute("PRAGMA temp_store = MEMORY")

def connect(
    path: Path | str | None = None,
    *,
    read_only: bool = False,
    check_same_thread: bool = True,
    timeout: float = 30.0,  # avoid 'database is locked' under light contention
) -> sqlite3.Connection:
    """
    Create a new SQLite connection with sane defaults.
    Autocommit is enabled; use `transaction()` (savepoint-based) for atomic blocks.
    """
    p = Path(path).expanduser() if path else get_db_path()

    if not read_only:
        _ensure_parent(p)
        conn = sqlite3.connect(
            p,
            isolation_level=None,  # autocommit
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=check_same_thread,
            timeout=timeout,
        )
    else:
        # Read-only, immutable if possible (faster; prevents accidental writes).
        # Note: immutable requires a real FS path; keep it off for non-local URIs.
        uri = f"file:{p.as_posix()}?mode=ro&immutable=1"
        conn = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
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
) -> Iterator[sqlite3.Connection]:
    """Context-managed connection that always closes."""
    conn = connect(path, read_only=read_only, check_same_thread=check_same_thread, timeout=timeout)
    try:
        yield conn
    finally:
        conn.close()

@contextmanager
def transaction(conn: sqlite3.Connection, name: Optional[str] = None) -> Iterator[None]:
    sp = name or f"sp_{id(conn)}_{os.getpid()}"
    try:
        conn.execute(f"SAVEPOINT {sp}")
        yield
        conn.execute(f"RELEASE SAVEPOINT {sp}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        conn.execute(f"RELEASE SAVEPOINT {sp}")
        raise

__all__ = ["connect", "get_conn", "get_db_path", "transaction"]
