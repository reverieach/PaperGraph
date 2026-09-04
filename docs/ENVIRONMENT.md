# PaperGraph 环境、配置与路径

文档状态：`CURRENT`
核验日期：2026-07-28
当前平台：Windows

## 1. 项目路径

当前仓库：

```text
D:\面试学习\papergraph\PaperGraph
```

文档和代码不得依赖该绝对路径；仓库内引用使用相对路径。绝对路径只用于当前开发机的启动说明。

## 2. Python 环境

### 完整 RAG 环境

```text
D:\AIModels\PaperGraph\venv-rag
```

解释器：

```text
D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe
```

这是当前阶段的权威后端环境。实测：

| 组件 | 版本/状态 |
| --- | --- |
| Python | 3.11.9 |
| SQLite | 3.45.1 |
| FastAPI | 0.140.4 |
| pytest | 9.1.1 |
| LanceDB | 0.34.0 |
| Docling | 2.115.0 |
| Docling Core | 2.88.0 |
| tiktoken | 0.13.0 |
| PyArrow | 25.0.0 |
| PyTorch | 2.7.1+cu126 |
| torchvision | 0.22.1+cu126 |
| RapidOCR | 3.9.2 |
| ONNX Runtime | 1.28.0 |
| CUDA | 可用 |
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU |
| 环境大小 | 约 5.95 GB |

`pip check` 通过。

### 第一阶段轻量环境

```text
backend\.venv
```

它同样是 Python 3.11.9，包含基础后端和测试依赖，但不包含：

- LanceDB；
- Docling；
- tiktoken；
- PyArrow；
- PyTorch。

它只保留为第一阶段轻量环境，不能用于完整 RAG 测试、Ingest 或 Dense Reader。

### 机器上的其他 Python

PATH 当前可能命中：

- `E:\ANACONDA\python.exe`
- 全局 Python 3.12
- WindowsApps Python

因此所有项目命令必须显式指定解释器，不使用裸 `python` 作为验收依据。

## 3. 后端启动

PowerShell：

```powershell
cd D:\面试学习\papergraph\PaperGraph\backend
$PaperGraphPython = 'D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe'
& $PaperGraphPython -m app.cli.preflight --strict-rag
& $PaperGraphPython run.py
```

默认：

- Backend：`http://127.0.0.1:8000`
- Health：`http://127.0.0.1:8000/health`
- RAG capability：`http://127.0.0.1:8000/health/capabilities`

`start.sh` 现要求显式设置 `PAPERGRAPH_PYTHON`，并在启动 API 前执行 strict RAG preflight；默认会额外拉起一个独立 Ingest Worker 进程。若运维方已在别处启动 Worker，可设置 `PAPERGRAPH_START_INGEST_WORKER=0` 禁用该行为。Windows 当前仍以以上 PowerShell 命令为权威启动方式。

## 4. 前端启动

```powershell
cd D:\面试学习\papergraph\PaperGraph\frontend
npm ci
npm run dev
```

默认：

- Frontend：`http://127.0.0.1:5173`
- Vite 通过同源代理访问 `/api`。

已验证版本：

- Node.js 24.11.0；
- npm 11.6.1。

导出前后端共享的 OpenAPI 类型时，也必须显式使用同一个完整 RAG 解释器，避免历史
`backend/.venv` 或系统 Python 生成错误的接口定义：

```powershell
cd D:\面试学习\papergraph\PaperGraph\frontend
$env:PAPERGRAPH_PYTHON = 'D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe'
npm run openapi:gen
```

`npm run openapi:gen` 不会再回退到裸 `python` 或 `backend/.venv`；未设置
`PAPERGRAPH_PYTHON` 会明确失败。

## 5. 配置文件

后端总是从：

```text
backend/.env
```

加载配置，不依赖当前工作目录。

创建方式：

```powershell
Copy-Item backend\.env.example backend\.env
```

不要覆盖已经配置好的 `.env`。不要把它提交到 Git。

### 必需配置

```env
PAPERGRAPH_JWT_SECRET=<至少 32 字符的随机私密字符串>
LLM_API_KEY=<chat model key>
LLM_BASE_URL=<OpenAI compatible base URL>
LLM_MODEL_ID=<model id>
```

JWT Secret 用于签名和验证登录 Token。它不是用户密码；更换后旧 Token 会失效。

### RAG/模型配置

```env
EMBED_MODEL_NAME=text-embedding-v4
EMBED_API_KEY=<embedding key>
EMBED_BASE_URL=<workspace compatible-mode/v1>
EMBED_DIMENSION=1024

RERANK_MODEL_NAME=qwen3-rerank
RERANK_API_KEY=<rerank key>
RERANK_ENDPOINT=<workspace compatible-api/v1/reranks>

RAG_EMBEDDING_ENABLED=true
RAG_RERANK_ENABLED=true
RAG_INGEST_WORKER_ENABLED=false
RAG_DEVICE=auto
RAG_DOCLING_OCR_MODE=auto
```

当前 `.env` 已配置 Embedding 和 Rerank 凭据，但不在文档中记录具体 Key。`RAG_INGEST_WORKER_ENABLED=true` 只用于单进程本地开发，默认 `false` 表示应启动独立 Worker。`RAG_DOCLING_OCR_MODE=auto` 对有足够原生文本层的 PDF 跳过 OCR；扫描件或文本层不可用时仍启用 OCR。可选值为 `auto`、`always`、`never`。

### 可选外部搜索

- `TAVILY_API_KEY`
- `OPENALEX_MAILTO`
- `NCBI_EMAIL`
- `NCBI_API_KEY`
- MCP arXiv 相关配置

缺少这些配置时必须明确降级，不能伪造数据源成功。

## 6. 数据路径

默认相对后端根目录：

| 内容 | 默认路径 | 当前状态 |
| --- | --- | --- |
| SQLite | `backend/data/papers.db` | 存在 |
| PDF | `backend/downloads/papers` | 存在 |
| Canonical artifact | `backend/data/rag_artifacts` | 尚未创建 |
| LanceDB | `backend/data/rag_vectors` | 尚未创建 |
| RAG corpus | `backend/data/rag_eval_corpus` | 存在 |
| 隔离 PDF eval workspace | `backend/data/rag_eval_workspace/rag_eval.db` | 已有 16 篇/419 页公开 PDF 的 canonical/FTS/vector 数据；不得替代业务库 |
| 隔离 SciFact workspace | `backend/data/rag_eval_workspace/benchmarks/scifact_v1/scifact.db` | 已有固定公开文本检索子集；不含 PDF 页码能力 |
| Docling 模型缓存 | `RAG_DOCLING_ARTIFACTS_PATH` 或 Docling 默认缓存 | 可配置 |

设置 `DATA_DIR` 后，数据库、默认 artifact 和 vector 路径随新的 data directory 解析。设置绝对 `RAG_ARTIFACTS_DIR`/`RAG_VECTORS_DIR` 可以覆盖。

评测工作区的命令、Hash、Silver/Golden 边界和 SciFact 评分卡见 [EVALUATION_STATUS.md](./EVALUATION_STATUS.md)。它们只使用隔离数据库，任何评测命令都不得指向 `backend/data/papers.db`。

## 7. Ingest Worker

PDF 保存与重型解析现在严格分离：保存服务在本地 PDF 落盘后幂等创建 `ingest_jobs`，API 只负责排队；Docling、Chunk、Embedding 与 LanceDB 仅由 Worker 消费。

先在一个终端启动 API，再在另一个终端启动 Worker：

```powershell
cd D:\面试学习\papergraph\PaperGraph\backend
$PaperGraphPython = 'D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe'
& $PaperGraphPython -m app.workers.ingest_worker
```

一次性 smoke 可使用：

```powershell
& $PaperGraphPython -m app.workers.ingest_worker --once
```

Worker 使用 SQLite lease、heartbeat、有限延迟重试和重启后过期 lease 恢复。`POST /api/papers/{paper_id}/ingest` 是幂等手动重试入口；`GET /api/papers/{paper_id}/ingest` 返回 user-scoped 状态，Reader 会轮询并显示状态。当前仍是单机 SQLite Worker，不应称为分布式任务系统。

历史论文回填默认 dry-run：它以 SQLite 只读连接校验既有 schema，不创建 Job、
不应用 Migration，也不会创建不存在的数据库；只有显式传入 `--execute` 才会进入写入路径：

```powershell
# 只列出会入队的候选，默认 dry-run
& $PaperGraphPython -m app.cli.backfill_ingest --user-id <USER_ID> --limit 25

# 确认后才创建队列任务；Worker 会异步处理
& $PaperGraphPython -m app.cli.backfill_ingest --user-id <USER_ID> --limit 25 --execute
```

它只处理该用户存在可读本地 PDF、且没有相同 file hash active version 的论文。支持 `--paper-id`、`--resume-after-paper-id` 和 `--parser-mode`；`--all-users` 必须同时显式指定 `--execute`。

## 8. 依赖可复现性缺口

`backend/requirements-rag.txt` 仍声明：

```text
rapidocr-onnxruntime>=1.3,<2
```

实际成功环境使用：

```text
rapidocr==3.9.2
onnxruntime==1.28.0
```

PyTorch/CUDA 也没有被 requirements 文件锁定。因此当前环境可运行，但尚不能只靠 requirements 文件完全重建。下一阶段需要生成 constraints/lock，并为 CPU/CUDA 说明安装变体。

## 9. Docker 状态

仓库包含 `docker-compose.yml`，但当前阶段没有安装/验证 Docker，RAG GPU、Docling 模型缓存和外置向量目录也没有完成容器验收。

Docker 是 `EXPERIMENTAL`，不是当前推荐路径。详见 [README-Docker.md](../README-Docker.md)。

## 10. 已知本机 Shell 提示

当前 PowerShell Profile 会在每条命令结束时出现一次空 Conda 命令错误。它没有改变上述测试退出结果，但会制造噪声。该问题属于用户 Shell 配置，不属于 PaperGraph 代码；不要误判为项目测试失败。
