"""
cmd/wire.py — 依赖注入编排

所有依赖组装集中在一处，整条链路一目了然。
组装顺序从底层到顶层：Config → Logging → DB → Redis → Repo → Biz → Service → Server。

设计要点：
- create_app() 是唯一工厂函数，封装完整的依赖组装逻辑。
- 模块级 app 变量供 uvicorn.run("cmd.wire:app") 字符串引用加载。
- _settings 供 main.py 读取 host/port/workers 等启动参数。
- 模块加载时调用 create_app()；数据库建表与索引核实在 HTTP lifespan 中执行。
- HTTP 层处理请求与权限，Service 层转换协议数据，Biz 层执行入库和问答流程。
"""

from __future__ import annotations

import os
from pathlib import Path

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from internal.biz.auth import AuthBiz
from internal.biz.chunk import ChunkBiz
from internal.biz.document import DocumentBiz
from internal.biz.embedding import EmbeddingBiz
from internal.biz.ingestion import IngestionBiz
from internal.biz.knowledge_base import KnowledgeBaseBiz
from internal.biz.materials import InputBudget
from internal.biz.rag import RAGBiz
from internal.biz.retrieval import RetrievalBiz
from internal.biz.tool_model import ToolModel
from internal.biz.tools import ReadOnlyToolExecutor
from internal.conf.config import AppConfig, get_settings
from internal.conf.logging import setup_logging
from internal.data.answer_runs import LocalAnswerRunStore
from internal.data.chunk.factory import create_chunk_repo
from internal.data.chunker.factory import create_text_chunker
from internal.data.database import create_engine, create_session_factory
from internal.data.document.factory import create_document_repo
from internal.data.embedding.cache import CachedEmbeddingProvider
from internal.data.embedding.factory import create_embedding_provider
from internal.data.keyword.factory import create_keyword_store
from internal.data.knowledge_base.factory import create_knowledge_base_repo
from internal.data.llm.factory import create_llm_provider
from internal.data.parser.factory import create_document_parser
from internal.data.reranker.factory import create_reranker
from internal.data.retriever.factory import create_retriever
from internal.data.storage.factory import create_file_storage_repo
from internal.data.user.factory import create_user_repo
from internal.data.vector_store.factory import create_vector_store
from internal.data.web.access import PublicWeb
from internal.data.web.html_reader import HTMLPageReader
from internal.data.web.tavily import TavilySearch
from internal.prompt.manager import PromptManager
from internal.server.http import HTTPServer
from internal.server.middleware.rate_limit import RateLimitSettings, create_rate_limiter
from internal.service.auth import AuthService
from internal.service.chunk import ChunkService
from internal.service.document import DocumentService
from internal.service.knowledge_base import KnowledgeBaseService
from internal.service.rag import RAGService
from internal.service.retrieval import RetrievalService
from pkg.session import SessionManager

logger = structlog.get_logger()


def _create_redis_client(redis_url: str) -> aioredis.Redis:
    
    """创建供会话与限流共用的 Redis 客户端；开发环境安装 fakeredis 时使用内存模拟客户端。"""
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

    # ── Chunk 链路 ──
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

    # ── Embedding 链路 ──
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
        # 注入 document_repo，供索引 metadata 写入 knowledge_base_id，
        # 保障多知识库场景下 Retriever 能在存储层完成 kb 隔离。
        document_repo=document_repo,
    )

    # 删除分块前先清理对应向量与关键词；索引创建由 IngestionBiz 分步触发。
    chunk_biz = ChunkBiz(
        chunk_repo=chunk_repo,
        chunker=text_chunker,
        on_chunk_delete=embedding_biz.delete_embeddings,
    )
    # 入库状态、互斥和重试由同一编排器管理；文档删除也复用它的同文档锁。
    ingestion = IngestionBiz(document_repo, chunk_repo, chunk_biz, embedding_biz, create_document_parser)
    # ChunkService 复用文档校验
    document_biz = DocumentBiz(
        document_repo=document_repo,
        file_storage_repo=file_storage_repo,
        ingestion=ingestion,
        max_file_size=settings.security.max_request_body_size,
        on_document_delete=chunk_biz.delete_chunks,
    )
    chunk_service = ChunkService(chunk_biz=chunk_biz, document_biz=document_biz)
    document_service = DocumentService(
        document_biz=document_biz,
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
    # 开启时必须提供有效重排器；关闭时按召回分排序，重排失败由检索层直接上报。
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
        # 独立检索接口和问答工具共用重排与相关性筛选规则。
        reranker=reranker,
        rerank_enabled=settings.rag.rerank_enabled,
        min_relevance=settings.rag.min_relevance,
        query_expand_enabled=settings.rag.query_expand_enabled,
        max_query_variants=settings.rag.max_query_variants,
        rrf_k=settings.rag.rrf_k,
    )
    retrieval_service = RetrievalService(retrieval_biz=retrieval_biz)

    # 只读工具与有边界的问答循环共用模型、资料和授权范围。
    tool_budget = InputBudget(model=llm_provider.model_name)
    tool_executor = ReadOnlyToolExecutor(
        retrieval=retrieval_biz, kb_repo=kb_repo, chunk_repo=chunk_repo,
        web=PublicWeb(
            search=TavilySearch(
                api_key=settings.tavily.api_key, timeout=settings.tavily.timeout,
                search_limit=settings.tavily.search_limit,
                summary_chars=settings.tavily.summary_chars,
            ),
            reader=HTMLPageReader(
                timeout=settings.web_reader.timeout,
            ),
        ),
        budget=tool_budget, context_window=settings.rag.context_window,
        document_repo=document_repo, chunker=text_chunker,
        timeout=settings.tools.timeout,
        web_page_chars=settings.web_reader.page_chars,
    )
    tool_service = ToolModel(
        tool_executor, llm_provider, tool_budget, model_timeout=settings.llm.timeout
    )

    # ── RAG 链路 ──
    rag_biz = RAGBiz(
        model=tool_service,
        prompt_manager=prompt_manager,
        budget=tool_budget,
        default_rag_template=settings.prompt.default_rag_template,
    )
    rag_service = RAGService(
        rag_biz=rag_biz,
        run_store=LocalAnswerRunStore(Path(__file__).resolve().parents[1] / "data" / "answer-runs"),
    )

    # ── 请求限流器：限制一个统计窗口内的请求次数 ──
    # 开发环境使用进程内计数，其他环境使用 Redis；真正的拦截由 HTTP 限流中间件执行。
    use_redis_limiter = os.environ.get("AI_ENV") != "dev"
    rate_limiter = create_rate_limiter(
        use_redis=use_redis_limiter,
        redis_client=redis_client if use_redis_limiter else None,
    )
    # standard_limit/heavy_limit 分别是普通接口和高开销接口的额度；window_seconds 是窗口秒数。
    # enabled 控制中间件是否执行限流，初始化限流器本身不会拒绝任何请求。
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
        ingestion=ingestion,
        rebuild_vectors=(settings.vector_store.provider == "memory"),
        ensure_pgvector=(settings.vector_store.provider == "pgvector"),
        vector_dimension=settings.embedding.dimension,
    )
    app = http_server.create()
    app.state.read_only_tools = tool_service
    return app


# ── 模块级变量 ──
# 供 uvicorn.run("cmd.wire:app") 字符串引用加载。
# 通过 create_app() 统一组装，不重复初始化。
_settings: AppConfig = get_settings()
setup_logging(_settings.log)
app: FastAPI = create_app()

