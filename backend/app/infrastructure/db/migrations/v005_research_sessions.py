from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_session_papers (
            session_id TEXT NOT NULL
                REFERENCES research_sessions(id) ON DELETE CASCADE,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            position INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(session_id, paper_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL
                REFERENCES research_sessions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_research_sessions_user_updated
            ON research_sessions(user_id, updated_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_research_session_papers_order
            ON research_session_papers(session_id, position)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_research_turns_session
            ON research_turns(user_id, session_id, id)
        """
    )
