"""根据数据库配置创建异步引擎和会话工厂；SQLite 不传入连接池容量参数。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from internal.conf.config import DatabaseConfig


def create_engine(config: DatabaseConfig) -> AsyncEngine:
    """按 config.url 创建异步引擎；非 SQLite 连接设置 pool_size 和 max_overflow。"""
    engine_kwargs: dict = {"echo": config.echo}

    # SQLite 不用连接池参数
    if not config.url.startswith("sqlite"):
        engine_kwargs["pool_size"] = config.pool_size
        engine_kwargs["max_overflow"] = config.pool_size

    return create_async_engine(config.url, **engine_kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建会话工厂；每次调用该工厂得到独立会话，提交后保留已加载属性。"""
    return async_sessionmaker(engine, expire_on_commit=False)
