from __future__ import annotations

import sqlite3

from .helpers import columns, now_ts, quote_identifier, rename_to_backup, table_exists


TURN_SQL = """
CREATE TABLE paper_reader_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    conversation_id TEXT NOT NULL REFERENCES reader_conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
)
"""


def _create_conversations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reader_conversations (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES auth_users(id),
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            title TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(user_id, paper_id, id)
        )
        """
    )


def _turns_are_canonical(conn: sqlite3.Connection) -> bool:
    required = {
        "id",
        "user_id",
        "paper_id",
        "conversation_id",
        "role",
        "content",
        "metadata_json",
        "created_at",
    }
    return required.issubset(columns(conn, "paper_reader_turns"))


def migrate(conn: sqlite3.Connection) -> None:
    _create_conversations(conn)
    if not table_exists(conn, "paper_reader_turns"):
        conn.execute(TURN_SQL)
    elif not _turns_are_canonical(conn):
        backup = rename_to_backup(conn, "paper_reader_turns")
        assert backup is not None
        conn.execute(TURN_SQL)
        old_cols = columns(conn, backup)
        if {"paper_id", "role", "content"}.issubset(old_cols):
            rows = conn.execute(
                f"""
                SELECT t.*, p.user_id AS owner_id
                FROM {quote_identifier(backup)} t
                JOIN papers p ON p.id=t.paper_id
                ORDER BY t.id ASC
                """
            ).fetchall()
            now = now_ts()
            seen: set[tuple[int, int]] = set()
            for row in rows:
                user_id = int(row["owner_id"])
                paper_id = int(row["paper_id"])
                conv_id = f"legacy-{user_id}-{paper_id}"
                key = (user_id, paper_id)
                if key not in seen:
                    created = int(row["created_at"] or now) if "created_at" in row.keys() else now
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO reader_conversations(
                            id,user_id,paper_id,title,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (conv_id, user_id, paper_id, "迁移的阅读记录", created, now),
                    )
                    seen.add(key)
                metadata = "{}"
                if "metadata_json" in row.keys() and row["metadata_json"]:
                    metadata = str(row["metadata_json"])
                elif "metadata" in row.keys() and row["metadata"]:
                    metadata = str(row["metadata"])
                role = str(row["role"] or "user").lower()
                if role not in {"user", "assistant", "tool"}:
                    role = "user"
                created = int(row["created_at"] or now) if "created_at" in row.keys() else now
                conn.execute(
                    """
                    INSERT INTO paper_reader_turns(
                        id,user_id,paper_id,conversation_id,role,content,
                        metadata_json,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(row["id"]),
                        user_id,
                        paper_id,
                        conv_id,
                        role,
                        str(row["content"] or ""),
                        metadata,
                        created,
                    ),
                )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reader_conversations_scope
            ON reader_conversations(user_id, paper_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reader_turns_scope
            ON paper_reader_turns(user_id, paper_id, conversation_id, id)
        """
    )
