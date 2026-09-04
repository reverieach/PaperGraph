from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """Make persisted ingest jobs recoverable after a worker interruption.

    ``ingest_jobs`` used to have a lease but no durable retry schedule or
    heartbeat record.  A crashed process could therefore leave a row looking
    permanently ``running`` and a failed attempt could not be distinguished
    from a job that had never been retried.  These fields are deliberately in
    SQLite (the queue source of truth), not in process memory.
    """

    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(ingest_jobs)").fetchall()
    }
    additions = (
        ("next_attempt_at", "INTEGER"),
        ("last_heartbeat_at", "INTEGER"),
    )
    for name, declaration in additions:
        if name not in columns:
            conn.execute(f"ALTER TABLE ingest_jobs ADD COLUMN {name} {declaration}")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ingest_jobs_ready
            ON ingest_jobs(status, next_attempt_at, lease_expires_at, created_at)
        """
    )
