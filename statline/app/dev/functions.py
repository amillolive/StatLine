"""Developer maintenance functions for local StatLine installations."""

from __future__ import annotations

import platform
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from statline.app.cli.main import (
    APIKEY_PATH,
    DEVICEID_PATH,
    KEYS_DIR,
    device_public_key_b64,
    ensure_device_keypair,
)
from statline.gateway.auth.service import (
    DB_PATH,
    DEVKEY_PATH,
    admin_approve_enrollment,
    admin_generate_devkey_files,
    admin_mint_regtoken,
    create_api_key_for_device,
    create_enrollment_request,
)


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table)})").fetchall()
    return [str(row[1]) for row in rows]


def _current_auth() -> tuple[str, str]:
    device_id = DEVICEID_PATH.read_text(encoding="utf-8").strip()
    api_key = APIKEY_PATH.read_text(encoding="utf-8").strip()
    return device_id, api_key[4:12] if api_key.startswith("api_") else api_key[:8]


def _print_matching_rows(
    connection: sqlite3.Connection,
    tables: Iterable[str],
    column: str,
    value: str,
    *,
    heading: str,
) -> None:
    print(heading)
    for table in tables:
        cols = _columns(connection, table)
        matches = [
            name
            for name in cols
            if column == name or (column == "prefix" and "prefix" in name.lower())
        ]
        for match in matches:
            rows = connection.execute(
                f"SELECT * FROM {_quote_identifier(table)} WHERE {_quote_identifier(match)} = ?",
                (value,),
            ).fetchall()
            if rows:
                print(f"\n[{table}]" + (f" via {match}" if match != column else ""))
                for row in rows:
                    print(dict(row))


def _update_exact(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    old: str,
    new: str,
    *,
    where_extra: str = "",
    where_params: Sequence[object] = (),
) -> int:
    if old == new:
        return 0
    sql = (
        f"UPDATE {_quote_identifier(table)} SET {_quote_identifier(column)} = ? "
        f"WHERE {_quote_identifier(column)} = ?"
    )
    params: tuple[object, ...] = (new, old, *where_params)
    if where_extra:
        sql += f" AND ({where_extra})"
    cursor = connection.execute(sql, params)
    return int(cursor.rowcount or 0)


def bootstrap_local_admin(
    *,
    org: str = "statline",
    user: str = "conner",
    email: str = "conner.walston@valpo.edu",
    scopes: Sequence[str] | None = None,
) -> None:
    """Bootstrap the first local administrator and persist its device credentials."""
    granted_scopes = list(scopes or ["admin"])
    print(f"Auth DB: {DB_PATH}")
    if not DEVKEY_PATH.exists():
        print("DEVKEY missing. Generating DEVKEY...")
        print(admin_generate_devkey_files(overwrite=False))
    else:
        print(f"DEVKEY present: {DEVKEY_PATH}")

    private_key = ensure_device_keypair(force=False)
    regtoken = admin_mint_regtoken(org=org, scopes=granted_scopes, ttl_days=None)
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    regtoken_path = KEYS_DIR / "bootstrap.regt"
    regtoken_path.write_text(regtoken, encoding="utf-8")
    print(f"Minted local bootstrap regtoken: {regtoken_path}")

    enrollment = create_enrollment_request(
        reg_token=regtoken,
        user=user,
        email=email,
        device_pub_b64=device_public_key_b64(private_key),
        meta={
            "hostname": platform.node(),
            "os": platform.platform(),
            "cli_version": "local-bootstrap",
        },
    )
    request_id = enrollment["request_id"]
    device_id = enrollment["device_id"]
    print(f"Created enrollment request: {request_id}")
    print(f"Device ID: {device_id}")
    if not admin_approve_enrollment(
        request_id=request_id,
        decided_by="local-bootstrap",
        decision_note="Bootstrap first local admin device",
    ):
        raise RuntimeError("Failed to approve enrollment")

    api_token, record = create_api_key_for_device(
        device_id=device_id,
        owner=user,
        scopes=granted_scopes,
        ttl_days=3650,
    )
    DEVICEID_PATH.write_text(device_id, encoding="utf-8")
    APIKEY_PATH.write_text(api_token, encoding="utf-8")
    print("\nLocal admin bootstrap complete.")
    print(f"DEVICEID written: {DEVICEID_PATH}")
    print(f"APIKEY written:   {APIKEY_PATH}")
    print(f"API prefix:       {record['prefix']}")
    print("\nNext:\n  statline --mode auto auth status\n  statline --mode auto auth whoami")


def rename_local_auth_identity(
    *,
    old_org: str = "statline",
    new_org: str = "statline-dev",
    old_owner: str = "conner",
    new_owner: str = "amillo-dev",
    old_user: str = "conner",
    new_user: str = "amillo-dev",
    old_email: str = "conner.walston@valpo.edu",
    new_email: str = "support@statline.dev",
    target_current_auth: bool = True,
) -> list[str]:
    """Rename local auth identity fields and return a human-readable change log."""
    database = Path(DB_PATH)
    if not database.exists():
        raise FileNotFoundError(f"Auth DB does not exist: {database}")
    device_id, api_prefix = _current_auth()
    backup = database.with_suffix(
        f".before-rename-{datetime.now(timezone.utc).astimezone():%Y%m%d-%H%M%S}{database.suffix}"
    )
    shutil.copy2(database, backup)
    print(
        f"DB:        {database}\nBackup:    {backup}\nDEVICEID:  {device_id}\nAPI prefix:{api_prefix}\n"
    )

    rename_map = {
        "org": (old_org, new_org),
        "owner": (old_owner, new_owner),
        "user": (old_user, new_user),
        "username": (old_user, new_user),
        "email": (old_email, new_email),
    }
    changes: list[str] = []
    with sqlite3.connect(str(database)) as connection:
        for table in _table_names(connection):
            cols = _columns(connection, table)
            selectors: list[tuple[str, tuple[object, ...], str]] = [("", (), "")]
            if target_current_auth and "device_id" in cols:
                selectors.append(
                    (f"{_quote_identifier('device_id')} = ?", (device_id,), " on current device")
                )
            selectors.extend(
                (f"{_quote_identifier(column)} = ?", (api_prefix,), f" on API prefix {api_prefix}")
                for column in cols
                if "prefix" in column.lower()
            )
            for column, (old, new) in rename_map.items():
                if column not in cols:
                    continue
                for where, params, label in selectors:
                    count = _update_exact(
                        connection,
                        table,
                        column,
                        old,
                        new,
                        where_extra=where,
                        where_params=params,
                    )
                    if count:
                        changes.append(
                            f"{table}.{column}{label}: {old!r} -> {new!r} ({count} row/s)"
                        )

    print("Applied changes:")
    for change in changes:
        print(f"  - {change}")
    if not changes:
        print("  none")
    print(
        "\nDone. Restart SLAPI, then check:\n  statline --mode auto auth whoami\n  statline --mode remote mod apikeys"
    )
    return changes


def repair_local_device() -> list[tuple[str, str]]:
    """Reactivate the current local device and API-key records where supported."""
    database = Path(DB_PATH)
    if not database.exists():
        raise FileNotFoundError(f"DB does not exist: {database}")
    device_id, api_prefix = _current_auth()
    print(f"DB:        {database}\nDEVICEID:  {device_id}\nAPI prefix:{api_prefix}")

    updates: list[tuple[str, str]] = []
    with sqlite3.connect(str(database)) as connection:
        connection.row_factory = sqlite3.Row
        tables = _table_names(connection)
        print("\nTables:")
        for table in tables:
            print(f"  - {table}")
        _print_matching_rows(
            connection, tables, "device_id", device_id, heading="\nDevice-like rows before repair:"
        )
        _print_matching_rows(
            connection, tables, "prefix", api_prefix, heading="\nAPI-key-like rows before repair:"
        )

        for table in tables:
            cols = _columns(connection, table)
            selectors: list[tuple[str, str]] = []
            if "device_id" in cols:
                selectors.append(("device_id", device_id))
            selectors.extend((column, api_prefix) for column in cols if "prefix" in column.lower())
            for selector, value in selectors:
                for column, replacement in (("status", "ACTIVE"), ("active", 1), ("access", 1)):
                    if column not in cols:
                        continue
                    connection.execute(
                        f"UPDATE {_quote_identifier(table)} SET {_quote_identifier(column)} = ? "
                        f"WHERE {_quote_identifier(selector)} = ?",
                        (replacement, value),
                    )
                    updates.append((table, f"{column}={replacement} where {selector}={value}"))

        print("\nApplied updates:")
        for table, update in updates:
            print(f"  - {table}: {update}")
        if not updates:
            print("  none")
        _print_matching_rows(
            connection, tables, "device_id", device_id, heading="\nDevice-like rows after repair:"
        )

    print("\nDone. Restart SLAPI, then run:\n  statline --mode auto auth whoami")
    return updates


__all__ = ["bootstrap_local_admin", "rename_local_auth_identity", "repair_local_device"]
