"""通过 SQLAlchemy 读写文档正文、解析状态和入库步骤，并聚合分块计数。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update

from internal.biz.constants import DocumentStatus, EmbeddingStatus
from internal.biz.repo import DocumentRepo
from internal.data.converter import document_do_to_po, document_po_to_do
from internal.data.models import ChunkPO, DocumentPO

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from internal.biz.entity import Document



class SQLAlchemyDocumentRepo(DocumentRepo):
    """DocumentRepo 的 SQLAlchemy 实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """保存异步数据库会话工厂；每次仓储操作通过它创建独立会话。"""
        self._session_factory = session_factory

    async def create(self, document: Document) -> Document:
        """插入文档记录并提交，刷新数据库生成的 ID 和时间后返回领域对象。"""
        async with self._session_factory() as session:
            po = document_do_to_po(document)
            session.add(po)
            await session.commit()
            await session.refresh(po)
            return document_po_to_do(po)

    async def get_by_id(self, document_id: int) -> Document | None:
        """按 ID 查询文档并补充分块/向量完成数量；不存在时返回 None。"""
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
        """返回文档记录引用过的非空知识库 ID，去除重复项。"""
        async with self._session_factory() as session:
            stmt = select(DocumentPO.knowledge_base_id).distinct()
            result = await session.execute(stmt)
            return [row[0] for row in result.all() if row[0] is not None]

    async def list_by_kb(self, knowledge_base_id: int) -> list[Document]:
        """按创建时间倒序列出知识库文档，并附上分块/向量完成数量。"""
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
        """删除指定文档记录并提交；返回是否实际删除了记录，不负责清理文件和索引。"""
        async with self._session_factory() as session:
            stmt = delete(DocumentPO).where(DocumentPO.id == document_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def list_all(self) -> list[Document]:
        """按 ID 顺序返回全部文档，供启动恢复和索引重建遍历。"""
        async with self._session_factory() as session:
            rows = await session.execute(select(DocumentPO).order_by(DocumentPO.id))
            return [document_po_to_do(po) for po in rows.scalars()]

    async def update_stages(self, document_id: int, stages: dict) -> None:
        # 阶段状态变化不改正文版本时间，避免只更新处理进度就使已有引用失效。
        """保存各入库阶段状态，保持正文版本时间 updated_at 不变。"""
        async with self._session_factory() as session:
            await session.execute(
                update(DocumentPO).where(DocumentPO.id == document_id).values(stages=stages, updated_at=DocumentPO.updated_at)
            )
            await session.commit()

    async def update_status(
        self, document_id: int, status: int, error_message: str | None = None
    ) -> None:
        """更新文档状态和错误信息；进入解析处理中时清除旧解析正文。"""
        async with self._session_factory() as session:
            values: dict = {"status": status, "error_message": error_message}
            if status == DocumentStatus.PROCESSING:
                # 重新解析后旧正文不再有效，需等待本轮解析结果写入。
                values["parsed_content"] = None
            stmt = (
                update(DocumentPO)
                .where(DocumentPO.id == document_id)
                .values(**values)
            )
            await session.execute(stmt)
            await session.commit()

    async def update_parsed_content(self, document_id: int, parsed_content: str) -> None:
        """写入解析正文，将文档标为已解析并清除原错误信息。"""
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
