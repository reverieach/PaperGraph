from __future__ import annotations

import sqlite3

from .connection import Database


class SchemaValidationError(RuntimeError):
    pass


REQUIRED_COLUMNS: dict[str, set[str]] = {
    "schema_migrations": {"version", "name", "checksum", "applied_at"},
    "auth_users": {
        "id",
        "username",
        "password_hash",
        "status",
        "created_at",
        "updated_at",
    },
    "papers": {"id", "user_id", "title", "created_at", "updated_at"},
    "paper_reading_sessions": {
        "id",
        "user_id",
        "paper_id",
        "duration_sec",
        "day_key",
        "created_at",
    },
    "daily_recommend_feedback": {
        "id",
        "user_id",
        "date_key",
        "paper_identity_key",
        "identity_type",
        "action",
        "created_at",
    },
    "negative_pref_memory": {
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
    "daily_papers_cache": {
        "user_id",
        "date_key",
        "cache_key",
        "payload_json",
        "created_at",
        "updated_at",
        "hit_count",
    },
    "daily_recommendations": {
        "id",
        "user_id",
        "date_key",
        "source",
        "arxiv_id",
        "title",
        "created_at",
    },
    "paper_relations": {
        "user_id",
        "source_paper_id",
        "target_paper_id",
        "relation",
        "score",
        "evidence",
        "created_at",
        "updated_at",
    },
    "paper_pdf_excerpt_cache": {
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
    "paper_opening_cache": {
        "user_id",
        "paper_id",
        "opening",
        "updated_at",
        "hit_count",
        "miss_count",
        "last_hit_at",
        "last_miss_at",
    },
    "reader_conversations": {
        "id",
        "user_id",
        "paper_id",
        "created_at",
        "updated_at",
    },
    "paper_reader_turns": {
        "id",
        "user_id",
        "paper_id",
        "conversation_id",
        "role",
        "content",
        "metadata_json",
        "created_at",
    },
    "memory_drafts": {
        "id",
        "user_id",
        "paper_id",
        "conversation_id",
        "status",
        "payload_json",
        "source_snapshot_hash",
    },
    "memories": {
        "id",
        "user_id",
        "scope_type",
        "scope_id",
        "kind",
        "content",
        "content_hash",
        "confirmed_by_user",
        "status",
        "metadata_json",
        "importance",
        "expires_at",
        "superseded_by",
    },
    "research_sessions": {
        "id",
        "user_id",
        "title",
        "created_at",
        "updated_at",
    },
    "research_session_papers": {
        "session_id",
        "paper_id",
        "position",
        "created_at",
    },
    "research_turns": {
        "id",
        "session_id",
        "user_id",
        "role",
        "content",
        "metadata_json",
        "created_at",
    },
    "document_versions": {
        "id",
        "user_id",
        "paper_id",
        "file_hash",
        "parser_id",
        "parser_version",
        "parser_config_hash",
        "chunker_version",
        "status",
        "quality_json",
        "page_count",
        "block_count",
        "chunk_count",
        "embedding_config_hash",
        "embedding_status",
        "embedding_indexed_count",
        "embedding_updated_at",
        "created_at",
    },
    "document_pages": {
        "id",
        "document_version_id",
        "user_id",
        "paper_id",
        "page_index",
        "text",
        "quality_json",
    },
    "document_blocks": {
        "id",
        "block_uid",
        "document_version_id",
        "user_id",
        "paper_id",
        "page_index",
        "block_order",
        "block_type",
        "section_path_json",
        "text",
        "provenance_json",
        "text_hash",
    },
    "document_chunks": {
        "id",
        "chunk_uid",
        "document_version_id",
        "user_id",
        "paper_id",
        "level",
        "ordinal",
        "content_type",
        "section_path_json",
        "page_start",
        "page_end",
        "display_text",
        "embedding_text",
        "sparse_text",
        "text_hash",
        "token_count",
        "chunker_version",
        "created_at",
    },
    "ingest_jobs": {
        "id",
        "user_id",
        "paper_id",
        "status",
        "current_step",
        "progress",
        "parser_mode",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "last_heartbeat_at",
        "created_at",
        "updated_at",
    },
}

REQUIRED_INDEXES = {
    "idx_papers_user_created",
    "ux_papers_user_id_id",
    "idx_reader_turns_scope",
    "idx_memories_scope",
    "idx_memories_active_content",
    "idx_memories_retrieval_scope",
    "idx_research_sessions_user_updated",
    "idx_research_turns_session",
    "idx_document_versions_active",
    "idx_document_versions_scope",
    "idx_document_pages_scope",
    "idx_document_blocks_version_order",
    "idx_document_chunks_scope",
    "idx_document_chunks_pages",
    "idx_document_chunks_parent",
    "idx_ingest_jobs_claim",
    "idx_ingest_jobs_ready",
    "idx_document_versions_embedding_status",
    "idx_negpref_memory_user_exp",
    "idx_daily_papers_cache_user_date",
    "idx_daily_reco_user_date",
    "idx_daily_reco_user_arxiv",
    "idx_paper_relations_user_source",
    "idx_paper_relations_user_target",
    "idx_pdf_excerpt_cache_user_updated",
    "idx_paper_opening_cache_user_updated",
}


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    return {
        str(row["name"]): row
        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _has_fk(
    conn: sqlite3.Connection,
    table: str,
    from_column: str,
    target_table: str,
) -> bool:
    return any(
        str(row["from"]) == from_column and str(row["table"]) == target_table
        for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    )


def validate_schema(db_path: str, *, read_only: bool = False) -> None:
    errors: list[str] = []
    with Database(db_path, read_only=read_only).read() as conn:
        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table, required in REQUIRED_COLUMNS.items():
            if table not in tables:
                errors.append(f"missing table: {table}")
                continue
            actual = _columns(conn, table)
            missing = sorted(required - set(actual))
            if missing:
                errors.append(f"{table} missing columns: {', '.join(missing)}")

        if "papers" in tables:
            paper_cols = _columns(conn, "papers")
            if "user_id" in paper_cols and not int(paper_cols["user_id"]["notnull"]):
                errors.append("papers.user_id must be NOT NULL")
            if not _has_fk(conn, "papers", "user_id", "auth_users"):
                errors.append("papers.user_id foreign key is missing")

        for table in (
            "paper_reading_sessions",
            "daily_recommend_feedback",
            "negative_pref_memory",
            "daily_papers_cache",
            "daily_recommendations",
            "paper_relations",
            "paper_pdf_excerpt_cache",
            "paper_opening_cache",
            "reader_conversations",
            "paper_reader_turns",
            "memory_drafts",
            "memories",
            "research_sessions",
            "research_turns",
            "document_versions",
            "document_pages",
            "document_blocks",
            "document_chunks",
            "ingest_jobs",
        ):
            if table in tables and not _has_fk(conn, table, "user_id", "auth_users"):
                errors.append(f"{table}.user_id foreign key is missing")

        indexes = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        for index in sorted(REQUIRED_INDEXES - indexes):
            errors.append(f"missing index: {index}")

        unfinished = [
            name
            for name in tables
            if name.endswith("_new") or name.endswith("_migrating")
        ]
        if unfinished:
            errors.append(f"unfinished migration tables: {', '.join(sorted(unfinished))}")

        for table in (
            "papers",
            "paper_authors",
            "negative_pref_memory",
            "daily_papers_cache",
            "daily_recommendations",
            "paper_relations",
            "paper_pdf_excerpt_cache",
            "paper_opening_cache",
            "reader_conversations",
            "paper_reader_turns",
            "memory_drafts",
            "memories",
            "research_sessions",
            "research_session_papers",
            "research_turns",
            "document_versions",
            "document_pages",
            "document_blocks",
            "document_chunks",
            "ingest_jobs",
        ):
            if table not in tables:
                continue
            violations = conn.execute(
                f'PRAGMA foreign_key_check("{table}")'
            ).fetchall()
            if violations:
                errors.append(f"{table} has {len(violations)} foreign key violation(s)")

        if "document_chunks_fts" not in tables:
            # FTS5 is optional in some system SQLite builds.  Do not make
            # ordinary paper CRUD unusable, but expose the degradation to
            # callers through the retrieval capability check.
            pass

    if errors:
        raise SchemaValidationError("; ".join(errors))
