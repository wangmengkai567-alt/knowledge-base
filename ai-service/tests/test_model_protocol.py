"""Native model protocol and safe status classification, without external requests."""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.data.llm.openai_patch import OpenAILLM
from pkg.errors.base import LLMError


class ModelProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_authorization_failure_is_safe_and_machine_readable(self):
        response = Mock(status_code=403)
        response.text = "must never be exposed: private prompt or key"
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        llm = OpenAILLM("https://example.invalid", "test-key", "qwen3.8-flash")
        with (
            patch("internal.data.llm.openai_patch.httpx.AsyncClient", return_value=client),
            self.assertRaises(LLMError) as context,
        ):
            await llm.generate_with_tools([], [{"type": "function"}])
        self.assertEqual(context.exception.reason, "LLM_AUTHORIZATION_FAILED")
        self.assertNotIn("private", str(context.exception))

    def test_missing_usage_or_mismatched_native_history_is_rejected(self):
        llm = OpenAILLM("https://example.invalid", "test-key", "qwen3.8-flash")
        payload = {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}
        with self.assertRaises(ValueError):
            llm._parse_result(payload, [{"type": "function"}], "auto")
        payload["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        payload["choices"][0]["message"]["tool_calls"] = [
            {"id": "c", "type": "function", "function": {"name": "tool", "arguments": "{}"}}
        ]
        with self.assertRaises(ValueError):
            llm._parse_result(payload, [{"type": "function"}], "auto")


if __name__ == "__main__":
    unittest.main()
