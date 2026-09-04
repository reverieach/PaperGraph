"""Validate the selected Python environment before starting PaperGraph."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from ..core.runtime_capabilities import collect_runtime_capabilities
from ..infrastructure.db import run_migrations
from ..settings import configure_logging, get_settings, validate_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run PaperGraph environment/RAG preflight checks")
    parser.add_argument("--db-path", default="", help="Optional DB to migrate and schema-validate")
    parser.add_argument(
        "--check-migrations",
        action="store_true",
        help="Apply and validate migrations for --db-path (or DATA_DIR/papers.db)",
    )
    parser.add_argument(
        "--strict-rag",
        action="store_true",
        help="Fail when required RAG packages or enabled provider configuration are unavailable",
    )
    return parser


def _db_path(raw: str) -> str:
    if str(raw or "").strip():
        return str(Path(raw).expanduser().resolve())
    return str(Path(get_settings().data_dir).expanduser().resolve() / "papers.db")


def _strict_errors(capabilities: dict) -> list[str]:
    errors: list[str] = []
    sqlite = capabilities["sqlite"]
    if not sqlite["fts5"]:
        errors.append("SQLite FTS5 is unavailable")
    for package in ("docling", "lancedb", "tiktoken", "pyarrow", "rapidocr", "onnxruntime"):
        if not capabilities["packages"][package]["available"]:
            errors.append(f"required RAG package unavailable: {package}")
    embedding = capabilities["embedding"]
    if embedding["enabled"] and not embedding["configured"]:
        errors.append("embedding is enabled but EMBED_API_KEY/EMBED_BASE_URL are not configured")
    rerank = capabilities["rerank"]
    if rerank["enabled"] and not rerank["configured"]:
        errors.append("rerank is enabled but RERANK_API_KEY/RERANK_ENDPOINT are not configured")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        validate_config()
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2

    capabilities = collect_runtime_capabilities()
    errors = _strict_errors(capabilities) if args.strict_rag else []
    if args.check_migrations:
        try:
            run_migrations(_db_path(args.db_path))
        except Exception as exc:
            errors.append(f"migration/schema check failed: {exc}")
    print(
        json.dumps(
            {"ok": not errors, "errors": errors, "capabilities": capabilities},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
