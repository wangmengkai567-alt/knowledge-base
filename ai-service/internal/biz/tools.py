"""固定只读工具的参数定义、模型可见说明和统一执行入口。

工具名与参数模型在 _ARGUMENTS 中固定列出，给模型的说明在 _DESCRIPTIONS 中定义。
ReadOnlyToolExecutor 按权限暴露工具并校验、执行调用；不提供动态注册接口。
cmd/wire.py 负责创建执行器并注入检索、数据库和网页服务等依赖。
"""

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

from internal.biz.materials import AnswerSource, MaterialProcessor, WebSource, pack_sources
from internal.biz.tool_types import ToolCall, ToolError, ToolFailure, ToolResult, WebDiagnostic
from internal.biz.web_access import normalize_public_url, validate_public_url
from internal.biz.web_sources import WebRegistry
from pkg.errors.base import APIError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from internal.biz.repo import (
        ChunkRepo,
        DocumentRepo,
        KnowledgeBaseRepo,
        TextChunker,
        WebProvider,
    )
    from internal.biz.retrieval import RetrievalBiz
    from internal.biz.token_budget import InputBudget

# run_scoped 的泛型返回类型。
T = TypeVar("T")


# 统一所有工具的参数校验规则，供具体工具的参数类继承。
class ToolArguments(BaseModel):
    # 拒绝未定义的字段，也不自动转换参数类型。
    model_config = ConfigDict(extra="forbid", strict=True)


# 定义知识库检索工具的输入格式，并在执行前校验模型提交的参数。
class KnowledgeSearchArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=512, pattern=r"\S", description="知识库检索子问题")
    # 本次请求的结果数量；执行时还会受会话 search_top_k 上限约束。
    top_k: int = Field(default=5, ge=1, le=10)


# 网页工具通过已登记的编号选择网页，实际网址由服务器查找。
SourceId = Annotated[str, StringConstraints(pattern=r"^web_[1-9][0-9]*$", max_length=32)]


# 定义网页搜索工具的输入格式，并在执行前校验模型提交的参数。
class WebSearchArguments(ToolArguments):
    query: str = Field(
        min_length=1, max_length=120, pattern=r"^[^\u0000-\u001f]*\S[^\u0000-\u001f]*$",
        description="完整搜索短语，单行且非空白，最多 120 字符；仅必要公开信息，禁止私有资料原文",
    )
    max_results: int = Field(default=10, ge=1, le=10)

# 限定网页读取工具能接收的调用参数，避免模型直接指定任意网址。
class WebReadArguments(ToolArguments):
    source_id: SourceId
    start: int = Field(default=0, ge=0, le=2_000_000, description="正文字符起点；首次从 0 开始")
    # 网页原文窗口的 token 额度，与顶层问答的输出 max_tokens 不同。
    max_tokens: int = Field(default=2000, ge=64, le=2000, description="网页原文窗口 token 额度，不是最终回答输出额度；下一页用实际返回 end")


# 定义页内查找工具的输入格式，供模型发起查找、执行器校验调用。
class WebFindArguments(ToolArguments):
    source_id: SourceId
    pattern: str = Field(min_length=1, max_length=120, pattern=r"\S", description="已读取正文中的字面文本，不是正则")


# 校验模型结束资料收集时提交的结论和引用，供后续生成答案使用。
class FinishArguments(ToolArguments):
    # answered 表示已有依据可以回答；insufficient 表示资料不足。
    outcome: Literal["answered", "insufficient"]
    # 只能选择实际已送给模型的引用编号；这一会话级条件在 _execute 中校验。
    citation_ids: list[Annotated[int, Field(gt=0)]] = Field(max_length=30)

    @model_validator(mode="after")
    def answered_needs_evidence(self) -> FinishArguments:
        # 模型声明已有充分资料时，必须同时选出引用。
        if self.outcome == "answered" and not self.citation_ids:
            raise ValueError("answered requires citation_ids")
        return self


# 工具名与参数模型的固定对应表：用于校验调用，也用于生成给模型的工具定义。
# 新增工具还需同步 definitions 的暴露条件、call_key 的去重规则和 _execute 的执行分支。
_ARGUMENTS = {
    "knowledge_base_search": KnowledgeSearchArguments,
    "web_search": WebSearchArguments,
    "web_read": WebReadArguments,
    "web_find": WebFindArguments,
    "finish_research": FinishArguments,
}
# 告诉大模型每个工具能做什么；definitions 将这些说明放入工具定义。
_DESCRIPTIONS = {
    "knowledge_base_search": "检索用户已选择且有权访问的知识库，返回带分块位置的资料。范围由服务器固定。",
    "web_search": "用一个最多 120 字符的 query 搜索。完整 directory 供选择，摘要不是网页正文。",
    "web_read": "通过 directory 中的 source_id 读取正文；首次抓取后固定使用本次快照。start/max_tokens 可定位其他片段，返回原文字符范围。不能填写网址。",
    "web_find": "在已读取快照中查找 pattern，仅返回最多 10 个命中的 source_id、字符位置、短预览和 in_code；不是引用证据。选择相关命中后用 web_read 的 start/max_tokens 读取小段正文；不发网络请求。",
    "finish_research": "证据齐备或已确认资料不足时单独调用，提交实际已送模的 citation_ids 和 outcome。answered 必须有证据，可使用搜索摘要；用户明确要求读取网页时须先实际读取，不能把摘要当作已读正文。该动作结束取证，随后独立生成答案，不再探索。",
}


def _parse_arguments(call: ToolCall) -> ToolArguments:
    """Schema 与执行共用参数模型；错误只含位置、类型和长度，不回显参数原文。"""
    try:
        return _ARGUMENTS[call.name].model_validate_json(call.arguments)
    except ValidationError as exc:
        details = []
        fields = {"query", "top_k", "max_results", "source_id", "start", "max_tokens", "pattern", "outcome", "citation_ids"}
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
    """服务器确定的用户、知识库和联网权限；模型参数不能扩大此范围。"""

    user_id: int
    knowledge_base_ids: tuple[int, ...]
    allow_web: bool


# 保存一次问答共享的权限和执行状态，让多次工具调用使用同一访问范围和网页快照。
@dataclass
class ToolSession:
    scope: ToolScope
    deadline: float = field(default_factory=lambda: monotonic() + 120)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
    # 由服务器根据本次搜索结果或用户明确提供的网址登记，不接受模型设置。
    web: WebRegistry = field(default_factory=WebRegistry)
    # 已送模编号用于校验 finish_research 的选择；active_sources 是当前上下文保留的原文。
    delivered_citations: dict = field(default_factory=dict)
    evidence_actions: int = 0
    finished: bool = False
    search_top_k: int = 10
    question: str = ""
    active_sources: list[AnswerSource | WebSource] = field(default_factory=list)
    final_reserve_controls: dict[str, dict] = field(default_factory=dict)


async def run_scoped(
    operation: Callable[[], Awaitable[T]], session: ToolSession, timeout: float
) -> T:
    """在会话剩余总时限与单次 timeout 内等待异步 operation，返回其执行结果。

    同时监听 session.cancelled；取消时抛出 CancelledError，超时时抛出 ToolError。
    退出前取消并等待尚未结束的本地任务，避免模型或工具请求遗留在后台。
    """
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
    """只读工具的统一入口：校验参数与权限，限制执行时限并约束返回资料大小。"""

    def __init__(
        self,
        retrieval: RetrievalBiz,
        kb_repo: KnowledgeBaseRepo,
        chunk_repo: ChunkRepo,
        web: WebProvider,
        budget: InputBudget,
        context_window: int = 0,
        timeout: float = 30,
        document_repo: DocumentRepo | None = None,
        chunker: TextChunker | None = None,
    ) -> None:
        """注入工具依赖及预算；context_window 为两侧扩展块数，timeout 单位为秒。"""
        self._retrieval = retrieval
        self._kb_repo = kb_repo
        self._materials = MaterialProcessor(
            chunk_repo, context_window, document_repo=document_repo, chunker=chunker,
        )
        self._budget = budget
        self._web = web
        self._timeout = timeout
        self._web_page_tokens = budget.limits.page_tokens

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
        """为一次问答创建工具会话：固定用户、知识库、联网权限，并验证初始访问范围。

        user_urls 最多三个，校验为公开网址后登记到网页目录；本步骤不会抓取网页正文。
        cancelled 和 deadline 让后续模型及工具共享同一个取消信号与截止时间。
        """
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
            """先核对知识库所有权，再校验用户 URL 并登记为本次可读取的网页。"""
            await self._authorize(session.scope)
            for url in user_urls:
                normalized = await validate_public_url(url)
                session.web.register(normalized, "", "user")

        await run_scoped(authorize, session, self._timeout)
        return session

    async def _authorize(self, scope: ToolScope) -> None:
        """逐个核对 scope 内的知识库是否存在且归该用户所有；任一不符即拒绝本次访问。"""
        for kid in scope.knowledge_base_ids:
            kb = await self._kb_repo.get_by_id(kid)
            if kb is None or kb.owner_id != scope.user_id:
                raise ToolError("KNOWLEDGE_BASE_FORBIDDEN", "所选知识库不存在或无访问权限")

    async def validate_sources(self, session: ToolSession, sources: list[AnswerSource | WebSource]) -> None:
        """生成前或发布前再次核验证据权限、文档版本及原文范围。

        知识库资料须仍对应当前就绪文档；网页正文须与本次已缓存快照一致。
        不返回新资料；来源变化或越权时抛出异常，使问答停止发布。
        """
        async def validate() -> None:
            """重新核对访问权限、文档版本及网页快照，防止发布已失效或越权的引用。"""
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
        """按工具类型规范化参数，生成识别重复动作的字符串键。

        搜索按去除多余空白并转小写的查询去重，改变结果数量不构成新查询。
        网页读取按来源编号、字符起点和窗口大小区分；查找按来源编号与查找文本区分。
        结束取证时对引用编号去重、排序，再与 outcome 一起生成键。
        """
        if call.name not in _ARGUMENTS or len(call.arguments) > 8192:
            raise ToolError("INVALID_TOOL_ARGUMENTS", "工具名称或参数无效")
        args = _parse_arguments(call)
        if isinstance(args, (KnowledgeSearchArguments, WebSearchArguments)):
            value = " ".join(args.query.casefold().split())
        elif isinstance(args, WebReadArguments):
            value = [args.source_id, args.start, args.max_tokens]
        elif isinstance(args, WebFindArguments):
            value = [args.source_id, args.pattern]
        else:
            # 剩余类型为结束取证；引用顺序不同仍视为同一次选择。
            value = [args.outcome, sorted(set(args.citation_ids))]
        return json.dumps([call.name, value], ensure_ascii=False)

    def definitions(self, scope: ToolScope) -> list[dict]:
        """按服务器固定权限返回模型可调用的工具 JSON Schema；禁止联网时不暴露网页工具。"""
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
        """执行工具并裁剪到 token/字节限额；结果可发送后再更新取证次数或结束标记。"""
        result = await self._collect_result(call, session)
        try:
            self._bound_result(result, query=session.question)
        except ToolError as exc:
            result = ToolResult(call.id, call.name, "error", errors=[*result.errors, ToolFailure(exc.code, exc.message)],
                                diagnostics=result.diagnostics, budget_diagnostics=result.budget_diagnostics)
            # 必要错误本身也必须满足限额；放不下时向运行层明确报错。
            self._bound_result(result, query=session.question)
        if result.selection is not None:
            session.finished = True
        if call.name != "finish_research" and not result.errors:
            session.evidence_actions += 1
        return result

    async def _collect_result(self, call: ToolCall, session: ToolSession) -> ToolResult:
        """执行一个工具动作，返回统一的 ToolResult，供问答循环记录并接回模型。

        受控的工具、依赖和超时错误转成结果中的 errors；取消异常继续向上传播。
        实际参数与授权校验、各工具分支执行由 _execute 完成。
        """
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
        """校验并分派单个工具动作，更新会话中的网页目录、快照或取证结束标记。

        call 携带模型提出的工具名和 JSON 参数，session 决定实际允许访问的范围。
        此处抛出的受控错误由 _collect_result 转成错误结果，随后 execute 统一限制结果体积。
        """
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
        # 每次调用重新核验所有权，避免长问答沿用已经失效的访问权限。
        await self._authorize(session.scope)
        # 知识库搜索：只检索会话已授权的知识库，结果经原文核对和邻块整理后返回。
        if isinstance(arguments, KnowledgeSearchArguments):
            if not session.scope.knowledge_base_ids:
                raise ToolError("KNOWLEDGE_BASE_FORBIDDEN", "本次提问未选择知识库")
            hits = await self._retrieval.retrieve_many(
                arguments.query, session.scope.knowledge_base_ids,
                min(arguments.top_k, session.search_top_k),
            )
            sources: list[AnswerSource | WebSource] = list(await self._materials.prepare(hits))
            result = ToolResult(
                call.id,
                call.name,
                "success" if sources else "empty",
                sources=sources,
            )
        # 网页搜索：将结果网址登记为本次目录，并将搜索摘要与目录编号关联。
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
                budget_diagnostics=list(web.budget_diagnostics),
            )
        # 网页读取：只接受目录编号，首次抓取快照，随后按字符窗口从同一快照切片。
        elif isinstance(arguments, WebReadArguments):
            entry = session.web.get(arguments.source_id)
            diagnostics = []
            phase = "snapshot_cache"
            try:
                # 同一网址在本次运行只抓取一次，后续窗口读取与定位共用固定快照。
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
                window = page.window(entry.source_id, arguments.start, min(arguments.max_tokens, self._web_page_tokens), self._budget.counter)
            except ToolError as exc:
                # 保留网页错误及已有诊断；必要时补充是快照取得阶段还是窗口截取阶段失败。
                if exc.diagnostics is None and diagnostics:
                    raise ToolError(exc.code, exc.message, diagnostics=replace(
                        diagnostics[-1], phase=phase, category=exc.code.lower(),
                    )) from exc
                raise
            result = ToolResult(
                call.id, call.name, "success",
                sources=[window],
                diagnostics=diagnostics,
                budget_diagnostics=[{
                    "layer": "page", "reasons": ["page_token_limit"] if window.end < len(page.content) else [],
                    "original_chars": len(page.content), "original_bytes": len(page.content.encode("utf-8")),
                    "retained_ranges": [{"source_id": entry.source_id, "start": window.start, "end": window.end}],
                    "content_tokens": self._budget.counter.count_text(window.content),
                }],
            )
        # 页内查找：仅在已有快照中定位文本，返回预览和位置，不将预览登记为正文证据。
        elif isinstance(arguments, WebFindArguments):
            matches = session.web.find(arguments.source_id, arguments.pattern, self._budget.counter, self._budget.limits.preview_tokens)
            page = session.web.page(arguments.source_id)
            result = ToolResult(call.id, call.name, "success", matches=matches, budget_diagnostics=[{
                "layer": "preview", "reasons": ["preview_token_limit"] if any(match["preview_end"] < len(page.content) for match in matches) else [],
                "original_chars": len(page.content), "original_bytes": len(page.content.encode("utf-8")),
                "retained_ranges": [{"start": match["preview_start"], "end": match["preview_end"]} for match in matches],
                "preview_tokens": [self._budget.counter.count_text(match["preview"]) for match in matches],
            }])
        else:
            # 结束取证前，只接受确实已送给模型的引用编号。
            ids = set(arguments.citation_ids)
            if not session.evidence_actions or not ids.issubset(session.delivered_citations):
                raise ToolError("EVIDENCE_SELECTION_INVALID", "必须先取证，且只能选择实际已送模的引用编号")
            result = ToolResult(
                call.id, call.name, "success",
                selection={"outcome": arguments.outcome, "citation_ids": sorted(ids)},
            )
        result.status = (
            ("partial" if result.sources else "error")
            if result.errors
            else ("success" if result.sources or result.directory or result.matches or result.selection is not None else "empty")
        )
        return result

    def _bound_result(self, result: ToolResult, *, query: str) -> None:
        """按 token 和字节上限裁剪工具结果，记录裁剪原因和保留的原文范围。"""
        had_sources = bool(result.sources)
        limits = self._budget.limits
        original_chars = sum(len(source.content) for source in result.sources)
        original_bytes = sum(len(source.content.encode("utf-8")) for source in result.sources)
        reasons = set()
        diagnostic = {"reasons": [], "original_chars": original_chars, "original_bytes": original_bytes}
        result.budget_diagnostics.append(diagnostic)

        def bounded(sources):
            """按保留的来源重建工具结果副本，并同步成功、部分成功、错误或空结果状态。"""
            status: Literal["success", "empty", "partial", "error"] = (("partial" if sources else "error") if result.errors else
                      ("success" if sources or result.directory or result.matches or result.selection is not None else "empty"))
            return replace(result, sources=sources, status=status)

        def fits(sources):
            """分别检查完整消息的 UTF-8 字节数和 content 的 token 数，记录导致超限的类别。"""
            message = bounded(sources).to_message()
            if len(json.dumps(message, ensure_ascii=False).encode("utf-8")) > limits.tool_result_max_bytes:
                reasons.add("tool_byte_limit")
                diagnostic["reasons"] = sorted(reasons)
                return False
            if self._budget.counter.count_text(message["content"]) > limits.tool_result_max_tokens:
                reasons.add("tool_token_limit")
                diagnostic["reasons"] = sorted(reasons)
                return False
            return True

        if not fits([]):
            raise ToolError("TOOL_RESULT_TOO_LARGE", "必要工具元数据超过 " + ",".join(sorted(reasons)))
        result.sources = [replace(source, citation_id=0) for source in pack_sources(result.sources, fits, query=query) if source.content.strip()]
        result.status = bounded(result.sources).status
        if not fits(result.sources) or (had_sources and not result.sources and not result.directory):
            raise ToolError("TOOL_RESULT_TOO_LARGE", "资料无法在工具结果预算内保留（代码块必须完整保留）：" + ",".join(sorted(reasons)))
        diagnostic.update({
            "reasons": sorted(reasons), "original_chars": original_chars, "original_bytes": original_bytes,
            "content_tokens": self._budget.counter.count_text(result.to_message()["content"]),
            "message_bytes": len(json.dumps(result.to_message(), ensure_ascii=False).encode("utf-8")),
            "retained_ranges": [{"source_id": source.source_id, "start": source.start, "end": source.end}
                                if isinstance(source, WebSource) else {"segments": [{"chunk_id": part.chunk_id, "start": part.start, "end": part.end} for part in source.segments]}
                                for source in result.sources],
        })
