# PaperGraph 文档索引

更新日期：2026-07-28
当前代码基线：当前工作树（Phase 2 Ingest/Hybrid/Context/Evidence/Trace 与多论文 canonical RAG 基础实现）
当前阶段：Phase 1 已完成；Phase 2 已完成 Ingest/隔离/双语 Retrieval/Memory Retrieval/Context/Evidence 和多论文 SQLite 回归基础，正在进行业务论文、Frozen Golden、浏览器 E2E 与旧链路收口

## 1. 从哪里开始

| 读者/任务 | 首选文档 |
| --- | --- |
| 第一次了解项目 | [根 README](../README.md) |
| 查看当前真实完成度 | [CURRENT_STATE.md](./CURRENT_STATE.md) |
| 查看已执行的 RAG 评测与 Golden 边界 | [EVALUATION_STATUS.md](./EVALUATION_STATUS.md) |
| 理解模块和调用链 | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| 配置环境、路径和启动命令 | [ENVIRONMENT.md](./ENVIRONMENT.md) |
| 运行测试和阶段验收 | [TESTING_AND_ACCEPTANCE.md](./TESTING_AND_ACCEPTANCE.md) |
| 执行下一阶段改造 | [NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md](./NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md) |
| AI Agent/开发协作规则 | [AGENTS.md](../AGENTS.md) |

## 2. 文档状态定义

| 状态 | 含义 |
| --- | --- |
| `CURRENT` | 当前事实源；代码变化后必须同步更新 |
| `EXECUTION` | 当前可执行计划；包含任务顺序和验收 |
| `HISTORICAL` | 带日期的历史基线/报告；不表示当前状态 |
| `VISION` | 长期愿景；不表示已经实现 |
| `EXPERIMENTAL` | 未完成当前环境验收的可选方案 |

## 3. 当前事实源

### `CURRENT`

- [CURRENT_STATE.md](./CURRENT_STATE.md)：能力、数据、测试和问题；
- [EVALUATION_STATUS.md](./EVALUATION_STATUS.md)：隔离 PDF/Silver/Golden Candidate/SciFact 的真实运行状态和禁止事项；
- [ARCHITECTURE.md](./ARCHITECTURE.md)：系统结构、Agent、Memory、RAG 和调用链；
- [ENVIRONMENT.md](./ENVIRONMENT.md)：两个 Python 环境、配置、存储路径和运行方式；
- [TESTING_AND_ACCEPTANCE.md](./TESTING_AND_ACCEPTANCE.md)：测试分层、Golden Test 和门禁；
- [SEARCH_OPTIMIZATION.md](../SEARCH_OPTIMIZATION.md)：外部论文搜索管线；它与 PDF 内部 RAG 是两套检索链路。

### `EXECUTION`

- [NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md](./NEXT_STAGE_HARDENING_AND_QUALITY_EXECUTION_GUIDE.md)：当前唯一详细执行计划；
- [PROJECT_UPGRADE_AND_REPAIR_GUIDE.md](./PROJECT_UPGRADE_AND_REPAIR_GUIDE.md)：总体阶段总览和当前入口；
- [PHASE_2_RAG_CONTEXT_MEMORY_UPGRADE_PLAN.md](./PHASE_2_RAG_CONTEXT_MEMORY_UPGRADE_PLAN.md)：第二阶段实现状态映射，不再使用最初的全 TODO 版本。

### `HISTORICAL`

- [PHASE_1_FOUNDATION_REPAIR_GUIDE.md](./PHASE_1_FOUNDATION_REPAIR_GUIDE.md)：第一阶段实施计划；
- [PHASE_1_COMPLETION_REPORT.md](./PHASE_1_COMPLETION_REPORT.md)：第一阶段完成时的结果；
- [PHASE_1_ACCEPTANCE_REPORT.md](./PHASE_1_ACCEPTANCE_REPORT.md)：2026-07-27 真实 PDF/API/UI 验收；
- [baselines/phase1_prechange.md](./baselines/phase1_prechange.md)：第一阶段改造前基线。

历史文档中的测试数量、数据库条数、环境和“当前”均以文档日期为准。需要判断今天的状态时只能使用 `CURRENT` 文档和真实运行结果。

### `VISION` / `EXPERIMENTAL`

- [PLATFORM_VISION.md](../PLATFORM_VISION.md)：产品长期愿景；
- [README-Docker.md](../README-Docker.md)：Docker 配置说明，目前未纳入本机验收主路径。

## 4. 唯一事实规则

如果文档出现冲突：

```text
真实代码/Schema/运行结果
> CURRENT_STATE
> ARCHITECTURE / ENVIRONMENT / TESTING
> 当前执行计划
> 历史报告
> 愿景和宣传材料
```

禁止：

- 在多个 README 维护不同快速开始；
- 把旧测试数写成当前测试数；
- 把文件存在写成功能可用；
- 把外部论文搜索的规则评分叫作 PDF 向量 RAG；
- 把当前软页码引用写成可靠证据引用；
- 把已实现但尚未完成业务论文/Golden/浏览器验收的多论文 canonical RAG 写成最终生产能力；
- 把多用户 ownership 基础写成生产级多租户。

## 5. 文档更新清单

每次行为变更后检查：

1. 当前能力状态是否变化；
2. 环境、依赖或绝对路径是否变化；
3. API、流程图或 Agent 职责是否变化；
4. 测试数量、warning 或 Golden 指标是否变化；
5. 新旧代码是否已经切换和删除；
6. 下一阶段工作包状态是否需要更新；
7. 根 README 是否仍能准确描述用户可见能力。
