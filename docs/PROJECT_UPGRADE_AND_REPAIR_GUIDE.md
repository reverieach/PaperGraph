# PaperGraph 总体升级与修复路线

文档状态：`EXECUTION`
更新日期：2026-07-28

本文件只提供阶段总览。当前详细实施步骤以 [下一阶段执行指南](./NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md) 为准。

## 1. 当前基线

Phase 1 已完成：

- 统一 SQLite 连接和版本化 Migration；
- JWT 和核心资源 user ownership；
- Reader History；
- 用户确认式 Memory；
- Export 和错误契约；
- 前端登录、PDF 和基础 UI；
- 请求级 Reader Agent；
- 第一阶段自动化和真实 PDF/UI 验收。

Phase 2 已实现底层：

- canonical document/version/page/block/chunk；
- Docling/PyMuPDF adapter；
- Parse Quality Gate；
- parent/child chunk；
- persisted ingest job/worker 类；
- SQLite FTS5；
- Embedding/LanceDB；
- Academic Query Planner、unicode61/trigram 双 FTS、RRF/task-aware Rerank；
- scope-safe MemoryRetriever、Memory dual FTS、配额与过期/supersede 基础；
- ContextPackage、TokenCounter、task policy 与服务端 History；
- canonical RAG 的 Evidence Registry/Citation Validator；
- Reader 渐进式 RAG 接入。
- session-scoped 多论文 Hybrid Recall、anchor 均衡、Evidence Expansion、ContextPackage 与 cross-paper citation 校验基础。

但当前业务数据库没有 document versions/chunks/vectors，因此产品仍未实际使用新 RAG。

## 2. 总原则

```text
正确性与隔离
→ 真实产品链路
→ 检索/Memory/Context 效果
→ 引用与评测
→ 删除旧实现
→ 多论文和高级能力
```

- 不新增无必要 Agent；
- 不自动永久写入 Memory；
- 不让 LLM 决定权限或数据作用域；
- 不用 Prompt 掩盖解析/召回缺陷；
- 不在单论文 RAG 验收前做 GraphRAG；
- 不长期保留两套 Reader/Memory 事实源。

## 3. 当前 P0-S2/P1

### 阶段门禁

1. Gate 0/WP1 基础实现已完成：权威解释器 preflight、自动 enqueue、独立 Worker 和 Reader Job 状态已落地；
2. 真实业务库尚未完成 canonical document 的端到端回填；隔离评测语料已完成 16 篇/419 页 PDF 的 canonical/FTS/vector 入库。
3. 已有 Silver v2（24 例/26 锚点）questions/qrels/runner，且 sparse 与受限 dense/rerank 对照已通过；Golden Candidate 待审查，尚无 Frozen Golden 或 answer/citation 门禁。

### P1

1. [已完成基础修复] v009 已将负反馈改为 user-scoped TTL signal，取消 LLM 自动长期偏好；
2. [已完成基础修复] Reader/Daily/KG/Feedback 的持久化 schema 已迁入 Migration，legacy 无 owner 表归档；
3. [已完成 canonical 路径基础修复] Evidence Citation 已绑定本轮 ContextPackage；legacy `[pN]` 兼容路径和旧 Reader tools 尚待删除；
4. RAG package failures 已记录 machine-readable degradation；前端已显示 context mode/degradation，服务端已记录脱敏 request trace；浏览器 E2E 和指标聚合尚未完成；
5. 单机 SQLite Worker 还没有跨主机调度、指标与告警；真实语料 E2E 还未验收。

完整问题表见当前执行指南。

## 4. 阶段路线

### 阶段 A：环境、入库和正确性

- 标准化 RAG 环境和 capability；
- 自动 enqueue；
- 独立 Worker/lease/retry/recovery；
- 前端 Ingest 状态；
- [已完成基础实现] runtime DDL → Migration；
- [已完成基础实现] 负反馈用户隔离。

### 阶段 B：检索、Memory 和 Context

- [已完成基础实现] Academic Query Planner；
- [已完成基础实现] unicode61 + CJK trigram + Dense 接口；
- [已完成基础实现] Query/Document Embedding；
- [已完成基础实现] task-aware Rerank；
- parent/neighbor expansion；
- [已完成基础实现] structured MemoryHit；
- Memory vector/Golden 校准；
- [已完成基础实现] single Token Budget ContextPackage。

### 阶段 C：引用、评测和收口

- [已完成 canonical 路径基础实现] Evidence Registry；
- [已完成 canonical 路径基础实现] Citation Validator；
- canonical Reader tools；
- Trace、timeout、SSE；
- Golden Test；
- 删除旧 Reader/Memory。

### 阶段 D：多论文

- [已完成基础实现] session scope、active canonical version、anchor 均衡、Evidence Expansion、evidence-based comparison 与 metadata-only 显式降级；
- 用业务论文/Frozen Golden 验证 per-paper recall coverage；
- 仅在 Golden 显示必要时再增加真正的 per-paper recall quota 或 cross-paper rerank；
- 完成浏览器 citation 交互与跨用户 E2E。

## 5. 暂缓

- GraphRAG；
- Neo4j；
- 自由 Agent-to-Agent；
- 自动长期 Memory；
- Kubernetes/分布式消息系统；
- 大规模插件平台；
- 无真实需求的团队 SaaS。

## 6. 验收入口

- 当前事实：[CURRENT_STATE.md](./CURRENT_STATE.md)
- 架构：[ARCHITECTURE.md](./ARCHITECTURE.md)
- 环境：[ENVIRONMENT.md](./ENVIRONMENT.md)
- 测试：[TESTING_AND_ACCEPTANCE.md](./TESTING_AND_ACCEPTANCE.md)
- 详细执行：[NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md](./NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md)
