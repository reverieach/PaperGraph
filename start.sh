#!/bin/bash
# PaperGraph(知脉) 一键启动：后端 + 前端

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# shellcheck source=ports.env
source "$ROOT/ports.env"

# The historical script used whatever ``python`` happened to be first on
# PATH.  That frequently selected the lightweight backend environment, which
# cannot run the canonical Docling/LanceDB pipeline.  Make the interpreter an
# explicit operator choice instead.
PAPERGRAPH_PYTHON="${PAPERGRAPH_PYTHON:-}"
if [ -z "$PAPERGRAPH_PYTHON" ]; then
    echo "PAPERGRAPH_PYTHON is required (point it at the full PaperGraph RAG Python)." >&2
    echo "Example: export PAPERGRAPH_PYTHON=/path/to/venv-rag/bin/python" >&2
    exit 2
fi
if [ ! -x "$PAPERGRAPH_PYTHON" ]; then
    echo "PAPERGRAPH_PYTHON is not executable: $PAPERGRAPH_PYTHON" >&2
    exit 2
fi

cleanup() {
    echo ""
    echo "正在关闭服务..."
    kill $BACKEND_PID $FRONTEND_PID ${WORKER_PID:-} 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID ${WORKER_PID:-} 2>/dev/null
    echo "已关闭。"
}
trap cleanup EXIT INT TERM

echo "============================================"
echo "  PaperGraph(知脉) 启动中..."
echo "============================================"

cd "$ROOT/backend"
echo "→ 后端 http://localhost:${BACKEND_PORT}"
export PORT="$BACKEND_PORT"
"$PAPERGRAPH_PYTHON" -m app.cli.preflight --strict-rag
PAPERGRAPH_UVICORN_RELOAD=0 "$PAPERGRAPH_PYTHON" run.py &
BACKEND_PID=$!

# The external worker is the normal owner of expensive PDF parsing/indexing.
# It is a separate process so API reloads or request cancellations do not
# abandon work.  Set PAPERGRAPH_START_INGEST_WORKER=0 only when an operator
# intentionally starts a dedicated worker elsewhere.
WORKER_PID=""
if [ "${PAPERGRAPH_START_INGEST_WORKER:-1}" != "0" ]; then
    echo "→ PDF Ingest Worker"
    "$PAPERGRAPH_PYTHON" -m app.workers.ingest_worker &
    WORKER_PID=$!
fi

cd "$ROOT/frontend"
export VITE_BACKEND_PORT="$BACKEND_PORT" VITE_DEV_PORT="$FRONTEND_PORT"
echo "→ 前端 http://127.0.0.1:${FRONTEND_PORT}"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "  前端: http://127.0.0.1:${FRONTEND_PORT}"
echo "  后端: http://127.0.0.1:${BACKEND_PORT}"
echo "  健康: http://127.0.0.1:${BACKEND_PORT}/health"
if [ -n "$WORKER_PID" ]; then
    echo "  PDF 入库: 独立 Worker 已启动"
else
    echo "  PDF 入库: 未由本脚本启动（PAPERGRAPH_START_INGEST_WORKER=0）"
fi
echo "  Ctrl+C 停止"
echo "============================================"

wait
