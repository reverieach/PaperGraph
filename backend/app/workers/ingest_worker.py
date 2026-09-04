"""CLI process entry point for the persisted PDF-ingest worker.

Run from ``backend`` with the authoritative RAG interpreter:

    & $PaperGraphPython -m app.workers.ingest_worker --once
    & $PaperGraphPython -m app.workers.ingest_worker
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import uuid
from pathlib import Path
from typing import Sequence

from ..infrastructure.db import run_migrations
from ..services.ingest.factory import build_ingest_worker
from ..settings import configure_logging, get_settings, validate_config

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PaperGraph's persisted PDF ingest worker")
    parser.add_argument("--db-path", default="", help="SQLite database path (defaults to DATA_DIR/papers.db)")
    parser.add_argument("--once", action="store_true", help="Claim and process at most one ready job, then exit")
    parser.add_argument("--poll-seconds", type=float, default=2.0, help="Idle poll interval for continuous mode")
    parser.add_argument("--worker-id", default="", help="Stable worker identifier for lease diagnostics")
    return parser


def _resolve_db_path(raw: str) -> str:
    if str(raw or "").strip():
        return str(Path(raw).expanduser().resolve())
    return str(Path(get_settings().data_dir).expanduser().resolve() / "papers.db")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        validate_config()
        db_path = _resolve_db_path(args.db_path)
        run_migrations(db_path)
        worker = build_ingest_worker(db_path)
    except Exception:
        logger.exception("ingest_worker_startup_failed")
        return 2

    worker_id = str(args.worker_id or f"ingest-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    if args.once:
        try:
            report = worker.run_once(worker_id=worker_id)
        except Exception:
            logger.exception("ingest_worker_once_failed", extra={"worker_id": worker_id})
            return 1
        print(json.dumps({"worker_id": worker_id, "report": report.to_dict() if report else None}, ensure_ascii=False))
        return 0

    stop_event = threading.Event()

    def _stop(*_unused: object) -> None:
        logger.info("ingest_worker_shutdown_requested", extra={"worker_id": worker_id})
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        candidate = getattr(signal, signal_name, None)
        if candidate is not None:
            signal.signal(candidate, _stop)
    logger.info("ingest_worker_started", extra={"worker_id": worker_id, "db_path": db_path})
    worker.run_forever(
        worker_id=worker_id,
        stop_event=stop_event,
        poll_seconds=max(0.1, float(args.poll_seconds)),
    )
    logger.info("ingest_worker_stopped", extra={"worker_id": worker_id})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
