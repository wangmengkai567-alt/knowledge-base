"""Run the real bounded loop and ToolModel with deterministic tool/model responses."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.answer_run import RunLimits
from internal.biz.entity import ChatResult
from internal.biz.materials import AnswerSource, InputBudget, MaterialSegment, WebSource
from internal.biz.rag import RAGBiz
from internal.biz.tool_model import ToolModel
from internal.biz.tool_types import ToolCall, ToolFailure, ToolResult
from internal.biz.tools import ReadOnlyToolExecutor, ToolScope, ToolSession
from internal.data.answer_runs import LocalAnswerRunStore
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
        biz, llm, _ = setup([action(), final()], [ToolResult("c1", "knowledge_base_search", "success", [source()])])
        run = await self.query(biz)
        self.assertEqual(run.status, "insufficient")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(run.sources[0].citation_id, 1)

    async def test_empty_result_is_insufficient_not_tool_failure(self):
        biz, _, _ = setup([action(), final()], [ToolResult("c1", "knowledge_base_search", "empty")])
        run = await self.query(biz)
        self.assertEqual(run.status, "insufficient")
        self.assertEqual(run.sources, [])

    async def test_valid_citation_matches_actual_sent_material(self):
        biz, llm, _ = setup([action(), final("先加载配置。[来源 1]", "answered")],
                            [ToolResult("c1", "knowledge_base_search", "success", [source()])])
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        sent = json.loads(next(m for m in llm.calls[1][0] if m["role"] == "tool")["content"])
        self.assertEqual(sent["sources"][0]["content"], run.sources[0].content)
        self.assertEqual(sent["sources"][0]["citation_id"], run.sources[0].citation_id)

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
        old = source()
        current = WebSource(0, old.url, old.title, "2026-10-29T01:00:00Z", "组件先连接存储，再加载配置。", "web_page")
        biz, _, _ = setup([action(), action("c2", "different evidence"),
                           final("两个时间的资料顺序不同。[来源 1][来源 2]", "answered")],
                          [ToolResult("c1", "knowledge_base_search", "success", [old]),
                           ToolResult("c2", "knowledge_base_search", "success", [current])])
        run = await self.query(biz)
        self.assertEqual(run.status, "completed")
        self.assertEqual([s.citation_id for s in run.sources], [1, 2])
        self.assertNotEqual(run.sources[0].content, run.sources[1].content)
        self.assertNotEqual(run.sources[0].fetched_at, run.sources[1].fetched_at)

    async def test_invalid_citation_never_published(self):
        biz, _, _ = setup([final("编造结论。[来源 99]", "answered")])
        run = await self.query(biz)
        self.assertEqual(run.status, "invalid_answer")
        self.assertEqual(run.answer, "")
        self.assertNotIn("answer_validated", [event.kind for event in run.events])

    async def test_tool_failure_stops_without_another_model_or_tool(self):
        result = ToolResult("c1", "knowledge_base_search", "error", errors=[ToolFailure("TOOL_DEPENDENCY_FAILED", "offline")])
        biz, llm, executor = setup([action()], [result])
        run = await self.query(biz)
        self.assertEqual(run.status, "tool_failed")
        self.assertEqual((len(llm.calls), len(executor.calls)), (1, 1))

    async def test_repeat_query_with_different_call_id_is_not_executed_again(self):
        biz, _, executor = setup([action(), action("c2", " TOPIC "), final()],
                                  [ToolResult("c1", "knowledge_base_search", "empty")])
        run = await self.query(biz)
        self.assertEqual(run.status, "repeated_call")
        self.assertEqual(len(executor.calls), 1)

    async def test_last_model_call_cannot_start_new_tools(self):
        biz, llm, executor = setup([final()], limits=RunLimits(model_calls=1))
        run = await self.query(biz)
        self.assertEqual(run.usage.model_calls, 1)
        self.assertEqual(llm.calls[0][1]["tool_choice"], "none")
        self.assertEqual(executor.calls, [])
        self.assertEqual(run.status, "budget_exhausted")

    async def test_tool_limit_reserves_final_answer_and_stops_tools(self):
        biz, _, executor = setup([action(), final()], [ToolResult("c1", "knowledge_base_search", "empty")],
                                  limits=RunLimits(tool_calls=1))
        run = await self.query(biz)
        self.assertEqual(run.status, "budget_exhausted")
        self.assertEqual(run.stop_reason, "tool_call_limit")
        self.assertEqual(len(executor.calls), 1)

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
