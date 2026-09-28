"""
internal/data/document/factory.py — DocumentRepo 工厂

config 驱动选择实现，当前只有 SQLAlchemy 实现。
后续可扩展其他实现（如 MongoDB）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.repo import DocumentRepo
from internal.data.document.sqlalchemy import SQLAlchemyDocumentRepo


def create_document_repo(session_factory: async_sessionmaker[AsyncSession]) -> DocumentRepo:
    """创建 DocumentRepo 实例，返回 biz 层接口类型。"""
    return SQLAlchemyDocumentRepo(session_factory=session_factory)
