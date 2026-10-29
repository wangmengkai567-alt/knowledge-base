# AI 知识库

Python FastAPI + Vue 3 个人知识库。后端在一次有预算的运行中选择知识库检索、网页搜索和正文读取，浏览器展示过程、终态和实际使用的来源。

## 问答与入库

- 知识库检索复用现有向量、BM25、RRF 和重排；保留真实分数类型，不按顺序制造相关性分数。
- 用户开启联网后才允许 Tavily Search / Extract；只外发必要公开关键词。搜索摘要与网页正文分别标记，网页保留 URL、获取时间和实际文字。
- 后端持有资料、工具调用、预算和引用映射；最终答案通过引用格式检查后一次发布。引用语义是否支持结论另由评测核对。
- 每题最多 6 次模型调用、10 次工具调用、120 秒；单次输入保守估算上限 16000，单次输出 2048，累计扣减上限 64000。保守估算与模型返回 token 分开记录。
- `completed`、`insufficient`、`tool_failed`、`model_failed`、`timeout`、`cancelled`、`budget_exhausted`、`repeated_call`、`invalid_answer` 是不同终态。资料不足允许说明缺失信息，不要求固定输出某句话。
- 解析、分块、向量索引和关键词索引分别记录状态、错误和更新时间。只有当前配置所需步骤全部完成才是 `ready`；从失败阶段重试，关键词重试不重新嵌入。
- 运行记录与实际资料保存在 `ai-service/data/answer-runs/user-*/`，本机保留七天，不支持断线重连或重启后续跑。页面取消会传到后端请求，但不能保证供应商停止计算或计费。

## 配置

复制模板，分别供 Compose 和本机运行读取；不要覆盖已有密钥文件：

```powershell
Copy-Item ai-service/.env.example .env
Copy-Item ai-service/.env.example ai-service/.env
```

配置清单见 [环境变量模板](ai-service/.env.example) 和 [已确认选型、预算](docs/preparation/preparation-report.md)。当前模型为 `qwen3.8-flash`，嵌入为 `qwen3.7-text-embedding-flash`、1024 维，重排为 `qwen3.7-text-rerank`，网页供应商为 Tavily。填写模型密钥、`TAVILY_API_KEY` 和 `POSTGRES_PASSWORD`。修改环境变量后需重新创建后端容器使其生效。

`AI_SERVER__WORKERS` 必须为 **1**；关键词索引是进程内状态。保留既有向量与上传文件，不为启用工具问答重新上传全部资料。

## Docker 运行与显式迁移

需要 Docker Compose；前端开发需要 Node.js。镜像使用 uv 和 `ai-service/uv.lock` 安装依赖，环境位于 `/opt/venv`，源码挂载不会覆盖它。依赖改变后必须重建镜像。

已有数据库升级前，先按 [备份与恢复说明](docs/preparation/preparation-report.md) 保存数据库和上传文件的成对备份。以下命令只处理本项目后端、数据库和 Redis：

```bash
docker compose -f deployments/docker/docker-compose.yaml --env-file .env build ai-service
docker compose -f deployments/docker/docker-compose.yaml --env-file .env up -d postgres redis
docker compose -f deployments/docker/docker-compose.yaml --env-file .env stop ai-service
docker compose -f deployments/docker/docker-compose.yaml --env-file .env run --rm --no-deps ai-service uv run --no-sync python -m cmd.migrate
docker compose -f deployments/docker/docker-compose.yaml --env-file .env up -d --no-deps ai-service
```

迁移幂等，不覆盖已有阶段错误或把旧解析标记复制为全部就绪。启动时核实真实向量并重建关键词索引，不自动重新嵌入。上传卷为 `docker_ai-uploads`，数据库卷为 `docker_ai-postgres-data`（默认 Compose 项目名 `docker`）。不要用 `down -v` 删除已有数据。

- 后端接口文档：[localhost:8084/docs](http://localhost:8084/docs)
- 进程健康：`/health/live`；数据库与 Redis 就绪：`/health/ready`；指标：`/metrics`
- 启动日志应确认关键词索引重建；就绪接口不能代替逐文档入库状态核对。

## 本机后端与前端

Python 环境和依赖统一由 uv 管理。已有数据默认在 Docker PostgreSQL；本机运行前须显式配置同一目标数据库、Redis、原文件路径，避免误用模板 SQLite。

```bash
cd ai-service
uv sync --locked --extra dev
uv run --no-sync --env-file .env python -m cmd.migrate
uv run --no-sync python -m cmd.main
```

另一个终端：

```bash
cd web
npm ci
npm run dev
```

打开 [localhost:5173](http://localhost:5173)，注册或登录。前端构建执行 `npm run build`，包括 Vue/TypeScript 检查。更多交互说明见 [前端说明](web/README.md)。

## API 约定

成功返回资源 JSON；HTTP 错误返回 `{code, reason, message}`，问答运行失败则由响应内 `status`、`stop_reason`、`error_code` 表达。鉴权使用 `Authorization: Bearer <access_token>`。

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/users/register`、`/api/v1/users/login` | 注册、登录 |
| GET/POST | `/api/v1/knowledge/bases` | 列表、创建知识库 |
| GET/PATCH/DELETE | `/api/v1/knowledge/bases/{id}` | 知识库详情、更新、删除 |
| POST/GET | `/api/v1/knowledge/bases/{kb_id}/documents` | 上传、文档列表 |
| GET/DELETE | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}` | 文档详情、删除 |
| POST | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/parse` | 重新解析；已解析资料使用 `force=true` |
| POST | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/retry` | `{}` 从失败步骤继续，或指定 `stage: parse/chunk/vector/keyword` |
| GET | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/chunks` | 当前分块 |
| POST | `/api/v1/knowledge/bases/{kb_id}/retrieve` | 独立检索，不是问答必经请求 |
| POST | `/api/v1/knowledge/bases/{kb_id}/rag/query` | 同步问答 |
| POST | `/api/v1/knowledge/bases/{kb_id}/rag/query/stream` | SSE 过程和最终结果，同一次执行 |

问答请求示例：

```json
{"query":"Docker 镜像和容器有什么区别？","knowledge_base_ids":[3],"top_k":8,"allow_web":false}
```

`user_urls` 可提供最多三个公开网址，要求开启联网。请求不接受旧的 `chunk_ids`。SSE 协议版本为 4，依次发送 `type=event` 过程、`type=result` 结果和 `[DONE]`；回答不是逐字流式输出。来源包含实际送模 `content`，知识库有分块位置、片段范围和文档版本时间，网页有 URL 与抓取时间。

## 项目记录

- [分阶段重构计划](docs/plans/agentic-rag-refactoring-plan.md)
- [评测说明](eval/README.md)
- [术语表](GLOSSARY.md)
- [只读工具问答设计](docs/adr/0002-bounded-read-only-question-answering.md)

提交和推送由用户决定。
