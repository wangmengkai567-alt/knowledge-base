"""
internal/data/embedding/openai.py — OpenAI 兼容 Embedding 实现

适用于 OpenAI / DeepSeek 等兼容 OpenAI API 格式的服务。
通过 httpx 调用远程 API，将文本列表转换为向量列表。
"""

from __future__ import annotations

import httpx
import structlog

from internal.biz.entity import EmbeddingResult, EmbeddingUsage
from internal.biz.repo import EmbeddingProvider
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI 兼容 API 的 Embedding 实现。

    支持 OpenAI、DeepSeek 等兼容 /v1/embeddings 接口的服务。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int = 1024,
        timeout: int = 30,
        request_dimensions: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._request_dimensions = request_dimensions

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """调用 OpenAI 兼容 API 获取文本向量。

        请求：POST {base_url}/embeddings
        请求头：Authorization: Bearer {api_key}
        请求体：{"model": model, "input": texts}

        响应里有 usage 时用真实值，否则按字符数估算。
        """
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model=self._model,
                dimension=self._dimension,
                usage=EmbeddingUsage(),
            )

        url = f"{self._base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": texts,
        }
        # 智谱 embedding-3 必须带 dimensions，否则默认 2048，会和库里的向量维度对不上。
        if self._request_dimensions:
            payload["dimensions"] = self._dimension

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            # 解析响应：data.data[i].embedding
            embeddings = data.get("data", [])
            if not embeddings:
                raise APIError(
                    code=ErrorCode.INTERNAL_ERROR,
                    reason="EMBEDDING_EMPTY_RESPONSE",
                    message="Embedding API returned empty data",
                )

            # 按 index 排序确保顺序正确
            embeddings.sort(key=lambda x: x.get("index", 0))
            vectors = [item["embedding"] for item in embeddings]

            # 校验返回数量与输入一致
            if len(vectors) != len(texts):
                raise APIError(
                    code=ErrorCode.INTERNAL_ERROR,
                    reason="EMBEDDING_COUNT_MISMATCH",
                    message=f"Expected {len(texts)} embeddings, got {len(vectors)}",
                )

            actual_dim = len(vectors[0]) if vectors else 0
            if actual_dim != self._dimension:
                raise APIError(
                    code=ErrorCode.EMBEDDING_ERROR,
                    reason="EMBEDDING_DIMENSION_MISMATCH",
                    message=(
                        f"Embedding model '{self._model}' returned dim={actual_dim}, "
                        f"config expects {self._dimension}. "
                        f"Set AI_EMBEDDING__DIMENSION={actual_dim} and rebuild vectors."
                    ),
                )
            for i, vec in enumerate(vectors):
                if len(vec) != actual_dim:
                    raise APIError(
                        code=ErrorCode.EMBEDDING_ERROR,
                        reason="EMBEDDING_DIMENSION_MISMATCH",
                        message=f"Embedding at index {i} has dim={len(vec)}, expected {actual_dim}",
                    )

            # 优先使用 API 返回的真实 usage，缺失时基于字符数估算
            usage_data = data.get("usage") or {}
            prompt_tokens = usage_data.get("prompt_tokens")
            total_tokens = usage_data.get("total_tokens")
            if prompt_tokens is None or total_tokens is None:
                estimated = max(1, sum(len(t) for t in texts) // 4)
                prompt_tokens = prompt_tokens if prompt_tokens is not None else estimated
                total_tokens = total_tokens if total_tokens is not None else estimated

            model_name = data.get("model") or self._model

            logger.debug(
                "embedding_api_success",
                batch_size=len(texts),
                dimension=len(vectors[0]) if vectors else 0,
                prompt_tokens=prompt_tokens,
                total_tokens=total_tokens,
            )
            return EmbeddingResult(
                vectors=vectors,
                model=model_name,
                dimension=actual_dim,
                usage=EmbeddingUsage(
                    prompt_tokens=int(prompt_tokens),
                    total_tokens=int(total_tokens),
                ),
            )

        except httpx.TimeoutException:
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="EMBEDDING_TIMEOUT",
                message=f"Embedding API timeout after {self._timeout}s",
            )
        except httpx.HTTPStatusError as e:
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="EMBEDDING_API_ERROR",
                message=f"Embedding API HTTP {e.response.status_code}",
            )
        except APIError:
            raise
        except Exception as e:
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="EMBEDDING_FAILED",
                message=f"Embedding API call failed: {str(e)[:200]}",
            )
