# AI Knowledge Base

上传 PDF / Markdown / TXT，解析分块后做向量 + 关键词混合检索，再按资料回答。

后端 FastAPI（`ai-service/`，`:8084`），前端 Vue 3（`web/`，`:5173`）。LLM / Embedding 走 OpenAI 兼容 HTTP，可接 DeepSeek、腾讯云 TokenHub、阿里云、Ollama。

## 功能

- 注册 / 登录，Opaque Token（Redis），登出可吊销
- 知识库 CRUD；删库级联文档、分块、向量
- 上传 PDF / Markdown / TXT（不支持 DOCX）。扫描版 PDF 会做 OCR。解析失败可重解析，嵌入失败可重试
- Hybrid 检索：向量余弦 + BM25，RRF 融合，可选关键词重叠 rerank
- RAG：先检索相关资料，再由 LLM 按资料生成回答（SSE 流式）。搜索页只检索一次：列表展示命中，同一批分块交给模型
- 无相关资料时直接回答「我不知道」，不调用 LLM、不编造
- 生成失败时返回「模型回答失败」，不把检索摘录当成回答
- `/metrics`、`/health/live`、`/health/ready`


## 技术栈

| 层 | 实现 |
|----|------|
| 后端 | Python 3.11+，FastAPI，Uvicorn，Pydantic v2 |
| 分层 | `server` → `service` → `biz` → `data` |
| 存储 | SQLite 或 PostgreSQL 16 + pgvector |
| 检索 | pgvector / 内存向量 + 进程内 BM25，RRF（k=60） |
| 前端 | Vue 3、TypeScript、Vite、Pinia、Element Plus、TailwindCSS |
| 其它 | Redis（Session / 限流）、structlog、Prometheus |

`AI_SERVER__WORKERS` 必须为 **1**。BM25 在进程内存里，多 worker 会导致上传和检索打到不同进程。

## 架构

```
浏览器 (Vue :5173)
    │  Vite 将 /api 代理到 FastAPI
    ▼
ai-service :8084
    server  鉴权 / 限流 / SSE
    service DTO ↔ 领域对象
    biz     文档、分块、检索、RAG、对话
    data    DB、向量库、BM25、LLM HTTP
    Redis   Session、限流
```

```
上传 PDF/MD/TXT
  → 解析（PDF 文字层；扫描页 OCR。表格保留表头对应关系）
  → 分块（递归，约 256 token / 重叠 32）
  → Embedding → 向量库 + BM25
提问
  → Embedding（单条 query 进程内 LRU 缓存）
  → Hybrid（向量 + BM25，RRF）+ 相关度门槛（每个知识库一次）
  → 无相关资料 → 回答「我不知道」（不调用 LLM）
  → 有资料 → 列表按文档去重展示；同一批块拼 context → LLM 生成
  → 生成失败 → 「模型回答失败」（不贴检索摘录）
```

## 目录

```
.
├── README.md
├── Makefile                          # docker / ruff
├── .gitignore
├── .dockerignore
│
├── ai-service/                       # FastAPI（:8084）
│   ├── .env.example                  # 复制为根目录 .env 和本目录 .env
│   ├── pyproject.toml
│   ├── cmd/
│   │   ├── main.py                   # python -m cmd.main
│   │   ├── wire.py                   # 依赖组装
│   │   └── migrate.py                # 建表
│   ├── configs/config.yaml           # 非密钥默认值，可被 .env 覆盖
│   ├── api/openapi/schemas/          # 请求 / 响应 DTO
│   ├── pkg/                          # 错误码、Session
│   ├── data/uploads/                 # 本地上传文件（不入库）
│   └── internal/
│       ├── conf/                     # 配置、日志
│       ├── server/                   # FastAPI、鉴权 / 限流 / 指标
│       ├── service/                  # DTO ↔ 领域对象
│       ├── biz/                      # 用户、知识库、文档、检索、RAG
│       ├── data/
│       │   ├── parser/               # PDF / Markdown / TXT
│       │   ├── chunker/              # 递归分块
│       │   ├── embedding/            # TokenHub / OpenAI 兼容 / memory
│       │   ├── llm/                  # 同上
│       │   ├── vector_store/         # pgvector / 内存
│       │   ├── keyword/              # 进程内 BM25
│       │   ├── retriever/            # 向量 / Hybrid + RRF
│       │   ├── reranker/             # 关键词重叠精排
│       │   ├── storage/              # 本地文件
│       │   ├── user/ · knowledge_base/ · document/ · chunk/
│       │   └── models.py · database.py
│       ├── prompt/templates/         # RAG 提示词
│       └── streaming/                # SSE
│
├── web/                              # Vue 3（:5173）
│   ├── README.md
│   ├── package.json
│   ├── vite.config.ts                # /api → :8084
│   ├── index.html
│   ├── public/
│   └── src/
│       ├── api/                      # 鉴权、知识库、文档、检索
│       ├── views/                    # 登录、检索、知识库、文档、设置等
│       ├── components/               # Search / Knowledge / Common
│       ├── layout/
│       ├── stores/
│       ├── router/
│       ├── hooks/
│       ├── types/
│       ├── utils/
│       └── style/
│
└── deployments/docker/
    ├── docker-compose.yaml           # Postgres + pgvector、Redis、ai-service
    ├── Dockerfile
    └── prometheus.yml
```

密钥在 `.env`（根目录给 Compose，`ai-service/.env` 给本机 Python），不入库。模板见 `ai-service/.env.example`。

## 快速开始

需要 Python 3.11+、Node.js。用 Postgres / Redis 时再加 Docker Compose。

### 1. 环境变量

Compose 读仓库根目录 `.env`；本机 `python -m cmd.main` 读 `ai-service/.env`。两份内容应一致。

```bash
cp ai-service/.env.example .env
cp ai-service/.env.example ai-service/.env
```

PowerShell：`Copy-Item ai-service\.env.example .env; Copy-Item ai-service\.env.example ai-service\.env`

填写 `AI_LLM__API_KEY` 和 `POSTGRES_PASSWORD`。完整项见 [`ai-service/.env.example`](./ai-service/.env.example)。端口、Session、限流等走 yaml 默认值，不必写进 `.env`。

改 `.env` 后需重启后端。换过 embedding 模型的文档要重新解析或重传。

### 2. 后端

```bash
cd ai-service
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install .
python -m cmd.main
```

http://localhost:8084/docs

### 3. 前端

```bash
cd web
npm install
npm run dev
```

http://localhost:5173 ，先注册再登录。Vite 把 `/api` 代理到 `http://localhost:8084`。

### 4. Docker Compose（Postgres + pgvector + Redis）

```bash
docker compose -f deployments/docker/docker-compose.yaml --env-file .env up -d --build
```

| 服务 | 地址 |
|------|------|
| ai-service | http://localhost:8084 |
| PostgreSQL | localhost:5432，用户/库 `ai_kb` |
| Redis | localhost:6379 |

本机 Ollama：

```bash
docker compose -f deployments/docker/docker-compose.yaml --profile ollama up -d ollama
docker exec -it ollama ollama pull qwen2.5:7b
docker exec -it ollama ollama pull nomic-embed-text
```

`.env` 设 `AI_LLM__PROVIDER=local`、`AI_EMBEDDING__PROVIDER=local`。Compose 网络内地址为 `http://ollama:11434`。

## 常见问题

| 现象 | 处理 |
|------|------|
| Compose 报 `POSTGRES_PASSWORD must be set` | 根目录 `.env` 填写该变量 |
| 关键词能搜到、语义很差 | embedding 仍是 `memory`，或文档用旧模型嵌入，需重解析 |
| 回答是「我不知道」 | 知识库没有足够相关的文档，不会编答案；可换问法或补文档 |
| 回答是「模型回答失败」 | 对话模型调用失败（Key、额度、网络）；不会改贴检索摘录 |
| 检索时有时无 | `workers` 改回 `1` |
| `/health/ready` 503 | 检查 Postgres / Redis 是否已启动 |
| 登录后接口 401 | 先注册；确认后端已启动，且 `web/.env` 中 `VITE_USE_MOCK=false` |
| 改 `.env` 无效 | 重启进程；Key 不要写进 yaml |

## API

成功时返回对应 Pydantic 模型的 JSON。业务错误为 `{ code, reason, message }`。需登录的接口带 `Authorization: Bearer <access_token>`。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/users/register` | 注册 |
| POST | `/api/v1/users/login` | 登录 |
| POST | `/api/v1/users/logout` | 登出 |
| POST | `/api/v1/users/token/refresh` | 刷新 token |
| GET | `/api/v1/users/me` | 当前用户 |
| GET/POST | `/api/v1/knowledge/bases` | 知识库列表 / 创建 |
| GET/PATCH/DELETE | `/api/v1/knowledge/bases/{id}` | 详情 / 更新 / 删除 |
| POST/GET | `/api/v1/knowledge/bases/{kb_id}/documents` | 上传 / 列表 |
| GET/DELETE | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}` | 详情 / 删除 |
| POST | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/parse` | 重解析 |
| POST | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/embed` | 重试嵌入 |
| GET | `/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/chunks` | 分块列表 |
| POST | `/api/v1/knowledge/bases/{kb_id}/retrieve` | 混合检索（返回分块；列表由前端按文档去重） |
| POST | `/api/v1/knowledge/bases/{kb_id}/rag/query` | 生成回答。可传 `chunk_ids` 跳过检索。无资料「我不知道」；生成失败「模型回答失败」 |
| POST | `/api/v1/knowledge/bases/{kb_id}/rag/query/stream` | 同上，SSE：有资料时先 `sources` 再逐段 `chunk` |
| GET | `/health/live` | 进程是否在跑 |
| GET | `/health/ready` | 数据库和 Redis 是否连得上 |
| GET | `/metrics` | Prometheus |

前端说明见 [`web/README.md`](./web/README.md)。
