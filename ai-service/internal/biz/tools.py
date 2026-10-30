"""首批只读工具的唯一执行入口；不开放任意工具注册。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import TYPE_CHECKING, Annotated, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)
from pydantic_core import PydanticCustomError

from internal.biz.materials import InputBudget, MaterialProcessor, pack_sources
from internal.biz.tool_types import ToolCall, ToolError, ToolFailure, ToolResult, WebDiagnostic
from internal.biz.web_access import normalize_public_url, validate_public_url
from pkg.errors.base import APIError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from internal.biz.repo import ChunkRepo, KnowledgeBaseRepo, WebProvider
    from internal.biz.retrieval import RetrievalBiz

T = TypeVar("T")


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KnowledgeSearchArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=512, pattern=r"\S", description="知识库检索子问题")
    top_k: int = Field(default=5, ge=1, le=10)


SearchKeyword = Annotated[str, StringConstraints(
    min_length=1, max_length=64, pattern=r"^[^\u0000-\u001f]*\S[^\u0000-\u001f]*$",
)]


class WebSearchArguments(ToolArguments):
    keywords: list[SearchKeyword] = Field(
        min_length=1, max_length=8,
        description='1 到 8 项字符串；每项 1 到 64 字符、单行且非空白；用一个空格连接后最多 120 字符。仅必要公开关键词，禁止私有资料原文',
    )
    max_results: int = Field(default=5, ge=1, le=5)

    @field_validator("keywords")
    @classmethod
    def minimal_keywords(cls, values: list[str]) -> list[str]:
        length = len(" ".join(values))
        if length > 120:
            raise PydanticCustomError(
                "keyword_total_length", "连接后关键词长 {actual_length}，允许最多 {max_length} 字符",
                {"actual_length": length, "max_length": 120},
            )
        return values


class WebReadArguments(ToolArguments):
    urls: list[str] = Field(
        min_length=1, max_length=3, description="本次搜索已返回或用户明确提供的公开网址"
    )


_ARGUMENTS = {
    "knowledge_base_search": KnowledgeSearchArguments,
    "web_search": WebSearchArguments,
    "web_read": WebReadArguments,
}
_DESCRIPTIONS = {
    "knowledge_base_search": "检索用户已选择且有权访问的知识库，返回带分块位置的资料。范围由服务器固定。",
    "web_search": "用必要公开关键词搜索网页，仅返回搜索摘要；摘要不代表已读取网页正文。keywords 每项最多 64 字符，各项用一个空格连接后总计最多 120 字符；这是本地约束。",
    "web_read": "读取本次已发现或用户明确提供的公开网页正文；不能访问内网或任意新网址。",
}


def _parse_arguments(call: ToolCall) -> ToolArguments:
    """Schema 与执行共用参数模型；错误只含位置、类型和长度，不回显参数原文。"""
    try:
        return _ARGUMENTS[call.name].model_validate_json(call.arguments)
    except ValidationError as exc:
        details = []
        fields = {"query", "top_k", "keywords", "max_results", "urls"}
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
    readable_urls: dict[str, str] = field(default_factory=dict)
    search_top_k: int = 10


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
    ) -> None:
        self._retrieval = retrieval
        self._kb_repo = kb_repo
        self._materials = MaterialProcessor(chunk_repo, context_window, budget)
        self._budget = budget
        self._web = web
        self._timeout = timeout
        self._max_content_chars = max_content_chars

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
                session.readable_urls[normalized] = ""

        await run_scoped(authorize, session, self._timeout)
        return session

    async def _authorize(self, scope: ToolScope) -> None:
        for kid in scope.knowledge_base_ids:
            kb = await self._kb_repo.get_by_id(kid)
            if kb is None or kb.owner_id != scope.user_id:
                raise ToolError("KNOWLEDGE_BASE_FORBIDDEN", "所选知识库不存在或无访问权限")

    @staticmethod
    def call_key(call: ToolCall) -> str:
        """按实际查询内容识别重复；改变数量或参数排列不能绕过检查。"""
        if call.name not in _ARGUMENTS or len(call.arguments) > 8192:
            raise ToolError("INVALID_TOOL_ARGUMENTS", "工具名称或参数无效")
        args = _parse_arguments(call)
        if isinstance(args, KnowledgeSearchArguments):
            value = " ".join(args.query.casefold().split())
        elif isinstance(args, WebSearchArguments):
            value = sorted({" ".join(word.casefold().split()) for word in args.keywords})
        else:
            value = sorted({normalize_public_url(url) for url in args.urls})
        return json.dumps([call.name, value], ensure_ascii=False)

    def definitions(self, scope: ToolScope) -> list[dict]:
        names = ["knowledge_base_search"] if scope.knowledge_base_ids else []
        if scope.allow_web:
            names.extend(("web_search", "web_read"))
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
            if diagnostic is None and exc.code == "TOOL_TIMEOUT" and call.name in ("web_search", "web_read"):
                diagnostic = WebDiagnostic(
                    endpoint="search" if call.name == "web_search" else "extract",
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
        except Exception:
            # 不发送异常原文或私有资料；明确故障，不返回空结果，不自动改走网页。
            return ToolResult(
                call.id,
                call.name,
                "error",
                errors=[ToolFailure("TOOL_DEPENDENCY_FAILED", "只读工具依赖执行失败")],
            )

    async def _execute(self, call: ToolCall, session: ToolSession) -> ToolResult:
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
            )
            result = ToolResult(
                call.id,
                call.name,
                "success" if prepared.sources else "empty",
                sources=prepared.sources,
            )
        elif isinstance(arguments, WebSearchArguments):
            web = await self._web.search(" ".join(arguments.keywords), arguments.max_results)
            result = ToolResult(
                call.id, call.name, "success", sources=web.sources, errors=web.errors,
                diagnostics=web.diagnostics,
            )
        else:
            urls = list(dict.fromkeys(normalize_public_url(url) for url in arguments.urls))
            if any(url not in session.readable_urls for url in urls):
                raise ToolError("URL_NOT_APPROVED", "只可读取本次搜索已返回或用户明确提供的网址")
            web = await self._web.read(urls)
            for source in web.sources:
                source.title = source.title or session.readable_urls[source.url]
            result = ToolResult(
                call.id, call.name, "success", sources=web.sources, errors=web.errors,
                diagnostics=web.diagnostics,
            )
        self._bound_result(result)
        if call.name == "web_search":
            for source in result.sources:
                session.readable_urls[source.url] = source.title
        result.status = (
            ("partial" if result.sources else "error")
            if result.errors
            else ("success" if result.sources else "empty")
        )
        return result

    def _bound_result(self, result: ToolResult) -> None:
        """工具输出上限；完整模型消息及所有历史的预算在 request_action 中再核算。"""
        had_sources = bool(result.sources)
        def fits(sources):
            bounded = replace(result, sources=sources)
            return (sum(len(source.content) for source in sources) <= self._max_content_chars
                    and len(json.dumps(bounded.to_message(), ensure_ascii=False).encode()) <= 12000)
        result.sources = [replace(source, citation_id=0) for source in pack_sources(result.sources, fits)]
        if not fits(result.sources) or (had_sources and not result.sources):
            raise ToolError("TOOL_RESULT_TOO_LARGE", "资料无法在工具结果预算内保留")
