"""有预算的后端问答循环；同步和 SSE 使用同一次执行结果。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import TYPE_CHECKING, Literal

import structlog

from internal.biz.answer_run import AnswerRun, RunLimits
from internal.biz.entity import ChatChunk
from internal.biz.materials import AnswerSource, InputBudget, WebSource, clip_source
from internal.biz.tool_types import ToolError, ToolFailure, ToolResult
from internal.biz.tools import ToolScope
from pkg.errors.base import APIError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from internal.biz.entity import ChatResult, RetrievalResult
    from internal.biz.tool_model import ToolModel
    from internal.biz.tool_types import ToolCall
    from internal.biz.tools import ToolSession
    from internal.prompt.manager import PromptManager

logger = structlog.get_logger()
RAG_EVENT_SOURCES = "sources"
RAG_EVENT_CHUNK = "chunk"
RAG_EVENT_TERMINAL = "terminal"
_FINAL_CONTROL = {
    "role": "system",
    "content": "现在只发布最终答案，不再调用工具。依据已有资料作答；资料不足时说明缺失信息。严格使用规定的 JSON 格式。",
}


@dataclass
class RAGStreamEvent:
    event_type: Literal["sources", "chunk", "terminal"]
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    retrieval_results: list[RetrievalResult] = field(default_factory=list)
    chunk: ChatChunk | None = None
    input_measurement: str = ""
    estimated_input_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    run: AnswerRun | None = None


def retrieval_results(run: AnswerRun) -> list[RetrievalResult]:
    hits = {}
    for source in run.sources:
        if isinstance(source, AnswerSource):
            for hit in source.retrieval_hits:
                hits.setdefault(hit.chunk_id, hit)
    return list(hits.values())


class RAGBiz:
    def __init__(
        self,
        model: ToolModel,
        prompt_manager: PromptManager,
        budget: InputBudget,
        default_rag_template: str = "rag",
        limits: RunLimits | None = None,
    ) -> None:
        self._model = model
        self._prompts = prompt_manager
        self._budget = budget
        self._template = default_rag_template
        self._limits = limits if limits is not None else RunLimits()

    async def query(
        self,
        query: str,
        knowledge_base_ids: list[int],
        top_k: int = 5,
        temperature: float | None = None,
        max_tokens: int | None = None,
        *,
        user_id: int,
        allow_web: bool = False,
        user_urls: tuple[str, ...] = (),
        cancelled: asyncio.Event | None = None,
    ) -> AnswerRun:
        run = AnswerRun(
            ToolScope(user_id, tuple(dict.fromkeys(knowledge_base_ids)), allow_web),
            self._limits,
            self._budget.measurement,
        )
        run.emit(
            "run_started",
            knowledge_base_ids=list(run.scope.knowledge_base_ids),
            allow_web=allow_web,
        )
        output_tokens = max_tokens if max_tokens is not None else self._budget.output_limit
        try:
            if not 1 <= output_tokens <= self._budget.output_limit or not 1 <= top_k <= 10:
                raise ToolError("INVALID_RUN_ARGUMENTS", "回答或检索数量预算无效")
            session = await self._model.executor.create_session(
                user_id,
                knowledge_base_ids,
                allow_web,
                user_urls,
                cancelled=cancelled,
                deadline=run.started + run.limits.timeout,
            )
            session.search_top_k = top_k
            messages = self._prompts.get_template(self._template).build_messages(
                {
                    "query": query,
                    "context": "资料仅来自后续工具结果。",
                }
            )
            await self._loop(run, session, messages, output_tokens, temperature)
        except asyncio.CancelledError:
            run.finish("cancelled", "user_cancelled")
            if asyncio.current_task().cancelling():
                raise
        except ToolError as exc:
            run.finish(
                "timeout" if exc.code == "TOOL_TIMEOUT" else "tool_failed",
                "execution_timeout" if exc.code == "TOOL_TIMEOUT" else "tool_error",
                exc.code,
            )
        except APIError as exc:
            if "BUDGET" in exc.reason:
                run.finish("budget_exhausted", "input_budget", exc.reason)
            else:
                run.finish(
                    "timeout" if exc.reason.endswith("TIMEOUT") else "model_failed",
                    "model_timeout" if exc.reason.endswith("TIMEOUT") else "model_error",
                    exc.reason,
                )
        except Exception:
            logger.error("answer_run_unexpected_error", run_id=run.run_id)
            run.finish("model_failed", "internal_error", "RUN_INTERNAL_ERROR")
        finally:
            logger.info(
                "answer_run_finished",
                run_id=run.run_id,
                status=run.status,
                stop_reason=run.stop_reason,
                model_calls=run.usage.model_calls,
                tool_calls=run.usage.tool_calls,
                prompt_tokens=run.usage.prompt_tokens,
                completion_tokens=run.usage.completion_tokens,
                charged_tokens=run.usage.charged_tokens,
            )
        return run

    @staticmethod
    def _check_active(run: AnswerRun, session: ToolSession) -> None:
        if session.cancelled.is_set():
            raise asyncio.CancelledError
        if monotonic() >= session.deadline:
            raise ToolError("TOOL_TIMEOUT", "本次执行时限已到")

    async def _loop(
        self,
        run: AnswerRun,
        session: ToolSession,
        messages: list[dict],
        output_tokens: int,
        temperature: float | None,
    ) -> None:
        seen_calls: set[str] = set()
        seen_ids: set[str] = set()
        pending_sources: list[AnswerSource | WebSource] = []
        final_reason = ""
        while run.status == "running":
            self._check_active(run, session)
            usage, limits = run.usage, run.limits
            estimate = self._model.estimate([*messages, _FINAL_CONTROL], session)
            remaining = limits.total_tokens - usage.charged_tokens
            # 每次探索前预留一个最大完整输入和回答，最后一次只能回答。
            reserve = self._budget.input_limit + output_tokens
            if not final_reason:
                if usage.model_calls >= limits.model_calls - 1:
                    final_reason = "model_call_limit"
                elif usage.tool_calls >= limits.tool_calls:
                    final_reason = "tool_call_limit"
                elif remaining < estimate + output_tokens + reserve:
                    final_reason = "token_budget"
            final = bool(final_reason)
            request = [*messages, _FINAL_CONTROL] if final else messages
            estimate = self._model.estimate(request, session)
            if (
                usage.model_calls >= limits.model_calls
                or estimate > self._budget.input_limit
                or estimate + output_tokens > remaining
            ):
                run.finish("budget_exhausted", final_reason or "input_budget")
                return
            self._model.validate_request(request, session, output_tokens)
            self._check_active(run, session)
            usage.model_calls += 1
            usage.estimated_input_tokens += estimate
            usage.charged_tokens += estimate + output_tokens
            usage.unreported_model_calls += 1
            run.sources.extend(pending_sources)
            pending_sources = []
            run.emit(
                "model_started",
                call=usage.model_calls,
                final=final,
                final_reason=final_reason,
                estimated_input_tokens=estimate,
                output_limit=output_tokens,
            )
            result = await self._model.request_action(
                request,
                session,
                tool_choice="none" if final else "auto",
                max_tokens=output_tokens,
                temperature=temperature,
            )
            self._account(run, result, estimate, output_tokens)
            self._check_active(run, session)
            if run.status != "running":
                return
            if not result.tool_calls:
                self._publish(run, result, final_reason)
                return
            if final:
                run.finish("model_failed", "unexpected_tool_call", "INVALID_TOOL_RESPONSE")
                return
            if len({call.id for call in result.tool_calls}) != len(result.tool_calls) or any(
                call.id in seen_ids for call in result.tool_calls
            ):
                run.finish("model_failed", "duplicate_call_id", "INVALID_TOOL_HISTORY")
                return
            seen_ids.update(call.id for call in result.tool_calls)
            assistant = result.to_message()
            assistant["content"] = ""  # 工具轮只保存动作，不保存模型的解释或内部推理。
            before_action = len(messages)
            messages.append(assistant)
            start = len(messages)
            for call in result.tool_calls:
                messages.append(self._denied(call, "RUN_STOPPED").to_message())
            if (
                self._model.estimate([*messages, _FINAL_CONTROL], session)
                > self._budget.input_limit
            ):
                # 尚未执行任何动作，无需把无法容纳的请求加入后续原生协议。
                # 保留上一份完整上下文和预留额度，用最后一轮回答明确结束。
                del messages[before_action:]
                final_reason = "input_budget"
                run.emit("tool_rejected", reason="input_budget", count=len(result.tool_calls))
                continue
            for index, call in enumerate(result.tool_calls):
                self._check_active(run, session)
                if final_reason:
                    break
                if usage.tool_calls >= limits.tool_calls:
                    final_reason = "tool_call_limit"
                    break
                try:
                    key = self._model.executor.call_key(call)
                except ToolError:
                    key = None  # 无效参数仍交唯一执行入口返回错误，计入次数。
                if key is not None and key in seen_calls:
                    final_reason = "repeated_call"
                    messages[start + index] = self._denied(call, "REPEATED_CALL").to_message()
                    run.emit(
                        "tool_rejected", call_id=call.id, tool=call.name, reason="repeated_call"
                    )
                    break
                if key is not None:
                    seen_calls.add(key)
                usage.tool_calls += 1
                run.emit(
                    "tool_started",
                    call_id=call.id,
                    tool=call.name,
                    arguments_summary=call.arguments[:256],
                )
                result_tool = await self._model.executor.execute(call, session)
                run.tool_results.append(result_tool)
                run.emit(
                    "tool_finished",
                    call_id=call.id,
                    tool=call.name,
                    status=result_tool.status,
                    source_count=len(result_tool.sources),
                    errors=[error.code for error in result_tool.errors],
                    error_summaries=[{"code": error.code, "message": error.message}
                                     for error in result_tool.errors],
                )
                self._check_active(run, session)
                if result_tool.errors or result_tool.status in ("error", "partial"):
                    code = result_tool.errors[0].code if result_tool.errors else "TOOL_ERROR"
                    run.finish(
                        "timeout" if code == "TOOL_TIMEOUT" else "tool_failed",
                        "tool_timeout" if code == "TOOL_TIMEOUT" else "tool_error",
                        code,
                    )
                    return
                bounded = self._fit_result(
                    result_tool,
                    run,
                    pending_sources,
                    messages,
                    start + index,
                    session,
                )
                if bounded is None:
                    messages[start + index] = self._denied(
                        call, "INPUT_BUDGET_EXCEEDED"
                    ).to_message()
                    final_reason = "input_budget"
                    break
                messages[start + index] = bounded.to_message()
                pending_sources.extend(bounded.sources)
                run.emit(
                    "materials_prepared",
                    call_id=call.id,
                    citation_ids=[source.citation_id for source in bounded.sources],
                    truncated=any(source.truncated for source in bounded.sources),
                )

    def _fit_result(
        self,
        result: ToolResult,
        run: AnswerRun,
        pending: list[AnswerSource | WebSource],
        messages: list[dict],
        index: int,
        session: ToolSession,
    ) -> ToolResult | None:
        # 同一内容复用已送模资料；内容或时间不同则保留独立快照，避免覆盖冲突。
        registry = [*run.sources, *pending]
        sources = []
        for original in result.sources:
            source = replace(original, citation_id=len(registry) + len(sources) + 1)
            if any(
                replace(existing, citation_id=0) == replace(source, citation_id=0)
                for existing in [*registry, *sources]
            ):
                continue
            sources.append(source)
        bounded = replace(result, sources=list(sources))

        def fits() -> bool:
            trial = list(messages)
            trial[index] = bounded.to_message()
            return (
                self._model.estimate([*trial, _FINAL_CONTROL], session) <= self._budget.input_limit
            )

        while not fits():
            if not bounded.sources:
                return None
            source = bounded.sources[-1]
            if len(source.content) <= 128:
                bounded.sources.pop()
            else:
                bounded.sources[-1] = clip_source(source, len(source.content) // 2)
        if sources and not bounded.sources:
            return None
        return bounded

    def _account(
        self,
        run: AnswerRun,
        result: ChatResult,
        estimate: int,
        output_tokens: int,
    ) -> None:
        usage = run.usage
        usage.prompt_tokens += result.prompt_tokens
        usage.completion_tokens += result.completion_tokens
        usage.charged_tokens += (
            max(estimate, result.prompt_tokens)
            + result.completion_tokens
            - estimate
            - output_tokens
        )
        usage.unreported_model_calls -= 1
        run.model = result.model
        run.emit(
            "model_finished",
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            finish_reason=result.finish_reason,
        )
        if (
            result.prompt_tokens > self._budget.input_limit
            or result.completion_tokens > output_tokens
            or usage.charged_tokens > run.limits.total_tokens
        ):
            run.finish("budget_exhausted", "actual_usage_exceeded", "MODEL_USAGE_EXCEEDED")
        elif result.finish_reason == "length":
            run.finish("budget_exhausted", "output_limit", "INCOMPLETE_MODEL_RESPONSE")
        elif result.finish_reason not in ("stop", "tool_calls"):
            run.finish("model_failed", "incomplete_response", "INCOMPLETE_MODEL_RESPONSE")

    @staticmethod
    def _denied(call: ToolCall, code: str) -> ToolResult:
        return ToolResult(
            call.id, call.name, "error", errors=[ToolFailure(code, "程序已停止新增工具动作")]
        )

    @staticmethod
    def _publish(run: AnswerRun, result: ChatResult, final_reason: str) -> None:
        try:
            value = json.loads(result.content)
            if (
                not isinstance(value, dict)
                or set(value) != {"status", "answer"}
                or value["status"] not in ("answered", "insufficient")
                or not isinstance(value["answer"], str)
                or not value["answer"].strip()
            ):
                raise ValueError("invalid final response")
            answer = value["answer"].strip()
            raw = re.findall(r"\[来源([^\]]*)\]", answer)
            if any(not re.fullmatch(r"\s*[1-9]\d*\s*", item) for item in raw):
                raise ValueError("invalid citation syntax")
            citations = [int(item.strip()) for item in raw]
            allowed = {source.citation_id for source in run.sources}
            if (
                any(number not in allowed for number in citations)
                or (value["status"] == "answered" and not citations)
                or answer.count("[来源") != len(raw)
            ):
                raise ValueError("invalid citations")
        except (ValueError, TypeError, KeyError):
            run.finish("invalid_answer", "invalid_citation_or_format", "INVALID_ANSWER")
            return
        run.answer = answer
        run.emit(
            "answer_validated",
            citation_ids=sorted(set(citations)),
            outcome=value["status"],
            semantic_support="not_evaluated",
        )
        if final_reason:
            run.finish(
                "repeated_call" if final_reason == "repeated_call" else "budget_exhausted",
                final_reason,
            )
        else:
            run.finish(
                "completed" if value["status"] == "answered" else "insufficient",
                "answer_completed" if value["status"] == "answered" else "insufficient_material",
            )

    async def query_stream(self, *args, **kwargs) -> AsyncIterator[RAGStreamEvent]:
        run = await self.query(*args, **kwargs)
        yield RAGStreamEvent(
            event_type=RAG_EVENT_SOURCES,
            sources=run.sources,
            retrieval_results=retrieval_results(run),
            input_measurement=run.input_measurement,
            estimated_input_tokens=run.usage.estimated_input_tokens,
        )
        if run.answer and run.status in ("completed", "insufficient"):
            yield RAGStreamEvent(
                event_type=RAG_EVENT_CHUNK,
                chunk=ChatChunk(run.answer, run.model, "stop"),
                prompt_tokens=run.usage.prompt_tokens,
                completion_tokens=run.usage.completion_tokens,
            )
        yield RAGStreamEvent(event_type=RAG_EVENT_TERMINAL, run=run)
