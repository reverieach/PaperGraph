# PaperGraph 外部论文搜索管线

文档状态：`CURRENT`
更新日期：2026-07-28

## 1. 作用范围

本文描述“从外部学术数据源找论文”的搜索链路，不描述已下载 PDF 内部的论文级 RAG。

两者必须区分：

| 链路 | 目标 | 主要数据 |
| --- | --- | --- |
| 外部论文搜索 | 找到候选论文 | 标题、摘要、作者、venue、年份、外部元数据 |
| PDF RAG | 回答某篇/多篇论文内容 | Page/Block/Chunk/Embedding/Evidence |

`semantic_scoring.py` 当前是词法、N-gram 和元数据启发式评分，不是向量 Embedding。

## 2. 当前流程

```mermaid
flowchart LR
    Q["自然语言问题"] --> INTENT["IntentParser / SearchAgent"]
    INTENT --> PLAN["ResolvedSearchPlan"]
    PLAN --> SRC["arXiv / OpenAlex / DBLP / Tavily / optional MCP"]
    SRC --> NORM["normalize / dedupe"]
    NORM --> FILTER["filters / relevance guard"]
    FILTER --> RANK["LLM rank or semantic fallback"]
    RANK --> SSE["SSE progress and results"]
```

主要实现：

- `backend/app/agents/search_agent.py`
- `backend/app/services/retrieval/search_plan.py`
- `backend/app/services/retrieval/search_pipeline.py`
- `backend/app/services/retrieval/query_enhancement.py`
- `backend/app/services/retrieval/semantic_scoring.py`
- `backend/app/services/retrieval/rrf_fusion.py`
- `backend/app/core/search/sources/`
- `backend/app/api/routes/search.py`

## 3. 已实现能力

### 查询与计划

- 自然语言意图解析；
- 作者、venue、年份和关键词约束；
- 学术缩写/术语扩展；
- 查询特异性分析；
- 宽泛/具体查询的召回上限调整；
- `ResolvedSearchPlan` 作为检索层确定性输入。

### 多源召回

- arXiv；
- OpenAlex；
- DBLP；
- Tavily 预搜索；
- 可选 arXiv MCP。

实际可用性依赖网络、Key、限流和数据源状态。缺少配置时必须降级，不能声明所有来源均成功。

### 去重和过滤

- DOI/arXiv/title 等身份归一；
- 作者、venue、年份和类别过滤；
- 相关性 guard；
- 宽泛候选去重。

### 排序

- 可选 LLM rank；
- LLM 超时后的 semantic fallback；
- 标题、关键词、N-gram、作者和 venue 启发式评分；
- RRF 工具函数。

## 4. 当前边界

- 没有外部搜索 Golden qrels；
- 没有 A/B 或点击指标证明当前优化优于旧版本；
- “语义评分”不是向量语义相似度；
- 外部来源失败后的降级存在，但 SSE heartbeat/断线恢复不完整；
- 同步第三方调用放入线程后不能被真正强制终止；
- Query Enhancement 的术语表需要持续维护；
- 不应把用户 skip 自动晋升为跨用户长期偏好。

## 5. 与 Reader RAG 的接口

外部搜索结果可以提供：

- 论文元数据；
- 摘要；
- PDF URL；
- 相关论文线索。

它们不能直接提供：

- 当前 PDF 的可靠页码；
- canonical chunk；
- 本轮 Reader Evidence；
- 可验证引用。

论文保存并完成 canonical Ingest 后，才进入 PDF RAG。

## 6. 当前测试

完整后端环境总基线为 `74 passed, 1 warning`。现有测试覆盖部分 Search Plan、RRF、Source Adapter 和 MCP 行为，但没有完整的搜索质量指标。

下一步搜索验收应建立：

- 固定查询集；
- gold paper/author/venue；
- Recall@K、MRR/nDCG；
- 来源成功率和延迟；
- LLM rank 与 fallback 对比；
- 中文/英文/混合查询；
- 断线、限流和超时。

PDF RAG 的 Golden Test 另见 [TESTING_AND_ACCEPTANCE.md](docs/TESTING_AND_ACCEPTANCE.md)。
