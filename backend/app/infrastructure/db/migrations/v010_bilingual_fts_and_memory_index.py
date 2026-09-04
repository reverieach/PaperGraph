from __future__ import annotations

import sqlite3


def migrate(conn: sqlite3.Connection) -> None:
    """Add the rebuildable trigram projection used for CJK sparse recall.

    ``document_chunks`` remains the only canonical source.  The existing
    unicode61 FTS table indexes a deliberately character-spaced sparse form
    and is useful for English, acronyms and short exact CJK terms.  It is not
    a useful index for a natural Chinese sentence.  This second external-content
    FTS projection keeps the original embedding text intact and lets the FTS5
    trigram tokenizer perform substring matching for CJK phrases.

    Some deployment SQLite builds have FTS5 but not the trigram tokenizer.  In
    that case the migration must not make the business schema unavailable: the
    retriever will expose a structured sparse degradation and continue with
    unicode61/dense recall.
    """

    # Canonical PDF parsing stays independent of embeddings, but a document
    # instruction changes every vector in the rebuildable projection.  Keep a
    # stable hash so retrieval can reject an old projection after that setting
    # changes instead of mixing incompatible vector spaces.
    version_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(document_versions)")
    }
    if "embedding_config_hash" not in version_columns:
        conn.execute("ALTER TABLE document_versions ADD COLUMN embedding_config_hash TEXT")

    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_trigram_fts USING fts5(
                section_path_json,
                embedding_text,
                content='document_chunks',
                content_rowid='id',
                tokenize='trigram case_sensitive 0 remove_diacritics 1'
            )
            """
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS document_chunks_trigram_ai
            AFTER INSERT ON document_chunks BEGIN
                INSERT INTO document_chunks_trigram_fts(
                    rowid, section_path_json, embedding_text
                ) VALUES (new.id, new.section_path_json, new.embedding_text);
            END;

            CREATE TRIGGER IF NOT EXISTS document_chunks_trigram_ad
            AFTER DELETE ON document_chunks BEGIN
                INSERT INTO document_chunks_trigram_fts(
                    document_chunks_trigram_fts, rowid, section_path_json, embedding_text
                ) VALUES ('delete', old.id, old.section_path_json, old.embedding_text);
            END;

            CREATE TRIGGER IF NOT EXISTS document_chunks_trigram_au
            AFTER UPDATE ON document_chunks BEGIN
                INSERT INTO document_chunks_trigram_fts(
                    document_chunks_trigram_fts, rowid, section_path_json, embedding_text
                ) VALUES ('delete', old.id, old.section_path_json, old.embedding_text);
                INSERT INTO document_chunks_trigram_fts(
                    rowid, section_path_json, embedding_text
                ) VALUES (new.id, new.section_path_json, new.embedding_text);
            END;
            """
        )
        conn.execute(
            "INSERT INTO document_chunks_trigram_fts(document_chunks_trigram_fts) "
            "VALUES('rebuild')"
        )
    except sqlite3.OperationalError:
        # Do not drop the unicode61 projection when only the optional trigram
        # tokenizer is unavailable.  The trigger/table names are unique to
        # this migration, so cleanup is isolated and idempotent.
        conn.execute("DROP TRIGGER IF EXISTS document_chunks_trigram_ai")
        conn.execute("DROP TRIGGER IF EXISTS document_chunks_trigram_ad")
        conn.execute("DROP TRIGGER IF EXISTS document_chunks_trigram_au")
        conn.execute("DROP TABLE IF EXISTS document_chunks_trigram_fts")
