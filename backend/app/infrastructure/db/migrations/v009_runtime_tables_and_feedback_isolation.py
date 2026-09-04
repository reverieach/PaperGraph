from __future__ import annotations

import sqlite3


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _archive_unscoped_table(
    conn: sqlite3.Connection,
    table: str,
    *,
    required_columns: set[str],
) -> None:
    """Keep legacy runtime data but never assign it to an arbitrary user.

    The old runtime-created tables had no reliable owner.  Copying those rows
    to a real user would create a privacy leak, so they are retained under an
    explicit legacy name and excluded from all current service queries.
    """

    if not _table_exists(conn, table):
        return
    if required_columns.issubset(_table_columns(conn, table)):
        return
    legacy = f"{table}_legacy_v009"
    if _table_exists(conn, legacy):
        raise RuntimeError(f"cannot archive {table}: {legacy} already exists")
    conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')


def _archive_table_if_present(conn: sqlite3.Connection, table: str) -> None:
    if not _table_exists(conn, table):
        return
    legacy = f"{table}_legacy_v009"
    if _table_exists(conn, legacy):
        raise RuntimeError(f"cannot archive {table}: {legacy} already exists")
    conn.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')


def migrate(conn: sqlite3.Connection) -> None:
    """Move service-owned DDL into migrations and scope mutable data by user.

    Before this migration, Reader/Daily/KG/negative-feedback services created
    and altered tables during requests.  Negative feedback was additionally
    global and auto-promoted to long-term preferences.  The new tables make
    every mutable preference/cache/recommendation record user-scoped.  Legacy
    unscoped records are archived, rather than silently attributed to a user.
    """

    _archive_unscoped_table(
        conn,
        "negative_pref_memory",
        required_columns={
            "id",
            "user_id",
            "created_at",
            "expires_at",
            "identity_key",
            "title",
            "source",
            "category",
            "payload_json",
            "revoked_at",
        },
    )
    # Automatic long-term negative preferences are intentionally retired.
    _archive_table_if_present(conn, "negative_pref_longterm")
    _archive_unscoped_table(
        conn,
        "daily_papers_cache",
        required_columns={
            "user_id",
            "date_key",
            "cache_key",
            "payload_json",
            "created_at",
            "updated_at",
            "hit_count",
        },
    )
    _archive_unscoped_table(
        conn,
        "daily_recommendations",
        required_columns={
            "id",
            "user_id",
            "date_key",
            "source",
            "arxiv_id",
            "title",
            "created_at",
        },
    )
    _archive_unscoped_table(
        conn,
        "paper_relations",
        required_columns={
            "user_id",
            "source_paper_id",
            "target_paper_id",
            "relation",
            "score",
            "evidence",
            "created_at",
            "updated_at",
        },
    )
    _archive_unscoped_table(
        conn,
        "paper_pdf_excerpt_cache",
        required_columns={
            "user_id",
            "paper_id",
            "pdf_abspath",
            "pdf_mtime",
            "pdf_size",
            "excerpt",
            "excerpt_pages",
            "updated_at",
            "hit_count",
            "miss_count",
            "last_hit_at",
            "last_miss_at",
        },
    )
    _archive_unscoped_table(
        conn,
        "paper_opening_cache",
        required_columns={
            "user_id",
            "paper_id",
            "opening",
            "updated_at",
            "hit_count",
            "miss_count",
            "last_hit_at",
            "last_miss_at",
        },
    )

    conn.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_user_id_id
            ON papers(user_id, id);

        CREATE TABLE IF NOT EXISTS negative_pref_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            identity_key TEXT NOT NULL,
            title TEXT,
            source TEXT,
            category TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            revoked_at INTEGER,
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
            UNIQUE(user_id, identity_key)
        );
        CREATE INDEX IF NOT EXISTS idx_negpref_memory_user_exp
            ON negative_pref_memory(user_id, expires_at);

        CREATE TABLE IF NOT EXISTS daily_papers_cache (
            user_id INTEGER NOT NULL,
            date_key TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            hit_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, date_key, cache_key),
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_daily_papers_cache_user_date
            ON daily_papers_cache(user_id, date_key, updated_at);

        CREATE TABLE IF NOT EXISTS daily_recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date_key TEXT NOT NULL,
            source TEXT NOT NULL,
            arxiv_id TEXT,
            title TEXT,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_daily_reco_user_date
            ON daily_recommendations(user_id, date_key, created_at);
        CREATE INDEX IF NOT EXISTS idx_daily_reco_user_arxiv
            ON daily_recommendations(user_id, source, arxiv_id);

        CREATE TABLE IF NOT EXISTS paper_relations (
            user_id INTEGER NOT NULL,
            source_paper_id INTEGER NOT NULL,
            target_paper_id INTEGER NOT NULL,
            relation TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0,
            evidence TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(user_id, source_paper_id, target_paper_id, relation),
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, source_paper_id) REFERENCES papers(user_id, id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, target_paper_id) REFERENCES papers(user_id, id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_relations_user_source
            ON paper_relations(user_id, source_paper_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_paper_relations_user_target
            ON paper_relations(user_id, target_paper_id, updated_at);

        CREATE TABLE IF NOT EXISTS paper_pdf_excerpt_cache (
            user_id INTEGER NOT NULL,
            paper_id INTEGER NOT NULL,
            pdf_abspath TEXT,
            pdf_mtime INTEGER,
            pdf_size INTEGER,
            excerpt TEXT,
            excerpt_pages TEXT,
            updated_at INTEGER,
            hit_count INTEGER NOT NULL DEFAULT 0,
            miss_count INTEGER NOT NULL DEFAULT 0,
            last_hit_at INTEGER,
            last_miss_at INTEGER,
            PRIMARY KEY(user_id, paper_id),
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, paper_id) REFERENCES papers(user_id, id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_pdf_excerpt_cache_user_updated
            ON paper_pdf_excerpt_cache(user_id, updated_at);

        CREATE TABLE IF NOT EXISTS paper_opening_cache (
            user_id INTEGER NOT NULL,
            paper_id INTEGER NOT NULL,
            opening TEXT,
            updated_at INTEGER,
            hit_count INTEGER NOT NULL DEFAULT 0,
            miss_count INTEGER NOT NULL DEFAULT 0,
            last_hit_at INTEGER,
            last_miss_at INTEGER,
            PRIMARY KEY(user_id, paper_id),
            FOREIGN KEY(user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
            FOREIGN KEY(user_id, paper_id) REFERENCES papers(user_id, id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_paper_opening_cache_user_updated
            ON paper_opening_cache(user_id, updated_at);
        """
    )
