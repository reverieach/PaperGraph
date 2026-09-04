"""Isolated, fixed-subset BEIR SciFact scorecard for PaperGraph retrieval.

SciFact is deliberately *not* mixed with the PDF corpus scorecard.  Its
documents are JSONL title/abstract records, so it can exercise the same
canonical SQLite chunks and sparse retriever without making false claims about
PDF parsing, page anchors, or citation quality.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...core.paper import Paper
from ...core.storage import PaperDatabase
from ...domain.document import (
    CanonicalDocument,
    DocumentBlock,
    DocumentPage,
    ParseQualityReport,
    stable_hash,
    stable_uid,
)
from ...infrastructure.db import Database, run_migrations
from ...repositories.document_repository import DocumentRepository
from ..ingest.chunking import CHUNKER_VERSION, HierarchicalChunker
from ..retrieval.hybrid import HybridChunkRetriever


class PublicBenchmarkError(ValueError):
    """Raised when an external benchmark archive or its fixed subset is unsafe."""


_SCIFACT_PARSER_ID = "beir_scifact_jsonl"
_SCIFACT_PARSER_VERSION = "scifact-jsonl-v1"
_SCIFACT_SOURCE = "public_benchmark_scifact"
_SCIFACT_USER = "rag-eval-scifact-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PublicBenchmarkError(f"benchmark manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise PublicBenchmarkError(f"invalid benchmark manifest JSON: {path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


@dataclass(frozen=True, slots=True)
class SciFactSubsetConfig:
    benchmark_id: str
    archive_sha256: str
    corpus_member: str
    queries_member: str
    qrels_member: str
    seed: str
    query_limit: int
    document_limit: int
    license_or_usage_note: str
    source_urls: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> "SciFactSubsetConfig":
        if not isinstance(raw, dict):
            raise PublicBenchmarkError("SciFact benchmark manifest must be an object")
        benchmark_id = str(raw.get("benchmark_id") or "").strip()
        archive_sha256 = str(raw.get("archive_sha256") or "").strip().lower()
        selection = raw.get("selection")
        if not isinstance(selection, dict):
            raise PublicBenchmarkError("SciFact benchmark manifest requires selection")
        missing = [
            key
            for key, value in {
                "benchmark_id": benchmark_id,
                "archive_sha256": archive_sha256,
                "selection.seed": selection.get("seed"),
                "license_or_usage_note": raw.get("license_or_usage_note"),
            }.items()
            if not str(value or "").strip()
        ]
        if missing:
            raise PublicBenchmarkError(
                "SciFact benchmark manifest missing fields: " + ", ".join(missing)
            )
        if len(archive_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in archive_sha256
        ):
            raise PublicBenchmarkError("SciFact archive_sha256 must be a SHA-256")
        try:
            query_limit = int(selection.get("query_limit"))
            document_limit = int(selection.get("document_limit"))
        except (TypeError, ValueError) as exc:
            raise PublicBenchmarkError("SciFact selection limits must be integers") from exc
        if query_limit < 1 or document_limit < 1 or query_limit > 300 or document_limit > 400:
            raise PublicBenchmarkError(
                "SciFact selection supports 1-300 queries and 1-400 documents"
            )
        source_urls = tuple(
            str(item).strip()
            for item in raw.get("source_urls", [])
            if str(item).strip()
        )
        if not source_urls:
            raise PublicBenchmarkError("SciFact benchmark manifest requires source_urls")
        paths = raw.get("archive_members")
        if not isinstance(paths, dict):
            raise PublicBenchmarkError("SciFact benchmark manifest requires archive_members")
        members = {
            key: str(paths.get(key) or "").strip()
            for key in ("corpus", "queries", "qrels")
        }
        if not all(members.values()):
            raise PublicBenchmarkError(
                "SciFact archive_members requires corpus, queries, and qrels"
            )
        return cls(
            benchmark_id=benchmark_id,
            archive_sha256=archive_sha256,
            corpus_member=members["corpus"],
            queries_member=members["queries"],
            qrels_member=members["qrels"],
            seed=str(selection["seed"]).strip(),
            query_limit=query_limit,
            document_limit=document_limit,
            license_or_usage_note=str(raw["license_or_usage_note"]).strip(),
            source_urls=source_urls,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_id": self.benchmark_id,
            "archive_sha256": self.archive_sha256,
            "archive_members": {
                "corpus": self.corpus_member,
                "queries": self.queries_member,
                "qrels": self.qrels_member,
            },
            "selection": {
                "seed": self.seed,
                "query_limit": self.query_limit,
                "document_limit": self.document_limit,
            },
            "license_or_usage_note": self.license_or_usage_note,
            "source_urls": list(self.source_urls),
        }


@dataclass(frozen=True, slots=True)
class SciFactWorkspace:
    """A benchmark-only database; it never points at the product paper DB."""

    root: Path
    db_path: Path
    mapping_path: Path
    reports_root: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "SciFactWorkspace":
        normalized = Path(root).expanduser().resolve()
        return cls(
            root=normalized,
            db_path=normalized / "scifact.db",
            mapping_path=normalized / "scifact_mapping.json",
            reports_root=normalized / "reports",
        )


def load_scifact_config(path: str | Path) -> SciFactSubsetConfig:
    return SciFactSubsetConfig.from_dict(_read_json(Path(path).expanduser().resolve()))


def _read_jsonl_member(archive: zipfile.ZipFile, member: str) -> list[dict[str, Any]]:
    try:
        with archive.open(member) as handle:
            lines = io.TextIOWrapper(handle, encoding="utf-8").read().splitlines()
    except KeyError as exc:
        raise PublicBenchmarkError(f"SciFact archive is missing member: {member}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PublicBenchmarkError(
                f"invalid JSONL in {member}:{line_number}"
            ) from exc
        if not isinstance(value, dict):
            raise PublicBenchmarkError(f"invalid JSONL object in {member}:{line_number}")
        rows.append(value)
    return rows


def read_scifact_archive(
    *,
    archive_path: str | Path,
    config: SciFactSubsetConfig,
) -> dict[str, Any]:
    """Read a pinned BEIR archive without extracting arbitrary zip contents."""

    path = Path(archive_path).expanduser().resolve()
    if not path.is_file():
        raise PublicBenchmarkError(f"SciFact archive does not exist: {path}")
    if _sha256(path) != config.archive_sha256:
        raise PublicBenchmarkError("SciFact archive SHA-256 does not match the manifest")
    try:
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise PublicBenchmarkError("SciFact archive contains a corrupt member")
            corpus_rows = _read_jsonl_member(archive, config.corpus_member)
            query_rows = _read_jsonl_member(archive, config.queries_member)
            try:
                with archive.open(config.qrels_member) as handle:
                    reader = csv.DictReader(
                        io.TextIOWrapper(handle, encoding="utf-8"), delimiter="\t"
                    )
                    qrel_rows = list(reader)
            except KeyError as exc:
                raise PublicBenchmarkError(
                    f"SciFact archive is missing member: {config.qrels_member}"
                ) from exc
    except zipfile.BadZipFile as exc:
        raise PublicBenchmarkError(f"SciFact archive is not a valid zip: {path}") from exc

    corpus: dict[str, dict[str, str]] = {}
    for row in corpus_rows:
        document_id = str(row.get("_id") or "").strip()
        title = str(row.get("title") or "").strip()
        text = str(row.get("text") or "").strip()
        if not document_id or not (title or text):
            continue
        corpus[document_id] = {"title": title, "text": text}
    queries = {
        str(row.get("_id") or "").strip(): str(row.get("text") or "").strip()
        for row in query_rows
        if str(row.get("_id") or "").strip() and str(row.get("text") or "").strip()
    }
    positives: dict[str, set[str]] = {}
    for row in qrel_rows:
        query_id = str(row.get("query-id") or "").strip()
        document_id = str(row.get("corpus-id") or "").strip()
        try:
            score = int(float(str(row.get("score") or "0")))
        except ValueError:
            continue
        if score > 0 and query_id in queries and document_id in corpus:
            positives.setdefault(query_id, set()).add(document_id)
    if not corpus or not queries or not positives:
        raise PublicBenchmarkError("SciFact archive has no usable corpus, queries, or positive qrels")
    return {"corpus": corpus, "queries": queries, "positives": positives}


def _deterministic_order(values: list[str] | set[str], *, seed: str, kind: str) -> list[str]:
    return sorted(
        {str(value) for value in values},
        key=lambda value: (stable_hash({"seed": seed, "kind": kind, "value": value}), value),
    )


def select_scifact_subset(
    dataset: dict[str, Any],
    *,
    config: SciFactSubsetConfig,
) -> dict[str, Any]:
    """Choose fixed qrels first, then fill a bounded corpus with distractors."""

    corpus = dataset["corpus"]
    queries = dataset["queries"]
    positives = dataset["positives"]
    eligible_queries = [
        query_id
        for query_id in queries
        if positives.get(query_id) and positives[query_id].issubset(corpus)
    ]
    ordered_queries = _deterministic_order(
        eligible_queries, seed=config.seed, kind="query"
    )
    if len(ordered_queries) < config.query_limit:
        raise PublicBenchmarkError(
            f"SciFact only has {len(ordered_queries)} usable positive queries; "
            f"requested {config.query_limit}"
        )
    query_ids = ordered_queries[: config.query_limit]
    positive_document_ids = set().union(*(positives[query_id] for query_id in query_ids))
    if len(positive_document_ids) > config.document_limit:
        raise PublicBenchmarkError(
            "selected SciFact positive documents exceed document_limit; "
            "increase the limit or reduce query_limit"
        )
    remaining = _deterministic_order(
        [document_id for document_id in corpus if document_id not in positive_document_ids],
        seed=config.seed,
        kind="distractor_document",
    )
    document_ids = list(sorted(positive_document_ids))
    document_ids.extend(remaining[: config.document_limit - len(document_ids)])
    document_ids = _deterministic_order(
        document_ids, seed=config.seed, kind="selected_document"
    )
    return {
        "query_ids": query_ids,
        "document_ids": document_ids,
        "queries": [
            {
                "query_id": query_id,
                "query": queries[query_id],
                "positive_document_ids": sorted(positives[query_id]),
            }
            for query_id in query_ids
        ],
        "documents": [
            {
                "document_id": document_id,
                "title": corpus[document_id]["title"],
                "text": corpus[document_id]["text"],
            }
            for document_id in document_ids
        ],
    }


def _ensure_benchmark_user(db_path: Path) -> int:
    run_migrations(str(db_path))
    now = int(time.time())
    database = Database(str(db_path))
    with database.transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM auth_users WHERE username=?", (_SCIFACT_USER,)
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cursor = conn.execute(
            """
            INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
            VALUES(?, 'benchmark-not-a-login-secret', 'active', ?, ?)
            """,
            (_SCIFACT_USER, now, now),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("SciFact benchmark user insert did not return an id")
        return int(cursor.lastrowid)


def _find_existing_benchmark_paper_id(
    *,
    db_path: Path,
    user_id: int,
    document_id: str,
) -> int | None:
    row = Database(str(db_path)).query_one(
        """
        SELECT id FROM papers
        WHERE user_id = ? AND source = ? AND source_url = ?
        LIMIT 1
        """,
        (int(user_id), _SCIFACT_SOURCE, f"beir:scifact:{document_id}"),
    )
    return int(row["id"]) if row is not None else None


def _ensure_scifact_document(
    *,
    workspace: SciFactWorkspace,
    user_id: int,
    paper_id: int,
    document_id: str,
    title: str,
    text: str,
    chunker: HierarchicalChunker,
) -> tuple[str, int, bool]:
    """Persist one JSONL record through the canonical chunk path idempotently."""

    repository = DocumentRepository(str(workspace.db_path))
    payload = f"{title.strip()}\n\n{text.strip()}".strip()
    file_hash = stable_hash(
        {"dataset": "beir_scifact", "document_id": document_id, "payload": payload}
    )
    parser_config_hash = stable_hash(
        {
            "dataset": "beir_scifact",
            "source_format": "jsonl_title_abstract",
            "parser_revision": _SCIFACT_PARSER_VERSION,
        }
    )
    version_id = repository.create_or_get_version(
        user_id=user_id,
        paper_id=paper_id,
        file_hash=file_hash,
        file_size=len(payload.encode("utf-8")),
        parser_id=_SCIFACT_PARSER_ID,
        parser_version=_SCIFACT_PARSER_VERSION,
        parser_config_hash=parser_config_hash,
        chunker_version=chunker.config.version,
    )
    existing = repository.get_version(user_id=user_id, document_version_id=version_id)
    if existing is not None and int(existing.get("chunk_count") or 0) > 0:
        if str(existing.get("status") or "") != "active":
            if not repository.activate_version(
                user_id=user_id,
                paper_id=paper_id,
                document_version_id=version_id,
            ):
                raise PublicBenchmarkError(
                    f"cannot activate existing SciFact document: {document_id}"
                )
        return version_id, int(existing.get("chunk_count") or 0), False

    page = DocumentPage(
        page_index=1,
        printed_page_label=None,
        text=payload,
        markdown=payload,
        quality={
            "source_type": "public_text_benchmark",
            "has_pdf_page_anchor": False,
        },
    )
    block = DocumentBlock(
        block_uid=stable_uid("blk", version_id, document_id, "title_abstract"),
        page_index=1,
        block_order=0,
        block_type="paragraph",
        section_path=["BEIR SciFact", "Title and abstract"],
        text=payload,
        markdown=payload,
        provenance={
            "dataset": "beir_scifact",
            "document_id": document_id,
            "source_type": "public_text_benchmark",
            "has_pdf_page_anchor": False,
        },
    )
    document = CanonicalDocument(
        document_version_id=version_id,
        user_id=user_id,
        paper_id=paper_id,
        file_hash=file_hash,
        parser_id=_SCIFACT_PARSER_ID,
        parser_version=_SCIFACT_PARSER_VERSION,
        pages=[page],
        blocks=[block],
        metadata={
            "dataset": "beir_scifact",
            "document_id": document_id,
            "source_type": "public_text_benchmark",
            "has_pdf_page_anchor": False,
        },
        quality=ParseQualityReport(
            page_count=1,
            non_empty_page_count=1,
            block_count=1,
            text_char_count=len(payload),
            score=1.0,
            flags=["public_text_benchmark_no_pdf_page_citations"],
        ),
    )
    chunks = chunker.chunk_document(document, paper_title=title)
    if not chunks:
        raise PublicBenchmarkError(f"SciFact document produced no chunks: {document_id}")
    repository.persist_document(document, chunks, quality_score=1.0)
    if not repository.activate_version(
        user_id=user_id,
        paper_id=paper_id,
        document_version_id=version_id,
    ):
        raise PublicBenchmarkError(f"cannot activate SciFact document: {document_id}")
    return version_id, len(chunks), True


def prepare_scifact_subset(
    *,
    manifest_path: str | Path,
    archive_path: str | Path,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """Build a bounded, canonical, benchmark-only SciFact workspace."""

    config = load_scifact_config(manifest_path)
    dataset = read_scifact_archive(archive_path=archive_path, config=config)
    subset = select_scifact_subset(dataset, config=config)
    workspace = SciFactWorkspace.from_root(workspace_root)
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.reports_root.mkdir(parents=True, exist_ok=True)
    user_id = _ensure_benchmark_user(workspace.db_path)
    papers = PaperDatabase(str(workspace.db_path))
    chunker = HierarchicalChunker()
    mappings: list[dict[str, Any]] = []
    created_documents = 0
    reused_documents = 0
    total_chunks = 0
    for record in subset["documents"]:
        document_id = str(record["document_id"])
        paper_id = _find_existing_benchmark_paper_id(
            db_path=workspace.db_path,
            user_id=user_id,
            document_id=document_id,
        )
        if paper_id is None:
            paper_id, _created = papers.add_paper(
                Paper(
                    title=str(record["title"]),
                    abstract=str(record["text"]),
                    source=_SCIFACT_SOURCE,
                    source_url=f"beir:scifact:{document_id}",
                    tags=["rag-eval", "public-benchmark", "scifact"],
                ),
                user_id=user_id,
            )
        version_id, chunk_count, created = _ensure_scifact_document(
            workspace=workspace,
            user_id=user_id,
            paper_id=paper_id,
            document_id=document_id,
            title=str(record["title"]),
            text=str(record["text"]),
            chunker=chunker,
        )
        created_documents += int(created)
        reused_documents += int(not created)
        total_chunks += chunk_count
        mappings.append(
            {
                "document_id": document_id,
                "paper_id": int(paper_id),
                "document_version_id": version_id,
                "title": str(record["title"]),
            }
        )
    mapping = {
        "schema_version": 1,
        "benchmark_id": config.benchmark_id,
        "prepared_at": int(time.time()),
        "workspace_root": str(workspace.root),
        "user_id": user_id,
        "source": config.to_dict(),
        "evaluation_surface": "public_text_retrieval_only_no_pdf_page_citations",
        "documents": mappings,
        "queries": subset["queries"],
    }
    _write_json(workspace.mapping_path, mapping)
    result = {
        "schema_version": 1,
        "benchmark_id": config.benchmark_id,
        "workspace_root": str(workspace.root),
        "evaluation_surface": "public_text_retrieval_only_no_pdf_page_citations",
        "query_count": len(subset["queries"]),
        "document_count": len(mappings),
        "created_document_count": created_documents,
        "reused_document_count": reused_documents,
        "total_chunk_count": total_chunks,
        "archive_sha256": config.archive_sha256,
    }
    _write_json(workspace.reports_root / "prepare_latest.json", result)
    return result


def _load_mapping(workspace: SciFactWorkspace) -> dict[str, Any]:
    payload = _read_json(workspace.mapping_path)
    if not isinstance(payload, dict):
        raise PublicBenchmarkError("SciFact workspace mapping must be an object")
    if not isinstance(payload.get("documents"), list) or not isinstance(
        payload.get("queries"), list
    ):
        raise PublicBenchmarkError("SciFact workspace mapping is incomplete; run prepare first")
    return payload


def run_scifact_sparse_scorecard(
    *,
    workspace_root: str | Path,
    limit: int = 10,
) -> dict[str, Any]:
    """Run a no-provider sparse scorecard over a fixed public text subset."""

    workspace = SciFactWorkspace.from_root(workspace_root)
    mapping = _load_mapping(workspace)
    user_id = int(mapping["user_id"])
    document_rows = mapping["documents"]
    paper_ids = [int(item["paper_id"]) for item in document_rows]
    document_by_paper = {int(item["paper_id"]): str(item["document_id"]) for item in document_rows}
    repository = DocumentRepository(str(workspace.db_path))
    inactive = [
        paper_id
        for paper_id in paper_ids
        if repository.get_active_version(user_id=user_id, paper_id=paper_id) is None
    ]
    if inactive:
        raise PublicBenchmarkError(
            f"SciFact workspace has {len(inactive)} documents without active canonical versions"
        )
    retriever = HybridChunkRetriever(repository)
    bounded_limit = max(1, min(int(limit), 50))
    cases: list[dict[str, Any]] = []
    recall_values: list[float] = []
    reciprocal_ranks: list[float] = []
    unexpected_degradation: set[str] = set()
    for record in mapping["queries"]:
        query = str(record.get("query") or "").strip()
        query_id = str(record.get("query_id") or "").strip()
        positives = {str(value) for value in record.get("positive_document_ids", [])}
        if not query or not query_id or not positives:
            raise PublicBenchmarkError("SciFact workspace contains an invalid query mapping")
        # BEIR qrels are document-level while PaperGraph retrieves chunks.
        # Pull a wider bounded chunk candidate window, then collapse parent /
        # child duplicates before measuring a document rank.  Treating two
        # chunks from the same document as rank 1 and 2 would understate the
        # actual document-level rank and make this scorecard incomparable even
        # to its own future runs.
        outcome = retriever.retrieve(
            user_id=user_id,
            paper_ids=paper_ids,
            query=query,
            limit=min(50, max(bounded_limit, bounded_limit * 3)),
        )
        hit_document_ids: list[str] = []
        seen_document_ids: set[str] = set()
        for hit in outcome.hits:
            document_id = document_by_paper.get(int(hit.paper_id))
            if document_id and document_id not in seen_document_ids:
                seen_document_ids.add(document_id)
                hit_document_ids.append(document_id)
            if len(hit_document_ids) >= bounded_limit:
                break
        first_relevant_rank = next(
            (
                rank
                for rank, document_id in enumerate(hit_document_ids, 1)
                if document_id in positives
            ),
            None,
        )
        recall_values.append(1.0 if first_relevant_rank is not None else 0.0)
        reciprocal_ranks.append(1.0 / first_relevant_rank if first_relevant_rank else 0.0)
        unexpected_degradation.update(
            reason
            for reason in outcome.degradation_reasons
            if reason != "dense_retrieval_not_configured"
        )
        cases.append(
            {
                "query_id": query_id,
                "first_relevant_rank": first_relevant_rank,
                "positive_document_ids": sorted(positives),
                "hit_document_ids": hit_document_ids,
            }
        )
    result = {
        "schema_version": 1,
        "benchmark_id": mapping.get("benchmark_id"),
        "created_at": int(time.time()),
        "evaluation_surface": "public_text_retrieval_only_no_pdf_page_citations",
        "quality_status": "diagnostic_public_subset_not_golden",
        "external_provider_calls": False,
        "query_count": len(cases),
        "document_count": len(document_rows),
        "limit": bounded_limit,
        "recall_at_k": round(sum(recall_values) / len(recall_values), 6),
        "mrr_at_k": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 6),
        "unexpected_degradation_reasons": sorted(unexpected_degradation),
        "cases": cases,
    }
    _write_json(workspace.reports_root / "sparse_scorecard_latest.json", result)
    return result


__all__ = [
    "PublicBenchmarkError",
    "SciFactSubsetConfig",
    "SciFactWorkspace",
    "load_scifact_config",
    "prepare_scifact_subset",
    "read_scifact_archive",
    "run_scifact_sparse_scorecard",
    "select_scifact_subset",
]
