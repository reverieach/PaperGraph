# PaperGraph 产品与平台愿景

文档状态：`VISION`
更新日期：2026-07-28

本文描述长期方向，不表示相关能力已经实现。当前事实以 [CURRENT_STATE.md](docs/CURRENT_STATE.md) 为准。

## 1. 产品定位

PaperGraph 希望成为一个本地优先、可解释、可扩展的学术研究工作台，把以下流程连接起来：

```text
发现论文
→ 保存和管理
→ PDF 阅读与证据问答
→ 用户确认式 Memory
→ 多论文比较和研究整理
→ 可追溯的研究资产
```

目标用户是个人研究者、学生和小型研究团队。当前优先做好单机/单实例、数百篇论文规模，而不是提前建设大规模 SaaS。

## 2. 核心原则

- 数据自主：论文、Memory 和研究历史由用户控制；
- 证据优先：回答能回到真实 PDF Evidence；
- 用户控制 Memory：LLM 生成候选，用户决定永久写入；
- 确定性 Workflow：权限、任务、索引和持久化不交给 Agent；
- 可替换模型：Chat、Embedding、Rerank 通过 Provider 边界接入；
- 渐进式工程化：先正确性，再效果，再运维和高级能力；
- 不以 Agent 数量或新技术名词衡量产品价值。

## 3. 当前基础

已经具备：

- Vue/FastAPI/SQLite 本地应用；
- JWT 和核心资源 ownership；
- 多源论文搜索；
- 文献库、PDF Reader 和阅读历史；
- 手动确认式 Paper/User Memory；
- Canonical PDF/Chunk/Hybrid RAG 底层；
- 多论文研究界面；
- 知识图谱基础页面。

尚未具备：

- 自动稳定 Ingest 产品闭环；
- 可靠 Evidence Citation；
- Golden Test 和可观测性；
- 多论文全文 RAG；
- 生产级权限、迁移、备份和任务运维；
- 团队协作和共享空间；
- GraphRAG。

## 4. 路线

### 当前：质量收口

- 环境和启动标准化；
- 自动 Ingest、回填和状态；
- 中文/跨语言 Retrieval；
- Memory Retrieval 和 Context Builder；
- Evidence Registry/Citation Validator；
- Golden Test、Tracing、故障注入；
- 删除旧链路。

### 下一步：多论文研究

- per-paper recall；
- 跨论文 Rerank；
- 论文配额；
- 共识、冲突和研究空白；
- 证据级引用。

### 工程化

- 独立任务 Worker；
- Migration/备份/恢复；
- 结构化日志和指标；
- 外部依赖限流、熔断和成本；
- 更完整的多用户隔离和删除权；
- Docker/部署验收。

### 有证据后再评估

- 团队空间；
- 跨论文长期研究 Memory；
- 知识图谱增量构建；
- GraphRAG；
- 插件体系；
- 更大规模数据库和分布式任务。

## 5. 高级能力进入条件

只有满足以下条件才考虑 GraphRAG、Neo4j 或更多 Agent：

- 基础 Hybrid RAG 和 Citation 已通过固定评测；
- 有具体问题证明图检索能改善效果；
- 当前 SQLite/LanceDB/单 Worker 成为真实瓶颈；
- 多实例或团队协作需求已经出现；
- 新复杂度有测试、运维和回滚能力承担。

## 6. 成功标准

短期不以“功能数量”衡量，而以：

- 用户能稳定保存和读取论文；
- 任意回答能解释用了哪些 Evidence/Memory；
- 无跨用户、跨论文数据污染；
- 中英文问题有可测召回；
- 失败有明确降级；
- 项目能被另一台机器按文档重建；
- 旧架构持续减少。
