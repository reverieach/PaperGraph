# PaperGraph Docker 状态与实验性部署说明

文档状态：`EXPERIMENTAL`
更新日期：2026-07-28

## 当前结论

仓库包含 `docker-compose.yml` 和前后端 Docker 构建文件，但本阶段没有安装或实际验收 Docker。当前推荐路径是 Windows 本地 `venv-rag`，详见 [ENVIRONMENT.md](docs/ENVIRONMENT.md)。

不能把以下能力视为已验证：

- 容器内 Docling 和 PyTorch；
- GPU/CUDA 透传；
- LanceDB/Canonical artifacts 持久化；
- Ingest Worker；
- 当前完整 RAG requirements；
- Docker fresh DB 到 Reader RAG 的端到端流程。

## 正式启用前的配置与验收项

在正式使用前至少需要处理：

1. 从根 `.env.example` 复制 `.env`，替换必需的 `PAPERGRAPH_JWT_SECRET` 和模型 API 占位值；
2. 已对齐 Compose 的 `DATA_DIR` / `DOWNLOADS_DIR` 字段，但仍需用 fresh volume 验证真实挂载和重启持久化；
3. Backend image 必须安装完整 RAG 依赖或明确拆成 API/Worker image；
4. 增加 `rag_artifacts`、`rag_vectors` 和模型缓存持久化卷；
5. GPU 模式需要 NVIDIA Container Toolkit 和独立验收；
6. Worker 不能只依赖 FastAPI BackgroundTasks；
7. Healthcheck 需要增加 RAG capability。

## 仅用于基础功能的现有命令

如果接受“尚未验证、可能只有基础功能”的状态，可在修复配置后尝试：

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f backend
```

入口：

- Frontend：`http://localhost:5173`
- Backend health：`http://localhost:8000/health`

不要在没有备份的情况下把真实 `backend/data` 直接绑定到实验容器。

## Docker 转为受支持路径的验收

- fresh container 启动；
- Migration/schema validation；
- 注册、登录、跨用户隔离；
- 保存论文和 PDF；
- canonical Ingest；
- Docling CPU/GPU；
- FTS/LanceDB；
- Reader 命中 `hybrid_rag_v2`；
- 引用跳页；
- 容器重启后 DB/PDF/artifact/vector/job 均保留；
- 完整后端测试和前端 E2E。

这些门禁完成前，README 不应把 Docker 描述为“一键部署推荐方案”。
