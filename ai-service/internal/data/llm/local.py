"""通过 Ollama /api/chat 发起非流式对话请求，返回正文及接口报告的输入、输出用量。"""

from __future__ import annotations

import httpx
import structlog

from internal.biz.entity import ChatResult
from internal.biz.repo import LLMProvider
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


class OllamaLLM(LLMProvider):
    """Ollama 本地 LLM — 通过原生 /api/chat 端点实现。

    适用于本地 Ollama 实例，无需 API Key。
    模型原生调用返回完整结果；问答过程事件由 RAGService 发布。
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
        """配置本地对话接口；max_tokens 限制输出，timeout 单位为秒，keep_alive 为模型驻留时间。"""
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._keep_alive = keep_alive

    @property
    def model_name(self) -> str:
        """返回本适配器请求的 Ollama 模型名称。"""
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

        except httpx.TimeoutException as exc:
            # 单独标记请求超时，调用方可据此区分执行时限与其他依赖错误。
            logger.error("ollama_llm_timeout", url=url, timeout=self._timeout)
            raise APIError(
                code=ErrorCode.LLM_TIMEOUT,
                message=f"Ollama API timeout after {self._timeout}s",
            ) from exc
        except httpx.ConnectError as e:
            # 连接服务失败时返回依赖错误，不返回空向量或空回答。
            logger.error("ollama_llm_connect_error", url=url, error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=(
                    f"Cannot connect to Ollama at {self._base_url}. "
                    f"Ensure Ollama is running and the model '{self._model}' is pulled. "
                    f"Run: ollama pull {self._model}"
                ),
            ) from e
        except APIError:
            # 保留前面已分类的服务响应或数据校验错误，不再包装成未预期异常。
            raise
        except Exception as e:
            # 将其余响应解析或处理异常转换为统一业务错误，并保留异常链。
            logger.error("ollama_llm_unexpected_error", error=str(e))
            raise APIError(
                code=ErrorCode.LLM_ERROR,
                message=f"Unexpected Ollama LLM error: {e}",
            ) from e
