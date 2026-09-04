"""Safely enqueue historical local PDFs for canonical document ingestion."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Sequence

from ..core.storage import PaperDatabase
from ..infrastructure.db import Database, run_migrations, validate_schema
from ..repositories.document_repository import DocumentRepository
from ..services.ingest.parsers import file_sha256
from ..services.ingest.queue import IngestEnqueueError, enqueue_owned_paper_ingest
from ..settings import configure_logging, get_settings, validate_config

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Queue historical PaperGraph PDFs for canonical ingest")
    parser.add_argument("--db-path", default="", help="SQLite database path (defaults to DATA_DIR/papers.db)")
    owner = parser.add_mutually_exclusive_group(required=True)
    owner.add_argument("--user-id", type=int, help="Only inspect one user's papers")
    owner.add_argument("--all-users", action="store_true", help="Inspect all owners (requires --execute)")
    parser.add_argument("--paper-id", type=int, help="Only inspect one paper")
    parser.add_argument("--limit", type=int, default=25, help="Maximum candidate papers to inspect")
    parser.add_argument("--resume-after-paper-id", type=int, default=0, help="Continue after this paper ID")
    parser.add_argument("--parser-mode", choices=("standard", "fallback", "auto"), default="standard")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually create durable ingest jobs; default is a read-only dry run",
    )
    return parser


def _resolve_db_path(raw: str) -> str:
    if str(raw or "").strip():
        return str(Path(raw).expanduser().resolve())
    return str(Path(get_settings().data_dir).expanduser().resolve() / "papers.db")


def _candidate_rows(
    db_path: str,
    *,
    user_id: int | None,
    paper_id: int | None,
    resume_after_paper_id: int,
    limit: int,
) -> list[dict[str, int]]:
    clauses = ["p.id > ?"]
    params: list[Any] = [max(0, int(resume_after_paper_id))]
    if user_id is not None:
        clauses.append("p.user_id = ?")
        params.append(int(user_id))
    if paper_id is not None:
        clauses.append("p.id = ?")
        params.append(int(paper_id))
    params.append(max(1, min(int(limit), 1000)))
    with Database(db_path).read() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.user_id
            FROM papers p
            WHERE {' AND '.join(clauses)}
            ORDER BY p.id ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [{"paper_id": int(row["id"]), "user_id": int(row["user_id"])} for row in rows]


def run_backfill(
    *,
    db_path: str,
    user_id: int | None,
    paper_id: int | None,
    resume_after_paper_id: int,
    limit: int,
    parser_mode: str,
    execute: bool,
) -> dict[str, Any]:
    """Inspect candidate PDFs and optionally enqueue only missing versions."""

    database = PaperDatabase(db_path)
    repository = DocumentRepository(db_path)
    summary: dict[str, Any] = {
        "mode": "execute" if execute else "dry_run",
        "scanned": 0,
        "eligible": 0,
        "skipped_no_local_pdf": 0,
        "skipped_active_version": 0,
        "would_enqueue": [],
        "enqueued": [],
        "failed": [],
        "last_paper_id": int(resume_after_paper_id or 0),
    }
    for row in _candidate_rows(
        db_path,
        user_id=user_id,
        paper_id=paper_id,
        resume_after_paper_id=resume_after_paper_id,
        limit=limit,
    ):
        owner_id = row["user_id"]
        candidate_paper_id = row["paper_id"]
        summary["scanned"] += 1
        summary["last_paper_id"] = candidate_paper_id
        pdf_path = database.get_library_pdf_abspath(candidate_paper_id, user_id=owner_id)
        if not pdf_path:
            summary["skipped_no_local_pdf"] += 1
            continue
        try:
            file_hash, _ = file_sha256(pdf_path)
        except OSError as exc:
            summary["failed"].append({"paper_id": candidate_paper_id, "error": str(exc)})
            continue
        active = repository.get_active_version(user_id=owner_id, paper_id=candidate_paper_id)
        if active and str(active.get("file_hash") or "") == file_hash:
            summary["skipped_active_version"] += 1
            continue
        summary["eligible"] += 1
        if not execute:
            summary["would_enqueue"].append({"paper_id": candidate_paper_id, "user_id": owner_id})
            continue
        try:
            result = enqueue_owned_paper_ingest(
                db=database,
                db_path=db_path,
                user_id=owner_id,
                paper_id=candidate_paper_id,
                parser_mode=parser_mode,
            )
            summary["enqueued"].append(result.to_dict())
        except IngestEnqueueError as exc:
            logger.warning(
                "ingest_backfill_enqueue_failed",
                extra={"paper_id": candidate_paper_id, "user_id": owner_id},
                exc_info=True,
            )
            summary["failed"].append({"paper_id": candidate_paper_id, "error": str(exc)})
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.all_users and not args.execute:
        print(json.dumps({"ok": False, "errors": ["--all-users requires --execute"]}, ensure_ascii=False))
        return 2
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        validate_config()
        db_path = _resolve_db_path(args.db_path)
        if args.execute:
            run_migrations(db_path)
        else:
            # A dry run must not apply a migration, create a database, or
            # change a journal mode.  It only validates an already initialized
            # schema through a SQLite read-only connection.
            validate_schema(db_path, read_only=True)
        summary = run_backfill(
            db_path=db_path,
            user_id=args.user_id,
            paper_id=args.paper_id,
            resume_after_paper_id=args.resume_after_paper_id,
            limit=args.limit,
            parser_mode=args.parser_mode,
            execute=args.execute,
        )
    except Exception as exc:
        logger.exception("ingest_backfill_failed")
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": not summary["failed"], "summary": summary}, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
