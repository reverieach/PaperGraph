---
title: 面试速查
module: Interview
tags:
  - elevator pitch
  - highlights
  - STAR
related:
  - 01_PROJECT_OVERVIEW.md
  - 71_INTERVIEW_QUESTIONS.md
  - 72_INTERVIEW_FOLLOW_UP_TREE.md
evidence:
  - 00_PROJECT_INDEX.md
  - 54_TESTING.md
  - docs/EVALUATION_STATUS.md
last_verified: 2026-07-31
---

# 面试速查

## 30 秒项目介绍

PaperGraph 是一个面向科研阅读的全栈文献系统。它把多源论文搜索、文献库、PDF canonical 入库和证据约束的 RAG 串起来：PDF 由独立 Worker 用 Docling 解析成带页码的 page/block/chunk，在线用 FTS、向量检索、RRF 和 rerank 召回，只有真正进入 Context 的片段才能获得 `[E#]` 引用。权限、Memory 写入和引用合法性由确定性代码控制，不交给 LLM。

## 1 分钟项目介绍

PaperGraph 解决的是科研人员“找论文、保存、读 PDF、做对比、沉淀研究记忆”割裂的问题。后端是 FastAPI 和 SQLite，前端是 Vue 3。保存 PDF 后，API 只做原子落盘和持久化排队，独立 Worker 完成 Docling/OCR、质量门、层次 Chunk 和 Embedding。Reader 查询时用 unicode61、中文 trigram、LanceDB dense、Weighted RRF 和 qwen3 rerank，再做 parent/neighbor expansion 和 Token Budget。项目最特别的是 Evidence Registry：只有本轮真正给模型的 canonical chunk 才能被引用，模型生成的未知 marker 会被清理。Memory 也不是自动写入，而是 LLM 生成草稿、用户编辑确认后幂等提交。当前代码和隔离测试较完整，最新 141 个后端测试通过，但业务库尚未回填 canonical 数据，Frozen Golden 和标准浏览器 E2E 仍是下一阶段重点。

## 3 分钟项目介绍

### 背景

普通 PDF Chat 往往把文本一次性抽出来，缺少版本、页码来源、权限和长期状态治理。PaperGraph 从学术搜索开始，最后落到可审计的论文证据与用户资产。

### 架构

- Vue 3 页面负责 Search、Daily、Library、Reader、Memory、Research 和 Graph。
- FastAPI Route 做认证、Schema 和 SSE。
- SQLite 是用户、论文、canonical 文档、会话、Memory、Job 的事实源。
- 独立 Worker 消费 SQLite lease Job，完成重型 Ingest。
- FTS5 和 LanceDB 是可重建检索投影。
- Chat/Embedding/Rerank 通过 OpenAI-compatible/DashScope provider。

### 核心链路

论文保存后先 `.part` 下载并校验 `%PDF-`，然后幂等创建 Job。Worker 解析、canonicalize、quality gate、parent-child-v3 Chunk，再写 page/block/chunk 和向量；新版本完成后原子激活。Reader 只查询 active version，经过 Hybrid Recall、Evidence Expansion 和 ContextPackage。每个存活片段分配 `[E#]`，Agent 工具返回还要按 UID 回表和重新预算。最终 Citation Validator 删除伪造 marker并返回 canonical page/snippet。

### 工程约束

LLM 只做意图、有限工具选择、排序辅助和生成；`user_id`、`paper_id`、version、Memory scope、持久化和引用合法性由代码决定。Memory 需要用户确认。多论文研究固定 session 论文集合并做 anchor 均衡，不是自由多 Agent。

### 结果与边界

2026-07-31 后端 `141 passed, 1 warning`，前端 typecheck/build 通过。隔离 16 篇/419 页语料有 3,217 chunks，Silver v2 上 Hybrid+Rerank Recall@10=1.0、MRR@10=0.920290。但这是小型开发集；业务库 11 篇论文尚无 active canonical version，Golden Candidate 还未用户审核，所以不能称为生产就绪。

## 项目核心亮点

1. canonical version + atomic activation，避免半成品进入 Reader。
2. SQLite 事实源与 FTS/LanceDB 投影分离。
3. unicode61 + CJK trigram + dense + RRF + task-aware rerank。
4. Evidence 预算后编号，工具结果回表再注册。
5. LLM 不决定权限、Memory scope 或 citation legality。
6. 持久化 Worker 有 lease、heartbeat、有限重试和崩溃恢复。
7. Memory 采用“草稿—用户确认—幂等提交”。
8. 单篇和多篇共享 Evidence/Citation 原则。

## 我的主要工作

`[请项目开发者补充个人负责范围]`

可以按以下模板填写，但不要在没有证据时直接使用：

```text
我主要负责【模块/阶段】。核心问题是【真实问题】；我设计了【方案】，
在【代码/测试】中落地，并通过【指标/验收】验证。当前边界是【限制】。
```

## 整体架构一句话

模块化单体 API + 独立 Ingest Worker + SQLite canonical 事实源 + FTS/LanceDB 可重建投影 + 有界 Agent/Tool + Evidence Validator。

## 一次请求完整流程

```text
Bearer 认证
→ user/paper scope
→ active document version
→ QueryPlan
→ unicode61 + trigram + dense
→ Weighted RRF + rerank
→ parent/neighbor expansion
→ Context Token Budget
→ Evidence Registry
→ bounded Agent tools（必要时）
→ LLM answer
→ Citation Validator
→ persisted server history
→ frontend Evidence 跳页
```

## 最关键的技术难点

### 难点 1：PDF 结构与页码可追溯

方案：Docling canonical page/block/chunk，表格 row chunk 重复 caption/header，所有 Chunk 保留 page/section/block UID。

### 难点 2：RAG 引用不能只靠 Prompt

方案：先预算后注册 Evidence，工具 UID 回表，响应后清理未知 `[E#]`。

### 难点 3：重型 Ingest 的可靠性

方案：持久化 Job、lease、heartbeat、重试、版本幂等和原子激活。

### 难点 4：LLM 与系统控制边界

方案：Agent 只处理语义，权限、持久化、Memory 和 citation legality 留在确定性 Workflow。

## 最有代表性的解决方案

Evidence Registry 是最能代表项目的设计。它把“模型应该引用来源”转成可验证数据结构：Registry 固化 user/paper/version/chunk/page；只有进入 Context 的片段才注册；模型回答后 Validator 删除不存在 marker。这样能证明引用来源合法，但仍诚实承认暂不能自动证明语义蕴含。

## 为什么选择当前技术

- FastAPI：Python AI/PDF 生态、Pydantic、SSE。
- SQLite：单机低运维、事务、FK、FTS。
- Docling：结构、表格、provenance。
- LanceDB：嵌入式本地向量投影。
- Vue/AntD：快速构建科研工作台。
- RRF：融合不同尺度的 sparse/dense 排名。

## 项目当前不足

- 业务库无 canonical 数据。
- Frozen Golden 未完成。
- 标准浏览器 PDF/SSE/Multi-paper E2E 不完整。
- 后端 mypy 140 errors。
- Docker、监控、CI/CD 和标准 token 不完整。
- citation entailment 未验证。

## 如果重新设计

保留 canonical version、deterministic scope、Evidence Registry、user-confirmed Memory；更早加入标准认证、Prompt/Eval version、结构化 observability、OpenAPI 单一类型源、浏览器 E2E、对象存储/投影重建 runbook。

## 易踩雷表述

| 避免 | 改为 |
|---|---|
| “完全消除幻觉” | “验证 citation source legality，entailment 待补” |
| “生产级分布式” | “单机持久化 Worker，可恢复” |
| “Golden 已通过” | “Silver 已测，Candidate 待审核” |
| “所有论文已向量化” | “隔离语料已入库，业务库尚未回填” |
| “多 Agent 协作” | “专用 Agent + 确定性 Workflow” |
| “标准 JWT” | “自定义 HMAC token，需标准化】 |
