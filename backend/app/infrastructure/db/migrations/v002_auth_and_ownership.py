from __future__ import annotations

import sqlite3

from .helpers import (
    columns,
    foreign_keys,
    legacy_owner_id,
    now_ts,
    quote_identifier,
    rename_to_backup,
    select_expr,
    table_exists,
)


READING_SESSIONS_SQL = """
CREATE TABLE paper_reading_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    duration_sec INTEGER NOT NULL,
    day_key TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
"""

DAILY_FEEDBACK_SQL = """
CREATE TABLE daily_recommend_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    date_key TEXT NOT NULL,
    paper_identity_key TEXT NOT NULL,
    identity_type TEXT NOT NULL,
    title TEXT,
    action TEXT NOT NULL,
    source_list TEXT,
    score_at_recommend REAL,
    created_at INTEGER NOT NULL
)
"""


def _is_owned_table(
    conn: sqlite3.Connection,
    table: str,
    required: set[str],
) -> bool:
    cols = columns(conn, table)
    if not required.issubset(cols):
        return False
    if not int(cols["user_id"]["notnull"]):
        return False
    return any(
        str(row["from"]) == "user_id"
        and str(row["table"]) == "auth_users"
        for row in foreign_keys(conn, table)
    )


def _ensure_reading_sessions(conn: sqlite3.Connection) -> None:
    required = {
        "id",
        "user_id",
        "paper_id",
        "duration_sec",
        "day_key",
        "created_at",
    }
    if not table_exists(conn, "paper_reading_sessions"):
        conn.execute(READING_SESSIONS_SQL)
        return
    if _is_owned_table(conn, "paper_reading_sessions", required):
        return

    backup = rename_to_backup(conn, "paper_reading_sessions")
    assert backup is not None
    old_names = set(columns(conn, backup))
    row_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(backup)}"
        ).fetchone()[0]
    )
    owner_id = legacy_owner_id(conn) if row_count else None
    conn.execute(READING_SESSIONS_SQL)
    if not row_count or "paper_id" not in old_names:
        return
    now = now_ts()
    old_id = (
        f"old.{quote_identifier('id')}" if "id" in old_names else "NULL"
    )
    old_duration = (
        f"old.{quote_identifier('duration_sec')}"
        if "duration_sec" in old_names
        else "0"
    )
    old_day = (
        f"old.{quote_identifier('day_key')}"
        if "day_key" in old_names
        else "''"
    )
    old_created = (
        f"old.{quote_identifier('created_at')}"
        if "created_at" in old_names
        else str(now)
    )
    conn.execute(
        f"""
        INSERT INTO paper_reading_sessions(
            id,user_id,paper_id,duration_sec,day_key,created_at
        )
        SELECT
            {old_id},
            COALESCE(p.user_id, {int(owner_id or 0)}),
            old.paper_id,
            COALESCE({old_duration}, 0),
            COALESCE({old_day}, ''),
            COALESCE({old_created}, {now})
        FROM {quote_identifier(backup)} old
        JOIN papers p ON p.id=old.paper_id
        """
    )


def _ensure_daily_feedback(conn: sqlite3.Connection) -> None:
    required = {
        "id",
        "user_id",
        "date_key",
        "paper_identity_key",
        "identity_type",
        "action",
        "created_at",
    }
    if not table_exists(conn, "daily_recommend_feedback"):
        conn.execute(DAILY_FEEDBACK_SQL)
        return
    if _is_owned_table(conn, "daily_recommend_feedback", required):
        return

    backup = rename_to_backup(conn, "daily_recommend_feedback")
    assert backup is not None
    old_names = set(columns(conn, backup))
    row_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {quote_identifier(backup)}"
        ).fetchone()[0]
    )
    owner_id = legacy_owner_id(conn) if row_count else None
    conn.execute(DAILY_FEEDBACK_SQL)
    if not row_count:
        return
    now = now_ts()
    conn.execute(
        f"""
        INSERT INTO daily_recommend_feedback(
            id,user_id,date_key,paper_identity_key,identity_type,title,action,
            source_list,score_at_recommend,created_at
        )
        SELECT
            {select_expr(old_names, "id", "NULL")},
            {int(owner_id or 0)},
            COALESCE({select_expr(old_names, "date_key", "''")}, ''),
            COALESCE({select_expr(old_names, "paper_identity_key", "''")}, ''),
            COALESCE({select_expr(old_names, "identity_type", "'legacy'")}, 'legacy'),
            {select_expr(old_names, "title")},
            COALESCE({select_expr(old_names, "action", "'legacy'")}, 'legacy'),
            {select_expr(old_names, "source_list")},
            {select_expr(old_names, "score_at_recommend")},
            COALESCE({select_expr(old_names, "created_at", str(now))}, {now})
        FROM {quote_identifier(backup)}
        """
    )


def _scope_optional_annotations(conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "paper_annotations"):
        return
    cols = columns(conn, "paper_annotations")
    if "user_id" not in cols:
        conn.execute(
            'ALTER TABLE "paper_annotations" ADD COLUMN user_id INTEGER'
        )
    owner_id = legacy_owner_id(conn)
    conn.execute(
        """
        UPDATE paper_annotations
        SET user_id = COALESCE(
            (SELECT p.user_id FROM papers p
             WHERE p.id = paper_annotations.paper_id),
            ?
        )
        WHERE user_id IS NULL
        """,
        (owner_id,),
    )


def migrate(conn: sqlite3.Connection) -> None:
    _ensure_reading_sessions(conn)
    _ensure_daily_feedback(conn)
    _scope_optional_annotations(conn)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reading_sessions_user_day
        ON paper_reading_sessions(user_id, day_key, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_feedback_user_created
        ON daily_recommend_feedback(user_id, created_at)
        """
    )
