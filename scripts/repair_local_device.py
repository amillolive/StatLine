from __future__ import annotations

import sqlite3
from pathlib import Path

from statline.cli import DEVICEID_PATH, APIKEY_PATH

try:
    from statline.slapi.auth import DB_PATH
except Exception as e:
    raise SystemExit(f"Could not import DB_PATH from statline.slapi.auth: {e}")

device_id = DEVICEID_PATH.read_text(encoding="utf-8").strip()
api_key = APIKEY_PATH.read_text(encoding="utf-8").strip()
api_prefix = api_key[4:12] if api_key.startswith("api_") else api_key[:8]

print(f"DB:        {DB_PATH}")
print(f"DEVICEID:  {device_id}")
print(f"API prefix:{api_prefix}")

db = Path(DB_PATH)
if not db.exists():
    raise SystemExit(f"DB does not exist: {db}")

con = sqlite3.connect(str(db))
con.row_factory = sqlite3.Row


def table_names() -> list[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(r["name"]) for r in rows]


def columns(table: str) -> list[str]:
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(r["name"]) for r in rows]


print("\nTables:")
for t in table_names():
    print(f"  - {t}")

print("\nDevice-like rows before repair:")
for t in table_names():
    cols = columns(t)
    if "device_id" not in cols:
        continue

    rows = con.execute(
        f"SELECT * FROM {t} WHERE device_id = ?",
        (device_id,),
    ).fetchall()

    if rows:
        print(f"\n[{t}]")
        for row in rows:
            print(dict(row))

print("\nAPI-key-like rows before repair:")
for t in table_names():
    cols = columns(t)
    possible_prefix_cols = [c for c in cols if "prefix" in c.lower()]
    if not possible_prefix_cols:
        continue

    for prefix_col in possible_prefix_cols:
        rows = con.execute(
            f"SELECT * FROM {t} WHERE {prefix_col} = ?",
            (api_prefix,),
        ).fetchall()

        if rows:
            print(f"\n[{t}] via {prefix_col}")
            for row in rows:
                print(dict(row))

updates: list[tuple[str, str]] = []

for t in table_names():
    cols = columns(t)

    if "device_id" in cols:
        if "status" in cols:
            con.execute(
                f"UPDATE {t} SET status = ? WHERE device_id = ?",
                ("ACTIVE", device_id),
            )
            updates.append((t, "status=ACTIVE"))

        if "active" in cols:
            con.execute(
                f"UPDATE {t} SET active = 1 WHERE device_id = ?",
                (device_id,),
            )
            updates.append((t, "active=1"))

        if "access" in cols:
            con.execute(
                f"UPDATE {t} SET access = 1 WHERE device_id = ?",
                (device_id,),
            )
            updates.append((t, "access=1"))

    prefix_cols = [c for c in cols if "prefix" in c.lower()]
    for prefix_col in prefix_cols:
        if "access" in cols:
            con.execute(
                f"UPDATE {t} SET access = 1 WHERE {prefix_col} = ?",
                (api_prefix,),
            )
            updates.append((t, f"access=1 where {prefix_col}={api_prefix}"))

        if "active" in cols:
            con.execute(
                f"UPDATE {t} SET active = 1 WHERE {prefix_col} = ?",
                (api_prefix,),
            )
            updates.append((t, f"active=1 where {prefix_col}={api_prefix}"))

con.commit()

print("\nApplied updates:")
if updates:
    for table, update in updates:
        print(f"  - {table}: {update}")
else:
    print("  none")

print("\nDevice-like rows after repair:")
for t in table_names():
    cols = columns(t)
    if "device_id" not in cols:
        continue

    rows = con.execute(
        f"SELECT * FROM {t} WHERE device_id = ?",
        (device_id,),
    ).fetchall()

    if rows:
        print(f"\n[{t}]")
        for row in rows:
            print(dict(row))

con.close()

print("\nDone. Restart SLAPI, then run:")
print("  statline --mode auto auth whoami")
