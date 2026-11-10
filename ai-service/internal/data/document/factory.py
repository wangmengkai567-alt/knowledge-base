"""创建使用给定数据库会话工厂的文档仓储。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.repo import DocumentRepo
from internal.data.document.sqlalchemy import SQLAlchemyDocumentRepo


def create_document_repo(session_factory: async_sessionmaker[AsyncSession]) -> DocumentRepo:
    """创建 DocumentRepo 实例，返回 biz 层接口类型。"""
    return SQLAlchemyDocumentRepo(session_factory=session_factory)
