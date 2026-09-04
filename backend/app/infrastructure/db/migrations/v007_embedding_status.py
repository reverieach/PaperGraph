from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """Persist the rebuildable dense-projection lifecycle on each version."""

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(document_versions)").fetchall()
    }
    additions = (
        ("embedding_status", "TEXT NOT NULL DEFAULT 'not_indexed'"),
        ("embedding_indexed_count", "INTEGER NOT NULL DEFAULT 0"),
        ("embedding_error", "TEXT"),
        ("embedding_updated_at", "INTEGER"),
    )
    for name, declaration in additions:
        if name not in columns:
            conn.execute(
                f"ALTER TABLE document_versions ADD COLUMN {name} {declaration}"
            )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_versions_embedding_status
            ON document_versions(user_id, paper_id, embedding_status)
        """
    )

