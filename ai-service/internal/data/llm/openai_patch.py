"""
internal/data/llm/openai_patch.py — OpenAI 兼容 LLM 实现（patched Authorization header）

基于 httpx 调用 OpenAI-compatible API（DeepSeek / OpenAI / Ollama）。
统一使用 /v1/chat/completions 端点。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import structlog

from internal.biz.entity import ChatChunk, ChatResult
from internal.biz.repo import LLMProvider
from internal.biz.tool_types import ToolCall
from pkg.errors.base import APIError, LLMError
from pkg.errors.codes import ErrorCode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger()


class OpenAILLM(LLMProvider):
    """OpenAI 兼容 LLM — 通过 httpx 调用 /v1/chat/completions。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def supports_native_tools(self) -> bool:
        # 当前只有准备阶段已验证的千问模型接入此能力。
        return self._model == "qwen3.8-flash"

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        return await self._complete(messages, temperature, max_tokens)

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str = "auto",
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> ChatResult:
        if not self.supports_native_tools:
            raise LLMError(
                reason="TOOL_CALLING_UNSUPPORTED", message="当前模型未确认原生工具调用能力"
            )
        if not tools or tool_choice not in ("auto", "none", "required"):
            raise LLMError(reason="INVALID_TOOL_REQUEST", message="工具定义或调用方式无效")
        return await self._complete(
            messages, temperature, max_tokens, tools=tools, tool_choice=tool_choice
        )

    async def _complete(
        self,
        messages: list[dict],
        temperature: float | None,
        max_tokens: int | None,
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> ChatResult:
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
        }
        if tools:
            payload.update(
                tools=tools, tool_choice=tool_choice,
                parallel_tool_calls=False, enable_thinking=False,
            )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            if response.status_code != 200:
                # 不把供应商响应原文（可能回显私有提示或密钥）放进错误或日志。
                raise LLMError(
                    reason="LLM_DEPENDENCY_ERROR",
                    message=f"LLM API 返回 HTTP {response.status_code}",
                )
            return self._parse_result(response.json(), tools, tool_choice)
        except httpx.TimeoutException as exc:
            raise LLMError(
                code=ErrorCode.LLM_TIMEOUT, reason="LLM_TIMEOUT", message="模型请求超时"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(reason="LLM_DEPENDENCY_ERROR", message="模型服务连接失败") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(reason="INVALID_LLM_RESPONSE", message="模型响应结构无效") from exc

    def _parse_result(self, data: dict, tools: list[dict] | None, tool_choice: str) -> ChatResult:
        if not isinstance(data, dict) or not isinstance(data.get("choices"), list):
            raise ValueError("missing choices")
        choice = data["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ValueError("invalid message")
        message = choice["message"]
        content = message.get("content")
        content = "" if content is None else content
        if not isinstance(content, str):
            raise ValueError("invalid content")
        raw_calls = message.get("tool_calls")
        raw_calls = [] if raw_calls is None else raw_calls
        if not isinstance(raw_calls, list):
            raise ValueError("invalid tool_calls")
        calls = []
        for raw in raw_calls:
            if not isinstance(raw, dict) or not isinstance(raw.get("function"), dict):
                raise ValueError("invalid function call")
            function = raw["function"]
            values = (raw["id"], function["name"], function["arguments"])
            if raw["type"] != "function" or not all(isinstance(v, str) and v for v in values):
                raise ValueError("invalid function call")
            calls.append(ToolCall(*values))
        if len({call.id for call in calls}) != len(calls):
            raise ValueError("duplicate call id")
        finish = choice.get("finish_reason")
        if bool(calls) != (finish == "tool_calls"):
            raise ValueError("tool_calls and finish_reason disagree")
        if calls and (not tools or tool_choice == "none"):
            raise ValueError("unexpected tool call")
        if tools and tool_choice == "required" and not calls:
            raise ValueError("required tool call missing")
        if not content and not calls:
            raise ValueError("empty response")
        usage = data.get("usage")
        if (tools or usage is not None) and not isinstance(usage, dict):
            raise ValueError("invalid or missing usage")
        usage = {} if usage is None else usage
        counts = [usage.get(key, 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if not all(type(count) is int and count >= 0 for count in counts):
            raise ValueError("invalid usage")
        if tools and (any(key not in usage for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
                      or counts[2] != counts[0] + counts[1]):
            raise ValueError("incomplete usage")
        return ChatResult(
            content=content, model=data.get("model", self._model),
            prompt_tokens=counts[0], completion_tokens=counts[1], total_tokens=counts[2],
            tool_calls=calls, finish_reason=finish,
        )

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """流式调用 OpenAI-compatible chat completions API。

        通过 httpx 发送 stream=True 请求，逐行解析 SSE 事件。
        """
        url = f"{self._base_url}/chat/completions"

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "stream": True,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        logger.debug(
            "openai_llm_stream_request",
            url=url,
            model=self._model,
            message_count=len(messages),
        )

        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout) as client,
                client.stream("POST", url, json=payload, headers=headers) as response,
            ):
                if response.status_code != 200:
                    raise LLMError(
                        reason="LLM_DEPENDENCY_ERROR",
                        message=f"LLM API 返回 HTTP {response.status_code}",
                    )

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or line.startswith(":" ):
                        continue

                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content") or ""
                    if not content:
                        content = (choice.get("message") or {}).get("content") or ""
                    finish_reason = choice.get("finish_reason")

                    yield ChatChunk(
                        delta=content,
                        model=data.get("model", self._model),
                        finish_reason=finish_reason,
                    )

        except httpx.TimeoutException as exc:
            logger.error("openai_llm_stream_timeout", url=url, timeout=self._timeout)
            raise APIError(
                code=ErrorCode.LLM_TIMEOUT,
                reason="LLM_TIMEOUT",
                message=f"LLM API timeout after {self._timeout}s",
            ) from exc
        except httpx.ConnectError as e:
            logger.error("openai_llm_stream_connect_error", url=url, error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                reason="LLM_DEPENDENCY_ERROR",
                message="LLM API connection failed",
            ) from e
        except APIError:
            raise
        except Exception as e:
            logger.error("openai_llm_stream_unexpected_error", error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                reason="LLM_ERROR",
                message=f"Unexpected LLM stream error: {e}",
            ) from e
