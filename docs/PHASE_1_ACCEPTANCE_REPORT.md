# PaperGraph 第一阶段验收报告

> 文档状态：`HISTORICAL_REPORT`
>
> 本报告是 2026-07-27 的验收快照，保留当时的测试数量、测试用户和数据库状态作为审计证据。当前权威基线请查看 [CURRENT_STATE.md](./CURRENT_STATE.md)、[ENVIRONMENT.md](./ENVIRONMENT.md) 和 [TESTING_AND_ACCEPTANCE.md](./TESTING_AND_ACCEPTANCE.md)。
>
> 验收日期：2026-07-27
> 验收分支：`codex/phase1-foundation`
> 结论：**通过（存在非阻塞告警，可进入第二阶段）**

## 1. 验收边界

本报告验收第一阶段的“可运行与正确性基础”，包括：

- 配置、统一初始化和数据库迁移；
- JWT 登录、路由跳转和用户级数据隔离；
- 文献入库、本地 PDF 下载、Range 读取和前端渲染；
- Reader 对话、历史持久化和论文级隔离；
- 统一 Memory 数据模型、草稿、显式确认、幂等提交和长期记忆管理；
- 长期记忆与协同研究前后端入口；
- 前端类型检查、生产构建和真实浏览器交互；
- SQLite 完整性、外键、WAL 和超时配置。

以下内容不属于第一阶段完成标准，仍按第二阶段计划实施：

- 结构感知 PDF 解析和标准化 Chunk；
- Embedding、BM25、向量混合召回和 Rerank；
- Evidence Registry、可校验页码引用和动态 Context Builder；
- 多论文全文 RAG；
- OCR 扫描件处理。

## 2. 真实论文测试集

测试集位于 `backend/data/rag_eval_corpus/`，由 `manifest.json` 记录来源、文件哈希、页数和覆盖特征。该目录受 `backend/data/` 的 Git 忽略规则保护，不进入源码提交。

| 指标 | 结果 |
| --- | ---: |
| 论文数 | 8 |
| PDF 总页数 | 162 |
| 总大小 | 25,977,811 bytes |
| PDF 头与加密检查 | 8/8 合格，均未加密 |
| 有文本页面 | 162/162 |
| 当前增强提取器成功率 | 8/8 |
| 当前增强提取器全文字符数 | 571,532 |

覆盖的论文包括 Transformer、BERT、RAG、ReAct、长上下文、GraphRAG、Docling 和 ColPali，包含双栏、表格、公式、图片、长附录和参考文献等版式。

已知缺口：本测试集没有自然扫描或纯图片 PDF，OCR 必须在第二阶段增加独立的授权或合成扫描件夹具。

## 3. 自动化检查结果

### 3.1 后端

| 检查 | 命令或方式 | 结果 |
| --- | --- | --- |
| Python | `backend/.venv` | 3.11.9 |
| 依赖一致性 | `python -m pip check` | 通过，无破损依赖 |
| 静态编译 | `python -m compileall -q app tests` | 通过 |
| 全量测试 | `python -m pytest -q` | **49 passed**，14.66 秒 |
| Phase 1 类型边界 | 文档规定的 mypy 命令 | 通过 |
| Git 空白错误 | `git diff --check` | 通过，仅有 Windows LF/CRLF 提示 |

测试存在一条非阻塞依赖告警：

- `StarletteDeprecationWarning`：当前 `starlette.testclient` 使用旧 httpx 适配方式，后续应评估升级到 `httpx2`。

补充说明：扩大 mypy 范围并允许跟随所有导入时，现有 LLM、搜索和 Reader 辅助模块仍暴露 101 个历史类型错误；它们不是本次 Phase 1 类型边界的失败，但应作为 P2/P3 技术债逐步清理。

### 3.2 前端

先在隔离前端副本中使用 Node 24.11.0、npm 11.6.1 完成构建验证；临时服务停止、Windows 文件锁释放后，又在真实 `frontend/` 目录完整复跑：

| 检查 | 结果 |
| --- | --- |
| `npm ci` | 通过，安装 250 packages |
| `npm run openapi:gen` | 通过 |
| `npm run typecheck` | 通过 |
| `npm run build` | 通过，Vite 8.1.5，约 20.6 秒 |
| 生成的 OpenAPI 类型与源码树版本 | SHA-256 完全一致 |
| `npm audit --omit=dev` | 0 个生产依赖漏洞 |

非阻塞告警：

- 完整 npm audit 报告包含 4 个 high，均来自开发工具依赖；
- Ant Design 分包约 748.95 kB，PDF worker 约 1.376 MB，需在工程化阶段优化首屏和缓存；
- 原 `frontend/node_modules` 曾因正在运行的 Vite 原生模块被 Windows 锁定，第一次 `npm ci` 无法覆盖该文件。释放文件锁后，原目录的 `npm ci`、typecheck、build 和生产依赖审计均已通过，此问题已关闭。

## 4. 数据库与迁移验收

在真实测试库 `backend/data/papers.db` 上执行迁移、重复迁移和终态检查：

| 检查 | 结果 |
| --- | --- |
| Migration | v1–v5 全部存在，checksum 稳定 |
| 重复执行 | 幂等通过 |
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 0 条违规 |
| `journal_mode` | `wal` |
| `foreign_keys` | `1` |
| `busy_timeout` | `5000 ms` |

当时的验收用户：

- `default`：原默认用户，状态 active；
- `phase1_accept_owner`：真实链路验收用户，user_id=3；
- `phase1_accept_other`：隔离验证用户，user_id=4。

8 篇测试论文均通过 `POST /api/papers/save` 写入 user_id=3，并完成 8/8 本地 PDF 下载；数据库保存相对路径，按 `backend/data` 解析后所有文件均真实存在。

## 5. API 与业务链路验收

### 5.1 登录与用户隔离

- 当时测试库中的 `default` 测试账户登录接口返回 200、`success=true`、有效 token、user_id=2；
- 浏览器使用验收用户登录后，由 `/login` 正确跳转到 `/search`；
- 未认证访问文献库返回 401；
- user_id=3 可见 8 篇测试论文，user_id=4 文献库为 0 篇；
- user_id=4 访问 user_id=3 的论文、PDF、Reader 历史和 Memory 草稿均返回 404；
- 隔离策略使用“不可见”语义，避免通过 403 暴露资源是否存在。

### 5.2 文献入库和 PDF

- 8 篇论文一次真实入库成功，耗时约 84.8 秒；
- 8/8 PDF 下载成功；
- PDF Range 请求返回 206、正确的 `Content-Range`、ETag 和 `%PDF-` 文件头；
- `If-None-Match` 返回 304；
- 浏览器中 RAG 论文识别为 19 页，生成 19 个 Canvas；
- 首个 Canvas 为 1155×1494 实际像素、770×996 CSS 像素，截图可见论文标题、作者和摘要，排除“空白占位”；
- “下一页”操作后页码由 1 变为 2；
- 文献库“阅读”在当前标签页跳转到 `/library/read/6`，标签页数量不变。

### 5.3 Reader 与上下文基础行为

- 首次打开论文返回 200，生成 conversation `paper-3-6`；
- 基于论文询问 RAG-Sequence 与 RAG-Token，返回有效回答并产生 1 个软引用标记；
- 询问“2+2 等于多少，只回答该问题”时，响应为 `2+2=4。`，没有再次强制摘要论文；
- Reader 历史正确持久化并按 `user_id + paper_id + conversation_id` 隔离；
- 浏览器阅读界面使用 100vh 单页布局，PDF 与对话各自拥有滚动区域，PDF 翻页后对话输入框仍可见且可用。

当前引用只能视为第一阶段的软页码格式，不能视为可审计证据。实际回答使用了 `[p2-p3]`，第二阶段必须由检索命中的 Chunk/Evidence 生成引用，禁止仅依赖模型自行输出页码。

### 5.4 Memory

- 真实 LLM 阅读总结草稿生成成功；
- 草稿包含聚合后的论文阅读总结、关键发现、待解决问题、研究决策和长期用户记忆候选；
- 论文记忆提交成功；
- 同一 `Idempotency-Key` 重复提交返回相同结果，没有重复写入；
- 论文记忆按 `user_id + scope_type=paper + scope_id=paper_id` 隔离；
- 长期记忆按 `user_id + scope_type=user + scope_id=user_id` 隔离；
- 长期记忆 API 创建、列表、删除均通过；
- 浏览器端手动添加、显示、删除均通过；
- “总结本次阅读”弹窗具备“保存这份论文阅读总结”、长期用户候选复选框、“确认保存”和“放弃本次草稿”操作；
- 阅读页面不存在默认“没有记忆”提示。

结论：第一阶段 Memory 的写入、读取、删除、隔离、显式确认和幂等性已通过；语义召回、压缩/遗忘、隐私生命周期和基于 Embedding 的相关性选择属于第二、三阶段。

### 5.5 协同研究

- 左侧“协同研究”入口可用；
- 前端可读取当前用户的 8 篇论文并选择研究材料；
- 会话初始化成功，材料侧栏可以收起；
- 真实 LLM 问答成功；
- 页面明确提示当前仅使用论文元数据与摘要，未冒充全文多论文 RAG。

## 6. 浏览器界面验收

| 场景 | 结果 |
| --- | --- |
| 登录后跳转 | 通过，进入 `/search` |
| 侧边栏长期记忆 | 通过 |
| 侧边栏协同研究 | 通过 |
| 文献库显示 8 篇 | 通过 |
| 阅读当前页跳转 | 通过，无新标签页 |
| 出版方与领域字段 | 通过，出版方以省略号截断，未与领域重叠 |
| PDF 非空渲染 | 通过 |
| PDF 翻页 | 通过 |
| Reader 独立面板 | 通过 |
| 非论文问题不自动摘要 | 通过 |
| 记忆总结弹窗与按钮 | 通过 |
| 长期记忆前端增删 | 通过 |
| 协同研究问答 | 通过 |

## 7. 性能基线

本轮不是性能优化阶段，但记录以下真实基线：

| 操作 | 耗时 |
| --- | ---: |
| 8 篇入库并串行下载 PDF | 84.8 秒 |
| 8 篇 PDF 双遍完整解析 | 90.7 秒 |
| RAG 论文首次 Reader opening | 31.6 秒 |
| 论文相关问答 | 12.2 秒 |
| 简单非论文问答 | 1.6 秒 |
| Memory 草稿生成 | 6.4 秒 |

这些数据说明当前同步长任务会直接拉长请求时间。第二/三阶段需要将下载、解析、Chunk、Embedding 和索引构建改为可追踪后台任务，并为前端提供明确的 processing/ready/failed 状态。

## 8. 遗留问题与级别

| 优先级 | 问题 | 影响 | 后续动作 |
| --- | --- | --- | --- |
| P1（第二阶段） | 当前页码引用不是 Evidence 驱动 | 可能产生看似合理但不可核验的引用 | 建立 Chunk/Evidence Registry，引用只允许来自命中证据 |
| P2 | 当前论文问答仍不是完整混合 RAG | 长论文中部信息和跨章节问题召回不稳定 | 结构化解析、Chunk、BM25+向量、Rerank、Context Builder |
| P2 | 下载和首次解析是同步长请求 | 慢请求、超时和并发能力受限 | 引入持久化 Job 状态和后台 worker |
| P2 | 全量 mypy 跟随导入仍有 101 个历史错误 | 类型边界不完整，重构风险较高 | 按模块逐步收紧，不一次性阻塞开发 |
| P2 | 没有扫描件/OCR 验收夹具 | 无法证明扫描论文可入库 | 增加 OCR 测试集、质量阈值和降级状态 |
| P3 | Windows 重定向日志中的中文出现乱码 | 定位问题体验差 | 统一 UTF-8 日志 handler 和终端编码 |
| P3 | Starlette TestClient 弃用告警 | 后续依赖升级可能破坏测试 | 锁定版本并单独完成 httpx2 兼容升级 |
| P3 | 前端开发依赖有 4 个 high，分包偏大 | 开发供应链和加载体验 | 升级开发依赖、拆分大包和缓存 PDF worker |

## 9. 最终判定

第一阶段的 P0/P1 基础正确性目标已经达到：

- 应用可启动；
- 数据库可迁移且结构完整；
- 默认账号可登录，前端可正确跳转；
- 用户、论文、历史和记忆隔离已实际验证；
- PDF 入库、传输和浏览器渲染可用；
- Memory 显式总结、选择、提交和管理可用；
- 前后端类型检查与构建通过；
- 49 项自动化测试全部通过。

因此项目可以进入第二阶段。下一阶段应严格聚焦“可靠论文级 RAG”：结构化入库、标准 Chunk、混合召回、Rerank、动态上下文和可核验引用，不应继续扩充 Agent 数量或先做 GraphRAG。
