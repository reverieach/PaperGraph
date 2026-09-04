# PaperGraph repository instructions

本文件是仓库级开发与 AI Agent 执行约定。它描述当前真实环境、事实源、架构边界和验收命令。若代码、运行结果与其他文档冲突，以代码和可复现运行结果为准，并同步更新本文件及 `docs/CURRENT_STATE.md`。

## 1. 文档事实源

按以下优先级理解项目：

1. 真实代码、Migration、数据库 Schema 和实际测试输出；
2. [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md)：当前能力和已知缺口；
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)：当前调用链与模块边界；
4. [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)：环境、路径、配置与启动；
5. [`docs/TESTING_AND_ACCEPTANCE.md`](docs/TESTING_AND_ACCEPTANCE.md)：测试和验收；
6. [`docs/NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md`](docs/NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md)：下一阶段执行计划；
7. Phase 1 报告和 baseline：只作为带日期的历史证据，不表示当前状态；
8. `PLATFORM_VISION.md`：愿景，不表示已经实现。

不要根据旧 README、历史测试数量或文件名推断功能已经可用。

## 2. 当前权威运行环境

当前 Windows 开发机存在两个 Python 环境：

| 环境 | 路径 | 用途 |
| --- | --- | --- |
| 完整 RAG 环境 | `D:\AIModels\PaperGraph\venv-rag` | 当前后端、完整测试、Docling、LanceDB、Embedding/Rerank 和 GPU |
| 第一阶段轻量环境 | `backend\.venv` | 保留的轻量基础环境；不能用于完整 RAG 验收 |

当前阶段所有后端命令默认使用：

```powershell
$PaperGraphPython = 'D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe'
```

禁止使用裸 `python` 作为验收命令，因为 PATH 当前可能命中 Anaconda。不要把 RAG 依赖重复安装进 `backend/.venv`，除非后续明确决定合并环境。

完整环境已验证：

- Python 3.11.9；
- LanceDB 0.34.0；
- Docling 2.115.0；
- tiktoken 0.13.0；
- PyArrow 25.0.0；
- PyTorch 2.7.1+cu126；
- CUDA 可用，GPU 为 NVIDIA GeForce RTX 4060 Laptop GPU；
- `pip check` 通过。

`requirements-rag.txt` 与实际 OCR 依赖仍有差异：当前环境使用 `rapidocr==3.9.2` 和 `onnxruntime==1.28.0`，文件仍声明旧的 `rapidocr-onnxruntime`。在修改依赖前先阅读 `docs/ENVIRONMENT.md`。

## 3. 统一验证命令

后端：

```powershell
cd backend
$PaperGraphPython = 'D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe'
& $PaperGraphPython -m pip check
& $PaperGraphPython -m compileall -q app tests
& $PaperGraphPython -m pytest -q
```

当前完整回归基线：`141 passed, 1 warning`（2026-07-28，权威 `venv-rag`）。唯一已知 warning 是 Starlette TestClient/httpx 兼容性弃用提示。

前端：

```powershell
cd frontend
npm run typecheck
npm run build
```

当前 Node.js 24.11.0、npm 11.6.1；构建通过。Ant Design 已按功能域分包，最大业务 JavaScript chunk 实测约 360.20 kB，不再触发 Vite 的 500 kB 警告；PDF.js worker 仍是约 1.376 MB 的独立静态资源。

## 4. 数据和路径安全

默认路径：

| 数据 | 路径 |
| --- | --- |
| SQLite | `backend/data/papers.db` |
| PDF | `backend/downloads/papers` |
| Canonical artifacts | `backend/data/rag_artifacts` |
| LanceDB | `backend/data/rag_vectors` |
| RAG 测试语料 | `backend/data/rag_eval_corpus` |
| 隔离 PDF 评测库 | `backend/data/rag_eval_workspace/rag_eval.db` |
| 隔离 SciFact 评测库 | `backend/data/rag_eval_workspace/benchmarks/scifact_v1/scifact.db` |
| 后端配置 | `backend/.env` |

规则：

- 自动化测试必须使用 `tmp_path`、临时数据库或明确的 DB 副本；
- 不要把 `backend/data/papers.db` 当作测试 fixture；
- 修改真实 DB 前必须备份 DB/WAL/SHM；
- 不提交 `.env`、API Key、JWT、真实 PDF、模型缓存、LanceDB 数据或用户 Memory；
- SQLite 是业务事实源，FTS/LanceDB 是可重建投影；
- 不要删除或重写历史 Migration；
- 当前工作树中的 `backend/data/papers.db` 修改属于用户测试数据，不得混入文档或代码提交。

## 5. 架构不变量

- 认证用户和资源作用域由 API/Service/Repository 决定，LLM 不决定 `user_id` 或允许访问的 `paper_id`；
- 确定性 Workflow 控制 Ingest、检索、持久化和引用校验；
- Agent 只负责语义理解、有限决策和回答生成；
- Reader Agent 使用请求级实例，不在单例中积累对话状态；
- 永久 Memory 只通过“用户点击总结 → LLM 草稿 → 用户选择/编辑 → 确认提交”写入；
- Paper Memory 绑定 `user_id + paper_id`，User Memory 绑定 `user_id`；
- Memory、History 和 Web 不能冒充 PDF 页码证据；
- canonical Hybrid RAG 请求使用 request-scoped Evidence Registry/Citation Validator：只有实际进入 ContextPackage 的当前论文 chunk 可映射为 `[E#]` 引用；Memory、History、Tool/Web 不能冒充 PDF 证据；
- legacy PDF fallback 仍保留 `[pN]` 兼容解析，不能声明为 Evidence-grounded citation；canonical Reader tools 通过 `DocumentRepository` 受请求范围约束，并且只能经 `ContextPackage`/Evidence Registry 回流为可引用证据；旧工具与 fallback 尚待验收后删除；
- 新论文 RAG 只有存在 active document version 时才启用；当前业务 DB 尚无 document chunks；
- 多论文研究在选中文献存在 active canonical version 时使用 Hybrid Recall、anchor 均衡、Evidence Expansion、统一 `ContextPackage` 和 `[E#]` 校验；未入库论文仅作为明确标记的 metadata/abstract 背景。该路径已有隔离 SQLite 回归，仍缺业务论文、Frozen Golden 和浏览器 E2E 验收；
- GraphRAG、Neo4j、自由多 Agent 协作不属于当前阶段。

## 6. 修改边界

### 文档

- 当前事实变化时至少同步更新 `CURRENT_STATE.md`；
- 评测语料、Silver/Golden 或公开评分变化时同步更新 `EVALUATION_STATUS.md`；
- 环境、路径、命令变化时同步更新 `ENVIRONMENT.md` 和本文件；
- 架构或调用链变化时同步更新 `ARCHITECTURE.md`；
- 测试数量和验收阈值变化时同步更新 `TESTING_AND_ACCEPTANCE.md`；
- 下一阶段范围或状态变化时同步更新执行指南；
- 历史报告只添加勘误/指针，不把历史运行结果改写成新结果。

### 代码

- 使用 `apply_patch` 修改文本文件；
- 先检查 Git status，保留用户已有修改；
- Schema 变更只能通过版本化 Migration；
- 新功能必须有权限、降级、日志和测试；
- 宽泛异常捕获必须提供可观测的 degradation/error code，不能静默伪装成功；
- 新旧实现替换完成并通过 Golden/回归测试后，删除旧实现，不长期保留两套事实源。

## 7. 当前执行优先级

1. 对真实业务论文完成 canonical Ingest、Embedding 回填和 Reader 浏览器验收；
2. 已完成 Silver sparse 与受限 dense/rerank 对照，Golden Candidate 待用户审查；未经审核不得运行 Candidate；
3. 补齐 Frozen Golden、answer/citation、标准浏览器 PDF/SSE、故障注入与性能验收；
4. Frozen Golden/E2E 验收后删除旧 Reader/Memory 链路；
5. 对多论文 RAG 补齐业务论文、Frozen Golden 和浏览器引用交互验收。

详细步骤见下一阶段执行指南。
