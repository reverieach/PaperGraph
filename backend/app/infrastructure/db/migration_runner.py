from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from .migrations import MIGRATIONS


class MigrationError(RuntimeError):
    pass


def _checksum(version: int, name: str, seed: str) -> str:
    raw = f"{version}:{name}:{seed}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _connect(db_path: str) -> sqlite3.Connection:
    path = str(db_path)
    if path != ":memory:" and not path.startswith("file:"):
        Path(os.path.abspath(path)).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


def run_migrations(db_path: str, *, validate: bool = True) -> None:
    """Apply all pending schema migrations atomically, then validate."""

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()

        applied = {
            int(row["version"]): row
            for row in conn.execute(
                "SELECT version,name,checksum,applied_at FROM schema_migrations"
            ).fetchall()
        }
        for migration in MIGRATIONS:
            expected = _checksum(
                migration.version,
                migration.name,
                migration.checksum_seed,
            )
            current = applied.get(migration.version)
            if current is not None:
                if str(current["name"]) != migration.name or str(current["checksum"]) != expected:
                    raise MigrationError(
                        f"migration checksum mismatch: v{migration.version} {migration.name}"
                    )
                continue
            try:
                conn.execute("BEGIN IMMEDIATE")
                migration.apply(conn)
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version,name,checksum,applied_at)
                    VALUES(?,?,?,unixepoch())
                    """,
                    (migration.version, migration.name, expected),
                )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                raise MigrationError(
                    f"migration v{migration.version} {migration.name} failed: {exc}"
                ) from exc

        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()

    if validate:
        from .schema_validator import validate_schema

        validate_schema(db_path)
