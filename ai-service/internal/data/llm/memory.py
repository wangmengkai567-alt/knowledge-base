"""
internal/data/llm/memory.py — 内存伪 LLM 实现

无需 API Key，基于输入内容生成确定性伪回复。
"""

from __future__ import annotations

import hashlib

import structlog

from internal.biz.entity import ChatResult
from internal.biz.repo import LLMProvider

logger = structlog.get_logger()


class MemoryLLM(LLMProvider):
    """内存伪 LLM — 基于输入生成确定性伪回复。

    回复内容包含用户输入的摘要，便于测试验证。
    """

    def __init__(self, model: str = "memory-llm-v1") -> None:
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def is_mock(self) -> bool:
        return True

    @staticmethod
    def _extract_user_content(messages: list[dict[str, str]]) -> str:
        """提取用户最后一条消息内容。"""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return ""

    def _build_pseudo_reply(self, user_content: str) -> str:
        """生成确定性伪回复内容。"""
        query_preview = user_content[:100] if user_content else "(empty)"
        content_hash = hashlib.md5(user_content.encode()).hexdigest()[:8]
        return (
            f"[MemoryLLM 伪回复] 这是 MemoryLLM 的模拟回复。\n\n"
            f"您的问题：{query_preview}\n\n"
            f"提示：当前使用 MemoryLLM，"
            f"将 LLM provider 改为 openai/deepseek/tencent 等后才会调用真实模型。\n\n"
            f"[hash: {content_hash}]"
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """生成伪回复。

        回复内容：提取用户最后一条消息，生成包含摘要的伪回复。
        """
        user_content = self._extract_user_content(messages)
        content = self._build_pseudo_reply(user_content)

        # 模拟 token 用量
        prompt_tokens = sum(len(m.get("content", "").split()) for m in messages)
        completion_tokens = len(content.split())

        result = ChatResult(
            content=content,
            model=self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

        logger.debug(
            "memory_llm_response",
            query_preview=user_content[:50],
            total_tokens=result.total_tokens,
        )

        return result
