from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """Create the canonical document and persisted ingest-job tables.

    SQLite remains the source of truth.  FTS5 is a rebuildable projection of
    ``document_chunks`` and is intentionally kept in the same migration so a
    fresh database and a legacy database have identical search semantics.
    """

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_versions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            file_hash TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            parser_id TEXT NOT NULL,
            parser_version TEXT NOT NULL,
            parser_config_hash TEXT NOT NULL,
            chunker_version TEXT NOT NULL,
            embedding_provider TEXT,
            embedding_model TEXT,
            embedding_dimension INTEGER,
            status TEXT NOT NULL CHECK(status IN (
                'staging','ready','active','partial_success','degraded',
                'failed','superseded'
            )),
            quality_score REAL NOT NULL DEFAULT 0,
            quality_json TEXT NOT NULL DEFAULT '{}',
            page_count INTEGER NOT NULL DEFAULT 0,
            block_count INTEGER NOT NULL DEFAULT 0,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            canonical_artifact_path TEXT,
            error_code TEXT,
            error_message TEXT,
            created_at INTEGER NOT NULL,
            activated_at INTEGER,
            superseded_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_document_versions_active
            ON document_versions(user_id, paper_id)
            WHERE status = 'active'
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_versions_scope
            ON document_versions(user_id, paper_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_document_versions_idempotency
            ON document_versions(
                user_id, paper_id, file_hash, parser_config_hash,
                chunker_version, parser_id, parser_version
            )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_version_id TEXT NOT NULL
                REFERENCES document_versions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            page_index INTEGER NOT NULL,
            printed_page_label TEXT,
            width REAL,
            height REAL,
            text TEXT NOT NULL DEFAULT '',
            markdown TEXT NOT NULL DEFAULT '',
            image_count INTEGER NOT NULL DEFAULT 0,
            table_count INTEGER NOT NULL DEFAULT 0,
            formula_count INTEGER NOT NULL DEFAULT 0,
            ocr_used INTEGER NOT NULL DEFAULT 0,
            quality_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(document_version_id, page_index)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_pages_scope
            ON document_pages(user_id, paper_id, document_version_id, page_index)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_uid TEXT NOT NULL UNIQUE,
            document_version_id TEXT NOT NULL
                REFERENCES document_versions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            page_index INTEGER NOT NULL,
            block_order INTEGER NOT NULL,
            block_type TEXT NOT NULL,
            section_path_json TEXT NOT NULL DEFAULT '[]',
            text TEXT NOT NULL DEFAULT '',
            markdown TEXT,
            table_html TEXT,
            formula_latex TEXT,
            bbox_json TEXT,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            text_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_blocks_version_order
            ON document_blocks(document_version_id, page_index, block_order)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_blocks_scope
            ON document_blocks(user_id, paper_id, document_version_id)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_uid TEXT NOT NULL UNIQUE,
            document_version_id TEXT NOT NULL
                REFERENCES document_versions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            parent_chunk_uid TEXT,
            level TEXT NOT NULL CHECK(level IN ('parent','child')),
            ordinal INTEGER NOT NULL,
            content_type TEXT NOT NULL,
            section_path_json TEXT NOT NULL DEFAULT '[]',
            page_start INTEGER NOT NULL,
            page_end INTEGER NOT NULL,
            block_uids_json TEXT NOT NULL DEFAULT '[]',
            display_text TEXT NOT NULL,
            embedding_text TEXT NOT NULL,
            sparse_text TEXT NOT NULL,
            text_hash TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            chunker_version TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(document_version_id, level, ordinal)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_chunks_scope
            ON document_chunks(user_id, paper_id, document_version_id, level, ordinal)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_chunks_pages
            ON document_chunks(document_version_id, page_start, page_end)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_chunks_parent
            ON document_chunks(parent_chunk_uid)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_document_chunks_hash
            ON document_chunks(text_hash)
        """
    )

    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                section_path_json,
                sparse_text,
                content='document_chunks',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS document_chunks_ai
            AFTER INSERT ON document_chunks BEGIN
                INSERT INTO document_chunks_fts(rowid, section_path_json, sparse_text)
                VALUES (new.id, new.section_path_json, new.sparse_text);
            END;

            CREATE TRIGGER IF NOT EXISTS document_chunks_ad
            AFTER DELETE ON document_chunks BEGIN
                INSERT INTO document_chunks_fts(document_chunks_fts, rowid, section_path_json, sparse_text)
                VALUES ('delete', old.id, old.section_path_json, old.sparse_text);
            END;

            CREATE TRIGGER IF NOT EXISTS document_chunks_au
            AFTER UPDATE ON document_chunks BEGIN
                INSERT INTO document_chunks_fts(document_chunks_fts, rowid, section_path_json, sparse_text)
                VALUES ('delete', old.id, old.section_path_json, old.sparse_text);
                INSERT INTO document_chunks_fts(rowid, section_path_json, sparse_text)
                VALUES (new.id, new.section_path_json, new.sparse_text);
            END;
            """
        )
        conn.execute("INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        # Keep the source tables usable on SQLite builds without FTS5.  The
        # retrieval service will report a sparse-search degradation flag.
        conn.execute("DROP TRIGGER IF EXISTS document_chunks_ai")
        conn.execute("DROP TRIGGER IF EXISTS document_chunks_ad")
        conn.execute("DROP TRIGGER IF EXISTS document_chunks_au")
        conn.execute("DROP TABLE IF EXISTS document_chunks_fts")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingest_jobs (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
            paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            requested_file_hash TEXT,
            status TEXT NOT NULL CHECK(status IN (
                'queued','running','needs_cloud_confirmation','succeeded',
                'degraded','failed','cancelled'
            )),
            current_step TEXT,
            progress REAL NOT NULL DEFAULT 0,
            parser_mode TEXT NOT NULL DEFAULT 'standard',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3,
            lease_owner TEXT,
            lease_expires_at INTEGER,
            requires_cloud_confirmation INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            result_document_version_id TEXT
                REFERENCES document_versions(id) ON DELETE SET NULL,
            created_at INTEGER NOT NULL,
            started_at INTEGER,
            updated_at INTEGER NOT NULL,
            finished_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ingest_jobs_claim
            ON ingest_jobs(status, lease_expires_at, created_at)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ingest_jobs_scope
            ON ingest_jobs(user_id, paper_id, created_at DESC)
        """
    )
