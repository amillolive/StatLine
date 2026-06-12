from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from statline.cli import APIKEY_PATH, DEVICEID_PATH

try:
    from statline.slapi.auth import DB_PATH
except Exception as e:
    raise SystemExit(f"Could not import DB_PATH from statline.slapi.auth: {e}")


# ── CHANGE THESE ──────────────────────────────────────────────────────────────

OLD_ORG = "statline"
NEW_ORG = "statline-dev"

OLD_OWNER = "conner"
NEW_OWNER = "amillo-dev"

OLD_USER = "conner"
NEW_USER = "amillo-dev"

OLD_EMAIL = "conner.walston@valpo.edu"
NEW_EMAIL = "support@statline.dev"

# If True, only rows connected to the current DEVICEID / APIKEY prefix are preferred.
# Rows that only have org/owner/user/email columns are still updated by value.
TARGET_CURRENT_AUTH = True


# ── INTERNALS ────────────────────────────────────────────────────────────────


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_names(con: sqlite3.Connection) -> list[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(row[0]) for row in rows]


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    rows = con.execute(f"PRAGMA table_info({qident(table)})").fetchall()
    return [str(row[1]) for row in rows]


def update_exact(
    con: sqlite3.Connection,
    table: str,
    column: str,
    old: str,
    new: str,
    *,
    where_extra: str = "",
    where_params: tuple[object, ...] = (),
) -> int:
    if old == new:
        return 0

    sql = f"UPDATE {qident(table)} SET {qident(column)} = ? WHERE {qident(column)} = ?"

    params: tuple[object, ...] = (new, old)

    if where_extra:
        sql += f" AND ({where_extra})"
        params += where_params

    cur = con.execute(sql, params)
    return int(cur.rowcount or 0)


def main() -> None:
    db = Path(DB_PATH)

    if not db.exists():
        raise SystemExit(f"Auth DB does not exist: {db}")

    device_id = DEVICEID_PATH.read_text(encoding="utf-8").strip()
    api_key = APIKEY_PATH.read_text(encoding="utf-8").strip()
    api_prefix = api_key[4:12] if api_key.startswith("api_") else api_key[:8]

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db.with_suffix(f".before-rename-{stamp}{db.suffix}")
    shutil.copy2(db, backup)

    print(f"DB:        {db}")
    print(f"Backup:    {backup}")
    print(f"DEVICEID:  {device_id}")
    print(f"API prefix:{api_prefix}")
    print()

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row

    changes: list[str] = []

    for table in table_names(con):
        cols = columns(con, table)

        # Common auth metadata columns.
        rename_map = {
            "org": (OLD_ORG, NEW_ORG),
            "owner": (OLD_OWNER, NEW_OWNER),
            "user": (OLD_USER, NEW_USER),
            "username": (OLD_USER, NEW_USER),
            "email": (OLD_EMAIL, NEW_EMAIL),
        }

        # Update by exact value.
        for col, (old, new) in rename_map.items():
            if col not in cols:
                continue

            count = update_exact(con, table, col, old, new)

            if count:
                changes.append(f"{table}.{col}: {old!r} -> {new!r} ({count} row/s)")

        # Extra targeted updates for rows tied to your current device.
        if TARGET_CURRENT_AUTH and "device_id" in cols:
            for col, (old, new) in rename_map.items():
                if col not in cols:
                    continue

                count = update_exact(
                    con,
                    table,
                    col,
                    old,
                    new,
                    where_extra=f"{qident('device_id')} = ?",
                    where_params=(device_id,),
                )

                if count:
                    changes.append(
                        f"{table}.{col} on current device: {old!r} -> {new!r} ({count} row/s)"
                    )

        # Extra targeted updates for rows tied to your current API key prefix.
        prefix_cols = [c for c in cols if "prefix" in c.lower()]
        for prefix_col in prefix_cols:
            for col, (old, new) in rename_map.items():
                if col not in cols:
                    continue

                count = update_exact(
                    con,
                    table,
                    col,
                    old,
                    new,
                    where_extra=f"{qident(prefix_col)} = ?",
                    where_params=(api_prefix,),
                )

                if count:
                    changes.append(
                        f"{table}.{col} on API prefix {api_prefix}: "
                        f"{old!r} -> {new!r} ({count} row/s)"
                    )

    con.commit()

    print("Applied changes:")
    if changes:
        for change in changes:
            print(f"  - {change}")
    else:
        print("  none")

    print()
    print("Done. Restart SLAPI, then check:")
    print("  statline --mode auto auth whoami")
    print("  statline --mode remote mod apikeys")


if __name__ == "__main__":
    main()
