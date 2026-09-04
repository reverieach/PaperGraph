---
title: 日志、追踪与监控
module: Observability
tags:
  - logging
  - request_id
  - health
  - trace
related:
  - 50_DEPLOYMENT_CONFIGURATION.md
  - 53_ERROR_HANDLING.md
  - 61_LIMITATIONS_TECH_DEBT.md
evidence:
  - backend/app/settings/logging.py
  - backend/app/api/main.py
  - backend/app/services/reader/reader_trace.py
  - backend/app/core/runtime_capabilities.py
last_verified: 2026-07-31
---

# 日志、追踪与监控

## 一句话结论

项目已有基础可观测性：stderr 分级日志、请求 ID、Reader request trace、Job 状态和 health/capability 探针；但没有结构化日志、集中采集、指标/告警、分布式追踪或成本仪表盘。

## 日志实现

`configure_logging`：

- logger scope：`app`；
- 输出：stderr；
- 格式：`LEVEL: logger: message`；
- level 来自 `LOG_LEVEL`，默认 INFO；
- 只在没有 handler 时添加；
- `propagate=False` 避免重复。

Service 使用 `logger.info/warning/error/exception` 记录：

- Search 阶段、来源失败、timeout、ranking method；
- PDF save timing；
- Ingest step、Job/Worker 错误；
- Multi-paper retrieval/LLM 失败；
- 未处理 API 异常。

## Request ID

`_MeaningfulActivityMiddleware`：

1. 接受调用方 `X-Request-ID`，否则生成 UUID hex。
2. 写入 `request.state.request_id`。
3. 响应 header 回传。
4. 统一错误体加入 `request_id`。

安全风险：当前接受任意客户端 request ID，未限制长度/字符；生产环境应 normalize 并同时生成 server trace ID。

## Reader Request Trace

`ReaderRequestTrace` 记录受限字段：

- request ID；
- context mode；
- query/retrieval stage 摘要；
- sparse/dense/rerank counts；
- degradation flags；
- tool name/status/code/elapsed/truncated；
- citation/invalid marker 统计。

设计目标是不记录 API key、完整 PDF 正文、完整 tool output 或跨用户内容。Trace 随响应 metadata/turn 保存的范围应保持最小化。

## Job 可观测性

`ingest_jobs` 本身是运行状态表：

- status；
- current_step；
- progress；
- attempt/max_attempts；
- lease owner/expiry；
- heartbeat；
- next attempt；
- error code/message；
- result version。

前端轮询可见 queued/running/degraded/failed，Reader 给永久错误差异化提示。当前没有独立 Job dashboard。

## 健康检查

| Endpoint | 内容 | 边界 |
|---|---|---|
| `/health` | service/version/KG metrics | 不证明外部模型可用 |
| `/health/capabilities` | 安全的 RAG readiness、embedded worker alive | 不泄露 key 或本地路径 |
| `/` | name/version/running/docs disabled | 最基础 liveness |
| strict preflight | 依赖、配置、可选 Schema | 启动前主动门禁 |

建议生产拆分 liveness/readiness：liveness 只看进程，readiness 检查 Migration、DB 写入能力、Worker freshness 与模型配置，不强制每次真实调用付费 API。

## 现有性能信号

- `save_papers` 记录 total/classify/db/memory/pdf timing。
- Search SSE final event 返回 `elapsed_ms`。
- Agent tool trace 有 `elapsed_ms`。
- Ingest report/评测报告记录整篇耗时。
- 构建报告记录 chunk size。

这些是离散日志，不是可聚合的 metric series。

## 应有但缺失的指标

| 域 | 建议指标 |
|---|---|
| API | request count、p50/p95/p99、4xx/5xx、in-flight |
| Search | source success/latency、recall candidates、fallback ratio |
| Ingest | queue depth、oldest age、claim conflicts、duration/page、OCR ratio、failure code |
| RAG | sparse/dense/rerank degradation、evidence count、invalid marker、no-hit |
| Models | token/cost/latency/retry/rate-limit |
| Storage | DB size/WAL size/lock waits、vector count mismatch、disk free |
| Frontend | SSE disconnect、PDF worker/load/render errors |

## 为什么这样设计

当前本机工具优先低运维，Python logging + SQLite Job 足以定位开发问题。随着能力变复杂，缺少跨请求聚合已成为验收与生产治理障碍。

## 当前限制

- 日志不是 JSON，无法稳定聚合字段。
- 多处 `except Exception` 只 debug/warning 或静默，降级口径不完全一致。
- 没有 OpenTelemetry、Prometheus、Sentry 或 APM。
- 无告警、SLO、日志轮转和保留策略。
- 健康检查没有外部 Worker heartbeat age 的独立 readiness。

## 面试官可能提问与回答要点

1. **如何定位一次 Reader 故障？** 用 X-Request-ID 关联 API log、Reader trace、retrieval degradation、tool event 和 persisted turn metadata。
2. **如何观察 Ingest？** 查询 Job status/step/attempt/lease/error 与 version quality/embedding status。
3. **现有监控够生产吗？** 不够，只有日志和 health，需要指标、告警与集中 trace。
4. **为什么不在日志记录全文？** 论文和 Memory 是用户数据，且体积大；只记录 ID、计数和脱敏状态。
5. **先补哪些指标？** queue oldest age、API/RAG p95、error/degradation ratio、模型成本、disk/DB lock。

## 证据来源

- `backend/app/settings/logging.py`
- `backend/app/api/main.py::_MeaningfulActivityMiddleware`
- `backend/app/services/reader/reader_trace.py`
- `backend/app/core/runtime_capabilities.py`
- `backend/app/services/papers/papers_library_service.py` timing log
