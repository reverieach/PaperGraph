---
title: 项目术语表
module: Reference
tags:
  - glossary
  - terminology
  - code mapping
related:
  - 00_PROJECT_INDEX.md
  - 21_RAG_PIPELINE.md
  - 31_DATABASE_STORAGE.md
evidence:
  - backend/app/domain
  - backend/app/services
  - backend/app/repositories
last_verified: 2026-07-31
---

# 项目术语表

## 一句话结论

本表把面试中的通用术语映射到 PaperGraph 的真实类、表和状态，避免把愿景或通用概念误说成项目已实现能力。

| 术语 | PaperGraph 中的含义 | 代码/数据映射 | 易混淆点 |
|---|---|---|---|
| canonical document | 统一 parser 输出的页/块/Chunk 文档模型 | `domain/document.py` | 不是简单纯文本 |
| document version | 一次 file/parser/chunker 组合的持久化版本 | `document_versions` | Embedding 是独立投影 |
| active version | 当前 Reader 唯一可查询版本 | `status='active'` partial unique | ready/degraded 需经激活 |
| page | PDF 物理页及统计 | `document_pages` | page_index 与显示页码需区分 |
| block | 标题、正文、表格、公式等结构单元 | `document_blocks` | 带 bbox/provenance |
| parent chunk | 较大语义上下文 | `level='parent'` | 通常不做 dense 向量 |
| child chunk | 精确检索/Embedding 单元 | `level='child'` | 可扩展回 parent |
| provenance | 内容到 PDF 页/块/bbox 的来源 | block/chunk fields | Citation 的基础 |
| Quality Gate | 判断 canonical 文档 accept/degrade/reject | `services/ingest/quality.py` | 不只是文本长度 |
| Ingest Job | 持久化 PDF 建库任务 | `ingest_jobs` | 不是内存 BackgroundTask |
| lease | Worker 对 Job 的有时限所有权 | owner/expiry/heartbeat | 崩溃后可恢复 |
| projection | 从 canonical 事实重建的索引 | FTS/LanceDB | 失败不应破坏 source |
| FTS unicode61 | SQLite 英文/通用 token 稀疏检索 | `document_chunks_fts` | 中文能力有限 |
| trigram FTS | CJK 连续三元子串检索 | `document_chunks_trigram_fts` | 只在 CJK query 使用 |
| dense retrieval | query/document embedding 相似度 | DashScope + LanceDB | 需 config hash 匹配 |
| Hybrid Recall | sparse + dense 多路召回 | `HybridChunkRetriever` | 不等于 Search 多学术源 |
| Weighted RRF | 按排名倒数融合 | `k=60` | 不直接比较原始分数 |
| rerank | 对融合候选二阶段排序 | `DashScopeReranker` | 默认阈值未校准 |
| QueryPlan | 语言、任务、lexical/dense query 与偏好 | `AcademicQueryPlan` | Reader 内确定性规划 |
| SearchIntent | 学术搜索自然语言结构化结果 | `agents/support/search_models.py` | 与 Reader QueryPlan 不同 |
| ResolvedSearchPlan | Search Pipeline 的单一参数事实源 | `services/retrieval/search_plan.py` | 固化后检索层无 LLM |
| Evidence Expansion | anchor → parent/neighbor | `EvidenceExpander` | 始终再次做 scope |
| ContextPackage | 按 token/source/section 组装的材料 | `services/context/builder.py` | 含非 Evidence 来源 |
| Evidence | 本轮进入 Context 的 canonical chunk | package evidence items | 不等于所有 recall hits |
| Evidence Registry | `[E#]` 到 provenance 的请求级映射 | `services/citation/evidence_registry.py` | 只存本轮可引用项 |
| Citation Validator | 清理未知 marker并构造 citation payload | `services/citation/validator.py` | 不验证 entailment |
| `[E#]` | canonical Evidence marker | `[E1]` 等 | 不能由模型自行生成 |
| degradation reason | 可选通道失败或质量下降的机器可读原因 | retrieval/context/job metadata | 不应静默伪成功 |
| Reader Agent | 单论文受限 tool-calling 生成器 | `PaperAnalysisAgent` | 权限不由 Agent 决定 |
| SearchAgent | 学术搜索意图解析器 | `agents/search_agent.py` | 检索由 Pipeline 执行 |
| bounded agent loop | 有轮次/调用/deadline/output 上限的工具循环 | `services/llm/agent_loop.py` | sync thread不能强杀 |
| canonical Reader tool | outline/section/table/search | `canonical_reader_tools.py` | 工具 JSON 不能直接引用 |
| tool re-entry | UID 回表、重新预算、注册 | canonical tool base | 防止工具数据冒充证据 |
| Memory Draft | LLM 从固定 turns 生成的候选 | `memory_drafts` | 尚未永久生效 |
| confirmed Memory | 用户确认的长期状态 | `memories` | 不可当 PDF Evidence |
| paper Memory | `user_id + paper_id` scope | reading_summary 等 | 不能跨论文 |
| user Memory | `user_id` scope | preference/research_goal | 仍需相关性门槛 |
| Research Session | 固定用户和论文集合的多论文会话 | `research_sessions` | 客户端 chat 不扩大集合 |
| anchor balance | 多论文先每篇一个 anchor | `_select_diverse_anchors` | 非 LLM planner |
| Deep Search | 子问题、有限轮次、RRF、精排、摘要综述 | `deep_search_pipeline.py` | 不是 PDF RAG |
| MCP source | arXiv MCP 搜索 adapter | `core/search/sources/mcp.py` | 默认关闭，不是 Reader tool |
| Graph | SQLite 论文关系的 D3 可视化 | `GraphService`/`KnowledgeGraph.vue` | 不是 GraphRAG |
| Silver Set | 开发诊断检索集 | `tests/golden/silver` | 可用于迭代，不是最终门禁 |
| Golden Candidate | 已校验、待用户审核样例 | `candidates/golden_v1` | 未经审核不得运行 |
| Frozen Golden | 用户批准后的不可变验收集 | 计划 `frozen/golden_v1` | 当前不存在 |
| canonical Reader API E2E | 临时 DB 的 PDF→FTS→Evidence 回归 | `test_canonical_reader_api_e2e.py` | 不是真实浏览器/LLM质量 |
| user scope | 当前认证用户的硬过滤 | Repository 参数/SQL | 不能由客户端/LLM提供 |
| request ID | 单次 HTTP 关联标识 | `X-Request-ID` | 当前接受客户端值 |
| capability probe | 不泄露 key/path 的运行能力摘要 | `/health/capabilities` | 不等于真实 API 调用成功 |

## 缩写

| 缩写 | 含义 |
|---|---|
| RAG | Retrieval-Augmented Generation |
| FTS | Full-Text Search |
| RRF | Reciprocal Rank Fusion |
| OCR | Optical Character Recognition |
| SSE | Server-Sent Events |
| FK | Foreign Key |
| WAL | Write-Ahead Logging |
| MRR | Mean Reciprocal Rank |
| ANN | Approximate Nearest Neighbor |
| MCP | Model Context Protocol |

## 状态词

| 状态 | 含义 |
|---|---|
| 已实现 | 代码与直接测试存在 |
| 部分实现 | 代码存在但运行/业务/产品验收不完整 |
| 已验证 | 有明确命令、输出或隔离评测 |
| 未完成 | 接受标准尚未满足 |
| 无法确认 | 仓库没有足够证据 |
