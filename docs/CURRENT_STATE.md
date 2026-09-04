# PaperGraph 当前状态

文档状态：`CURRENT`
核验日期：2026-07-28
代码基线：当前工作树（第二阶段 Gate 0/WP1-WP11 基础实现、Silver v2、Reader trace 与多论文 canonical regression）
分支：`codex/phase1-foundation`

## 1. 当前定位

PaperGraph 是一个本地优先的学术文献搜索、管理、PDF 阅读和研究辅助应用。它已经具备可运行的多用户认证基础、论文所有权隔离、文献库、PDF Reader、手动确认式 Memory、外部论文多源搜索和知识图谱界面；论文级 RAG 的 canonical 数据模型、解析、Chunk、FTS、Embedding/LanceDB、Hybrid/Rerank 和 Context Builder 已实现，并已在隔离公开 PDF 语料中完成基础入库/检索验证，但尚未形成用户业务库和 Reader 端到端产品闭环。

当前准确定位是：

> 可运行的学术 AI 应用原型，正在从“功能可用”进入“证据可追溯、效果可评测、链路可恢复”的工程收口阶段。

它不是生产级多租户 SaaS，也不是 GraphRAG 或自由多 Agent 系统。

## 2. 最近验证结果

### 后端

使用完整 RAG 环境：

```text
D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe
```

结果：

```text
pytest:     141 passed, 1 warning
pip check:  No broken requirements found
compileall: passed
```

warning 是 Starlette TestClient/httpx 适配弃用提示，不阻塞当前功能。

全仓 `mypy app` 当前仍报告 `140 errors in 41 files`；本轮改动涉及的 8 个 RAG/Context/Agent 模块以 `--follow-imports=skip` 单独检查已通过。全仓类型债务是后续工程化工作，不应被误写成当前类型检查全绿。

### 前端

```text
Node.js: 24.11.0
npm:     11.6.1
typecheck/build: passed
```

Ant Design 已按功能域分包，最大业务 JavaScript chunk 约 360.20 kB，Vite 的 500 kB chunk 告警已消失。PDF.js worker 仍是约 1.376 MB 的独立静态资源，后续按缓存策略优化。

## 3. 当前数据库和文件状态

2026-07-28 对 `backend/data/papers.db` 只读盘点：

| 数据 | 数量 |
| --- | ---: |
| papers | 11 |
| memories | 4 |
| memory_drafts | 6 |
| reader_conversations | 2 |
| paper_reader_turns | 18 |
| document_versions | 0 |
| document_pages | 0 |
| document_blocks | 0 |
| document_chunks | 0 |
| ingest_jobs | 0 |

`PRAGMA foreign_key_check` 为 0 条异常。

隔离评测工作区 `backend/data/rag_eval_workspace/rag_eval.db` 已有 16 篇、419 页公开 PDF、16 个 active canonical document version、3,217 个 active canonical chunks（1,102 parent / 2,115 child）。其来源、Hash 与页数由 `backend/tests/golden/corpus_manifest.json` 固定。它不与 `backend/data/papers.db` 混用；后者仍没有 canonical document 表数据。

当前没有：

- `backend/data/rag_artifacts`；
- `backend/data/rag_vectors`。

这意味着代码和 RAG 环境已经存在，但当前业务数据仍未完成 canonical Ingest 和向量索引。

## 4. 能力状态

| 模块 | 状态 | 当前事实 |
| --- | --- | --- |
| 登录/注册/JWT | 已验证 | Bearer JWT；Secret 至少 32 字符；隔离浏览器已验证注册后跳转 `/search` |
| 用户资源隔离 | 基础已完成 | Paper/PDF/Reader History/Memory/Export 按 user_id |
| 文献库 | 已验证 | 保存、列表、分类、更新、删除、PDF 下载 |
| PDF Viewer | 构建/静态资源已验证，标准浏览器待终验 | PDF.js 独立 Worker 已正确打包；Vite Preview 的 `.mjs` 响应已核验，Nginx 显式 MIME 已配置。内置测试浏览器没有 Web Worker，不能用于 canvas 结论 |
| Reader 对话 | 隔离产品 E2E 已验证 | 请求级 Agent、历史持久化、旧 PDF fallback；临时库真实 `save → job → worker → canonical RAG → Evidence Registry → LLM [E#] citation` 已通过；业务论文仍未验证 |
| Memory 写入 | 已验证 | 用户点击总结、草稿、选择/编辑、幂等确认、软删除 |
| Memory 召回 | 部分完成 | v011 后使用严格 user/paper scope、confirmed/active/expiry hard filter、unicode61/trigram FTS、lexical relevance gate 与 paper/user 低配额；未接入 dense/vector 或 Golden 校准 |
| 长期 Memory UI | 基础浏览器 smoke 已验证 | 可查看、添加和删除用户 Memory；隔离浏览器已验证手动添加后回显，尚未覆盖 Reader 摘要草稿到确认保存的完整 UI 流程 |
| 外部论文搜索 | 已实现 | arXiv/OpenAlex/DBLP/Tavily 等，规则/LLM 排序和 SSE |
| 搜索质量评测 | 未完成 | 无固定 search qrels/A-B 指标 |
| Canonical PDF 模型 | 已实现 | version/page/block/chunk/job schema |
| PDF Parser | 已在隔离 corpus 验证 | Docling 主路径、PyMuPDF fallback、Quality Gate；16 篇公开 PDF 已真实 canonical 入库；`auto` OCR 通过有界文本层预检避免对原生文本 PDF 无效 OCR，并在视觉核验的 image-only fixture 上实际抽出文本 |
| 层级 Chunk | 已验证 | section/page-aware parent/child；隔离语料产生 3,217 active chunks；新表格使用 `parent-child-v3`，caption/header 随 row split 重复且表格不再和前文合并 |
| FTS5/BM25 | 已有真实小语料诊断 | v010 `unicode61` + optional `trigram` 双投影、确定性 QueryPlan；Silver v2 sparse 与受限 dense/rerank 对照已运行 |
| LanceDB/Embedding | 环境和代码可用，隔离评测已真实回填 | 已拆分 `embed_documents`/`embed_query`，document instruction 由 config hash 防止旧向量混用；16 篇隔离 PDF 已产生向量，业务库仍无实际向量数据 |
| Hybrid/Rerank | Silver v2 已验证 | Weighted RRF + task-aware qwen3-rerank、bounded candidate rerank、表格/摘要 source preference 和 Evidence expansion；Hybrid+Rerank 在 24 Silver case 达到 `Recall@10=1.0/MRR@10=0.920290`，尚未 Golden 校准 |
| Dynamic Context | 已完成基础接入 | `ContextPackage` 是 Reader 的唯一上下文装配点；QueryPlan task policy、tiktoken 预算、跨源去重、服务端 History、dropped trace 和 canonical tool re-entry 已接入；旧 fallback 仍待收口 |
| 引用 | canonical 路径已可靠 | canonical Hybrid RAG 用 request-scoped Evidence Registry 校验 `[E#]`，返回的 snippet/page 来自 chunk；legacy fallback 仍有 `[pN]` 兼容解析，不能视为可靠证据引用 |
| 多论文研究 | canonical RAG 基础已实现并有隔离回归 | session 仅检索当前用户选中的论文；多论文 Hybrid Recall → anchor 均衡 → Evidence Expansion → 统一 ContextPackage → `[E#]` 校验；未入库论文明确降级为摘要背景，尚无业务论文/Golden/浏览器 E2E |
| 知识图谱 | 可展示的基础能力 | 已迁移关系表并增加用户/论文复合约束；仍有扩展性问题，不是 GraphRAG |
| Ingest 产品闭环 | 隔离产品 E2E 已验证 | 本地 PDF 保存后幂等 enqueue；API 不跑重型任务；真实隔离 PDF 已完成下载、Job、Worker 和 canonical 激活；损坏/加密 PDF 给出明确错误码 |
| 独立 Ingest Worker | 隔离产品 E2E 已验证 | 独立 CLI、lease/heartbeat、有限重试、默认 dry-run 回填；不可解析/加密等永久输入错误不会浪费重试；仍是单机 SQLite |
| 评测集 | 部分完成 | Silver v2 24 例/26 锚点已验证，sparse 与受限 dense/rerank 对照已运行；Golden Candidate 10 例已校验、待用户审查；隔离 Reader LLM/Evidence smoke 已通过，但尚无 Frozen Golden 或 answer/citation 门禁 |
| Docker | 未在当前阶段验收 | 配置存在，不是当前推荐开发路径 |

## 5. 当前真实 RAG 行为

Reader 收到问题后：

1. 服务端按 `user_id + paper_id + conversation_id` 读取已持久化 History；客户端 `messages` 不再进入 Prompt；
2. `MemoryRetriever` 读取当前用户的 confirmed/active paper/user Memory，并用 scope、过期、FTS/lexical relevance 与配额过滤；
3. 只有论文存在 active document version 时才尝试 Hybrid RAG；
4. Hybrid RAG 先生成确定性 QueryPlan，再使用 unicode61、CJK trigram、可选 Dense LanceDB、Weighted RRF 与任务化 Rerank；
5. `DynamicContextBuilder` 生成单一、Token-budgeted `ContextPackage`，区分 Evidence/Memory/History/tool/legacy source；
6. canonical 路径为本轮实际进入 Prompt 的 chunk 建立 `EvidenceRegistry`；LLM 只可使用 `[E#]`，Validator 返回 canonical chunk 的 snippet/page；
7. 无 active version 或检索故障才降级为旧 PDF 摘录；该路径不产生可靠 Evidence citation。

由于当前数据库 `document_versions=0`，现有论文实际不会进入新 Hybrid RAG，仍使用旧上下文路径。

## 6. 当前 Memory 行为

正式写入：

```text
阅读对话
→ 用户点击“总结本次阅读”
→ LLM 生成 MemoryDraft
→ 用户选择/编辑 paper 或 user memory
→ 用户确认
→ MemoryRepository 持久化
```

作用域：

- Paper Memory：`user_id + scope_type=paper + scope_id=paper_id`；
- User Memory：`user_id + scope_type=user + scope_id=user_id`。

当前读取侧：

- `MemoryRetriever` 先执行用户、当前论文、active、confirmed、expiry 的硬过滤，再用 unicode61/trigram FTS 和确定性 lexical overlap 召回；检索结果不会作为 PDF Evidence 或指令来源；
- 问题为空时只回退少量当前论文 Memory，不自动注入长期 User Memory；问题存在时 Paper/User Memory 分别以低配额（默认 3+2）返回；
- `importance`、`expires_at`、`superseded_by` 已进入 v011 schema；Repository 可更新检索策略或 supersede，且 expired/unconfirmed/superseded 不参与检索；
- 交给 Context Builder 的 Memory 带 `citation_allowed=false`，不会进入 PDF Evidence registry。

已知缺口：

- 未接入 Memory Embedding/LanceDB 或 rerank；当前策略故意优先保证小规模 Memory 的范围正确和可解释性；
- `importance`/TTL/supersede 还未暴露为完整用户管理 UI/API；
- FTS/lexical 阈值、配额与跨语言实际效果仍没有 Golden 校准；
- `negative_feedback_memory.py` 现在是 user-scoped、TTL 的确定性 skip signal；它不属于正式 Memory，不会被自动晋升或注入 Reader 上下文。

## 7. 当前 Agent 结构

项目有三个命名 Agent：

- `SearchAgent`：搜索意图与搜索对话辅助；
- `PaperAnalysisAgent`：论文分类、Reader 回答和旧 Reader tools；
- `KnowledgeGraphAgent`：知识图谱相关 LLM 能力。

当前不是 ReAct 框架主导的自由多 Agent 系统。真实流程主要由 API、Service、Repository 和确定性 Retrieval Pipeline 控制。Reader 的工具循环支持 function calling、最多 5 轮、请求级 deadline/输出限额与失败降级；线程池中已经开始的阻塞底层调用仍不能被 Python 可靠强杀，因此不能把等待超时表述为任务已终止。

## 8. 最严重的当前问题

### 阶段门禁

1. 当前业务数据库无 active document version，新 RAG 尚未服务真实用户论文；
2. 隔离公开 PDF 已完成 Docling/FTS/Embedding 入库和有上限的 Silver v2 Embedding/Rerank 对照；隔离产品流已验证真实 PDF 入库、真实 LLM 回答和 canonical Evidence 页码锚点，但业务真实论文与 Frozen Golden 仍未完成；
3. 尚无 Frozen Golden、answer-faithfulness/citation-entailment、标准浏览器 PDF canvas/引用跳页和 SSE E2E；
4. 依赖 lock/constraints 尚未完成，完整环境仍不能只靠 requirements 重建。

### P1

1. legacy fallback 的 `[pN]` 仍是兼容逻辑，不能视为 Evidence-grounded citation；
2. canonical Reader tools 已回流到 `ContextPackage`/Evidence Registry，但旧 Reader 工具与 fallback 仍存在，尚未达到可删除门禁；
3. Worker 的单机 SQLite 模式没有跨主机调度、指标与告警；当前适合本地/单机开发，不是生产队列。

本轮已完成的 P1 基础修复：v009 已迁移 Reader/Daily/KG/Feedback 的持久化 schema；仅保留 capability probe 的内存 FTS DDL，普通业务查询不再执行 schema DDL。

### P2

1. 双语 sparse/跨语言检索已有真实 PDF Silver v2 与受限 dense/rerank 对照，但尚无已审核 Frozen Golden；
2. Query/Document Embedding 接口已拆分，但 opt-in instruction 还未用 Golden dev split 校准；
3. Rerank 已移除固定默认阈值，任务化 instruction 和未来阈值仍待 Golden 校准；
4. Memory 的 dense/vector 语义召回仍未验证是否有必要；
5. canonical 与旧 Reader/fallback 事实源并存；
6. 多论文全文 RAG 只有 SQLite canonical regression；仍缺业务论文、Frozen Golden 与浏览器引用交互验收。

## 9. 当前阶段

Phase 1 已完成并作为历史基线保留。

Phase 2 当前状态：

- Gate 0/WP1/WP2 已完成代码与临时数据库验收：RAG strict preflight、显式 Worker CLI、保存后自动 enqueue、lease/retry/recovery、状态 API/Reader UI、只读 dry-run backfill、v009 runtime schema 与负反馈隔离；
- WP3 已完成基础代码与临时数据库回归和 Silver v2：v010 dual FTS、AcademicQueryPlanner、Weighted RRF、Embedding split/config hash、task-aware Rerank、bounded candidate rerank、Evidence expansion、CJK layout normalization 与表格 row chunk；
- WP4 已完成基础代码与临时数据库回归：v011 Memory retrieval schema/dual FTS、`MemoryRetriever`、hard scope/expiry filter、相关性门槛、paper/user quota、supersede 和非证据化 Context handoff；
- WP5/WP6 核心已完成基础代码、临时数据库回归和隔离产品 E2E：`ContextPackage`、tiktoken 预算、task policy、服务端 History、request-scoped Evidence Registry、Citation Validator、canonical tool re-entry、Reader request trace；真实测试 PDF 的 LLM 问答已返回 canonical Evidence 页码锚点；
- 数据模型、Parser、Chunk、Job、Embedding/LanceDB、Hybrid、Rerank、Context/Evidence：已实现或部分接入；
- 隔离真实语料回填、Embedding 和 Silver v2 sparse/dense/rerank 对照、canonical tools 主链迁移、Reader trace 与多论文 canonical SQLite regression 已完成；旧 fallback 删除、业务库回填、Memory dense/vector retrieval、Frozen Golden、answer/citation/UI E2E、故障注入与多论文业务/浏览器验收：未完成。

隔离评测的精确语料、指标、命令和禁止事项以 [EVALUATION_STATUS.md](./EVALUATION_STATUS.md) 为准。

下一步以 [下一阶段执行指南](./NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md) 为唯一详细计划。
