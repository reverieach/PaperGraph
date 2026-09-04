# PaperGraph 第一阶段完成报告

> 文档状态：`HISTORICAL_REPORT`
>
> 本报告记录 2026-07-27 第一阶段完成时的环境与结果，其中测试数量、环境选择和数据状态都是历史快照，不可当作当前基线。当前状态请查看 [CURRENT_STATE.md](./CURRENT_STATE.md)、[ENVIRONMENT.md](./ENVIRONMENT.md) 和 [TESTING_AND_ACCEPTANCE.md](./TESTING_AND_ACCEPTANCE.md)。

完成日期：2026-07-27
分支：`codex/phase1-foundation`

## 1. 结论

第一阶段的 P0 基础闭环已经完成：空库可以启动，旧结构可以迁移，认证与核心资源按真实用户隔离，阅读历史可持久化，Memory 改为“用户点击总结 → LLM 生成草稿 → 用户编辑/勾选 → 幂等确认写入”，前后端类型和生产构建通过。

本阶段没有加入向量检索、标准论文 Chunk RAG、GraphRAG 或持久化任务队列。这些仍属于第二、三阶段。

## 2. 已完成工作包

| 工作包 | 状态 | 实现结果 |
| --- | --- | --- |
| WP0 安全基线 | VERIFIED | 建立 Git 分支、记录环境和改造前结果；真实 DB 未用于写入测试 |
| WP1 统一连接 | VERIFIED | canonical DB 路径统一 WAL、foreign keys、busy timeout、事务回滚 |
| WP2 Migration | VERIFIED | 4 个版本迁移、checksum、fresh/legacy/idempotent/rollback/schema validation |
| WP3 Auth/Ownership | VERIFIED | `auth_users`、强制 Bearer、必需随机 Secret、Paper/PDF/History/Memory/Export 用户隔离 |
| WP4 Reader History | VERIFIED | `reader_conversations` 与 turn 绑定 user + paper；问答双 turn 原子写入 |
| WP5 Memory | VERIFIED | 单一 canonical repository、草稿/确认/幂等/去重/软删除/重启召回和审核 UI |
| WP6 Export | VERIFIED | 显式字段、作者顺序、active confirmed Memory、全链路用户过滤 |
| WP7 错误契约 | VERIFIED | request ID、稳定 error code、核心写失败不再静默成功、500 不泄露内部异常 |
| WP8 前端环境 | VERIFIED | Windows `npm ci`、跨平台 PDF.js postinstall、OpenAPI 生成、typecheck + build |
| WP9 自动化测试 | VERIFIED | 迁移、鉴权、隔离、历史、Memory、Export 和真实 API 集成测试 |
| WP10 并发状态收口 | VERIFIED | Reader Agent 请求级实例；搜索意图缓存与单例初始化加锁；缓存对象深拷贝 |

## 3. 核心不变量

当前代码保证：

```text
Token user_id
→ 解析当前用户的 paper_id
→ 解析该用户与论文下的 conversation_id
→ LLM 只生成 Memory 草稿
→ 客户端不能指定最终 user_id / scope_id
→ 用户确认后 Repository 在事务内决定 scope 并写入
```

- 缺失或无效 Token 返回 401，不再降级共享用户；
- 同一 DOI 可以由不同用户分别保存；
- 论文、PDF、阅读历史、Memory、图查询和导出均按 owner 过滤；
- KG 候选召回不再跨用户读取论文；
- Web 搜索片段标记为 `source_type=web`，不能作为 PDF 页码证据；
- Agent 自动 Memory 写入被禁用，旧 Memory 文件仅保留只读/报错适配，不再执行旧 SQL。

## 4. 实际验收结果

| 命令/场景 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe -m pip check` | 通过，无破损依赖 |
| `.\.venv\Scripts\python.exe -m pytest -q` | 46 passed（最终验收以末次命令输出为准） |
| `.\.venv\Scripts\python.exe -m compileall -q app tests` | 通过 |
| Phase 1 核心模块 mypy | 通过 |
| `npm ci` | Windows 通过 |
| `npm run openapi:gen` | 通过 |
| `npm run build` | typecheck + Vite 8 production build 通过 |
| `npm audit --omit=dev` | 0 vulnerabilities |
| Fresh DB 真实进程启动 | `/health=healthy` |
| 未认证真实进程请求 | 401 |
| 注册、Bearer、空文献库 | 通过 |
| Legacy fixture | 迁移和 schema validator 通过 |
| Migration 注入失败 | 完整 rollback，不记录假成功 |

第一阶段当时使用 Windows/Conda 环境时必须显式使用 `backend/.venv`；直接执行 PATH 中的
`python` 可能命中未安装项目依赖的 Anaconda 解释器。

测试覆盖：

- fresh DB、legacy DB、重复迁移、checksum 篡改、迁移失败回滚；
- WAL、foreign keys、busy timeout；
- JWT 注册、登录、篡改 token、跨用户 Paper CRUD；
- 同 DOI 的多用户隔离；
- opening 去重、turn 顺序、跨用户 history 404；
- Memory 无效 evidence、草稿不落正式表、确认写入、内容 hash 去重、幂等、软删除、重启召回；
- API error contract、Memory API、Export 字段/作者顺序/用户隔离；
- Web/PDF 来源边界。

## 5. 数据安全说明

没有对仓库真实 `backend/data/papers.db` 执行正式迁移。正式首次启动前仍应：

1. 停止旧进程写入；
2. 备份 DB、WAL、SHM；
3. 在副本运行迁移和导出检查；
4. 确认 legacy 数据统一进入禁用登录的 `__legacy__` owner；
5. 再启动新版本。

## 6. 已知非阻塞项

以下不是本阶段 P0，但需要保留在后续计划中：

- 全量 `mypy app` 仍有 133 个既有类型错误，集中在 LLM、搜索源、retrieval、daily 和 reader 辅助模块；本阶段新增的 infrastructure/domain/repository/memory/auth/history 类型检查已通过；
- 完整 `npm audit` 仍报告 OpenAPI 本地生成工具链的 4 个 high 开发依赖问题；生产依赖审计为 0，生成输入是本地可信 JSON，等待上游 `openapi-typescript` 升级；
- 前端 Ant Design 主 chunk 约 710 kB，构建警告仍在，属于性能优化；
- 搜索 SSE 尚无 heartbeat/断点恢复，线程中的同步第三方调用也不能被真正强制终止；
- daily cache、opening cache、PDF excerpt cache 等派生缓存仍有独立的小型 SQL 适配，正式业务事实表已经统一迁移；
- 尚未实现标准论文 Chunk、Embedding、BM25/向量混合召回、Reranker 和可靠页码引用。

## 7. 第二阶段入口

第二阶段可以在当前基础上开始，但应保持以下顺序：

1. 定义 `DocumentChunk` 和页码/章节元数据；
2. 建立可重复的 PDF 解析与 Chunk 测试集；
3. 先实现 SQLite FTS5/BM25 召回；
4. 再接 Embedding 与向量索引；
5. 做 hybrid fusion、rerank、token-budget Context Builder；
6. 用固定论文问答集验收召回率和引用正确率。
