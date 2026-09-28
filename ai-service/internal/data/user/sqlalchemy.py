"""
internal/data/user/sqlalchemy.py — UserRepo 的 SQLAlchemy 实现

data 层实现 biz 定义的 UserRepo 接口（依赖倒置）。
负责技术细节：SQLAlchemy ORM 操作、PO ↔ DO 转换。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from internal.biz.entity import User
from internal.biz.repo import UserRepo
from internal.data.converter import user_po_to_do
from internal.data.models import UserPO


class SQLAlchemyUserRepo(UserRepo):
    """UserRepo 的 SQLAlchemy 实现。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(self, user: User) -> User:
        async with self._session_factory() as session:
            po = UserPO(
                email=user.email,
                nickname=user.nickname,
                password_hash=user.password_hash,
                status=user.status,
            )
            session.add(po)
            await session.commit()
            await session.refresh(po)
            return user_po_to_do(po)

    async def get_by_email(self, email: str) -> User | None:
        async with self._session_factory() as session:
            stmt = select(UserPO).where(UserPO.email == email)
            result = await session.execute(stmt)
            po = result.scalar_one_or_none()
            return user_po_to_do(po) if po else None

    async def get_by_id(self, user_id: int) -> User | None:
        async with self._session_factory() as session:
            stmt = select(UserPO).where(UserPO.id == user_id)
            result = await session.execute(stmt)
            po = result.scalar_one_or_none()
            return user_po_to_do(po) if po else None

    async def update_last_login(self, user_id: int, login_at: datetime) -> None:
        async with self._session_factory() as session:
            stmt = (
                update(UserPO)
                .where(UserPO.id == user_id)
                .values(last_login_at=login_at)
            )
            await session.execute(stmt)
            await session.commit()
