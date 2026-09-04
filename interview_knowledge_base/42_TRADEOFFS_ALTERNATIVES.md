---
title: 架构取舍与替代方案
module: Architecture
tags:
  - tradeoff
  - alternatives
  - scale trigger
related:
  - 40_TECH_SELECTION.md
  - 41_ARCHITECTURE_DECISIONS.md
  - 62_FUTURE_IMPROVEMENTS.md
evidence:
  - backend/app
  - frontend/src
  - docs/CURRENT_STATE.md
  - docs/NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md
last_verified: 2026-07-31
---

# 架构取舍与替代方案

## 一句话结论

当前方案为单机可控、证据可审计和低运维优化；不是所有替代技术都更“高级”，只有当并发、数据量、团队或可靠性目标变化时才应更换。

## 核心取舍矩阵

| 问题 | 当前方案 | 得到什么 | 牺牲什么 | 更换条件 |
|---|---|---|---|---|
| 业务 DB | SQLite WAL | 零运维、FTS、便携 | 单写者、多副本弱 | 集中 SaaS/高并发 |
| Job | SQLite lease | 无额外中间件、持久化 | 优先级/吞吐/可观测有限 | 多机 Worker |
| PDF | 本地文件 | 简单、隐私 | 共享/灾备困难 | 横向扩容 |
| Vector | LanceDB local | 嵌入式、Arrow | 服务化能力弱 | 大规模在线向量 |
| Sparse | SQLite FTS5 | 与事实源近、低成本 | 分词/集群功能弱 | 海量语料/复杂检索 |
| Fusion | RRF | 无需分值校准 | 不利用绝对置信度 | 有可靠校准集 |
| Reader | bounded Agent | 可按需工具、成本有界 | 深度受限 | 复杂研究任务 |
| Multi-paper | 统一检索+一次生成 | scope 清晰 | 推理深度有限 | 有多 Agent Golden |
| Memory | 用户确认 | 减少误记 | 多一步操作 | 有可靠 consent UX |
| Progress | SSE | 单向简单 | 断线恢复弱 | 需要可靠双向状态 |
| Token | tiktoken/近似 | 快速 | 供应商 tokenizer 偏差 | 多模型精确成本控制 |

## SQLite vs PostgreSQL

### 当前选择 SQLite 的原因

- 单机项目；
- Migration/transaction/FK/FTS5 已够；
- 评测库复制容易；
- Job 与业务数据同一事务域。

### PostgreSQL 更优的场景

- 多 API/Worker 副本；
- 并发写、集中备份、PITR；
- row-level security；
- pgvector 与 SQL 聚合统一。

### 迁移成本

- 重写 Migration 和 SQLite-specific FTS/trigram；
- Job claim 改 `FOR UPDATE SKIP LOCKED`；
- 本地路径/评测流程调整；
- user data 迁移与回滚。

## LanceDB vs pgvector/Qdrant/Milvus

| 方案 | 适合当前性 | 优点 | 缺点 |
|---|---|---|---|
| LanceDB | 高 | 本地、Python、零服务 | 多租户/运维生态较小 |
| pgvector | 中 | 与 PostgreSQL 一致事务 | 当前仍是 SQLite |
| Qdrant | 规模增长后 | filter/服务化/监控 | 新服务 |
| Milvus | 超大规模 | 分布式吞吐 | 当前过重 |

当前没有 ANN 性能证据，不应仅凭“向量多”提前迁移。

## Docling vs PyMuPDF/Unstructured/cloud OCR

- Docling：结构与表格强，计算重。
- PyMuPDF：文本和页码快，复杂版面弱。
- Unstructured：连接器丰富，但仍需验证表格/provenance。
- 云 OCR：扫描质量可能更好，但带隐私、成本和确认问题。

当前采用 Docling 主路径 + PyMuPDF canonical 降级，符合“质量优先、离线可用”。

## SSE vs WebSocket

搜索是服务端向客户端单向进度，SSE 足够。若未来支持：

- 暂停/恢复任务；
- 人工中途反馈；
- 可靠 replay；
- 多端订阅；

则可用 WebSocket 或“持久化 event log + SSE cursor”，而不是只替换协议。

## Agent vs Workflow

| 任务 | 更适合 Agent | 更适合确定性 Workflow |
|---|---|---|
| 理解开放 query | 是 | 较弱 |
| 用户权限 | 否 | 是 |
| PDF version 激活 | 否 | 是 |
| 工具选择 | 有界使用 | 可设置上限 |
| Citation legality | 否 | 是 |
| Memory 草稿 | 是 | 提交必须 Workflow |
| 多源重试/超时 | 否 | 是 |

## RAG 替代策略

- 只 BM25：精确但跨语言弱。
- 只 dense：专名/数字/公式可能弱，且依赖外部 API。
- late interaction/ColBERT：效果潜力大，但索引和 GPU 成本高。
- GraphRAG：适合实体关系问答，但当前论文页码证据仍需文本检索。
- Long-context 全文：实现简单，但成本、位置偏差和多论文预算仍存在。

当前 Hybrid parent-child 是合理基线；是否升级必须由 Frozen Golden 和成本数据决定。

## 面试回答原则

- 不说“某技术最好”，说“它适合当前单机、数据量和质量目标”。
- 给出明确迁移触发器，而不是泛泛“以后上微服务”。
- 区分已测指标与合理推断。
- 任何效果方案先要求同一 Frozen Golden 对照。

## 面试官可能提问与回答要点

1. **为什么不用微服务？** 当前模块化单体减少运维，Worker 已隔离最重任务；团队/流量未证明需要拆。
2. **如果数据量增长 100 倍？** 先压测瓶颈，再把对象存储、DB、队列、向量服务逐项外置。
3. **为什么不用 GraphRAG？** 当前核心是 PDF 页码 Evidence，Graph 关系不能替代原文证据。
4. **为什么不用长上下文塞全文？** 成本、位置偏差、多论文超预算，且引用映射仍要结构化。
5. **替换技术如何避免拍脑袋？** 固定 corpus/query/模型/成本，比较 Recall/MRR/citation/latency。

## 证据来源

- `backend/app/services/retrieval/hybrid.py`
- `backend/app/services/ingest/parsers.py`
- `backend/app/infrastructure/db/connection.py`
- `backend/app/services/llm/agent_loop.py`
- `docs/NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md`
