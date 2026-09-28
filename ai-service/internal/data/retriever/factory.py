"""
internal/data/retriever/factory.py — Retriever 工厂

根据配置创建对应的 Retriever 实例：
- vector：纯向量检索（Sprint 8 默认）
- hybrid：向量 + 关键词 RRF 融合（Sprint 18）
"""

from __future__ import annotations

from internal.biz.repo import KeywordStore, Retriever, VectorStore
from internal.data.retriever.hybrid import HybridRetriever
from internal.data.retriever.vector import VectorRetriever


def create_retriever(
    strategy: str = "vector",
    vector_store: VectorStore | None = None,
    keyword_store: KeywordStore | None = None,
    rrf_k: int = 60,
    candidate_multiplier: int = 2,
) -> Retriever:
    """根据策略创建 Retriever 实例。"""
    if strategy == "vector":
        if vector_store is None:
            raise ValueError("Retriever strategy 'vector' requires vector_store")
        return VectorRetriever(vector_store=vector_store)

    if strategy == "hybrid":
        if vector_store is None:
            raise ValueError("Retriever strategy 'hybrid' requires vector_store")
        if keyword_store is None:
            raise ValueError("Retriever strategy 'hybrid' requires keyword_store")
        return HybridRetriever(
            vector_store=vector_store,
            keyword_store=keyword_store,
            rrf_k=rrf_k,
            candidate_multiplier=candidate_multiplier,
        )

    raise ValueError(f"Unsupported retriever strategy: '{strategy}'")
