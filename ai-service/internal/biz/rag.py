"""有预算的后端问答循环；同步和 SSE 使用同一次执行结果。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, replace
from time import monotonic
from typing import TYPE_CHECKING

import structlog

from internal.biz.answer_rendering import AnswerEvidenceError, render_answer
from internal.biz.answer_run import AnswerRun, RunEvent, RunLimits
from internal.biz.entity import ChatResult
from internal.biz.evidence import EvidenceBindingError, bind_catalog
from internal.biz.materials import (
    AnswerSource,
    InputBudget,
    WebSource,
    evidence_coverage,
    pack_sources,
    question_parts,
)
from internal.biz.tool_types import (
    ANSWER_CONTRACT_VERSION,
    AnswerPayload,
    ToolError,
    ToolFailure,
    ToolResult,
)
from internal.biz.tools import ToolScope
from internal.biz.web_sources import unseen_web_ranges
from pkg.errors.base import APIError
from pkg.errors.diagnostics import unexpected_error_details

if TYPE_CHECKING:
    from collections.abc import Callable

    from internal.biz.entity import RetrievalResult
    from internal.biz.tool_model import ToolModel
    from internal.biz.tool_types import ToolCall
    from internal.biz.tools import ToolSession
    from internal.prompt.manager import PromptManager

logger = structlog.get_logger()
_STOP_DETAILS = {
    "model_call_limit": "必须为最终回答保留最后一次模型调用，不能继续取证。",
    "tool_call_limit": "工具调用次数已达到上限，不能继续取证。",
    "input_budget": "完整模型请求的输入空间不足，不能继续取证；不代表工具调用次数已用完。",
    "token_budget": "本次剩余总额度不足以同时支付继续取证和最终回答。",
    "no_new_evidence": "连续两次操作没有取得新正文、目录或定位信息。",
    "repeated_call": "检测到重复工具动作，本次停止继续取证。",
    "evidence_complete": "已显式选定足以回答的证据。",
    "evidence_insufficient": "已显式确认所查资料不足以完整回答。",
    "web_read_failed": "某个网页正文读取失败；只能依据已成功取得的资料回答，缺少必要依据时说明无法核实，不得声称读过失败网页或证明其内容不存在。",
}
_COVERAGE_PREFIX = "本次问题证据覆盖（词面候选，不是语义验收）："
_BUDGET_STOPS = frozenset({"model_call_limit", "tool_call_limit", "input_budget", "token_budget"})
_WEB_READ_FAILURES = frozenset({
    "TOOL_INVALID_RESPONSE", "TOOL_DEPENDENCY_FAILED", "WEB_CONTENT_EMPTY",
    "WEB_UNSUPPORTED_CONTENT", "WEB_REDIRECT_FAILED", "TOOL_RESPONSE_TOO_LARGE",
})


def _coverage_control(query: str, sources: list[AnswerSource | WebSource]) -> dict:
    return {"role": "system", "content": _COVERAGE_PREFIX + json.dumps(
        evidence_coverage(query, sources), ensure_ascii=False, separators=(",", ":"),
    ) + "逐项核对正文是否足够，缺什么查什么；候选命中不等于已回答。比较题逐份文档取证，资料足够立即 finish_research。"}


def _final_control(reason: str) -> dict:
    return {
        "role": "system",
        "content": "现在只发布最终答案，不再调用工具。依据已有资料作答；资料不足时说明缺失信息。"
        "严格使用规定的 JSON 格式。程序记录的取证停止原因是 " + reason + "：" + _STOP_DETAILS[reason]
        + " 如需解释停止原因，只能使用该事实，不得改称其他上限，也不能据此声称原始资料不存在。",
    }


# 停止原因尚未确定时，按最长控制消息预留，确保实际收尾解释也能装入。
_FINAL_RESERVE_CONTROL = max(
    (_final_control(reason) for reason in _STOP_DETAILS),
    key=lambda message: len(json.dumps(message, ensure_ascii=False).encode("utf-8")),
)


@dataclass(frozen=True)
class RequestBudgetPlan:
    exploration_input: int
    final_input: int
    output_tokens: int
    input_ceiling: int
    remaining_tokens: int

    @property
    def final_reservation(self) -> int:
        return self.final_input + self.output_tokens

    def stop_reason(self, *, final: bool) -> str:
        inputs = (self.final_input,) if final else (self.exploration_input, self.final_input)
        if max(inputs) > self.input_ceiling:
            return "input_budget"
        required = self.final_reservation
        if not final:
            required += self.exploration_input + self.output_tokens
        return "token_budget" if required > self.remaining_tokens else ""


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
            session.question = query
            run.emit("evidence_needs_planned", questions=question_parts(query),
                     knowledge_base_ids=list(run.scope.knowledge_base_ids),
                     method="explicit_question_clauses")
            run.web_directory, run.web_pages = session.web.entries, session.web.pages
            if run.web_directory:
                run.emit("web_directory_updated", directory=[asdict(entry) for entry in run.web_directory])
            messages = self._prompts.get_template(self._template).build_messages(
                {
                    "query": query,
                    "context": "资料仅来自后续工具结果。",
                }
            )
            messages.append(_coverage_control(query, []))
            await self._loop(run, session, messages, output_tokens, temperature)
        except asyncio.CancelledError:
            run.finish("cancelled", "user_cancelled")
            if asyncio.current_task().cancelling():
                raise
        except EvidenceBindingError as exc:
            run.finish("tool_failed", str(exc), "EVIDENCE_BINDING_INVALID")
        except ToolError as exc:
            run.finish(
                "timeout" if exc.code == "TOOL_TIMEOUT" else "tool_failed",
                "execution_timeout" if exc.code == "TOOL_TIMEOUT" else "tool_error",
                exc.code,
            )
        except APIError as exc:
            if exc.reason.startswith("SOURCE_"):
                run.finish("tool_failed", exc.reason.lower(), exc.reason)
            elif exc.reason == "LLM_USAGE_UNKNOWN":
                run.finish("usage_unknown", "usage_unknown", exc.reason)
            elif exc.reason in ("INPUT_BUDGET_EXCEEDED", "MATERIAL_BUDGET_EXCEEDED", "OUTPUT_BUDGET_EXCEEDED"):
                reason = "output_limit" if exc.reason == "OUTPUT_BUDGET_EXCEEDED" else "input_budget"
                run.finish("budget_exhausted", reason, exc.reason)
            else:
                run.finish(
                    "timeout" if exc.reason.endswith("TIMEOUT") else "model_failed",
                    "model_timeout" if exc.reason.endswith("TIMEOUT") else "model_error",
                    exc.reason,
                )
        except Exception as exc:
            logger.error("answer_run_unexpected_error", run_id=run.run_id, **unexpected_error_details(exc))
            run.finish("internal_error", "internal_error", "RUN_INTERNAL_ERROR")
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

    def _budget_plan(
        self, messages: list[dict], session: ToolSession, run: AnswerRun, output_tokens: int,
    ) -> RequestBudgetPlan:
        return RequestBudgetPlan(
            self._model.estimate(messages, session),
            self._model.estimate(
                [*messages, _FINAL_RESERVE_CONTROL], replace(session, finished=True), final=True,
            ),
            output_tokens,
            self._budget.input_ceiling(output_tokens),
            run.limits.total_tokens - run.usage.settled_tokens,
        )

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
        seen_matches: set[tuple[str, int, int]] = set()
        pending_sources: list[AnswerSource | WebSource] = []
        no_progress = 0
        while run.status == "running":
            self._check_active(run, session)
            usage, limits = run.usage, run.limits
            plan = self._budget_plan(messages, session, run, output_tokens)
            if not run.exploration_stop_reason:
                if usage.model_calls >= limits.model_calls - 1:
                    run.stop_exploration("model_call_limit")
                elif usage.tool_calls >= limits.tool_calls:
                    run.stop_exploration("tool_call_limit")
                elif reason := plan.stop_reason(final=False):
                    run.stop_exploration(reason)
            if no_progress >= 2:
                run.stop_exploration("no_new_evidence")
            final = bool(run.exploration_stop_reason)
            if final:
                # 无论由 finish_research 还是程序限制结束取证，最终生成前都关闭工具会话。
                session.finished = True
                if not usage.tool_calls:
                    run.finish("budget_exhausted", run.exploration_stop_reason)
                    return
                # 最终请求不再携带探索工具及其历史，按最终预算重新装载已送模原文。
                # 本轮待送模的片段也参与装载，但仅在最终请求实际发出时登记。
                _, reason = self._fit_results(
                    [], run, messages, len(messages), session, output_tokens,
                    pending_sources=pending_sources,
                )
                if reason:
                    run.finish("budget_exhausted", reason)
                    return
                pending_ids = {source.citation_id for source in pending_sources}
                pending_sources = [source for source in session.active_sources
                                   if source.citation_id in pending_ids]
                plan = self._budget_plan(messages, session, run, output_tokens)
            remaining = plan.remaining_tokens
            reserve = plan.final_reservation
            usage.final_reserved_tokens = reserve
            run.emit("budget_planned", settled_tokens=usage.settled_tokens,
                     remaining_tokens=remaining, final_reservation=reserve,
                     available_for_exploration=remaining - usage.reserved_tokens - reserve,
                     input_measurement=run.input_measurement)
            request = [*messages, _final_control(run.exploration_stop_reason)] if final else messages
            estimate = self._model.estimate(request, session, final=final)
            if (
                usage.model_calls >= limits.model_calls
                or estimate > plan.input_ceiling
                or estimate + output_tokens > remaining
            ):
                reason = ("model_call_limit" if usage.model_calls >= limits.model_calls else
                          "input_budget" if estimate > plan.input_ceiling else "token_budget")
                run.finish("budget_exhausted", reason)
                return
            self._model.validate_request(request, session, output_tokens, final=final)
            catalog = []
            if final:
                await self._model.executor.validate_sources(session, session.active_sources)
                catalog = bind_catalog(request, session.active_sources, run.scope, run.run_id)
            self._check_active(run, session)
            call_number = run.reserve_model(estimate, output_tokens, final)
            if pending_sources:
                run.sources.extend(pending_sources)
                run.emit("sources_updated", citation_ids=[s.citation_id for s in run.sources])
                run.emit("evidence_coverage_updated", **evidence_coverage(session.question, run.sources))
            pending_sources = []
            session.delivered_citations = {source.citation_id: source for source in run.sources}
            if final:
                run.evidence_catalog = catalog
                run.final_request = self._model.request_messages(request, session, final=True)
                run.emit("evidence_catalog_prepared", evidence_ids=[item.evidence_id for item in catalog],
                         answer_contract_version=ANSWER_CONTRACT_VERSION)
            run.emit(
                "model_started",
                call=usage.model_calls,
                final=final,
                request_mode="strict_answer" if final else "native_tools",
                tool_definition_count=0 if final else len(self._model.executor.definitions(session.scope)),
                tool_choice=None if final else "required",
                exploration_stop_reason=run.exploration_stop_reason,
                estimated_input_tokens=estimate,
                output_limit=output_tokens,
            )
            try:
                result = await self._model.request_action(
                    request,
                    session,
                    final=final,
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
                    run.final_response = result.content
                    await self._model.executor.validate_sources(session, session.active_sources)
                    self._check_active(run, session)
                    run.emit("evidence_sources_verified", phase="before_publish", evidence_count=len(run.evidence_catalog))
                    self._publish(run, result)
                return
            if not result.tool_calls:
                run.finish("model_failed", "evidence_not_requested", "REQUIRED_TOOL_CALL_MISSING")
                return
            if len(result.tool_calls) > 1 and any(call.name == "finish_research" for call in result.tool_calls):
                run.finish("model_failed", "invalid_tool_batch", "FINISH_MUST_BE_ALONE")
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
            # 新动作及其占位结果也占空间；执行前用同一装载器重新分配历史原文，保留收尾预算。
            prepared_history, reason = self._fit_results([], run, messages, start, session, output_tokens)
            if prepared_history is None:
                # 尚未执行任何动作，无需把无法容纳的请求加入后续原生协议。
                # 保留上一份完整上下文和预留额度，用最后一轮回答明确结束。
                del messages[before_action:]
                run.stop_exploration(reason)
                run.emit("tool_rejected", reason=reason, count=len(result.tool_calls))
                continue
            batch_results: list[ToolResult] = []
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
                    call_key=key,
                )
                result_tool = await self._model.executor.execute(call, session)
                run.tool_results.append(result_tool)
                if call.name in ("web_search", "web_read"):
                    run.emit("web_directory_updated", directory=[asdict(entry) for entry in run.web_directory])
                run.emit(
                    "tool_finished",
                    call_id=call.id,
                    tool=call.name,
                    status=result_tool.status,
                    source_count=len(result_tool.sources),
                    errors=[error.code for error in result_tool.errors],
                    error_summaries=[asdict(error) for error in result_tool.errors],
                    web_diagnostics=[asdict(item) for item in result_tool.diagnostics],
                    directory=[asdict(entry) for entry in result_tool.directory],
                    matches=result_tool.matches,
                    selection=result_tool.selection,
                )
                self._check_active(run, session)
                if result_tool.errors or result_tool.status in ("error", "partial"):
                    code = result_tool.errors[0].code if result_tool.errors else "TOOL_ERROR"
                    window_limited = call.name == "web_read" and code in (
                        "WEB_CODE_BLOCK_TOO_LARGE", "TOOL_RESULT_TOO_LARGE",
                    )
                    read_failed = (call.name == "web_read" and bool(result_tool.errors)
                                   and all(error.code in _WEB_READ_FAILURES for error in result_tool.errors))
                    if read_failed:
                        # 保留本次错误及已取得资料，进入已预留的最终生成，不重试网页。
                        run.stop_exploration("web_read_failed")
                    elif not window_limited:
                        run.finish(
                            "timeout" if code == "TOOL_TIMEOUT" else "tool_failed",
                            "tool_timeout" if code == "TOOL_TIMEOUT" else "tool_error",
                            code,
                        )
                        return
                if result_tool.selection is not None:
                    run.evidence_selection = result_tool.selection
                    run.stop_exploration("evidence_complete" if result_tool.selection["outcome"] == "answered" else "evidence_insufficient")
                batch_results.append(result_tool)
            bounded_results, reason = self._fit_results(
                batch_results, run, messages, start, session, output_tokens,
            )
            if bounded_results is None:
                # 保留已经预留过的未送模占位结果，不增加错误说明挤掉收尾空间。
                run.stop_exploration(reason)
                run.emit("materials_rejected", call_ids=[item.call_id for item in batch_results], reason=reason)
                continue
            for index, bounded in enumerate(bounded_results):
                if bounded.errors:
                    run.emit("materials_limited", call_id=bounded.call_id,
                             errors=[error.code for error in bounded.errors])
                new_matches = {
                    (match["source_id"], match["start"], match["end"])
                    for match in bounded.matches
                } - seen_matches
                seen_matches.update(new_matches)
                no_progress = 0 if bounded.sources or bounded.directory or new_matches else no_progress + 1
                messages[start + index] = bounded.to_message()
                pending_sources.extend(bounded.sources)
                run.emit(
                    "materials_prepared",
                    call_id=bounded.call_id,
                    citation_ids=[source.citation_id for source in bounded.sources],
                    truncated=any(source.truncated for source in bounded.sources),
                )
            if no_progress >= 2:
                run.stop_exploration("no_new_evidence")

    @staticmethod
    def _new_sources(
        result: ToolResult, registry: list[AnswerSource | WebSource],
    ) -> list[AnswerSource | WebSource]:
        # 只向后续模型发送尚未见过的原文范围；重排分变化不构成新证据。
        next_id = max((source.citation_id for source in registry), default=0) + 1
        sources = []
        for original in result.sources:
            source = replace(original)
            if isinstance(source, AnswerSource):
                segments = []
                for segment in source.segments:
                    portions = [segment]
                    for existing in [*registry, *sources]:
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
                    segments.extend(replace(part, code_ranges=[(a, b) for a, b in part.code_ranges
                                                               if part.start <= a < b <= part.end])
                                    for part in portions)
                if not segments:
                    continue
                ids = {part.chunk_id for part in segments}
                source = replace(source, segments=segments,
                                 retrieval_hits=[hit for hit in source.retrieval_hits if hit.chunk_id in ids])
            else:
                for part in unseen_web_ranges(source, [*registry, *sources]):
                    sources.append(replace(part, citation_id=next_id + len(sources)))
                continue
            if not source.content.strip():
                continue
            sources.append(replace(source, citation_id=next_id + len(sources)))
        return sources

    def _fit_results(
        self, results: list[ToolResult], run: AnswerRun, messages: list[dict], start: int,
        session: ToolSession, output_tokens: int, *,
        pending_sources: list[AnswerSource | WebSource] | None = None,
    ) -> tuple[list[ToolResult] | None, str]:
        """历史和新结果统一抽取；已送模登记保持不变，每轮压缩范围单独留档。"""
        bounded = [replace(result, sources=[]) for result in results]
        candidates: list[AnswerSource | WebSource] = []
        owners: dict[int, tuple[str, int]] = {}
        previous: dict[int, dict] = {}
        registry = {source.citation_id: source for source in [*run.sources, *(pending_sources or [])]}
        supplied = {event.data["call_id"]: event.data["citation_ids"]
                    for event in run.events if event.kind == "materials_prepared"}
        for index, message in enumerate(messages[:start]):
            if message["role"] != "tool":
                continue
            payload = json.loads(message["content"])
            previous[index] = payload
            for citation_id in supplied.get(message["tool_call_id"], []):
                if citation_id not in registry:
                    continue  # 仅允许已送模或即将随本次最终请求送模的片段。
                source = registry[citation_id]
                candidates.append(source)
                owners[source.citation_id] = ("history", index)
        for index, result in enumerate(results):
            sources = self._new_sources(result, [*run.sources, *candidates])
            owners.update((source.citation_id, ("new", index)) for source in sources)
            candidates.extend(sources)
        if run.evidence_selection is not None:
            selected = set(run.evidence_selection["citation_ids"])
            candidates = [source for source in candidates if source.citation_id in selected]
        final_only = (bool(run.exploration_stop_reason)
                      or run.usage.model_calls >= run.limits.model_calls - 1
                      or run.usage.tool_calls >= run.limits.tool_calls)

        def project(values):
            grouped: dict[tuple[str, int], list] = {}
            for source in values:
                grouped.setdefault(owners[source.citation_id], []).append(source)
            projected = [replace(item, sources=grouped.get(("new", i), []))
                         for i, item in enumerate(bounded)]
            trial = list(messages)
            for i, payload in previous.items():
                kept = grouped.get(("history", i), [])
                value = {**payload, "sources": ToolResult("", "", "success", sources=kept).source_values(),
                         "context_compacted": True}
                trial[i] = {**messages[i], "content": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}
            for i, item in enumerate(projected):
                trial[start + i] = item.to_message()
            for i, message in enumerate(trial):
                if message["role"] == "system" and message["content"].startswith(_COVERAGE_PREFIX):
                    trial[i] = _coverage_control(session.question, values)
            return projected, trial

        def fits(values):
            _, trial = project(values)
            return not self._budget_plan(trial, session, run, output_tokens).stop_reason(final=final_only)

        limited: set[int] = set()
        while True:
            _, empty = project([])
            empty_plan = self._budget_plan(empty, session, run, output_tokens)
            reason = empty_plan.stop_reason(final=final_only)
            if reason and not final_only and not empty_plan.stop_reason(final=True):
                run.stop_exploration(reason)
                final_only = True
                reason = ""
            if reason:
                return None, reason
            packed = pack_sources(candidates, fits, query=session.question)
            present = {owners[source.citation_id][1] for source in packed
                       if owners[source.citation_id][0] == "new"}
            missing = {index for kind, index in owners.values() if kind == "new"} - limited - present
            if not missing:
                break
            for index in missing:
                if not bounded[index].directory and not bounded[index].matches:
                    bounded[index] = replace(bounded[index], status="error", errors=[ToolFailure(
                        "MATERIAL_WINDOW_TOO_LARGE",
                        "该正文窗口无法在完整请求预算内保留可用原文；此窗口未送模。请定位读取更相关的小段，不能引用未读取的内容或宣称原文不存在。",
                    )])
            limited.update(missing)
            candidates = [source for source in candidates
                          if owners[source.citation_id][0] != "new" or owners[source.citation_id][1] not in limited]
        projected, trial = project(packed)
        plan = self._budget_plan(trial, session, run, output_tokens)
        if reason := plan.stop_reason(final=final_only):
            return None, reason
        # 只更新上下文投影，不改写历史 tool_results 或已送模原文登记。
        messages[:start] = trial[:start]
        session.active_sources = packed
        run.usage.final_reserved_tokens = plan.final_reservation
        run.emit("materials_budgeted", strategy="question_relevant_verbatim",
                 candidate_chars=sum(len(source.content) for source in candidates),
                 retained_chars=sum(len(source.content) for source in packed),
                 retained_citation_ids=[source.citation_id for source in packed],
                 active_ranges=[{
                     "citation_id": source.citation_id,
                     "segments": [{"chunk_id": part.chunk_id, "start": part.start, "end": part.end}
                                  for part in source.segments],
                 } if isinstance(source, AnswerSource) else {
                     "citation_id": source.citation_id, "start": source.start, "end": source.end,
                 } for source in packed],
                 remaining_tokens=plan.remaining_tokens,
                 exploration_input=plan.exploration_input, final_input=plan.final_input,
                 final_reservation=run.usage.final_reserved_tokens,
                 available_for_exploration=plan.remaining_tokens - run.usage.final_reserved_tokens)
        return projected, ""

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
            or result.prompt_tokens + result.completion_tokens > self._budget.context_capacity
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
            allowed = {source.citation_id for source in run.sources}
            if run.evidence_selection is not None:
                if value.status != run.evidence_selection["outcome"]:
                    run.finish("invalid_answer", "evidence_selection_mismatch", "EVIDENCE_SELECTION_MISMATCH")
                    return
                allowed &= set(run.evidence_selection["citation_ids"])
            rendered = render_answer(value, {item.evidence_id: item for item in run.evidence_catalog
                                             if item.citation_id in allowed and item.run_id == run.run_id})
        except AnswerEvidenceError as exc:
            run.finish("invalid_answer", exc.reason, exc.reason.upper())
            return
        except (ValueError, TypeError, KeyError):
            run.finish("invalid_answer", "invalid_citation_or_format", "INVALID_ANSWER")
            return
        run.answer = rendered.answer
        if any(result.name == "web_read" and any(error.code in _WEB_READ_FAILURES for error in result.errors)
               for result in run.tool_results):
            run.answer += "\n\n部分网页正文读取失败，本次回答仅依据已成功取得的资料；未读到的网页内容无法核实。"
        run.answer_claims = rendered.claims
        run.emit(
            "answer_validated",
            citation_ids=rendered.citation_ids,
            outcome=value.status,
            answer_contract_version=ANSWER_CONTRACT_VERSION,
            claim_count=len(rendered.claims),
            quote_binding="program_owned_final_evidence_slices",
            semantic_support="not_evaluated",
        )
        if value.status == "insufficient" and run.exploration_stop_reason in _BUDGET_STOPS:
            run.finish("budget_exhausted", run.exploration_stop_reason)
        else:
            run.finish(
                "completed" if value.status == "answered" else "insufficient",
                "answer_completed" if value.status == "answered" else "insufficient_material",
            )
