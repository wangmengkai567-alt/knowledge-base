"""
internal/biz/chunk.py — 文档分块业务逻辑

ChunkBiz 负责：
- chunk_document：对已解析文档的文本进行分块并持久化
- list_chunks：列出文档下所有分块
- delete_chunks：删除文档下所有分块（文档删除时级联调用）

不感知 HTTP（不 import fastapi）、不感知存储（不 import sqlalchemy）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from internal.biz.entity import Chunk
from internal.biz.tokenizer import count_tokens

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from internal.biz.repo import ChunkRepo, TextChunker

logger = structlog.get_logger()


class ChunkBiz:
    """文档分块业务逻辑 — 不感知协议、不感知存储。"""

    def __init__(
        self,
        chunk_repo: ChunkRepo,
        chunker: TextChunker,
        on_chunk_delete: Callable[[int, list[Chunk]], Awaitable[None]],
    ) -> None:
        self._chunk_repo = chunk_repo
        self._chunker = chunker
        self._on_chunk_delete = on_chunk_delete

    async def chunk_document(self, document_id: int, parsed_content: str) -> list[Chunk]:
        """对已解析的文档内容进行分块。

        流程：
        1. 调用分块器切分文本
        2. 清理旧索引，再原子替换分块；后续索引由入库编排执行
        """
        text_chunks = self._chunker.chunk(parsed_content)
        if not text_chunks:
            raise ValueError("解析内容未产生任何分块")

        chunks = [
            Chunk(
                id=None,
                document_id=document_id,
                position=i,
                content=chunk_text,
                token_count=count_tokens(chunk_text),
            )
            for i, chunk_text in enumerate(text_chunks)
        ]

        old_chunks = await self._chunk_repo.list_by_document(document_id)
        if old_chunks:
            await self._on_chunk_delete(document_id, old_chunks)

        created = await self._chunk_repo.replace_for_document(document_id, chunks)

        logger.info(
            "document_chunked",
            document_id=document_id,
            chunk_count=len(created),
            avg_length=sum(len(c.content) for c in created) // len(created),
        )

        return created

    async def list_chunks(self, document_id: int) -> list[Chunk]:
        """列出文档下所有分块，按 position 排序。"""
        return await self._chunk_repo.list_by_document(document_id)

    async def delete_chunks(self, document_id: int) -> int:
        """删除文档下所有分块，返回删除数量。"""
        chunks = await self._chunk_repo.list_by_document(document_id)
        if chunks:
            await self._on_chunk_delete(document_id, chunks)
        return await self._chunk_repo.delete_by_document(document_id)
