# AI 知识库 · 前端

Vue 3 + TypeScript + Vite。Vite 把 `/api` 代理到 `ai-service`（默认 `:8084`）。

## 快速开始

先启动 [ai-service](../README.md)。

```bash
npm install
npm run dev
```

访问 `http://localhost:5173`，注册后登录。

`VITE_USE_MOCK=true` 时使用本地假数据，不启动后端也可看页面；字段与真实 API 不完全一致。

```bash
npm run build
npm run typecheck
```

## 环境变量

| 变量 | 说明 | 仓库当前默认 |
|------|------|----------------|
| `VITE_API_BASE_URL` | 前端请求前缀 | `/api` |
| `VITE_API_TARGET` | Vite 代理目标 | `http://localhost:8084` |
| `VITE_USE_MOCK` | `true` 时用本地假数据 | `false` |

浏览器请求 `/api/...`，Vite 转发到 `VITE_API_TARGET`。

## 与后端的约定

- 成功：直接返回资源 JSON（如文档对象、检索 `results`），**没有** `{ code, data }` 信封。
- 失败：`{ code, reason, message }`；Axios 拦截器按 `code` 或 HTTP 状态提示。
- 鉴权：`Authorization: Bearer <access_token>`。
- 流式 RAG：`POST /api/v1/knowledge/bases/{id}/rag/query/stream`，用 `fetch` 解析 SSE（不能用 EventSource）。无相关资料时正文为「我不知道」；生成失败为「模型回答失败」。

不要使用已废弃路径 `GET /api/v1/kbs`。知识库是：

- `GET/POST /api/v1/knowledge/bases`
- `GET/PATCH/DELETE /api/v1/knowledge/bases/{id}`

完整表见仓库根目录 [README.md](../README.md) 的 API 概览，或运行中的 `http://localhost:8084/docs`。

## 页面

| 路由 | 说明 |
|------|------|
| `/login` | 登录 / 去注册 |
| `/home` | 提问入口 |
| `/search` | 上方为根据资料生成的回答，下方为参考资料（可点进文档） |
| `/knowledge` | 知识库卡片；删除会级联后端文档 |
| `/knowledge/:kbId/documents` | 上传（pdf/md/txt，含进度）、列表、重解析/重嵌入、轮询状态 |
| `/knowledge/:kbId/documents/:docId` | 预览与分块 |
| `/history` | 检索历史（浏览器本地） |
| `/favorites` | 收藏（浏览器本地） |
| `/settings` | 偏好写入 `localStorage`，不修改服务端模型 |

上传不支持 Word（.docx）。

## 目录

```
web/
├── vite.config.ts          # /api 代理到 VITE_API_TARGET
├── index.html
├── public/
└── src/
    ├── api/                # request.ts、auth / knowledge / document / search / chat
    ├── views/              # Login、SearchHome、SearchResults、KnowledgeBase、Document…
    ├── components/
    │   ├── Search/
    │   ├── Knowledge/
    │   └── Common/
    ├── layout/             # AppLayout / Header / Sidebar
    ├── stores/             # user / knowledge / history / favorites / ui / settings
    ├── router/
    ├── hooks/
    ├── types/
    ├── utils/
    └── style/
```
