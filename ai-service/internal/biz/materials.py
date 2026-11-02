"""资料的唯一处理入口：筛选、邻块合并、完整提示预算与引用映射。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Literal

from internal.biz.ingestion_state import ingestion_status
from internal.biz.query_plan import plan_query
from internal.biz.relevance import apply_relevance_gate
from internal.biz.text_structure import fenced_code_ranges
from pkg.errors.base import RAGError

if TYPE_CHECKING:
    from collections.abc import Callable

    from internal.biz.entity import Chunk, RetrievalResult
    from internal.biz.repo import ChunkRepo, DocumentRepo, TextChunker


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
    code_ranges: list[tuple[int, int]] = field(default_factory=list)


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
    source_id: str = ""
    start: int = 0
    end: int = 0
    total_chars: int = 0
    code_ranges: list[tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class InputBudget:
    model: str
    input_limit: int = 16000
    output_limit: int = 2048
    # 该模型的规格来自准备阶段；更换模型必须显式确认容量。
    context_capacity: int = 1000000
    measurement: str = "utf8_bytes_estimate"

    def input_ceiling(self, output_tokens: int) -> int:
        """输入限额独立于输出限额；只有模型总上下文容量需要扣除输出。"""
        return min(self.input_limit, self.context_capacity - output_tokens)

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
    def __init__(
        self, chunk_repo: ChunkRepo, context_window: int, budget: InputBudget, *,
        document_repo: DocumentRepo | None = None, chunker: TextChunker | None = None,
    ) -> None:
        if budget.model != "qwen3.8-flash":
            raise ValueError("资料预算尚未确认此模型的容量：" + budget.model)
        self._chunk_repo = chunk_repo
        self._window = context_window
        self._budget = budget
        self._documents = document_repo
        self._chunker = chunker

    async def prepare(
        self,
        hits: list[RetrievalResult],
        build_messages: Callable[[str], list[dict]],
        output_tokens: int,
        *,
        tools: list[dict] | None = None,
        history: list[dict] | None = None,
        query: str = "",
    ) -> PreparedMaterials:
        return self.prepare_sources(
            await self._merge(hits), build_messages, output_tokens, tools=tools, history=history,
            query=query,
        )

    def prepare_sources(
        self,
        candidates: list[AnswerSource | WebSource],
        build_messages: Callable[[str], list[dict]],
        output_tokens: int,
        *,
        tools: list[dict] | None = None,
        history: list[dict] | None = None,
        query: str = "",
    ) -> PreparedMaterials:
        """知识库和网页资料共用预算、实际文字裁剪与引用编号。"""
        budget = self._budget
        tools, history = tools or [], history or []
        if not 0 < output_tokens <= budget.output_limit:
            raise RAGError(reason="OUTPUT_BUDGET_EXCEEDED", message="回答预算必须在 1 到 2048 之间")
        limit = budget.input_ceiling(output_tokens)

        def measure(sources: list[AnswerSource | WebSource]) -> tuple[list[dict], int]:
            messages = build_messages(render_materials(sources))
            return messages, budget.estimate(messages, tools, history)

        _, base_size = measure([])
        if base_size >= limit:
            raise RAGError(reason="INPUT_BUDGET_EXCEEDED", message="问题及系统提示已超过输入预算")

        sources = pack_sources(
            [replace(source, citation_id=index) for index, source in enumerate(candidates, 1)],
            lambda values: measure(values)[1] <= limit,
            query=query,
        )
        messages, size = measure(sources)
        if candidates and not sources:
            raise RAGError(reason="MATERIAL_BUDGET_EXCEEDED", message="输入预算不足以容纳资料")
        return PreparedMaterials(messages, sources, size, output_tokens, budget.measurement)

    async def _read_document(self, doc_id: int):
        if self._documents is None or self._chunker is None:
            raise RAGError(reason="SOURCE_CONFIGURATION_INVALID", message="知识库资料处理缺少规范正文与分块策略")
        document = await self._documents.get_by_id(doc_id)
        if document is None or document.parsed_content is None or ingestion_status(document.stages) != "ready":
            raise RAGError(reason="SOURCE_CHANGED", message="检索文档的规范正文不存在或不再就绪")
        chunks = await self._chunk_repo.list_by_document(doc_id)
        spans = self._chunker.chunk_spans(document.parsed_content)
        if len(chunks) != len(spans) or any(
            chunk.position != index or chunk.content != document.parsed_content[start:end]
            for index, (chunk, (start, end)) in enumerate(zip(chunks, spans, strict=True))
        ):
            raise RAGError(reason="SOURCE_RECHUNK_REQUIRED", message="现有分块不对应当前原文切片策略，请重新分块并建立索引")
        return document, chunks, spans, fenced_code_ranges(document.parsed_content)

    async def validate_sources(self, sources: list[AnswerSource]) -> None:
        """生成前与发布前均核对当前版本和原分块切片，不补原文、不接受新位置。"""
        for doc_id in dict.fromkeys(source.document_id for source in sources):
            document, chunks, _, _ = await self._read_document(doc_id)
            version = (document.updated_at or document.created_at).isoformat()
            by_id = {chunk.id: chunk for chunk in chunks}
            for source in (value for value in sources if value.document_id == doc_id):
                if source.knowledge_base_id != document.knowledge_base_id or source.document_updated_at != version:
                    raise RAGError(reason="SOURCE_CHANGED", message="证据的文档归属或版本已变化")
                for segment in source.segments:
                    chunk = by_id.get(segment.chunk_id)
                    if (chunk is None or chunk.position != segment.position
                            or not 0 <= segment.start < segment.end <= len(chunk.content)
                            or chunk.content[segment.start:segment.end] != segment.content):
                        raise RAGError(reason="SOURCE_CHANGED", message="证据原分块或字符范围已变化")

    async def _merge(self, hits: list[RetrievalResult]) -> list[AnswerSource]:
        by_doc: dict[int, list[RetrievalResult]] = {}
        for hit in hits:
            by_doc.setdefault(hit.document_id, []).append(hit)
        ranked: list[tuple[int, AnswerSource]] = []
        order = {hit.chunk_id: index for index, hit in enumerate(hits)}
        for doc_id, doc_hits in by_doc.items():
            document, chunks, spans, codes = await self._read_document(doc_id)
            by_position = {chunk.position: chunk for chunk in chunks if chunk.id is not None}
            for hit in doc_hits:
                if (document.knowledge_base_id != hit.knowledge_base_id
                        or (document.updated_at or document.created_at) != hit.document_updated_at):
                    raise RAGError(reason="SOURCE_CHANGED", message="检索文档的归属或版本已变化")
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
                    MaterialSegment(
                        chunk.id, chunk.position, 0, len(chunk.content), chunk.content,
                        [(left - spans[chunk.position][0], right - spans[chunk.position][0])
                         for left, right in codes
                         if spans[chunk.position][0] <= left < right <= spans[chunk.position][1]],
                    )
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
        relative_end = min(length, len(source.content))
        end = source.start + relative_end
        for left, right in source.code_ranges:
            if left < end < right:
                end = left
        return replace(source, content=source.content[:end-source.start], end=end, truncated=True,
                       code_ranges=[(left, right) for left, right in source.code_ranges if right <= end])
    segments: list[MaterialSegment] = []
    remaining = length
    for segment in source.segments:
        if remaining <= 0:
            break
        end = min(segment.end, segment.start + remaining)
        for left, right in segment.code_ranges:
            if left < end < right:
                end = left
        content = segment.content[:end - segment.start]
        if content:
            segments.append(replace(segment, content=content, end=end,
                                    code_ranges=[(a, b) for a, b in segment.code_ranges
                                                 if segment.start <= a < b <= end]))
        remaining -= len(content) + 1  # render_materials 的换行分隔
    ids = {segment.chunk_id for segment in segments}
    return replace(
        source,
        segments=segments,
        truncated=True,
        retrieval_hits=[hit for hit in source.retrieval_hits if hit.chunk_id in ids],
    )


def question_parts(query: str) -> list[str]:
    """只按显式分句列出待核查主题；词面匹配不宣称语义覆盖。"""
    return list(dict.fromkeys(
        part.strip() for part in re.split(r"[；;？?。\n]+|以及|(?<=\s)和(?=\s)", query) if part.strip()
    ))


def question_terms(query: str) -> set[str]:
    lexical = plan_query(query).lexical
    terms = set(re.findall(r"[a-z0-9_]+(?:[-.][a-z0-9_]+)*", lexical.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", lexical):
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms - {"md", "txt", "请比", "比较", "分别", "引用", "数字", "同一", "一套", "标准"}


def evidence_coverage(query: str, sources: list[AnswerSource | WebSource]) -> dict:
    """按问题分句和真实来源登记候选；缺失仅指本次送模片段未命中。"""
    topics = []
    for part in question_parts(query):
        terms = question_terms(part)
        citations = [source.citation_id for source in sources
                     if any(term in source.content.casefold() for term in terms)]
        topics.append({"question": part, "candidate_citation_ids": citations,
                       "state": "candidate_found" if citations else "not_found_in_sent_excerpts"})
    documents = []
    seen = set()
    for source in sources:
        key = source_identity(source)
        if key in seen:
            continue
        seen.add(key)
        group = [item for item in sources if source_identity(item) == key]
        documents.append({
            "knowledge_base_id": source.knowledge_base_id if isinstance(source, AnswerSource) else None,
            "document": source.document_filename if isinstance(source, AnswerSource) else source.url,
            "version": source.document_updated_at if isinstance(source, AnswerSource) else source.fetched_at,
            "citation_ids": [item.citation_id for item in group],
        })
    return {"method": "lexical_candidates_not_semantic_verification", "topics": topics,
            "documents": documents}


def source_identity(source: AnswerSource | WebSource) -> tuple:
    if isinstance(source, AnswerSource):
        return (source.knowledge_base_id, source.document_id, source.document_updated_at)
    return (source.url, source.fetched_at, source.source_type)


def _text_ranges(content: str, code_ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """正文按行/句切分，代码仅使用规范正文或网页快照登记的边界。"""
    ranges = []
    cursor = 0

    def prose(start: int, end: int) -> None:
        offset = start
        for line in content[start:end].splitlines(keepends=True):
            line_end = offset + len(line)
            if line.strip():
                for match in re.finditer(r".+?(?:[。！？]|$)", line):
                    if match[0].strip():
                        ranges.append((offset + match.start(), offset + match.end()))
                if ranges:
                    ranges[-1] = (ranges[-1][0], line_end)
            elif ranges:
                ranges[-1] = (ranges[-1][0], line_end)
            offset = line_end

    for left, right in code_ranges:
        if not cursor <= left < right <= len(content):
            raise RAGError(reason="SOURCE_CODE_RANGE_INVALID", message="原文代码边界与当前片段不一致")
        prose(cursor, left)
        ranges.append((left, right))
        cursor = right
    prose(cursor, len(content))
    return ranges


@dataclass(frozen=True)
class _Excerpt:
    source: int
    segment: int
    start: int
    end: int
    score: float


def _excerpt_source(source: AnswerSource | WebSource, excerpts: list[_Excerpt]) -> AnswerSource | WebSource:
    if isinstance(source, WebSource):
        left, right = min(item.start for item in excerpts), max(item.end for item in excerpts)
        window = clip_source(replace(source, content=source.content[left:], start=source.start + left), right - left)
        return replace(window, truncated=source.truncated or left > 0 or right < len(source.content),
                       code_ranges=[(a, b) for a, b in window.code_ranges if a >= window.start])
    segments = []
    for item in sorted(excerpts, key=lambda item: (item.segment, item.start)):
        original = source.segments[item.segment]
        left, right = original.start + item.start, original.start + item.end
        if segments and segments[-1].chunk_id == original.chunk_id and left <= segments[-1].end:
            previous = segments.pop()
            left = previous.start
            right = max(right, previous.end)
        segments.append(replace(original, start=left, end=right,
                                content=original.content[left-original.start:right-original.start],
                                code_ranges=[(a, b) for a, b in original.code_ranges if left <= a < b <= right]))
    ids = {segment.chunk_id for segment in segments}
    return replace(source, segments=segments, truncated=source.truncated or segments != source.segments,
                   retrieval_hits=[hit for hit in source.retrieval_hits if hit.chunk_id in ids])


def pack_sources(
    candidates: list[AnswerSource | WebSource],
    fits: Callable[[list[AnswerSource | WebSource]], bool],
    *,
    query: str = "",
) -> list[AnswerSource | WebSource]:
    """按问题相关原文单元分配完整请求空间；先覆盖不同文档，再添加相关上下文。"""
    terms = question_terms(query)
    excerpts = []
    named = set()
    for index, source in enumerate(candidates):
        local_terms = terms
        if isinstance(source, WebSource) and source.source_type == "web_page":
            # 模型已选择读取的正文比搜索摘要优先；标题提供跨语言主题词，仍只抽取原文。
            named.add(index)
            local_terms = terms | question_terms(source.title)
        if isinstance(source, AnswerSource):
            stem = re.sub(r"^\d+[._-]", "", source.document_filename.rsplit(".", 1)[0]).casefold()
            if len(stem) >= 2 and stem in query.casefold():
                named.add(index)
        texts = [segment.content for segment in source.segments] if isinstance(source, AnswerSource) else [source.content]
        hit_ids = {hit.chunk_id for hit in source.retrieval_hits} if isinstance(source, AnswerSource) else set()
        for segment, content in enumerate(texts):
            if isinstance(source, WebSource):
                code_ranges = [(left - source.start, right - source.start) for left, right in source.code_ranges]
            else:
                part = source.segments[segment]
                code_ranges = [(left - part.start, right - part.start) for left, right in part.code_ranges]
            ranges = _text_ranges(content, code_ranges)
            scores = [sum(1 for term in local_terms if term in content[left:right].casefold())
                      for left, right in ranges]
            hit = ((isinstance(source, AnswerSource) and source.segments[segment].chunk_id in hit_ids)
                   or (isinstance(source, WebSource) and source.source_type == "web_page"))
            for position, (left, right) in enumerate(ranges):
                direct = scores[position]
                neighbor = max(scores[max(0, position - 1):position + 2], default=0)
                # 邻句保留单位、前置条件和否定；不把文档名匹配冒充正文依据。
                score = direct * 4 + neighbor + (0.25 if hit else 0)
                # 比较题中的真实数值/单位比泛泛的限制介绍更有信息量；仍须词面相关。
                if direct and re.search(r"\d\s*(?:[KMGT]?B|[kmgt]b?|cores?|核|秒|分钟|小时|%)\b", content[left:right], re.I):
                    score += 4
                if not terms or score > 0:
                    if direct:
                        left = ranges[max(0, position - 1)][0]
                        right = ranges[min(len(ranges) - 1, position + 1)][1]
                    excerpts.append(_Excerpt(index, segment, left, right, score))
    excerpts.sort(key=lambda item: (item.source not in named, -item.score, item.source, item.segment, item.start))
    chosen: list[_Excerpt] = []

    def project(items: list[_Excerpt]) -> list[AnswerSource | WebSource]:
        grouped: dict[int, list[_Excerpt]] = {}
        for item in items:
            grouped.setdefault(item.source, []).append(item)
        return [_excerpt_source(candidates[index], group) for index, group in sorted(grouped.items())]

    # 每份文档最相关的完整单元先争取空间；之后按相关度补齐，绝不等长截取每份开头。
    first, rest, seen = [], [], set()
    for item in excerpts:
        key = source_identity(candidates[item.source])
        (rest if key in seen or (named and item.source not in named) else first).append(item)
        seen.add(key)
    for item in [*first, *rest]:
        if any(old.source == item.source and old.segment == item.segment
               and old.start <= item.start and item.end <= old.end for old in chosen):
            continue
        trial = [*chosen, item]
        if fits(project(trial)):
            chosen = trial
    return project(chosen)


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
