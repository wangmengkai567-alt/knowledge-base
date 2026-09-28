"""
internal/data/embedding/local.py — Ollama 本地 Embedding 实现

基于 Ollama 原生接口（/api/embeddings）实现 EmbeddingProvider。
无需 API 密钥，不依赖外部云。

Ollama Embedding 接口：
    请求 POST /api/embeddings
    {"model": "nomic-embed-text", "prompt": "文本内容"}

    响应：{"embedding": [0.1, 0.2, ...]}

注意：该接口每次只接受一条文本，本实现内部用 asyncio.gather 并发批量请求。
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
    """Ollama 本地 Embedding — 通过原生 /api/embeddings 端点实现。

    每次 API 调用仅处理单条文本，内部使用并发提升批量效率。
    维度由模型决定，首次调用时从响应中探测并缓存。
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "nomic-embed-text",
        dimension: int = 768,
        timeout: int = 60,
        max_concurrency: int = 5,
        keep_alive: str = "5m",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._max_concurrency = max_concurrency
        self._keep_alive = keep_alive

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    async def _embed_single(
        self, client: httpx.AsyncClient, text: str
    ) -> list[float]:
        """对单条文本调用 Ollama /api/embeddings。

        使用信号量控制并发度，防止短时间大量请求压垮 Ollama。
        """
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
        """批量嵌入文本 — 并发调用 Ollama /api/embeddings。

        内部使用 asyncio.Semaphore 控制并发度（默认 5），
        防止短时间大量请求压垮本地 Ollama 实例。
        """
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model=self._model,
                dimension=self._dimension,
                usage=EmbeddingUsage(),
            )

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _bounded_embed(client: httpx.AsyncClient, text: str) -> list[float]:
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

            # 校验维度一致性（添加空向量守卫）
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

            # 估算 token 用量（Ollama 原生 API 不返回 usage）
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
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_TIMEOUT",
                message=f"Ollama embedding API timeout after {self._timeout}s",
            )
        except httpx.ConnectError as e:
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
            raise
        except Exception as e:
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="OLLAMA_EMBEDDING_FAILED",
                message=f"Ollama embedding API call failed: {str(e)[:200]}",
            )
