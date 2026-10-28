"""资料的唯一处理入口：筛选、邻块合并、完整提示预算与引用映射。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from internal.biz.relevance import apply_relevance_gate
from pkg.errors.base import RAGError

if TYPE_CHECKING:
    from collections.abc import Callable

    from internal.biz.entity import Chunk, RetrievalResult
    from internal.biz.repo import ChunkRepo


def select_materials(
    query: str, candidates: list[RetrievalResult], threshold: float
) -> list[RetrievalResult]:
    """仅对真实重排分使用重排门槛；术语规则也只在这里执行一次。"""
    scored = [item for item in candidates if item.score_type == "rerank"]
    allowed = {item.chunk_id for item in apply_relevance_gate(query, scored, threshold)}
    return [item for item in candidates if item.score_type != "rerank" or item.chunk_id in allowed]


@dataclass(frozen=True)
class MaterialSegment:
    chunk_id: int
    position: int
    start: int
    end: int
    content: str


@dataclass
class AnswerSource:
    citation_id: int
    document_id: int
    document_filename: str
    knowledge_base_id: int
    segments: list[MaterialSegment]
    retrieval_hits: list[RetrievalResult]
    source_type: str = "knowledge_base"
    truncated: bool = False
    document_updated_at: str | None = None

    @property
    def content(self) -> str:
        return "\n".join(segment.content for segment in self.segments)


@dataclass
class WebSource:
    citation_id: int
    url: str
    title: str
    fetched_at: str
    content: str
    source_type: Literal["web_search_summary", "web_page"]
    truncated: bool = False


@dataclass(frozen=True)
class InputBudget:
    model: str
    input_limit: int = 16000
    output_limit: int = 2048
    # 该模型的规格来自准备阶段；更换模型必须显式确认容量。
    context_capacity: int = 1000000
    measurement: str = "utf8_bytes_estimate"

    def estimate(self, messages: list[dict], tools: list[dict], history: list[dict]) -> int:
        """保守的 UTF-8 字节估算，包含消息包装；不是供应商精确 token 计量。"""
        payload = {"messages": [*history, *messages], "tools": tools}
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 256


@dataclass
class PreparedMaterials:
    messages: list[dict]
    sources: list[AnswerSource | WebSource]
    estimated_input_tokens: int
    output_tokens: int
    measurement: str


class MaterialProcessor:
    def __init__(self, chunk_repo: ChunkRepo, context_window: int, budget: InputBudget) -> None:
        if budget.model != "qwen3.8-flash":
            raise ValueError("资料预算尚未确认此模型的容量：" + budget.model)
        self._chunk_repo = chunk_repo
        self._window = context_window
        self._budget = budget

    async def prepare(
        self,
        hits: list[RetrievalResult],
        build_messages: Callable[[str], list[dict]],
        output_tokens: int,
        *,
        tools: list[dict] | None = None,
        history: list[dict] | None = None,
    ) -> PreparedMaterials:
        return self.prepare_sources(
            await self._merge(hits), build_messages, output_tokens, tools=tools, history=history
        )

    def prepare_sources(
        self,
        candidates: list[AnswerSource | WebSource],
        build_messages: Callable[[str], list[dict]],
        output_tokens: int,
        *,
        tools: list[dict] | None = None,
        history: list[dict] | None = None,
    ) -> PreparedMaterials:
        """知识库和网页资料共用预算、实际文字裁剪与引用编号。"""
        budget = self._budget
        tools, history = tools or [], history or []
        if not 0 < output_tokens <= budget.output_limit:
            raise RAGError(reason="OUTPUT_BUDGET_EXCEEDED", message="回答预算必须在 1 到 2048 之间")
        limit = min(budget.input_limit, budget.context_capacity - output_tokens)

        def measure(sources: list[AnswerSource | WebSource]) -> tuple[list[dict], int]:
            messages = build_messages(render_materials(sources))
            return messages, budget.estimate(messages, tools, history)

        _, base_size = measure([])
        if base_size >= limit:
            raise RAGError(reason="INPUT_BUDGET_EXCEEDED", message="问题及系统提示已超过输入预算")

        sources: list[AnswerSource | WebSource] = []
        for original in candidates:
            candidate = replace(original, citation_id=len(sources) + 1)
            _, size = measure([*sources, candidate])
            if size <= limit:
                sources.append(candidate)
                continue
            # 对实际送模正文截断，同时裁剪分块映射；不登记未送模的分块。
            low, high = 0, len(candidate.content)
            best: AnswerSource | WebSource | None = None
            while low <= high:
                middle = (low + high) // 2
                clipped = clip_source(candidate, middle)
                _, size = measure([*sources, clipped])
                if size <= limit:
                    best = clipped if clipped.content else None
                    low = middle + 1
                else:
                    high = middle - 1
            if best is not None:
                sources.append(best)
            break
        messages, size = measure(sources)
        if candidates and not sources:
            raise RAGError(reason="MATERIAL_BUDGET_EXCEEDED", message="输入预算不足以容纳资料")
        return PreparedMaterials(messages, sources, size, output_tokens, budget.measurement)

    async def _merge(self, hits: list[RetrievalResult]) -> list[AnswerSource]:
        by_doc: dict[int, list[RetrievalResult]] = {}
        for hit in hits:
            by_doc.setdefault(hit.document_id, []).append(hit)
        ranked: list[tuple[int, AnswerSource]] = []
        order = {hit.chunk_id: index for index, hit in enumerate(hits)}
        for doc_id, doc_hits in by_doc.items():
            chunks = await self._chunk_repo.list_by_document(doc_id)
            by_position = {chunk.position: chunk for chunk in chunks if chunk.id is not None}
            for hit in doc_hits:
                if hit.chunk_position not in by_position:
                    raise RAGError(reason="SOURCE_CHANGED", message="检索资料已变化，请重新提问")
                chunk = by_position[hit.chunk_position]
                if chunk.id != hit.chunk_id or chunk.content != hit.chunk_content:
                    raise RAGError(reason="SOURCE_CHANGED", message="检索资料已变化，请重新提问")
            selected = sorted(
                {
                    pos
                    for hit in doc_hits
                    for pos in range(
                        hit.chunk_position - self._window, hit.chunk_position + self._window + 1
                    )
                    if pos in by_position
                }
            )
            runs: list[list[Chunk]] = []
            for pos in selected:
                if not runs or pos != runs[-1][-1].position + 1:
                    runs.append([])
                runs[-1].append(by_position[pos])
            for run in runs:
                ids = {chunk.id for chunk in run}
                included = [hit for hit in doc_hits if hit.chunk_id in ids]
                segments = [
                    MaterialSegment(chunk.id, chunk.position, 0, len(chunk.content), chunk.content)
                    for chunk in run
                ]
                first = doc_hits[0]
                ranked.append(
                    (
                        min(order[hit.chunk_id] for hit in included),
                        AnswerSource(
                            citation_id=0,
                            document_id=doc_id,
                            document_filename=first.document_filename,
                            knowledge_base_id=first.knowledge_base_id,
                            document_updated_at=(
                                first.document_updated_at.isoformat()
                                if first.document_updated_at
                                else None
                            ),
                            segments=segments,
                            retrieval_hits=included,
                        ),
                    )
                )
        ranked.sort(key=lambda item: item[0])
        return [source for _, source in ranked]


def clip_source(source: AnswerSource | WebSource, length: int) -> AnswerSource | WebSource:
    if isinstance(source, WebSource):
        return replace(source, content=source.content[:length], truncated=True)
    segments: list[MaterialSegment] = []
    remaining = length
    for segment in source.segments:
        if remaining <= 0:
            break
        content = segment.content[:remaining]
        if content:
            segments.append(replace(segment, content=content, end=segment.start + len(content)))
        remaining -= len(content) + 1  # render_materials 的换行分隔
    ids = {segment.chunk_id for segment in segments}
    return replace(
        source,
        segments=segments,
        truncated=True,
        retrieval_hits=[hit for hit in source.retrieval_hits if hit.chunk_id in ids],
    )


def render_materials(sources: list[AnswerSource | WebSource]) -> str:
    return "\n\n".join(
        (
            f"[来源 {source.citation_id}: {source.document_filename}]\n{source.content}"
            if isinstance(source, AnswerSource)
            else f"[来源 {source.citation_id}: {source.title}; {source.source_type}; "
            f"{source.url}; 获取时间 {source.fetched_at}]\n{source.content}"
        )
        for source in sources
    )
