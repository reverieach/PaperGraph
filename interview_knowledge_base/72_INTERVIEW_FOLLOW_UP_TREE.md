---
title: 面试追问树
module: Interview
tags:
  - follow-up
  - deep dive
  - evidence
related:
  - 70_INTERVIEW_CHEATSHEET.md
  - 71_INTERVIEW_QUESTIONS.md
  - 73_GLOSSARY.md
evidence:
  - backend/app/services/retrieval/hybrid.py
  - backend/app/services/ingest
  - backend/app/services/citation
  - backend/app/services/memory
last_verified: 2026-07-31
---

# 面试追问树

## 追问树一：为什么使用向量数据库？

```text
为什么使用向量数据库？
├── 为什么不用普通关系数据库？
├── 为什么还需要 FTS？
├── 为什么选择 LanceDB？
├── 数据量增大后怎么办？
└── 如何评估向量检索效果？
```

### 主问题

- 简短回答：为了召回跨语言、同义改写和语义相近的 PDF Chunk；但它只是 SQLite canonical chunks 的可重建投影。
- 深入回答：项目先校验 active version 的 embedding provider/model/dimension/config hash，再对 query embedding，用 user/paper/version metadata filter 查 LanceDB。dense 与 unicode61/trigram 经 Weighted RRF(k=60) 融合，不单独决定结果。
- 项目证据：`services/retrieval/hybrid.py`、`infrastructure/vector/lancedb_store.py`。
- 当前边界：无显式 ANN index；效果只在 Silver 小集验证。

### 为什么不用普通关系数据库？

- 简短回答：SQLite/FTS 能做词法检索，但不能稳定处理跨语言和语义改写。
- 深入回答：项目保留 SQLite 做事实与 sparse；向量只补语义。若只用关系 SQL LIKE，会失去排名、中文和语义能力。
- 证据：`sparse_retriever.py`、`academic_query_planner.py`。
- 边界：FTS 本身已覆盖很多专名查询，dense 不是所有 query 都必需。

### 为什么还需要 FTS？

- 简短回答：学术问题有模型名、数字、公式、缩写和专名，精确词法往往优于 dense。
- 深入回答：unicode61 处理英文，trigram 补中文；RRF 让不同分值空间互补。
- 证据：v006/v010 Migration、`hybrid.py`。
- 边界：trigram 会扩大候选，需要 limit/rerank。

### 为什么选择 LanceDB？

- 简短回答：嵌入式、本地、Python/Arrow 友好，符合单机项目。
- 深入回答：无需部署独立向量服务，metadata filter 可做 scope；version projection 可删除重建。
- 证据：`requirements-rag.txt`、`lancedb_store.py`。
- 边界：多租户、监控、横向扩容能力未验证。

### 数据量增大后怎么办？

- 简短回答：先测 p95 和 Recall；确认瓶颈后加 ANN 或迁服务化向量库。
- 深入回答：对象存储、PostgreSQL、外部队列、Qdrant/pgvector 应按瓶颈逐项迁移，保留相同 Frozen Golden。
- 证据：`55_PERFORMANCE_OPTIMIZATION.md`。
- 边界：当前没有规模性能集。

### 如何评估？

- 简短回答：Recall@k、MRR/nDCG、evidence completeness、latency 和 cost。
- 深入回答：同一 Frozen Golden 比 sparse、dense、hybrid、rerank；按语言/任务分组，不能只看总分。
- 证据：`docs/EVALUATION_STATUS.md`。
- 边界：当前只有 Silver 和 SciFact 诊断。

## 追问树二：如何保证 RAG 引用可信？

```text
如何保证 RAG 引用可信？
├── 页码从哪里来？
├── 为什么不能只靠 Prompt？
├── 工具返回如何处理？
├── 如果模型伪造 [E99] 呢？
└── 引用可信是否等于答案正确？
```

### 主问题

- 简短回答：canonical provenance + active version + Context budget 后编号 + request-scoped Registry + response Validator。
- 深入回答：Chunk 记录 page range/section/block UID；只有进入 Prompt 的 item 注册 `[E#]`；Validator 按 Registry 返回 snippet/page。
- 证据：`evidence_registry.py`、`validator.py`。
- 边界：只验证 source legality。

### 页码从哪里来？

- 简短回答：Docling canonical block/chunk 的 page provenance，不是模型生成。
- 深入回答：Canonicalizer 从 Docling provenance 取 page/bbox，Chunk 聚合 page_start/page_end。
- 证据：`canonicalizer.py`、`chunking.py`。
- 边界：解析 provenance 不完整时 quality 会降级，仍需抽样核验。

### 为什么不能只靠 Prompt？

- 简短回答：模型可能忽略规则或受文档注入影响。
- 深入回答：Prompt 是软约束，Registry/Validator 是确定性来源约束；授权同理不能交给 Prompt。
- 证据：`prompts/paper_analysis.py`、`validator.py`。
- 边界：确定性校验仍不能理解任意自然语言 claim。

### 工具返回如何处理？

- 简短回答：只取 chunk UID，Repository 回表，重新进 Context Builder 后才注册。
- 深入回答：工具 JSON 可能被截断或含状态/外部数据，不能直接升格 Evidence。
- 证据：`canonical_reader_tools.py`。
- 边界：tool selection 质量未有独立 Golden。

### 模型伪造 `[E99]` 呢？

- 简短回答：Validator 删除未知 marker，并在 metadata 记录 invalid marker。
- 深入回答：公开 citation payload只从 Registry 构建，不能从回答文本反向猜页码。
- 证据：`services/citation/validator.py`。
- 边界：删除 marker 后该句可能仍保留，需要 answer faithfulness 评测。

### 引用可信是否等于答案正确？

- 简短回答：不等于。
- 深入回答：Registry 证明证据来源，entailment 需要 required/forbidden fact、abstention 和人工复核。
- 证据：`docs/TESTING_AND_ACCEPTANCE.md`。
- 边界：Frozen answer/citation Golden 尚未完成。

## 追问树三：为什么使用持久化 Ingest Worker？

```text
为什么使用持久化 Worker？
├── 为什么不用 BackgroundTasks？
├── 如何防止重复消费？
├── Worker 崩溃怎么办？
├── 哪些错误重试？
└── 如何保证新旧版本一致？
```

### 主问题

- 简短回答：Docling/OCR/Embedding 长且易失败，需要脱离 HTTP 并支持重启恢复。
- 深入回答：SQLite Job 保存 status/attempt/lease/heartbeat/next_attempt/version；API 只 enqueue。
- 证据：`services/ingest/worker.py`、v008 Migration。
- 边界：单机 SQLite，不是分布式队列。

### 为什么不用 FastAPI BackgroundTasks？

- 简短回答：进程重启任务会丢，状态与重试难查询。
- 深入回答：Uvicorn reload 或 API crash 会中断内存任务；持久 Job 可由独立 Worker 恢复。
- 证据：`api/main.py::lifespan` 注释。
- 边界：开发模式仍可内嵌 Worker，但不推荐长期运行。

### 如何防止重复消费？

- 简短回答：`BEGIN IMMEDIATE` claim + lease owner/expiry + Job/version 幂等。
- 深入回答：活动相同 Job 复用；过期 running 才能重新 claim；version unique identity 防重复数据。
- 证据：`document_repository.py`。
- 边界：长 transaction 要避免；SQLite lock 仍需监控。

### Worker 崩溃怎么办？

- 简短回答：Heartbeat 停止，lease 到期后重新 claim。
- 深入回答：Job 状态在 DB，不依赖进程内队列；解析结果由 version 状态判定是否复用/修复。
- 证据：`worker.py`、`test_ingest_queue_and_worker.py`。
- 边界：底层外部任务不可恢复到中间算子，只能阶段级重跑。

### 哪些错误重试？

- 简短回答：临时网络/Provider/DB 错误有限重试；加密、损坏、缺失、质量硬失败不重试。
- 深入回答：稳定错误码保持 version/job/report 一致，避免浪费 attempts。
- 证据：`ingest/service.py`。
- 边界：异常分类仍需故障注入完善。

### 如何保证版本一致？

- 简短回答：新 version 完整持久化和投影后才原子激活，active unique。
- 深入回答：Embedding 失败可 degraded，但不会混合旧新 chunks；旧 active 被 supersede。
- 证据：v006 partial index、`activate_version`。
- 边界：投影清理/垃圾回收需继续验证。

## 追问树四：为什么 Memory 需要用户确认？

```text
为什么 Memory 需要用户确认？
├── LLM 草稿如何追溯？
├── 如何防重复提交？
├── 如何检索 Memory？
├── Memory 能作为引用吗？
└── 为什么不自动学习负反馈？
```

### 主问题

- 简短回答：永久状态的误写会持续污染后续请求，用户应拥有最终控制权。
- 深入回答：LLM 只对固定 conversation snapshot 生成候选，用户编辑/选择后提交。
- 证据：`memory_draft_service.py`。
- 边界：多一步 UX 可能降低使用率。

### 草稿如何追溯？

- 简短回答：每项带 `evidence_turn_ids`，必须是 snapshot 中 turn 的子集。
- 深入回答：Service 保存 from/to turn、snapshot hash，并用 Pydantic 验证。
- 证据：`domain/memory.py`、`MemoryDraftService`。
- 边界：turn 证明来源，不证明总结语义完全正确。

### 如何防重复提交？

- 简短回答：Idempotency-Key + normalized content hash + unique active index。
- 深入回答：事务 commit，IntegrityError 处理并发竞态。
- 证据：`memory_repository.py`、v004 Migration。
- 边界：跨语义同义文本仍可能重复。

### 如何检索？

- 简短回答：scope/status/expiry 硬过滤，再 dual FTS/lexical/RRF，paper/user 配额。
- 深入回答：min relevance 约 .04，最多 3 paper + 2 user。
- 证据：`services/memory/retriever.py`。
- 边界：dense semantic retrieval 未实现。

### Memory 能作为引用吗？

- 简短回答：不能。
- 深入回答：它进入 ContextPackage 的 memory source，但不进入 EvidenceRegistry。
- 证据：`context/builder.py`、`evidence_registry.py`。
- 边界：回答可受 Memory 影响，因此仍需无关注入率测试。

### 为什么不自动学习负反馈？

- 简短回答：一次 skip 可能是临时偏好，不能直接变长期画像。
- 深入回答：Daily feedback 保存在专用表，长期 Memory 仍需显式确认。
- 证据：`negative_feedback_memory.py`、v009 Migration。
- 边界：推荐侧如何长期聚合反馈仍缺效果评估。

## 追问树五：为什么选择 SQLite？

```text
为什么选择 SQLite？
├── 能支持并发吗？
├── FTS 如何做中文？
├── 如何做 Migration？
├── 如何备份？
└── 何时迁 PostgreSQL？
```

### 主问题

- 简短回答：单机、低运维、事务/FK/FTS 完整，符合个人科研工具。
- 深入回答：业务事实、Job、Memory 和 FTS 同一库，评测可复制隔离。
- 证据：`infrastructure/db/connection.py`、migrations。
- 边界：多副本和高写并发不适合。

### 能支持并发吗？

- 简短回答：WAL + busy timeout + 短 transaction，可以支持当前单机并发。
- 深入回答：Job claim 用 immediate lock；Reader 多读；仍只有单写者。
- 证据：`Database.connect`、`claim_next_ingest_job`。
- 边界：未做高并发压测。

### FTS 如何做中文？

- 简短回答：unicode61 + v010 trigram 双路。
- 深入回答：只有 CJK query 启用 trigram，再通过 RRF 合并。
- 证据：v010、`sparse_retriever.py`。
- 边界：trigram 索引体积和大规模延迟未测。

### 如何做 Migration？

- 简短回答：version/checksum、`BEGIN IMMEDIATE`、commit 后 FK/schema validate。
- 深入回答：已有 fresh/upgrade/idempotent/rollback tests，不改写历史。
- 证据：`migration_runner.py`、`tests/test_migrations.py`。
- 边界：当前业务 DB 仍停 v007，需副本演练。

### 如何备份？

- 简短回答：修改前备份 DB/WAL/SHM，并记录 PDF/artifact/vector 清单。
- 深入回答：SQLite online backup 或停写快照；投影可重建但 PDF/canonical 事实必须保护。
- 证据：AGENTS.md 数据安全规则。
- 边界：自动 backup/PITR 未实现。

### 何时迁 PostgreSQL？

- 简短回答：多副本、高写并发、集中备份和 RLS 需求出现时。
- 深入回答：迁移需重做 FTS/trigram、Job claim、路径/投影与回滚。
- 证据：`42_TRADEOFFS_ALTERNATIVES.md`。
- 边界：目前没有触发指标。

## 追问树六：项目是否生产就绪？

```text
项目是否生产就绪？
├── 测试通过了什么？
├── 为什么 Silver 不够？
├── 当前业务数据状态？
├── 部署与监控缺什么？
└── 上线前最短路径？
```

### 主问题

- 简短回答：不应称生产就绪。
- 深入回答：代码与隔离回归强，但业务回填、Frozen Golden、标准浏览器、Docker/CI/监控/安全未闭环。
- 证据：`54_TESTING.md`、`61_LIMITATIONS_TECH_DEBT.md`。
- 边界：无线上 SLA/QPS/事故数据。

### 测试通过了什么？

- 简短回答：141 backend tests、前端 typecheck/build。
- 深入回答：Migration、权限、Ingest、RAG、Memory、Agent、多论文均有临时 DB 回归。
- 证据：2026-07-31 命令。
- 边界：自动测试不是产品效果证明。

### 为什么 Silver 不够？

- 简短回答：开发集小且参与调试，容易过拟合。
- 深入回答：Candidate 要由用户审查后冻结，才能作为最终不可变门禁。
- 证据：`docs/EVALUATION_STATUS.md`。
- 边界：Candidate 目前不得运行。

### 当前业务数据状态？

- 简短回答：11 papers，0 active version/chunk/job。
- 深入回答：隔离库的 16 篇/3,217 chunks 不能替代业务库。
- 证据：2026-07-31 只读 SQLite snapshot。
- 边界：未启动 API 迁移，以保护用户 DB。

### 部署与监控缺什么？

- 简短回答：完整 Docker、CI/CD、结构化 metrics/alerts、标准 auth。
- 深入回答：当前只有 Windows 本机进程、stderr、health 和 request trace。
- 证据：Dockerfile、logging、health。
- 边界：无生产环境事实。

### 上线前最短路径？

- 简短回答：DB 副本演练→业务回填→Frozen Golden→浏览器/故障 E2E→删兼容代码→安全/观测门禁。
- 深入回答：每阶段都有可回滚证据与验收指标。
- 证据：`62_FUTURE_IMPROVEMENTS.md`。
- 边界：个人负责范围和上线目标需开发者确认。
