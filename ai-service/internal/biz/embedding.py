"""
internal/biz/embedding.py — 文本嵌入业务逻辑

EmbeddingBiz 负责：
- embed_document：对文档的所有分块进行嵌入（调 EmbeddingProvider → 存 VectorStore）
- delete_embeddings：删除文档的向量（文档删除时级联调用）

"""

from __future__ import annotations

import structlog

from internal.biz.constants import EmbeddingStatus
from internal.biz.entity import Chunk
from internal.biz.repo import (
    ChunkRepo,
    DocumentRepo,
    EmbeddingProvider,
    KeywordStore,
    VectorStore,
)
from internal.biz.table_meta import index_metadata


logger = structlog.get_logger()


class EmbeddingBiz:
    """文本嵌入业务逻辑 — 不感知协议、不感知存储。"""

    def __init__(
        self,
        chunk_repo: ChunkRepo,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        batch_size: int = 32,
        keyword_store: KeywordStore | None = None,
        document_repo: DocumentRepo | None = None,
    ) -> None:
        self._chunk_repo = chunk_repo
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._batch_size = batch_size
        # None 时所有 keyword 相关操作跳过（零运行时开销）。
        self._keyword_store = keyword_store
        # Sprint 18-fix (P0)：用于向 metadata 注入 knowledge_base_id，
        # 让 Retriever 层能做 kb 隔离过滤（防止跨知识库召回）。
        # None 时不写 kb_id（无法按知识库隔离向量）。
        self._document_repo = document_repo

    async def embed_document(self, document_id: int, chunks: list[Chunk]) -> int:
        """对文档的所有分块进行嵌入。

        流程：
        1. 过滤出待嵌入的 chunks（PENDING 或 FAILED）
        2. 按 batch_size 分批调用 EmbeddingProvider
        3. 将向量存入 VectorStore
        4. 更新 chunks 的 embedding_status 为 COMPLETED

        返回:
            成功嵌入的 chunk 数量
        """
        if not chunks:
            logger.warning("no_chunks_to_embed", document_id=document_id)
            return 0

        # 过滤待嵌入的 chunks（PENDING 首次嵌入；FAILED 允许重试）
        pending_chunks = [
            c for c in chunks
            if c.embedding_status in (EmbeddingStatus.PENDING, EmbeddingStatus.FAILED)
            and c.id is not None
        ]
        if not pending_chunks:
            logger.info("all_chunks_already_embedded", document_id=document_id)
            return 0

        # 文档标题/文件名拼接到待索引文本：让"标题命中"也能被向量与 BM25 检索到。
        # 例：搜 "agent" 时 "Agent进入深水区后的十大趋势.md" 正文是中文、无 "agent" 字面词，
        # 但标题含 Agent——若不把标题纳入索引，该文档根本进不了候选集（用户反馈 case）。
        # 仅用于建索引（向量/BM25），不影响返回给前端的 chunk.content 展示。
        doc_filename = ""
        if self._document_repo is not None:
            try:
                doc = await self._document_repo.get_by_id(document_id)
                doc_filename = doc.filename if doc else ""
            except Exception as e:
                logger.warning("resolve_doc_filename_failed", document_id=document_id, error=str(e))
        texts = [f"{doc_filename}\n{c.content}" if doc_filename else c.content for c in pending_chunks]
        chunk_ids = [c.id for c in pending_chunks]

        try:
            # 分批嵌入
            all_vectors: list[list[float]] = []
            for i in range(0, len(texts), self._batch_size):
                batch_texts = texts[i:i + self._batch_size]
                batch_result = await self._embedding_provider.embed(batch_texts)
                all_vectors.extend(batch_result.vectors)

            # 存入向量库
            # 让 Retriever 能按 kb 过滤，实现多知识库隔离。
            kb_id = await self._resolve_kb_id(document_id)
            metadatas = [
                {
                    "document_id": c.document_id,
                    "position": c.position,
                    **({"knowledge_base_id": kb_id} if kb_id is not None else {}),
                    **index_metadata(c.content),
                }
                for c in pending_chunks
            ]
            await self._vector_store.upsert(
                chunk_ids=chunk_ids,
                vectors=all_vectors,
                metadatas=metadatas,
            )

            # 同步写入关键词索引（混合检索使用）
            # 关键词索引写入失败不影响向量库，降级为纯向量检索。
            if self._keyword_store is not None:
                try:
                    await self._keyword_store.index(
                        chunk_ids=chunk_ids,
                        texts=texts,
                        metadatas=metadatas,
                    )
                except Exception as e:
                    logger.warning(
                        "keyword_index_failed",
                        document_id=document_id,
                        error=str(e),
                        exc_info=True,
                    )

            # 更新嵌入状态为 COMPLETED
            await self._chunk_repo.update_embedding_status(
                chunk_ids=chunk_ids,
                status=EmbeddingStatus.COMPLETED,
            )

            logger.info(
                "document_embedded",
                document_id=document_id,
                chunk_count=len(pending_chunks),
                dimension=self._embedding_provider.dimension,
                model=self._embedding_provider.model_name,
            )
            return len(pending_chunks)

        except Exception as e:
            # 嵌入失败：更新状态为 FAILED
            logger.error(
                "embedding_failed",
                document_id=document_id,
                error=str(e),
            )
            await self._chunk_repo.update_embedding_status(
                chunk_ids=chunk_ids,
                status=EmbeddingStatus.FAILED,
            )
            raise

    async def retry_document_embeddings(self, document_id: int) -> int:
        """对文档已有分块重试嵌入（PENDING + FAILED）。不重新解析、不分块。"""
        chunks = await self._chunk_repo.list_by_document(document_id)
        return await self.embed_document(document_id, chunks)

    async def _resolve_kb_id(self, document_id: int) -> int | None:
        """查询文档所属知识库 ID，用于写入 metadata。

        失败降级：查不到或未注入 document_repo 时返回 None，
        upsert 的 metadata 不写 kb_id（此时 Retriever 层的 kb 过滤会跳过该 chunk，
        属于安全默认——宁可漏召回也不跨 kb 泄漏）。
        """
        if self._document_repo is None:
            return None
        try:
            doc = await self._document_repo.get_by_id(document_id)
        except Exception as e:
            logger.warning(
                "resolve_kb_id_failed",
                document_id=document_id,
                error=str(e),
            )
            return None
        if doc is None:
            logger.warning("resolve_kb_id_document_missing", document_id=document_id)
            return None
        return doc.knowledge_base_id

    async def delete_embeddings(self, document_id: int, chunks: list[Chunk]) -> int:
        """删除文档的所有向量。

        参数:
            document_id: 文档 ID
            chunks: 文档下的分块列表

        返回:
            删除的向量数量
        """
        chunk_ids = [c.id for c in chunks if c.id is not None]
        if not chunk_ids:
            return 0

        try:
            await self._vector_store.delete(chunk_ids)
            # Sprint 18：同步清理关键词索引
            if self._keyword_store is not None:
                try:
                    await self._keyword_store.delete(chunk_ids)
                except Exception as e:
                    logger.warning(
                        "keyword_index_delete_failed",
                        document_id=document_id,
                        error=str(e),
                    )
            logger.info(
                "embeddings_deleted",
                document_id=document_id,
                count=len(chunk_ids),
            )
            return len(chunk_ids)
        except Exception as e:
            logger.warning(
                "embedding_delete_failed",
                document_id=document_id,
                error=str(e),
            )
            return 0

    async def rebuild_indices(self, include_vectors: bool = True) -> int:
        """启动时重建内存索引（vector store + keyword index）。

        memory 向量库与 BM25 关键词索引均为进程内易失结构，进程重启后会清空。
        而文档分块的文本内容持久化在数据库（sqlite/pg）中，因此可从 DB 重新载入
        全部分块并重建两路索引，使检索在重启后依然可用。否则会出现
        "未找到相关结果"（Sprint 18 修复：此前只重建关键词索引、未重建向量库，
        导致 dev 的 memory 向量库重启后为空，检索两路皆空）。

        参数:
            include_vectors: 是否重建向量库。
                - 仅 memory 向量库需要（pgvector 持久化、重启后仍在，无需重建）。
                - 关键词索引（内存结构）始终重建。

        返回:
            成功建索引的 chunk 数量。容错：无分块 / 异常均安全返回 0，不抛出。
        """
        try:
            chunks = await self._chunk_repo.list_all()
        except Exception as e:
            logger.warning("index_rebuild_load_failed", error=str(e))
            return 0

        if not chunks:
            logger.info("index_rebuild_no_chunks")
            return 0

        # 文档 → 知识库 ID / 文件名 映射，写入 metadata 以便检索时按 kb 过滤；
        # 文件名同时拼入待索引文本，让"标题命中"也能被向量与 BM25 检索到。
        doc_ids = list({c.document_id for c in chunks if c.id is not None})
        kb_map: dict[int, int] = {}
        filename_map: dict[int, str] = {}
        if doc_ids and self._document_repo is not None:
            try:
                docs = await self._document_repo.get_by_ids(doc_ids)
                kb_map = {d.id: d.knowledge_base_id for d in docs}
                filename_map = {d.id: d.filename for d in docs}
            except Exception as e:
                logger.warning("index_rebuild_kb_resolve_failed", error=str(e))

        # 收集需要建索引的 chunk（内容与 ID 均有效）
        chunk_ids: list[int] = []
        texts: list[str] = []
        metadatas: list[dict] = []
        for c in chunks:
            if c.id is None or not c.content:
                continue
            chunk_ids.append(c.id)
            fname = filename_map.get(c.document_id, "")
            texts.append(f"{fname}\n{c.content}" if fname else c.content)
            metadatas.append({
                "document_id": c.document_id,
                "knowledge_base_id": kb_map.get(c.document_id),
                **index_metadata(c.content),
            })

        if not chunk_ids:
            return 0

        # 分批重建：逐批嵌入 + 写向量库 + 写关键词索引。
        # 单批失败不影响其余批次（容错），确保启动健壮。
        processed = 0
        for i in range(0, len(chunk_ids), self._batch_size):
            batch_ids = chunk_ids[i:i + self._batch_size]
            batch_texts = texts[i:i + self._batch_size]
            batch_meta = metadatas[i:i + self._batch_size]
            try:
                if include_vectors:
                    result = await self._embedding_provider.embed(batch_texts)
                    await self._vector_store.upsert(
                        chunk_ids=batch_ids,
                        vectors=result.vectors,
                        metadatas=batch_meta,
                    )
                if self._keyword_store is not None:
                    await self._keyword_store.index(
                        chunk_ids=batch_ids,
                        texts=batch_texts,
                        metadatas=batch_meta,
                    )
                processed += len(batch_ids)
            except Exception as e:
                logger.warning(
                    "index_rebuild_batch_failed",
                    error=str(e),
                    batch_index=i,
                    batch_size=len(batch_ids),
                )

        logger.info(
            "indices_rebuilt",
            chunk_count=processed,
            include_vectors=include_vectors,
            keyword_store=bool(self._keyword_store),
        )
        return processed
