---
title: PaperGraph 项目概览
module: 全局
tags:
  - 项目背景
  - 能力边界
  - 技术规模
related:
  - 00_PROJECT_INDEX.md
  - 02_SYSTEM_ARCHITECTURE.md
  - 70_INTERVIEW_CHEATSHEET.md
evidence:
  - backend/app/api/main.py
  - backend/app/settings/config.py
  - frontend/src/router/index.ts
  - docs/CURRENT_STATE.md
  - docs/EVALUATION_STATUS.md
last_verified: 2026-07-31
---

# PaperGraph 项目概览

## 一句话结论

PaperGraph 不是单一聊天机器人，而是一个以用户文献库和 canonical PDF 证据为中心的科研阅读工作台：先找论文、保存与入库，再在严格作用域和引用规则下完成单篇或多篇研究问答。

## 业务目标

| 科研痛点 | PaperGraph 的处理方式 |
|---|---|
| 搜索条件分散、不同学术源结果重复 | LLM 解析意图，arXiv/DBLP/OpenAlex 多源召回，统一去重与排序 |
| PDF 结构复杂，纯文本抽取丢失页码/表格 | Docling canonical page/block/chunk 模型，保留 provenance |
| RAG 回答容易伪造页码或引用 | 只为进入 ContextPackage 的证据分配 `[E#]`，响应后校验 |
| 长期阅读偏好容易被模型擅自记住 | 草稿—用户筛选/编辑—幂等提交的 Memory 工作流 |
| 多论文比较容易串论文、串用户 | Research session 固定论文集合，Repository 强制 user/paper/version scope |
| 文献资产难迁移 | 用户作用域 JSON、BibTeX 和图谱 PNG 导出 |

## 主要技术规模

2026-07-31 的仓库扫描结果：

| 项目 | 规模/状态 |
|---|---|
| 后端语言/框架 | Python 3.11、FastAPI、Pydantic v2 |
| 后端源文件 | `backend/app` 下 200 个 Python 文件 |
| API | 路由模块 38 个 handler，加根/健康检查共 41 个 handler |
| 测试 | 31 个 `test_*.py` 文件；最新 `141 passed, 1 warning` |
| 前端 | Vue 3、TypeScript、Vite 8、Ant Design Vue |
| 前端页面 | Search、Daily、Library、Reader、Memory、Research、Graph、Login |
| 业务存储 | SQLite + WAL；PDF 和 canonical artifact 使用本地文件 |
| 检索投影 | SQLite FTS5 + LanceDB |
| 模型接入 | OpenAI-compatible Chat、DashScope Embedding/Rerank |
| PDF 解析 | Docling 主解析，RapidOCR 本地 OCR，PyMuPDF canonical 降级解析器 |

## 程序入口

| 进程 | 入口 | 责任 |
|---|---|---|
| Backend API | `backend/run.py` → `app.api.main:app` | 配置校验、Migration、HTTP/SSE、认证 |
| Ingest Worker | `python -m app.workers.ingest_worker` | claim Job、解析、Chunk、Embedding、激活版本 |
| Frontend | `frontend/src/main.ts` | 创建 Vue 应用并挂载 Router/Ant Design |
| RAG 评测 | `backend/run_rag_eval.py` | 隔离 prepare/validate/retrieval/benchmark |
| Preflight | `python -m app.cli.preflight --strict-rag` | 依赖、配置与可选 DB Schema 门禁 |
| Backfill | `python -m app.cli.backfill_ingest` | 默认 dry-run 的历史论文入队 |

## 产品功能面

1. 学术检索：普通检索和显式 Deep Search，通过 SSE 展示意图理解、子问题、轮次、RRF、精排与综述进度。
2. 文献库：保存论文元数据、可选 LLM 分类、下载本地 PDF、自动创建 Ingest Job。
3. PDF Reader：左侧 PDF.js，右侧 canonical 问答、可跳页 Evidence chip、阅读时长与 Memory 草稿。
4. 多论文研究：2–8 篇固定论文的研究 session，跨论文 evidence-grounded 回答。
5. 长期 Memory：手动创建 user Memory，或从 Reader 对话生成 paper/user 候选再确认。
6. Daily：结合用户文献库、行为与多源候选生成每日推荐，并保存反馈。
7. Knowledge Graph：以 paper/author/category 等节点和确定性关系构建 D3 可视化。
8. Export：用户作用域 JSON、BibTeX、图谱 PNG。

## 明确边界

- canonical Reader 的前提是目标论文存在 active document version。
- Graph 是业务关系可视化，不是 Neo4j，也不是 GraphRAG。
- Agent 不负责授权、持久化和引用合法性；这些由 API/Service/Repository/Validator 决定。
- 多论文不是自由多 Agent 协作，而是固定 session + 一次确定性 RAG + LLM 生成。
- SQLite 是业务事实源；FTS 和 LanceDB 是可重建投影。
- 当前没有生产级 CI/CD、指标平台、分布式队列或线上 SLA 证据。

## 当前成熟度

| 层面 | 状态 | 说明 |
|---|---|---|
| 功能代码 | 已实现 | 搜索、库、Ingest、Reader、Memory、多论文、Daily、Graph、Export 均有真实路由与 Service |
| 权限与数据隔离 | 已实现并有测试 | user/paper/session/version scope 有 Repository/API 回归 |
| canonical RAG | 隔离环境已验证 | 临时 API E2E 与 16 篇公开 PDF 评测存在 |
| 业务数据上线 | 未完成 | 业务库快照无 active version/chunk/job |
| 产品质量门禁 | 部分完成 | Silver 开发集有分数，Frozen Golden 尚未批准 |
| 标准浏览器验收 | 部分完成 | PDF worker/citation 跳页与 SSE 断线恢复仍需验收 |
| 部署运维 | 本机可运行，容器实验性 | Windows + 显式 venv 是当前权威路径 |

## 项目亮点

- “可引用”不是 Prompt 约定，而是 ContextPackage 存活、Evidence Registry 映射和响应后 Validator 的组合。
- Ingest 用 SQLite Job、lease、heartbeat 和有限重试把重型解析从 HTTP 中剥离，且不引入额外消息中间件。
- canonical version 将 parser/chunker/file hash 固化为可审计身份，Embedding 作为可重建投影独立管理。
- 中英文检索组合 unicode61、CJK trigram、dense 和 task-aware rerank，而不是只依赖一个向量查询。
- Memory 写入需要用户显式确认，避免把模型推断当成永久事实。

## 面试官可能提问与回答要点

### 1. 这是一个什么项目？

它是科研阅读全链路系统，核心不是聊天 UI，而是围绕论文搜索、PDF canonical 化、证据检索、引用校验和用户资产沉淀建立可审计流程。

### 2. 和普通“上传 PDF 问答”有什么差别？

它有持久化版本、页/块/Chunk provenance、异步 Ingest、混合检索、工具回表、引用注册与多用户隔离，不把模型生成的页码当作可信引用。

### 3. 项目最有价值的工程决策是什么？

把权限、工作流、持久化和引用合法性保留在确定性代码，把 LLM 限制在意图理解、排序辅助、语义决策和回答生成。

### 4. 当前能否称为生产就绪？

不能。功能和隔离回归较完整，但业务库尚无 canonical 数据，Frozen Golden、标准浏览器 E2E、CI/CD、生产监控和容器 RAG 验收仍缺失。

## 证据来源

- `backend/app/api/main.py::lifespan`：启动、Migration、worker 边界。
- `frontend/src/router/index.ts`：真实页面范围。
- `backend/app/infrastructure/db/migrations/`：v001–v011 数据能力。
- `docs/EVALUATION_STATUS.md`：隔离语料与评测边界。
- 2026-07-31 实测：后端 141 tests、前端 typecheck/build。
