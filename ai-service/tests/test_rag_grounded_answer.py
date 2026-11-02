"""Run the real bounded loop and ToolModel with deterministic tool/model responses."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.answer_run import RunLimits
from internal.biz.entity import ChatResult, Chunk, RetrievalResult
from internal.biz.materials import AnswerSource, InputBudget, MaterialSegment, WebSource
from internal.biz.rag import RAGBiz
from internal.biz.tool_model import ToolModel
from internal.biz.tool_types import ToolCall, ToolFailure, ToolResult
from internal.biz.tools import ReadOnlyToolExecutor, ToolScope, ToolSession
from internal.data.answer_runs import LocalAnswerRunStore
from internal.data.chunker.recursive import RecursiveCharacterChunker
from internal.prompt.base import PromptTemplate
from internal.service.rag import RAGService
from pkg.errors.base import LLMError


class Prompts:
    def get_template(self, name):
        return PromptTemplate(name, "JSON answer using sources", "{{ query }}", ["query"])


def final(answer="资料不足", status="insufficient"):
    return ChatResult(content=json.dumps({"status": status, "answer": answer}, ensure_ascii=False),
                      model="qwen3.8-flash", prompt_tokens=20, completion_tokens=10,
                      total_tokens=30, finish_reason="stop")


def action(call_id="c1", query="topic"):
    return ChatResult(content="", model="qwen3.8-flash", prompt_tokens=20, completion_tokens=10,
                      total_tokens=30, tool_calls=[ToolCall(call_id, "knowledge_base_search", json.dumps({"query": query}))],
                      finish_reason="tool_calls")


class LLM:
    supports_native_tools = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.waiting = asyncio.Event()
        self.closed = False

    async def generate_with_tools(self, messages, tools, **kwargs):
        self.calls.append((messages, kwargs))
        value = next(self.responses)
        if value == "wait":
            self.waiting.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.closed = True
        if isinstance(value, Exception):
            raise value
        return value


class Executor:
    call_key = staticmethod(ReadOnlyToolExecutor.call_key)

    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    async def create_session(self, user_id, knowledge_base_ids, allow_web, user_urls, **kwargs):
        return ToolSession(ToolScope(user_id, tuple(knowledge_base_ids), allow_web),
                           **{key: value for key, value in kwargs.items() if value is not None})

    def definitions(self, scope):
        return [{"type": "function", "function": {"name": "knowledge_base_search", "parameters": {"type": "object"}}}]

    async def execute(self, call, session):
        self.calls.append(call)
        return next(self.results)


def setup(responses, results=(), limits=None, timeout=30):
    llm, executor = LLM(responses), Executor(results)
    budget = InputBudget("qwen3.8-flash")
    biz = RAGBiz(ToolModel(executor, llm, budget, model_timeout=timeout), Prompts(), budget, limits=limits)
    return biz, llm, executor


def source():
    return WebSource(0, "https://example.com/", "runtime", "2026-10-29T00:00:00Z",
                     "组件先加载配置，再连接存储。", "web_page")


def finish_action(outcome, citation_ids, call_id="done"):
    return ChatResult(
        "", "qwen3.8-flash", prompt_tokens=20, completion_tokens=10, total_tokens=30,
        tool_calls=[ToolCall(call_id, "finish_research", json.dumps(
            {"outcome": outcome, "citation_ids": citation_ids},
        ))], finish_reason="tool_calls",
    )


def grounded_answer(status, claims=(), missing=()):
    return ChatResult(
        json.dumps({"status": status, "claims": list(claims), "missing": list(missing)}, ensure_ascii=False),
        "qwen3.8-flash", prompt_tokens=20, completion_tokens=10, total_tokens=30, finish_reason="stop",
    )


def research_setup(actions, answer, *, contents=("启动顺序：先加载配置，再连接存储。",), limits=None):
    budget = InputBudget("qwen3.8-flash")
    chunker = RecursiveCharacterChunker(256, 32)
    documents, chunks_by_document, hits = {}, {}, []
    for number, content in enumerate(contents, 1):
        version = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(hours=number)
        documents[number] = SimpleNamespace(
            id=number, knowledge_base_id=1, parsed_content=content, updated_at=version, created_at=version,
            stages={name: {"status": "completed"} for name in ("parse", "chunk", "vector", "keyword")},
        )
        chunks = [Chunk(number * 100 + pos, number, pos, text)
                  for pos, text in enumerate(chunker.chunk(content))]
        chunks_by_document[number] = chunks
        hits.extend(RetrievalResult(
            chunk.id, 0.9, chunk.content, chunk.position, number, f"startup-v{number}.md", 1, version,
            score_type="rerank", score_method="fixed",
        ) for chunk in chunks)
    retrieval = SimpleNamespace(retrieve_many=AsyncMock(return_value=hits))
    executor = ReadOnlyToolExecutor(
        retrieval, SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(owner_id=1))),
        SimpleNamespace(list_by_document=AsyncMock(side_effect=chunks_by_document.__getitem__)),
        SimpleNamespace(), budget,
        document_repo=SimpleNamespace(get_by_id=AsyncMock(side_effect=documents.get)), chunker=chunker,
    )
    sessions = []
    create_session = executor.create_session

    async def record_session(*args, **kwargs):
        session = await create_session(*args, **kwargs)
        sessions.append(session)
        return session

    executor.create_session = AsyncMock(side_effect=record_session)
    executor.execute = AsyncMock(wraps=executor.execute)
    llm = LLM(actions)

    def generate_answer(messages, **kwargs):
        assert sessions[0].finished, "最终生成开始前必须关闭取证工具会话"
        return answer

    llm.generate_answer = AsyncMock(side_effect=generate_answer)
    biz = RAGBiz(ToolModel(executor, llm, budget), Prompts(), budget, limits=limits)
    return biz, llm, executor, retrieval, sessions


class BoundedRunTest(unittest.IsolatedAsyncioTestCase):
    async def query(self, biz, **kwargs):
        return await biz.query(query="启动顺序？", knowledge_base_ids=[1], user_id=1, **kwargs)

    async def test_tool_selection_uses_supported_auto_mode(self):
        biz, llm, _ = setup([action(), final()], [ToolResult("c1", "knowledge_base_search", "empty")])
        await self.query(biz)
        self.assertEqual(llm.calls[0][1]["tool_choice"], "auto")
        self.assertEqual(sum(m["role"] == "system" for m in llm.calls[0][0]), 1)
        self.assertEqual(llm.calls[1][1]["tool_choice"], "auto")

    def test_kb_tool_context_omits_internal_index_metadata(self):
        material = AnswerSource(1, 3, "note.md", 1, [MaterialSegment(11, 0, 0, 5, "abcde")], [])
        payload = json.loads(ToolResult("c1", "knowledge_base_search", "success", [material]).to_message()["content"])
        self.assertEqual(payload["sources"][0]["content"], material.content)
        self.assertNotIn("segments", payload["sources"][0])
        self.assertNotIn("retrieval_hits", payload["sources"][0])

    async def test_model_receives_explicit_current_web_permission(self):
        biz, llm, _ = setup([final()])
        await self.query(biz)
        self.assertIn("本次禁止联网", llm.calls[0][0][0]["content"])

    async def test_nonempty_material_can_still_be_insufficient_without_forced_retry(self):
        biz, llm, executor, retrieval, _ = research_setup(
            [action(query="启动顺序"), finish_action("insufficient", [1])],
            grounded_answer("insufficient", claims=[{
                "scope": "source", "text": "启动时先加载配置。", "evidence_ids": ["E1"], "code_evidence_id": None,
            }], missing=["加载配置后的启动步骤"]), contents=("启动顺序：先加载配置。",),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "insufficient")
        self.assertEqual(run.exploration_stop_reason, "evidence_insufficient")
        self.assertEqual(run.evidence_selection, {"outcome": "insufficient", "citation_ids": [1]})
        self.assertIn("先加载配置", run.answer)
        self.assertIn("加载配置后的启动步骤", run.answer)
        self.assertEqual([call.args[0].name for call in executor.execute.await_args_list],
                         ["knowledge_base_search", "finish_research"])
        retrieval.retrieve_many.assert_awaited_once()
        llm.generate_answer.assert_awaited_once()
        self.assertEqual(run.usage.model_calls, 3)

    async def test_empty_result_is_insufficient_not_tool_failure(self):
        biz, llm, executor, retrieval, _ = research_setup(
            [action(query="启动顺序"), finish_action("insufficient", [])],
            grounded_answer("insufficient", missing=["启动顺序的原文依据"]), contents=(),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "insufficient")
        self.assertEqual(run.exploration_stop_reason, "evidence_insufficient")
        self.assertEqual(run.evidence_selection, {"outcome": "insufficient", "citation_ids": []})
        self.assertEqual(run.sources, [])
        self.assertIn("启动顺序的原文依据", run.answer)
        self.assertTrue(all(not result.errors for result in run.tool_results))
        self.assertEqual(executor.execute.await_count, 2)
        retrieval.retrieve_many.assert_awaited_once()
        llm.generate_answer.assert_awaited_once()

    async def test_valid_citation_matches_actual_sent_material(self):
        content = "启动顺序：先加载配置，再连接存储。"
        biz, llm, _, _, _ = research_setup(
            [action(query="启动顺序"), finish_action("answered", [1])],
            grounded_answer("answered", claims=[{
                "scope": "source", "text": content, "evidence_ids": ["E1"], "code_evidence_id": None,
            }]), contents=(content,),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.exploration_stop_reason, "evidence_complete")
        self.assertEqual(run.evidence_selection, {"outcome": "answered", "citation_ids": [1]})
        self.assertEqual(llm.generate_answer.await_args.args[0], run.final_request)
        sent = json.loads(run.final_request[-1]["content"])
        self.assertEqual(sent["sources"][0]["evidence"][0]["content"], content)
        self.assertEqual(run.answer_claims[0]["evidence"][0]["quote"], content)
        self.assertEqual(run.answer_claims[0]["evidence"][0]["citation_id"], 1)
        self.assertEqual(run.evidence_catalog[0].origin["chunk_id"], 100)
        self.assertEqual(run.usage.actual_total_tokens, 90)
        self.assertEqual(run.usage.unreported_model_calls, 0)

    async def test_materials_leave_room_for_followup_tool_history(self):
        large = source()
        large.content = "甲" * 4200
        biz, llm, executor = setup([action(), final()],
                                  [ToolResult("c1", "knowledge_base_search", "success", [large])])
        await self.query(biz)
        budget = InputBudget("qwen3.8-flash")
        sent = llm.calls[1][0]
        measured = budget.estimate(sent, executor.definitions(None), [])
        self.assertLessEqual(measured, budget.input_limit - budget.output_limit)

    async def test_conflicting_versions_keep_separate_sources(self):
        # 两份分别存储的版本文档；同一文档的过期切片不能冒充当前版本。
        old = "启动顺序：先加载配置，再连接存储。"
        current = "启动顺序：先连接存储，再加载配置。"
        biz, llm, _, _, _ = research_setup(
            [action(query="启动顺序"), finish_action("answered", [1, 2])],
            grounded_answer("answered", claims=[
                {"scope": "source", "text": old, "evidence_ids": ["E1"], "code_evidence_id": None},
                {"scope": "source", "text": current, "evidence_ids": ["E2"], "code_evidence_id": None},
                {"scope": "shared", "text": "两份版本资料描述的启动顺序不同。",
                 "evidence_ids": ["E1", "E2"], "code_evidence_id": None},
            ]), contents=(old, current),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.evidence_selection["citation_ids"], [1, 2])
        self.assertEqual([source.citation_id for source in run.sources], [1, 2])
        self.assertEqual([source.content for source in run.sources], [old, current])
        self.assertNotEqual(run.sources[0].document_id, run.sources[1].document_id)
        self.assertNotEqual(run.sources[0].document_updated_at, run.sources[1].document_updated_at)
        self.assertEqual([run.answer_claims[i]["evidence"][0]["quote"] for i in range(2)], [old, current])
        llm.generate_answer.assert_awaited_once()

    async def test_invalid_citation_never_published(self):
        biz, llm, _, _, _ = research_setup(
            [action(query="启动顺序"), finish_action("answered", [1])],
            grounded_answer("answered", claims=[{
                "scope": "source", "text": "编造结论。", "evidence_ids": ["E9999"], "code_evidence_id": None,
            }]),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "invalid_answer")
        self.assertEqual(run.error_code, "INVALID_EVIDENCE_ID")
        self.assertEqual(run.answer, "")
        self.assertNotIn("answer_validated", [event.kind for event in run.events])
        llm.generate_answer.assert_awaited_once()
        self.assertEqual(run.usage.model_calls, 3)

    async def test_tool_failure_stops_without_another_model_or_tool(self):
        result = ToolResult("c1", "knowledge_base_search", "error", errors=[ToolFailure("TOOL_DEPENDENCY_FAILED", "offline")])
        biz, llm, executor = setup([action()], [result])
        run = await self.query(biz)
        self.assertEqual(run.status, "tool_failed")
        self.assertEqual((len(llm.calls), len(executor.calls)), (1, 1))

    async def test_repeat_query_with_different_call_id_is_not_executed_again(self):
        biz, llm, executor, retrieval, sessions = research_setup(
            [action(query="启动顺序"), action("c2", " 启动顺序 ")],
            grounded_answer("answered", claims=[{
                "scope": "source", "text": "启动时先加载配置。", "evidence_ids": ["E1"], "code_evidence_id": None,
            }]),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.exploration_stop_reason, "repeated_call")
        self.assertIsNone(run.evidence_selection)
        self.assertEqual(executor.execute.await_count, 1)
        retrieval.retrieve_many.assert_awaited_once()
        llm.generate_answer.assert_awaited_once()
        self.assertTrue(sessions[0].finished)

    async def test_program_limits_close_research_before_final_answer(self):
        for limits, reason in ((RunLimits(model_calls=2), "model_call_limit"),
                               (RunLimits(tool_calls=1), "tool_call_limit")):
            with self.subTest(reason=reason):
                biz, llm, executor, retrieval, sessions = research_setup(
                    [action(query="启动顺序")], grounded_answer("answered", claims=[{
                        "scope": "source", "text": "启动时先加载配置。", "evidence_ids": ["E1"],
                        "code_evidence_id": None,
                    }]), limits=limits,
                )
                run = await self.query(biz)
                self.assertEqual(run.status, "completed")
                self.assertEqual(run.exploration_stop_reason, reason)
                self.assertIsNone(run.evidence_selection)
                self.assertTrue(sessions[0].finished)
                self.assertEqual(executor.execute.await_count, 1)
                llm.generate_answer.assert_awaited_once()
                blocked = await executor.execute(ToolCall("late", "knowledge_base_search", '{"query":"late"}'), sessions[0])
                self.assertEqual(blocked.errors[0].code, "RUN_STOPPED")
                retrieval.retrieve_many.assert_awaited_once()
                self.assertEqual(run.usage.model_calls, 2)

    async def test_no_new_evidence_closes_research_without_finish_tool(self):
        biz, llm, executor, retrieval, sessions = research_setup(
            [action(query="启动顺序"), action("c2", "后续启动步骤")],
            grounded_answer("insufficient", missing=["启动顺序的原文依据"]), contents=(),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "insufficient")
        self.assertEqual(run.exploration_stop_reason, "no_new_evidence")
        self.assertIsNone(run.evidence_selection)
        self.assertTrue(sessions[0].finished)
        self.assertEqual([call.args[0].name for call in executor.execute.await_args_list],
                         ["knowledge_base_search", "knowledge_base_search"])
        self.assertEqual(retrieval.retrieve_many.await_count, 2)
        self.assertEqual(run.usage.model_calls, 3)
        llm.generate_answer.assert_awaited_once()

    async def test_finish_research_requires_prior_evidence_action(self):
        biz, llm, _, retrieval, sessions = research_setup(
            [finish_action("insufficient", [])], grounded_answer("insufficient", missing=["启动依据"]),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "tool_failed")
        self.assertEqual(run.error_code, "EVIDENCE_SELECTION_INVALID")
        self.assertFalse(sessions[0].finished)
        retrieval.retrieve_many.assert_not_awaited()
        llm.generate_answer.assert_not_awaited()

    async def test_finish_research_rejects_unsent_citations(self):
        biz, llm, _, _, _ = research_setup(
            [action(query="启动顺序"), finish_action("answered", [99])], grounded_answer("answered"),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "tool_failed")
        self.assertEqual(run.error_code, "EVIDENCE_SELECTION_INVALID")
        self.assertIsNone(run.evidence_selection)
        llm.generate_answer.assert_not_awaited()

    async def test_final_answer_must_match_finish_research_outcome(self):
        biz, llm, _, _, _ = research_setup(
            [action(query="启动顺序"), finish_action("insufficient", [1])],
            grounded_answer("answered", claims=[{
                "scope": "source", "text": "启动时先加载配置。", "evidence_ids": ["E1"], "code_evidence_id": None,
            }]),
        )
        run = await self.query(biz)
        self.assertEqual(run.status, "invalid_answer")
        self.assertEqual(run.error_code, "EVIDENCE_SELECTION_MISMATCH")
        self.assertEqual(run.answer, "")
        llm.generate_answer.assert_awaited_once()

    async def test_exploration_answer_cannot_skip_finish_research(self):
        answer = grounded_answer("answered", claims=[{
            "scope": "source", "text": "启动时先加载配置。", "evidence_ids": ["E1"], "code_evidence_id": None,
        }])
        biz, llm, _, _, _ = research_setup([action(query="启动顺序"), answer], answer)
        run = await self.query(biz)
        self.assertEqual(run.status, "model_failed")
        self.assertEqual(run.error_code, "REQUIRED_TOOL_CALL_MISSING")
        self.assertEqual(run.answer, "")
        llm.generate_answer.assert_not_awaited()

    async def test_finish_research_selection_limits_final_catalog(self):
        for evidence_id, status in (("E1", "completed"), ("E2", "invalid_answer")):
            with self.subTest(evidence_id=evidence_id):
                biz, llm, _, _, _ = research_setup(
                    [action(query="启动顺序"), finish_action("answered", [2])],
                    grounded_answer("answered", claims=[{
                        "scope": "source", "text": "启动时先连接存储。", "evidence_ids": [evidence_id],
                        "code_evidence_id": None,
                    }]), contents=("启动顺序：先加载配置。", "启动顺序：先连接存储。"),
                )
                run = await self.query(biz)
                self.assertEqual(run.status, status)
                self.assertEqual([(item.evidence_id, item.citation_id) for item in run.evidence_catalog], [("E1", 2)])
                self.assertEqual(run.evidence_catalog[0].origin["document_id"], 2)
                sent = json.loads(run.final_request[-1]["content"])
                self.assertEqual([source["citation_id"] for source in sent["sources"]], [2])
                llm.generate_answer.assert_awaited_once()
                if status == "invalid_answer":
                    self.assertEqual(run.error_code, "INVALID_EVIDENCE_ID")
                    self.assertEqual(run.answer, "")

    def test_model_call_budget_requires_research_and_final_generation(self):
        for count in (0, 1, 7, 2.5, True):
            with (
                self.subTest(model_calls=count),
                self.assertRaisesRegex(ValueError, "模型调用总次数必须为 2 到 6 次"),
            ):
                RunLimits(model_calls=count)
        for count in range(2, 7):
            with self.subTest(model_calls=count):
                self.assertEqual(RunLimits(model_calls=count).model_calls, count)
        self.assertEqual(RunLimits().model_calls, 6)

    def budget_run(self, limits, *, has_evidence=True, unexpected_tool=False):
        content = "启动顺序：先加载配置，再连接存储。"
        material = AnswerSource(
            0, 1, "startup.md", 1, [MaterialSegment(11, 0, 0, len(content), content)], [],
            document_updated_at="2026-11-02T00:00:00+00:00",
        )
        sources = [material] if has_evidence else []
        result = ToolResult("c1", "knowledge_base_search", "success" if sources else "empty", sources)
        biz, llm, executor = setup([action(query="启动顺序")], [result], limits=limits)
        payload = {"status": "answered" if sources else "insufficient",
                   "claims": [{"scope": "source", "text": content, "evidence_ids": ["E1"],
                               "code_evidence_id": None}] if sources else [],
                   "missing": [] if sources else ["启动顺序的原文依据"]}
        response = ChatResult(json.dumps(payload, ensure_ascii=False), "qwen3.8-flash",
                              prompt_tokens=20, completion_tokens=10, total_tokens=30,
                              finish_reason="stop")
        llm.generate_answer = AsyncMock(return_value=action("c2", "new query") if unexpected_tool else response)
        executor.validate_sources = AsyncMock()
        return biz, llm, executor

    async def test_last_model_call_cannot_start_new_tools(self):
        biz, llm, executor = self.budget_run(RunLimits(model_calls=2))
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.exploration_stop_reason, "model_call_limit")
        self.assertEqual(run.usage.model_calls, 2)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(len(executor.calls), 1)
        llm.generate_answer.assert_awaited_once()
        self.assertEqual(set(llm.generate_answer.await_args.kwargs), {"max_tokens", "temperature"})
        self.assertIn("先加载配置", run.answer)
        self.assertEqual(run.answer_claims[0]["evidence_ids"], ["E1"])
        self.assertEqual(run.usage.actual_total_tokens, 60)
        self.assertEqual(run.usage.unreported_model_calls, 0)
        self.assertEqual([call.phase for call in run.usage.calls], ["exploration", "final"])
        self.assertEqual(executor.validate_sources.await_count, 2)

    async def test_last_model_call_explains_missing_evidence(self):
        biz, llm, executor = self.budget_run(RunLimits(model_calls=2), has_evidence=False)
        run = await self.query(biz)
        self.assertEqual(run.status, "budget_exhausted")
        self.assertEqual(run.stop_reason, "model_call_limit")
        self.assertEqual(run.usage.model_calls, 2)
        self.assertEqual(len(executor.calls), 1)
        llm.generate_answer.assert_awaited_once()
        self.assertIn("启动顺序的原文依据", run.answer)
        self.assertEqual(json.loads(run.final_response)["status"], "insufficient")
        self.assertEqual(run.sources, [])

    async def test_final_model_tool_call_is_rejected_without_execution(self):
        biz, llm, executor = self.budget_run(RunLimits(model_calls=2), unexpected_tool=True)
        run = await self.query(biz)
        self.assertEqual(run.status, "model_failed")
        self.assertEqual(run.error_code, "INVALID_TOOL_RESPONSE")
        self.assertEqual(run.stop_reason, "unexpected_tool_call")
        self.assertEqual(run.usage.model_calls, 2)
        self.assertEqual(len(executor.calls), 1)
        llm.generate_answer.assert_awaited_once()
        self.assertEqual(run.answer, "")

    async def test_tool_limit_reserves_final_answer_and_stops_tools(self):
        biz, llm, executor = self.budget_run(RunLimits(tool_calls=1))
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.exploration_stop_reason, "tool_call_limit")
        self.assertEqual(run.usage.model_calls, 2)
        self.assertEqual(len(executor.calls), 1)
        llm.generate_answer.assert_awaited_once()
        self.assertIn("先加载配置", run.answer)

    async def test_input_over_budget_does_not_start_model(self):
        biz, llm, executor = setup([])
        run = await biz.query(query="甲" * 16000, knowledge_base_ids=[1], user_id=1)
        self.assertEqual(run.status, "budget_exhausted")
        self.assertEqual(llm.calls, [])
        self.assertEqual(executor.calls, [])

    async def test_model_failure_is_explicit(self):
        biz, llm, _ = setup([LLMError(reason="LLM_DEPENDENCY_ERROR", message="offline")])
        run = await self.query(biz)
        self.assertEqual(run.status, "model_failed")
        self.assertEqual(run.answer, "")
        self.assertEqual(len(llm.calls), 1)

    async def test_timeout_closes_waiting_model_and_stops(self):
        biz, llm, executor = setup(["wait"], timeout=0.01)
        run = await self.query(biz)
        self.assertEqual(run.status, "timeout")
        self.assertTrue(llm.closed)
        self.assertEqual(executor.calls, [])

    async def test_cancel_signal_closes_model_and_records_terminal(self):
        biz, llm, executor = setup(["wait"])
        cancelled = asyncio.Event()
        task = asyncio.create_task(self.query(biz, cancelled=cancelled))
        await llm.waiting.wait()
        cancelled.set()
        run = await task
        self.assertEqual(run.status, "cancelled")
        self.assertEqual(run.events[-1].kind, "run_finished")
        self.assertTrue(llm.closed)
        self.assertEqual(executor.calls, [])

    async def test_sse_and_saved_result_are_the_same_run(self):
        biz, _, _ = setup([final()])
        with tempfile.TemporaryDirectory() as folder:
            service = RAGService(biz, LocalAnswerRunStore(Path(folder)))
            events = [e async for e in service.query_stream(query="q", knowledge_base_ids=[1], user_id=1)]
            result = events[-1]
            saved = json.loads(next(Path(folder).glob("user-1/*.json")).read_text(encoding="utf-8"))
            self.assertEqual(saved["result"], {k: v for k, v in result.items() if k != "type"})
            self.assertTrue(all(e["run_id"] == result["run_id"] for e in events))
            self.assertEqual([e["sequence"] for e in events[:-1]], list(range(1, len(events))))


if __name__ == "__main__":
    unittest.main()
