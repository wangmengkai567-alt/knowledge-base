"""为最多五条、每条不超过 512 字符的嵌入请求提供进程内 LRU 缓存。"""

from __future__ import annotations

import asyncio
from collections import OrderedDict

import structlog

from internal.biz.entity import EmbeddingResult, EmbeddingUsage
from internal.biz.repo import EmbeddingProvider

logger = structlog.get_logger()

_DEFAULT_MAX_ENTRIES = 1024  # 缓存最多保存的文本—向量条目数。
_QUERY_BATCH_LIMIT = 5       # 可进入查询缓存的单批文本数量上限。
_QUERY_MAX_CHARS = 512       # 可进入查询缓存的单条文本字符数上限。


class CachedEmbeddingProvider(EmbeddingProvider):
    """包装嵌入服务，复用短文本向量；不符合查询大小条件的批次直接调用底层服务。"""

    def __init__(
        self,
        inner: EmbeddingProvider,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        """包装 inner 嵌入服务；max_entries 限制可缓存的不同短查询文本数量。"""
        if max_entries <= 0:
            raise ValueError(f"max_entries must be positive, got {max_entries}")
        self._inner = inner
        self._max_entries = max_entries
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def dimension(self) -> int:
        """返回底层嵌入服务实际声明的向量维度。"""
        return self._inner.dimension

    @property
    def model_name(self) -> str:
        """返回底层嵌入模型标识，缓存层不改变模型。"""
        return self._inner.model_name

    # 先复用缓存中的向量，只向上游请求尚未缓存的短查询。
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
        # 同一批次内重复的文本只请求一次，返回时再恢复原来的数量和顺序。
        unique_index: dict[str, int] = {}
        for index in missing_idx:
            text = texts[index]
            if text not in unique_index:
                unique_index[text] = len(unique_texts)
                unique_texts.append(text)

        result = await self._inner.embed(unique_texts)
        # 网络请求在锁外执行，避免其他查询等待整个模型请求结束。
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

    # 在持锁状态下更新缓存，容量不足时移除最久未使用的记录。
    def _put_locked(self, text: str, vector: list[float]) -> None:
        self._cache[text] = list(vector)
        self._cache.move_to_end(text)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)


def _looks_like_queries(texts: list[str]) -> bool:
    """仅允许数量不超过批次上限、且每条字符数不超过文本上限的输入进入查询缓存。"""
    return len(texts) <= _QUERY_BATCH_LIMIT and all(len(text) <= _QUERY_MAX_CHARS for text in texts)
