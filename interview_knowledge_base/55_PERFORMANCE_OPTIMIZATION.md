---
title: 性能优化与容量边界
module: Performance
tags:
  - latency
  - token budget
  - caching
  - batching
related:
  - 11_FEATURE_ACADEMIC_SEARCH.md
  - 21_RAG_PIPELINE.md
  - 50_DEPLOYMENT_CONFIGURATION.md
evidence:
  - backend/app/services/ingest/parsers.py
  - backend/app/services/embedding/dashscope_embedding.py
  - backend/app/services/retrieval/hybrid.py
  - backend/app/services/context/builder.py
  - frontend/vite.config.ts
last_verified: 2026-07-31
---

# 性能优化与容量边界

## 一句话结论

项目已在重任务异步化、OCR 跳过、Embedding batch、候选上限、Token Budget、并行搜索、前端懒加载和 PDF 可见页渲染上做了实质优化；但没有系统的并发/规模压测与生产 p95 基线。

## 性能热点

| 热点 | 原因 | 当前控制 |
|---|---|---|
| Docling/OCR | layout/table/model 推理重 | 独立 Worker、auto OCR、GPU auto |
| Embedding | 外部调用和向量量 | child only、batch 10、2 attempts |
| Dense search | 向量扫描 | metadata prefilter、候选 limit |
| Rerank | 外部模型 | top candidates≤50、timeout、2 attempts |
| LLM Reader | Prompt/token 成本 | Context 2,800 + tool 800，evidence cap |
| Deep Search | 多轮/多源/多 LLM | subquery≤4、round≤2、top-24/top-8 |
| Search network | 多源慢/失败 | async parallel、per-source/total timeout |
| PDF frontend | 多页 canvas | IntersectionObserver lazy render |
| UI bundle | AntD/PDF/KaTeX/D3 | route lazy load + manual chunks |

## Ingest 优化

### HTTP 与 Worker 解耦

保存请求只写 PDF/Job，避免用户等待分钟级解析。

### OCR Auto

最多采样 12 页文本层；原生文本充足时跳过 OCR。真实 92 页 PDF 检测为 `skip_ocr_native_text`。不过完整解析由约 225.9 秒降到约 215.7 秒，说明主要瓶颈仍是 layout/table，不应夸大 OCR 优化。

### 幂等与复用

相同 file/parser/chunker version 可复用已有 canonical version；若只向量投影损坏，修复 projection 而不重跑 Docling。

### Chunk/Embedding

- 只 embedding child chunks，parent 用于 expansion。
- batch 10。
- version 级 count verify。
- table-aware chunk 避免过多无语义片段。

## Retrieval 优化

- QueryPlan 只在有 CJK 时启用 trigram。
- 先 recall 限额，再 rerank。
- RRF 只在 bounded candidate map 上做。
- precision/cross-language 任务动态扩大候选，但 cap 100。
- Dense 只查询 embedding status/config 完全匹配的 active version。
- `reader_search_document` 工具用 sparse-only，避免一次 Tool 产生外部模型费用。
- Evidence Expansion 只从 top4 anchor、最多12 chunks。

## Context/Agent 成本控制

| 参数 | 典型值 |
|---|---:|
| Reader 文档 context | 2,800 tokens |
| Reader tool reserve | 800 tokens |
| Reader evidence | ≤10 |
| Multi-paper context | 6,200 |
| Multi-paper evidence | ≤12 |
| Agent tool iterations | ≤5 |
| Agent tool calls | ≤8 |
| Tool shared deadline | 28 sec |
| canonical tool output | 520 tokens |

`DynamicContextBuilder` 按来源 cap、去重、section diversity，只为存活 Evidence 编号，既控成本也提升信噪比。

## Search 性能

- `PaperSearcher.search_async` 并行 sources。
- Search intent cache：TTL 5 min，max 200。
- Deep Search subqueries 并行。
- Fine rank 在线程中运行并用 AnyIO wall timeout。
- SSE 立即发送阶段，改善长任务感知延迟。
- 默认 recall candidates 24、fine rank 15、results 5–30。

## 前端性能

2026-07-31 构建：

| 资源 | 大小 |
|---|---:|
| 最大业务 chunk | 360.20 kB |
| PaperReader | 358.59 kB |
| KaTeX | 258.87 kB |
| PDF worker | 1,375.83 kB |

优化：

- Route dynamic import。
- Ant Design 功能域分包。
- PDF worker 独立资源。
- 可见页 canvas 渲染、ResizeObserver debounce。
- Object URL 和 render task cleanup。
- Library search debounce。

## 缓存

| 缓存 | 生命周期 | 边界 |
|---|---|---|
| Search intent TTLCache | 进程 5 分钟 | 不跨实例 |
| Daily cache | SQLite user/date | 可持久化 |
| Browser search conversation | localStorage | 仅本设备 |
| canonical artifacts/index | 文件/DB | 可复用版本 |

Reader canonical opening没有依赖未版本化缓存，避免内容版本不一致。

## 当前瓶颈与风险

- Docling 单篇长文档耗时高。
- image-only 单页 OCR 约 85 秒。
- LanceDB 无显式 ANN index。
- SQLite 单写者限制多 Worker/高写并发。
- 外部 Embedding/Rerank/LLM latency 与费用未形成报表。
- sync tool timeout 不取消底层线程。
- 没有 50–200 篇规模性能集；文档明确暂不盲目下载做压测。

## 优化优先级

1. 先建立 ingest/query/LLM p50/p95 和 cost 基线。
2. 对业务论文完成真实 profile，定位 Docling/table/OCR 热点。
3. 为 LanceDB 构建 ANN 与 brute-force 对照，只有质量/延迟有收益才启用。
4. 增加 queue concurrency/backpressure，监控 SQLite lock。
5. Provider batch/connection reuse/circuit breaker。
6. PDF worker preload、Reader 子依赖进一步分包。
7. 多副本后外置 rate limit/cache/queue/storage。

## 面试官可能提问与回答要点

1. **最大性能瓶颈是什么？** 当前是 Docling layout/table 与扫描 OCR，不是纯 embedding。
2. **如何控制 LLM 成本？** Context source caps、evidence limit、tool budget、候选/轮次上限。
3. **为什么 child only embedding？** 减少向量量并保持精确 recall，parent 通过关系扩展。
4. **如何证明优化有效？** 同一 corpus/config 对比 ingest duration、Recall/MRR、query p95 和 cost。
5. **何时加 ANN？** 向量规模和 p95 证明 brute-force 成瓶颈，且 Recall 不退化时。

## 证据来源

- `backend/app/services/ingest/parsers.py`
- `backend/app/services/embedding/dashscope_embedding.py`
- `backend/app/services/retrieval/hybrid.py`
- `backend/app/services/context/builder.py`
- `frontend/src/components/PdfJsViewer.vue`
- 2026-07-31 Vite build output
