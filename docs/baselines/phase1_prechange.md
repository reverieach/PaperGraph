# 第一阶段改造前基线

> 文档状态：`HISTORICAL_BASELINE`
>
> 本文件只用于比较第一阶段改造前后的差异，不代表当前环境、测试数量或数据库状态。当前基线见 [CURRENT_STATE.md](../CURRENT_STATE.md) 和 [TESTING_AND_ACCEPTANCE.md](../TESTING_AND_ACCEPTANCE.md)。

记录日期：2026-07-27

本基线来自改造前实际命令和代码检查，用于和第一阶段完成状态对比。测试与迁移均使用临时数据库或真实数据库副本，没有对 `backend/data/papers.db` 执行迁移。

## 环境

| 工具 | 版本 |
| --- | --- |
| Python | 3.11.9 |
| Node.js | 24.11 |
| npm | 11.6.1 |
| SQLite | 3.45.1 |

## 改造前验证

| 检查 | 结果 |
| --- | --- |
| 后端既有测试 | 37 passed |
| 前端 Vite build | 通过 |
| 前端 `vue-tsc --noEmit` | 失败，12 个类型错误 |
| Memory 首次写入 | 旧 schema/SQL/参数契约不一致，不能作为可靠链路 |
| Fresh/Legacy migration | 无统一 runner，未形成可重复验收 |
| 用户隔离 | 缺失 Token 可降级共享用户，Paper/History/Memory 未统一绑定 owner |
| Windows `npm ci` | Unix shell postinstall 构成跨平台阻塞 |

## 真实旧库结构观察

对真实数据库只做了只读结构检查，发现：

- `users` 是旧文档存储表，不是认证用户表；
- `memories` 同时存在 `id`、`memory_id`、`properties`、`timestamp` 等旧契约字段；
- `paper_reader_turns` 没有完整的 `user_id + conversation_id` 作用域；
- 论文和多个 Service 表由不同模块运行时建表；
- 真实库当时论文和 Memory 记录数均为 0。

第一阶段迁移测试使用临时 fresh DB、最小 legacy fixture 和真实库临时副本完成，未覆盖或改写仓库真实数据文件。
