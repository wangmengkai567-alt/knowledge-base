"""后端持有知识库问答：真实检索 → 统一资料处理 → 有来源的回答。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from internal.biz.entity import ChatChunk, RetrievalResult
from internal.biz.materials import AnswerSource, InputBudget, MaterialProcessor, PreparedMaterials
from pkg.errors.base import RAGError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from internal.biz.repo import ChunkRepo, LLMProvider
    from internal.biz.retrieval import RetrievalBiz
    from internal.prompt.manager import PromptManager

RAG_EVENT_SOURCES = "sources"
RAG_EVENT_CHUNK = "chunk"


@dataclass
class RAGResult:
    answer: str
    model: str
    sources: list[AnswerSource]
    retrieval_results: list[RetrievalResult]
    input_measurement: str
    estimated_input_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class RAGStreamEvent:
    event_type: Literal["sources", "chunk"]
    sources: list[AnswerSource] = field(default_factory=list)
    retrieval_results: list[RetrievalResult] = field(default_factory=list)
    chunk: ChatChunk | None = None
    input_measurement: str = ""
    estimated_input_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0


class RAGBiz:
    def __init__(
        self,
        retrieval_biz: RetrievalBiz,
        llm_provider: LLMProvider,
        prompt_manager: PromptManager,
        chunk_repo: ChunkRepo,
        default_rag_template: str = "rag",
        context_window: int = 0,
    ) -> None:
        self._retrieval_biz = retrieval_biz
        self._llm_provider = llm_provider
        self._prompt_manager = prompt_manager
        self._template = default_rag_template
        self._budget = InputBudget(model=llm_provider.model_name)
        self._materials = MaterialProcessor(chunk_repo, context_window, self._budget)

    async def _prepare(
        self,
        query: str,
        knowledge_base_ids: list[int],
        top_k: int,
        max_tokens: int | None,
    ) -> tuple[list[RetrievalResult], PreparedMaterials]:
        batches = await asyncio.gather(
            *(
                self._retrieval_biz.retrieve(
                    query=query,
                    knowledge_base_id=kid,
                    top_k=top_k,
                    apply_threshold=True,
                    dedupe_by_document=False,
                )
                for kid in dict.fromkeys(knowledge_base_ids)
            )
        )
        hits = [hit for batch in batches for hit in batch]
        # 相同配置产生的最终分才允许跨库排序；无分数条目保留，不补造数值。
        kinds = {(hit.score_type, hit.score_method) for hit in hits}
        if len(kinds) > 1:
            raise RAGError(reason="INCOMPARABLE_SCORES", message="不同类型的检索分不能混合排序")
        hits.sort(key=lambda hit: (hit.score is not None, hit.score), reverse=True)
        hits = hits[:top_k]
        template = self._prompt_manager.get_template(self._template)
        prepared = await self._materials.prepare(
            hits,
            lambda context: template.build_messages({"query": query, "context": context}),
            max_tokens if max_tokens is not None else self._budget.output_limit,
        )
        return hits, prepared

    async def _answer(
        self,
        prepared: PreparedMaterials,
        temperature: float | None,
    ) -> tuple[str, str, int, int]:
        if not prepared.sources:
            return "我不知道", "", 0, 0
        result = await self._llm_provider.generate(
            messages=prepared.messages,
            temperature=temperature,
            max_tokens=prepared.output_tokens,
        )
        if result.prompt_tokens > self._budget.input_limit:
            raise RAGError(
                reason="INPUT_MEASUREMENT_EXCEEDED", message="模型实际输入超过预算，需校准计量方式"
            )
        if result.completion_tokens > prepared.output_tokens:
            raise RAGError(reason="OUTPUT_BUDGET_EXCEEDED", message="模型输出超过回答预算")
        text = (result.content or "").strip()
        if not text:
            raise RAGError(reason="EMPTY_ANSWER", message="模型返回空回答")
        if not re.fullmatch(r"我不知道[。.!！]?", text):
            citations = [int(value) for value in re.findall(r"\[来源\s*(\d+)\]", text)]
            allowed = {source.citation_id for source in prepared.sources}
            if not citations or any(value not in allowed for value in citations):
                raise RAGError(
                    reason="INVALID_CITATION", message="回答缺少引用或引用不属于本次送模资料"
                )
        return text, result.model, result.prompt_tokens, result.completion_tokens

    async def query(
        self,
        query: str,
        knowledge_base_ids: list[int],
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> RAGResult:
        hits, prepared = await self._prepare(query, knowledge_base_ids, top_k, max_tokens)
        answer, model, prompt_tokens, completion_tokens = await self._answer(prepared, temperature)
        return RAGResult(
            answer,
            model,
            prepared.sources,
            hits,
            prepared.measurement,
            prepared.estimated_input_tokens,
            prompt_tokens,
            completion_tokens,
        )

    async def query_stream(
        self,
        query: str,
        knowledge_base_ids: list[int],
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[RAGStreamEvent]:
        hits, prepared = await self._prepare(query, knowledge_base_ids, top_k, max_tokens)
        yield RAGStreamEvent(
            event_type=RAG_EVENT_SOURCES,
            sources=prepared.sources,
            retrieval_results=hits,
            input_measurement=prepared.measurement,
            estimated_input_tokens=prepared.estimated_input_tokens,
        )
        answer, model, prompt_tokens, completion_tokens = await self._answer(prepared, temperature)
        yield RAGStreamEvent(
            event_type=RAG_EVENT_CHUNK,
            chunk=ChatChunk(delta=answer, model=model, finish_reason="stop"),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
