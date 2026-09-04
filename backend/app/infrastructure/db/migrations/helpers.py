from __future__ import annotations

import sqlite3
import time
from typing import Iterable


def quote_identifier(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    if not table_exists(conn, table):
        return {}
    return {
        str(row["name"]): row
        for row in conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    }


def foreign_keys(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if not table_exists(conn, table):
        return []
    return list(conn.execute(f"PRAGMA foreign_key_list({quote_identifier(table)})").fetchall())


def backup_name(conn: sqlite3.Connection, base: str) -> str:
    candidate = f"{base}_legacy_phase1"
    suffix = 1
    while table_exists(conn, candidate):
        suffix += 1
        candidate = f"{base}_legacy_phase1_{suffix}"
    return candidate


def rename_to_backup(conn: sqlite3.Connection, table: str) -> str | None:
    if not table_exists(conn, table):
        return None
    target = backup_name(conn, table)
    conn.execute(
        f"ALTER TABLE {quote_identifier(table)} RENAME TO {quote_identifier(target)}"
    )
    return target


def select_expr(existing: Iterable[str], name: str, default_sql: str = "NULL") -> str:
    return quote_identifier(name) if name in set(existing) else default_sql


def now_ts() -> int:
    return int(time.time())


def legacy_owner_id(conn: sqlite3.Connection) -> int:
    now = now_ts()
    row = conn.execute(
        "SELECT id FROM auth_users WHERE username='__legacy__'"
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO auth_users(
            username, password_hash, status, created_at, updated_at
        ) VALUES('__legacy__', '!login-disabled!', 'disabled', ?, ?)
        """,
        (now, now),
    )
    if cur.lastrowid is None:
        raise RuntimeError("legacy auth user insert did not return an id")
    return int(cur.lastrowid)
