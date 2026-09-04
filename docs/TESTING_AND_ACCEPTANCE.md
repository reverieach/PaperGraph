# PaperGraph 测试与验收

文档状态：`CURRENT`
核验日期：2026-07-28

## 1. 当前基线

### 后端完整环境

```powershell
cd backend
$PaperGraphPython = 'D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe'
& $PaperGraphPython -m pip check
& $PaperGraphPython -m compileall -q app tests run_rag_eval.py
& $PaperGraphPython -m pytest -q
```

当前完整回归结果：`141 passed, 1 warning`（2026-07-28）；`pip check`、`compileall`、前端 `typecheck` 与生产构建均通过。隔离评测的已执行命令和指标以 [EVALUATION_STATUS.md](./EVALUATION_STATUS.md) 为准。

warning：

```text
StarletteDeprecationWarning:
fastapi.testclient 当前通过旧 httpx 适配，未来应迁移到 httpx2。
```

全仓 `mypy app` 当前为 `140 errors in 41 files`，因此不能将类型检查标为全绿；本轮 8 个 RAG/Context/Agent 文件通过了独立的 `mypy --follow-imports=skip` 检查。类型债务需要单列治理，不能通过扩大 `ignore_missing_imports` 掩盖。

### 前端

```powershell
cd frontend
npm run typecheck
npm run build
```

当前结果：通过。

Ant Design 已按功能域分包，最大业务 JavaScript chunk 约 360.20 kB，构建不再出现 500 kB chunk 告警。PDF.js worker 仍是约 1.376 MB 的独立静态资源。

## 2. 环境选择门禁

完整后端测试必须使用 `venv-rag`。

误用 `backend/.venv` 时结果为：

```text
70 passed, 4 failed
```

4 项均因该轻量环境没有 LanceDB。这个结果只证明环境选错，不代表代码回归。测试报告必须记录 `sys.executable`。

## 3. 当前测试覆盖

已覆盖：

- fresh/legacy/idempotent/rollback Migration；
- JWT、篡改 Token 和跨用户 Paper 隔离；
- Reader conversation/turn 所有权；
- Memory Draft、证据 turn、确认、去重、软删除和重启读取；
- Paper/Memory/Export 用户隔离；
- Canonical document repository；
- Parser Adapter、Quality Gate；
- section/page-aware hierarchical chunking；
- CJK PDF artificial spacing normalization、nearby table caption attachment、caption/header-preserving table row chunking；
- Docling `auto` OCR native-text preflight、ASCII-safe Windows staging 和 OCR mode 配置；
- 真实 image-only PDF 的快速 OCR 决策测试；另一次显式 Docling/RapidOCR smoke 已实际抽出 678 字符，重型 OCR 不放入默认 pytest；
- 加密/损坏 PDF 的明确拒绝：Docling 之前返回 `PDF_ENCRYPTED`/`PDF_INVALID`，并在 report/document version/ingest job 中保留同一错误码；Worker 对永久输入错误不重试；
- persisted Ingest Service/Worker、lease/heartbeat、延迟重试与过期恢复；
- 保存 PDF 后自动 enqueue、user-scoped Job status、独立 Worker CLI 与 SQLite 只读 dry-run backfill；
- v009 runtime schema、legacy 无 owner 表归档、TTL skip signal、Daily/Reader/KG 跨用户隔离；
- Embedding response validation；
- LanceDB upsert/search/scope filter；
- v010 document dual FTS migration、中文自然问句 trigram recall、QueryPlan、Hybrid/Weighted RRF、Embedding split/config hash 和 task-aware Rerank 基础行为；
- v011 Memory dual FTS migration、confirmed/active/current-user/current-paper hard filter、相关性门槛、paper/user 配额、过期/删除/supersede 和非证据化 handoff；
- QueryPlan-aware Dynamic ContextPackage、TokenCounter、跨源去重和最新 History tail；
- Reader RAG 上下文接入，且客户端 messages 不再作为 Prompt history；
- 临时数据库 API 级 canonical Reader E2E：PDF → canonical chunk → FTS → `hybrid_rag_v2` → Evidence Registry → `[E#]` citation；不调用外部 Embedding/Rerank/LLM；
- request-scoped Evidence Registry、Citation Validator、伪造 `[E99]`/`[pN]` 移除和 canonical snippet/page 返回；
- canonical Reader tool re-entry、Reader request trace 的脱敏字段边界、Agent loop deadline/output safety；
- 多论文 canonical RAG：session paper scope、双论文全文检索、anchor 均衡、Evidence Expansion、跨论文 `[E#]` 校验、部分入库不把摘要变成 citation、伪造 `[E#]`/`[pN]` 清理；
- legacy `[pN]` 语法解析兼容路径。
- 隔离产品 E2E：注册后跳转 `/search`、侧边栏进入 `/memory`、手动长期记忆保存/回显；真实测试 PDF 的保存 → 下载 → Job → 独立 Worker → canonical chunks → Reader 导读 → 一次真实 LLM 问答 → canonical `[E#]` 页码锚点；全程使用临时服务和临时 DB。

未充分覆盖：

- 隔离 16 篇/419 页公开 PDF 的 Docling/FTS/Embedding 入库，以及 Silver v2（24 例）sparse 与受限 dense/rerank 检索已实际运行；但它们仍是开发诊断，未替代产品 E2E；
- 已构建、未运行的 10 例 Golden Candidate（含中文与中英混合问题）；
- 真实 Embedding/Rerank 在更大、已冻结的评测集上的效果；
- Memory dense/vector semantic retrieval 与真实用户 Memory 的 Golden 校准；
- 旧全文 fallback/legacy Reader tools 的删除；
- PDF prompt injection、损坏/截断 PDF 的完整产品链路，以及加密/损坏 PDF 在前端 Job 状态/重试 UI 的浏览器验收；
- Search SSE 断线和 heartbeat；
- 多论文全文 RAG 的业务论文、Frozen Golden 与浏览器 citation E2E；当前已有双论文 canonical SQLite 回归（全文双论文、部分入库与伪造引用清理）。
- 标准浏览器端的 PDF canvas 与引用跳页 E2E、SSE 断线恢复。内置测试浏览器无 Web Worker，不能用它判定 PDF.js 生产渲染；Vite Preview 已核验 `.mjs` 响应，Nginx 已显式配置 Worker MIME。

## 4. 测试分层

| 层级 | 内容 | 默认 |
| --- | --- | --- |
| T0 | 纯函数、Schema、scope、RRF、budget | 每次 pytest |
| T1 | Repository/Migration/API 临时数据库集成 | 每次 pytest |
| T2 | 本地 LanceDB、真实 PDF、fake model provider | 合并前 |
| T3 | 真实 Embedding/Rerank/API smoke | 显式开关，阶段验收 |
| T4 | Golden Retrieval/Context/Answer/Citation | 模型或 Prompt 变更 |
| T5 | 浏览器 E2E | 发布前 |

外部 API 测试必须有显式开关、样本/费用限制，不在日志或 fixture 记录 Key。

## 5. Golden Corpus

当前位置：

```text
backend/data/rag_eval_corpus/
├─ manifest.json
└─ pdfs/
```

当前 PDF corpus 包含：

- Attention Is All You Need；
- BERT；
- RAG；
- ReAct；
- Lost in the Middle；
- GraphRAG；
- ColPali；
- Docling Technical Report。
- SELF-RAG；
- Corrective Retrieval Augmented Generation；
- BGE M3-Embedding；
- MMLongBench-Doc；
- ACL 2024 中文 RAG 综述；
- ACL 2024 汉语字词资源 RAG；
- ACL 2024 Self-Guide；
- Llama 3 Herd of Models。

共 16 篇、419 页。隔离 canonical 工作区已产生 3,217 个 active chunks（1,102 parent / 2,115 child）。

当前已有：

- `rag_eval.py` 隔离 prepare / ingest / qrel validation / sparse retrieval runner；
- 24 例 evidence-first Silver v2 retrieval cases / 26 个证据锚点与 provenance 校验；
- 10 例 `pending_user_review` Golden Candidate；
- 独立 SciFact 60 query / 300 document 公开文本检索评分卡。

仍没有：

- 已批准的 Frozen Golden；
- 真实 Embedding/Rerank 在 Frozen Golden 上的对照；
- Memory、Context、answer/citation 和 Reader UI 的完整 Golden/E2E cases。

因此仍不能声称 Hybrid、Rerank 或 Context 已在最终产品效果上优于旧方案。

现有正常 PDF 核心语料已达到 16 篇上限，暂不以盲目增加论文数量替代质量标注。原生与 image-only 英文 OCR smoke 均已实际验证；下一步优先补中文/低清扫描、损坏/截断、prompt injection 小型 fixture，以及 answer/citation/UI E2E；这些 fixture 不计入正常论文数量。

性能集暂不建设。本阶段不下载 50–200 篇论文做规模压测；只记录单次真实 Ingest、Retrieval 和外部 API 的基本耗时/成本。

## 6. 评测集状态与自主执行规则

```text
Silver Set
→ Agent 自主生成、自动验证、修订并运行
→
Golden Candidate
→ Agent 自主生成并做 schema/provenance 校验，标记 pending_user_review
→ 主开发继续，不等待逐条审核；未经用户审查绝不运行 Candidate
→
Frozen Golden
→ 用户集中审核通过
→ 冻结 golden-v1
→ 完整重跑阶段验收
```

自动生成采用 Evidence-first，并记录 source hash、生成/验证模型、Prompt、parser 和 chunker 版本。自动验证至少包括独立验证调用、页码/Hash/scope 确定性检查、歧义过滤和去重。未经用户批准的 Candidate 不能作为最终发布门禁。

公开基准使用固定小型子集并独立报告。当前已完成 BEIR SciFact 的 60 query / 300 title-abstract document 固定子集（只测公共文本检索）；它不代表 PDF 页码、版面、引用或回答质量。QASPER、QASA/PeerQA、MMLongBench-Doc 的数据适配在许可、样本边界和 PDF 证据链确定后再加入。

不把不同 Evidence 单位和任务类型强行合并成一个总分。每个子集记录原始 ID、版本、许可、固定 seed 和 Adapter 版本。

## 7. Golden Test 目标结构

```text
backend/tests/golden/
├─ corpus_manifest.json
├─ generation_runs/
├─ silver/
├─ candidates/golden_v1/
├─ frozen/golden_v1/
├─ benchmarks/scifact_v1.json
├─ imports/
│  ├─ qasper/
│  ├─ qasa_peerqa/
│  └─ mmlongbench_doc/
├─ fixtures/
└─ runners/
```

必须覆盖：

- 中文问中文；
- 中文问英文；
- 英文问英文；
- 英文问中文；
- 缩写/模型/数据集；
- 中段事实；
- 表格/公式/参考文献；
- 不可回答；
- prompt injection；
- Memory 相关/无关/跨用户/跨论文；
- 多论文证据区分。

## 8. 不可妥协的验收

| 指标 | 要求 |
| --- | --- |
| Chunk user/paper/version/page provenance | 100% |
| 跨用户 Retrieval/Memory 泄漏 | 0 |
| 跨论文越权 | 0 |
| deleted/unconfirmed/expired Memory 召回 | 0 |
| Citation marker 映射本轮 Evidence | 100% |
| Citation snippet 来自 canonical source | 100% |
| Migration fresh/legacy/idempotent/rollback | 全通过 |
| 前端 typecheck/build | 通过 |

效果初始目标：

- Hybrid Recall@10 ≥ 0.85；
- Hybrid 不低于最佳单路 Retriever；
- Rerank 后 MRR/nDCG 不下降；
- 页码命中率 ≥ 0.90；
- 无关 Memory 注入率 0；
- 不可回答问题伪引用率 0。

Silver 和 Golden Candidate 可以用于开发期相对比较。绝对阈值在用户审核并冻结第一版 Golden 后复核；最终阶段验收只对 Frozen Golden 生效，且不能通过删除困难样本达标。

## 9. Answer 评测边界

- 不对完整 LLM 文本做逐字断言；
- 断言 required facts、forbidden facts、abstention、Evidence 和页码；
- 固定模型、Prompt 版本、temperature 和输入 Hash；
- LLM-as-judge 只作为辅助；
- 权限、scope 和 citation legality 必须由确定性代码判断；
- 高价值失败案例必须人工复核。

## 10. 阶段完成报告

下一阶段完成后生成：

```text
docs/PHASE_2_5_COMPLETION_REPORT.md
```

必须记录：

- Git commit；
- `sys.executable` 和依赖版本；
- 真实命令与退出码；
- corpus/问题/qrels 版本；
- 解析、检索、Memory、Context、引用指标；
- 外部 API 成本和延迟；
- 降级测试；
- 未完成项。
