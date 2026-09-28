"""cmd/migrate.py — 幂等建表（ORM 表 + 丢掉废弃列 + pgvector）

不走 get_settings()：AppConfig 会校验 LLM API Key，
migrate 只需要数据库 URL。
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import create_async_engine

from internal.data.models import Base
from internal.data.vector_store.pgvector import ensure_pgvector_schema
from internal.server.http import _drop_unused_shell_columns


async def migrate() -> None:
    url = os.environ.get("AI_DATABASE__URL", "").strip()
    if not url:
        raise SystemExit("AI_DATABASE__URL must be set")

    raw_dim = os.environ.get("AI_EMBEDDING__DIMENSION", "1024")
    try:
        dimension = int(raw_dim)
    except ValueError as exc:
        raise SystemExit(f"AI_EMBEDDING__DIMENSION must be an integer, got {raw_dim!r}") from exc

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _drop_unused_shell_columns(engine)
        if engine.dialect.name == "postgresql":
            await ensure_pgvector_schema(engine, dimension=dimension)
        print("Migration completed successfully")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(migrate())
