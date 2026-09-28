"""
internal/data/database.py — SQLAlchemy 异步引擎 & Session 工厂

data 层基础设施 — 提供 AsyncEngine 和 async_sessionmaker。
改 URL 即可在 SQLite 与 PostgreSQL 之间切换。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from internal.conf.config import DatabaseConfig


def create_engine(config: DatabaseConfig) -> AsyncEngine:
    """创建 SQLAlchemy AsyncEngine。

    SQLite 和 PostgreSQL 的连接池参数不同：
    - SQLite 不支持 pool_size（用 NullPool 或 StaticPool）
    - PostgreSQL 支持 pool_size
    """
    engine_kwargs: dict = {"echo": config.echo}

    # SQLite 不用连接池参数
    if not config.url.startswith("sqlite"):
        engine_kwargs["pool_size"] = config.pool_size
        engine_kwargs["max_overflow"] = config.pool_size

    return create_async_engine(config.url, **engine_kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建 async_sessionmaker — 每个请求独立 session。"""
    return async_sessionmaker(engine, expire_on_commit=False)
