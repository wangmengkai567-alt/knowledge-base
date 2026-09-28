"""
internal/data/llm/local.py — Ollama 本地 LLM 实现

基于 Ollama 原生接口（/api/chat）实现 LLMProvider。
无需 API 密钥，不依赖外部云。

Ollama 接口文档：https://github.com/ollama/ollama/blob/main/docs/api.md

与 OpenAI 兼容层（/v1/chat/completions）的区别：
- 使用原生 /api/chat，响应更精简
- 可用 keep_alive 控制模型在内存中的驻留时间
- 流式用 NDJSON（每行一个 JSON），不是 SSE
- 原生接口不需要 Authorization 请求头
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import structlog

from internal.biz.entity import ChatResult, ChatChunk
from internal.biz.repo import LLMProvider
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


class OllamaLLM(LLMProvider):
    """Ollama 本地 LLM — 通过原生 /api/chat 端点实现。

    适用于本地 Ollama 实例，无需 API Key。
    支持非流式 generate() 和流式 generate_stream()。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 120,
        keep_alive: str = "5m",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._keep_alive = keep_alive

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """调用 Ollama /api/chat（非流式）。

        请求格式：
            POST /api/chat
            {"model": "...", "messages": [...], "stream": false,
             "options": {"temperature": ..., "num_predict": ...},
             "keep_alive": "5m"}

        响应格式：
            {"message": {"role": "assistant", "content": "..."},
             "eval_count": N, "prompt_eval_count": M,
             "total_duration": ..., "done": true}
        """
        url = f"{self._base_url}/api/chat"

        payload: dict = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature if temperature is not None else self._temperature,
                "num_predict": max_tokens if max_tokens is not None else self._max_tokens,
            },
            "keep_alive": self._keep_alive,
        }

        logger.debug(
            "ollama_llm_request",
            url=url,
            model=self._model,
            message_count=len(messages),
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload)

            if response.status_code != 200:
                logger.error(
                    "ollama_llm_error",
                    status_code=response.status_code,
                    body=response.text[:500],
                )
                raise APIError(
                    code=ErrorCode.LLM_ERROR,
                    message=(
                        f"Ollama API returned {response.status_code}: "
                        f"{response.text[:200]}"
                    ),
                )

            data = response.json()
            content = data.get("message", {}).get("content", "")

            # Ollama 的 token 用量字段
            prompt_tokens = data.get("prompt_eval_count", 0)
            completion_tokens = data.get("eval_count", 0)
            total_tokens = prompt_tokens + completion_tokens

            result = ChatResult(
                content=content,
                model=self._model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

            logger.debug(
                "ollama_llm_response",
                model=result.model,
                total_tokens=result.total_tokens,
                content_length=len(result.content),
            )

            return result

        except httpx.TimeoutException:
            logger.error("ollama_llm_timeout", url=url, timeout=self._timeout)
            raise APIError(
                code=ErrorCode.LLM_TIMEOUT,
                message=f"Ollama API timeout after {self._timeout}s",
            )
        except httpx.ConnectError as e:
            logger.error("ollama_llm_connect_error", url=url, error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=(
                    f"Cannot connect to Ollama at {self._base_url}. "
                    f"Ensure Ollama is running and the model '{self._model}' is pulled. "
                    f"Run: ollama pull {self._model}"
                ),
            )
        except APIError:
            raise
        except Exception as e:
            logger.error("ollama_llm_unexpected_error", error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=f"Unexpected Ollama LLM error: {e}",
            )

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """调用 Ollama /api/chat 端点（流式）。

        Ollama 流式响应使用 NDJSON 格式（每行一个 JSON 对象），而非 SSE。

        每行格式：
            {"message": {"content": "delta"}, "done": false}
            ...
            {"message": {"content": ""}, "done": true,
             "eval_count": N, "prompt_eval_count": M}
        """
        url = f"{self._base_url}/api/chat"

        payload: dict = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": temperature if temperature is not None else self._temperature,
                "num_predict": max_tokens if max_tokens is not None else self._max_tokens,
            },
            "keep_alive": self._keep_alive,
        }

        logger.debug(
            "ollama_llm_stream_request",
            url=url,
            model=self._model,
            message_count=len(messages),
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        body = await response.aread()
                        logger.error(
                            "ollama_llm_stream_error",
                            status_code=response.status_code,
                            body=body.decode(errors="replace")[:500],
                        )
                        raise APIError(
                            code=ErrorCode.LLM_ERROR,
                            message=(
                                f"Ollama API returned {response.status_code}: "
                                f"{body.decode(errors='replace')[:200]}"
                            ),
                        )

                    # Ollama 使用 NDJSON：每行一个完整的 JSON 对象
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            data = json.loads(line)
                        except (json.JSONDecodeError, ValueError) as e:
                            logger.warning(
                                "ollama_ndjson_parse_error",
                                line=line[:100],
                                error=str(e),
                            )
                            continue

                        content = data.get("message", {}).get("content", "")
                        done = data.get("done", False)

                        yield ChatChunk(
                            delta=content,
                            model=self._model,
                            finish_reason="stop" if done else None,
                        )

        except httpx.TimeoutException:
            logger.error("ollama_llm_stream_timeout", url=url, timeout=self._timeout)
            raise APIError(
                code=ErrorCode.LLM_TIMEOUT,
                message=f"Ollama API timeout after {self._timeout}s",
            )
        except httpx.ConnectError as e:
            logger.error("ollama_llm_stream_connect_error", url=url, error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=(
                    f"Cannot connect to Ollama at {self._base_url}. "
                    f"Ensure Ollama is running and the model '{self._model}' is pulled. "
                    f"Run: ollama pull {self._model}"
                ),
            )
        except APIError:
            raise
        except Exception as e:
            logger.error("ollama_llm_stream_unexpected_error", error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=f"Unexpected Ollama stream error: {e}",
            )
