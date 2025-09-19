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

def get_db_path() -> Path | str:
    env = os.getenv("STATLINE_DB")
    if not env:
        return _DEFAULT_DB
    # allow special handles/URIs
    if env == ":memory:" or env.startswith("file:"):
        return env
    return Path(env).expanduser()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool, timeout_s: float) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout_s * 1000)}")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA trusted_schema = OFF")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA journal_size_limit = 32MB")  # optional

def _is_special_path(s: str) -> bool:
    """True for SQLite specials we shouldn't Path-ify."""
    return s == ":memory:" or s.startswith("file:")

def connect(
    path: Path | str | None = None,
    *,
    read_only: bool = False,
    check_same_thread: bool = True,
    timeout: float = 30.0,
) -> sqlite3.Connection:
    """
    Create a new SQLite connection with sane defaults.
    - RW: regular filesystem path or special targets (file: URI, :memory:)
    - RO: uses URI with mode=ro; tries immutable=1 and falls back to plain ro
    """
    # Resolve base target from arg or env/default
    base: Path | str = path if path is not None else get_db_path()

    if not read_only:
        # Writable connections
        if isinstance(base, str) and _is_special_path(base):
            # Special targets
            if base.startswith("file:"):
                conn = sqlite3.connect(
                    base,
                    uri=True,
                    isolation_level=None,
                    detect_types=sqlite3.PARSE_DECLTYPES,
                    check_same_thread=check_same_thread,
                    timeout=timeout,
                )
            else:  # ":memory:"
                conn = sqlite3.connect(
                    base,
                    isolation_level=None,
                    detect_types=sqlite3.PARSE_DECLTYPES,
                    check_same_thread=check_same_thread,
                    timeout=timeout,
                )
        else:
            # Filesystem path
            p: Path = base if isinstance(base, Path) else Path(base).expanduser()
            _ensure_parent(p)
            conn = sqlite3.connect(
                p,
                isolation_level=None,
                detect_types=sqlite3.PARSE_DECLTYPES,
                check_same_thread=check_same_thread,
                timeout=timeout,
            )
    else:
        # Read-only connections (always via URI)
        if isinstance(base, str) and base.startswith("file:"):
            uri = base
            p_for_fallback: Optional[Path] = None
        elif isinstance(base, str) and base == ":memory:":
            # RO memory → shared cache alias; still a URI
            uri = "file::memory:?cache=shared&mode=ro"
            p_for_fallback = None
        else:
            p_ro: Path = base if isinstance(base, Path) else Path(base).expanduser()
            uri = f"file:{p_ro.as_posix()}?mode=ro&immutable=1"
            p_for_fallback = p_ro

        try:
            conn = sqlite3.connect(
                uri,
                uri=True,
                isolation_level=None,
                detect_types=sqlite3.PARSE_DECLTYPES,
                check_same_thread=check_same_thread,
                timeout=timeout,
            )
        except sqlite3.OperationalError:
            # Some builds don't support immutable=1 → retry plain ro (only for FS paths)
            if p_for_fallback is None:
                raise
            uri2 = f"file:{p_for_fallback.as_posix()}?mode=ro"
            conn = sqlite3.connect(
                uri2,
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
