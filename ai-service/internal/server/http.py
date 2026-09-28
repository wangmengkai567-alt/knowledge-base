"""
internal/server/http.py — HTTP Server 创建与路由注册

只做路由注册和中间件挂载，不含业务逻辑。

设计要点：
- HTTPServer 类只负责创建 FastAPI app 并挂载中间件。
- 路由按业务模块拆成独立方法。
- lifespan 用闭包创建，通过构造函数注入配置。
- /health/live 只表示进程在跑；/health/ready 检查数据库和 Redis。
- 注册顺序：CORS → SecurityHeaders → AuthMiddleware → 异常处理器 → 路由。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from sqlalchemy import inspect, text as sql_text

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
)
from api.openapi.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from api.openapi.schemas.retrieval import RetrievalRequest, RetrievalResponse
from api.openapi.schemas.rag import RAGQueryRequest, RAGQueryResponse
from internal.conf.config import ServerConfig, SessionConfig
from internal.server.middleware.auth import AuthMiddleware
from internal.server.middleware.security import SecurityHeadersMiddleware
from internal.server.middleware.rate_limit import RateLimitMiddleware, RateLimitSettings
from internal.server.middleware.metrics import MetricsMiddleware, metrics_endpoint
from internal.service.auth import AuthService
from internal.service.chunk import ChunkService
from internal.service.document import DocumentService
from internal.service.knowledge_base import KnowledgeBaseService
from internal.service.retrieval import RetrievalService
from internal.service.rag import RAGService
from internal.biz.rag import RAG_EVENT_SOURCES, RAG_EVENT_CHUNK
from internal.streaming.sse import format_sse_data, SSE_DONE
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode
from pkg.errors.fastapi import register_exception_handlers
from pkg.session import SessionManager

logger = structlog.get_logger()


async def _drop_unused_shell_columns(engine: AsyncEngine) -> None:

    async def _drop(table: str, column: str, constraint: str | None = None) -> None:
        async with engine.begin() as conn:

            def _has_column(sync_conn) -> bool:
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
    """HTTP Server — 协议适配层。

    持有 ServerConfig + AuthService + DocumentService + SessionManager，
    create() 返回组装好的 FastAPI app。
    """

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
        embedding_biz: object | None = None,
        # Sprint 18 修复：memory 向量库为进程内易失结构，重启后清空，
        # 启动时需从 DB 重建向量索引。pgvector 持久化则无需重建。
        rebuild_vectors: bool = False,
        # 使用 pgvector 时：启动时幂等创建 extension + chunk_vectors。
        ensure_pgvector: bool = False,
        vector_dimension: int = 1024,
    ) -> None:
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
        self._embedding_biz = embedding_biz
        self._rebuild_vectors = rebuild_vectors
        self._ensure_pgvector = ensure_pgvector
        self._vector_dimension = vector_dimension

    def _create_lifespan(self):
        """创建 lifespan 闭包 — 通过闭包捕获配置，不依赖 app.state。"""
        service_name = self._server_config.service_name

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
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
            # 易失结构，重启后清空。阻塞式重建，确保对外服务前索引就绪，
            # 否则首次检索会返回空（"未找到相关结果"）。失败仅告警，不阻断启动。
            if self._embedding_biz is not None:
                await self._warmup_indices()
            yield
            # 关闭 Redis 连接
            await self._session_manager.close()
            logger.info("app_stopping", service=service_name)

        return lifespan

    async def _warmup_indices(self) -> None:
        """启动时重建内存索引（vector store + keyword index）。"""
        try:
            # type: ignore — 避免在此处硬依赖 biz 具体类型
            count = await self._embedding_biz.rebuild_indices(  # type: ignore[attr-defined]
                include_vectors=self._rebuild_vectors,
            )
            if count > 0:
                logger.info(
                    "indices_warmup_done",
                    chunk_count=count,
                    rebuild_vectors=self._rebuild_vectors,
                )
            else:
                logger.warning(
                    "indices_warmup_empty",
                    hint="no chunks indexed; search will return empty. "
                         "upload/process a document first.",
                )
        except Exception as e:
            logger.warning(
                "indices_warmup_failed",
                error=str(e),
                exc_info=True,
            )

    def create(self) -> FastAPI:
        """创建 FastAPI 应用实例。

        注册顺序：CORS → SecurityHeaders → AuthMiddleware → 异常处理器 → 路由。
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

        # CORS（最外层）
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
            """列出知识库（含文档数 / 分块数）。空库会种默认库并回填存量文档 ID。"""
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
        async def list_documents(kb_id: int, request: Request) -> DocumentListResponse:
            user_id = request.state.user_id
            await kb_service.get_knowledge_base(kb_id, user_id=user_id)
            return await document_service.list_documents(knowledge_base_id=kb_id, user_id=user_id)

        @router.get("/{doc_id}", response_model=DocumentResponse)
        async def get_document(kb_id: int, doc_id: int, request: Request) -> DocumentResponse:
            return await authorize_document(kb_id, doc_id, request.state.user_id)

        @router.delete("/{doc_id}", status_code=204)
        async def delete_document(kb_id: int, doc_id: int, request: Request) -> None:
            user_id = request.state.user_id
            await authorize_document(kb_id, doc_id, user_id)
            await document_service.delete_document(doc_id, user_id=user_id)

        @router.post("/{doc_id}/parse", response_model=DocumentResponse)
        async def parse_document(
            kb_id: int,
            doc_id: int,
            request: Request,
            body: ParseDocumentRequest = ParseDocumentRequest(),
        ) -> DocumentResponse:
            user_id = request.state.user_id
            await authorize_document(kb_id, doc_id, user_id)
            return await document_service.parse_document(document_id=doc_id, user_id=user_id, force=body.force)

        @router.post("/{doc_id}/embed", response_model=DocumentResponse)
        async def retry_document_embeddings(kb_id: int, doc_id: int, request: Request) -> DocumentResponse:
            user_id = request.state.user_id
            await authorize_document(kb_id, doc_id, user_id)
            return await document_service.retry_embeddings(document_id=doc_id, user_id=user_id)

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
        """检索路由 — 知识库内向量检索。"""
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
        """RAG 路由 — 检索增强生成（Sprint 12/13）。"""
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
            await self._knowledge_base_service.get_knowledge_base(kb_id, user_id=request.state.user_id)
            allowed = body.knowledge_base_ids or [kb_id]
            for kid in allowed:
                if kid != kb_id:
                    await self._knowledge_base_service.get_knowledge_base(kid, user_id=request.state.user_id)
            return await rag_service.query(
                query=body.query,
                knowledge_base_id=kb_id,
                top_k=body.top_k,
                temperature=body.temperature,
                max_tokens=body.max_tokens,
                chunk_ids=body.chunk_ids,
                allowed_kb_ids=allowed,
            )

        @router.post(
            "/query/stream",
            summary="RAG 检索增强问答（SSE 流式）",
            responses={
                200: {
                    "description": "SSE 流式响应",
                    "content": {
                        "text/event-stream": {
                            "example": 'data: {"type": "sources", ...}\n\ndata: {"type": "chunk", ...}\n\ndata: [DONE]\n\n',
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
            await self._knowledge_base_service.get_knowledge_base(kb_id, user_id=request.state.user_id)
            allowed = body.knowledge_base_ids or [kb_id]
            for kid in allowed:
                if kid != kb_id:
                    await self._knowledge_base_service.get_knowledge_base(kid, user_id=request.state.user_id)
            async def event_generator():
                try:
                    async for event in rag_service.query_stream(
                        query=body.query,
                        knowledge_base_id=kb_id,
                        top_k=body.top_k,
                        temperature=body.temperature,
                        max_tokens=body.max_tokens,
                        chunk_ids=body.chunk_ids,
                        allowed_kb_ids=allowed,
                    ):
                        if event.event_type == RAG_EVENT_SOURCES:
                            # 发送 sources 元数据
                            sources_data = [
                                {
                                    "chunk_id": s.chunk_id,
                                    "content": s.chunk_content[:200],
                                    "score": round(s.score, 4),
                                    "document_id": s.document_id,
                                    "document_filename": s.document_filename,
                                    "knowledge_base_id": s.knowledge_base_id,
                                }
                                for s in event.sources
                            ]
                            yield format_sse_data({
                                "type": "sources",
                                "sources": sources_data,
                                "source_count": len(sources_data),
                            })
                        elif event.event_type == RAG_EVENT_CHUNK and event.chunk:
                            # 发送 LLM 流式分片
                            yield format_sse_data({
                                "type": "chunk",
                                "delta": event.chunk.delta,
                                "model": event.chunk.model,
                                "finish_reason": event.chunk.finish_reason,
                            })
                    yield SSE_DONE
                except Exception as e:
                    logger.error("rag_stream_error", error=str(e), exc_info=True)
                    yield format_sse_data({"type": "error", "error": str(e)})

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


















