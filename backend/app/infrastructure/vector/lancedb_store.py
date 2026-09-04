"""LanceDB adapter for rebuildable paper-chunk vector rows."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VectorStoreUnavailable(RuntimeError):
    pass


@dataclass(slots=True)
class VectorRecord:
    chunk_uid: str
    user_id: int
    paper_id: int
    document_version_id: str
    content_type: str
    vector: list[float]
    index_version: str = "v1"


@dataclass(slots=True)
class VectorHit:
    chunk_uid: str
    user_id: int
    paper_id: int
    document_version_id: str
    content_type: str
    score: float
    distance: float


class LanceDBVectorStore:
    table_name = "paper_chunk_vectors"

    def __init__(self, path: str, *, dimension: int = 1024) -> None:
        self.path = str(Path(path))
        self.dimension = int(dimension)
        if self.dimension <= 0:
            raise ValueError("vector dimension must be positive")
        self._db: Any | None = None
        self._table: Any | None = None

    def _get_table(self, *, create: bool = False) -> Any:
        if self._table is not None:
            return self._table
        try:
            import lancedb

            Path(self.path).mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(self.path)
            # LanceDB 0.34 renamed ``table_names`` to ``list_tables``.  Keep a
            # compatibility fallback for older installations without emitting
            # a deprecation warning on current versions.
            list_tables = getattr(self._db, "list_tables", None)
            if callable(list_tables):
                listed = list_tables()
                names = set(getattr(listed, "tables", listed))
            else:
                names = set(self._db.table_names())
            if self.table_name in names:
                self._table = self._db.open_table(self.table_name)
            elif create:
                self._table = self._db.create_table(
                    self.table_name,
                    data=[
                        {
                            "chunk_uid": "__schema__",
                            "user_id": 0,
                            "paper_id": 0,
                            "document_version_id": "__schema__",
                            "index_version": "v1",
                            "content_type": "schema",
                            "vector": [0.0] * self.dimension,
                        }
                    ],
                )
                self._table.delete("chunk_uid = '__schema__'")
            else:
                return None
            return self._table
        except Exception as exc:
            raise VectorStoreUnavailable(f"LanceDB initialization failed: {exc}") from exc

    def upsert(self, records: list[VectorRecord]) -> int:
        if not records:
            return 0
        table = self._get_table(create=True)
        rows: list[dict[str, Any]] = []
        for record in records:
            vector = [float(value) for value in record.vector]
            if len(vector) != self.dimension:
                raise ValueError(f"vector dimension mismatch for {record.chunk_uid}")
            if not all(math.isfinite(value) for value in vector) or not any(abs(value) > 0 for value in vector):
                raise ValueError(f"invalid vector for {record.chunk_uid}")
            rows.append(
                {
                    "chunk_uid": record.chunk_uid,
                    "user_id": int(record.user_id),
                    "paper_id": int(record.paper_id),
                    "document_version_id": record.document_version_id,
                    "index_version": record.index_version,
                    "content_type": record.content_type,
                    "vector": vector,
                }
            )
        # Delete/re-add is intentionally simple and idempotent for the current
        # scale.  SQLite remains the authoritative list for rebuild/GC.
        for record in records:
            escaped = record.chunk_uid.replace("'", "''")
            try:
                table.delete(f"chunk_uid = '{escaped}'")
            except Exception as exc:
                raise VectorStoreUnavailable(
                    f"vector upsert cleanup failed for {record.chunk_uid}: {exc}"
                ) from exc
        table.add(rows)
        return len(rows)

    def search(
        self,
        query_vector: list[float],
        *,
        user_id: int,
        paper_ids: list[int],
        document_version_ids: list[str] | None = None,
        limit: int = 20,
    ) -> list[VectorHit]:
        if not paper_ids:
            return []
        table = self._get_table(create=False)
        if table is None:
            return []
        vector = [float(value) for value in query_vector]
        if len(vector) != self.dimension:
            raise ValueError("query vector dimension mismatch")
        paper_sql = ",".join(str(int(value)) for value in paper_ids)
        where = f"user_id = {int(user_id)} AND paper_id IN ({paper_sql})"
        if document_version_ids:
            quoted = ",".join("'" + str(value).replace("'", "''") + "'" for value in document_version_ids)
            where += f" AND document_version_id IN ({quoted})"
        try:
            query = table.search(vector).metric("cosine").where(where, prefilter=True).limit(max(1, min(int(limit), 100)))
            rows = query.to_list()
        except TypeError:
            # Older LanceDB versions do not expose ``prefilter``; the user and
            # paper filter is still pushed into the query before materializing.
            rows = table.search(vector).metric("cosine").where(where).limit(max(1, min(int(limit), 100))).to_list()
        except Exception as exc:
            raise VectorStoreUnavailable(f"vector search failed: {exc}") from exc
        hits: list[VectorHit] = []
        for row in rows:
            distance = float(row.get("_distance", 0.0))
            hits.append(
                VectorHit(
                    chunk_uid=str(row.get("chunk_uid") or ""),
                    user_id=int(row.get("user_id") or 0),
                    paper_id=int(row.get("paper_id") or 0),
                    document_version_id=str(row.get("document_version_id") or ""),
                    content_type=str(row.get("content_type") or "paragraph"),
                    score=1.0 - distance,
                    distance=distance,
                )
            )
        return hits

    def delete_version(self, document_version_id: str) -> int:
        table = self._get_table(create=False)
        if table is None:
            return 0
        escaped = str(document_version_id).replace("'", "''")
        try:
            before = len(table.to_arrow())
            table.delete(f"document_version_id = '{escaped}'")
            return before - len(table.to_arrow())
        except Exception as exc:
            raise VectorStoreUnavailable(f"vector delete failed: {exc}") from exc

    def count_version(self, document_version_id: str) -> int:
        table = self._get_table(create=False)
        if table is None:
            return 0
        escaped = str(document_version_id).replace("'", "''")
        try:
            return int(table.count_rows(f"document_version_id = '{escaped}'"))
        except TypeError:
            # Older LanceDB releases do not accept a predicate in count_rows;
            # the fallback is only used for verification, never retrieval.
            rows = table.search([0.0] * self.dimension).limit(100000).to_list()
            return sum(1 for row in rows if str(row.get("document_version_id") or "") == document_version_id)
        except Exception as exc:
            raise VectorStoreUnavailable(f"vector count failed: {exc}") from exc

    def count(self) -> int:
        table = self._get_table(create=False)
        return int(table.count_rows()) if table is not None else 0
