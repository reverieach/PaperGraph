---
title: 面试问题与参考答案
module: Interview
tags:
  - questions
  - answers
  - project defense
related:
  - 70_INTERVIEW_CHEATSHEET.md
  - 72_INTERVIEW_FOLLOW_UP_TREE.md
  - 99_UNCONFIRMED_QUESTIONS.md
evidence:
  - interview_knowledge_base
  - backend/app
  - backend/tests
last_verified: 2026-07-31
---

# 面试问题与参考答案

## 项目介绍

### Q1：请介绍 PaperGraph。

PaperGraph 是面向个人科研阅读的全栈文献系统，覆盖多源搜索、文献库、PDF canonical 入库、单篇/多篇证据 RAG、可确认 Memory、Daily、Graph 和导出。核心特点是把论文正文变成带 version/page/section/provenance 的 canonical chunks，并用 Evidence Registry 控制 `[E#]` 引用。

### Q2：它解决的核心问题是什么？

不是单纯“和 PDF 聊天”，而是解决搜索、保存、解析、证据问答和知识沉淀之间的断裂，同时处理多用户权限、长任务恢复、引用伪造和长期 Memory 误写。

## 架构设计

### Q3：整体架构是什么？

Vue SPA → FastAPI 模块化单体 → Service/Repository → SQLite 事实源；独立 Ingest Worker 通过 SQLite Job 协调；PDF/artifact 本地文件；FTS5/LanceDB 检索投影；外部 Chat/Embedding/Rerank 和学术 API。

### Q4：为什么不是微服务？

当前是单机科研工具，SQLite 和本地 PDF 是中心资产。模块化单体减少部署成本，最重的 Ingest 已通过独立 Worker 隔离。没有并发、团队或扩缩容数据证明拆微服务有收益。

### Q5：架构中最重要的不变量是什么？

LLM 不决定 user_id/paper_id/version/Memory scope；只有 active version 参与 Reader；只有进入本轮 ContextPackage 的 canonical chunk 才能映射为 `[E#]`。

## 功能实现

### Q6：一篇搜索结果如何变成可问答论文？

保存元数据 → PDF `.part` 下载/校验/原子替换 → 幂等 Job → Worker Docling/OCR → canonical page/block/chunk → quality gate → FTS/Embedding/LanceDB → 原子激活 version → Reader Hybrid Recall。

### Q7：为什么 PDF 保存成功后不立即解析？

Docling/OCR/Embedding 是秒到分钟级且可能失败。持久化 Job 让 API 快速返回、Worker 可重试和崩溃恢复，也避免 Uvicorn reload 中断任务。

### Q8：多论文研究怎么保证论文均衡？

先为每篇有 hit 的论文选择一个 anchor，再允许每篇最多两个，总 anchors≤4、Evidence≤12。Session 固定 paper_ids，所有 hydrate 都按 user/paper/active version 过滤。

## 技术选型

### Q9：为什么用 SQLite？

当前单机规模下，SQLite 同时提供事务、FK、WAL、FTS5、Migration 和可移植性；Job、Memory commit、active version 也能在同一事务域。多副本/高写并发时再迁 PostgreSQL。

### Q10：为什么用 Docling？

项目需要页、块、章节、表格、公式、bbox 和 provenance，而不仅是纯文本。Docling 提供结构树；PyMuPDF 用于快速预检和 canonical 降级解析。

### Q11：为什么用 LanceDB？

它是嵌入式本地向量库，与 Python/Arrow 集成，适合单机可重建投影。当前缺 ANN 规模基线，不能夸大为大规模向量服务。

## 数据库

### Q12：如何管理 PDF 版本？

`document_versions` 用 user/paper/file hash/parser config/chunker version 幂等，partial unique index 保证每个用户/论文只有一个 active。新 version 完成后事务内 supersede 旧 active。

### Q13：为什么 Embedding 不属于 document version identity？

Embedding 是可重建 projection。换模型或 instruction 不应重跑昂贵 Docling；项目用独立 embedding status/config hash 防止误用。

### Q14：如何处理 SQLite 并发？

WAL、5 秒 busy timeout；Job claim/Migration 用 `BEGIN IMMEDIATE`；lease 避免重复消费。它仍是单写者模型，扩到多机需外部 DB/队列。

## API

### Q15：为什么 Search 使用 SSE？

搜索是长耗时、服务端单向进度，SSE 比 WebSocket 简单。事件覆盖意图、子问题、轮次、RRF、精排、综述和 final_result。当前缺 heartbeat/resume。

### Q16：API 如何统一错误？

FastAPI exception handlers 返回 status、`error_code` 和 `request_id`；服务端记录原异常，对外隐藏堆栈。永久 PDF 错误另有稳定 Job code。

### Q17：前后端类型如何同步？

FastAPI OpenAPI 可由显式 RAG Python 导出，再用 `openapi-typescript` 生成类型。但当前仍有手写 interface，是待收敛问题。

## Agent

### Q18：项目里的 Agent 做什么？

SearchAgent 把自然语言解析成结构化 intent；PaperAnalysisAgent 在请求级 Context 内选择固定 Reader tools 并生成回答。授权、持久化和引用校验由 Workflow/Repository 完成。

### Q19：如何避免 Agent 无限循环？

Reader 最大 5 个 tool rounds、8 次工具调用、共享 28 秒 deadline、单工具默认 4 秒、输出 520 tokens；达到上限要求 final answer。

### Q20：这是多 Agent 系统吗？

有多个专用 Agent/LLM task，但没有自由 Agent-to-Agent 协作。多论文由固定 session + 统一检索 + 一次生成完成。

## RAG

### Q21：Hybrid Retrieval 怎么实现？

QueryPlanner 识别语言/任务；unicode61 和 CJK trigram sparse 与可用 dense 并行召回；Weighted RRF(k=60) 融合；小幅结构 prior；qwen3 task-aware rerank；然后 parent/neighbor expansion。

### Q22：为什么需要 CJK trigram？

SQLite unicode61 对中文自然问句 token 边界弱。trigram 提供连续汉字子串召回，Silver 对中问英/中文查询的混合检索明显强于 sparse 单路。

### Q23：为什么 parent-child Chunk？

Child 小而精确，适合 recall/embedding；Parent 和相邻 child 提供完整语义。这样不必在精确率和上下文完整性之间二选一。

### Q24：如何防止伪造引用？

Context Builder 裁剪后才为 canonical evidence 编号，Registry 固化 provenance；工具 UID 回表再注册；Validator 删除不存在的 `[E#]` 并返回真实 page/snippet。

### Q25：Citation Validator 能证明答案正确吗？

不能。它证明 marker 来自本轮合法 canonical source，不证明自然语言 claim 被证据蕴含。后者需 answer/citation Golden。

## Prompt

### Q26：Prompt 设计重点是什么？

结构化任务只输出 JSON；Reader 把 PDF/Memory/History/Tool/Web 标为不可信数据，只允许系统给出的 `[E#]`，证据不足必须说明，且不能自动写 Memory。

### Q27：如何处理 LLM JSON 不稳定？

提取 JSON object、Pydantic/schema、字段 hygiene、长度/enum 限制；Search 带 last output/correction hint 重试，Memory 验证 evidence turn subset。

## Tool Calling

### Q28：Reader 有哪些工具？

outline、section、table、document search，以及外部 paper/reference lookup。canonical tools 只能访问请求固定的 user/paper/active version。

### Q29：工具返回为什么要重新进 Context Builder？

工具 JSON 可能包含状态、被截断文本或外部数据，不等于 PDF Evidence。按 chunk UID 回表并预算后，只有存活文本能注册新 `[E#]`。

## Memory

### Q30：为什么 Memory 不能自动写？

一次模型误判若变成永久用户画像会持续污染后续回答。项目让 LLM 只做草稿，用户编辑/选择后用 Idempotency-Key 事务提交。

### Q31：Memory 如何检索？

先硬过滤 user、paper、confirmed、active、未过期，再用 unicode/CJK、lexical overlap、dual FTS 和 RRF；最多 3 paper + 2 user，且不进入 Evidence Registry。

## MCP

### Q32：项目 MCP 的真实状态是什么？

有 `arxiv-mcp-server` stdio adapter，默认关闭、每次 spawn，与原生 arXiv 重叠；requirements 未声明 MCP 包，真实端到端未充分证明，所以只能称可选架构适配。

## 异常处理

### Q33：哪些 Ingest 错误不重试？

PDF 缺失、Hash 失败、加密、损坏、质量门硬失败。这些是永久输入问题，继续重试只浪费资源且不会激活 version。

### Q34：Embedding 或 Rerank 失败怎么办？

Embedding 失败清理部分向量并标记 failed，canonical chunk/FTS 可 degraded active；Rerank 失败保留 RRF 排序，并返回 degradation reason。

## 性能

### Q35：性能优化做了什么？

Ingest 异步化、auto OCR、version 复用、child-only embedding/batch、并行多源搜索、候选/轮次/Token 上限、前端路由分包和 PDF 可见页懒渲染。

### Q36：当前最大瓶颈是什么？

Docling layout/table 与扫描 OCR。92 页原生文本 PDF 跳 OCR 只小幅缩短总耗时；image-only 单页约 85 秒。

## 安全

### Q37：如何做多用户隔离？

token 得 user_id，Route 显式传递，Repository 对 Paper/Document/Memory/Research/Export 再硬过滤；LLM 和客户端 body 不决定 owner。

### Q38：认证有什么问题？

密码 bcrypt 没问题，但所谓 JWT 实际是 hex header/payload 的自定义 HMAC token；localStorage 也有 XSS 风险。生产应标准化 token/session 和 cookie。

## 测试

### Q39：如何验证项目？

2026-07-31 `pip check/compileall/141 tests` 和前端 typecheck/build 通过。测试覆盖 Migration、权限、Ingest、RAG、Memory、Agent 和多论文；隔离 16 篇 PDF 有 Silver 指标。

### Q40：为什么还不能说生产就绪？

业务库没有 canonical chunks，Golden Candidate 未审核，标准浏览器 PDF/SSE/multi-paper、生产监控、CI/CD 和容器 RAG 都未闭环。

## 项目难点

### Q41：最难的问题是什么？

把“LLM 引用 PDF”做成可验证链路：从解析 provenance、active version、scope retrieval、Context budget、tool re-entry 到 response validator，任一环节缺失都会产生伪引用。

### Q42：最有代表性的工程解决方案是什么？

持久化 Ingest Job + canonical version + Evidence Registry。前者解决长任务可靠性，后两者解决数据一致性和引用可信。

## 架构取舍

### Q43：为什么不用 GraphRAG？

当前目标是页码级 PDF Evidence。Graph 可组织关系，但不能替代原文 Chunk 与 citation provenance；目前图谱只用于可视化。

### Q44：什么时候迁移到 PostgreSQL/外部队列？

出现多 API/Worker 副本、高写并发、PITR、共享部署和 queue governance 需求时；迁移前用指标证明 SQLite/Job 是瓶颈。

## 改进方向

### Q45：下一步优先做什么？

先备份/演练 Migration，回填业务论文 canonical data；用户审核并冻结 Golden；完成标准浏览器和故障 E2E；之后删除旧兼容代码，再补安全、观测和 CI。

### Q46：如果重新设计会改什么？

保留 deterministic scope、canonical version、projection、Evidence 和 confirmed Memory；更早引入标准认证、Prompt/Eval version、结构化日志、OpenAPI 单一类型源和浏览器 E2E。

## 使用注意

- “我的主要工作”没有仓库证据，使用 `[请项目开发者补充个人负责范围]`。
- 不把 Silver 当 Golden。
- 不把隔离评测库当业务库。
- 不把 source-valid citation 当成 semantic-faithful answer。
