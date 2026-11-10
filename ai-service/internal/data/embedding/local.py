"""通过 Ollama /api/embeddings 为每条文本获取向量。

批量输入按配置并发执行；向量维度取实际响应，用量按输入字符数估算。
"""

from __future__ import annotations

import asyncio

import httpx
import structlog

from internal.biz.entity import EmbeddingResult, EmbeddingUsage
from internal.biz.repo import EmbeddingProvider
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


class OllamaEmbedding(EmbeddingProvider):
    """逐条请求本地嵌入接口，并通过信号量限制批次并发；维度由实际响应更新。"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        dimension: int = 768,
        timeout: int = 60,
        max_concurrency: int = 5,
        keep_alive: str = "5m",
    ) -> None:
        """配置本地嵌入接口；timeout 单位为秒，max_concurrency 为并发数，keep_alive 为模型驻留时间。"""
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._max_concurrency = max_concurrency
        self._keep_alive = keep_alive

    @property
    def dimension(self) -> int:
        """返回当前向量维度；请求成功后该值反映模型实际输出长度。"""
        return self._dimension

    @property
    def model_name(self) -> str:
        """返回发送给 Ollama 的嵌入模型名称。"""
        return self._model

    async def _embed_single(
        self, client: httpx.AsyncClient, text: str
    ) -> list[float]:
        """为一条文本请求嵌入向量；并发限制由调用方的信号量执行。"""
        url = f"{self._base_url}/api/embeddings"
        payload = {
            "model": self._model,
            "prompt": text,
            "keep_alive": self._keep_alive,
        }

        response = await client.post(url, json=payload)

        if response.status_code != 200:
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_API_ERROR",
                message=(
                    f"Ollama embedding API returned {response.status_code}: "
                    f"{response.text[:200]}"
                ),
            )

        data = response.json()
        embedding = data.get("embedding")

        if not embedding or not isinstance(embedding, list):
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_EMPTY",
                message="Ollama embedding API returned empty or invalid embedding",
            )

        return embedding

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """按输入顺序收集并发请求的向量，校验非空与数量，返回按实际响应更新的维度和估算用量。"""
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model=self._model,
                dimension=self._dimension,
                usage=EmbeddingUsage(),
            )

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _bounded_embed(client: httpx.AsyncClient, text: str) -> list[float]:
            """取得本批并发名额后请求单条文本，完成或异常时自动释放名额。"""
            async with semaphore:
                return await self._embed_single(client, text)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                # 并发请求所有文本的嵌入向量
                vectors = await asyncio.gather(
                    *[_bounded_embed(client, t) for t in texts]
                )

            # 校验返回数量
            if len(vectors) != len(texts):
                raise APIError(
                    code=ErrorCode.INTERNAL_ERROR,
                    reason="OLLAMA_EMBEDDING_COUNT_MISMATCH",
                    message=f"Expected {len(texts)} embeddings, got {len(vectors)}",
                )

            # 拒绝空向量；随后取实际响应维度更新当前提供商的维度记录。
            if not vectors or not vectors[0]:
                raise APIError(
                    code=ErrorCode.INTERNAL_ERROR,
                    reason="OLLAMA_EMBEDDING_EMPTY_VECTORS",
                    message="Ollama returned empty embedding vectors",
                )
            actual_dim = len(vectors[0])
            if actual_dim != self._dimension:
                logger.info(
                    "ollama_embedding_dimension_adjusted",
                    configured=self._dimension,
                    actual=actual_dim,
                )
                # 以实际维度为准
                self._dimension = actual_dim

            # 此接口响应未解析用量，返回输入总字符数除以 4 的估算值。
            estimated_tokens = max(1, sum(len(t) for t in texts) // 4)

            logger.debug(
                "ollama_embedding_success",
                batch_size=len(texts),
                dimension=self._dimension,
                estimated_tokens=estimated_tokens,
            )

            return EmbeddingResult(
                vectors=list(vectors),
                model=self._model,
                dimension=self._dimension,
                usage=EmbeddingUsage(
                    prompt_tokens=estimated_tokens,
                    total_tokens=estimated_tokens,
                ),
            )

        except httpx.TimeoutException:
            # 单独标记请求超时，调用方可据此区分执行时限与其他依赖错误。
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_TIMEOUT",
                message=f"Ollama embedding API timeout after {self._timeout}s",
            )
        except httpx.ConnectError as e:
            # 连接服务失败时返回依赖错误，不返回空向量或空回答。
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_CONNECT_ERROR",
                message=(
                    f"Cannot connect to Ollama at {self._base_url}. "
                    f"Ensure Ollama is running and the model '{self._model}' is pulled. "
                    f"Run: ollama pull {self._model}"
                ),
            )
        except APIError:
            # 保留前面已分类的服务响应或数据校验错误，不再包装成未预期异常。
            raise
        except Exception as e:
            # 将其余响应解析或处理异常转换为统一业务错误，并保留异常链。
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_FAILED",
                message=f"Ollama embedding API call failed: {str(e)[:200]}",
            )
