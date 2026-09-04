---
title: 技术选型
module: Architecture
tags:
  - technology selection
  - FastAPI
  - SQLite
  - Docling
  - LanceDB
related:
  - 41_ARCHITECTURE_DECISIONS.md
  - 42_TRADEOFFS_ALTERNATIVES.md
  - 50_DEPLOYMENT_CONFIGURATION.md
evidence:
  - backend/requirements.txt
  - backend/requirements-rag.txt
  - frontend/package.json
  - backend/app/settings/config.py
  - docs/ENVIRONMENT.md
last_verified: 2026-07-31
---

# 技术选型

## 一句话结论

技术栈围绕“Windows 单机科研工具、结构化 PDF、可控 RAG、低运维”选择：FastAPI/Pydantic、Vue/TypeScript、SQLite/FTS5、Docling/RapidOCR、LanceDB 和 OpenAI-compatible 模型接口。

## 选型总表

| 技术 | 项目中的作用 | 优势 | 代价/边界 |
|---|---|---|---|
| Python 3.11 | 后端、AI、PDF、评测 | AI/PDF 生态完整 | 类型债较多 |
| FastAPI | HTTP/SSE/DI/OpenAPI | Pydantic 集成、异步友好 | 需谨慎处理 sync 重任务 |
| Pydantic v2 | 请求、配置、LLM JSON | 强 schema | 手写/生成类型仍可能漂移 |
| Vue 3 + TS | SPA | Composition API、轻量 | 无前端测试体系 |
| Ant Design Vue | UI 组件 | 快速构建数据密集页面 | bundle 较大 |
| SQLite + WAL | 业务事实源 | 零运维、事务、FTS、便携 | 单写者、多副本受限 |
| FTS5 unicode61/trigram | 稀疏检索 | 精确、中文补召回、本地 | 排序/分析器能力有限 |
| Docling | PDF 结构解析 | layout/table/provenance | 依赖重、CPU/GPU 耗时 |
| RapidOCR + ONNX Runtime | 扫描件 OCR | 本地可用 | 低清中文尚未系统评测 |
| PyMuPDF | 快速预检与 canonical 降级解析 | 轻、成熟 | 结构能力弱于 Docling |
| LanceDB | dense vector projection | 本地嵌入式、Arrow 生态 | 当前无 ANN/index 规模验证 |
| tiktoken | Context budget | 快速可复现 token 估算 | 与非 OpenAI tokenizer 有偏差 |
| OpenAI Python SDK | Chat/Embedding compatible API | 统一接口 | 供应商兼容差异 |
| DashScope Embedding/Rerank | `text-embedding-v4` / `qwen3-rerank` | 中英与任务排序 | 外部成本/网络依赖 |
| D3 | Knowledge Graph | 自定义力导图 | 大图性能与可访问性成本 |
| PDF.js | 浏览器 PDF | 页级 canvas 与跳转 | worker 资源大 |
| KaTeX | 数学公式 | 浏览器渲染快 | 仅支持其语法子集 |

## 为什么选择 FastAPI

代码证据：

- Route 用 `Depends(require_user)`。
- Pydantic ResponseModel 覆盖主要 API。
- async route + AnyIO/Threadpool 混合外部 I/O。
- 自动 OpenAPI 被脚本导出给 TypeScript。
- StreamingResponse 用于 SSE。

合理推断：项目 AI/PDF 代码本身是 Python，FastAPI 能避免跨语言 RPC 并保留 async API。

## 为什么选择 SQLite

代码证据：

- `Database` 统一 FK/WAL/busy timeout。
- v001–v011 Migration。
- FTS5 与 source table 同库。
- Job lease、Memory commit、active version 都依赖 transaction/unique index。
- 评测使用隔离 SQLite，方便复制与复现。

合理推断：单机个人科研工具更看重便携和零服务依赖，而不是横向扩容。

## 为什么选择 Docling

项目需要 page/block/table/formula/bbox/provenance，不只是字符串。Docling 能输出结构树与表格；PyMuPDF 用于加密/损坏检测、文本层采样和结构能力不足时的 canonical 降级。

## 为什么选择 FTS + LanceDB

- FTS：专名、模型名、公式标识、精确词。
- trigram：中文自然问句。
- dense：跨语言和语义改写。
- LanceDB 嵌入式，适合本地路径和 PyArrow 环境。
- RRF 避免不同分值直接归一。

## 为什么选择 Vue

页面以表格、卡片、分栏 Reader、SSE steps、图谱交互为主。Vue Composition API 与 Ant Design Vue 可以快速实现；动态路由 import 和 manual chunks 已控制主包。

## 依赖状态

2026-07-31：

- `pip check`：通过。
- `requirements-rag.txt` 已声明 `rapidocr>=3.9,<4` 与 `onnxruntime>=1.28,<2`。
- PyTorch 仍故意不锁，需按 CPU/CUDA host 选择。
- Node 24.11.0、npm 11.6.1；Vite 8.1.5 构建通过。

## 更换条件

| 当前技术 | 触发更换的条件 | 候选 |
|---|---|---|
| SQLite | 多副本、高写并发、集中服务、PITR | PostgreSQL |
| SQLite Job | 多机 Worker、优先级/队列治理 | Redis Streams/Celery/云队列 |
| 本地 PDF | 多节点、灾备、共享访问 | S3/MinIO |
| LanceDB local | 千万级向量、复杂租户与在线扩缩容 | pgvector/Qdrant/Milvus |
| 进程内限流/cache | 多实例 | Redis |
| SSE | 需要双向协作或可靠 resume | WebSocket/持久化 event log |
| tiktoken 估算 | 模型 tokenizer 差异影响 budget | Provider-specific tokenizer |

## 面试官可能提问与回答要点

1. **为什么不用全套云服务？** 当前本机科研场景重视隐私、便携和成本，外部只用于模型/搜索。
2. **为什么不用 Elasticsearch？** SQLite FTS 在当前规模足够，避免额外集群。
3. **为什么用 LanceDB？** 嵌入式、本地、与 Arrow/Python 结合，适合可重建投影。
4. **为什么不只用 Docling？** PyMuPDF 更适合快速预检和受控降级。
5. **技术选型最大的风险？** 完整 RAG 环境重且锁定不足，Docker/CPU/CUDA 复现仍未完成。

## 证据来源

- `backend/requirements.txt`
- `backend/requirements-rag.txt`
- `frontend/package.json`
- `backend/app/settings/config.py`
- `docs/ENVIRONMENT.md`
