"""
internal/data/embedding/cache.py — Query Embedding LRU 缓存

搜索页对每个知识库各打一次 retrieve，同一句 query 及其 2～3 条
短变体会被嵌 N 次。回答不再二次检索，但仍要挡住这 N 次重复。

文档分块是大批量、内容几乎不重复，缓存收益低且会挤掉 query，因此
只缓存「少量短文本」（query / 变体）。
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

import structlog

from internal.biz.entity import EmbeddingResult, EmbeddingUsage
from internal.biz.repo import EmbeddingProvider

logger = structlog.get_logger()

_DEFAULT_MAX_ENTRIES = 1024
_QUERY_BATCH_LIMIT = 5
_QUERY_MAX_CHARS = 512


class CachedEmbeddingProvider(EmbeddingProvider):
    """装饰 EmbeddingProvider：query 及少量变体命中则跳过上游 API。"""

    def __init__(
        self,
        inner: EmbeddingProvider,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        if max_entries <= 0:
            raise ValueError(f"max_entries must be positive, got {max_entries}")
        self._inner = inner
        self._max_entries = max_entries
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts or not _looks_like_queries(texts):
            return await self._inner.embed(texts)

        vectors: list[list[float] | None] = [None] * len(texts)
        missing_idx: list[int] = []
        async with self._lock:
            for index, text in enumerate(texts):
                cached = self._cache.get(text)
                if cached is not None:
                    self._cache.move_to_end(text)
                    vectors[index] = list(cached)
                else:
                    missing_idx.append(index)

        if not missing_idx:
            logger.debug("embedding_cache_hit", count=len(texts))
            return EmbeddingResult(
                vectors=[item for item in vectors if item is not None],
                model=self._inner.model_name,
                dimension=self._inner.dimension,
                usage=EmbeddingUsage(),
            )

        unique_texts: list[str] = []
        unique_index: dict[str, int] = {}
        for index in missing_idx:
            text = texts[index]
            if text not in unique_index:
                unique_index[text] = len(unique_texts)
                unique_texts.append(text)

        result = await self._inner.embed(unique_texts)
        async with self._lock:
            for text, vector in zip(unique_texts, result.vectors):
                self._put_locked(text, vector)

        for index in missing_idx:
            text = texts[index]
            slot = unique_index[text]
            vectors[index] = list(result.vectors[slot])

        return EmbeddingResult(
            vectors=[item if item is not None else [] for item in vectors],
            model=result.model,
            dimension=result.dimension,
            usage=result.usage,
        )

    def _put_locked(self, text: str, vector: list[float]) -> None:
        self._cache[text] = list(vector)
        self._cache.move_to_end(text)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)


def _looks_like_queries(texts: list[str]) -> bool:
    return len(texts) <= _QUERY_BATCH_LIMIT and all(len(text) <= _QUERY_MAX_CHARS for text in texts)
