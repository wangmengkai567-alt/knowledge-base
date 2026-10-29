"""Exercise real tool validation, ownership, empty results and dependency failures."""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.materials import InputBudget
from internal.biz.tool_types import ToolCall, ToolError, WebResult
from internal.biz.tools import ReadOnlyToolExecutor
from pkg.errors.base import LLMError


class ReadOnlyToolsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.retrieval = SimpleNamespace(retrieve_many=AsyncMock(return_value=[]))
        self.kbs = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(owner_id=1)))
        self.web = SimpleNamespace(search=AsyncMock(return_value=WebResult()), read=AsyncMock())
        self.executor = ReadOnlyToolExecutor(
            self.retrieval, self.kbs, AsyncMock(), self.web, InputBudget("qwen3.8-flash")
        )

    async def test_scope_cannot_access_another_owner(self):
        self.kbs.get_by_id.return_value.owner_id = 2
        with self.assertRaises(ToolError) as context:
            await self.executor.create_session(1, [1], False)
        self.assertEqual(context.exception.code, "KNOWLEDGE_BASE_FORBIDDEN")
        self.retrieval.retrieve_many.assert_not_awaited()

    async def test_disabled_web_never_reaches_provider(self):
        session = await self.executor.create_session(1, [1], False)
        result = await self.executor.execute(ToolCall("c", "web_search", '{"keywords":["Docker"]}'), session)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.errors[0].code, "WEB_DISABLED")
        self.web.search.assert_not_awaited()

    async def test_stringified_array_is_rejected_without_coercion(self):
        session = await self.executor.create_session(1, [1], True)
        result = await self.executor.execute(ToolCall("c", "web_search", '{"keywords":"[\\"Docker\\"]"}'), session)
        self.assertEqual(result.errors[0].code, "INVALID_TOOL_ARGUMENTS")
        self.web.search.assert_not_awaited()

    async def test_read_requires_previously_approved_url(self):
        session = await self.executor.create_session(1, [1], True)
        result = await self.executor.execute(ToolCall("c", "web_read", '{"urls":["https://example.com/"]}'), session)
        self.assertEqual(result.errors[0].code, "URL_NOT_APPROVED")
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
