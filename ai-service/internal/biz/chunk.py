"""
internal/biz/chunk.py — 文档分块业务逻辑

ChunkBiz 负责：
- chunk_document：对已解析文档的文本进行分块并持久化
- list_chunks：列出文档下所有分块
- delete_chunks：删除文档下所有分块（文档删除时级联调用）

不感知 HTTP（不 import fastapi）、不感知存储（不 import sqlalchemy）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from internal.biz.entity import Chunk
from internal.biz.repo import ChunkRepo, TextChunker
from internal.biz.tokenizer import count_tokens

logger = structlog.get_logger()


class ChunkBiz:
    """文档分块业务逻辑 — 不感知协议、不感知存储。"""

    def __init__(
        self,
        chunk_repo: ChunkRepo,
        chunker: TextChunker,
        on_chunk_success: Callable[[int, list[Chunk]], Awaitable[None]] | None = None,
        on_chunk_delete: Callable[[int, list[Chunk]], Awaitable[None]] | None = None,
    ) -> None:
        self._chunk_repo = chunk_repo
        self._chunker = chunker
        self._on_chunk_success = on_chunk_success
        self._on_chunk_delete = on_chunk_delete

    async def chunk_document(self, document_id: int, parsed_content: str) -> list[Chunk]:
        """对已解析的文档内容进行分块。

        流程：
        1. 调用分块器切分文本
        2. 原子替换分块并触发 Embedding
        """
        if not parsed_content or not parsed_content.strip():
            logger.warning("empty_content_skip_chunking", document_id=document_id)
            return []

        text_chunks = self._chunker.chunk(parsed_content)
        if not text_chunks:
            logger.warning("no_chunks_produced", document_id=document_id)
            return []

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
        if old_chunks and self._on_chunk_delete is not None:
            try:
                await self._on_chunk_delete(document_id, old_chunks)
            except Exception as e:
                logger.warning(
                    "pre_replace_embedding_delete_failed",
                    document_id=document_id,
                    error=str(e),
                )

        created = await self._chunk_repo.replace_for_document(document_id, chunks)

        logger.info(
            "document_chunked",
            document_id=document_id,
            chunk_count=len(created),
            avg_length=sum(len(c.content) for c in created) // len(created),
        )

        if self._on_chunk_success is not None:
            try:
                await self._on_chunk_success(document_id, created)
            except Exception as e:
                logger.warning(
                    "auto_embed_failed",
                    document_id=document_id,
                    error=str(e),
                )

        return created

    async def list_chunks(self, document_id: int) -> list[Chunk]:
        """列出文档下所有分块，按 position 排序。"""
        return await self._chunk_repo.list_by_document(document_id)

    async def delete_chunks(self, document_id: int) -> int:
        """删除文档下所有分块，返回删除数量。"""
        chunks = await self._chunk_repo.list_by_document(document_id)
        deleted = await self._chunk_repo.delete_by_document(document_id)
        if self._on_chunk_delete is not None and chunks:
            try:
                await self._on_chunk_delete(document_id, chunks)
            except Exception as e:
                logger.warning(
                    "embedding_delete_failed",
                    document_id=document_id,
                    error=str(e),
                )
        return deleted
