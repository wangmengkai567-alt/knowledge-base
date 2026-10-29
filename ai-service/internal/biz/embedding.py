"""向量与关键词独立维护；向量按批记录成功，重试不重算已完成分块。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from internal.biz.constants import EmbeddingStatus
from internal.biz.table_meta import index_metadata

if TYPE_CHECKING:
    from internal.biz.entity import Chunk
    from internal.biz.repo import (
        ChunkRepo,
        DocumentRepo,
        EmbeddingProvider,
        KeywordStore,
        VectorStore,
    )


class EmbeddingBiz:
    def __init__(
        self,
        chunk_repo: ChunkRepo,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        document_repo: DocumentRepo,
        batch_size: int = 32,
        keyword_store: KeywordStore | None = None,
    ) -> None:
        self._chunk_repo = chunk_repo
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._document_repo = document_repo
        self._batch_size = batch_size
        self._keyword_store = keyword_store

    @property
    def keyword_required(self) -> bool:
        return self._keyword_store is not None

    async def _index_inputs(self, document_id: int, chunks: list[Chunk]) -> tuple[list, list, list]:
        doc = await self._document_repo.get_by_id(document_id)
        if doc is None:
            raise ValueError("索引所属文档不存在")
        if not chunks or any(c.id is None or not c.content.strip() for c in chunks):
            raise ValueError("索引需要非空且已持久化的分块")
        return (
            [c.id for c in chunks],
            [f"{doc.filename}\n{c.content}" for c in chunks],
            [
                {
                    "document_id": document_id,
                    "knowledge_base_id": doc.knowledge_base_id,
                    "position": c.position,
                    **index_metadata(c.content),
                }
                for c in chunks
            ],
        )

    async def reconcile_vectors(self, document_id: int, chunks: list[Chunk]) -> bool:
        """核对真实向量及归属，不根据旧的 COMPLETED 标记推断存在。"""
        doc = await self._document_repo.get_by_id(document_id)
        if doc is None or not chunks:
            return False
        ids = [c.id for c in chunks]
        existing = await self._vector_store.existing_ids(ids, document_id, doc.knowledge_base_id)
        for status, selected in (
            (EmbeddingStatus.COMPLETED, [c.id for c in chunks if c.id in existing]),
            (EmbeddingStatus.FAILED, [c.id for c in chunks if c.id not in existing]),
        ):
            if selected:
                await self._chunk_repo.update_embedding_status(selected, status)
        return len(existing) == len(chunks)

    async def embed_document(self, document_id: int, chunks: list[Chunk]) -> int:
        pending = [c for c in chunks if c.embedding_status != EmbeddingStatus.COMPLETED]
        processed = 0
        errors = []
        for start in range(0, len(pending), self._batch_size):
            batch = pending[start : start + self._batch_size]
            ids = [c.id for c in batch]
            try:
                ids, texts, metadata = await self._index_inputs(document_id, batch)
                result = await self._embedding_provider.embed(texts)
                if len(result.vectors) != len(ids):
                    raise ValueError("嵌入结果数量与分块不符")
                await self._vector_store.upsert(ids, result.vectors, metadata)
                await self._chunk_repo.update_embedding_status(ids, EmbeddingStatus.COMPLETED)
                processed += len(batch)
            except Exception as exc:
                await self._chunk_repo.update_embedding_status(ids, EmbeddingStatus.FAILED)
                errors.append(str(exc))
        if errors:
            raise RuntimeError("；".join(errors)[:1000])
        return processed

    async def index_keywords(self, document_id: int, chunks: list[Chunk]) -> None:
        if self._keyword_store is None:
            raise ValueError("当前检索配置不需要关键词索引")
        ids, texts, metadata = await self._index_inputs(document_id, chunks)
        await self._keyword_store.index(ids, texts, metadata)
        doc = await self._document_repo.get_by_id(document_id)
        existing = await self._keyword_store.existing_ids(ids, document_id, doc.knowledge_base_id)
        if set(ids) != existing:
            raise ValueError("关键词索引未覆盖当前全部分块")

    async def delete_embeddings(self, document_id: int, chunks: list[Chunk]) -> int:
        ids = [c.id for c in chunks if c.id is not None]
        await self._vector_store.delete(ids)
        if self._keyword_store is not None:
            await self._keyword_store.delete(ids)
        return len(ids)
