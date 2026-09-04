---
title: API 设计
module: Backend API
tags:
  - FastAPI
  - Pydantic
  - SSE
  - error contract
related:
  - 03_REQUEST_DATA_FLOW.md
  - 52_SECURITY_AUTH.md
  - 53_ERROR_HANDLING.md
evidence:
  - backend/app/api/main.py
  - backend/app/api/routes
  - backend/app/models/schemas.py
  - frontend/src/services/api
last_verified: 2026-07-31
---

# API 设计

## 一句话结论

PaperGraph API 是带统一认证、Pydantic Schema、请求 ID、用户作用域和 SSE 的 FastAPI 接口；复杂业务交给 Service，Route 负责协议适配和安全错误映射。

## 路由分组

2026-07-31 扫描到路由模块 38 个 handler，加根与健康检查共 41 个。

| Prefix | 主要端点 | 作用 |
|---|---|---|
| `/api/auth` | register/login/verify | 用户认证 |
| `/api/papers` | library、save、CRUD、PDF、ingest、daily、reading、graph | 论文资产 |
| `/api/ai` | reader opening/chat/history | 单篇 canonical Reader |
| `/api/research` | sessions/list/get/chat | 多论文研究 |
| `/api` Memory | drafts、commit、paper/user memories、stats/list/delete | 可确认 Memory |
| `/api/papers/search-agent` | `/stream` | 搜索 SSE |
| `/api/export` | `/json` | 用户作用域导出 |
| root | `/`、`/health`、`/health/capabilities` | 状态与安全能力探针 |

## 请求边界

1. 除 register/login 和 health 外，业务入口依赖 `require_user`。
2. Pydantic Field/Query 限制 ID、字符串长度、列表和分页。
3. Route 从 token 取得 `user_id`，不接受 body 传入 owner。
4. Route 调 Service 时显式传 `user_id`。
5. Repository 再按 user/paper/session/version 过滤。

## 响应与错误契约

统一异常 handler：

```json
{
  "success": false,
  "detail": "安全的用户提示或校验错误",
  "error_code": "AUTH_REQUIRED",
  "request_id": "..."
}
```

未知 500 使用 `message` 而非 `detail`，这是当前轻微契约不一致。状态码映射含 `BAD_REQUEST/AUTH_REQUIRED/FORBIDDEN/NOT_FOUND/CONFLICT/INVALID_REQUEST/RATE_LIMITED/INTERNAL_ERROR/SERVICE_UNAVAILABLE`。

所有响应 header 带 `X-Request-ID`。`safe_http_500` 记录原异常但对外只返回“服务暂时不可用”。

## SSE 搜索协议

`POST /api/papers/search-agent/stream` 返回 `text/event-stream`：

| Event type | 内容 |
|---|---|
| `status` | 当前阶段人类可读说明 |
| `tool_call` | understand_intent/search_pipeline 状态 |
| `deep:decompose` | 子问题 |
| `deep:round` | 轮次/并行数 |
| `deep:expand` | 新子问题 |
| `deep:rrf` | 融合候选数 |
| `deep:rank` | 精排 |
| `deep:synthesis` | 综述生成 |
| `error` | 安全错误消息 |
| `final` | elapsed/success |
| `final_result` | 完整 `SearchAgentResponse` |

服务端使用容量 128 的 AnyIO memory stream，设置 `Cache-Control: no-cache, no-transform` 和 `X-Accel-Buffering: no`。前端用 `fetch + ReadableStream` 解析，而不是 Axios。

## Ingest API

| Method | Path | 语义 |
|---|---|---|
| POST | `/api/papers/{paper_id}/ingest` | 幂等创建/复用 Job |
| GET | `/api/papers/{paper_id}/ingest` | 返回 rag_ready、active version、latest job |
| GET | `/api/papers/{paper_id}/ingest/{job_id}` | 用户作用域 Job 详情 |

重型工作不在 API 中执行。前端根据 Job 状态轮询。

## OpenAPI

- FastAPI `docs_url=None/redoc_url=None`，交互文档关闭。
- `/openapi.json` 仍可由 app schema 导出。
- `frontend/scripts/export-openapi.mjs` 使用显式 `PAPERGRAPH_PYTHON` 导出。
- `openapi-typescript` 生成 `frontend/src/types/openapi.ts`。
- 手写 API types 与 generated OpenAPI 同时存在，仍有漂移风险。

## 限流

`check_rate_limit` 是带 lock 的进程内滑动窗口：

- 默认 30 次/60 秒；
- Search 10 次/60 秒；
- Reader chat 15 次/60 秒；
- Reader opening 20 次/60 秒；
- key 为 user + client IP。

它不跨进程、不持久化、重启清空。

## 超时

| 入口 | 客户端/服务端 |
|---|---|
| Search SSE | 前端约 420 秒；后端墙钟约 420 秒 |
| Reader chat | 前端 240 秒；工具共享 28 秒，模型仍有自身 timeout |
| 普通 Axios | 默认 120 秒 |
| 多论文 chat | 客户端继承 Axios 120 秒 |
| PDF download/save | 服务按阶段设置外部 HTTP timeout |

## 为什么这样设计

FastAPI 的 dependency 和 Pydantic 适合把认证/参数验证前置；SSE 比 WebSocket 更适合单向长任务进度，且 Nginx 只需关闭 buffering。Service/Repository 分层使 Route 不直接承载 RAG 细节。

## 当前限制

- 无 API version prefix。
- 错误体 `detail/message` 不完全统一。
- SSE 没有 heartbeat、断线续传和客户端 resume token。
- 进程内限流无法支撑多副本。
- API docs UI 被关闭，开发发现性依赖脚本生成。

## 面试官可能提问与回答要点

1. **为什么 Search 用 SSE？** 流程长且只需服务端向客户端单向推送阶段，SSE 比 WebSocket 简单。
2. **如何保证 API 不越权？** token user_id + Route 传递 + Repository 再过滤。
3. **为什么 Ingest POST 不直接解析？** 解析耗时且需重试/恢复，API 只建持久化 Job。
4. **如何处理统一错误？** FastAPI exception handlers + request_id + machine-readable error_code。
5. **如何同步前后端类型？** 导出 OpenAPI 并生成 types，但手写 type 仍需收敛。

## 证据来源

- `backend/app/api/main.py`
- `backend/app/api/deps.py`
- `backend/app/api/routes/*.py`
- `frontend/src/services/api/*.ts`
