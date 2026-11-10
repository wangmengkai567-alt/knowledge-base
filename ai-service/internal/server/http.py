"""
internal/server/http.py — HTTP Server 创建与路由注册

创建应用、挂载中间件和路由，并管理启动检查与资源关闭。

设计要点：
- HTTPServer 负责组装 HTTP 层，具体业务交给应用服务。
- 路由按业务模块拆成独立方法。
- lifespan 用闭包创建，通过构造函数注入配置。
- /health/live 只表示进程在跑；/health/ready 检查数据库和 Redis。
- 请求依次经过 Metrics、Auth、RateLimit、Security、CORS，最后进入路由。
"""

from __future__ import annotations

import os
from contextlib import aclosing, asynccontextmanager
from typing import TYPE_CHECKING, AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from api.openapi.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    RegisterRequest,
    UserResponse,
)
from api.openapi.schemas.chunk import ChunkListResponse
from api.openapi.schemas.document import (
    DocumentListResponse,
    DocumentResponse,
    ParseDocumentRequest,
    RetryDocumentRequest,
)
from api.openapi.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from api.openapi.schemas.rag import RAGQueryRequest, RAGQueryResponse
from api.openapi.schemas.retrieval import RetrievalRequest, RetrievalResponse
from internal.biz.tool_types import ANSWER_CONTRACT_VERSION
from internal.conf.config import ServerConfig, SessionConfig
from internal.server.middleware.auth import AuthMiddleware
from internal.server.middleware.metrics import MetricsMiddleware, metrics_endpoint
from internal.server.middleware.rate_limit import RateLimitMiddleware, RateLimitSettings
from internal.server.middleware.security import SecurityHeadersMiddleware
from internal.service.auth import AuthService
from internal.service.chunk import ChunkService
from internal.service.document import DocumentService
from internal.service.knowledge_base import KnowledgeBaseService
from internal.streaming.sse import SSE_DONE, format_sse_data
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode
from pkg.errors.diagnostics import unexpected_error_details
from pkg.errors.fastapi import register_exception_handlers
from pkg.session import SessionManager

if TYPE_CHECKING:
    from internal.service.rag import RAGService
    from internal.service.retrieval import RetrievalService

logger = structlog.get_logger()


# 清理已废弃的数据库列；启动和显式迁移共用，目标列不存在时跳过。
async def _drop_unused_shell_columns(engine: AsyncEngine) -> None:

    async def _drop(table: str, column: str, constraint: str | None = None) -> None:
        """列存在时删除指定废弃列；PostgreSQL 下先移除指定约束，避免约束阻止删列。"""
        async with engine.begin() as conn:

            def _has_column(sync_conn) -> bool:
                """通过同步 SQLAlchemy 检查器确认目标表和目标列均存在。"""
                inspector = inspect(sync_conn)
                if table not in inspector.get_table_names():
                    return False
                return column in {c["name"] for c in inspector.get_columns(table)}

            exists = await conn.run_sync(_has_column)
            if not exists:
                return

            dialect = engine.dialect.name
            if dialect == "postgresql" and constraint:
                await conn.execute(
                    sql_text(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
                )
            quoted = f'"{column}"' if dialect == "sqlite" else column
            await conn.execute(sql_text(f"ALTER TABLE {table} DROP COLUMN {quoted}"))
            logger.info("schema_dropped_column", table=table, column=column)

    await _drop("users", "global_role", "ck_users_global_role")
    await _drop("knowledge_bases", "visibility", "ck_kb_visibility")
    await _drop("chunks", "summary")
    await _drop("chunks", "related_questions")


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str
    detail: str | None = None


class HTTPServer:
    """组装 HTTP 路由、中间件与生命周期回调，依赖通过构造函数注入。"""

    def __init__(
        self,
        server_config: ServerConfig,
        auth_service: AuthService,
        session_manager: SessionManager,
        session_config: SessionConfig,
        document_service: DocumentService | None = None,
        knowledge_base_service: KnowledgeBaseService | None = None,
        chunk_service: ChunkService | None = None,
        retrieval_service: RetrievalService | None = None,
        rag_service: RAGService | None = None,
        session_factory: async_sessionmaker | None = None,
        engine: AsyncEngine | None = None,
        rate_limit_settings: RateLimitSettings | None = None,
        ingestion: object | None = None,
        # memory 向量库为进程内易失结构，重启后清空，
        # 启动时需从 DB 重建向量索引。pgvector 持久化则无需重建。
        rebuild_vectors: bool = False,
        # 使用 pgvector 时：启动时幂等创建 extension + chunk_vectors。
        ensure_pgvector: bool = False,
        vector_dimension: int = 1024,
    ) -> None:
        """注入 HTTP 配置和应用服务；设置数据库、限流及启动时索引处理选项。"""
        self._server_config = server_config
        self._auth_service = auth_service
        self._session_manager = session_manager
        self._session_config = session_config
        self._document_service = document_service
        self._knowledge_base_service = knowledge_base_service
        self._chunk_service = chunk_service
        self._retrieval_service = retrieval_service
        self._rag_service = rag_service
        self._session_factory = session_factory
        self._engine = engine
        self._rate_limit_settings = rate_limit_settings
        # 启动时为 hybrid 检索重建索引
        self._ingestion = ingestion
        self._rebuild_vectors = rebuild_vectors
        self._ensure_pgvector = ensure_pgvector
        self._vector_dimension = vector_dimension

    def _create_lifespan(self):
        """创建 lifespan 闭包 — 通过闭包捕获配置，不依赖 app.state。"""
        service_name = self._server_config.service_name

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
            """接收请求前准备表结构、恢复索引并启动问答记录清理；退出时关闭相关服务和连接。"""
            logger.info("app_starting", service=service_name)
            # 启动时幂等建表（已存在则跳过）
            if self._engine is not None:
                from internal.data.models import Base
                async with self._engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
                await _drop_unused_shell_columns(self._engine)
            if self._ensure_pgvector and self._engine is not None:
                from internal.data.vector_store.pgvector import ensure_pgvector_schema
                await ensure_pgvector_schema(
                    self._engine,
                    dimension=self._vector_dimension,
                )
            # 接收请求前核实索引并重建进程内数据；单文档失败写入其阶段状态。
            # 无法列出文档或缺少必需的状态迁移时，异常向上传播并中止启动。
            if self._ingestion is not None:
                await self._warmup_indices()
            if self._rag_service is not None:
                await self._rag_service.start()
            yield
            if self._rag_service is not None:
                await self._rag_service.close()
            # 关闭 Redis 连接
            await self._session_manager.close()
            logger.info("app_stopping", service=service_name)

        return lifespan

    async def _warmup_indices(self) -> None:
        """调用入库编排核实向量记录，并按当前配置重建进程内索引。"""
        count = await self._ingestion.rebuild_indices(
            include_vectors=self._rebuild_vectors,
        )
        logger.info("indices_warmup_done", chunk_count=count)

    def create(self) -> FastAPI:
        """创建 FastAPI 应用实例。

        挂载中间件、异常处理器和各业务路由，启动任务由 lifespan 执行。
        """
        config = self._server_config

        debug = config.debug

        app = FastAPI(
            title=config.app_title,
            version=config.app_version,
            description=config.app_description,
            lifespan=self._create_lifespan(),
            debug=debug,
        )

        # 注册 CORS；实际请求顺序由后续中间件的添加顺序决定。
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=config.cors_credentials,
            allow_methods=config.cors_methods,
            allow_headers=config.cors_headers,
        )

        # 安全响应头
        app.add_middleware(SecurityHeadersMiddleware)

        # ── 中间件注册（Starlette add_middleware 为 LIFO：后添加的先执行）──
        # 执行顺序：Metrics → Auth → RateLimit → Security → CORS → Route
        # RateLimit 中间件（Auth 之前注册 = 更内层 = 后执行）
        if self._rate_limit_settings is not None and self._rate_limit_settings.rate_limiter is not None:
            app.add_middleware(
                RateLimitMiddleware,
                rate_limiter=self._rate_limit_settings.rate_limiter,
                standard_limit=self._rate_limit_settings.standard_limit,
                heavy_limit=self._rate_limit_settings.heavy_limit,
                window_seconds=self._rate_limit_settings.window_seconds,
                enabled=self._rate_limit_settings.enabled,
            )

        # Auth 中间件（RateLimit 之后注册 = 更外层 = 先执行，设置 user_id）
        app.add_middleware(
            AuthMiddleware,
            session_manager=self._session_manager,
            session_config=self._session_config,
        )

        # Prometheus 指标中间件
        app.add_middleware(MetricsMiddleware)

        register_exception_handlers(app)

        # 路由注册 — 每个业务模块独立注册
        app.include_router(self._health_router())
        app.include_router(self._auth_router())
        app.include_router(self._ops_router())
        if self._knowledge_base_service is not None:
            app.include_router(self._knowledge_base_router())
        if self._document_service is not None:
            app.include_router(self._document_router())
        if self._chunk_service is not None:
            app.include_router(self._chunk_router())
        if self._retrieval_service is not None:
            app.include_router(self._retrieval_router())
        if self._rag_service is not None:
            app.include_router(self._rag_router())

        # ── 前端工作台静态文件 ──
        web_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "web")
        )
        if os.path.isdir(web_dir):
            app.mount("/web", StaticFiles(directory=web_dir, html=True), name="web")

        return app

    def _health_router(self) -> APIRouter:
        """健康检查。

        /live：进程在跑就返回 ok。
        /ready：数据库和 Redis 都能连上才返回 ok。
        """
        router = APIRouter(prefix="/health", tags=["Health"])
        engine = self._engine
        session_manager = self._session_manager

        @router.get("/live", summary="进程是否在跑", response_model=HealthResponse)
        async def liveness() -> HealthResponse:
            """进程在跑就返回 ok。"""
            return HealthResponse(status="ok")

        @router.get("/ready", summary="数据库和 Redis 是否可用", response_model=HealthResponse)
        async def readiness() -> HealthResponse:
            """数据库和 Redis 都能连上才返回 ok。"""
            detail_parts: list[str] = []

            # 检查数据库
            if engine is not None:
                try:
                    async with engine.connect() as conn:
                        await conn.execute(sql_text("SELECT 1"))
                    detail_parts.append("db:ok")
                except Exception as e:
                    logger.error("readiness_db_fail", error=str(e))
                    return HealthResponse(status="fail", detail=f"db:{type(e).__name__}")
            else:
                detail_parts.append("db:skip")

            # 检查 Redis
            try:
                await session_manager.ping()
                detail_parts.append("redis:ok")
            except Exception as e:
                logger.error("readiness_redis_fail", error=str(e))
                return HealthResponse(
                    status="fail", detail=f"redis:{type(e).__name__}"
                )

            return HealthResponse(status="ok", detail=" ".join(detail_parts))

        return router

    def _ops_router(self) -> APIRouter:
        """运维路由 — /metrics 等运维端点。"""
        router = APIRouter(tags=["Ops"])

        @router.get("/metrics", summary="Prometheus 指标", include_in_schema=False)
        async def metrics():
            """Prometheus 指标抓取端点。"""
            return metrics_endpoint()

        return router

    def _auth_router(self) -> APIRouter:
        """认证路由 — 注册/登录/登出/刷新/当前用户。"""
        router = APIRouter(prefix="/api/v1/users", tags=["Auth"])
        auth_service = self._auth_service

        @router.post("/register", response_model=UserResponse, status_code=201)
        async def register(request: RegisterRequest) -> UserResponse:
            """用户注册。"""
            return await auth_service.register(request)

        @router.post("/login", response_model=LoginResponse)
        async def login(request: LoginRequest, http_request: Request) -> LoginResponse:
            """用户登录，返回 access_token + refresh_token。

            从 User-Agent header 提取设备指纹，绑定到 session。
            """
            user_agent = http_request.headers.get("User-Agent", "")
            return await auth_service.login(request, user_agent)

        @router.post("/logout")
        async def logout(request: Request) -> dict:
            """用户登出 — 需要登录。"""
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "")
            await auth_service.logout(token)
            return {"status": "ok"}

        @router.get("/me", response_model=UserResponse)
        async def me(request: Request) -> UserResponse:
            """获取当前用户信息 — 需要登录。"""
            user_id = request.state.user_id
            return await auth_service.get_current_user(user_id)

        @router.post("/token/refresh", response_model=RefreshTokenResponse)
        async def refresh_token(
            request: RefreshTokenRequest, http_request: Request
        ) -> RefreshTokenResponse:
            """刷新 access_token。"""
            user_agent = http_request.headers.get("User-Agent", "")
            return await auth_service.refresh_token(request.refresh_token, user_agent)

        return router

    def _knowledge_base_router(self) -> APIRouter:
        """知识库路由 — 列表 / 创建 / 详情 / 更新 / 删除。"""
        router = APIRouter(prefix="/api/v1/knowledge/bases", tags=["KnowledgeBases"])
        kb_service = self._knowledge_base_service
        assert kb_service is not None

        @router.get("", response_model=KnowledgeBaseListResponse)
        async def list_knowledge_bases(request: Request) -> KnowledgeBaseListResponse:
            """返回当前用户的知识库及计数；列表前补齐目录，目录为空时创建默认库。"""
            user_id = request.state.user_id
            return await kb_service.list_knowledge_bases(user_id=user_id)

        @router.post("", response_model=KnowledgeBaseResponse, status_code=201)
        async def create_knowledge_base(
            body: KnowledgeBaseCreateRequest,
            request: Request,
        ) -> KnowledgeBaseResponse:
            """创建知识库。"""
            user_id = request.state.user_id
            return await kb_service.create_knowledge_base(user_id=user_id, body=body)

        @router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
        async def get_knowledge_base(kb_id: int, request: Request) -> KnowledgeBaseResponse:
            """查单个知识库详情。"""
            return await kb_service.get_knowledge_base(kb_id, user_id=request.state.user_id)

        @router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
        async def update_knowledge_base(
            kb_id: int,
            body: KnowledgeBaseUpdateRequest,
            request: Request,
        ) -> KnowledgeBaseResponse:
            """更新知识库名称 / 描述 / 图标。"""
            return await kb_service.update_knowledge_base(kb_id, body, user_id=request.state.user_id)

        @router.delete("/{kb_id}", status_code=204)
        async def delete_knowledge_base(kb_id: int, request: Request) -> None:
            """删除知识库，并级联删除其下文档、分块与向量。"""
            user_id = request.state.user_id
            await kb_service.delete_knowledge_base(kb_id=kb_id, user_id=user_id)

        return router

    def _document_router(self) -> APIRouter:
        """文档路由 — 上传/列表/删除文档。"""
        router = APIRouter(prefix="/api/v1/knowledge/bases/{kb_id}/documents", tags=["Documents"])
        document_service = self._document_service
        kb_service = self._knowledge_base_service
        assert document_service is not None and kb_service is not None

        async def authorize_document(kb_id: int, doc_id: int, user_id: int) -> DocumentResponse:
            # 知识库所有权与文档归属在路由统一校验，不能只凭文档 ID 调用业务操作。
            await kb_service.get_knowledge_base(kb_id, user_id=user_id)
            document = await document_service.get_document(doc_id, user_id=user_id)
            if document.knowledge_base_id != kb_id:
                raise APIError(
                    code=ErrorCode.NOT_FOUND,
                    reason="DOCUMENT_NOT_FOUND",
                    message="Document not found.",
                )
            return document

        @router.post("", response_model=DocumentResponse, status_code=201)
        async def upload_document(kb_id: int, request: Request) -> DocumentResponse:
            """上传文档到知识库。"""
            user_id = request.state.user_id
            await kb_service.get_knowledge_base(kb_id, user_id=user_id)
            form = await request.form()
            file = form.get("file")
            if file is None:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    reason="MISSING_FILE",
                    message="No file provided in the 'file' field.",
                )
            content = await file.read()
            filename = file.filename or "unnamed"
            return await document_service.upload(
                knowledge_base_id=kb_id,
                user_id=user_id,
                filename=filename,
                content=content,
            )

        @router.get("", response_model=DocumentListResponse)
        # 确认当前用户拥有路径中的知识库，再返回该库的文档列表。
        async def list_documents(kb_id: int, request: Request) -> DocumentListResponse:
            user_id = request.state.user_id
            await kb_service.get_knowledge_base(kb_id, user_id=user_id)
            return await document_service.list_documents(knowledge_base_id=kb_id, user_id=user_id)

        @router.get("/{doc_id}", response_model=DocumentResponse)
        # 同时核对知识库所有权及文档归属，再返回文档详情。
        async def get_document(kb_id: int, doc_id: int, request: Request) -> DocumentResponse:
            return await authorize_document(kb_id, doc_id, request.state.user_id)

        @router.delete("/{doc_id}", status_code=204)
        # 核对知识库、文档及当前用户的关系后，删除文档及关联资源。
        async def delete_document(kb_id: int, doc_id: int, request: Request) -> None:
            user_id = request.state.user_id
            await authorize_document(kb_id, doc_id, user_id)
            await document_service.delete_document(doc_id, user_id=user_id)

        @router.post("/{doc_id}/parse", response_model=DocumentResponse)
        # 完成归属校验后触发文档解析；force 控制是否重新处理已有结果。
        async def parse_document(
            kb_id: int,
            doc_id: int,
            request: Request,
            body: ParseDocumentRequest = ParseDocumentRequest(),
        ) -> DocumentResponse:
            user_id = request.state.user_id
            await authorize_document(kb_id, doc_id, user_id)
            return await document_service.parse_document(document_id=doc_id, user_id=user_id, force=body.force)

        @router.post("/{doc_id}/retry", response_model=DocumentResponse)
        # 完成归属校验后重试指定入库阶段；stage 为空时由入库业务确定处理起点。
        async def retry_document(
            kb_id: int, doc_id: int, request: Request, body: RetryDocumentRequest,
        ) -> DocumentResponse:
            user_id = request.state.user_id
            await authorize_document(kb_id, doc_id, user_id)
            return await document_service.retry(doc_id, user_id, body.stage)

        return router
    def _chunk_router(self) -> APIRouter:
        """分块路由 — 查看文档的分块列表。"""
        router = APIRouter(
            prefix="/api/v1/knowledge/bases/{kb_id}/documents/{doc_id}/chunks",
            tags=["Chunks"],
        )
        chunk_service = self._chunk_service
        document_service = self._document_service
        kb_service = self._knowledge_base_service
        assert chunk_service is not None and document_service is not None and kb_service is not None

        @router.get("", response_model=ChunkListResponse)
        # 核对知识库所有权和文档归属，只返回该文档本轮可展示的分块。
        async def list_chunks(kb_id: int, doc_id: int, request: Request) -> ChunkListResponse:
            user_id = request.state.user_id
            await kb_service.get_knowledge_base(kb_id, user_id=user_id)
            document = await document_service.get_document(doc_id, user_id=user_id)
            if document.knowledge_base_id != kb_id:
                raise APIError(
                    code=ErrorCode.NOT_FOUND,
                    reason="DOCUMENT_NOT_FOUND",
                    message="Document not found.",
                )
            return await chunk_service.list_chunks(document_id=doc_id, user_id=user_id)

        return router
    def _retrieval_router(self) -> APIRouter:
        """检索路由 — 调用当前配置的检索服务返回知识库资料。"""
        router = APIRouter(
            prefix="/api/v1/knowledge/bases/{kb_id}/retrieve",
            tags=["Retrieval"],
        )
        retrieval_service = self._retrieval_service

        @router.post("", response_model=RetrievalResponse)
        async def retrieve(
            kb_id: int,
            body: RetrievalRequest,
            request: Request,
        ) -> RetrievalResponse:
            """检索与 query 最相关的 chunks。"""
            await self._knowledge_base_service.get_knowledge_base(kb_id, user_id=request.state.user_id)
            return await retrieval_service.retrieve(
                query=body.query,
                knowledge_base_id=kb_id,
                top_k=body.top_k,
            )

        return router

    def _rag_router(self) -> APIRouter:
        """问答路由：校验知识库权限，再转交同步或 SSE 应用服务。"""
        router = APIRouter(
            prefix="/api/v1/knowledge/bases/{kb_id}/rag",
            tags=["RAG"],
        )
        rag_service = self._rag_service

        @router.post("/query", response_model=RAGQueryResponse, summary="RAG 检索增强问答")
        async def rag_query(
            kb_id: int,
            body: RAGQueryRequest,
            request: Request,
        ) -> RAGQueryResponse:
            # 路径中的知识库和请求额外选择的知识库都要逐一检查所有权。
            await self._knowledge_base_service.get_knowledge_base(kb_id, user_id=request.state.user_id)
            allowed = body.knowledge_base_ids or [kb_id]
            for kid in allowed:
                if kid != kb_id:
                    await self._knowledge_base_service.get_knowledge_base(kid, user_id=request.state.user_id)
            return await rag_service.query(
                query=body.query,
                knowledge_base_ids=allowed,
                top_k=body.top_k,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                user_id=request.state.user_id,
                allow_web=body.allow_web,
                user_urls=tuple(body.user_urls),
            )

        @router.post(
            "/query/stream",
            summary="RAG 检索增强问答（SSE 流式）",
            responses={
                200: {
                    "description": "SSE 流式响应",
                    "content": {
                        "text/event-stream": {
                            "example": 'data: {"type": "event", "kind": "run_started", ...}\n\ndata: {"type": "result", ...}\n\ndata: [DONE]\n\n',
                        }
                    },
                }
            },
        )
        async def rag_query_stream(
            kb_id: int,
            body: RAGQueryRequest,
            request: Request,
        ) -> StreamingResponse:
            # 在开始流式响应前完成权限检查，避免建立连接后才发现越权。
            await self._knowledge_base_service.get_knowledge_base(kb_id, user_id=request.state.user_id)
            allowed = body.knowledge_base_ids or [kb_id]
            for kid in allowed:
                if kid != kb_id:
                    await self._knowledge_base_service.get_knowledge_base(kid, user_id=request.state.user_id)
            async def event_generator():
                # 转发同一次问答的进度和最终结果，结束后发送 SSE 结束标记。
                try:
                    async with aclosing(rag_service.query_stream(
                        query=body.query, knowledge_base_ids=allowed, top_k=body.top_k,
                        temperature=body.temperature, max_tokens=body.max_tokens,
                        user_id=request.state.user_id, allow_web=body.allow_web,
                        user_urls=tuple(body.user_urls),
                    )) as events:
                        async for event in events:
                            yield format_sse_data(event)
                    yield SSE_DONE
                except Exception as exc:
                    # 流式响应建立后通过 error 帧报告执行或快照保存失败，再发送结束标记。
                    logger.error("rag_stream_error", **unexpected_error_details(exc))
                    yield format_sse_data({
                        "type": "error", "version": 6, "tool_contract_version": 3,
                        "answer_contract_version": ANSWER_CONTRACT_VERSION,
                        "error_code": "RUN_STREAM_FAILED",
                        "error": "问答传输或运行记录保存失败，请重新提问",
                    })
                    yield SSE_DONE

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
                },
            )

        return router


















