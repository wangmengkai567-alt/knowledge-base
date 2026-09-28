"""
internal/data/chunk/sqlalchemy.py — ChunkRepo 的 SQLAlchemy 实现

data 层实现 biz 定义的 ChunkRepo 接口（依赖倒置）。
负责技术细节：SQLAlchemy ORM 操作、PO ↔ DO 转换。
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.entity import Chunk
from internal.biz.repo import ChunkRepo
from internal.data.converter import chunk_do_to_po, chunk_po_to_do
from internal.data.models import ChunkPO


class SQLAlchemyChunkRepo(ChunkRepo):
    """ChunkRepo 的 SQLAlchemy 实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_batch(self, chunks: list[Chunk]) -> list[Chunk]:
        """批量创建分块记录。"""
        if not chunks:
            return []

        async with self._session_factory() as session:
            pos = [chunk_do_to_po(c) for c in chunks]
            session.add_all(pos)
            await session.commit()
            # refresh 获取生成的 id
            for p in pos:
                await session.refresh(p)
            return [chunk_po_to_do(p) for p in pos]

    async def list_by_document(self, document_id: int) -> list[Chunk]:
        """列出文档下所有分块，按 position 排序。"""
        async with self._session_factory() as session:
            stmt = (
                select(ChunkPO)
                .where(ChunkPO.document_id == document_id)
                .order_by(ChunkPO.position.asc())
            )
            result = await session.execute(stmt)
            return [chunk_po_to_do(po) for po in result.scalars().all()]

    async def delete_by_document(self, document_id: int) -> int:
        """删除文档下所有分块，返回删除数量。"""
        async with self._session_factory() as session:
            stmt = delete(ChunkPO).where(ChunkPO.document_id == document_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def replace_for_document(
        self, document_id: int, chunks: list[Chunk]
    ) -> list[Chunk]:
        """原子替换文档的所有分块（先删后建，同一事务）。"""
        async with self._session_factory() as session:
            # 删除旧分块
            await session.execute(
                delete(ChunkPO).where(ChunkPO.document_id == document_id)
            )
            # 插入新分块
            if chunks:
                pos = [chunk_do_to_po(c) for c in chunks]
                session.add_all(pos)
                await session.flush()
                for p in pos:
                    await session.refresh(p)
                await session.commit()
                return [chunk_po_to_do(p) for p in pos]
            await session.commit()
            return []

    async def update_embedding_status(
        self, chunk_ids: list[int], status: int
    ) -> int:
        """批量更新分块的嵌入状态。"""
        if not chunk_ids:
            return 0
        async with self._session_factory() as session:
            stmt = (
                update(ChunkPO)
                .where(ChunkPO.id.in_(chunk_ids))
                .values(embedding_status=status)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount

    async def get_by_ids(self, chunk_ids: list[int]) -> list[Chunk]:
        """根据 ID 列表批量查分块。"""
        if not chunk_ids:
            return []
        async with self._session_factory() as session:
            stmt = (
                select(ChunkPO)
                .where(ChunkPO.id.in_(chunk_ids))
            )
            result = await session.execute(stmt)
            return [chunk_po_to_do(po) for po in result.scalars().all()]

    async def list_all(self) -> list[Chunk]:
        """列出全部分块（用于启动时重建关键词索引等运维场景）。"""
        async with self._session_factory() as session:
            stmt = select(ChunkPO).order_by(ChunkPO.id.asc())
            result = await session.execute(stmt)
            return [chunk_po_to_do(po) for po in result.scalars().all()]
