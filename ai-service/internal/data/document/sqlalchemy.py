"""
internal/data/document/sqlalchemy.py — DocumentRepo 的 SQLAlchemy 实现

data 层实现 biz 定义的 DocumentRepo 接口（依赖倒置）。
负责技术细节：SQLAlchemy ORM 操作、PO ↔ DO 转换。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.constants import DocumentStatus, EmbeddingStatus
from internal.biz.entity import Document
from internal.biz.repo import DocumentRepo
from internal.data.converter import document_do_to_po, document_po_to_do
from internal.data.models import ChunkPO, DocumentPO


class SQLAlchemyDocumentRepo(DocumentRepo):
    """DocumentRepo 的 SQLAlchemy 实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, document: Document) -> Document:
        async with self._session_factory() as session:
            po = document_do_to_po(document)
            session.add(po)
            await session.commit()
            await session.refresh(po)
            return document_po_to_do(po)

    async def get_by_id(self, document_id: int) -> Document | None:
        async with self._session_factory() as session:
            stmt = select(DocumentPO).where(DocumentPO.id == document_id)
            result = await session.execute(stmt)
            po = result.scalar_one_or_none()
            if po is None:
                return None
            doc = document_po_to_do(po)
        await self._attach_chunk_stats([doc])
        return doc

    async def get_by_ids(self, document_ids: list[int]) -> list[Document]:
        """根据 ID 列表批量查文档。"""
        if not document_ids:
            return []
        async with self._session_factory() as session:
            stmt = select(DocumentPO).where(DocumentPO.id.in_(document_ids))
            result = await session.execute(stmt)
            docs = [document_po_to_do(po) for po in result.scalars().all()]
        await self._attach_chunk_stats(docs)
        return docs

    async def list_distinct_kb_ids(self) -> list[int]:
        async with self._session_factory() as session:
            stmt = select(DocumentPO.knowledge_base_id).distinct()
            result = await session.execute(stmt)
            return [row[0] for row in result.all() if row[0] is not None]

    async def list_by_kb(self, knowledge_base_id: int) -> list[Document]:
        async with self._session_factory() as session:
            stmt = (
                select(DocumentPO)
                .where(DocumentPO.knowledge_base_id == knowledge_base_id)
                .order_by(DocumentPO.created_at.desc())
            )
            result = await session.execute(stmt)
            docs = [document_po_to_do(po) for po in result.scalars().all()]

        await self._attach_chunk_stats(docs)
        return docs

    async def _attach_chunk_stats(self, docs: list[Document]) -> None:
        """聚合分块数与各嵌入状态计数。"""
        if not docs:
            return
        doc_ids = [d.id for d in docs if d.id is not None]
        if not doc_ids:
            return
        async with self._session_factory() as session:
            stmt = (
                select(ChunkPO.document_id, ChunkPO.embedding_status, func.count())
                .where(ChunkPO.document_id.in_(doc_ids))
                .group_by(ChunkPO.document_id, ChunkPO.embedding_status)
            )
            result = await session.execute(stmt)
            rows = result.all()

        pending: dict[int, int] = {}
        failed: dict[int, int] = {}
        completed: dict[int, int] = {}
        totals: dict[int, int] = {}
        for doc_id, status, count in rows:
            n = int(count)
            totals[doc_id] = totals.get(doc_id, 0) + n
            if status == EmbeddingStatus.PENDING:
                pending[doc_id] = n
            elif status == EmbeddingStatus.FAILED:
                failed[doc_id] = n
            elif status == EmbeddingStatus.COMPLETED:
                completed[doc_id] = n

        for d in docs:
            if d.id is None:
                continue
            d.chunk_count = totals.get(d.id, 0)
            d.embedding_pending_count = pending.get(d.id, 0)
            d.embedding_failed_count = failed.get(d.id, 0)
            d.embedding_completed_count = completed.get(d.id, 0)

    async def delete(self, document_id: int) -> bool:
        async with self._session_factory() as session:
            stmt = delete(DocumentPO).where(DocumentPO.id == document_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def update_status(
        self, document_id: int, status: int, error_message: str | None = None
    ) -> None:
        async with self._session_factory() as session:
            values: dict = {"status": status}
            if error_message is not None:
                values["error_message"] = error_message
            stmt = (
                update(DocumentPO)
                .where(DocumentPO.id == document_id)
                .values(**values)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_parsed_content(self, document_id: int, parsed_content: str) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(DocumentPO)
                .where(DocumentPO.id == document_id)
                .values(
                    parsed_content=parsed_content,
                    status=DocumentStatus.PARSED,
                    error_message=None,
                )
            )
            await session.execute(stmt)
            await session.commit()
