from __future__ import annotations

import sqlite3

import pytest

from app.infrastructure.db import Database, run_migrations
from app.infrastructure.db.migration_runner import MigrationError
from app.infrastructure.db.migrations import Migration
from app.infrastructure.db.schema_validator import (
    SchemaValidationError,
    validate_schema,
)


def _table_names(db_path: str) -> set[str]:
    with Database(db_path).read() as conn:
        return {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }


def test_fresh_migration_is_complete_idempotent_and_safe(tmp_path) -> None:
    db_path = str(tmp_path / "fresh.db")

    run_migrations(db_path)
    run_migrations(db_path)

    with Database(db_path).read() as conn:
        versions = [
            int(row["version"])
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
        ]
        assert versions == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == 5000
        journal_mode = str(
            conn.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        assert journal_mode == "wal"

    assert {
        "auth_users",
        "papers",
        "reader_conversations",
        "paper_reader_turns",
        "memory_drafts",
        "memories",
        "research_sessions",
        "research_session_papers",
        "research_turns",
    }.issubset(_table_names(db_path))


def test_legacy_schema_is_preserved_and_normalized(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            abstract TEXT,
            doi TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        INSERT INTO papers(title,abstract,doi,created_at,updated_at)
        VALUES(
            'Legacy Paper',
            'Legacy abstract',
            '10.1000/legacy',
            '2025-01-02 03:04:05',
            '2025-01-02 03:04:05'
        );

        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT,
            user_id TEXT,
            memory_type TEXT,
            content TEXT,
            properties TEXT,
            timestamp REAL
        );
        INSERT INTO memories(
            memory_id,user_id,memory_type,content,properties,timestamp
        ) VALUES(
            'm-old',
            'papergraph:paper:1',
            'summary',
            '  legacy   memory  ',
            '{"paper_id": 1}',
            1735787045
        );

        CREATE TABLE daily_recommend_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_key TEXT NOT NULL,
            paper_identity_key TEXT NOT NULL,
            identity_type TEXT NOT NULL,
            title TEXT,
            action TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        INSERT INTO daily_recommend_feedback(
            date_key,paper_identity_key,identity_type,title,action,created_at
        ) VALUES(
            '2025-01-02','doi:10.1000/legacy','doi','Legacy Paper','save',
            1735787045
        );
        """
    )
    conn.commit()
    conn.close()

    run_migrations(db_path)

    with Database(db_path).read() as migrated:
        legacy_user = migrated.execute(
            "SELECT id,status FROM auth_users WHERE username='__legacy__'"
        ).fetchone()
        assert legacy_user is not None
        assert legacy_user["status"] == "disabled"
        paper = migrated.execute(
            "SELECT user_id,title,created_at FROM papers WHERE id=1"
        ).fetchone()
        assert paper is not None
        assert int(paper["user_id"]) == int(legacy_user["id"])
        assert paper["title"] == "Legacy Paper"
        assert isinstance(paper["created_at"], int)
        memory = migrated.execute(
            """
            SELECT user_id,scope_type,scope_id,content,confirmed_by_user,
                   metadata_json
            FROM memories
            """
        ).fetchone()
        assert memory is not None
        assert int(memory["user_id"]) == int(legacy_user["id"])
        assert memory["scope_type"] == "paper"
        assert memory["scope_id"] == "1"
        assert memory["content"] == "legacy memory"
        assert int(memory["confirmed_by_user"]) == 0
        feedback = migrated.execute(
            """
            SELECT user_id,paper_identity_key,action
            FROM daily_recommend_feedback
            """
        ).fetchone()
        assert feedback is not None
        assert int(feedback["user_id"]) == int(legacy_user["id"])
        assert feedback["paper_identity_key"] == "doi:10.1000/legacy"
        assert feedback["action"] == "save"

    names = _table_names(db_path)
    assert any(name.startswith("papers_legacy_") for name in names)
    assert any(name.startswith("memories_legacy_") for name in names)


def test_unscoped_runtime_tables_are_archived_not_assigned_to_a_user(
    tmp_path,
    monkeypatch,
) -> None:
    from app.infrastructure.db import migration_runner

    db_path = str(tmp_path / "runtime-legacy.db")
    original = migration_runner.MIGRATIONS
    monkeypatch.setattr(migration_runner, "MIGRATIONS", original[:8])
    run_migrations(db_path, validate=False)
    monkeypatch.setattr(migration_runner, "MIGRATIONS", original)

    with Database(db_path).transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE negative_pref_memory (
                id INTEGER PRIMARY KEY,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                identity_key TEXT,
                title TEXT,
                payload_json TEXT NOT NULL
            );
            INSERT INTO negative_pref_memory VALUES(1, 1, 2, 'legacy', 'Legacy skip', '{}');
            CREATE TABLE negative_pref_longterm (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                value TEXT NOT NULL
            );
            INSERT INTO negative_pref_longterm VALUES(1, 'topic', 'legacy topic');
            CREATE TABLE paper_opening_cache (
                paper_id INTEGER PRIMARY KEY,
                opening TEXT,
                updated_at INTEGER
            );
            INSERT INTO paper_opening_cache VALUES(1, 'legacy opening', 1);
            """
        )

    run_migrations(db_path)

    with Database(db_path).read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM negative_pref_memory").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_opening_cache").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM negative_pref_memory_legacy_v009"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM negative_pref_longterm_legacy_v009"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_opening_cache_legacy_v009"
        ).fetchone()[0] == 1


def test_failed_migration_rolls_back_and_checksum_changes_are_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    from app.infrastructure.db import migration_runner

    db_path = str(tmp_path / "rollback.db")
    run_migrations(db_path)
    original = migration_runner.MIGRATIONS

    def fail_after_ddl(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE must_rollback(id INTEGER PRIMARY KEY)")
        raise RuntimeError("forced failure")

    monkeypatch.setattr(
        migration_runner,
        "MIGRATIONS",
        (*original, Migration(999, "forced_failure", "v1", fail_after_ddl)),
    )
    with pytest.raises(MigrationError, match="forced failure"):
        run_migrations(db_path, validate=False)
    assert "must_rollback" not in _table_names(db_path)

    monkeypatch.setattr(
        migration_runner,
        "MIGRATIONS",
        (
            Migration(
                original[0].version,
                original[0].name,
                "changed-checksum",
                original[0].apply,
            ),
            *original[1:],
        ),
    )
    with pytest.raises(MigrationError, match="checksum mismatch"):
        run_migrations(db_path, validate=False)


def test_schema_validator_rejects_a_damaged_schema(tmp_path) -> None:
    db_path = str(tmp_path / "damaged.db")
    run_migrations(db_path)
    with Database(db_path).transaction() as conn:
        conn.execute("DROP INDEX idx_memories_scope")
    with pytest.raises(SchemaValidationError, match="idx_memories_scope"):
        validate_schema(db_path)
