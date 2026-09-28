"""
internal/data/user/factory.py — UserRepo 工厂

config 驱动选择实现，当前只有 SQLAlchemy 实现。
后续可扩展其他实现（如 Tortoise ORM）。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from internal.biz.repo import UserRepo
from internal.data.user.sqlalchemy import SQLAlchemyUserRepo


def create_user_repo(session_factory: async_sessionmaker[AsyncSession]) -> UserRepo:
    """创建 UserRepo 实例，返回 biz 层接口类型。"""
    return SQLAlchemyUserRepo(session_factory=session_factory)
