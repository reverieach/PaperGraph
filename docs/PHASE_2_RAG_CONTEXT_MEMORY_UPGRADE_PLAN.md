# PaperGraph Phase 2 实现状态与收口入口

文档状态：`EXECUTION_STATUS`
更新日期：2026-07-28
代码基线：当前工作树（Gate 0/WP1-WP11 基础实现、真实 Silver v2 诊断、Reader trace 与多论文 canonical regression）

本文件取代 2026-07-27 的“全部 TODO”初始计划。详细设计和后续步骤已经合并到 [下一阶段执行指南](./NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md)。

## 1. Phase 2 目标

```text
PDF
→ versioned parse
→ canonical page/block
→ page/section-aware parent-child chunks
→ FTS + vector
→ hybrid recall + rerank
→ dynamic context
→ evidence-grounded answer
→ validated citation
```

Memory 保持：

```text
用户点击总结
→ LLM draft
→ 用户选择/编辑/确认
→ paper/user scoped persistence
→ relevant retrieval
```

## 2. 当前环境

权威环境：

```text
D:\AIModels\PaperGraph\venv-rag
```

已验证：

- LanceDB 0.34.0；
- Docling 2.115.0；
- tiktoken 0.13.0；
- PyArrow 25.0.0；
- PyTorch 2.7.1+cu126；
- CUDA/RTX 4060；
- `141 passed, 1 warning`（2026-07-28，含 Parser/Chunker、真实 image-only OCR 决策、加密/损坏 PDF 拒绝及永久错误不重试、Hybrid、Context/Evidence、Reader API E2E、multi-paper canonical regression、Reader trace 与评测 runner 回归）；
- `npm run build`（`vue-tsc --noEmit && vite build`）通过。

环境不缺依赖；当前已通过 strict preflight、显式 `PAPERGRAPH_PYTHON` 和 constraints 固定解释器选择。全仓 `mypy app` 仍有 141 个历史类型错误，但本轮 8 个 RAG/Context/Agent 模块的 scoped mypy 已通过。剩余问题还包括可跨机器重建的完整 lock 与 CPU/CUDA 安装矩阵。

## 3. 工作包状态

| 原工作包 | 状态 | 当前证据/缺口 |
| --- | --- | --- |
| WP0 Phase 1 基线 | `COMPLETED` | Phase 1 报告和验收存在 |
| WP1 RAG 环境 | `COMPLETED_BASELINE` | strict preflight、显式 `PAPERGRAPH_PYTHON`、核心 constraints 已落地；完整跨机器 lock/CPU-CUDA 矩阵仍待补 |
| WP2 Domain/Migration/Repository | `IMPLEMENTED` | document domain、v006/v007、repository |
| WP3 Parser/Canonical/Quality | `VERIFIED_IN_ISOLATED_CORPUS` | Docling/PyMuPDF、canonicalizer、quality；16 篇/419 页公开 PDF 已在隔离库完成真实入库；native-text OCR preflight 已在 92 页真实 PDF 验证，visualized image-only fixture 的真实 Docling/RapidOCR smoke 也已抽出文本 |
| WP4 Hierarchical Chunker | `VERIFIED_IN_ISOLATED_CORPUS` | 1,102 parent + 2,115 child active chunks；新表格使用 `parent-child-v3`，保留 caption/header、按完整 row 分块，避免裸 HTML/前文混入 |
| WP5 Job/Worker | `VERIFIED_IN_ISOLATED_PRODUCT_FLOW` | 自动 enqueue、独立 Worker CLI、lease/heartbeat/retry/recovery、Reader Job 状态与 dry-run backfill 已实现；真实隔离 PDF 已走完保存、下载、Job、Worker、Docling canonical 激活；加密/损坏等永久输入错误不重试；业务库未验收 |
| WP5.5 Runtime schema/feedback isolation | `IMPLEMENTED_BASELINE` | v009 迁移 Reader/Daily/KG/Feedback 持久化 schema，归档无 owner legacy 表，取消 LLM 自动长期负偏好，增加复合 user/paper FK 与隔离测试 |
| WP6 Embedding/LanceDB | `PARTIAL` | Provider/index/store 和环境通过；当前业务数据未建索引 |
| WP7 Hybrid/Rerank | `VERIFIED_ON_SILVER_V2` | v010 `AcademicQueryPlanner`、unicode61/trigram 双 FTS、Weighted RRF、Embedding split/config hash、task-aware Rerank、bounded candidate rerank、source preference 与 Evidence Expansion 已落地；24 例 Silver 上 Hybrid+Rerank `Recall@10=1.0/MRR@10=0.920290`；阈值和 Frozen Golden 未完成 |
| WP8 Context Builder | `IMPLEMENTED_AND_TRACEABLE` | `ContextPackage`、tiktoken TokenCounter、QueryPlan task policy、跨源去重、服务端 History、dropped trace、canonical tool re-entry 已落地；隔离浏览器已验证真实 Reader LLM 问答和 canonical Evidence 页码锚点，answer/citation Golden、标准浏览器 PDF canvas/跳页与 SSE E2E 未完成 |
| WP9 Memory Retrieval | `IMPLEMENTED_BASELINE` | v011 `MemoryRetriever` 已使用 user/paper hard scope、confirmed/active/expiry filter、dual FTS/lexical gate、quota、supersede 和非证据化 handoff；Memory dense/vector、用户策略 UI 与 Golden 未完成 |
| WP10 Reader/Citation | `VERIFIED_IN_ISOLATED_PRODUCT_FLOW` | canonical RAG 建立 request-scoped Evidence Registry，Validator 只返回当前 paper/version/chunk 的 `[E#]` 和 canonical snippet/page；真实隔离 PDF 的浏览器 Reader opening/chat 已返回 `[E1]/[E2]` 与页码锚点；legacy `[pN]`、旧 Reader tools、标准浏览器 PDF canvas/跳页尚待删除/迁移/验收 |
| WP11 Multi-paper full RAG | `IMPLEMENTED_WITH_ISOLATED_REGRESSION` | `MultiPaperResearchService` 已按 session paper scope 执行 Hybrid Recall、每篇 anchor 均衡、Evidence Expansion、单一 ContextPackage、跨所选论文 Evidence Registry 与 Citation Validator；未入库论文仅为摘要背景。双论文全文/部分入库/伪造引用 SQLite 回归已通过；业务论文、Frozen Golden 与浏览器 citation E2E 未完成 |
| WP12 Evaluation/Observability | `PARTIAL_VERIFIED` | 16 篇/419 页 PDF corpus、Silver v2 24 例、Golden Candidate 10 例、隔离 runner、SciFact 60/300、Reader trace、加密 PDF 降级及隔离 Reader 产品 E2E 已完成；Frozen Golden、answer/citation、故障注入与标准浏览器/SSE E2E 未完成 |

## 4. 当前数据库事实

```text
papers=11
memories=4
memory_drafts=6
reader_conversations=2
paper_reader_turns=18
document_versions=0
document_pages=0
document_blocks=0
document_chunks=0
ingest_jobs=0
```

因此不能把“组件已实现”等同于“RAG 已在产品中工作”。

## 5. 已确定技术决策

- SQLite 是业务事实源；
- FTS/LanceDB 是可重建投影；
- Docling primary，PyMuPDF fallback；
- parent child/page/section-aware chunks；
- text-embedding-v4，1024 维；
- qwen3-rerank；
- Workflow/Service 控制流程，Agent 只处理语义；
- Memory 永久写入必须用户确认；
- 不新增 Agent、GraphRAG、Neo4j；
- 当前规模目标为本地数百篇论文。

## 6. 当前收口顺序

1. [已完成基础实现] 环境/启动/preflight；
2. [已完成基础实现] 自动 Ingest、回填、Job 状态和独立 Worker；
3. [已完成基础实现] Runtime DDL 和负反馈隔离；
4. [已完成 Silver v2 验证] 中英文 QueryPlan、dual FTS、Dense 接口拆分、Rerank、bounded candidate rerank、Evidence expansion、表格 row chunk 与真实语料校准；继续进行 Frozen Golden 门禁；
5. [已完成基础实现] Structured Memory Retrieval；继续完成 Golden 校准，并仅在确有收益时增加 Memory vector projection；
6. [已完成基础实现] Single-budget ContextPackage、服务端 History、source trust/instruction boundary；
7. [已完成基础实现] canonical Evidence Registry/Citation Validator、canonical Reader tools 与 tool re-entry；继续迁移旧 Reader tools；
8. [已完成扩展评测] Silver v2 sparse 与受限 dense/rerank、Golden Candidate 与固定 SciFact 子集、Reader request trace；等待用户审核 Candidate。继续补故障注入、answer/citation 与 E2E 门禁；
9. Frozen Golden/E2E 后删除旧 Reader/Memory；
10. [已完成基础实现] 多论文全文 RAG；继续完成业务论文、Frozen Golden 与浏览器 citation E2E 验收。

## 7. 完成标准

Phase 2 只有在以下条件同时满足时才能标记完成：

- 当前业务/测试论文完成真实 canonical Ingest；
- Reader 明确返回 hybrid RAG mode；
- Hybrid 和 Rerank 通过固定 qrels；
- Memory 无关/跨用户注入为 0；
- Citation 100% 映射本轮 Evidence；
- Agent 自主构建并验证 Silver Set、Golden Candidate 和固定公开基准子集；用户审核后冻结 `golden-v1`，最终 Golden、故障降级和 E2E 通过；
- 旧 Reader/Memory 实现删除；
- 生成 `PHASE_2_5_COMPLETION_REPORT.md`。
