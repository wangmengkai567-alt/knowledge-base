"""有预算的后端问答循环；同步和 SSE 使用同一次执行结果。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, replace
from time import monotonic
from typing import TYPE_CHECKING

import structlog

from internal.biz.answer_run import AnswerRun, RunEvent, RunLimits
from internal.biz.entity import ChatResult
from internal.biz.materials import AnswerSource, InputBudget, WebSource, pack_sources
from internal.biz.tool_types import AnswerPayload, ToolError, ToolFailure, ToolResult
from internal.biz.tools import ToolScope
from pkg.errors.base import APIError

if TYPE_CHECKING:
    from collections.abc import Callable

    from internal.biz.entity import RetrievalResult
    from internal.biz.tool_model import ToolModel
    from internal.biz.tool_types import ToolCall
    from internal.biz.tools import ToolSession
    from internal.prompt.manager import PromptManager

logger = structlog.get_logger()
_FINAL_CONTROL = {
    "role": "system",
    "content": "现在只发布最终答案，不再调用工具。依据已有资料作答；资料不足时说明缺失信息。严格使用规定的 JSON 格式。",
}


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
        on_event: Callable[[AnswerRun, RunEvent], None] | None = None,
    ) -> AnswerRun:
        run = AnswerRun(
            ToolScope(user_id, tuple(dict.fromkeys(knowledge_base_ids)), allow_web),
            self._limits,
            self._budget.measurement,
            on_event=on_event,
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
            if exc.reason == "LLM_USAGE_UNKNOWN":
                run.finish("usage_unknown", "usage_unknown", exc.reason)
            elif "BUDGET" in exc.reason:
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
                settled_tokens=run.usage.settled_tokens,
                actual_total_tokens=run.usage.actual_total_tokens,
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
        no_progress = 0
        while run.status == "running":
            self._check_active(run, session)
            usage, limits = run.usage, run.limits
            estimate = self._model.estimate([*messages, _FINAL_CONTROL], session, final=True)
            remaining = limits.total_tokens - usage.settled_tokens
            # 收尾使用当前完整请求的保守估算，不固定扣留最大输入窗口。
            reserve = estimate + output_tokens
            usage.final_reserved_tokens = reserve
            if not run.exploration_stop_reason:
                if usage.model_calls >= limits.model_calls - 1:
                    run.stop_exploration("model_call_limit")
                elif usage.tool_calls >= limits.tool_calls:
                    run.stop_exploration("tool_call_limit")
                elif self._model.estimate(messages, session) > self._budget.input_limit:
                    run.stop_exploration("input_budget")
                elif remaining < self._model.estimate(messages, session) + output_tokens + reserve:
                    run.stop_exploration("token_budget")
            if no_progress >= 2:
                run.stop_exploration("no_new_evidence")
            final = bool(run.exploration_stop_reason)
            if final and not usage.tool_calls:
                run.finish("budget_exhausted", run.exploration_stop_reason)
                return
            run.emit("budget_planned", settled_tokens=usage.settled_tokens,
                     remaining_tokens=remaining, final_reservation=reserve,
                     available_for_exploration=remaining - usage.reserved_tokens - reserve,
                     input_measurement=run.input_measurement)
            request = [*messages, _FINAL_CONTROL] if final else messages
            estimate = self._model.estimate(request, session, final=final)
            if (
                usage.model_calls >= limits.model_calls
                or estimate > self._budget.input_limit
                or estimate + output_tokens > remaining
            ):
                reason = ("model_call_limit" if usage.model_calls >= limits.model_calls else
                          "input_budget" if estimate > self._budget.input_limit else "token_budget")
                run.finish("budget_exhausted", reason)
                return
            self._model.validate_request(request, session, output_tokens, final=final)
            self._check_active(run, session)
            call_number = run.reserve_model(estimate, output_tokens, final)
            if pending_sources:
                run.sources.extend(pending_sources)
                run.emit("sources_updated", citation_ids=[s.citation_id for s in run.sources])
            pending_sources = []
            run.emit(
                "model_started",
                call=usage.model_calls,
                final=final,
                request_mode="strict_answer" if final else "native_tools",
                tool_definition_count=0 if final else len(self._model.executor.definitions(session.scope)),
                tool_choice=None if final else "required" if not usage.tool_calls else "auto",
                exploration_stop_reason=run.exploration_stop_reason,
                estimated_input_tokens=estimate,
                output_limit=output_tokens,
            )
            try:
                result = await self._model.request_action(
                    request,
                    session,
                    final=final,
                    require_tool=not usage.tool_calls,
                    max_tokens=output_tokens,
                    temperature=temperature,
                )
            except BaseException as exc:
                reported = exc.metadata.get("reported_usage") if isinstance(exc, APIError) else None
                if reported is not None:
                    self._account(run, ChatResult("", self._budget.model, **reported), call_number, check_response=False)
                else:
                    run.unknown_model(call_number, getattr(exc, "reason", type(exc).__name__))
                raise
            self._account(run, result, call_number)
            self._check_active(run, session)
            if run.status != "running":
                return
            if final:
                if result.tool_calls:
                    run.finish("model_failed", "unexpected_tool_call", "INVALID_TOOL_RESPONSE")
                else:
                    self._publish(run, result)
                return
            if not result.tool_calls:
                if not usage.tool_calls:
                    run.finish("model_failed", "evidence_not_requested", "REQUIRED_TOOL_CALL_MISSING")
                    return
                # 探索正文不发布、不作为事实或修复素材；下一轮始终是预留的正式最终生成。
                run.stop_exploration("model_finished_exploration")
                continue
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
            final_estimate = self._model.estimate([*messages, _FINAL_CONTROL], session, final=True)
            if final_estimate > self._budget.input_limit or final_estimate + output_tokens > limits.total_tokens - usage.settled_tokens:
                # 尚未执行任何动作，无需把无法容纳的请求加入后续原生协议。
                # 保留上一份完整上下文和预留额度，用最后一轮回答明确结束。
                del messages[before_action:]
                reason = "input_budget" if final_estimate > self._budget.input_limit else "token_budget"
                run.stop_exploration(reason)
                run.emit("tool_rejected", reason=reason, count=len(result.tool_calls))
                continue
            for index, call in enumerate(result.tool_calls):
                self._check_active(run, session)
                if run.exploration_stop_reason:
                    break
                if usage.tool_calls >= limits.tool_calls:
                    run.stop_exploration("tool_call_limit")
                    break
                try:
                    key = self._model.executor.call_key(call)
                except ToolError:
                    key = None  # 无效参数仍交唯一执行入口返回错误，计入次数。
                if key is not None and key in seen_calls:
                    run.stop_exploration("repeated_call")
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
                    error_summaries=[asdict(error) for error in result_tool.errors],
                    web_diagnostics=[asdict(item) for item in result_tool.diagnostics],
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
                    output_tokens,
                )
                if bounded is None:
                    messages[start + index] = self._denied(
                        call, "INPUT_BUDGET_EXCEEDED"
                    ).to_message()
                    run.stop_exploration("input_budget")
                    break
                no_progress = 0 if bounded.sources else no_progress + 1
                if no_progress >= 2:
                    run.stop_exploration("no_new_evidence")
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
        output_tokens: int,
    ) -> ToolResult | None:
        # 只向后续模型发送尚未见过的原文范围；重排分变化不构成新证据。
        registry = [*run.sources, *pending]
        next_id = max((source.citation_id for source in registry), default=0) + 1
        sources = []
        for original in result.sources:
            source = replace(original)
            if isinstance(source, AnswerSource):
                segments = []
                for segment in source.segments:
                    portions = [segment]
                    for existing in registry:
                        if not isinstance(existing, AnswerSource) or (
                            existing.document_id, existing.document_updated_at
                        ) != (source.document_id, source.document_updated_at):
                            continue
                        for old in existing.segments:
                            if old.chunk_id != segment.chunk_id:
                                continue
                            remaining = []
                            for part in portions:
                                start, end = max(part.start, old.start), min(part.end, old.end)
                                if start >= end or part.content[start-part.start:end-part.start] != old.content[start-old.start:end-old.start]:
                                    remaining.append(part)
                                    continue
                                if part.start < start:
                                    remaining.append(replace(part, end=start, content=part.content[:start-part.start]))
                                if end < part.end:
                                    remaining.append(replace(part, start=end, content=part.content[end-part.start:]))
                            portions = remaining
                    segments.extend(portions)
                if not segments:
                    continue
                ids = {part.chunk_id for part in segments}
                source = replace(source, segments=segments,
                                 retrieval_hits=[hit for hit in source.retrieval_hits if hit.chunk_id in ids])
            elif any(isinstance(existing, WebSource) and replace(existing, citation_id=0) == replace(source, citation_id=0)
                     for existing in registry):
                continue
            sources.append(replace(source, citation_id=next_id + len(sources)))
        bounded = replace(result)
        empty = list(messages)
        empty[index] = replace(bounded, sources=[]).to_message()
        base = self._model.estimate([*empty, _FINAL_CONTROL], session, final=True)
        remaining = run.limits.total_tokens - run.usage.settled_tokens
        ceiling = min(self._budget.input_limit - self._budget.output_limit, remaining - output_tokens)
        if base > ceiling:
            return None
        # 当前资料只使用剩余输入空间的一半，为缺失子问题的新证据保留空间。
        limit = base + max(0, ceiling - base) // 2
        def fits(values):
            trial = list(messages)
            trial[index] = replace(bounded, sources=values).to_message()
            return self._model.estimate([*trial, _FINAL_CONTROL], session, final=True) <= limit
        bounded.sources = pack_sources(sources, fits)
        if not fits(bounded.sources) or (sources and not bounded.sources):
            return None
        trial = list(messages)
        trial[index] = bounded.to_message()
        run.usage.final_reserved_tokens = self._model.estimate([*trial, _FINAL_CONTROL], session, final=True) + output_tokens
        run.emit("materials_budgeted", remaining_tokens=remaining,
                 final_reservation=run.usage.final_reserved_tokens,
                 available_for_exploration=remaining - run.usage.final_reserved_tokens)
        return bounded

    def _account(
        self,
        run: AnswerRun,
        result: ChatResult,
        call: int,
        *,
        check_response: bool = True,
    ) -> None:
        usage = run.usage
        entry = usage.calls[call - 1]
        if not run.settle_model(call, result.prompt_tokens, result.completion_tokens, result.total_tokens):
            return
        run.model = result.model
        run.emit(
            "model_finished",
            call=call,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            provider_total_tokens=result.total_tokens,
            settled_tokens=usage.settled_tokens,
            released_reservation=entry.reservation,
            finish_reason=result.finish_reason,
        )
        if (
            result.prompt_tokens > self._budget.input_limit
            or result.completion_tokens > entry.output_limit
            or usage.settled_tokens > run.limits.total_tokens
        ):
            run.finish("budget_exhausted", "actual_usage_exceeded", "MODEL_USAGE_EXCEEDED")
        elif result.total_tokens != result.prompt_tokens + result.completion_tokens:
            run.finish("model_failed", "usage_inconsistent", "MODEL_USAGE_INCONSISTENT")
        elif not check_response:
            return
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
    def _publish(run: AnswerRun, result: ChatResult) -> None:
        run.final_response = result.content
        run.emit("answer_check_started")
        try:
            value = AnswerPayload.model_validate_json(result.content)
            answer = value.answer.strip()
            if not answer:
                raise ValueError("empty final answer")
            raw = re.findall(r"\[来源([^\]]*)\]", answer)
            if any(not re.fullmatch(r"\s*[1-9]\d*\s*", item) for item in raw):
                raise ValueError("invalid citation syntax")
            citations = [int(item.strip()) for item in raw]
            allowed = {source.citation_id for source in run.sources}
            if (
                any(number not in allowed for number in citations)
                or (value.status == "answered" and not citations)
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
            outcome=value.status,
            semantic_support="not_evaluated",
        )
        run.finish(
            "completed" if value.status == "answered" else "insufficient",
            "answer_completed" if value.status == "answered" else "insufficient_material",
        )
