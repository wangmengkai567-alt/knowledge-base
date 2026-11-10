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

配置清单见 [环境变量模板](ai-service/.env.example) 和 [已确认选型、预算](docs/preparation/preparation-report.md)。当前模型为 `qwen3.8-flash`，嵌入为 `qwen3.7-text-embedding-flash`、1024 维，重排为 `qwen3.7-text-rerank`，网页搜索使用 Tavily，正文使用受控静态 HTML 读取。填写模型密钥、`TAVILY_API_KEY` 和 `POSTGRES_PASSWORD`。修改环境变量后需重新创建后端容器使其生效。

`AI_SERVER__WORKERS` 必须为 **1**；关键词索引是进程内状态。保留既有向量与上传文件，不为启用工具问答重新上传全部资料。

## Docker 运行与显式迁移

在项目根目录执行以下命令。需要已启动的 Docker Desktop（或 Docker Engine）及 Docker Compose；前端开发还需要 Node.js。先按上文创建并填写根目录 `.env`，至少设置 `POSTGRES_PASSWORD` 和所需模型密钥。以下命令只操作后端、PostgreSQL 和 Redis，不启动前端。

### 启动服务

首次启动时，先构建 AI 服务镜像并启动 PostgreSQL 和 Redis：

```bash
docker compose -f deployments/docker/docker-compose.yaml --env-file .env build ai-service
docker compose -f deployments/docker/docker-compose.yaml --env-file .env up -d postgres redis
```

等待 PostgreSQL 和 Redis 就绪后，执行一次数据库迁移，再启动 AI 服务：

```bash
docker compose -f deployments/docker/docker-compose.yaml --env-file .env run --rm --no-deps ai-service uv run --no-sync python -m cmd.migrate
docker compose -f deployments/docker/docker-compose.yaml --env-file .env up -d --no-deps ai-service
```

后续普通启动只需启动三个服务，无需重复构建和迁移：

```bash
docker compose -f deployments/docker/docker-compose.yaml --env-file .env up -d postgres redis ai-service
```

依赖变化后需重新构建镜像。数据库结构更新时，先按 [备份与恢复说明](docs/preparation/preparation-report.md) 备份数据库和上传文件，再停止 AI 服务并执行迁移，迁移成功后重新启动。

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

`user_urls` 可提供最多三个公开网址，要求开启联网。请求不接受旧的 `chunk_ids`。SSE/同步响应协议版本为 6，工具契约版本为 3，答案契约版本为 4；SSE 依次发送 `type=event` 过程、`type=result` 结果和 `[DONE]`；回答不是逐字流式输出。每条结论使用 `text/evidence_ids/code_evidence_id`，可引用一个或多个互补证据；程序绑定原文和引用，不按来源数量拒绝。标签按实际来源类型显示“知识库”“网页正文”“搜索摘要”，混合类型显示“多来源资料”，含摘要时明确标注；标签不表示共识。来源包含实际送模 `content`，知识库有分块位置、片段范围和文档版本时间，网页有 URL 与抓取时间。

## 项目记录

- [分阶段重构计划](docs/plans/agentic-rag-refactoring-plan.md)
- [评测说明](eval/README.md)
- [术语表](GLOSSARY.md)
- [只读工具问答设计](docs/adr/0002-bounded-read-only-question-answering.md)

提交和推送由用户决定。

### 问答 Token 预算

使用随部署提供的固定 Qwen tokenizer；完整请求按模型模板、工具定义或答案 Schema 计数后，加 10% 与 256 token 的显式余量，标记为 `model_tokenizer_estimate`。实际结算仅采用供应商 usage。单次输入 16000、输出 2048、整次累计 64000；模型/工具次数与 120 秒时限保持。

`token_budget` 配置集中管理额度：工具完整 content 3000 token、完整消息 12000 字节；摘要 512 token、网页窗口 2000 token、定位预览 64 token。`web_read.max_tokens` 控制网页窗口，`start/end` 和 `total_chars` 始终为原文字符坐标，下一页使用实际返回的 `end`。原网页接收与缓存字节上限保持。

旧 `AI_WEB_READER__PAGE_CHARS`、`AI_TAVILY__SUMMARY_CHARS`、`TAVILY_SUMMARY_CHARS` 必须迁移到 `AI_TOKEN_BUDGET__PAGE_TOKENS/SUMMARY_TOKENS`，不得机械换算旧字符数。模型或固定资源版本不匹配、资源缺失时启动失败，不在线下载或切换计数器。资源与许可位于 `ai-service/resources/qwen38-flash/`；重新构建镜像会一并打包。

运行与快照保存计数资源、投影版本、余量以及逐次请求估算明细。旧快照保留原计量标识；评测按计量方法与资源版本分组，不混加新旧输入估算。
