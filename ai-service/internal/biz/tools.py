"""首批只读工具的唯一执行入口；不开放任意工具注册。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from internal.biz.materials import AnswerSource, InputBudget, MaterialProcessor, pack_sources
from internal.biz.tool_types import ToolCall, ToolError, ToolFailure, ToolResult, WebDiagnostic
from internal.biz.web_access import normalize_public_url, validate_public_url
from internal.biz.web_sources import WebRegistry
from pkg.errors.base import APIError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from internal.biz.materials import WebSource
    from internal.biz.repo import (
        ChunkRepo,
        DocumentRepo,
        KnowledgeBaseRepo,
        TextChunker,
        WebProvider,
    )
    from internal.biz.retrieval import RetrievalBiz

T = TypeVar("T")


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KnowledgeSearchArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=512, pattern=r"\S", description="知识库检索子问题")
    top_k: int = Field(default=5, ge=1, le=10)


SourceId = Annotated[str, StringConstraints(pattern=r"^web_[1-9][0-9]*$", max_length=32)]


class WebSearchArguments(ToolArguments):
    query: str = Field(
        min_length=1, max_length=120, pattern=r"^[^\u0000-\u001f]*\S[^\u0000-\u001f]*$",
        description="完整搜索短语，单行且非空白，最多 120 字符；仅必要公开信息，禁止私有资料原文",
    )
    max_results: int = Field(default=5, ge=1, le=5)

class WebReadArguments(ToolArguments):
    source_id: SourceId
    start: int = Field(default=0, ge=0, le=2_000_000, description="正文字符起点；首次从 0 开始")
    max_chars: int = Field(default=5000, ge=100, le=5000)


class WebFindArguments(ToolArguments):
    source_id: SourceId
    pattern: str = Field(min_length=1, max_length=120, pattern=r"\S", description="已读取正文中的字面文本，不是正则")


class FinishArguments(ToolArguments):
    outcome: Literal["answered", "insufficient"]
    citation_ids: list[Annotated[int, Field(gt=0)]] = Field(max_length=30)

    @model_validator(mode="after")
    def answered_needs_evidence(self) -> FinishArguments:
        if self.outcome == "answered" and not self.citation_ids:
            raise ValueError("answered requires citation_ids")
        return self


_ARGUMENTS = {
    "knowledge_base_search": KnowledgeSearchArguments,
    "web_search": WebSearchArguments,
    "web_read": WebReadArguments,
    "web_find": WebFindArguments,
    "finish_research": FinishArguments,
}
_DESCRIPTIONS = {
    "knowledge_base_search": "检索用户已选择且有权访问的知识库，返回带分块位置的资料。范围由服务器固定。",
    "web_search": "用一个最多 120 字符的 query 搜索。完整 directory 供选择，摘要不是网页正文。",
    "web_read": "通过 directory 中的 source_id 读取正文；首次抓取后固定使用本次快照。start/max_chars 可定位其他片段，返回原文字符范围。不能填写网址。",
    "web_find": "在已读取快照中查找 pattern，仅返回最多 10 个命中的 source_id、字符位置、短预览和 in_code；不是引用证据。选择相关命中后用 web_read 的 start/max_chars 读取小段正文；不发网络请求。",
    "finish_research": "证据齐备或已确认资料不足时单独调用，提交实际已送模的 citation_ids 和 outcome。answered 必须有证据；使用网页摘要时还须有正文依据。该动作结束取证，随后独立生成答案，不再探索。",
}


def _parse_arguments(call: ToolCall) -> ToolArguments:
    """Schema 与执行共用参数模型；错误只含位置、类型和长度，不回显参数原文。"""
    try:
        return _ARGUMENTS[call.name].model_validate_json(call.arguments)
    except ValidationError as exc:
        details = []
        fields = {"query", "top_k", "max_results", "source_id", "start", "max_chars", "pattern", "outcome", "citation_ids"}
        for error in exc.errors(include_url=False, include_input=True)[:4]:
            location = ".".join(str(part + 1) if isinstance(part, int) else
                                part if part in fields else "extra_field" for part in error["loc"])
            context = {key: value for key, value in error.get("ctx", {}).items()
                       if key in {"min_length", "max_length", "actual_length", "ge", "le"}
                       and type(value) in (int, float)}
            if error["type"] in ("string_too_long", "string_too_short"):
                context["actual_length"] = len(error["input"])
            limits = ", ".join(f"{key}={value}" for key, value in context.items())
            details.append(f"{location or 'arguments'}: {error['type']}" + (f" ({limits})" if limits else ""))
        raise ToolError("INVALID_TOOL_ARGUMENTS", "；".join(details)) from exc


@dataclass(frozen=True)
class ToolScope:
    user_id: int
    knowledge_base_ids: tuple[int, ...]
    allow_web: bool


@dataclass
class ToolSession:
    scope: ToolScope
    deadline: float = field(default_factory=lambda: monotonic() + 120)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    # 由服务器根据本次搜索结果或用户明确提供的网址登记，不接受模型设置。
    web: WebRegistry = field(default_factory=WebRegistry)
    delivered_citations: dict = field(default_factory=dict)
    evidence_actions: int = 0
    finished: bool = False
    search_top_k: int = 10
    question: str = ""
    active_sources: list[AnswerSource | WebSource] = field(default_factory=list)


async def run_scoped(
    operation: Callable[[], Awaitable[T]], session: ToolSession, timeout: float
) -> T:
    """取消会关闭正在等待的模型/工具请求；不声称能停止供应商计算或计费。"""
    if session.cancelled.is_set():
        raise asyncio.CancelledError
    remaining = min(timeout, session.deadline - monotonic())
    if remaining <= 0:
        raise ToolError("TOOL_TIMEOUT", "本次执行时限已到")
    task = asyncio.create_task(operation())
    cancellation = asyncio.create_task(session.cancelled.wait())
    try:
        done, _ = await asyncio.wait(
            (task, cancellation), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if session.cancelled.is_set():
            raise asyncio.CancelledError
        if task not in done:
            raise ToolError("TOOL_TIMEOUT", "操作超过允许时限")
        return await task
    finally:
        for pending in (task, cancellation):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, cancellation, return_exceptions=True)


class ReadOnlyToolExecutor:
    def __init__(
        self,
        retrieval: RetrievalBiz,
        kb_repo: KnowledgeBaseRepo,
        chunk_repo: ChunkRepo,
        web: WebProvider,
        budget: InputBudget,
        context_window: int = 0,
        timeout: float = 30,
        max_content_chars: int = 8000,
        web_page_chars: int = 5000,
        document_repo: DocumentRepo | None = None,
        chunker: TextChunker | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._kb_repo = kb_repo
        self._materials = MaterialProcessor(
            chunk_repo, context_window, budget, document_repo=document_repo, chunker=chunker,
        )
        self._budget = budget
        self._web = web
        self._timeout = timeout
        self._max_content_chars = max_content_chars
        self._web_page_chars = web_page_chars

    async def create_session(
        self,
        user_id: int,
        knowledge_base_ids: list[int],
        allow_web: bool,
        user_urls: tuple[str, ...] = (),
        *,
        cancelled: asyncio.Event | None = None,
        deadline: float | None = None,
    ) -> ToolSession:
        if type(user_id) is not int or user_id <= 0 or type(allow_web) is not bool:
            raise ToolError("INVALID_SCOPE", "工具用户或联网范围无效")
        if any(type(kid) is not int or kid <= 0 for kid in knowledge_base_ids):
            raise ToolError("INVALID_SCOPE", "知识库范围无效")
        if user_urls and not allow_web:
            raise ToolError("WEB_DISABLED", "本次提问未开启联网")
        if len(user_urls) > 3:
            raise ToolError("INVALID_SCOPE", "一次最多允许三个用户提供的网址")
        session = ToolSession(
            ToolScope(user_id, tuple(dict.fromkeys(knowledge_base_ids)), allow_web)
        )
        if cancelled is not None:
            session.cancelled = cancelled
        if deadline is not None:
            session.deadline = min(session.deadline, deadline)

        async def authorize() -> None:
            await self._authorize(session.scope)
            for url in user_urls:
                normalized = await validate_public_url(url)
                session.web.register(normalized, "", "user")

        await run_scoped(authorize, session, self._timeout)
        return session

    async def _authorize(self, scope: ToolScope) -> None:
        for kid in scope.knowledge_base_ids:
            kb = await self._kb_repo.get_by_id(kid)
            if kb is None or kb.owner_id != scope.user_id:
                raise ToolError("KNOWLEDGE_BASE_FORBIDDEN", "所选知识库不存在或无访问权限")

    async def validate_sources(self, session: ToolSession, sources: list[AnswerSource | WebSource]) -> None:
        async def validate() -> None:
            await self._authorize(session.scope)
            knowledge = [source for source in sources if isinstance(source, AnswerSource)]
            if any(source.knowledge_base_id not in session.scope.knowledge_base_ids for source in knowledge):
                raise ToolError("EVIDENCE_SCOPE_INVALID", "证据不属于本次授权知识库")
            await self._materials.validate_sources(knowledge)
            for source in sources:
                if isinstance(source, AnswerSource):
                    continue
                if not session.scope.allow_web or session.web.get(source.source_id).url != source.url:
                    raise ToolError("EVIDENCE_SCOPE_INVALID", "网页证据不属于本次授权目录")
                if source.source_type == "web_page":
                    page = session.web.page(source.source_id)
                    if (page.fetched_at != source.fetched_at or not 0 <= source.start < source.end <= len(page.content)
                            or page.content[source.start:source.end] != source.content):
                        raise ToolError("SOURCE_CHANGED", "网页证据与本次快照不一致")
        await run_scoped(validate, session, self._timeout)

    @staticmethod
    def call_key(call: ToolCall) -> str:
        """按实际查询内容识别重复；改变数量或参数排列不能绕过检查。"""
        if call.name not in _ARGUMENTS or len(call.arguments) > 8192:
            raise ToolError("INVALID_TOOL_ARGUMENTS", "工具名称或参数无效")
        args = _parse_arguments(call)
        if isinstance(args, (KnowledgeSearchArguments, WebSearchArguments)):
            value = " ".join(args.query.casefold().split())
        elif isinstance(args, WebReadArguments):
            value = [args.source_id, args.start, args.max_chars]
        elif isinstance(args, WebFindArguments):
            value = [args.source_id, args.pattern]
        else:
            value = [args.outcome, sorted(set(args.citation_ids))]
        return json.dumps([call.name, value], ensure_ascii=False)

    def definitions(self, scope: ToolScope) -> list[dict]:
        names = ["knowledge_base_search"] if scope.knowledge_base_ids else []
        if scope.allow_web:
            names.extend(("web_search", "web_read", "web_find"))
        if names:
            names.append("finish_research")
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "strict": True,
                    "description": _DESCRIPTIONS[name],
                    "parameters": _ARGUMENTS[name].model_json_schema(),
                },
            }
            for name in names
        ]

    async def execute(self, call: ToolCall, session: ToolSession) -> ToolResult:
        started = monotonic()
        try:
            return await run_scoped(lambda: self._execute(call, session), session, self._timeout)
        except ToolError as exc:
            diagnostic = exc.diagnostics
            if diagnostic is None and exc.code == "TOOL_TIMEOUT" and call.name in ("web_search", "web_read", "web_find"):
                diagnostic = WebDiagnostic(
                    endpoint="search" if call.name == "web_search" else "web_page",
                    phase="tool_execution", category="timeout",
                    elapsed_seconds=round(monotonic() - started, 3),
                    exception_type=type(exc).__name__,
                    timeout_layer="run" if session.deadline <= started + self._timeout else "tool",
                )
            return ToolResult(
                call.id, call.name, "error",
                errors=[ToolFailure(exc.code, exc.message, diagnostics=diagnostic)],
                diagnostics=[diagnostic] if diagnostic is not None else [],
            )
        except APIError as exc:
            if exc.reason in {
                "SOURCE_RECHUNK_REQUIRED", "SOURCE_CHANGED", "SOURCE_CODE_RANGE_INVALID",
                "SOURCE_CONFIGURATION_INVALID", "MATERIAL_BUDGET_EXCEEDED",
            }:
                return ToolResult(call.id, call.name, "error", errors=[ToolFailure(exc.reason, exc.message)])
            return ToolResult(
                call.id,
                call.name,
                "error",
                errors=[
                    ToolFailure(
                        "TOOL_TIMEOUT"
                        if exc.reason.endswith("TIMEOUT")
                        else "TOOL_DEPENDENCY_FAILED",
                        f"知识库依赖调用失败（{exc.reason}）",
                    )
                ],
            )
        except TimeoutError:
            return ToolResult(
                call.id, call.name, "error", errors=[ToolFailure("TOOL_TIMEOUT", "工具依赖超时")]
            )

    async def _execute(self, call: ToolCall, session: ToolSession) -> ToolResult:
        if session.finished:
            raise ToolError("RUN_STOPPED", "已结束取证，不能再执行工具动作")
        if (
            not isinstance(call.id, str)
            or not call.id.strip()
            or not isinstance(call.name, str)
            or not isinstance(call.arguments, str)
            or len(call.arguments) > 8192
        ):
            raise ToolError("INVALID_TOOL_ARGUMENTS", "工具调用编号或参数长度无效")
        if call.name not in _ARGUMENTS:
            raise ToolError("UNKNOWN_TOOL", "不允许调用此工具")
        if call.name.startswith("web_") and not session.scope.allow_web:
            raise ToolError("WEB_DISABLED", "本次提问未开启联网")
        arguments = _parse_arguments(call)
        await self._authorize(session.scope)
        if isinstance(arguments, KnowledgeSearchArguments):
            if not session.scope.knowledge_base_ids:
                raise ToolError("KNOWLEDGE_BASE_FORBIDDEN", "本次提问未选择知识库")
            hits = await self._retrieval.retrieve_many(
                arguments.query, session.scope.knowledge_base_ids,
                min(arguments.top_k, session.search_top_k),
            )
            prepared = await self._materials.prepare(
                hits,
                lambda context: [{"role": "tool", "tool_call_id": call.id, "content": context}],
                self._budget.output_limit,
                query=f"{session.question}\n{arguments.query}",
            )
            result = ToolResult(
                call.id,
                call.name,
                "success" if prepared.sources else "empty",
                sources=prepared.sources,
            )
        elif isinstance(arguments, WebSearchArguments):
            web = await self._web.search(arguments.query, arguments.max_results)
            directory = []
            for source in web.sources:
                entry = session.web.register(normalize_public_url(source.url), source.title, "search")
                source.source_id = entry.source_id
                source.start, source.end = 0, len(source.content)
                source.total_chars = max(source.total_chars, len(source.content))
                directory.append(replace(entry))
            result = ToolResult(
                call.id, call.name, "success", sources=web.sources, errors=web.errors,
                diagnostics=web.diagnostics, directory=directory,
            )
        elif isinstance(arguments, WebReadArguments):
            entry = session.web.get(arguments.source_id)
            diagnostics = []
            phase = "snapshot_cache"
            try:
                if entry.source_id not in session.web.pages:
                    web = await self._web.read([entry.url])
                    diagnostics = web.diagnostics
                    if web.errors or len(web.pages) != 1:
                        raise ToolError("WEB_SNAPSHOT_INVALID", "正文读取未返回唯一有效快照")
                    session.web.cache(entry.source_id, web.pages[0])
                else:
                    diagnostics = [WebDiagnostic("web_cache", "complete", "cache_hit", 0)]
                phase = "page_window"
                page = session.web.page(entry.source_id)
                window = page.window(entry.source_id, arguments.start, min(arguments.max_chars, self._web_page_chars))
            except ToolError as exc:
                if exc.diagnostics is None and diagnostics:
                    raise ToolError(exc.code, exc.message, diagnostics=replace(
                        diagnostics[-1], phase=phase, category=exc.code.lower(),
                    )) from exc
                raise
            result = ToolResult(
                call.id, call.name, "success",
                sources=[window],
                diagnostics=diagnostics,
            )
        elif isinstance(arguments, WebFindArguments):
            matches = session.web.find(arguments.source_id, arguments.pattern)
            result = ToolResult(call.id, call.name, "success", matches=matches)
        else:
            ids = set(arguments.citation_ids)
            if not session.evidence_actions or not ids.issubset(session.delivered_citations):
                raise ToolError("EVIDENCE_SELECTION_INVALID", "必须先取证，且只能选择实际已送模的引用编号")
            selected = [session.delivered_citations[number] for number in ids]
            if arguments.outcome == "answered" and any(source.source_type == "web_search_summary" for source in selected) and not any(source.source_type == "web_page" for source in selected):
                raise ToolError("WEB_BODY_REQUIRED", "网页摘要不足以完成回答，请先取得正文依据")
            result = ToolResult(
                call.id, call.name, "success",
                selection={"outcome": arguments.outcome, "citation_ids": sorted(ids)},
            )
        self._bound_result(result, query=session.question)
        if result.selection is not None:
            session.finished = True
        if call.name != "finish_research" and not result.errors:
            session.evidence_actions += 1
        result.status = (
            ("partial" if result.sources else "error")
            if result.errors
            else ("success" if result.sources or result.directory or result.matches or result.selection is not None else "empty")
        )
        return result

    def _bound_result(self, result: ToolResult, *, query: str) -> None:
        """工具输出上限；完整模型消息及所有历史的预算在 request_action 中再核算。"""
        had_sources = bool(result.sources)
        def fits(sources):
            bounded = replace(result, sources=sources)
            return (sum(len(source.content) for source in sources) <= self._max_content_chars
                    and len(json.dumps(bounded.to_message(), ensure_ascii=False).encode()) <= 12000)
        result.sources = [replace(source, citation_id=0) for source in pack_sources(result.sources, fits, query=query) if source.content.strip()]
        if not fits(result.sources) or (had_sources and not result.sources and not result.directory):
            raise ToolError("TOOL_RESULT_TOO_LARGE", "资料无法在工具结果预算内保留")
