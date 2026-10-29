"""显式回填无阶段记录的存量文档；启动核实不再推断当前轮次完成。"""

from sqlalchemy import Text, cast, inspect, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from internal.biz.constants import DocumentStatus
from internal.biz.ingestion_state import STAGES, stage_state
from internal.data.models import ChunkPO, DocumentPO


async def migrate_ingestion_stages(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns("documents"))
        if "stages" not in {column["name"] for column in columns}:
            await conn.execute(
                text("ALTER TABLE documents ADD COLUMN stages JSON NOT NULL DEFAULT '{}'")
            )
        # 仅显式迁移可核实旧系统的无阶段记录，不能覆盖已有处理轮次的 pending/failed。
        documents = (
            (
                await conn.execute(
                    select(
                        DocumentPO.id,
                        DocumentPO.status,
                        DocumentPO.parsed_content,
                        DocumentPO.error_message,
                    ).where(cast(DocumentPO.stages, Text) == "{}")
                )
            )
            .mappings()
            .all()
        )
        for doc in documents:
            stages = {name: stage_state("pending") for name in STAGES}
            if (
                doc.status == DocumentStatus.PARSED
                and doc.parsed_content
                and doc.parsed_content.strip()
            ):
                stages["parse"] = stage_state("completed")
                chunks = (
                    await conn.execute(
                        select(ChunkPO.position, ChunkPO.content)
                        .where(ChunkPO.document_id == doc.id)
                        .order_by(ChunkPO.position)
                    )
                ).all()
                if (
                    chunks
                    and [c.position for c in chunks] == list(range(len(chunks)))
                    and all(c.content.strip() for c in chunks)
                ):
                    stages["chunk"] = stage_state("completed")
                else:
                    stages["chunk"] = stage_state("failed", "存量分块未通过核实，请重试分块")
            elif doc.status in (
                DocumentStatus.ERROR,
                DocumentStatus.PROCESSING,
                DocumentStatus.PARSED,
            ):
                stages["parse"] = stage_state(
                    "failed", doc.error_message or "存量解析未完成或缺少有效文本"
                )
            await conn.execute(
                update(DocumentPO)
                .where(DocumentPO.id == doc.id)
                .values(stages=stages, updated_at=DocumentPO.updated_at)
            )
