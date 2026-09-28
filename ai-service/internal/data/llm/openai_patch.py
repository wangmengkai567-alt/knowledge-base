"""
internal/data/llm/openai_patch.py — OpenAI 兼容 LLM 实现（patched Authorization header）

基于 httpx 调用 OpenAI-compatible API（DeepSeek / OpenAI / Ollama）。
统一使用 /v1/chat/completions 端点。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import asyncio
import httpx
import structlog

from internal.biz.entity import ChatResult, ChatChunk
from internal.biz.repo import LLMProvider
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

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

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """调用 OpenAI-compatible chat completions API。"""
        url = f"{self._base_url}/chat/completions"

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._temperature,
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        logger.debug(
            "openai_llm_request",
            url=url,
            model=self._model,
            message_count=len(messages),
        )

        delays = (2.0, 4.0, 8.0, 16.0)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response: httpx.Response | None = None
                for attempt in range(len(delays) + 1):
                    response = await client.post(url, json=payload, headers=headers)
                    if response.status_code != 429:
                        break
                    wait = delays[min(attempt, len(delays) - 1)]
                    logger.warning(
                        "openai_llm_rate_limited",
                        attempt=attempt + 1,
                        wait_seconds=wait,
                        status_code=429,
                    )
                    if attempt >= len(delays):
                        break
                    await asyncio.sleep(wait)

            if response is None:
                raise APIError(
                    code=ErrorCode.LLM_ERROR,
                    reason="LLM_ERROR",
                    message="LLM API returned no response",
                )

            if response.status_code != 200:
                logger.error(
                    "openai_llm_error",
                    status_code=response.status_code,
                    body=response.text[:500],
                )
                raise APIError(
                    code=ErrorCode.LLM_ERROR,
                    reason="LLM_ERROR",
                    message=f"LLM API returned {response.status_code}: {response.text[:200]}",
                )

            data = response.json()

            # 解析响应
            choices = data.get("choices", [])
            if not choices:
                logger.warning("openai_llm_empty_choices", model=self._model, data=str(data)[:200])
                return ChatResult(
                    content="",
                    model=data.get("model", self._model),
                    prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
                    completion_tokens=0,
                    total_tokens=data.get("usage", {}).get("total_tokens", 0),
                )

            choice = choices[0]
            content = choice.get("message", {}).get("content", "")

            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

            result = ChatResult(
                content=content,
                model=data.get("model", self._model),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

            logger.debug(
                "openai_llm_response",
                model=result.model,
                total_tokens=result.total_tokens,
                content_length=len(result.content),
            )

            return result

        except httpx.TimeoutException:
            logger.error("openai_llm_timeout", url=url, timeout=self._timeout)
            raise APIError(
                code=ErrorCode.LLM_TIMEOUT,
                reason="LLM_TIMEOUT",
                message=f"LLM API timeout after {self._timeout}s",
            )
        except httpx.ConnectError as e:
            logger.error("openai_llm_connect_error", url=url, error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=f"LLM API connection error: {e}",
            )
        except APIError:
            raise
        except Exception as e:
            logger.error("openai_llm_unexpected_error", error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                reason="LLM_ERROR",
                message=f"Unexpected LLM error: {e}",
            ) from e

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
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        logger.error(
                            "openai_llm_stream_error",
                            status_code=response.status_code,
                            body=body.decode(errors="replace")[:500],
                        )
                        raise APIError(
                            code=ErrorCode.LLM_ERROR,
                            message=f"LLM API returned {response.status_code}: {body.decode(errors='replace')[:200]}",
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

        except httpx.TimeoutException:
            logger.error("openai_llm_stream_timeout", url=url, timeout=self._timeout)
            raise APIError(
                code=ErrorCode.LLM_TIMEOUT,
                reason="LLM_TIMEOUT",
                message=f"LLM API timeout after {self._timeout}s",
            )
        except httpx.ConnectError as e:
            logger.error("openai_llm_stream_connect_error", url=url, error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=f"LLM API connection error: {e}",
            )
        except APIError:
            raise
        except Exception as e:
            logger.error("openai_llm_stream_unexpected_error", error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                reason="LLM_ERROR",
                message=f"Unexpected LLM stream error: {e}",
            ) from e
