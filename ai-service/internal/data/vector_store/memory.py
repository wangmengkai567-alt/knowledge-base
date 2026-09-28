"""
internal/data/vector_store/memory.py — 内存向量存储

纯 Python 实现的暴力相似度搜索，无需外部数据库。
适合小规模数据（< 10K 向量）。
"""

from __future__ import annotations

import math
import structlog

from internal.biz.repo import VectorStore

logger = structlog.get_logger()


class InMemoryVectorStore(VectorStore):
    """内存向量存储 — 暴力余弦相似度搜索。

    向量存储在内存字典中，进程重启数据丢失。
    """

    def __init__(self) -> None:
        # chunk_id → (向量, 元数据)
        self._store: dict[int, tuple[list[float], dict]] = {}

    async def upsert(
        self,
        chunk_ids: list[int],
        vectors: list[list[float]],
        metadatas: list[dict] | None = None,
    ) -> None:
        """插入或更新向量。"""
        for i, cid in enumerate(chunk_ids):
            meta = metadatas[i] if metadatas else {}
            self._store[cid] = (vectors[i], meta)
        logger.debug("memory_vector_upsert", count=len(chunk_ids), total=len(self._store))

    async def delete(self, chunk_ids: list[int]) -> None:
        """删除指定分块的向量。"""
        for cid in chunk_ids:
            self._store.pop(cid, None)
        logger.debug("memory_vector_delete", count=len(chunk_ids), total=len(self._store))

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
    ) -> list[tuple[int, float]]:
        """暴力余弦相似度搜索。

        遍历所有向量计算余弦相似度，按分数降序返回 top_k。
        支持 filter_metadata 过滤（精确匹配 metadata 中的 key-value）。
        """
        if not self._store:
            return []

        results: list[tuple[int, float]] = []
        for cid, (vec, meta) in self._store.items():
            # 过滤条件检查
            if filter_metadata:
                match = all(meta.get(k) == v for k, v in filter_metadata.items())
                if not match:
                    continue

            score = self._cosine_similarity(query_vector, vec)
            results.append((cid, score))

        # 按相似度降序排列，取 top_k
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度。"""
        if len(a) != len(b) or not a:
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)
