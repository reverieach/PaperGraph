---
title: 前端实现
module: Frontend
tags:
  - Vue 3
  - TypeScript
  - PDF.js
  - SSE
related:
  - 14_FEATURE_PAPER_READER.md
  - 30_API_DESIGN.md
  - 55_PERFORMANCE_OPTIMIZATION.md
evidence:
  - frontend/src/router/index.ts
  - frontend/src/services/api
  - frontend/src/views
  - frontend/src/components/PdfJsViewer.vue
  - frontend/vite.config.ts
last_verified: 2026-07-31
---

# 前端实现

## 一句话结论

前端是 Vue 3 Composition API 单页应用：按路由懒加载功能页，Axios 统一普通 API 与 token，`fetch` 处理搜索 SSE，PDF.js 做 Reader 画布懒渲染，D3/KaTeX 分别处理图谱和公式。

## 页面结构

| Route | View | 主要职责 |
|---|---|---|
| `/login` | `Login.vue` | 注册/登录 |
| `/search` | `SearchAgent.vue` | 搜索对话、Deep Search、SSE 进度与结果保存 |
| `/daily` | `DailyArxiv.vue` | 每日推荐、反馈、保存/阅读 |
| `/library` | `Library.vue` | 筛选、分页、批量、导出 |
| `/library/read/:id` | `PaperReader.vue` | PDF + canonical Reader + Memory |
| `/memory` | `LongTermMemory.vue` | user Memory |
| `/research` | `MultiPaperResearch.vue` | 固定论文集合研究 |
| `/graph` | `KnowledgeGraph.vue` | D3 关系图 |

Router guard 只做用户体验级检查：无 `pg_token` 跳 `/login`。真实授权仍由后端验证。

## API 层

`apiClient`：

- base URL 来自 `config/ports.ts`；
- 默认 timeout 120 秒；
- request interceptor 注入 Bearer；
- 401 清 token 并跳登录；
- 针对 Daily/Search/Reader 给不同 timeout 提示；
- 把 FastAPI `detail` 数组/字符串转成 Error。

功能 API 拆成 papers/reader/memory/research/search/daily 文件。

## Search SSE

`searchAgentChatStream` 使用原生 `fetch`：

1. 创建 420 秒 `AbortController`。
2. 手动加 token。
3. 读 `ReadableStream`，按 `\n\n`/`\r\n\r\n` 分帧。
4. 解析每个 `data:` JSON。
5. `useSearchAgentChat` 把 event 映射为步骤 UI。
6. 必须收到 `final_result`，否则报告流不完整。

支持 Deep Search 的 decompose/round/RRF/rank/synthesis 进度。

## PDF.js Reader

`PdfJsViewer.vue`：

- Blob URL 作为 src。
- 加载 `pdfjs-dist` worker。
- 用 IntersectionObserver 只渲染可见页。
- ResizeObserver 调整 placeholder 和 canvas。
- 保存 page element/canvas/render task map。
- 切换文件时 generation guard + cleanup，避免旧异步任务写回。
- 支持页码输入、缩放、滚动更新 current page。
- 暴露 `gotoPage` 给 Citation chip。

`PaperReader.vue`：

- 双栏 drag resize；
- 获取 Paper/PDF/Ingest 状态；
- Opening、History、Chat；
- Context mode/degradation 提示；
- Memory Draft modal；
- 页面卸载时 revoke Blob URL、清 timer、上报阅读时长。

## Multi-paper 与 Graph

- `MultiPaperResearch.vue` 选择用户文献、固定 session、显示每个 turn 的 paper/title/page citation。
- `KnowledgeGraph.vue` 读取 graph JSON，D3 force simulation，支持过滤、展开、节点详情、PNG。
- 图谱和 Reader 都是路由懒加载，避免首屏引入大依赖。

## 状态管理

项目没有 Pinia/Vuex。状态分为：

- 页面级 `ref/reactive/computed`；
- composable 管 Search chat；
- localStorage 保存 token、username 和搜索会话；
- 服务器保存 Reader/Research turns 和 Memory。

合理推断：当前页面之间共享状态较少，轻量本地状态足够。

## 构建与分包

2026-07-31 `npm run typecheck` 与 `npm run build` 通过：

- 最大业务 JS chunk：`antd-data` 360.20 kB；
- `PaperReader` 358.59 kB；
- KaTeX 258.87 kB；
- PDF.js worker 1,375.83 kB 独立资源；
- 3844 modules transformed；
- 无 Vite 500 kB business chunk 告警。

Vite 对 Ant Design 按功能域 manual chunks，页面通过动态 import 懒加载。

## 异常处理

- 401 全局注销。
- 422/5xx/timeout 映射用户提示。
- SSE malformed event 被跳过；无 final result 视为失败。
- PDF load/render 错误显示重试状态。
- Ingest Job 永久错误在 Reader 顶部显示差异化说明。
- 组件卸载清理 timer/observer/render task/object URL。

## 当前限制

- 没有前端单元测试、组件测试或正式 Playwright suite。
- localStorage token 可被 XSS 读取。
- 搜索对话本地保存而 Reader/Research 服务端保存，数据策略不统一。
- 手写 TypeScript API interface 与 generated OpenAPI 并存。
- PDF.js worker 在内置测试浏览器不可用，普通 Chrome/Edge 尚缺自动门禁。

## 面试官可能提问与回答要点

1. **为什么 Search 不用 Axios？** Axios 对浏览器流式 SSE body 不如 fetch/ReadableStream 直接。
2. **PDF 大文件怎么优化？** IntersectionObserver 懒渲染可见页、取消 render task、独立 worker。
3. **为什么不用全局状态库？** 页面状态相对独立，服务端保存关键历史；当前复杂度不需要。
4. **如何跳转 Evidence 页？** 后端返回 page，chip 调 `PdfJsViewer.gotoPage`。
5. **前端安全边界是什么？** Router guard 只是 UX，所有真实权限由后端。

## 证据来源

- `frontend/src/router/index.ts`
- `frontend/src/services/api/client.ts`
- `frontend/src/services/api/search.ts`
- `frontend/src/components/PdfJsViewer.vue`
- 2026-07-31 `npm run typecheck && npm run build`
