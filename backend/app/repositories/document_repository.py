"""SQLite source-of-truth repository for canonical PDF documents.

The repository deliberately knows nothing about Docling or LanceDB.  It
persists pages, blocks and chunks atomically and exposes only active-version
scoped reads to retrieval code.  Vector rows are a rebuildable projection and
are handled by ``VectorStore`` in the retrieval package.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterable
from typing import Any

from ..domain.document import CanonicalDocument, DocumentChunk, stable_uid
from ..infrastructure.db import Database


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class DocumentRepository:
    """Persist and query the canonical document facts for one SQLite DB."""

    def __init__(self, db_path: str) -> None:
        self.db = Database(db_path)

    def create_or_get_version(
        self,
        *,
        user_id: int,
        paper_id: int,
        file_hash: str,
        file_size: int,
        parser_id: str,
        parser_version: str,
        parser_config_hash: str,
        chunker_version: str,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        embedding_dimension: int | None = None,
    ) -> str:
        """Return a deterministic version id, creating a staging row if needed."""

        # Canonical document identity deliberately excludes the embedding
        # projection.  Embeddings are rebuildable and changing model/dimension
        # must not create a second copy of the parsed PDF.
        version_id = stable_uid(
            "dv",
            int(user_id),
            int(paper_id),
            file_hash,
            parser_id,
            parser_version,
            parser_config_hash,
            chunker_version,
        )
        now = int(time.time())
        conn = self.db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT id FROM document_versions
                WHERE user_id = ? AND paper_id = ? AND file_hash = ?
                  AND parser_config_hash = ? AND chunker_version = ?
                  AND parser_id = ? AND parser_version = ?
                LIMIT 1
                """,
                (
                    int(user_id),
                    int(paper_id),
                    str(file_hash),
                    str(parser_config_hash),
                    str(chunker_version),
                    str(parser_id),
                    str(parser_version),
                ),
            ).fetchone()
            if existing:
                conn.commit()
                return str(existing[0])
            # The paper/user pair is checked here as well as by the FK so a
            # caller cannot create a version for another user's paper.
            owned = conn.execute(
                "SELECT 1 FROM papers WHERE id = ? AND user_id = ?",
                (int(paper_id), int(user_id)),
            ).fetchone()
            if not owned:
                raise ValueError("paper does not belong to user")
            conn.execute(
                """
                INSERT INTO document_versions(
                    id, user_id, paper_id, file_hash, file_size,
                    parser_id, parser_version, parser_config_hash,
                    chunker_version, embedding_provider, embedding_model,
                    embedding_dimension, status, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version_id,
                    int(user_id),
                    int(paper_id),
                    str(file_hash),
                    int(file_size),
                    str(parser_id),
                    str(parser_version),
                    str(parser_config_hash),
                    str(chunker_version),
                    embedding_provider,
                    embedding_model,
                    embedding_dimension,
                    "staging",
                    now,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return version_id

    def persist_document(
        self,
        document: CanonicalDocument,
        chunks: Iterable[DocumentChunk],
        *,
        quality_score: float | None = None,
        canonical_artifact_path: str | None = None,
    ) -> dict[str, int | str]:
        """Replace a staging version's derived rows and mark it ready.

        Re-running this method is safe: all child rows are version-scoped and
        are replaced in one transaction.  The version is never activated by
        this method; activation is a separate atomic operation.
        """

        chunk_rows = list(chunks)
        now = int(time.time())
        score = float(
            document.quality.score if quality_score is None else quality_score
        )
        with self.db.transaction() as conn:
            version = conn.execute(
                "SELECT user_id,paper_id,status FROM document_versions WHERE id = ?",
                (document.document_version_id,),
            ).fetchone()
            if version is None:
                raise ValueError("document version does not exist")
            if int(version[0]) != int(document.user_id) or int(version[1]) != int(document.paper_id):
                raise ValueError("document version scope mismatch")
            if str(version[2]) in {"active", "superseded"}:
                raise ValueError("cannot replace an activated document version")

            conn.execute(
                "DELETE FROM document_chunks WHERE document_version_id = ?",
                (document.document_version_id,),
            )
            conn.execute(
                "DELETE FROM document_blocks WHERE document_version_id = ?",
                (document.document_version_id,),
            )
            conn.execute(
                "DELETE FROM document_pages WHERE document_version_id = ?",
                (document.document_version_id,),
            )

            conn.executemany(
                """
                INSERT INTO document_pages(
                    document_version_id,user_id,paper_id,page_index,
                    printed_page_label,width,height,text,markdown,
                    image_count,table_count,formula_count,ocr_used,quality_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        document.document_version_id,
                        int(document.user_id),
                        int(document.paper_id),
                        int(page.page_index),
                        page.printed_page_label,
                        page.width,
                        page.height,
                        page.text or "",
                        page.markdown or "",
                        int(page.image_count),
                        int(page.table_count),
                        int(page.formula_count),
                        int(bool(page.ocr_used)),
                        _json(page.quality),
                    )
                    for page in document.pages
                ],
            )
            conn.executemany(
                """
                INSERT INTO document_blocks(
                    block_uid,document_version_id,user_id,paper_id,page_index,
                    block_order,block_type,section_path_json,text,markdown,
                    table_html,formula_latex,bbox_json,provenance_json,text_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        block.block_uid,
                        document.document_version_id,
                        int(document.user_id),
                        int(document.paper_id),
                        int(block.page_index),
                        int(block.block_order),
                        str(block.block_type),
                        _json(block.section_path),
                        block.text or "",
                        block.markdown,
                        block.table_html,
                        block.formula_latex,
                        _json(block.bbox) if block.bbox is not None else None,
                        _json(block.provenance),
                        block.text_hash,
                    )
                    for block in document.blocks
                ],
            )
            conn.executemany(
                """
                INSERT INTO document_chunks(
                    chunk_uid,document_version_id,user_id,paper_id,parent_chunk_uid,
                    level,ordinal,content_type,section_path_json,page_start,page_end,
                    block_uids_json,display_text,embedding_text,sparse_text,text_hash,
                    token_count,chunker_version,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        chunk.chunk_uid,
                        document.document_version_id,
                        int(document.user_id),
                        int(document.paper_id),
                        chunk.parent_chunk_uid,
                        chunk.level,
                        int(chunk.ordinal),
                        chunk.content_type,
                        _json(chunk.section_path),
                        int(chunk.page_start),
                        int(chunk.page_end),
                        _json(chunk.block_uids),
                        chunk.display_text,
                        chunk.embedding_text,
                        chunk.sparse_text,
                        chunk.text_hash,
                        int(chunk.token_count),
                        chunk.chunker_version,
                        now,
                    )
                    for chunk in chunk_rows
                ],
            )
            conn.execute(
                """
                UPDATE document_versions
                SET status = 'ready', quality_score = ?, quality_json = ?,
                    page_count = ?, block_count = ?, chunk_count = ?,
                    canonical_artifact_path = ?, error_code = NULL,
                    error_message = NULL
                WHERE id = ?
                """,
                (
                    score,
                    _json(document.quality.to_dict()),
                    len(document.pages),
                    len(document.blocks),
                    len(chunk_rows),
                    canonical_artifact_path,
                    document.document_version_id,
                ),
            )
        return {
            "document_version_id": document.document_version_id,
            "page_count": len(document.pages),
            "block_count": len(document.blocks),
            "chunk_count": len(chunk_rows),
        }

    def activate_version(
        self,
        *,
        user_id: int,
        paper_id: int,
        document_version_id: str,
    ) -> bool:
        """Atomically make a ready version active for the owned paper."""

        now = int(time.time())
        with self.db.transaction() as conn:
            target = conn.execute(
                """
                SELECT status FROM document_versions
                WHERE id = ? AND user_id = ? AND paper_id = ?
                """,
                (document_version_id, int(user_id), int(paper_id)),
            ).fetchone()
            if target is None or str(target[0]) not in {"ready", "degraded", "active", "superseded"}:
                return False
            conn.execute(
                """
                UPDATE document_versions
                SET status = 'superseded', superseded_at = ?
                WHERE user_id = ? AND paper_id = ? AND status = 'active'
                  AND id <> ?
                """,
                (now, int(user_id), int(paper_id), document_version_id),
            )
            conn.execute(
                """
                UPDATE document_versions
                SET status = 'active', activated_at = ?, superseded_at = NULL
                WHERE id = ? AND user_id = ? AND paper_id = ?
                """,
                (now, document_version_id, int(user_id), int(paper_id)),
            )
        return True

    def get_active_version(self, *, user_id: int, paper_id: int) -> dict[str, Any] | None:
        row = self.db.query_one(
            """
            SELECT * FROM document_versions
            WHERE user_id = ? AND paper_id = ? AND status = 'active'
            LIMIT 1
            """,
            (int(user_id), int(paper_id)),
        )
        return _row_dict(row)

    def get_version(self, *, user_id: int, document_version_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM document_versions WHERE id = ? AND user_id = ?",
            (document_version_id, int(user_id)),
        )
        return _row_dict(row)

    def set_version_status(
        self,
        *,
        user_id: int,
        document_version_id: str,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        allowed = {
            "staging",
            "ready",
            "active",
            "partial_success",
            "degraded",
            "failed",
            "superseded",
        }
        if status not in allowed:
            raise ValueError(f"invalid document version status: {status}")
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE document_versions
                SET status = ?, error_code = ?, error_message = ?
                WHERE id = ? AND user_id = ?
                """,
                (status, error_code, error_message, document_version_id, int(user_id)),
            )
            return int(cursor.rowcount or 0) == 1

    def set_embedding_status(
        self,
        *,
        user_id: int,
        document_version_id: str,
        status: str,
        indexed_count: int = 0,
        error: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        config_hash: str | None = None,
    ) -> bool:
        allowed = {"not_indexed", "running", "ready", "degraded", "failed"}
        if status not in allowed:
            raise ValueError(f"invalid embedding status: {status}")
        with self.db.transaction() as conn:
            assignments = [
                "embedding_status = ?",
                "embedding_indexed_count = ?",
                "embedding_error = ?",
                "embedding_updated_at = ?",
            ]
            values: list[Any] = [
                status,
                max(0, int(indexed_count)),
                error,
                int(time.time()),
            ]
            if provider is not None:
                assignments.append("embedding_provider = ?")
                values.append(str(provider))
            if model is not None:
                assignments.append("embedding_model = ?")
                values.append(str(model))
            if dimension is not None:
                assignments.append("embedding_dimension = ?")
                values.append(int(dimension))
            if config_hash is not None:
                assignments.append("embedding_config_hash = ?")
                values.append(str(config_hash))
            values.extend([str(document_version_id), int(user_id)])
            cursor = conn.execute(
                f"""
                UPDATE document_versions
                SET {", ".join(assignments)}
                WHERE id = ? AND user_id = ?
                """,
                tuple(values),
            )
            return int(cursor.rowcount or 0) == 1

    def list_chunks(
        self,
        *,
        user_id: int,
        paper_id: int,
        document_version_id: str | None = None,
        level: str = "child",
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [int(user_id), int(paper_id), level]
        version_sql = ""
        if document_version_id:
            version_sql = " AND c.document_version_id = ?"
            params.append(document_version_id)
        params.append(max(1, min(int(limit), 10000)))
        rows = self.db.query_all(
            f"""
            SELECT c.* FROM document_chunks c
            JOIN document_versions v ON v.id = c.document_version_id
            WHERE c.user_id = ? AND c.paper_id = ? AND c.level = ?
              AND v.status = 'active'{version_sql}
            ORDER BY c.ordinal ASC LIMIT ?
            """,
            tuple(params),
        )
        return [_row_dict(row) or {} for row in rows]

    def list_version_chunks(
        self,
        *,
        user_id: int,
        paper_id: int,
        document_version_id: str,
        level: str = "child",
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """Read chunks for an owned version, including staging versions.

        Index construction happens before a version is activated.  Keeping
        this method separate from ``list_chunks`` prevents retrieval code
        from accidentally exposing staging/superseded content while allowing
        the projection builder to index an owned, deterministic version.
        """

        rows = self.db.query_all(
            """
            SELECT c.* FROM document_chunks c
            WHERE c.user_id = ? AND c.paper_id = ?
              AND c.document_version_id = ? AND c.level = ?
            ORDER BY c.ordinal ASC LIMIT ?
            """,
            (
                int(user_id),
                int(paper_id),
                str(document_version_id),
                str(level),
                max(1, min(int(limit), 10000)),
            ),
        )
        return [_row_dict(row) or {} for row in rows]

    def get_chunks_by_uid(
        self,
        *,
        user_id: int,
        chunk_uids: list[str],
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Hydrate retrieval hits while enforcing user and active-version scope."""

        if not chunk_uids:
            return []
        placeholders = ",".join("?" for _ in chunk_uids)
        active_sql = " AND v.status = 'active'" if active_only else ""
        rows = self.db.query_all(
            f"""
            SELECT c.* FROM document_chunks c
            JOIN document_versions v ON v.id = c.document_version_id
            WHERE c.user_id = ? AND c.chunk_uid IN ({placeholders}){active_sql}
            """,
            (int(user_id), *[str(uid) for uid in chunk_uids]),
        )
        hydrated = [dict(row) for row in rows]
        by_uid = {str(row["chunk_uid"]): row for row in hydrated}
        return [by_uid[uid] for uid in chunk_uids if uid in by_uid]

    def expand_active_evidence_chunks(
        self,
        *,
        user_id: int,
        anchor_chunk_uids: list[str],
        neighbor_radius: int = 1,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        """Hydrate canonical parent and same-parent child neighbours safely.

        Retrieval starts from child chunks.  A child can answer a narrow fact
        but omit its definition, table header, or immediate qualification.  A
        bounded parent/neighbor expansion supplies that local context while
        retaining the exact user and *active document version* hard filters.
        It is intentionally a repository operation, not Agent-selected SQL.
        """

        anchors = list(dict.fromkeys(
            str(value or "").strip() for value in anchor_chunk_uids if str(value or "").strip()
        ))[:32]
        if not anchors:
            return []
        radius = max(0, min(int(neighbor_radius), 2))
        bounded_limit = max(1, min(int(limit), 200))
        placeholders = ",".join("?" for _ in anchors)
        rows = self.db.query_all(
            f"""
            WITH anchors AS (
                SELECT c.chunk_uid,
                       c.document_version_id,
                       c.paper_id,
                       c.parent_chunk_uid,
                       c.ordinal
                FROM document_chunks c
                JOIN document_versions v ON v.id = c.document_version_id
                WHERE c.user_id = ?
                  AND c.chunk_uid IN ({placeholders})
                  AND c.level = 'child'
                  AND v.status = 'active'
            )
            SELECT DISTINCT c.*
            FROM document_chunks c
            JOIN document_versions v ON v.id = c.document_version_id
            WHERE c.user_id = ?
              AND v.status = 'active'
              AND (
                  c.chunk_uid IN (SELECT chunk_uid FROM anchors)
                  OR c.chunk_uid IN (
                      SELECT parent_chunk_uid
                      FROM anchors
                      WHERE parent_chunk_uid IS NOT NULL
                  )
                  OR (
                      c.level = 'child'
                      AND EXISTS (
                          SELECT 1
                          FROM anchors a
                          WHERE a.document_version_id = c.document_version_id
                            AND a.paper_id = c.paper_id
                            AND a.parent_chunk_uid IS c.parent_chunk_uid
                            AND c.ordinal BETWEEN a.ordinal - ? AND a.ordinal + ?
                      )
                  )
              )
            ORDER BY c.document_version_id ASC, c.ordinal ASC
            LIMIT ?
            """,
            (
                int(user_id),
                *anchors,
                int(user_id),
                radius,
                radius,
                bounded_limit,
            ),
        )
        return [_row_dict(row) or {} for row in rows]

    def has_fts(self) -> bool:
        return self._has_fts_projection("document_chunks_fts")

    def has_trigram_fts(self) -> bool:
        """Whether this SQLite build completed the optional CJK projection."""

        return self._has_fts_projection("document_chunks_trigram_fts")

    def _has_fts_projection(self, table_name: str) -> bool:
        if table_name not in {
            "document_chunks_fts",
            "document_chunks_trigram_fts",
        }:
            raise ValueError("unsupported FTS projection")
        row = self.db.query_one(
            "SELECT 1 FROM sqlite_master WHERE name = ?", (table_name,)
        )
        return row is not None

    def search_fts(
        self,
        *,
        user_id: int,
        paper_ids: list[int],
        match_query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self._search_fts_projection(
            table_name="document_chunks_fts",
            user_id=user_id,
            paper_ids=paper_ids,
            match_query=match_query,
            limit=limit,
        )

    def search_trigram_fts(
        self,
        *,
        user_id: int,
        paper_ids: list[int],
        match_query: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Search the optional original-text trigram FTS projection."""

        return self._search_fts_projection(
            table_name="document_chunks_trigram_fts",
            user_id=user_id,
            paper_ids=paper_ids,
            match_query=match_query,
            limit=limit,
        )

    def _search_fts_projection(
        self,
        *,
        table_name: str,
        user_id: int,
        paper_ids: list[int],
        match_query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self._has_fts_projection(table_name) or not match_query.strip():
            return []
        scoped_paper_ids = list(dict.fromkeys(int(value) for value in paper_ids))[:400]
        if not scoped_paper_ids:
            return []
        placeholders = ",".join("?" for _ in scoped_paper_ids)
        params: list[Any] = [
            str(match_query),
            int(user_id),
            *scoped_paper_ids,
            max(1, min(int(limit), 100)),
        ]
        # ``table_name`` is checked by ``_has_fts_projection`` against this
        # closed set before interpolation; it is never user input.
        rows = self.db.query_all(
            f"""
            SELECT c.*, bm25({table_name}) AS bm25_score
            FROM {table_name}
            JOIN document_chunks c ON c.id = {table_name}.rowid
            JOIN document_versions v ON v.id = c.document_version_id
            WHERE {table_name} MATCH ?
              AND c.user_id = ?
              AND c.paper_id IN ({placeholders})
              AND c.level = 'child'
              AND v.status = 'active'
            ORDER BY bm25_score ASC
            LIMIT ?
            """,
            tuple(params),
        )
        return [_row_dict(row) or {} for row in rows]

    def create_ingest_job(
        self,
        *,
        user_id: int,
        paper_id: int,
        requested_file_hash: str | None,
        parser_mode: str = "standard",
        max_attempts: int = 3,
        requires_cloud_confirmation: bool = False,
    ) -> str:
        now = int(time.time())
        conn = self.db.connect()
        try:
            # Serialize enqueue checks so two concurrent API requests reuse
            # the same live job but a terminal job never blocks a new retry.
            conn.execute("BEGIN IMMEDIATE")
            owned = conn.execute(
                "SELECT 1 FROM papers WHERE id = ? AND user_id = ?",
                (int(paper_id), int(user_id)),
            ).fetchone()
            if not owned:
                raise ValueError("paper does not belong to user")
            existing = conn.execute(
                """
                SELECT id FROM ingest_jobs
                WHERE user_id = ? AND paper_id = ?
                  AND requested_file_hash IS ? AND parser_mode = ?
                  AND status IN ('queued','running','needs_cloud_confirmation')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    int(user_id),
                    int(paper_id),
                    requested_file_hash,
                    str(parser_mode),
                ),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return str(existing[0])
            job_id = f"job_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO ingest_jobs(
                    id,user_id,paper_id,requested_file_hash,status,current_step,
                    progress,parser_mode,attempt_count,max_attempts,
                    requires_cloud_confirmation,next_attempt_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    int(user_id),
                    int(paper_id),
                    requested_file_hash,
                    "queued",
                    "queued",
                    0.0,
                    parser_mode,
                    0,
                    max(1, int(max_attempts)),
                    int(bool(requires_cloud_confirmation)),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return job_id

    def claim_ingest_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 900,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Claim one queued/expired job with a short ``BEGIN IMMEDIATE`` txn."""

        now = int(time.time())
        lease_until = now + max(30, int(lease_seconds))
        conn = self.db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # A worker may die after claiming a job.  Once its lease expires,
            # expose the row for retry; when it already exhausted attempts,
            # make the terminal state visible instead of leaving it stuck in
            # ``running`` forever.
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status='failed', current_step='retry_exhausted',
                    error_code=COALESCE(error_code, 'RETRY_EXHAUSTED'),
                    error_message=COALESCE(error_message, 'maximum ingest attempts exhausted'),
                    lease_owner=NULL, lease_expires_at=NULL, finished_at=?, updated_at=?
                WHERE status='running'
                  AND COALESCE(lease_expires_at, 0) < ?
                  AND attempt_count >= max_attempts
                """,
                (now, now, now),
            )
            filters = [
                "((status = 'queued' AND (next_attempt_at IS NULL OR next_attempt_at <= ?)) "
                "OR (status = 'running' AND COALESCE(lease_expires_at, 0) < ?))",
                "attempt_count < max_attempts",
            ]
            params: list[Any] = [now, now]
            if job_id:
                filters.append("id = ?")
                params.append(str(job_id))
            row = conn.execute(
                f"""
                SELECT * FROM ingest_jobs
                WHERE {' AND '.join(filters)}
                ORDER BY created_at ASC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            job_id = str(row["id"])
            conn.execute(
                """
                UPDATE ingest_jobs
                SET status='running', current_step='claimed',
                    attempt_count=attempt_count+1, lease_owner=?,
                    lease_expires_at=?, last_heartbeat_at=?, next_attempt_at=NULL,
                    started_at=COALESCE(started_at,?), error_code=NULL,
                    error_message=NULL, updated_at=?
                WHERE id=?
                """,
                (worker_id, lease_until, now, now, now, job_id),
            )
            conn.commit()
            claimed = conn.execute("SELECT * FROM ingest_jobs WHERE id = ?", (job_id,)).fetchone()
            return _row_dict(claimed)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def update_ingest_job(
        self,
        *,
        job_id: str,
        worker_id: str | None = None,
        status: str | None = None,
        current_step: str | None = None,
        progress: float | None = None,
        result_document_version_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        clear_lease: bool = False,
    ) -> bool:
        allowed = {
            "queued",
            "running",
            "needs_cloud_confirmation",
            "succeeded",
            "degraded",
            "failed",
            "cancelled",
        }
        if status is not None and status not in allowed:
            raise ValueError(f"invalid ingest job status: {status}")
        values: list[Any] = []
        assignments: list[str] = []
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if current_step is not None:
            assignments.append("current_step = ?")
            values.append(current_step)
        if progress is not None:
            assignments.append("progress = ?")
            values.append(max(0.0, min(1.0, float(progress))))
        if result_document_version_id is not None:
            assignments.append("result_document_version_id = ?")
            values.append(result_document_version_id)
        if error_code is not None:
            assignments.append("error_code = ?")
            values.append(error_code)
        if error_message is not None:
            assignments.append("error_message = ?")
            values.append(error_message)
        if clear_lease:
            assignments.extend(["lease_owner = NULL", "lease_expires_at = NULL"])
        if status in {"succeeded", "degraded", "failed", "cancelled"}:
            assignments.append("finished_at = ?")
            values.append(int(time.time()))
        assignments.append("updated_at = ?")
        values.append(int(time.time()))
        if not assignments:
            return False
        where = "id = ?"
        values.append(job_id)
        if worker_id:
            where += " AND lease_owner = ?"
            values.append(worker_id)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE ingest_jobs SET {', '.join(assignments)} WHERE {where}",
                tuple(values),
            )
            return int(cursor.rowcount or 0) == 1

    def renew_ingest_job_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        lease_seconds: int = 900,
    ) -> bool:
        """Renew a running job only when this worker still owns its lease."""

        now = int(time.time())
        lease_until = now + max(30, int(lease_seconds))
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE ingest_jobs
                SET lease_expires_at=?, last_heartbeat_at=?, updated_at=?
                WHERE id=? AND status='running' AND lease_owner=?
                """,
                (lease_until, now, now, str(job_id), str(worker_id)),
            )
            return int(cursor.rowcount or 0) == 1

    def requeue_ingest_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        retry_at: int,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Release one failed attempt for a delayed retry by another worker."""

        now = int(time.time())
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE ingest_jobs
                SET status='queued', current_step='retry_waiting', progress=0,
                    next_attempt_at=?, error_code=?, error_message=?,
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE id=? AND status='running' AND lease_owner=?
                """,
                (
                    max(now, int(retry_at)),
                    str(error_code),
                    str(error_message)[:4000],
                    now,
                    str(job_id),
                    str(worker_id),
                ),
            )
            return int(cursor.rowcount or 0) == 1

    def get_paper_ingest_status(
        self,
        *,
        user_id: int,
        paper_id: int,
    ) -> dict[str, Any] | None:
        """Return a user-scoped summary used by the library and reader UI."""

        owned = self.db.query_one(
            "SELECT 1 FROM papers WHERE id=? AND user_id=?",
            (int(paper_id), int(user_id)),
        )
        if owned is None:
            return None
        active = self.get_active_version(user_id=int(user_id), paper_id=int(paper_id))
        latest = self.db.query_one(
            """
            SELECT * FROM ingest_jobs
            WHERE user_id=? AND paper_id=?
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            (int(user_id), int(paper_id)),
        )
        return {
            "paper_id": int(paper_id),
            "active_document_version": active,
            "latest_job": _row_dict(latest),
            "rag_ready": active is not None,
        }

    def get_ingest_job(self, *, user_id: int, job_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM ingest_jobs WHERE id = ? AND user_id = ?",
            (job_id, int(user_id)),
        )
        return _row_dict(row)
