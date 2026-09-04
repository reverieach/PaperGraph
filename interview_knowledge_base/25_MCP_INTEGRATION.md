---
title: MCP 集成状态
module: External Search
tags:
  - MCP
  - arxiv-mcp-server
  - opt-in
related:
  - 11_FEATURE_ACADEMIC_SEARCH.md
  - 50_DEPLOYMENT_CONFIGURATION.md
  - 99_UNCONFIRMED_QUESTIONS.md
evidence:
  - backend/app/core/search/sources/mcp.py
  - backend/app/core/search/paper_searcher.py
  - backend/app/settings/config.py
  - backend/tests/test_mcp_source.py
last_verified: 2026-07-31
---

# MCP 集成状态

## 一句话结论

项目存在可选的 `arxiv-mcp-server` stdio 搜索适配器，但默认关闭、spawn-per-call、与原生 arXiv 能力重叠，且当前 requirements 没有声明 MCP Python 依赖，因此只能称为架构性适配和测试覆盖，不能称为当前主搜索来源。

## 业务目标

验证学术搜索 source 接口能否接入 MCP server，使未来 Semantic Scholar、PubMed 等来源可按相同 Paper 模型进入 Search Pipeline。

## 真实实现

`backend/app/core/search/sources/mcp.py`：

1. 根据当前 Python 环境或 `MCP_ARXIV_COMMAND` 解析 server command。
2. 为每次查询创建 stdio MCP client/session。
3. 调用 `search_papers` tool。
4. 解析返回 content 中的结构化论文结果。
5. 映射为 PaperGraph `Paper(source="mcp")`。
6. 交给 `PaperSearcher` 的统一 normalize/dedupe 流程。

配置：

| 配置 | 默认 | 作用 |
|---|---|---|
| `MCP_ARXIV_ENABLED` | false | 是否启用 |
| `MCP_ARXIV_COMMAND` | 空 | server 可执行文件 |
| `MCP_ARXIV_STORAGE_PATH` | data dir 下默认目录 | server 存储路径 |

## 调用流程

```mermaid
flowchart LR
    SEARCH["PaperSearcher"] --> FLAG{"MCP enabled?"}
    FLAG -->|no| SKIP["Skip source"]
    FLAG -->|yes| SPAWN["spawn arxiv-mcp-server"]
    SPAWN --> SESSION["stdio ClientSession"]
    SESSION --> TOOL["call_tool(search_papers)"]
    TOOL --> MAP["map to Paper(source=mcp)"]
    MAP --> PIPE["normalize + dedupe + rank"]
```

## 与 Function Calling 的区别

| 项目 | MCP Source | Reader Function Calling |
|---|---|---|
| 协议 | 外部 server + stdio MCP | OpenAI-style tool calls |
| 用途 | 学术论文搜索来源 | 当前 PDF 内按需取证 |
| 作用域 | 公开搜索 query | user/paper/active-version |
| Evidence | 只产生论文元数据 | canonical tool 可经回表成为 `[E#]` |
| 默认状态 | 关闭 | canonical Reader 中启用 |

## 异常处理

- 未启用：不尝试 import/启动。
- command 不存在或 session 失败：记录/返回空 source，不阻断其他学术源。
- 单条返回格式异常：跳过该条。
- server 启动成本约 0.5 秒，代码选择每次 spawn 以避免跨 event loop 复用问题。

## 为什么这样设计

代码注释明确认为当前 MCP arXiv 与原生 arXiv 重叠，其价值主要是“pluggable MCP source”。spawn-per-call 牺牲延迟，换取 event loop 与连接生命周期简单。

## 技术取舍

| 方案 | 优点 | 缺点 | 当前采用 |
|---|---|---|---|
| 原生 arXiv HTTP | 直接、成熟 | 每个源单独适配 | 主路径采用 |
| MCP spawn-per-call | 标准工具协议、易扩源 | 启动慢、依赖额外运行时 | 可选 |
| 常驻 MCP session pool | 延迟低 | loop、重连、并发复杂 | 未采用 |

## 当前限制

- `backend/requirements.txt` 与 `requirements-rag.txt` 未列出 `mcp` 包。
- 未看到 MCP 真实 server 的端到端运行报告；现有测试主要是 adapter/mock 边界。
- `ResolvedSearchPlan` 的常规 allowed sources 不包含 MCP，启用路径需要进一步确认完整可达性。
- 与原生 arXiv 重复，没有独立质量/成本收益证据。

## 可改进方向

近期：

- 明确 optional dependency extra 和 preflight capability。
- 增加真实 opt-in smoke，记录 command/version/timeout。
- 证明 SearchPlan 到 `PaperSearcher` 的完整启用路径。

长期：

- 只有新增有差异价值的来源时再扩 MCP。
- 若 QPS 上升，设计常驻连接池、隔离进程与熔断。

## 面试官可能提问与回答要点

1. **项目是否用了 MCP？** 有可选 arXiv adapter 代码和测试，但默认关闭，不能说主流程依赖 MCP。
2. **为什么还保留原生 arXiv？** MCP source 能力重叠且启动成本高，原生 adapter 更直接。
3. **MCP 结果能当 PDF Evidence 吗？** 不能，它只是公开论文元数据来源。
4. **如何让 MCP 生产可用？** 锁依赖、preflight、真实 smoke、连接生命周期、timeout/metrics。
5. **MCP 的架构价值是什么？** 把新搜索源标准化为可插拔 server/tool，而不是改动核心 Pipeline。

## 证据来源

- `backend/app/core/search/sources/mcp.py`
- `backend/app/core/search/paper_searcher.py`
- `backend/app/settings/config.py::mcp_arxiv_*`
- `backend/tests/test_mcp_source.py`
