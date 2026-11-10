"""创建使用给定数据库会话工厂的用户仓储。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from internal.biz.repo import UserRepo
from internal.data.user.sqlalchemy import SQLAlchemyUserRepo


def create_user_repo(session_factory: async_sessionmaker[AsyncSession]) -> UserRepo:
    """创建 UserRepo 实例，返回 biz 层接口类型。"""
    return SQLAlchemyUserRepo(session_factory=session_factory)
