"""
internal/biz/rag.py — RAG 检索增强生成业务逻辑

RAGBiz 负责：
- 已有检索分块时直接用于生成；否则才自己检索
- 按 context_window 向两侧扩邻块再拼 context
- 使用 RAG 模板构建 messages
- 调用 LLM 生成回答；失败时返回「模型回答失败」，不贴检索摘录
- 无相关资料时不调用 LLM，直接回答「我不知道」

不感知 HTTP（不 import fastapi）、不感知存储（不 import sqlalchemy）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal

import structlog

from internal.biz.context_expand import build_windows, windows_to_context_chunks
from internal.biz.entity import ChatChunk, RetrievalResult
from internal.biz.repo import ChunkRepo, LLMProvider
from internal.biz.retrieval import RetrievalBiz
from internal.prompt.base import build_context_block
from internal.prompt.manager import PromptManager

logger = structlog.get_logger()

# RAG 流式事件类型常量
RAG_EVENT_SOURCES = "sources"
RAG_EVENT_CHUNK = "chunk"

_NO_EVIDENCE_ANSWER = "我不知道"
_LLM_FAILED_ANSWER = "模型回答失败"


@dataclass
class RAGResult:
    """RAG 查询结果值对象 — Sprint 12。"""

    answer: str               # LLM 生成的回答
    model: str                # 使用的模型名称
    sources: list[RetrievalResult]  # 引用的来源列表


@dataclass
class RAGStreamEvent:
    """RAG 流式事件值对象 — Sprint 13。

    通过 event_type 区分：
    - "sources": 检索来源元数据（sources 有值，chunk 为 None）
    - "chunk": LLM 流式分片（chunk 有值，sources 为空）
    """

    event_type: Literal["sources", "chunk"]  # RAG_EVENT_SOURCES | RAG_EVENT_CHUNK
    sources: list[RetrievalResult] = field(default_factory=list)
    chunk: ChatChunk | None = None


class RAGBiz:
    """检索增强生成：有现成分块则直接拼 context 调 LLM，否则先检索。"""

    def __init__(
        self,
        retrieval_biz: RetrievalBiz,
        llm_provider: LLMProvider,
        prompt_manager: PromptManager,
        default_rag_template: str = "rag",
        chunk_repo: ChunkRepo | None = None,
        context_window: int = 0,
    ) -> None:
        self._retrieval_biz = retrieval_biz
        self._llm_provider = llm_provider
        self._prompt_manager = prompt_manager
        self._default_rag_template = default_rag_template
        self._chunk_repo = chunk_repo
        self._context_window = max(0, context_window)

    def _build_context(self, sources: list[RetrievalResult]) -> str:
        """将检索结果构建为 context 文本块 — 抽取公共逻辑，避免 query/query_stream 重复。"""
        context_chunks = [
            {
                "content": s.chunk_content,
                "document_filename": s.document_filename,
                "score": s.score,
            }
            for s in sources
        ]
        return build_context_block(context_chunks)

    async def _build_generation_context(self, sources: list[RetrievalResult]) -> str:
        """把命中块向两侧扩邻块后再送给 LLM。"""
        if not sources or self._context_window <= 0 or self._chunk_repo is None:
            return self._build_context(sources)

        doc_ids = {s.document_id for s in sources}
        chunks_by_doc = {}
        for doc_id in doc_ids:
            chunks_by_doc[doc_id] = await self._chunk_repo.list_by_document(doc_id)

        windows = build_windows(sources, chunks_by_doc, self._context_window)
        return build_context_block(windows_to_context_chunks(windows))

    async def _retrieve_sources(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int,
        chunk_ids: list[int] | None = None,
        allowed_kb_ids: list[int] | None = None,
    ) -> list[RetrievalResult]:
        if chunk_ids is not None:
            return await self._retrieval_biz.hydrate_chunk_ids(
                chunk_ids[:top_k],
                knowledge_base_id,
                allowed_kb_ids=allowed_kb_ids,
            )
        kb_ids: list[int] = []
        for kb_id in allowed_kb_ids or [knowledge_base_id]:
            if kb_id > 0 and kb_id not in kb_ids:
                kb_ids.append(kb_id)
        if not kb_ids:
            kb_ids = [knowledge_base_id]
        merged: list[RetrievalResult] = []
        for kb_id in kb_ids:
            merged.extend(
                await self._retrieval_biz.retrieve(
                    query=query,
                    knowledge_base_id=kb_id,
                    top_k=top_k,
                    apply_threshold=True,
                    dedupe_by_document=False,
                )
            )
        merged.sort(key=lambda item: item.score, reverse=True)
        return merged[:top_k]

    def _build_messages(self, query: str, context: str) -> list[dict[str, str]]:
        template = self._prompt_manager.get_template(self._default_rag_template)
        return template.build_messages(variables={"query": query, "context": context})

    def _text_event(self, text: str, model: str = "") -> RAGStreamEvent:
        return RAGStreamEvent(
            event_type=RAG_EVENT_CHUNK,
            chunk=ChatChunk(delta=text, model=model, finish_reason="stop"),
        )

    async def query(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
        chunk_ids: list[int] | None = None,
        allowed_kb_ids: list[int] | None = None,
    ) -> RAGResult:
        """RAG 查询：已有分块则直接生成，否则先检索再生成。"""
        sources = await self._retrieve_sources(
            query, knowledge_base_id, top_k, chunk_ids=chunk_ids, allowed_kb_ids=allowed_kb_ids
        )

        logger.info(
            "rag_retrieval_completed",
            knowledge_base_id=knowledge_base_id,
            query_length=len(query),
            source_count=len(sources),
        )

        if not sources:
            return RAGResult(answer=_NO_EVIDENCE_ANSWER, model="", sources=[])

        context = await self._build_generation_context(sources)
        messages = self._build_messages(query, context)

        logger.info(
            "rag_prompt_built",
            template=self._default_rag_template,
            message_count=len(messages),
            context_length=len(context),
        )

        try:
            result = await self._llm_provider.generate(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            logger.warning(
                "rag_llm_failed",
                error=str(exc),
                source_count=len(sources),
                exc_info=True,
            )
            return RAGResult(answer=_LLM_FAILED_ANSWER, model="", sources=sources)

        if not (result.content or "").strip():
            return RAGResult(answer=_LLM_FAILED_ANSWER, model="", sources=sources)

        logger.info(
            "rag_query_completed",
            knowledge_base_id=knowledge_base_id,
            model=result.model,
            total_tokens=result.total_tokens,
            answer_length=len(result.content),
            source_count=len(sources),
        )

        return RAGResult(
            answer=result.content,
            model=result.model,
            sources=sources,
        )

    async def query_stream(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
        chunk_ids: list[int] | None = None,
        allowed_kb_ids: list[int] | None = None,
    ) -> AsyncIterator[RAGStreamEvent]:
        """RAG 流式查询：已有分块则直接生成，否则先检索再生成。"""
        sources = await self._retrieve_sources(
            query, knowledge_base_id, top_k, chunk_ids=chunk_ids, allowed_kb_ids=allowed_kb_ids
        )

        logger.info(
            "rag_stream_retrieval_completed",
            knowledge_base_id=knowledge_base_id,
            query_length=len(query),
            source_count=len(sources),
        )

        if not sources:
            yield self._text_event(_NO_EVIDENCE_ANSWER)
            logger.info(
                "rag_stream_no_evidence",
                knowledge_base_id=knowledge_base_id,
            )
            return

        context = await self._build_generation_context(sources)
        messages = self._build_messages(query, context)

        logger.info(
            "rag_stream_prompt_built",
            template=self._default_rag_template,
            message_count=len(messages),
            context_length=len(context),
        )

        yield RAGStreamEvent(event_type=RAG_EVENT_SOURCES, sources=sources)

        chunk_count = 0
        yielded_text = False
        try:
            async for chunk in self._llm_provider.generate_stream(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                if chunk.delta:
                    yielded_text = True
                    chunk_count += 1
                    yield RAGStreamEvent(event_type=RAG_EVENT_CHUNK, chunk=chunk)
                elif chunk.finish_reason:
                    chunk_count += 1
                    yield RAGStreamEvent(event_type=RAG_EVENT_CHUNK, chunk=chunk)
        except Exception as exc:
            logger.warning(
                "rag_stream_llm_failed",
                error=str(exc),
                yielded_text=yielded_text,
                source_count=len(sources),
                exc_info=True,
            )
            if yielded_text:
                logger.info(
                    "rag_stream_completed",
                    knowledge_base_id=knowledge_base_id,
                    chunk_count=chunk_count,
                    source_count=len(sources),
                    interrupted=True,
                )
                return

        if not yielded_text:
            try:
                result = await self._llm_provider.generate(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (result.content or "").strip()
                if text:
                    yielded_text = True
                    chunk_count += 1
                    yield self._text_event(text, model=result.model)
                    logger.info(
                        "rag_stream_used_nonstream_generate",
                        knowledge_base_id=knowledge_base_id,
                        model=result.model,
                    )
            except Exception as exc:
                logger.warning(
                    "rag_generate_failed",
                    error=str(exc),
                    source_count=len(sources),
                    exc_info=True,
                )

        if not yielded_text:
            yield self._text_event(_LLM_FAILED_ANSWER)
            chunk_count += 1

        logger.info(
            "rag_stream_completed",
            knowledge_base_id=knowledge_base_id,
            chunk_count=chunk_count,
            source_count=len(sources),
        )
