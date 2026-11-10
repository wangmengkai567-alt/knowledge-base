"""资料的唯一处理入口：筛选、邻块合并、预算内原文选择与引用映射。"""
# 管理输入预算、资料组织和上下文裁剪

from __future__ import annotations

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
    """原分块中的连续原文切片；start/end 为原分块内的字符范围，左闭右开。"""

    chunk_id: int
    position: int  # 原文中的分块序号，从 0 开始。
    start: int     # 原分块内的字符起点，包含该位置。
    end: int       # 原分块内的字符终点，不包含该位置。
    content: str   # 必须等于原分块正文的 [start:end]。
    code_ranges: list[tuple[int, int]] = field(default_factory=list)  # 原分块内完整代码块的字符范围。


# 将同一文档的原文片段组织为可引用资料，供模型作答和后续核验使用。
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
        """按 segments 的顺序用换行连接实际引用的原文；各段在源文档中不一定连续。"""
        return "\n".join(segment.content for segment in self.segments)


# 将网页摘要或正文组织为可引用资料，并保留原文位置。
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


# 将检索结果整理为模型可用的资料：核对原文、补充相邻分块并控制请求大小。
class MaterialProcessor:
    def __init__(
        self, chunk_repo: ChunkRepo, context_window: int, *,
        document_repo: DocumentRepo | None = None, chunker: TextChunker | None = None,
    ) -> None:
        """注入原文及分块依赖；context_window 为命中块两侧各扩展的块数。"""
        self._chunk_repo = chunk_repo
        self._window = context_window
        self._documents = document_repo
        self._chunker = chunker

    async def prepare(self, hits: list[RetrievalResult]) -> list[AnswerSource]:
        """核验并合并候选原文；工具与完整请求分别用实际发送视图装包。"""
        return await self._merge(hits)

    async def _read_document(self, doc_id: int):
        """读取就绪文档并核对当前分块确实对应规范正文，返回文档、分块、位置区间和代码区间。

        分块与正文或当前切分策略不一致时抛出异常，要求重新分块，避免引用错误位置。
        """
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
        """按文档核对检索命中，扩展相邻块并组成连续来源，按最佳命中的顺序返回。

        只合并位置连续的块，保留每个原分块的 ID 和字符范围；资料变化时抛出异常。
        """
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
            # 扩展命中块的相邻位置，再按连续区间分组；不连续片段分别保留来源。
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
    """返回最多保留 length 个正文字符的来源副本，并同步缩短来源范围及命中信息。

    截取点若落在代码块内部，就退到代码块起点，避免把半段代码作为原文证据。
    """
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
    """按问号、分号等显式分隔符拆分问题，去重后返回待查询主题，不调用模型。"""
    return list(dict.fromkeys(
        part.strip() for part in re.split(r"[；;？?。\n]+|以及|(?<=\s)和(?=\s)", query) if part.strip()
    ))


def question_terms(query: str) -> set[str]:
    """提取用于片段选择的英文词和中文双字片段，返回去重词集合；网址不参与主题匹配。"""
    # 网址只用于定位来源；域名和路径中的 get/com 等不是问题主题词。
    query = re.sub(r"https?://[^\s<>]+", " ", query)
    lexical = plan_query(query).lexical
    terms = set(re.findall(r"[a-z0-9_]+(?:[-.][a-z0-9_]+)*", lexical.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", lexical):
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms - {"md", "txt", "请比", "比较", "分别", "引用", "数字", "同一", "一套", "标准"}


def evidence_coverage(query: str, sources: list[AnswerSource | WebSource]) -> dict:
    """列出每个问题分句在已送模片段中匹配到的引用编号，以及这些片段的来源版本。

    匹配按关键词完成；未匹配只表示当前片段未命中，不说明整份文档不存在相关内容。
    """
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
    """返回来源及版本的组合标识，用于把同一文档或同一次获取的网页片段归为一组。"""
    if isinstance(source, AnswerSource):
        return (source.knowledge_base_id, source.document_id, source.document_updated_at)
    return (source.url, source.fetched_at, source.source_type)


def _text_ranges(content: str, code_ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """正文按行/句切分，代码仅使用规范正文或网页快照登记的边界。"""
    ranges = []
    cursor = 0

    def prose(start: int, end: int) -> None:
        """将非代码区间按行和中文句末标点拆成字符范围，保留行尾换行及附属空白行。"""
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


# 保存候选摘录的位置和相关性，供预算不足时挑选、缩短原文片段。
@dataclass(frozen=True)
class _Excerpt:
    source: int
    segment: int
    start: int
    end: int
    score: float
    focus_start: int
    focus_end: int


def _excerpt_focus(content: str, start: int, end: int, codes: list[tuple[int, int]], terms: set[str]) -> tuple[int, int]:
    """在候选区间内找优先保留的命中位置，返回字符起止偏移；涉及代码时返回完整代码范围。"""
    for left, right in codes:
        if start < right and left < end:
            return left, right  # 匹配或邻接的代码单元只能整体选择。
    match = min((match for term in terms for match in re.finditer(re.escape(term), content[start:end], re.I)),
                key=lambda match: (match.start(), -match.end()), default=None)
    if match is not None:
        return start + match.start(), start + match.end()
    first = start + len(content[start:end]) - len(content[start:end].lstrip())
    return first, first + 1


def _excerpt_source(source: AnswerSource | WebSource, excerpts: list[_Excerpt]) -> AnswerSource | WebSource:
    """按选中的原文区间重建来源副本，同时更新范围、截断标记和仍被保留的检索命中。"""
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
    """按问题相关性选择原文片段，返回满足 fits 预算检查的来源副本列表。

    candidates 是候选来源，query 用于提取主题词；fits 每次接收一组来源，
    由调用方判断完整请求是否能放下。先为不同来源争取空间，再补上下文；代码只整块保留。
    """
    # fits 由调用方核算完整请求；此处只选择连续原文窗口，代码块必须整体保留。
    terms = question_terms(query)
    excerpts = []
    contexts = {}
    named = set()
    for index, source in enumerate(candidates):
        local_terms = terms
        title_phrase = ""
        if isinstance(source, WebSource) and source.source_type == "web_page":
            # 模型已选择读取的正文比搜索摘要优先；标题提供跨语言主题词，仍只抽取原文。
            named.add(index)
            title = source.title.split("|")[0].strip()
            local_terms = terms | question_terms(title)
            title_phrase = title.rstrip("?？").casefold()
        if isinstance(source, WebSource) and source.source_type == "web_search_summary":
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
            contexts[index, segment] = content, code_ranges
            # 搜索摘要已是短资料，优先保留完整内容。
            ranges = ([(0, len(content))] if content.strip() and isinstance(source, WebSource)
                      and source.source_type == "web_search_summary" else _text_ranges(content, code_ranges))
            scores = [sum(1 for term in local_terms if term in content[left:right].casefold())
                      for left, right in ranges]
            hit = ((isinstance(source, AnswerSource) and source.segments[segment].chunk_id in hit_ids)
                   or (isinstance(source, WebSource) and source.source_type == "web_page"))
            for position, (left, right) in enumerate(ranges):
                direct = scores[position]
                neighbor = max(scores[max(0, position - 1):position + 2], default=0)
                # 同时考虑相邻句的关键词命中，选择正文时保留命中位置前后的句子。
                score = direct * 4 + neighbor + (0.25 if hit else 0)
                # 正文既包含完整页标题又有其他说明文字时，增加该片段的排序分。
                paragraph = content[left:right].casefold()
                if (title_phrase and title_phrase in paragraph
                        and paragraph.replace(title_phrase, "", 1).strip(" \t\r\n?？#")):
                    score += 8
                # 片段命中问题关键词且包含数值单位时，增加其排序分。
                if direct and re.search(r"\d\s*(?:[KMGT]?B|[kmgt]b?|cores?|核|秒|分钟|小时|%)\b", content[left:right], re.I):
                    score += 4
                if not terms or score > 0:
                    focus_start, focus_end = _excerpt_focus(content, left, right, code_ranges, local_terms)
                    if direct:
                        left = ranges[max(0, position - 1)][0]
                        right = ranges[min(len(ranges) - 1, position + 1)][1]
                    excerpts.append(_Excerpt(index, segment, left, right, score, focus_start, focus_end))
    excerpts.sort(key=lambda item: (item.source not in named, -item.score, item.source, item.segment, item.start))
    chosen: list[_Excerpt] = []

    def project(items: list[_Excerpt]) -> list[AnswerSource | WebSource]:
        """按候选来源分组已选摘录，重新构造可送入模型的来源对象列表。"""
        grouped: dict[int, list[_Excerpt]] = {}
        for item in items:
            grouped.setdefault(item.source, []).append(item)
        return [_excerpt_source(candidates[index], group) for index, group in sorted(grouped.items())]

    def contained(item: _Excerpt) -> bool:
        """检查同一来源、同一片段中是否已选过覆盖该摘录的更大字符区间。"""
        return any(old.source == item.source and old.segment == item.segment
                   and old.start <= item.start and item.end <= old.end for old in chosen)

    def budgeted_window(item: _Excerpt) -> _Excerpt | None:
        # 对有限原文候选逐项核验，不假定 token 数随字符窗口严格单调。
        content, codes = contexts[item.source, item.segment]
        minimum = item.focus_end - item.focus_start
        maximum = item.end - item.start
        lengths = {minimum, maximum}
        lengths.update(minimum + (maximum - minimum) * step // 128 for step in range(129))
        lengths.update(min(maximum, minimum + 2 ** power) for power in range(maximum.bit_length()))
        seen = set()
        for length in sorted(lengths, reverse=True):
            start = max(item.start, min(item.focus_start - (length - minimum) // 2, item.end - length))
            end = start + length
            for left, right in codes:
                if left < start < right:
                    start = right
                if left < end < right:
                    end = left
            if content[start - 1:start + 1] == "\r\n":
                start += 1
            if content[end - 1:end + 1] == "\r\n":
                end -= 1
            if (start, end) in seen:
                continue
            seen.add((start, end))
            window = replace(item, start=start, end=end)
            if start <= item.focus_start < item.focus_end <= end and fits(project([*chosen, window])):
                return window
        return None

    # 先按来源分组保留各来源排名最高的片段，其余片段按相关度补充。
    first, rest, seen = [], [], set()
    for item in excerpts:
        key = source_identity(candidates[item.source])
        (rest if key in seen else first).append(item)
        seen.add(key)
    # 首轮仅尝试每份来源的核心句或完整代码块；能否保留仍由完整请求预算决定。
    for item in first:
        content, codes = contexts[item.source, item.segment]
        source = candidates[item.source]
        if isinstance(source, WebSource) and source.source_type == "web_search_summary":
            core_start, core_end = item.start, item.end
        else:
            core_start, core_end = next(
                span for span in _text_ranges(content, codes)
                if span[0] <= item.focus_start < item.focus_end <= span[1]
            )
        core = replace(item, start=core_start, end=core_end)
        if fits(project([*chosen, core])):
            chosen.append(core)
    pending = []
    for item in [*first, *rest]:
        if contained(item):
            continue
        trial = [*chosen, item]
        if fits(project(trial)):
            chosen = trial
        else:
            _, codes = contexts[item.source, item.segment]
            if (item.focus_start, item.focus_end) in codes:
                code = replace(item, start=item.focus_start, end=item.focus_end)
                if not contained(code) and fits(project([*chosen, code])):
                    chosen.append(code)
            pending.append(item)
    # 完整句/行优先。长正文以查询命中为中心，在同一完整请求预算内确定连续窗口；代码保持原子性。
    for item in pending:
        if not contained(item) and (window := budgeted_window(item)) is not None:
            chosen.append(window)
    return project(chosen)


def render_materials(sources: list[AnswerSource | WebSource]) -> str:
    """把来源列表渲染为带引用编号和来源身份的正文字符串，供提示模板或预算估算使用。"""
    return "\n\n".join(
        (
            f"[来源 {source.citation_id}: {source.document_filename}]\n{source.content}"
            if isinstance(source, AnswerSource)
            else f"[来源 {source.citation_id}: {source.title}; {source.source_type}; "
            f"{source.url}; 获取时间 {source.fetched_at}]\n{source.content}"
        )
        for source in sources
    )
