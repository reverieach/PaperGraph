"""Reproducible local evaluation workflow for the isolated RAG corpus.

The application database is deliberately never accepted as an evaluation
target.  This module creates a separate SQLite source of truth and separate
artifact/vector roots under ``backend/data/rag_eval_workspace`` (all ignored by
Git), while corpus metadata, Silver cases and code stay version controlled.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ...core.paper import Paper
from ...core.storage import PaperDatabase
from ...infrastructure.db import Database, run_migrations
from ...infrastructure.vector.lancedb_store import LanceDBVectorStore
from ...repositories.document_repository import DocumentRepository
from ..embedding.dashscope_embedding import DashScopeEmbeddingProvider
from ..embedding.indexer import DocumentEmbeddingIndexer
from ..ingest.service import IngestService
from ..rerank.dashscope_reranker import DashScopeReranker
from ..retrieval.hybrid import HybridChunkRetriever


class CorpusManifestError(ValueError):
    """Raised when a corpus or case manifest is malformed or inconsistent."""


_TRACKED_QUALITY_STATUSES = {"silver", "golden_candidate", "frozen_gold"}
_TRACKED_REVIEW_STATUSES = {
    "auto_verified",
    "pending_user_review",
    "approved",
    "rejected",
}


def _require_external_opt_in(*, enabled: bool, run_external: bool, purpose: str) -> None:
    """Fail closed before an evaluation would invoke a billable provider.

    Dense indexing, query embeddings and reranking use externally configured
    endpoints.  They must never become an accidental side effect of a normal
    local regression command, even when credentials are available in the
    developer environment.
    """

    if enabled and not run_external:
        raise CorpusManifestError(
            f"{purpose} can call an external provider; pass run_external=True "
            "(CLI: --run-external) explicitly"
        )


def _bounded_positive(value: int, *, name: str, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CorpusManifestError(f"{name} must be an integer") from exc
    if parsed < 1 or parsed > maximum:
        raise CorpusManifestError(f"{name} must be between 1 and {maximum}")
    return parsed


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CorpusManifestError(f"manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CorpusManifestError(f"invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _safe_child(root: Path, value: str) -> Path:
    root = root.expanduser().resolve()
    candidate = (root / value).resolve()
    if candidate != root and root not in candidate.parents:
        raise CorpusManifestError(f"corpus path escapes root: {value}")
    return candidate


@dataclass(frozen=True, slots=True)
class CorpusPaper:
    corpus_id: str
    title: str
    file: str
    source_url: str
    pdf_url: str
    sha256: str
    pages: int
    languages: tuple[str, ...]
    features: tuple[str, ...]
    license_or_usage_note: str
    arxiv_id: str | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> "CorpusPaper":
        if not isinstance(raw, dict):
            raise CorpusManifestError("corpus paper must be an object")
        required = ("corpus_id", "title", "file", "source_url", "pdf_url", "sha256", "pages")
        missing = [key for key in required if not str(raw.get(key) or "").strip()]
        if missing:
            raise CorpusManifestError(f"corpus paper missing fields: {', '.join(missing)}")
        corpus_id = str(raw["corpus_id"]).strip()
        sha = str(raw["sha256"]).strip().lower()
        if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
            raise CorpusManifestError(f"invalid sha256 for {corpus_id}")
        try:
            pages = int(raw["pages"])
        except (TypeError, ValueError) as exc:
            raise CorpusManifestError(f"invalid pages for {corpus_id}") from exc
        if pages < 1:
            raise CorpusManifestError(f"pages must be positive for {corpus_id}")
        languages = tuple(str(item).strip() for item in raw.get("languages", []) if str(item).strip())
        if not languages:
            raise CorpusManifestError(f"languages must be non-empty for {corpus_id}")
        return cls(
            corpus_id=corpus_id,
            title=str(raw["title"]).strip(),
            file=str(raw["file"]).strip(),
            source_url=str(raw["source_url"]).strip(),
            pdf_url=str(raw["pdf_url"]).strip(),
            sha256=sha,
            pages=pages,
            languages=languages,
            features=tuple(str(item).strip() for item in raw.get("features", []) if str(item).strip()),
            license_or_usage_note=str(raw.get("license_or_usage_note") or "").strip(),
            arxiv_id=str(raw.get("arxiv_id") or "").strip() or None,
        )


@dataclass(frozen=True, slots=True)
class EvaluationWorkspace:
    root: Path
    db_path: Path
    artifacts_root: Path
    vectors_root: Path
    reports_root: Path
    mapping_path: Path

    @classmethod
    def from_root(cls, root: str | Path) -> "EvaluationWorkspace":
        normalized = Path(root).expanduser().resolve()
        return cls(
            root=normalized,
            db_path=normalized / "rag_eval.db",
            artifacts_root=normalized / "artifacts",
            vectors_root=normalized / "vectors",
            reports_root=normalized / "reports",
            mapping_path=normalized / "corpus_mapping.json",
        )


def load_corpus_manifest(path: str | Path) -> tuple[dict[str, Any], list[CorpusPaper]]:
    manifest_path = Path(path).expanduser().resolve()
    payload = _read_json(manifest_path)
    if not isinstance(payload, dict):
        raise CorpusManifestError("corpus manifest must be an object")
    papers_raw = payload.get("papers")
    if not isinstance(papers_raw, list) or not papers_raw:
        raise CorpusManifestError("corpus manifest requires non-empty papers")
    papers = [CorpusPaper.from_dict(item) for item in papers_raw]
    ids = [paper.corpus_id for paper in papers]
    if len(ids) != len(set(ids)):
        raise CorpusManifestError("corpus_id values must be unique")
    return payload, papers


def verify_corpus(
    papers: Iterable[CorpusPaper],
    *,
    corpus_root: str | Path,
) -> list[dict[str, Any]]:
    """Verify local source PDFs before any DB write or external API call."""

    root = Path(corpus_root).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for paper in papers:
        path = _safe_child(root, paper.file)
        if not path.is_file():
            raise CorpusManifestError(f"missing corpus PDF for {paper.corpus_id}: {path}")
        with path.open("rb") as handle:
            header = handle.read(8)
        if not header.startswith(b"%PDF-"):
            raise CorpusManifestError(f"not a PDF for {paper.corpus_id}: {path}")
        digest = _sha256(path)
        if digest != paper.sha256:
            raise CorpusManifestError(
                f"sha256 mismatch for {paper.corpus_id}: expected {paper.sha256}, got {digest}"
            )
        try:
            import fitz

            document = fitz.open(path)
            page_count = len(document)
            encrypted = bool(document.is_encrypted)
            document.close()
        except Exception as exc:
            raise CorpusManifestError(f"cannot open PDF {paper.corpus_id}: {type(exc).__name__}") from exc
        if encrypted:
            raise CorpusManifestError(f"encrypted PDF is not a normal corpus item: {paper.corpus_id}")
        if page_count != paper.pages:
            raise CorpusManifestError(
                f"page count mismatch for {paper.corpus_id}: expected {paper.pages}, got {page_count}"
            )
        results.append(
            {
                "corpus_id": paper.corpus_id,
                "path": str(path),
                "sha256": digest,
                "pages": page_count,
                "size_bytes": path.stat().st_size,
            }
        )
    return results


def _ensure_eval_user(db_path: Path, *, username: str = "rag-eval-runner") -> int:
    run_migrations(str(db_path))
    now = int(time.time())
    with Database(str(db_path)).transaction() as conn:
        existing = conn.execute(
            "SELECT id FROM auth_users WHERE username=?", (username,)
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cursor = conn.execute(
            """
            INSERT INTO auth_users(username,password_hash,status,created_at,updated_at)
            VALUES(?, 'evaluation-not-a-login-secret', 'active', ?, ?)
            """,
            (username, now, now),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("evaluation user insert did not return an id")
        return int(cursor.lastrowid)


def _find_existing_eval_paper_id(
    *,
    db_path: Path,
    user_id: int,
    paper: CorpusPaper,
) -> int | None:
    """Resolve an evaluation paper without relying on optional external IDs.

    ``PaperDatabase.add_paper`` correctly de-duplicates DOI/arXiv/PMID-style
    identities for product imports.  A public conference PDF can legitimately
    have none of those identifiers, however, and repeated evaluation
    preparation must not create a new paper row and abandon the old canonical
    version.  Evaluation rows are isolated by user and source, then matched by
    their stable source URL (or the next best manifest identity).
    """

    where = ["p.user_id = ?", "p.source = 'rag_eval_corpus'"]
    values: list[Any] = [int(user_id)]
    source_url = str(paper.source_url or "").strip()
    pdf_url = str(paper.pdf_url or "").strip()
    if source_url:
        where.append("p.source_url = ?")
        values.append(source_url)
    elif paper.arxiv_id:
        where.append("p.arxiv_id = ?")
        values.append(str(paper.arxiv_id))
    else:
        where.extend(["p.title = ?", "p.pdf_url = ?"])
        values.extend([str(paper.title), pdf_url])
    row = Database(str(db_path)).query_one(
        f"""
        SELECT p.id,
               EXISTS(
                   SELECT 1 FROM document_versions v
                   WHERE v.paper_id = p.id AND v.user_id = p.user_id
                     AND v.status = 'active'
               ) AS has_active_version
        FROM papers p
        WHERE {' AND '.join(where)}
        ORDER BY has_active_version DESC, p.id ASC
        LIMIT 1
        """,
        tuple(values),
    )
    return int(row["id"]) if row is not None else None


def prepare_workspace(
    *,
    manifest_path: str | Path,
    corpus_root: str | Path,
    workspace_root: str | Path,
) -> tuple[EvaluationWorkspace, dict[str, Any]]:
    """Create/refresh an isolated DB mapping without touching the app DB."""

    metadata, papers = load_corpus_manifest(manifest_path)
    verified = verify_corpus(papers, corpus_root=corpus_root)
    workspace = EvaluationWorkspace.from_root(workspace_root)
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.artifacts_root.mkdir(parents=True, exist_ok=True)
    workspace.vectors_root.mkdir(parents=True, exist_ok=True)
    workspace.reports_root.mkdir(parents=True, exist_ok=True)
    user_id = _ensure_eval_user(workspace.db_path)
    database = PaperDatabase(str(workspace.db_path))
    mapping_papers: list[dict[str, Any]] = []
    verified_by_id = {str(item["corpus_id"]): item for item in verified}
    for paper in papers:
        paper_id = _find_existing_eval_paper_id(
            db_path=workspace.db_path,
            user_id=user_id,
            paper=paper,
        )
        if paper_id is None:
            paper_id, _created = database.add_paper(
                Paper(
                    title=paper.title,
                    arxiv_id=paper.arxiv_id,
                    source_url=paper.source_url,
                    pdf_url=paper.pdf_url,
                    source="rag_eval_corpus",
                    tags=["rag-eval", *paper.languages],
                ),
                user_id=user_id,
            )
        mapping_papers.append(
            {
                "corpus_id": paper.corpus_id,
                "paper_id": int(paper_id),
                "title": paper.title,
                "file": paper.file,
                "sha256": paper.sha256,
                "pages": paper.pages,
                "verified": verified_by_id[paper.corpus_id],
            }
        )
    mapping = {
        "schema_version": 1,
        "prepared_at": int(time.time()),
        "manifest_name": metadata.get("name"),
        "manifest_path": str(Path(manifest_path).expanduser().resolve()),
        "corpus_root": str(Path(corpus_root).expanduser().resolve()),
        "workspace_root": str(workspace.root),
        "user_id": user_id,
        "papers": mapping_papers,
    }
    _write_json(workspace.mapping_path, mapping)
    return workspace, mapping


def _load_mapping(workspace: EvaluationWorkspace) -> dict[str, Any]:
    payload = _read_json(workspace.mapping_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        raise CorpusManifestError("evaluation workspace mapping is invalid; run prepare first")
    return payload


def _embedding_indexer(workspace: EvaluationWorkspace) -> DocumentEmbeddingIndexer:
    provider = DashScopeEmbeddingProvider()
    if not provider.api_key or not provider.base_url:
        raise CorpusManifestError(
            "embedding requested but provider API key/base URL is not configured"
        )
    return DocumentEmbeddingIndexer(
        DocumentRepository(str(workspace.db_path)),
        provider,
        LanceDBVectorStore(str(workspace.vectors_root), dimension=provider.dimension),
    )


def run_ingest(
    *,
    workspace_root: str | Path,
    parser_mode: str = "auto",
    with_embedding: bool = False,
    run_external: bool = False,
    max_external_papers: int = 8,
    corpus_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Ingest verified corpus PDFs into the isolated workspace only."""

    workspace = EvaluationWorkspace.from_root(workspace_root)
    mapping = _load_mapping(workspace)
    selected = {str(value).strip() for value in (corpus_ids or []) if str(value).strip()}
    selected_items = [
        item
        for item in mapping["papers"]
        if not selected or str(item["corpus_id"]) in selected
    ]
    unknown = sorted(selected - {str(item["corpus_id"]) for item in mapping["papers"]})
    if unknown:
        raise CorpusManifestError(f"unknown corpus IDs for ingest: {unknown}")
    _require_external_opt_in(
        enabled=bool(with_embedding),
        run_external=bool(run_external),
        purpose="embedding ingest",
    )
    external_limit = _bounded_positive(
        max_external_papers,
        name="max_external_papers",
        maximum=50,
    )
    if with_embedding and len(selected_items) > external_limit:
        raise CorpusManifestError(
            "embedding ingest selected "
            f"{len(selected_items)} papers, above max_external_papers={external_limit}"
        )
    repository = DocumentRepository(str(workspace.db_path))
    indexer = _embedding_indexer(workspace) if with_embedding else None
    service = IngestService(
        str(workspace.db_path),
        artifacts_root=str(workspace.artifacts_root),
        embedding_indexer=indexer,
    )
    started = time.perf_counter()
    reports: list[dict[str, Any]] = []
    for item in selected_items:
        corpus_id = str(item["corpus_id"])
        if selected and corpus_id not in selected:
            continue
        path = _safe_child(Path(mapping["corpus_root"]), str(item["file"]))
        if _sha256(path) != str(item["sha256"]):
            raise CorpusManifestError(f"corpus file changed after prepare: {corpus_id}")
        report_started = time.perf_counter()
        report = service.ingest_pdf(
            user_id=int(mapping["user_id"]),
            paper_id=int(item["paper_id"]),
            pdf_path=str(path),
            paper_title=str(item["title"]),
            parser_mode=parser_mode,
        )
        report_data = report.to_dict()
        report_data["corpus_id"] = corpus_id
        report_data["elapsed_ms"] = round((time.perf_counter() - report_started) * 1000, 2)
        active = repository.get_active_version(
            user_id=int(mapping["user_id"]), paper_id=int(item["paper_id"])
        )
        report_data["active_document_version_id"] = active.get("id") if active else None
        reports.append(report_data)
    result = {
        "schema_version": 1,
        "created_at": int(time.time()),
        "workspace_root": str(workspace.root),
        "with_embedding": bool(with_embedding),
        "external": {
            "explicit_opt_in": bool(run_external),
            "max_external_papers": external_limit,
            "selected_paper_count": len(selected_items),
        },
        "parser_mode": parser_mode,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "reports": reports,
    }
    _write_json(workspace.reports_root / "ingest_latest.json", result)
    return result


def _string_list(value: Any, *, field: str, line_number: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CorpusManifestError(f"case {line_number} field {field} must be a list")
    values = [str(item).strip() for item in value if str(item).strip()]
    return list(dict.fromkeys(values))


def _int_list(value: Any, *, field: str, line_number: int) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CorpusManifestError(f"case {line_number} field {field} must be a list")
    parsed: list[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError) as exc:
            raise CorpusManifestError(
                f"case {line_number} field {field} contains a non-integer"
            ) from exc
        if number < 1:
            raise CorpusManifestError(
                f"case {line_number} field {field} must contain positive values"
            )
        parsed.append(number)
    return list(dict.fromkeys(parsed))


def _normalize_evidence(
    raw: dict[str, Any],
    *,
    corpus_ids: list[str],
    answerable: bool,
    require_anchor: bool,
    line_number: int,
) -> list[dict[str, Any]]:
    """Normalize qrels into independently matchable evidence anchors.

    A qrel can have both an exact current chunk UID and stable fallbacks such
    as page/section/terms.  Exact UIDs catch accidental annotation mistakes;
    stable anchors make it possible to remap a valid question after a parser
    or chunker upgrade without pretending an old UID is still canonical.
    """

    evidence_raw = raw.get("evidence")
    if evidence_raw is None:
        # Legacy one-anchor JSONL remains readable while new tracked cases use
        # the explicit list.  This also keeps small test fixtures concise.
        evidence_raw = [
            {
                "corpus_id": corpus_id,
                "pages": raw.get("gold_pages", []),
                "section_patterns": raw.get("gold_section_patterns", []),
                "required_terms": raw.get("required_terms", []),
                "chunk_uids": raw.get("gold_chunk_uids", []),
                "chunk_text_hashes": raw.get("gold_chunk_text_hashes", []),
                "block_text_hashes": raw.get("gold_block_text_hashes", []),
            }
            for corpus_id in corpus_ids
        ]
    if not isinstance(evidence_raw, list):
        raise CorpusManifestError(f"case {line_number} field evidence must be a list")
    evidence: list[dict[str, Any]] = []
    for index, item in enumerate(evidence_raw, 1):
        if not isinstance(item, dict):
            raise CorpusManifestError(
                f"case {line_number} evidence {index} must be an object"
            )
        corpus_id = str(item.get("corpus_id") or "").strip()
        if not corpus_id:
            if len(corpus_ids) == 1:
                corpus_id = corpus_ids[0]
            else:
                raise CorpusManifestError(
                    f"case {line_number} evidence {index} requires corpus_id"
                )
        if corpus_id not in corpus_ids:
            raise CorpusManifestError(
                f"case {line_number} evidence {index} corpus_id is outside corpus_ids"
            )
        anchor = {
            "corpus_id": corpus_id,
            "group": str(item.get("group") or f"evidence_{index}").strip(),
            "pages": _int_list(
                item.get("pages", item.get("gold_pages")),
                field=f"evidence[{index}].pages",
                line_number=line_number,
            ),
            "section_patterns": _string_list(
                item.get("section_patterns", item.get("gold_section_patterns")),
                field=f"evidence[{index}].section_patterns",
                line_number=line_number,
            ),
            "required_terms": _string_list(
                item.get("required_terms"),
                field=f"evidence[{index}].required_terms",
                line_number=line_number,
            ),
            "chunk_uids": _string_list(
                item.get("chunk_uids"),
                field=f"evidence[{index}].chunk_uids",
                line_number=line_number,
            ),
            "chunk_text_hashes": _string_list(
                item.get("chunk_text_hashes"),
                field=f"evidence[{index}].chunk_text_hashes",
                line_number=line_number,
            ),
            "block_text_hashes": _string_list(
                item.get("block_text_hashes"),
                field=f"evidence[{index}].block_text_hashes",
                line_number=line_number,
            ),
        }
        if not anchor["group"]:
            raise CorpusManifestError(
                f"case {line_number} evidence {index} group must be non-empty"
            )
        if answerable and require_anchor and not any(
            anchor[key]
            for key in (
                "pages",
                "section_patterns",
                "required_terms",
                "chunk_uids",
                "chunk_text_hashes",
                "block_text_hashes",
            )
        ):
            raise CorpusManifestError(
                f"case {line_number} answerable evidence {index} has no anchor"
            )
        evidence.append(anchor)
    if answerable and require_anchor and not evidence:
        raise CorpusManifestError(f"case {line_number} answerable case requires evidence")
    return evidence


def _normalize_tracked_metadata(
    raw: dict[str, Any],
    *,
    corpus_ids: list[str],
    line_number: int,
) -> None:
    quality_status = str(raw.get("quality_status") or "").strip()
    if not quality_status:
        return
    if quality_status not in _TRACKED_QUALITY_STATUSES:
        raise CorpusManifestError(
            f"case {line_number} has unsupported quality_status: {quality_status}"
        )
    review_status = str(raw.get("review_status") or "").strip()
    if review_status not in _TRACKED_REVIEW_STATUSES:
        raise CorpusManifestError(f"case {line_number} has invalid review_status")
    required_fields = (
        "query_language",
        "document_language",
        "task_type",
        "generator_model",
        "generator_prompt_version",
        "verifier_model",
        "parser_version",
        "chunker_version",
    )
    if len(corpus_ids) == 1:
        required_fields = (*required_fields, "source_sha256")
    missing = [key for key in required_fields if not str(raw.get(key) or "").strip()]
    if missing:
        raise CorpusManifestError(
            f"case {line_number} tracked metadata missing: {', '.join(missing)}"
        )
    source_sha256 = str(raw.get("source_sha256") or "").strip().lower()
    if source_sha256 and (
        len(source_sha256) != 64
        or any(char not in "0123456789abcdef" for char in source_sha256)
    ):
        raise CorpusManifestError(f"case {line_number} has invalid source_sha256")
    if quality_status == "golden_candidate" and review_status != "pending_user_review":
        raise CorpusManifestError(
            f"case {line_number} golden candidate must remain pending_user_review"
        )
    if quality_status == "frozen_gold" and review_status != "approved":
        raise CorpusManifestError(
            f"case {line_number} frozen gold must be approved"
        )
    raw["quality_status"] = quality_status
    raw["review_status"] = review_status
    if source_sha256:
        raw["source_sha256"] = source_sha256
    if len(corpus_ids) > 1:
        provenance = raw.get("provenance_by_corpus")
        if not isinstance(provenance, dict):
            raise CorpusManifestError(
                f"case {line_number} with multiple corpus_ids requires provenance_by_corpus"
            )
        normalized_provenance: dict[str, dict[str, str]] = {}
        for corpus_id in corpus_ids:
            item = provenance.get(corpus_id)
            if not isinstance(item, dict):
                raise CorpusManifestError(
                    f"case {line_number} missing provenance for {corpus_id}"
                )
            normalized_item = {
                key: str(item.get(key) or "").strip()
                for key in ("source_sha256", "parser_version", "chunker_version")
            }
            if not all(normalized_item.values()):
                raise CorpusManifestError(
                    f"case {line_number} provenance for {corpus_id} is incomplete"
                )
            sha = normalized_item["source_sha256"].lower()
            if len(sha) != 64 or any(char not in "0123456789abcdef" for char in sha):
                raise CorpusManifestError(
                    f"case {line_number} provenance for {corpus_id} has invalid source_sha256"
                )
            normalized_item["source_sha256"] = sha
            normalized_provenance[corpus_id] = normalized_item
        raw["provenance_by_corpus"] = normalized_provenance


def load_retrieval_cases(path: str | Path) -> list[dict[str, Any]]:
    case_path = Path(path).expanduser().resolve()
    try:
        raw_lines = case_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise CorpusManifestError(f"retrieval case file does not exist: {case_path}") from exc
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(raw_lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusManifestError(f"invalid JSONL at {case_path}:{line_number}") from exc
        if not isinstance(raw, dict):
            raise CorpusManifestError(f"case {line_number} must be an object")
        case_id = str(raw.get("case_id") or "").strip()
        query = str(raw.get("query") or "").strip()
        corpus_ids = raw.get("corpus_ids", raw.get("paper_ids"))
        if not case_id or not query or not isinstance(corpus_ids, list) or not corpus_ids:
            raise CorpusManifestError(f"case {line_number} requires case_id/query/corpus_ids")
        if case_id in seen:
            raise CorpusManifestError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        normalized_ids = _string_list(
            corpus_ids, field="corpus_ids", line_number=line_number
        )
        if not normalized_ids:
            raise CorpusManifestError(f"case {line_number} requires non-empty corpus_ids")
        answerable_value = raw.get("answerable", True)
        if not isinstance(answerable_value, bool):
            raise CorpusManifestError(f"case {line_number} answerable must be boolean")
        raw["case_id"] = case_id
        raw["query"] = query
        raw["corpus_ids"] = normalized_ids
        raw["answerable"] = answerable_value
        raw["gold_pages"] = _int_list(
            raw.get("gold_pages"), field="gold_pages", line_number=line_number
        )
        raw["gold_section_patterns"] = _string_list(
            raw.get("gold_section_patterns"),
            field="gold_section_patterns",
            line_number=line_number,
        )
        raw["required_terms"] = _string_list(
            raw.get("required_terms"),
            field="required_terms",
            line_number=line_number,
        )
        _normalize_tracked_metadata(
            raw,
            corpus_ids=normalized_ids,
            line_number=line_number,
        )
        raw["evidence"] = _normalize_evidence(
            raw,
            corpus_ids=normalized_ids,
            answerable=answerable_value,
            require_anchor=str(raw.get("quality_status") or "") in _TRACKED_QUALITY_STATUSES,
            line_number=line_number,
        )
        required_groups = _string_list(
            raw.get("required_evidence_groups"),
            field="required_evidence_groups",
            line_number=line_number,
        )
        available_groups = {
            str(anchor["group"])
            for anchor in raw["evidence"]
        }
        if not required_groups and answerable_value:
            required_groups = sorted(available_groups)
        if any(group not in available_groups for group in required_groups):
            raise CorpusManifestError(
                f"case {line_number} required_evidence_groups references an unknown group"
            )
        raw["required_evidence_groups"] = required_groups
        cases.append(raw)
    if not cases:
        raise CorpusManifestError("retrieval cases must be non-empty")
    return cases


def _matched_evidence_indices(
    hit: Any,
    case: dict[str, Any],
    paper_by_id: dict[int, str],
) -> list[int]:
    hit_paper = paper_by_id.get(int(getattr(hit, "paper_id", 0) or 0))
    if hit_paper not in set(case["corpus_ids"]):
        return []
    page_start = int(getattr(hit, "page_start", 0) or 0)
    page_end = int(getattr(hit, "page_end", page_start) or page_start)
    hit_uid = str(getattr(hit, "chunk_uid", "") or "")
    hit_section = " > ".join(
        str(value) for value in (getattr(hit, "section_path", []) or [])
    ).casefold()
    hit_text = str(getattr(hit, "display_text", "") or "").casefold()
    matched: list[int] = []
    for index, anchor in enumerate(case.get("evidence") or []):
        if str(anchor.get("corpus_id") or "") != hit_paper:
            continue
        exact_uids = set(anchor.get("chunk_uids") or [])
        if hit_uid and hit_uid in exact_uids:
            matched.append(index)
            continue
        pages = set(anchor.get("pages") or [])
        sections = [str(value).casefold() for value in anchor.get("section_patterns") or []]
        terms = [str(value).casefold() for value in anchor.get("required_terms") or []]
        flexible_anchor = bool(pages or sections or terms)
        if not flexible_anchor:
            continue
        if pages and not any(page_start <= page <= page_end for page in pages):
            continue
        if sections and not any(pattern in hit_section for pattern in sections):
            continue
        if terms and not all(term in hit_text for term in terms):
            continue
        matched.append(index)
    return matched


def _is_relevant_hit(hit: Any, case: dict[str, Any], paper_by_id: dict[int, str]) -> bool:
    return bool(_matched_evidence_indices(hit, case, paper_by_id))


def _case_provenance_for_corpus(
    case: dict[str, Any],
    corpus_id: str,
) -> dict[str, str] | None:
    if not str(case.get("quality_status") or "").strip():
        return None
    per_corpus = case.get("provenance_by_corpus")
    if isinstance(per_corpus, dict):
        value = per_corpus.get(corpus_id)
        return dict(value) if isinstance(value, dict) else None
    return {
        "source_sha256": str(case.get("source_sha256") or ""),
        "parser_version": str(case.get("parser_version") or ""),
        "chunker_version": str(case.get("chunker_version") or ""),
    }


def validate_retrieval_cases(
    *,
    workspace_root: str | Path,
    cases_path: str | Path,
) -> dict[str, Any]:
    """Validate tracked qrels against this exact isolated canonical corpus.

    The check is intentionally independent of retrieval scores.  It catches a
    stale page, a changed PDF hash, a parser/chunker drift, or a dangling chunk
    UID before those labels can make a retrieval run look worse (or better)
    than it really is.
    """

    workspace = EvaluationWorkspace.from_root(workspace_root)
    mapping = _load_mapping(workspace)
    cases = load_retrieval_cases(cases_path)
    user_id = int(mapping["user_id"])
    mapping_by_id = {str(item["corpus_id"]): item for item in mapping["papers"]}
    repository = DocumentRepository(str(workspace.db_path))
    active_by_id: dict[str, dict[str, Any]] = {}
    for corpus_id, item in mapping_by_id.items():
        active = repository.get_active_version(
            user_id=user_id,
            paper_id=int(item["paper_id"]),
        )
        if active is None:
            raise CorpusManifestError(
                f"evaluation corpus {corpus_id} has no active canonical document"
            )
        active_by_id[corpus_id] = active

    requested_chunk_uids = {
        uid
        for case in cases
        for anchor in case.get("evidence") or []
        for uid in anchor.get("chunk_uids") or []
    }
    chunks_by_uid = {
        str(row["chunk_uid"]): row
        for row in repository.get_chunks_by_uid(
            user_id=user_id,
            chunk_uids=sorted(requested_chunk_uids),
            active_only=True,
        )
    }
    verified_chunk_qrels = 0
    verified_cases: list[dict[str, Any]] = []
    for case in cases:
        referenced_ids = set(case["corpus_ids"])
        referenced_ids.update(
            str(anchor.get("corpus_id") or "")
            for anchor in case.get("evidence") or []
        )
        unknown = sorted(corpus_id for corpus_id in referenced_ids if corpus_id not in mapping_by_id)
        if unknown:
            raise CorpusManifestError(
                f"case {case['case_id']} references unknown corpus IDs: {unknown}"
            )
        for corpus_id in sorted(referenced_ids):
            provenance = _case_provenance_for_corpus(case, corpus_id)
            if provenance is not None:
                expected_hash = str(provenance.get("source_sha256") or "")
                if expected_hash != str(mapping_by_id[corpus_id]["sha256"]):
                    raise CorpusManifestError(
                        f"case {case['case_id']} source_sha256 does not match {corpus_id}"
                    )
                active = active_by_id[corpus_id]
                if str(provenance.get("parser_version") or "") != str(
                    active.get("parser_version") or ""
                ):
                    raise CorpusManifestError(
                        f"case {case['case_id']} parser_version is stale for {corpus_id}"
                    )
                if str(provenance.get("chunker_version") or "") != str(
                    active.get("chunker_version") or ""
                ):
                    raise CorpusManifestError(
                        f"case {case['case_id']} chunker_version is stale for {corpus_id}"
                    )
        for anchor in case.get("evidence") or []:
            corpus_id = str(anchor["corpus_id"])
            active = active_by_id[corpus_id]
            page_count = int(active.get("page_count") or 0)
            for page in anchor.get("pages") or []:
                if int(page) > page_count:
                    raise CorpusManifestError(
                        f"case {case['case_id']} has page {page} beyond {corpus_id} page_count={page_count}"
                    )
            chunk_uids = [str(uid) for uid in anchor.get("chunk_uids") or []]
            if chunk_uids:
                missing_chunks = [uid for uid in chunk_uids if uid not in chunks_by_uid]
                if missing_chunks:
                    raise CorpusManifestError(
                        f"case {case['case_id']} has stale or missing chunk_uids: {missing_chunks}"
                    )
                wrong_scope = [
                    uid
                    for uid in chunk_uids
                    if int(chunks_by_uid[uid].get("paper_id") or 0)
                    != int(mapping_by_id[corpus_id]["paper_id"])
                ]
                if wrong_scope:
                    raise CorpusManifestError(
                        f"case {case['case_id']} qrel chunk scope mismatch: {wrong_scope}"
                    )
                expected_hashes = set(anchor.get("chunk_text_hashes") or [])
                if expected_hashes:
                    actual_hashes = {
                        str(chunks_by_uid[uid].get("text_hash") or "")
                        for uid in chunk_uids
                    }
                    if not expected_hashes.issubset(actual_hashes):
                        raise CorpusManifestError(
                            f"case {case['case_id']} qrel chunk text hash is stale"
                        )
                verified_chunk_qrels += len(chunk_uids)
        verified_cases.append(
            {
                "case_id": case["case_id"],
                "quality_status": case.get("quality_status") or "untracked",
                "answerable": bool(case["answerable"]),
                "evidence_count": len(case.get("evidence") or []),
            }
        )
    return {
        "schema_version": 1,
        "workspace_root": str(workspace.root),
        "cases_path": str(Path(cases_path).expanduser().resolve()),
        "case_count": len(cases),
        "tracked_case_count": sum(
            1 for case in cases if str(case.get("quality_status") or "").strip()
        ),
        "verified_chunk_qrel_count": verified_chunk_qrels,
        "cases": verified_cases,
    }


def _retrieval_breakdowns(result_cases: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Report quality by task/language instead of hiding failures in one mean."""

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for field in ("query_language", "document_language", "task_type", "quality_status"):
        buckets: dict[str, list[dict[str, Any]]] = {}
        for case in result_cases:
            value = str(case.get(field) or "unknown")
            buckets.setdefault(value, []).append(case)
        result[field] = {}
        for value, bucket in sorted(buckets.items()):
            answerable = [item for item in bucket if bool(item.get("answerable"))]
            first_ranks = [
                int(item["first_relevant_rank"])
                for item in answerable
                if item.get("first_relevant_rank") is not None
            ]
            complete = [
                bool(item["complete_evidence_at_k"])
                for item in answerable
                if item.get("complete_evidence_at_k") is not None
            ]
            result[field][value] = {
                "case_count": len(bucket),
                "answerable_case_count": len(answerable),
                "recall_at_k": (
                    round(len(first_ranks) / len(answerable), 6)
                    if answerable
                    else None
                ),
                "mrr_at_k": (
                    round(sum(1.0 / rank for rank in first_ranks) / len(answerable), 6)
                    if answerable
                    else None
                ),
                "evidence_complete_at_k": (
                    round(sum(1.0 if value else 0.0 for value in complete) / len(complete), 6)
                    if complete
                    else None
                ),
            }
    return result


def run_retrieval_evaluation(
    *,
    workspace_root: str | Path,
    cases_path: str | Path,
    limit: int = 10,
    with_dense: bool = False,
    with_rerank: bool = False,
    run_external: bool = False,
    max_external_cases: int = 50,
) -> dict[str, Any]:
    """Run deterministic sparse/hybrid retrieval metrics against Silver cases."""

    workspace = EvaluationWorkspace.from_root(workspace_root)
    mapping = _load_mapping(workspace)
    cases = load_retrieval_cases(cases_path)
    _require_external_opt_in(
        enabled=bool(with_dense or with_rerank),
        run_external=bool(run_external),
        purpose="dense/rerank retrieval evaluation",
    )
    external_limit = _bounded_positive(
        max_external_cases,
        name="max_external_cases",
        maximum=500,
    )
    if (with_dense or with_rerank) and len(cases) > external_limit:
        raise CorpusManifestError(
            "external retrieval selected "
            f"{len(cases)} cases, above max_external_cases={external_limit}"
        )
    validation = validate_retrieval_cases(
        workspace_root=workspace.root,
        cases_path=cases_path,
    )
    user_id = int(mapping["user_id"])
    paper_id_by_corpus = {str(item["corpus_id"]): int(item["paper_id"]) for item in mapping["papers"]}
    paper_by_id = {paper_id: corpus_id for corpus_id, paper_id in paper_id_by_corpus.items()}
    embedding = _embedding_indexer(workspace).provider if with_dense else None
    vector_store = (
        LanceDBVectorStore(str(workspace.vectors_root), dimension=int(embedding.dimension))
        if embedding is not None
        else None
    )
    reranker = None
    if with_rerank:
        candidate = DashScopeReranker()
        if not candidate.api_key or not candidate.endpoint:
            raise CorpusManifestError("rerank requested but provider is not configured")
        reranker = candidate
    retriever = HybridChunkRetriever(
        DocumentRepository(str(workspace.db_path)),
        vector_store=vector_store,
        embedding_provider=embedding,
        reranker=reranker,
    )
    result_cases: list[dict[str, Any]] = []
    reciprocal_ranks: list[float] = []
    recall_values: list[float] = []
    completeness_values: list[float] = []
    for case in cases:
        unknown = [item for item in case["corpus_ids"] if item not in paper_id_by_corpus]
        if unknown:
            raise CorpusManifestError(f"case {case['case_id']} references unknown corpus IDs: {unknown}")
        outcome = retriever.retrieve(
            user_id=user_id,
            paper_ids=list(paper_by_id),
            query=case["query"],
            limit=max(1, min(int(limit), 50)),
        )
        hit_rows = []
        for rank, hit in enumerate(outcome.hits, 1):
            matched_evidence = _matched_evidence_indices(hit, case, paper_by_id)
            hit_rows.append(
                {
                    "rank": rank,
                    "corpus_id": paper_by_id.get(int(hit.paper_id)),
                    "chunk_uid": hit.chunk_uid,
                    "page_start": hit.page_start,
                    "page_end": hit.page_end,
                    "section_path": list(hit.section_path),
                    "content_type": hit.content_type,
                    "rerank_score": hit.rerank_score,
                    "rrf_score": hit.rrf_score,
                    "relevant": bool(matched_evidence),
                    "matched_evidence_indices": matched_evidence,
                }
            )
        first_rank = next((row["rank"] for row in hit_rows if row["relevant"]), None)
        required_groups = set(case.get("required_evidence_groups") or [])
        matched_groups = {
            str((case.get("evidence") or [])[index].get("group") or "")
            for row in hit_rows
            for index in row["matched_evidence_indices"]
            if 0 <= index < len(case.get("evidence") or [])
        }
        complete_evidence = (
            required_groups.issubset(matched_groups) if required_groups else None
        )
        if case["answerable"]:
            recall_values.append(1.0 if first_rank is not None else 0.0)
            reciprocal_ranks.append(1.0 / first_rank if first_rank is not None else 0.0)
            if complete_evidence is not None:
                completeness_values.append(1.0 if complete_evidence else 0.0)
        result_cases.append(
            {
                "case_id": case["case_id"],
                "query": case["query"],
                "answerable": case["answerable"],
                "quality_status": case.get("quality_status") or "untracked",
                "query_language": case.get("query_language"),
                "document_language": case.get("document_language"),
                "task_type": case.get("task_type"),
                "first_relevant_rank": first_rank,
                "required_evidence_groups": sorted(required_groups),
                "matched_evidence_groups": sorted(group for group in matched_groups if group),
                "complete_evidence_at_k": complete_evidence,
                "degradation_reasons": list(outcome.degradation_reasons),
                "hits": hit_rows,
            }
        )
    answerable_count = len(recall_values)
    result = {
        "schema_version": 1,
        "created_at": int(time.time()),
        "cases_path": str(Path(cases_path).expanduser().resolve()),
        "limit": int(limit),
        "with_dense": bool(with_dense),
        "with_rerank": bool(with_rerank),
        "external": {
            "explicit_opt_in": bool(run_external),
            "max_external_cases": external_limit,
            "selected_case_count": len(cases),
        },
        "validation": validation,
        "answerable_case_count": answerable_count,
        "recall_at_k": round(sum(recall_values) / answerable_count, 6) if answerable_count else None,
        "mrr_at_k": round(sum(reciprocal_ranks) / answerable_count, 6) if answerable_count else None,
        "evidence_complete_case_count": len(completeness_values),
        "evidence_complete_at_k": (
            round(sum(completeness_values) / len(completeness_values), 6)
            if completeness_values
            else None
        ),
        "breakdowns": _retrieval_breakdowns(result_cases),
        "cases": result_cases,
    }
    mode = "hybrid" if with_dense else "sparse"
    if with_rerank:
        mode += "_rerank"
    _write_json(workspace.reports_root / f"retrieval_{mode}_latest.json", result)
    return result


def workspace_summary(workspace_root: str | Path) -> dict[str, Any]:
    workspace = EvaluationWorkspace.from_root(workspace_root)
    mapping = _load_mapping(workspace)
    repository = DocumentRepository(str(workspace.db_path))
    user_id = int(mapping["user_id"])
    rows: list[dict[str, Any]] = []
    for item in mapping["papers"]:
        paper_id = int(item["paper_id"])
        active = repository.get_active_version(user_id=user_id, paper_id=paper_id)
        rows.append(
            {
                "corpus_id": item["corpus_id"],
                "paper_id": paper_id,
                "active_version": active.get("id") if active else None,
                "status": active.get("status") if active else None,
                "page_count": int(active.get("page_count") or 0) if active else 0,
                "chunk_count": int(active.get("chunk_count") or 0) if active else 0,
                "embedding_status": active.get("embedding_status") if active else None,
            }
        )
    return {"workspace_root": str(workspace.root), "papers": rows}


__all__ = [
    "CorpusManifestError",
    "CorpusPaper",
    "EvaluationWorkspace",
    "load_corpus_manifest",
    "load_retrieval_cases",
    "prepare_workspace",
    "run_ingest",
    "run_retrieval_evaluation",
    "validate_retrieval_cases",
    "verify_corpus",
    "workspace_summary",
]
