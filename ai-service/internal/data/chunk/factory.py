"""
internal/data/chunk/factory.py — ChunkRepo 工厂

config 驱动选择实现，当前只有 SQLAlchemy 实现。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.repo import ChunkRepo
from internal.data.chunk.sqlalchemy import SQLAlchemyChunkRepo


def create_chunk_repo(session_factory: async_sessionmaker[AsyncSession]) -> ChunkRepo:
    """创建 ChunkRepo 实例，返回 biz 层接口类型。"""
    return SQLAlchemyChunkRepo(session_factory=session_factory)
