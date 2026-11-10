"""Exercise real tool validation, ownership, empty results and dependency failures."""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from internal.biz.token_budget import InputBudget, TokenLimits
from internal.biz.tool_types import ToolCall, ToolError, WebResult
from internal.biz.tools import ReadOnlyToolExecutor
from internal.biz.web_sources import WebPage
from internal.data.token_counter import TOKENIZER_REF, ModelTokenCounter
from pkg.errors.base import LLMError

_COUNTER = ModelTokenCounter(str(Path(__file__).resolve().parents[1] / "resources/qwen38-flash"),
                             model="qwen3.8-flash", tokenizer_ref=TOKENIZER_REF)

def make_budget(input_limit=16000):
    limits = TokenLimits(input_limit=input_limit, tool_result_max_tokens=min(3000, input_limit),
                         page_tokens=min(2000, input_limit), summary_tokens=min(512, input_limit),
                         preview_tokens=min(64, input_limit))
    return InputBudget("qwen3.8-flash", _COUNTER, _COUNTER.render_request, TOKENIZER_REF, limits)


class ReadOnlyToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.retrieval = SimpleNamespace(retrieve_many=AsyncMock(return_value=[]))
        self.kbs = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(owner_id=1)))
        self.web = SimpleNamespace(search=AsyncMock(return_value=WebResult()), read=AsyncMock())
        self.executor = ReadOnlyToolExecutor(
            self.retrieval, self.kbs, AsyncMock(), self.web, make_budget()
        )

    async def test_scope_cannot_access_another_owner(self):
        self.kbs.get_by_id.return_value.owner_id = 2
        with self.assertRaises(ToolError) as context:
            await self.executor.create_session(1, [1], False)
        self.assertEqual(context.exception.code, "KNOWLEDGE_BASE_FORBIDDEN")
        self.retrieval.retrieve_many.assert_not_awaited()

    async def test_disabled_web_never_reaches_provider(self):
        session = await self.executor.create_session(1, [1], False)
        result = await self.executor.execute(ToolCall("c", "web_search", '{"query":"Docker"}'), session)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.errors[0].code, "WEB_DISABLED")
        self.web.search.assert_not_awaited()

    async def test_search_rejects_wrong_types_without_coercion(self):
        for arguments in ({"query": ["Docker"]}, {"query": "Docker", "max_results": "5"}):
            with self.subTest(arguments=arguments):
                session = await self.executor.create_session(1, [1], True)
                result = await self.executor.execute(ToolCall("c", "web_search", json.dumps(arguments)), session)
                self.assertEqual(result.errors[0].code, "INVALID_TOOL_ARGUMENTS")
                self.web.search.assert_not_awaited()

    async def test_search_uses_current_query_and_result_limit(self):
        session = await self.executor.create_session(1, [1], True)
        result = await self.executor.execute(ToolCall("c", "web_search", '{"query":"Docker","max_results":3}'), session)
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.errors, [])
        self.web.search.assert_awaited_once_with("Docker", 3)

    async def test_read_requires_registered_source_id(self):
        session = await self.executor.create_session(1, [1], True)
        result = await self.executor.execute(ToolCall("c", "web_read", '{"source_id":"web_1"}'), session)
        self.assertEqual(result.errors[0].code, "SOURCE_NOT_APPROVED")
        self.web.read.assert_not_awaited()

    async def test_registered_source_is_read_with_current_window_arguments(self):
        session = await self.executor.create_session(1, [1], True)
        entry = session.web.register("https://example.com/", "Example", "user")
        content = "说明：这是实际读取的网页正文。"
        self.web.read.return_value = WebResult(pages=[WebPage(entry.url, entry.title, "2026-11-02T00:00:00Z", content, [])])
        result = await self.executor.execute(ToolCall("c", "web_read", json.dumps({
            "source_id": entry.source_id, "start": 3, "max_tokens": 100,
        })), session)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.errors, [])
        self.assertEqual(result.sources[0].content, content[3:])
        self.assertEqual((result.sources[0].source_id, result.sources[0].start), (entry.source_id, 3))
        self.web.read.assert_awaited_once_with([entry.url])

    async def test_obsolete_web_arguments_are_rejected(self):
        # 明确的负例：生产工具不再接受旧字段。
        for tool, arguments in (("web_search", {"keywords": ["Docker"]}),
                                ("web_read", {"urls": ["https://example.com/"]}),
                                ("web_read", {"source_id": "web_1", "max_chars": 100})):
            with self.subTest(tool=tool):
                session = await self.executor.create_session(1, [1], True)
                result = await self.executor.execute(ToolCall("c", tool, json.dumps(arguments)), session)
                self.assertEqual(result.errors[0].code, "INVALID_TOOL_ARGUMENTS")
        self.web.search.assert_not_awaited()
        self.web.read.assert_not_awaited()

    async def test_empty_knowledge_result_is_not_dependency_error(self):
        session = await self.executor.create_session(1, [1], False)
        result = await self.executor.execute(ToolCall("c", "knowledge_base_search", '{"query":"q"}'), session)
        self.assertEqual(result.status, "empty")
        self.assertEqual(result.errors, [])
        self.retrieval.retrieve_many.assert_awaited_once_with("q", (1,), 5)

    async def test_dependency_failure_never_switches_to_web(self):
        self.retrieval.retrieve_many.side_effect = LLMError(reason="EMBEDDING_ERROR", message="offline")
        session = await self.executor.create_session(1, [1], True)
        result = await self.executor.execute(ToolCall("c", "knowledge_base_search", '{"query":"q"}'), session)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.errors[0].code, "TOOL_DEPENDENCY_FAILED")
        self.web.search.assert_not_awaited()

    async def test_cancellation_closes_active_tool(self):
        started, closed, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def wait(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        self.retrieval.retrieve_many.side_effect = wait
        session = await self.executor.create_session(1, [1], False, cancelled=cancelled)
        task = asyncio.create_task(self.executor.execute(ToolCall("c", "knowledge_base_search", '{"query":"q"}'), session))
        await started.wait()
        cancelled.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())

    async def test_cancelled_session_starts_no_operation(self):
        signal = asyncio.Event()
        signal.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.executor.create_session(1, [1], False, cancelled=signal)
        self.kbs.get_by_id.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
