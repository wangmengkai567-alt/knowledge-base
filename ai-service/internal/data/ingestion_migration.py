"""第五阶段显式迁移。新增状态先保持未核实，启动编排再以真实索引回填。"""

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine


async def migrate_ingestion_stages(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns("documents"))
        if "stages" not in {column["name"] for column in columns}:
            await conn.execute(
                text("ALTER TABLE documents ADD COLUMN stages JSON NOT NULL DEFAULT '{}'")
            )
