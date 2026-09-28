"""
internal/data/knowledge_base/sqlalchemy.py — KnowledgeBaseRepo 的 SQLAlchemy 实现

data 层实现 biz 定义的 KnowledgeBaseRepo 接口（依赖倒置）。
只负责知识库表的增删改查，以及列表所需的文档数 / 分块数统计。
领域对象 DO 与表映射 PO 的转换走 converter，本文件不写业务规则。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.entity import KnowledgeBase
from internal.biz.repo import KnowledgeBaseRepo
from internal.data.converter import knowledge_base_do_to_po, knowledge_base_po_to_do
from internal.data.models import ChunkPO, DocumentPO, KnowledgeBasePO


class SQLAlchemyKnowledgeBaseRepo(KnowledgeBaseRepo):
    """KnowledgeBaseRepo 的 SQLAlchemy 异步实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, kb: KnowledgeBase) -> KnowledgeBase:
        """插入一条知识库；带主键时需同步 Postgres 序列。"""
        async with self._session_factory() as session:
            po = knowledge_base_do_to_po(kb)
            session.add(po)
            await session.commit()
            # 回填默认库会显式指定 id=1；不把序列推到 MAX(id)，下次自增仍从 1 开始会撞主键
            if kb.id is not None:
                await self._sync_id_sequence(session)
            await session.refresh(po)
            return knowledge_base_po_to_do(po)

    async def get_by_id(self, kb_id: int) -> KnowledgeBase | None:
        """按主键查询；不存在返回 None。"""
        async with self._session_factory() as session:
            stmt = select(KnowledgeBasePO).where(KnowledgeBasePO.id == kb_id)
            result = await session.execute(stmt)
            po = result.scalar_one_or_none()
            if po is None:
                return None
            kb = knowledge_base_po_to_do(po)
        await self._attach_counts([kb])
        return kb

    async def list_all(self) -> list[KnowledgeBase]:
        """列出全部知识库，按创建时间倒序。"""
        async with self._session_factory() as session:
            stmt = select(KnowledgeBasePO).order_by(KnowledgeBasePO.created_at.desc())
            result = await session.execute(stmt)
            kbs = [knowledge_base_po_to_do(po) for po in result.scalars().all()]
        await self._attach_counts(kbs)
        return kbs

    async def list_ids(self) -> list[int]:
        """只取已有知识库 id，供 Biz 对比 documents 里出现过的 kb id 并回填。"""
        async with self._session_factory() as session:
            stmt = select(KnowledgeBasePO.id)
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def update(self, kb: KnowledgeBase) -> KnowledgeBase:
        """更新名称 / 描述 / 图标 / 状态，再按 id 读回（带最新计数）。"""
        if kb.id is None:
            raise ValueError("knowledge base id is required for update")
        async with self._session_factory() as session:
            stmt = (
                update(KnowledgeBasePO)
                .where(KnowledgeBasePO.id == kb.id)
                .values(
                    name=kb.name,
                    description=kb.description,
                    icon=kb.icon,
                    status=int(kb.status),
                )
            )
            await session.execute(stmt)
            await session.commit()
        updated = await self.get_by_id(kb.id)
        if updated is None:
            raise ValueError(f"knowledge base {kb.id} disappeared after update")
        return updated

    async def delete(self, kb_id: int) -> bool:
        """删除知识库行本身。文档 / 分块 / 向量由 Biz 先级联清掉。"""
        async with self._session_factory() as session:
            stmt = delete(KnowledgeBasePO).where(KnowledgeBasePO.id == kb_id)
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def _attach_counts(self, kbs: list[KnowledgeBase]) -> None:
        """一次查出各库文档数与分块数，避免前端再扇出 documents 接口。"""
        if not kbs:
            return
        kb_ids = [kb.id for kb in kbs if kb.id is not None]
        if not kb_ids:
            return
        async with self._session_factory() as session:
            doc_stmt = (
                select(DocumentPO.knowledge_base_id, func.count())
                .where(DocumentPO.knowledge_base_id.in_(kb_ids))
                .group_by(DocumentPO.knowledge_base_id)
            )
            doc_result = await session.execute(doc_stmt)
            doc_counts = {kb_id: count for kb_id, count in doc_result.all()}

            chunk_stmt = (
                select(DocumentPO.knowledge_base_id, func.count())
                .join(ChunkPO, ChunkPO.document_id == DocumentPO.id)
                .where(DocumentPO.knowledge_base_id.in_(kb_ids))
                .group_by(DocumentPO.knowledge_base_id)
            )
            chunk_result = await session.execute(chunk_stmt)
            chunk_counts = {kb_id: count for kb_id, count in chunk_result.all()}

        for kb in kbs:
            if kb.id is None:
                continue
            kb.doc_count = int(doc_counts.get(kb.id, 0))
            kb.chunk_count = int(chunk_counts.get(kb.id, 0))

    async def _sync_id_sequence(self, session: AsyncSession) -> None:
        """显式插入 id 后对齐自增序列，避免 PostgreSQL 下次 INSERT 撞主键。"""
        bind = session.get_bind()
        dialect = bind.dialect.name if bind is not None else ""
        if dialect != "postgresql":
            return
        await session.execute(
            text(
                "SELECT setval("
                "pg_get_serial_sequence('knowledge_bases', 'id'), "
                "COALESCE((SELECT MAX(id) FROM knowledge_bases), 1)"
                ")"
            )
        )
        await session.commit()
