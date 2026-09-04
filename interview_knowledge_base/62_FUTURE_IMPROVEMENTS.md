---
title: 未来改进路线
module: Roadmap
tags:
  - roadmap
  - acceptance
  - hardening
related:
  - 61_LIMITATIONS_TECH_DEBT.md
  - 54_TESTING.md
  - 42_TRADEOFFS_ALTERNATIVES.md
evidence:
  - docs/NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md
  - docs/EVALUATION_STATUS.md
  - docs/CURRENT_STATE.md
last_verified: 2026-07-31
---

# 未来改进路线

## 一句话结论

合理路线不是继续堆功能，而是先把业务数据、Golden、浏览器和故障验收闭环，再删除双轨兼容代码，随后补安全/观测/CI，最后根据量化瓶颈决定是否服务化。

## Phase 0：保护数据与建立基线

### 工作

1. 备份 `papers.db`、WAL、SHM 和 PDF 清单。
2. 在数据库副本演练 v007→v011 Migration。
3. 记录 file hash、schema version、row counts、FK check。
4. 固定当前 Git commit、Python/Node/模型配置和测试结果。

### 验收

- 原库不被测试修改。
- 副本 v011 schema validation 和 FK check 通过。
- 11 篇论文所有权、PDF path 与 Reader history 可核对。

## Phase 1：业务论文 canonical 回填

### 工作

1. `backfill_ingest` 默认 dry-run 列出候选。
2. 用户确认范围后 `--execute`。
3. 独立 Worker 消费并监控 Job。
4. 对每篇抽查 page/block/chunk/provenance/quality。
5. 验证 FTS 和 embedding config/count。

### 验收

- 每篇可读 PDF 有 active version 或稳定永久 error。
- active version 唯一。
- FTS/LanceDB count 一致。
- Reader 返回 canonical context mode 和合法 `[E#]`。

## Phase 2：Frozen Golden

### 工作

1. 用户审查 10 个 Golden Candidate。
2. 修订措辞/evidence 后复制为不可变 `frozen/golden_v1`。
3. 固定 parser/chunker/embedding/rerank/prompt/model/temperature/hash。
4. 运行 sparse、dense、rerank、Context、answer/citation。
5. 对不可回答、表格、公式、中英跨语言和多论文分别报告。

### 验收

- provenance 100%。
- 跨 scope 0。
- Recall@10 与 citation page 达阈值。
- 无关 Memory 注入和不可回答伪引用为 0。
- 不删除困难样例来达标。

## Phase 3：浏览器与故障 E2E

### 浏览器

- 注册/登录；
- Search SSE；
- 保存→PDF→Job→Worker→Reader；
- PDF.js canvas；
- Evidence 点击跳页；
- Memory draft/commit；
- Multi-paper citation；
- token 失效和 401。

### 故障

- 加密、损坏、截断、prompt injection PDF；
- Embedding/Rerank/LLM timeout；
- SQLite lock、Worker crash/lease recovery；
- 磁盘不足与 artifact/vector 写失败；
- SSE 客户端中断。

### 验收

错误码、前端提示、日志/trace、Job 状态和数据不变量一致。

## Phase 4：删除兼容代码

删除条件：

- 业务 canonical 回填完成；
- Frozen Golden 通过；
- 标准浏览器 E2E 通过；
- 回滚方案明确。

删除范围：

- 旧 Reader/PDF tool/fallback；
- 未版本化 opening/excerpt cache；
- 兼容 context mode/Prompt/parser；
- 只为旧行为存在的测试与表使用；
- 文档中双轨表述。

完成后全仓搜索旧符号为 0，并更新 `CURRENT_STATE/ARCHITECTURE/TESTING`。

## Phase 5：工程强化

### 类型与契约

- 按 Repository→Domain→Service→API 分批清 140 mypy errors。
- OpenAPI generated type 作为前端唯一 API contract。
- 错误 response 统一 `detail/error_code/request_id`。
- Prompt/Context/Retrieval 增加显式 version。

### 安全

- 标准 JWT library 或 server-side session。
- HttpOnly Secure SameSite cookie。
- 登录专用限流、token revoke/rotation。
- CSP/HSTS/TLS 与外部模型数据分类。

### 可观测

- JSON logs + OpenTelemetry。
- Prometheus metrics/Grafana。
- queue oldest age、RAG degradation、invalid citation、model cost 告警。
- Worker heartbeat readiness。

### CI/CD

- backend lint/type/test；
- frontend typecheck/build/test；
- Migration fresh/upgrade；
- 无外部费用的 T0/T1；
- 显式手动 T3/T4；
- browser E2E；
- secret scanning 与 dependency scan。

## Phase 6：按指标扩展

只有出现真实触发器时：

- SQLite→PostgreSQL；
- Job→外部队列；
- PDF/artifact→对象存储；
- LanceDB→服务化向量库；
- 进程 cache/rate limit→Redis；
- 单机 Worker→分布式 worker pool。

迁移前保留同一 Frozen Golden、权限测试与成本基线。

## 改进优先级

| 优先级 | 改进 | 价值 |
|---|---|---|
| P0 | 业务回填 + Frozen Golden + Browser E2E | 证明主链真实可用 |
| P0 | 删除双轨代码 | 消除双事实源 |
| P1 | 安全 token/cookie/data governance | 降低账户与数据风险 |
| P1 | Observability + CI | 持续可验证 |
| P1 | mypy/contract 收敛 | 降低维护成本 |
| P2 | ANN/queue/DB 服务化 | 仅在规模触发后 |
| P2 | Memory dense、多论文深推理 | 需先有质量数据 |

## 如果重新设计

仍会保留：

- deterministic authorization；
- canonical version；
- fact/projection 分离；
- Evidence Registry；
- user-confirmed Memory；
- bounded tools。

会更早加入：

- 标准 token/session；
- versioned Prompt/Eval manifest；
- structured observability；
- front/backend shared API types；
- 浏览器 E2E；
- 投影重建和数据备份 runbook。

## 面试官可能提问与回答要点

1. **下一步最重要是什么？** 不是新功能，是业务论文 canonical 回填和 Frozen Golden。
2. **什么时候删兼容代码？** 业务回填、Golden、浏览器三道门禁后。
3. **什么时候上微服务？** 多副本/并发/团队指标证明模块边界需要独立扩缩时。
4. **如何保证改进不退化 RAG？** 固定 Frozen Golden、模型、Prompt 与成本做前后对照。
5. **安全先改什么？** 标准 token + HttpOnly cookie + 登录限流 + 数据治理。

## 证据来源

- `docs/NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md`
- `docs/EVALUATION_STATUS.md`
- `docs/TESTING_AND_ACCEPTANCE.md`
- 本知识库 `61_LIMITATIONS_TECH_DEBT.md`
