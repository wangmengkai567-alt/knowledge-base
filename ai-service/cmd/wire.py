"""
cmd/wire.py — 依赖注入编排

所有依赖组装集中在一处，整条链路一目了然。
组装顺序从底层到顶层：Config → Logging → DB → Redis → Repo → Biz → Service → Server。

设计要点：
- create_app() 是唯一工厂函数，封装完整的依赖组装逻辑。
- 模块级 app 变量供 uvicorn.run("cmd.wire:app") 字符串引用加载。
- _settings 供 main.py 读取 host/port/workers 等启动参数。
- 不再有双重初始化：模块级变量通过 create_app() 创建，不重复组装。
- 不再有模块级 asyncio.run()：建表逻辑在 lifespan 中执行。
"""

from __future__ import annotations

import os

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from internal.biz.auth import AuthBiz
from internal.biz.chunk import ChunkBiz
from internal.biz.document import DocumentBiz
from internal.biz.embedding import EmbeddingBiz
from internal.biz.knowledge_base import KnowledgeBaseBiz
from internal.biz.retrieval import RetrievalBiz
from internal.biz.rag import RAGBiz
from internal.conf.config import AppConfig, get_settings
from internal.conf.logging import setup_logging
from internal.data.chunk.factory import create_chunk_repo
from internal.data.chunker.factory import create_text_chunker
from internal.data.database import create_engine, create_session_factory
from internal.data.document.factory import create_document_repo
from internal.data.embedding.factory import create_embedding_provider
from internal.data.embedding.cache import CachedEmbeddingProvider
from internal.data.knowledge_base.factory import create_knowledge_base_repo
from internal.data.keyword.factory import create_keyword_store
from internal.data.llm.factory import create_llm_provider
from internal.data.parser.factory import create_document_parser
from internal.data.reranker.factory import create_reranker
from internal.data.retriever.factory import create_retriever
from internal.data.storage.factory import create_file_storage_repo
from internal.data.user.factory import create_user_repo
from internal.data.vector_store.factory import create_vector_store
from internal.prompt.manager import PromptManager
from internal.server.http import HTTPServer
from internal.server.middleware.rate_limit import create_rate_limiter, RateLimitSettings
from internal.service.auth import AuthService
from internal.service.rag import RAGService
from internal.service.chunk import ChunkService
from internal.service.document import DocumentService
from internal.service.knowledge_base import KnowledgeBaseService
from internal.service.retrieval import RetrievalService
from pkg.session import SessionManager

logger = structlog.get_logger()


def _create_redis_client(redis_url: str) -> aioredis.Redis:
    
    if os.environ.get("AI_ENV") == "dev":
        try:
            import fakeredis.aioredis
            return fakeredis.aioredis.FakeRedis(decode_responses=True)
        except ImportError:
            pass

    pool = aioredis.ConnectionPool.from_url(
        redis_url,
        decode_responses=True,
        max_connections=50,
        socket_timeout=5,
        socket_connect_timeout=5,
        retry_on_timeout=True,
    )
    return aioredis.Redis(connection_pool=pool)


def create_app() -> FastAPI:
    """工厂函数 — 组装所有依赖并返回 FastAPI app 实例。

    组装顺序：Config → Logging → DB → Redis → Repo → Biz → Service → Server。
    """
    settings = get_settings()
    setup_logging(settings.log)

    # ── DB ──
    engine = create_engine(settings.database)
    session_factory = create_session_factory(engine)

    # ── Redis ──
    redis_client = _create_redis_client(settings.redis.url)
    session_manager = SessionManager(
        redis_client=redis_client,
        redis_config=settings.redis,
        session_config=settings.session,
    )

    # ── Repo → Biz → Service ──
    user_repo = create_user_repo(session_factory)
    auth_biz = AuthBiz(
        user_repo=user_repo,
        session_manager=session_manager,
        session_config=settings.session,
        security_config=settings.security,
    )
    auth_service = AuthService(auth_biz=auth_biz)

    # ── Document 链路 ──
    document_repo = create_document_repo(session_factory)
    file_storage_repo = create_file_storage_repo(
        upload_dir=settings.storage.upload_dir,
    )

    # ── Chunk 链路（Sprint 6）──
    chunk_repo = create_chunk_repo(session_factory)
    text_chunker = create_text_chunker(
        chunk_size=settings.rag.chunk_size,
        chunk_overlap=settings.rag.chunk_overlap,
    )

    # ── LLM & Prompt（供分块摘要与对话复用，需在 ChunkBiz 前创建）──
    prompt_manager = PromptManager(template_dir=settings.prompt.template_dir)
    prompt_manager.load_all()

    llm_provider = create_llm_provider(
        provider=settings.llm.provider,
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key,
        model=settings.llm.chat_model,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_tokens,
        timeout=settings.llm.timeout,
        keep_alive=settings.llm.keep_alive,
    )
    logger.info(
        "llm_provider_initialized",
        provider=settings.llm.provider,
        model=settings.llm.chat_model,
        is_mock=llm_provider.is_mock,
    )

    # ── Embedding 链路（Sprint 7）──
    embedding_provider = CachedEmbeddingProvider(
        create_embedding_provider(
            provider=settings.embedding.provider,
            base_url=settings.embedding.base_url,
            api_key=settings.embedding.api_key,
            model=settings.embedding.model,
            dimension=settings.embedding.dimension,
            timeout=settings.embedding.timeout,
            keep_alive=settings.embedding.keep_alive,
            max_concurrency=settings.embedding.max_concurrency,
        )
    )
    logger.info(
        "embedding_provider_initialized",
        provider=settings.embedding.provider,
        model=settings.embedding.model,
        dimension=settings.embedding.dimension,
        query_cache_entries=1024,
    )
    vector_store = create_vector_store(
        provider=settings.vector_store.provider,
        session_factory=session_factory,
        dimension=settings.embedding.dimension,
    )


    # 仅当启用 hybrid 检索时创建，避免纯向量场景的内存开销。
    keyword_store = None
    if settings.rag.retriever_provider == "hybrid":
        keyword_store = create_keyword_store(
            k1=settings.keyword_store.bm25_k1,
            b=settings.keyword_store.bm25_b,
        )
        logger.info(
            "keyword_store_initialized",
            k1=settings.keyword_store.bm25_k1,
            b=settings.keyword_store.bm25_b,
        )

    embedding_biz = EmbeddingBiz(
        chunk_repo=chunk_repo,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        batch_size=settings.embedding.batch_size,
        keyword_store=keyword_store,
        # Sprint 18-fix (P0)：注入 document_repo，供 metadata 写入 knowledge_base_id，
        # 保障多知识库场景下 Retriever 能在存储层完成 kb 隔离。
        document_repo=document_repo,
    )

    # ChunkBiz 回调触发 EmbeddingBiz
    chunk_biz = ChunkBiz(
        chunk_repo=chunk_repo,
        chunker=text_chunker,
        on_chunk_success=embedding_biz.embed_document,
        on_chunk_delete=embedding_biz.delete_embeddings,
    )
    # ChunkService 需要 DocumentBiz 做所有权校验，先创建 document_biz 再组装
    document_biz = DocumentBiz(
        document_repo=document_repo,
        file_storage_repo=file_storage_repo,
        parser_factory=create_document_parser,
        max_file_size=settings.security.max_request_body_size,
        on_parse_success=chunk_biz.chunk_document,
        on_document_delete=chunk_biz.delete_chunks,
    )
    chunk_service = ChunkService(chunk_biz=chunk_biz, document_biz=document_biz)
    document_service = DocumentService(
        document_biz=document_biz,
        embedding_biz=embedding_biz,
    )

    kb_repo = create_knowledge_base_repo(session_factory)
    kb_biz = KnowledgeBaseBiz(
        kb_repo=kb_repo,
        document_repo=document_repo,
        document_biz=document_biz,
        default_embedding_model=settings.embedding.model,
        default_chunk_size=settings.rag.chunk_size,
        default_chunk_overlap=settings.rag.chunk_overlap,
    )
    knowledge_base_service = KnowledgeBaseService(kb_biz=kb_biz)

    # ── Retrieval 链路──
    # ── Reranker ──
    # 提前在 Retrieval 链路之前创建，使搜索（retrieve）与 RAG 共用同一 Reranker。
    # 仅当开启 rerank 时创建实例；未开启则 reranker=None，检索/重排均优雅降级。
    reranker = None
    if settings.rag.rerank_enabled:
        reranker = create_reranker(
            provider=settings.reranker.provider,
            base_url=settings.reranker.base_url,
            api_key=settings.reranker.api_key,
            model=settings.reranker.model,
            timeout=settings.reranker.timeout,
        )
        logger.info("reranker_initialized", provider=settings.reranker.provider)

    # 根据 retriever_provider 选择 vector 或 hybrid 策略。
    retriever = create_retriever(
        strategy=settings.rag.retriever_provider,
        vector_store=vector_store,
        keyword_store=keyword_store,
        rrf_k=settings.rag.rrf_k,
        candidate_multiplier=settings.rag.rrf_candidate_multiplier,
    )
    logger.info(
        "retriever_initialized",
        strategy=settings.rag.retriever_provider,
        rrf_k=settings.rag.rrf_k if settings.rag.retriever_provider == "hybrid" else None,
        candidate_multiplier=settings.rag.rrf_candidate_multiplier if settings.rag.retriever_provider == "hybrid" else None,
        query_expand=settings.rag.query_expand_enabled,
        context_window=settings.rag.context_window,
    )
    retrieval_biz = RetrievalBiz(
        embedding_provider=embedding_provider,
        retriever=retriever,
        chunk_repo=chunk_repo,
        document_repo=document_repo,
        # 否则纯向量语义检索对短关键词（如 "docker"）会召回不相关文档排在前。
        reranker=reranker,
        rerank_enabled=settings.rag.rerank_enabled,
        rerank_top_k=settings.rag.rerank_top_k,
        min_relevance=settings.rag.min_relevance,
        query_expand_enabled=settings.rag.query_expand_enabled,
        max_query_variants=settings.rag.max_query_variants,
        rrf_k=settings.rag.rrf_k,
    )
    retrieval_service = RetrievalService(retrieval_biz=retrieval_biz)

    # ── RAG 链路 ──
    rag_biz = RAGBiz(
        retrieval_biz=retrieval_biz,
        llm_provider=llm_provider,
        prompt_manager=prompt_manager,
        default_rag_template=settings.prompt.default_rag_template,
        chunk_repo=chunk_repo,
        context_window=settings.rag.context_window,
    )
    rag_service = RAGService(rag_biz=rag_biz)

    # ── Rate Limiter──
    use_redis_limiter = os.environ.get("AI_ENV") != "dev"
    rate_limiter = create_rate_limiter(
        use_redis=use_redis_limiter,
        redis_client=redis_client if use_redis_limiter else None,
    )
    rate_limit_settings = RateLimitSettings(
        rate_limiter=rate_limiter,
        enabled=settings.rate_limit.enabled,
        standard_limit=settings.rate_limit.standard_limit,
        heavy_limit=settings.rate_limit.heavy_limit,
        window_seconds=settings.rate_limit.window_seconds,
    )
    logger.info(
        "rate_limiter_initialized",
        enabled=rate_limit_settings.enabled,
        backend="redis" if use_redis_limiter else "memory",
        standard_limit=rate_limit_settings.standard_limit,
        heavy_limit=rate_limit_settings.heavy_limit,
    )

    # ── Server ──
    http_server = HTTPServer(
        server_config=settings.server,
        auth_service=auth_service,
        session_manager=session_manager,
        session_config=settings.session,
        document_service=document_service,
        knowledge_base_service=knowledge_base_service,
        chunk_service=chunk_service,
        retrieval_service=retrieval_service,
        rag_service=rag_service,
        session_factory=session_factory,
        engine=engine,
        rate_limit_settings=rate_limit_settings,
        # memory 向量库为易失结构，重启后清空，需从 DB 重建；
        # pgvector 持久化则无需重建向量，只重建内存关键词索引。
        embedding_biz=embedding_biz,
        rebuild_vectors=(settings.vector_store.provider == "memory"),
        ensure_pgvector=(settings.vector_store.provider == "pgvector"),
        vector_dimension=settings.embedding.dimension,
    )
    return http_server.create()


# ── 模块级变量 ──
# 供 uvicorn.run("cmd.wire:app") 字符串引用加载。
# 通过 create_app() 统一组装，不重复初始化。
_settings: AppConfig = get_settings()
setup_logging(_settings.log)
app: FastAPI = create_app()

