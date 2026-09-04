---
title: PaperGraph 请求与数据流
module: 架构
tags:
  - 请求流程
  - Ingest
  - Reader
related:
  - 02_SYSTEM_ARCHITECTURE.md
  - 13_FEATURE_CANONICAL_PDF_INGEST.md
  - 14_FEATURE_PAPER_READER.md
evidence:
  - backend/app/api/main.py
  - backend/app/services/papers/papers_library_service.py
  - backend/app/services/ingest/worker.py
  - backend/app/services/reader/paper_reader_service.py
last_verified: 2026-07-31
---

# PaperGraph 请求与数据流

## 一句话结论

PaperGraph 的关键数据流分为同步控制面和异步数据面：HTTP 完成认证、保存与排队，Worker 完成 canonical 建库，Reader 请求只消费 active version 并把最终可引用 Evidence 注册到本轮响应。

## 启动流程

```mermaid
sequenceDiagram
    participant OS as Process
    participant API as FastAPI lifespan
    participant CFG as Settings
    participant DB as SQLite
    participant W as Ingest Worker

    OS->>API: 启动 app.api.main:app
    API->>CFG: 从 backend/.env 加载并 validate_config
    API->>DB: run_migrations + schema validate
    alt RAG_INGEST_WORKER_ENABLED=true
        API->>W: 仅开发模式内嵌启动
    else 推荐方式
        OS->>W: 单独 python -m app.workers.ingest_worker
    end
    API-->>OS: /health 与 /health/capabilities 可用
```

配置错误或 Migration 失败会阻止启动，不会带着不完整 Schema 对外服务。

## 通用用户请求流程

1. 浏览器 API service 从 `localStorage.pg_token` 注入 `Authorization: Bearer ...`。
2. `_MeaningfulActivityMiddleware` 读取或生成 `X-Request-ID`。
3. `require_user` 验签并再次查询 `auth_users` 状态。
4. Route 用 Pydantic 验证请求，复杂入口调用进程内滑动窗口限流。
5. Service 接收已认证 `user_id`，Repository 再做资源作用域过滤。
6. 成功响应按具体 ResponseModel 返回；失败进入统一 HTTP/Validation/Unhandled handler。
7. 响应携带 `X-Request-ID`；错误体携带 `error_code` 和 `request_id`。

## PDF 保存与 canonical 入库流

```mermaid
sequenceDiagram
    participant UI as Library/Search UI
    participant API as POST /api/papers/save
    participant LIB as Paper Library Service
    participant DB as SQLite
    participant FS as PDF Files
    participant Q as ingest_jobs
    participant W as Independent Worker
    participant DOC as DocumentRepository
    participant V as FTS/LanceDB

    UI->>API: papers + download_pdfs
    API->>LIB: authenticated user_id
    LIB->>DB: upsert paper metadata
    LIB->>FS: stream to .part
    LIB->>FS: validate size + %PDF-, os.replace
    LIB->>DB: set local_pdf_path
    LIB->>Q: idempotent enqueue
    API-->>UI: saved ids + job references

    W->>Q: BEGIN IMMEDIATE claim + lease
    W->>FS: hash and read PDF
    W->>W: Docling/OCR -> canonicalize -> quality -> chunk
    W->>DOC: persist version/pages/blocks/chunks
    W->>V: build embedding/vector projection
    W->>DOC: atomically activate version
    W->>Q: succeeded or degraded
    UI->>API: poll GET /{paper_id}/ingest
    API-->>UI: progress/error/rag_ready
```

关键点：

- 已有合格目标文件会复用，不重复下载。
- 只有本地 PDF 存在才 enqueue。
- Job 和 version 都有幂等身份；Worker 可恢复过期 lease。
- Embedding 失败可保留 sparse 可用的 degraded active version，但解析/质量硬失败不会激活。

## canonical Reader Chat 流

```mermaid
sequenceDiagram
    participant UI as PaperReader.vue
    participant API as /api/ai/paper-reader/chat
    participant RS as PaperReaderService
    participant DB as DocumentRepository
    participant HR as HybridChunkRetriever
    participant EX as EvidenceExpander
    participant CP as DynamicContextBuilder
    participant AG as PaperAnalysisAgent
    participant TOOL as Canonical Reader Tool
    participant REG as EvidenceRegistry
    participant LLM as Chat Model
    participant VAL as CitationValidator

    UI->>API: paper_id + conversation_id + user_message
    API->>RS: authenticated user_id
    RS->>DB: verify owned paper + active version
    RS->>DB: load server-side recent history
    RS->>HR: user/paper/query
    HR->>DB: unicode61 + trigram sparse
    HR->>HR: optional dense + weighted RRF + rerank
    HR-->>RS: scoped hits + degradation reasons
    RS->>EX: parent/neighbor expansion
    EX->>DB: active-version scoped hydrate
    RS->>CP: evidence + metadata + memory + history
    CP->>REG: register surviving canonical evidence
    RS->>AG: request-scoped context and registry
    AG->>LLM: system + ContextPackage + tools
    opt LLM calls tool
        LLM->>TOOL: validated JSON args
        TOOL->>DB: scoped query
        TOOL->>CP: rehydrate UID, re-budget
        CP->>REG: register new surviving evidence
        TOOL-->>LLM: untrusted JSON + updated evidence text
    end
    LLM-->>AG: answer with [E#]
    AG->>VAL: validate markers against registry
    VAL-->>RS: cleaned reply + canonical citations
    RS->>DB: persist server-side exchange + trace
    RS-->>UI: reply + citations + degradation flags
```

## 多论文请求流

1. 用户从自己的文献库选择 2–8 篇论文，创建 `research_session`。
2. Repository 固化 `research_session_papers`，后续 chat 不接受客户端临时扩大范围。
3. Service 查找所选论文的 active version 集合。
4. Hybrid Recall 使用 `paper_ids` 列表作为硬 scope。
5. 每篇先选一个 anchor，再允许每篇最多两个，防止单篇垄断。
6. 扩展、ContextPackage、Evidence Registry 和 Citation Validator 与单篇共用原则。
7. 只有摘要的论文只作为明确标记的背景，不能生成 PDF citation。

## 学术搜索 SSE 流

```mermaid
flowchart LR
    Q["自然语言 query"] --> I["SearchAgent intent JSON"]
    I --> P["ResolvedSearchPlan"]
    P --> R["多源并行 recall"]
    R --> D["normalize / dedupe / relevance guard"]
    D --> K["LLM rank 或 semantic fallback"]
    K --> SSE["SSE progress + final_result"]
    P -->|deep_search=true| DS["2–4 子问题 → 并行轮次 → RRF → LLM rank → synthesis"]
    DS --> SSE
```

SSE 队列容量为 128，服务端总墙钟约 420 秒；前端 `fetch` 流逐块解析 `data:` 并更新步骤 UI。

## 异常与降级流

```mermaid
flowchart TD
    X["阶段执行"] --> T{错误类型}
    T -->|请求/权限| H["4xx + error_code + request_id"]
    T -->|永久 PDF 输入错误| P["Job failed；不重试；不激活版本"]
    T -->|可重试 Worker 错误| B["有限 attempt + next_attempt_at + lease recovery"]
    T -->|Embedding/Rerank 不可用| D["明确 degradation flag；保留 sparse/已构建事实"]
    T -->|模型/外部源超时| F["有界 fallback 或 5xx/504"]
    T -->|未知异常| G["logger.exception + 通用 500"]
```

代表性永久错误：`PDF_FILE_MISSING`、`PDF_HASH_FAILED`、`PDF_ENCRYPTED`、`PDF_INVALID`、`QUALITY_GATE_FAILED`。降级不等于伪装成功；对外返回 `context_mode`、`degradation_flags` 或 Job `error_code`。

## 数据写入与查询的一致性

| 操作 | 一致性策略 |
|---|---|
| Paper 保存 | SQLite transaction；PDF 成功后记录相对路径 |
| PDF 文件 | `.part` + 头/大小校验 + `os.replace` |
| Job claim | `BEGIN IMMEDIATE`，lease owner/expiry/heartbeat |
| Version 写入 | page/block/chunk 同一受控阶段，激活单独原子切换 |
| FTS | external-content FTS + trigger，可 rebuild |
| LanceDB | version 级 delete/add/count verify，失败清理 |
| Memory commit | `Idempotency-Key` + active content unique index + transaction |
| Reader history | 服务器持久化，客户端历史不作为 canonical Reader Prompt 事实源 |

## 面试官可能提问

### 为什么要把 Ingest 和 Reader 请求拆开？

Reader 需要稳定 active version，Ingest 耗时且可能失败。用版本和 Job 把二者解耦后，Reader 不会读到半写入 Chunk，HTTP 也不被 OCR/Embedding 阻塞。

### 工具结果为什么还要回表？

LLM 可伪造工具输出里的文本或 UID，且工具结果可能超预算。只有 Repository 返回的 scoped chunk 经过 ContextPackage 后才进入 Evidence Registry。

### 降级怎么避免掩盖故障？

返回机器可读 `degradation_reasons/error_code/context_mode`，日志记录错误类型；永久输入错误不重试，模型投影失败与源数据失败分开。

## 证据来源

- `backend/app/services/papers/papers_library_service.py::save_papers`
- `backend/app/core/pdf_download.py::download_paper_pdf_to_path`
- `backend/app/services/ingest/worker.py::IngestWorker`
- `backend/app/services/reader/paper_reader_service.py::process_chat`
- `backend/app/agents/support/canonical_reader_tools.py`
- `backend/app/api/routes/search.py::search_agent_chat_stream`
