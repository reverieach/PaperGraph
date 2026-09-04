---
title: 冲突、待确认问题与证据缺口
module: Audit
tags:
  - conflict
  - unknown
  - verification
related:
  - 61_LIMITATIONS_TECH_DEBT.md
  - 62_FUTURE_IMPROVEMENTS.md
  - 00_PROJECT_INDEX.md
evidence:
  - repository-wide inspection
  - 2026-07-31 verification commands
last_verified: 2026-07-31
---

# 冲突、待确认问题与证据缺口

## 1. 业务数据库只到 Migration v007，代码已到 v011

### 发现的现象

2026-07-31 以 SQLite URI `mode=ro` 检查 `backend/data/papers.db`，`schema_migrations` 只有 v001–v007；代码 `migrations/__init__.py` 注册到 v011。业务库有 11 篇论文、0 active version、0 chunks、0 ingest jobs，FK check 为 0 issues。

### 涉及文件

- `backend/data/papers.db`（仅只读检查，未修改）
- `backend/app/infrastructure/db/migrations/__init__.py`
- `backend/app/api/main.py::lifespan`

### 当前能够确认的内容

API startup 会调用 `run_migrations`，代码拥有 v008–v011 upgrade path 和 tests。

### 无法确认的内容

为什么当前真实 DB 尚未经过最新 API startup；v007→v011 对这份用户数据的实际升级耗时与结果。

### 建议向项目开发者询问的问题

何时允许先备份 DB/WAL/SHM，再在副本运行 Migration rehearsal，并最终迁移真实库？

## 2. 环境文档的 RapidOCR 依赖说明已落后于实际文件

### 发现的现象

`docs/ENVIRONMENT.md` 和仓库级说明称 `requirements-rag.txt` 仍声明旧 `rapidocr-onnxruntime`；实际 2026-07-31 文件已是 `rapidocr>=3.9,<4` 与 `onnxruntime>=1.28,<2`。

### 涉及文件

- `backend/requirements-rag.txt`
- `docs/ENVIRONMENT.md`
- `AGENTS.md`

### 当前能够确认的内容

实际 requirements 已修正，权威 venv 中 RapidOCR 3.9.2/ONNX Runtime 1.28.0，`pip check` 通过。

### 无法确认的内容

该修正是否尚未提交，或文档为何没有同步。

### 建议向项目开发者询问的问题

是否将依赖修正视为当前正式事实，并同步更新 `ENVIRONMENT.md` 与仓库约定？

## 3. mypy 错误数量文档不一致

### 发现的现象

`TESTING_AND_ACCEPTANCE.md` 写 140 errors/41 files，`EVALUATION_STATUS.md` 的表格写 141 个历史错误。2026-07-31 实测为 140 errors/41 files。

### 涉及文件

- `docs/TESTING_AND_ACCEPTANCE.md`
- `docs/EVALUATION_STATUS.md`
- `backend/app`

### 当前能够确认的内容

当前实际值是 140/41；前端 typecheck 通过。

### 无法确认的内容

141 是旧运行还是文档笔误。

### 建议向项目开发者询问的问题

是否以本次完整命令输出同步所有 current 文档，并为 mypy 建立可追踪 baseline？

## 4. “JWT”名称与实际编码标准不一致

### 发现的现象

token 有 header.payload.signature 和 HS256 语义，但 header/payload 使用 UTF-8 JSON 的 hex，signature 也是 hex，不是 JWT 规范的 base64url。

### 涉及文件

- `backend/app/services/auth/user_service.py`
- `frontend/src/services/api/client.ts`

### 当前能够确认的内容

签名验证、过期、compare_digest、active user lookup 有效；它是自定义 HMAC token。

### 无法确认的内容

是否有与标准 JWT gateway/第三方服务互操作的计划。

### 建议向项目开发者询问的问题

是否迁移 PyJWT/Authlib 或 server-side session，并处理旧 token 过渡、撤销和 refresh？

## 5. Docker 配置无法提供完整 canonical RAG

### 发现的现象

Backend Dockerfile 只安装 `requirements.txt`；Compose 没有 Worker、artifact/vector/model cache volume 或 GPU。Dockerfile ENV 名与 Settings 实际字段也不完全一致。

### 涉及文件

- `backend/Dockerfile`
- `docker-compose.yml`
- `backend/app/settings/config.py`

### 当前能够确认的内容

基础 API/前端的实验配置存在；文档也明确 Docker 未验收。

### 无法确认的内容

项目是否计划提供 CPU RAG 镜像、CUDA 镜像，还是完全以 Windows 本机为部署目标。

### 建议向项目开发者询问的问题

Docker 应补齐为正式交付路径，还是删除/隔离以避免“一键部署”误解？

## 6. MCP 依赖与完整可达性未确认

### 发现的现象

MCP adapter import MCP client，但基础和 RAG requirements 均未声明 `mcp`；常规 `ResolvedSearchPlan` allowed sources 也只有 arXiv/DBLP/OpenAlex。

### 涉及文件

- `backend/app/core/search/sources/mcp.py`
- `backend/app/core/search/paper_searcher.py`
- `backend/app/services/retrieval/search_plan.py`
- `backend/requirements*.txt`

### 当前能够确认的内容

adapter、config 和 mock/pure tests 存在，默认关闭。

### 无法确认的内容

真实 UI query 如何稳定选择 MCP；当前完整 venv 是否偶然含 MCP；server 的真实 smoke 结果。

### 建议向项目开发者询问的问题

MCP 是保留的近期功能还是实验代码？若保留，是否增加 optional extra、preflight 和真实 E2E？

## 7. 隔离评测成绩与业务上线状态差距

### 发现的现象

隔离库有 16 篇/419 页/3,217 chunks 和 Silver 指标；业务库 0 active chunks。

### 涉及文件

- `docs/EVALUATION_STATUS.md`
- `backend/data/rag_eval_workspace/rag_eval.db`
- `backend/data/papers.db`

### 当前能够确认的内容

算法链路和公开 corpus 已运行；业务数据未回填。

### 无法确认的内容

业务论文的语言、扫描质量、表格复杂度、真实 query 分布与结果。

### 建议向项目开发者询问的问题

哪几篇用户论文可作为首批 canonical 回填和浏览器验收样本？

## 8. Golden Candidate 的用户审核时间与负责人

### 发现的现象

10 个 Candidate 已通过 provenance 校验，但明确不得在用户审核前运行。

### 涉及文件

- `backend/tests/golden/candidates/golden_v1`
- `docs/EVALUATION_STATUS.md`

### 当前能够确认的内容

Candidate schema/qrel 有效。

### 无法确认的内容

谁审核、何时冻结、哪些措辞/证据需要修改。

### 建议向项目开发者询问的问题

能否安排集中 review，并把 approval/hash/model/parser/chunker 写入 frozen manifest？

## 9. Citation 语义蕴含尚无门禁

### 发现的现象

Validator 只校验 marker 是否映射 Registry；它不会判断回答中的结论是否由 snippet 支持。

### 涉及文件

- `backend/app/services/citation/validator.py`
- `docs/TESTING_AND_ACCEPTANCE.md`

### 当前能够确认的内容

伪造 marker 和错误来源可清理，canonical snippet/page 可返回。

### 无法确认的内容

真实回答的 claim-level faithfulness、漏引、过度推断率。

### 建议向项目开发者询问的问题

是否采用 claim segmentation + required/forbidden facts + 人工复核，并将 LLM judge 仅作为辅助？

## 10. 标准浏览器 PDF.js 与 SSE 验收未完成

### 发现的现象

内置浏览器缺 Web Worker，不能代表普通 Chrome/Edge；SSE 无 heartbeat/recovery 测试。

### 涉及文件

- `frontend/src/components/PdfJsViewer.vue`
- `frontend/src/services/api/search.ts`
- `docs/TESTING_AND_ACCEPTANCE.md`

### 当前能够确认的内容

Vite/Nginx `.mjs` MIME 已配置，前端 build 通过。

### 无法确认的内容

普通浏览器的多页 canvas、跳页、缩放、长 SSE、断网恢复。

### 建议向项目开发者询问的问题

目标浏览器矩阵是什么，是否允许建立 Playwright Chrome/Edge 发布门禁？

## 11. 待删除兼容代码的精确范围

### 发现的现象

canonical 主链已实现，但代码仍出现旧 Reader/PDF tools、fallback、compat enum/default 和未版本化 cache。

### 涉及文件

- `backend/app/services/reader`
- `backend/app/agents/support`
- `backend/app/services/context`
- `backend/app/api/routes/paper_reader.py`

### 当前能够确认的内容

用户要求本知识库只描述 canonical，旧链路是待删除部分。

### 无法确认的内容

哪些外部调用方仍依赖旧 response/default；哪些 cache table 可安全迁移/删除。

### 建议向项目开发者询问的问题

在三道门禁通过后，是否接受先生成 symbol/call-site/table 清单，再分 commit 删除并保留回滚 tag？

## 12. 个人负责范围无法从仓库确认

### 发现的现象

Git 仓库和文档没有足够可靠的作者—模块责任映射。

### 涉及文件

- 全仓
- `70_INTERVIEW_CHEATSHEET.md`

### 当前能够确认的内容

项目实现与测试事实。

### 无法确认的内容

用户本人设计、开发、重构或验收了哪些模块。

### 建议向项目开发者询问的问题

请按“模块、问题、方案、具体贡献、验收指标”补充个人负责范围。

## 13. 生产环境、SLA、成本和用户规模未知

### 发现的现象

仓库只有本机环境与实验 Docker，无线上基础设施、QPS、SLA、模型费用或事故记录。

### 涉及文件

- `docs/ENVIRONMENT.md`
- `docker-compose.yml`
- `backend/app/settings`

### 当前能够确认的内容

本机可运行、依赖和功能验证。

### 无法确认的内容

是否有未入仓的生产部署、真实用户、数据规模、预算和合规要求。

### 建议向项目开发者询问的问题

面试中是否应将项目定义为个人/课程/原型/内部工具？是否有可公开的规模与成本数据？

## 14. 外部模型的数据治理边界未知

### 发现的现象

Chat、Embedding、Rerank 通过外部兼容 API；文档不记录 key，但未描述文本保留、区域、脱敏或用户 consent。

### 涉及文件

- `backend/app/services/llm`
- `backend/app/services/embedding`
- `backend/app/services/rerank`
- `backend/.env.example`

### 当前能够确认的内容

权限与 key 不由 LLM 决定，日志避免记录 key。

### 无法确认的内容

供应商数据政策、是否允许发送完整论文片段、成本限额。

### 建议向项目开发者询问的问题

目标部署是否处理敏感/未公开论文？需要本地模型、脱敏、租户级 opt-out 或审计日志吗？

## 15. CI/CD 与分支发布策略不存在

### 发现的现象

未发现有效 GitHub Actions/其他 CI pipeline；验收靠本机命令。

### 涉及文件

- 仓库根目录
- `docs/TESTING_AND_ACCEPTANCE.md`

### 当前能够确认的内容

本机命令可复现当前 141 tests 和前端 build。

### 无法确认的内容

是否有仓库外 CI、发布审批、artifact 签名与回滚流程。

### 建议向项目开发者询问的问题

是否建立分层 CI：免费 T0/T1 每次运行、显式 T3/T4、浏览器 E2E、依赖/secret scan？
