#!/usr/bin/env python3
"""Run PaperGraph RAG evaluation only against an isolated workspace.

Examples (from ``backend`` using the authoritative interpreter):

    & $PaperGraphPython run_rag_eval.py prepare
    & $PaperGraphPython run_rag_eval.py ingest --parser-mode auto
    & $PaperGraphPython run_rag_eval.py retrieval --cases tests/golden/silver/retrieval_questions.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.evaluation.rag_eval import (
    prepare_workspace,
    run_ingest,
    run_retrieval_evaluation,
    validate_retrieval_cases,
    workspace_summary,
)
from app.services.evaluation.scifact import (
    prepare_scifact_subset,
    run_scifact_sparse_scorecard,
)


_BACKEND_ROOT = Path(__file__).resolve().parent
_DEFAULT_MANIFEST = _BACKEND_ROOT / "tests" / "golden" / "corpus_manifest.json"
_DEFAULT_CORPUS = _BACKEND_ROOT / "data" / "rag_eval_corpus"
_DEFAULT_WORKSPACE = _BACKEND_ROOT / "data" / "rag_eval_workspace"
_DEFAULT_SCIFACT_MANIFEST = _BACKEND_ROOT / "tests" / "golden" / "benchmarks" / "scifact_v1.json"
_DEFAULT_SCIFACT_ARCHIVE = _BACKEND_ROOT / "data" / "rag_eval_imports" / "beir_scifact" / "scifact.zip"
_DEFAULT_SCIFACT_WORKSPACE = _DEFAULT_WORKSPACE / "benchmarks" / "scifact_v1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaperGraph isolated RAG evaluation")
    parser.add_argument("--workspace", default=str(_DEFAULT_WORKSPACE))
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="verify PDFs and prepare isolated SQLite mapping")
    prepare.add_argument("--manifest", default=str(_DEFAULT_MANIFEST))
    prepare.add_argument("--corpus-root", default=str(_DEFAULT_CORPUS))

    ingest = sub.add_parser("ingest", help="ingest verified PDFs into the isolated workspace")
    ingest.add_argument("--parser-mode", choices=("auto", "standard", "fallback"), default="auto")
    ingest.add_argument("--with-embedding", action="store_true")
    ingest.add_argument(
        "--run-external",
        action="store_true",
        help="explicitly allow billable embedding API calls",
    )
    ingest.add_argument("--max-external-papers", type=int, default=8)
    ingest.add_argument("--corpus-id", action="append", default=[])

    validate = sub.add_parser("validate", help="validate retrieval qrels against canonical chunks")
    validate.add_argument(
        "--cases",
        default=str(_BACKEND_ROOT / "tests" / "golden" / "silver" / "retrieval_questions.jsonl"),
    )

    retrieval = sub.add_parser("retrieval", help="run deterministic retrieval metrics")
    retrieval.add_argument(
        "--cases",
        default=str(_BACKEND_ROOT / "tests" / "golden" / "silver" / "retrieval_questions.jsonl"),
    )
    retrieval.add_argument("--limit", type=int, default=10)
    retrieval.add_argument("--with-dense", action="store_true")
    retrieval.add_argument("--with-rerank", action="store_true")
    retrieval.add_argument(
        "--run-external",
        action="store_true",
        help="explicitly allow billable embedding/rerank API calls",
    )
    retrieval.add_argument("--max-external-cases", type=int, default=50)

    scifact_prepare = sub.add_parser(
        "scifact-prepare",
        help="prepare the isolated fixed SciFact public text-retrieval subset",
    )
    scifact_prepare.add_argument("--manifest", default=str(_DEFAULT_SCIFACT_MANIFEST))
    scifact_prepare.add_argument("--archive", default=str(_DEFAULT_SCIFACT_ARCHIVE))
    scifact_prepare.add_argument(
        "--benchmark-workspace", default=str(_DEFAULT_SCIFACT_WORKSPACE)
    )

    scifact_score = sub.add_parser(
        "scifact-score",
        help="score sparse retrieval on the isolated SciFact public text subset",
    )
    scifact_score.add_argument(
        "--benchmark-workspace", default=str(_DEFAULT_SCIFACT_WORKSPACE)
    )
    scifact_score.add_argument("--limit", type=int, default=10)
    sub.add_parser("summary", help="show isolated workspace ingest state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "prepare":
        _workspace, result = prepare_workspace(
            manifest_path=args.manifest,
            corpus_root=args.corpus_root,
            workspace_root=args.workspace,
        )
    elif args.command == "ingest":
        result = run_ingest(
            workspace_root=args.workspace,
            parser_mode=args.parser_mode,
            with_embedding=bool(args.with_embedding),
            run_external=bool(args.run_external),
            max_external_papers=int(args.max_external_papers),
            corpus_ids=args.corpus_id,
        )
    elif args.command == "validate":
        result = validate_retrieval_cases(
            workspace_root=args.workspace,
            cases_path=args.cases,
        )
    elif args.command == "retrieval":
        result = run_retrieval_evaluation(
            workspace_root=args.workspace,
            cases_path=args.cases,
            limit=args.limit,
            with_dense=bool(args.with_dense),
            with_rerank=bool(args.with_rerank),
            run_external=bool(args.run_external),
            max_external_cases=int(args.max_external_cases),
        )
    elif args.command == "scifact-prepare":
        result = prepare_scifact_subset(
            manifest_path=args.manifest,
            archive_path=args.archive,
            workspace_root=args.benchmark_workspace,
        )
    elif args.command == "scifact-score":
        result = run_scifact_sparse_scorecard(
            workspace_root=args.benchmark_workspace,
            limit=args.limit,
        )
    else:
        result = workspace_summary(args.workspace)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
