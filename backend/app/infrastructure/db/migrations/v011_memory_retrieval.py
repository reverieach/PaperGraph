from __future__ import annotations

import sqlite3


def _add_column_if_missing(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    definition: str,
) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _create_memory_fts(
    conn: sqlite3.Connection,
    *,
    table_name: str,
    tokenizer: str,
    suffix: str,
) -> None:
    """Create an external-content Memory FTS projection and maintenance triggers."""

    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {table_name} USING fts5(
            kind,
            content,
            content='memories',
            content_rowid='rowid',
            tokenize='{tokenizer}'
        )
        """
    )
    conn.executescript(
        f"""
        CREATE TRIGGER IF NOT EXISTS memories_{suffix}_ai
        AFTER INSERT ON memories BEGIN
            INSERT INTO {table_name}(rowid, kind, content)
            VALUES (new.rowid, new.kind, new.content);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_{suffix}_ad
        AFTER DELETE ON memories BEGIN
            INSERT INTO {table_name}({table_name}, rowid, kind, content)
            VALUES ('delete', old.rowid, old.kind, old.content);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_{suffix}_au
        AFTER UPDATE ON memories BEGIN
            INSERT INTO {table_name}({table_name}, rowid, kind, content)
            VALUES ('delete', old.rowid, old.kind, old.content);
            INSERT INTO {table_name}(rowid, kind, content)
            VALUES (new.rowid, new.kind, new.content);
        END;
        """
    )
    conn.execute(f"INSERT INTO {table_name}({table_name}) VALUES('rebuild')")


def _drop_memory_fts(conn: sqlite3.Connection, *, table_name: str, suffix: str) -> None:
    conn.execute(f"DROP TRIGGER IF EXISTS memories_{suffix}_ai")
    conn.execute(f"DROP TRIGGER IF EXISTS memories_{suffix}_ad")
    conn.execute(f"DROP TRIGGER IF EXISTS memories_{suffix}_au")
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")


def migrate(conn: sqlite3.Connection) -> None:
    """Add bounded, scope-safe lexical retrieval support for confirmed Memory.

    Memory remains canonical in SQLite and is only written through the existing
    draft/confirmation workflow.  The two FTS tables are rebuildable search
    projections; they do not create a second Memory source of truth.
    """

    _add_column_if_missing(
        conn,
        table="memories",
        column="importance",
        definition="importance REAL NOT NULL DEFAULT 0.5",
    )
    _add_column_if_missing(
        conn,
        table="memories",
        column="expires_at",
        definition="expires_at INTEGER",
    )
    _add_column_if_missing(
        conn,
        table="memories",
        column="superseded_by",
        definition="superseded_by TEXT",
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_memories_retrieval_scope
            ON memories(
                user_id, status, confirmed_by_user, scope_type, scope_id,
                expires_at, importance DESC, updated_at DESC
            )
        """
    )

    try:
        _create_memory_fts(
            conn,
            table_name="memories_fts",
            tokenizer="unicode61 remove_diacritics 2",
            suffix="fts",
        )
    except sqlite3.OperationalError:
        _drop_memory_fts(conn, table_name="memories_fts", suffix="fts")

    try:
        _create_memory_fts(
            conn,
            table_name="memories_trigram_fts",
            tokenizer="trigram case_sensitive 0 remove_diacritics 1",
            suffix="trigram_fts",
        )
    except sqlite3.OperationalError:
        _drop_memory_fts(
            conn,
            table_name="memories_trigram_fts",
            suffix="trigram_fts",
        )
