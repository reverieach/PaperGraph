# RAG 评测状态（2026-07-28）

这是当前评测事实的权威记录。它只描述隔离评测工作区，**不描述也不修改**
`backend/data/papers.db` 中的用户文献库。

## 已完成且实际验证

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 隔离 PDF 语料 | 16 篇公开合法 PDF、419 页、16 个 active canonical document version、3,217 个 active chunks（1,102 parent / 2,115 child） | `backend/tests/golden/corpus_manifest.json`；`backend/data/rag_eval_workspace/rag_eval.db`（gitignored 运行产物） |
| PDF 解析/Chunk | 16 篇均已在隔离库通过 `Docling auto -> canonical -> parent/child chunk -> FTS` 入库；新增表格采用 caption/header 保留的 `parent-child-v3` | `backend/data/rag_eval_workspace/reports/ingest_latest.json`；`services/ingest/chunking.py` |
| OCR 预检 | Llama 3（92 页）真实 PDF 的 12 页文本层采样判定为 `skip_ocr_native_text`；本次总入库约 215.7 秒，较强制 OCR 的约 225.9 秒仅小幅改善，主要耗时仍是版面/表格解析 | `services/ingest/parsers.py`；隔离 `ingest_latest.json`；运行记录 |
| 图像型扫描 OCR 探针 | 视觉核验的 1 页 image-only PDF：原生文本 0、图片 1、`auto` 判定 `use_ocr_low_native_text`；真实 Docling/RapidOCR 输出 678 字符并命中 `Hybrid retrieval` | 2026-07-28 隔离临时运行，约 85 秒；快速单测 `tests/test_ingest_parsers.py::test_auto_ocr_detects_a_real_image_only_pdf` 固化了图像型输入与 OCR 决策，但不在每次 pytest 中重复重型 OCR |
| Silver Set v2 | 24 例（23 可答、1 不可答）、26 个证据锚点；包含中文原生问句、中问英、长文档中部、表格和多证据问题；所有 qrel 已校验 PDF hash、parser/chunker 版本、Chunk UID/text hash | `backend/tests/golden/silver/retrieval_questions.jsonl`；`generation_runs/silver_v2.json` |
| Silver sparse 对照 | `Recall@10=0.869565`、`MRR@10=0.573240`、`evidence_complete@10=0.869565`；中文和中问英明显弱于混合检索 | `generation_runs/silver_v2.json`；隔离 `retrieval_sparse_latest.json` |
| Silver Hybrid + Rerank | 真实 `text-embedding-v4` + `qwen3-rerank`，`Recall@10=1.0`、`MRR@10=0.920290`、`evidence_complete@10=1.0`；该结论只适用于本小型 Silver 开发集 | `generation_runs/silver_v2.json`；隔离 `retrieval_hybrid_rerank_latest.json` |
| 后端回归 | `141 passed, 1 warning` | 2026-07-28 使用 `D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe` 的实际运行结果；覆盖真实图像型 OCR 决策、加密/损坏 PDF 的 Parser/Service 回归、永久输入错误不重试与多论文 canonical SQLite regression |
| Canonical Reader API E2E | 临时 DB、无外部模型：真实 `PDF -> canonical chunk -> FTS -> ContextPackage -> Evidence Registry -> [E#] citation`，响应为 `hybrid_rag_v2` | `backend/tests/test_canonical_reader_api_e2e.py`；不等于真实 LLM 或浏览器 E2E |
| Multi-paper canonical regression | 临时 SQLite：双论文 active canonical chunk 经 session-scoped Hybrid Recall、anchor 均衡、Evidence Expansion 和 cross-paper `[E#]` Validator；部分入库时 abstract-only 论文不能产生 citation，伪造 `[E#]/[pN]` 被清理 | `backend/tests/test_multi_paper_rag.py`；不等于业务论文、Golden 或浏览器 E2E |
| 不可解析/加密 PDF 降级 | AES-256 加密 PDF 和结构损坏 PDF 分别在 Docling 转换前被拒绝，版本/Job/report 返回 `PDF_ENCRYPTED`/`PDF_INVALID`，不会激活 canonical version；Worker 对这些不可重试输入错误只消耗一次 attempt | `tests/test_ingest_parsers.py`；`tests/test_ingest_service.py`；`tests/test_ingest_queue_and_worker.py`；`services/ingest/parsers.py`/`service.py`/`worker.py` |
| 隔离产品 Ingest → Reader E2E | 临时服务/临时 DB：注册后跳转 `/search`；长期 Memory 手动保存/回显；真实 PDF 保存、下载、持久化 Job、独立 Worker、Docling canonical 入库（2 页、2 blocks、3 chunks）；Reader 自动导读和一次真实 LLM 问答均返回 canonical Evidence `[E1]/[E2]` 与页码锚点 | 2026-07-28 本地实际运行；仅使用生成的公开测试 PDF 和隔离库，不写入 `backend/data/papers.db` |
| 静态类型 | 本轮 8 个 RAG/Context/Agent 模块的 `mypy --follow-imports=skip` 通过；全仓 `mypy app` 仍有 141 个历史类型错误 | 2026-07-28 实际运行结果；不得将局部通过误写为全仓类型全绿 |
| 前端生产构建 | `vue-tsc --noEmit && vite build` 通过 | 2026-07-28 的实际 `npm run build` 结果 |
| Golden Candidate | 已从 Silver 未使用的扩展论文构建 10 例，10/10 qrel 溯源校验通过 | `backend/tests/golden/candidates/golden_v1/` |
| SciFact 公共子集 | 固定 60 test queries / 300 title-abstract documents / 659 chunks；无外部模型调用 | `backend/tests/golden/benchmarks/scifact_v1.json`；隔离 `scifact.db` |
| SciFact sparse 诊断 | 文档级去重后 `Recall@10=0.966667`、`MRR@10=0.901852`；只验证公共文本检索 | `backend/data/rag_eval_workspace/benchmarks/scifact_v1/reports/sparse_scorecard_latest.json`（gitignored） |

## 严格边界

- SciFact 是 BEIR 的 title/abstract 文本检索子集，不含 PDF 页码、版面或可引用 PDF 证据。因此它**不能**证明 PDF RAG、引用正确性或回答忠实性。
- Silver v2 的分数是小语料开发诊断，不是产品通过标准，也不能用于声称通用效果。它不能替代业务库真实论文 E2E。
- 16 篇/419 页语料与 `backend/data/papers.db` 严格隔离。业务库当前仍没有 active canonical document version；不能表述为“用户论文已经全部向量化”。
- Golden Candidate 是 `pending_user_review`。当前仅允许其 schema/provenance 校验；**未经用户审核不得运行** sparse、dense 或 rerank 检索，不得用于调参或报告 Golden 分数。
- `frozen_gold/approved` 只有在用户审核后从候选集复制冻结；绝不原地篡改候选集。
- 仍没有 Reader 的 answer-faithfulness、citation-entailment、完整浏览器 UI/SSE Golden 门禁，也没有业务库真实论文 E2E。隔离浏览器已经验证了一次真实 LLM 问答和 Evidence 页码锚点，但不等于质量评测或发布验收。
- 本轮内置浏览器环境 `Worker` 不可用，PDF.js 因而走 fake worker 后失败；Vite Preview 已确认 `.mjs` Worker 资源为 `text/javascript`，生产 `frontend/nginx.conf` 已显式配置 `application/javascript`。这不能证明普通 Chrome/Edge 的 PDF canvas 渲染失败；必须在具备 Web Worker 的标准浏览器补一次 PDF 可视化与引用跳页验收。
- OCR auto 已正确避免了对明显原生文本 PDF 的无效 OCR，并在视觉核验的 image-only fixture 上实际得到 OCR 文本；它不是全链路性能优化承诺。当前 image-only 单页约 85 秒，原生文本单篇总耗时改善有限，中文/低清扫描质量仍未单独评测。

## 可复现命令

从 `backend` 目录使用 `D:\AIModels\PaperGraph\venv-rag\Scripts\python.exe`：

PDF 原件不随 Git 分发。需要在遵守来源许可的前提下，依据 `corpus_manifest.json` 的
`pdf_url` 手动获取到 `backend/data/rag_eval_corpus/pdfs/`，再由 runner 校验 `%PDF-` 文件头、
SHA-256、页数和加密状态；缺失或哈希不一致会失败，不会静默下载或写入业务库。

```powershell
# 对已获取的本地 PDF 做前置校验，并在隔离工作区建立映射
& $PaperGraphPython run_rag_eval.py prepare

# 只校验 Silver/Golden Candidate 的当前 provenance，不调用外部 API
& $PaperGraphPython run_rag_eval.py validate --cases tests/golden/silver/retrieval_questions.jsonl
& $PaperGraphPython run_rag_eval.py validate --cases tests/golden/candidates/golden_v1/retrieval_questions.jsonl

# 已执行过的本地 sparse 诊断
& $PaperGraphPython run_rag_eval.py retrieval --cases tests/golden/silver/retrieval_questions.jsonl --limit 10
& $PaperGraphPython run_rag_eval.py scifact-score --limit 10

# 已执行过的受限真实模型对照；只允许用于 Silver，且会调用已配置的 embedding/rerank 服务
& $PaperGraphPython run_rag_eval.py retrieval --cases tests/golden/silver/retrieval_questions.jsonl --limit 10 --with-dense --with-rerank --run-external --max-external-cases 24
```

Embedding 或 rerank 评测需要显式的 `--run-external` 和上限；这会使用已配置的外部模型接口。Golden Candidate 在审核前即便带该开关也不得执行。

## 下一步门禁

1. 用户审核 `candidates/golden_v1/retrieval_questions.jsonl` 的问题、证据和中英文措辞。
2. 审核通过后，复制为不可变 `frozen/golden_v1`，写入审批记录，才首次完整运行 Golden。
3. 以当前 Silver v2 记录为回归基线；新增 Silver、改变 parser/chunker/embedding/rerank 或检索策略时，同一命令、同一上限重跑并保留新运行记录，不能覆盖 v2。
4. 增加扫描/OCR、损坏/截断 PDF、PDF prompt injection、answer/citation 和 Reader UI/SSE E2E 门禁；加密 PDF 的确定性拒绝已覆盖，仍需补产品状态 UI 验收。
5. 在业务库真实入库、标准浏览器 PDF/引用跳页和 Frozen Golden 验收后，再删除旧 Reader/Memory 兼容链路。
