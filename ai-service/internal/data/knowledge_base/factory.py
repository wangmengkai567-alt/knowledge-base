"""
internal/data/knowledge_base/factory.py — KnowledgeBaseRepo 工厂
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.repo import KnowledgeBaseRepo
from internal.data.knowledge_base.sqlalchemy import SQLAlchemyKnowledgeBaseRepo


def create_knowledge_base_repo(
    session_factory: async_sessionmaker[AsyncSession],
) -> KnowledgeBaseRepo:
    """创建 KnowledgeBaseRepo 实例，返回 biz 层接口类型。"""
    return SQLAlchemyKnowledgeBaseRepo(session_factory=session_factory)
