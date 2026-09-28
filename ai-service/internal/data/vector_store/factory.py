"""
internal/data/vector_store/factory.py — VectorStore 工厂

根据配置创建对应的 VectorStore 实例。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from internal.biz.repo import VectorStore
from internal.data.vector_store.memory import InMemoryVectorStore
from internal.data.vector_store.pgvector import PGVectorStore


def create_vector_store(
    provider: str,
    session_factory: async_sessionmaker | None = None,
    dimension: int = 1024,
) -> VectorStore:
    """根据提供商创建 VectorStore 实例。

    参数:
        provider: 提供商名称（memory / pgvector）
        session_factory: SQLAlchemy session 工厂（pgvector 必需）
        dimension: 向量维度

    返回:
        VectorStore 实例
    """
    if provider == "memory":
        return InMemoryVectorStore()

    if provider == "pgvector":
        if session_factory is None:
            raise ValueError("pgvector provider requires session_factory")
        return PGVectorStore(
            session_factory=session_factory,
            dimension=dimension,
        )

    raise ValueError(f"Unsupported vector store provider: '{provider}'")
