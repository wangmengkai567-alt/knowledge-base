"""单进程入库编排：阶段持久化、同文档互斥、定向重试与启动核实。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

import structlog

from internal.biz.constants import DocumentStatus, EmbeddingStatus
from internal.biz.ingestion_state import STAGES, initial_stages, stage_state
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from internal.biz.chunk import ChunkBiz
    from internal.biz.embedding import EmbeddingBiz
    from internal.biz.entity import Document
    from internal.biz.repo import ChunkRepo, DocumentParser, DocumentRepo

logger = structlog.get_logger()


class IngestionBiz:
    def __init__(
        self,
        documents: DocumentRepo,
        chunks: ChunkRepo,
        chunk_biz: ChunkBiz,
        embeddings: EmbeddingBiz,
        parser_factory: Callable[[str], DocumentParser],
    ) -> None:
        self._documents = documents
        self._chunks = chunks
        self._chunk_biz = chunk_biz
        self._embeddings = embeddings
        self._parser_factory = parser_factory
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    def lock(self, document_id: int) -> asyncio.Lock:
        lock = self._locks.get(document_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[document_id] = lock
        return lock

    def new_stages(self) -> dict:
        return initial_stages(self._embeddings.keyword_required)

    async def _step(
        self,
        doc: Document,
        name: str,
        action: Callable[[], Awaitable[object]],
    ) -> bool:
        if name == "chunk":
            defaults = self.new_stages()
            doc.stages["vector"] = defaults["vector"]
            doc.stages["keyword"] = defaults["keyword"]
        doc.stages[name] = stage_state("running")
        try:
            await self._documents.update_stages(doc.id, doc.stages)
            await action()
        except asyncio.CancelledError:
            doc.stages[name] = stage_state("failed", "处理被中断，请重试此阶段")
            await self._documents.update_stages(doc.id, doc.stages)
            if name == "parse":
                await self._documents.update_status(doc.id, DocumentStatus.ERROR, "解析被中断")
            raise
        except Exception as exc:
            doc.stages[name] = stage_state("failed", str(exc) or type(exc).__name__)
            await self._documents.update_stages(doc.id, doc.stages)
            if name == "parse":
                await self._documents.update_status(doc.id, DocumentStatus.ERROR, str(exc)[:1000])
            logger.error("ingestion_stage_failed", document_id=doc.id, stage=name, error=str(exc))
            return False
        doc.stages[name] = stage_state("completed")
        await self._documents.update_stages(doc.id, doc.stages)
        return True

    async def process(
        self, document_id: int, *, force: bool = False, stage: str | None = None
    ) -> Document:
        async with self.lock(document_id):
            doc = await self._documents.get_by_id(document_id)
            if doc is None:
                raise APIError(
                    code=ErrorCode.NOT_FOUND, reason="DOCUMENT_NOT_FOUND", message="文档不存在"
                )
            if force:
                doc.stages = self.new_stages()
                await self._documents.update_stages(doc.id, doc.stages)
            if not doc.stages:
                raise APIError(
                    code=ErrorCode.INVALID_REQUEST,
                    reason="INGESTION_NOT_VERIFIED",
                    message="请先执行状态迁移并重启服务",
                )
            if stage is not None:
                index = STAGES.index(stage)
                prerequisites = STAGES[: min(index, 2)]
                if any(doc.stages[name]["status"] != "completed" for name in prerequisites):
                    raise APIError(
                        code=ErrorCode.INVALID_REQUEST,
                        reason="STAGE_NOT_READY",
                        message="前置阶段尚未完成，请先重试失败阶段",
                    )
                if stage == "parse" and doc.stages["parse"]["status"] == "completed":
                    raise APIError(
                        code=ErrorCode.INVALID_REQUEST,
                        reason="REPARSE_REQUIRES_FORCE",
                        message="已解析文档请使用重新解析入口",
                    )
                if stage == "keyword" and not self._embeddings.keyword_required:
                    raise APIError(
                        code=ErrorCode.INVALID_REQUEST,
                        reason="KEYWORD_NOT_REQUIRED",
                        message="当前配置不使用关键词索引",
                    )
                if stage in ("chunk", "vector", "keyword"):
                    doc.stages[stage] = stage_state("pending")
            if stage in (None, "parse") and doc.stages["parse"]["status"] != "completed":
                # 重解析立即撤销就绪；旧分块直到新的文本分块成功替换前保留，但不可召回。
                doc.stages = self.new_stages()
                await self._documents.update_stages(doc.id, doc.stages)
                await self._documents.update_status(doc.id, DocumentStatus.PROCESSING)

                async def parse() -> None:
                    content = await self._parser_factory(doc.file_path).parse(doc.file_path)
                    if not content.strip():
                        raise ValueError("解析结果为空")
                    await self._documents.update_parsed_content(doc.id, content)
                    doc.parsed_content = content

                if not await self._step(doc, "parse", parse):
                    return await self._documents.get_by_id(doc.id)
            if (
                stage in (None, "parse", "chunk")
                and doc.stages["chunk"]["status"] != "completed"
                and not await self._step(
                    doc, "chunk", lambda: self._chunk_biz.chunk_document(doc.id, doc.parsed_content)
                )
            ):
                return await self._documents.get_by_id(doc.id)
            chunks = await self._chunks.list_by_document(doc.id)
            if (
                stage in (None, "parse", "chunk", "vector")
                and doc.stages["vector"]["status"] != "completed"
            ):

                async def vector() -> None:
                    await self._embeddings.reconcile_vectors(doc.id, chunks)
                    current = await self._chunks.list_by_document(doc.id)
                    await self._embeddings.embed_document(doc.id, current)
                    if not await self._embeddings.reconcile_vectors(doc.id, current):
                        raise ValueError("向量索引缺少当前分块")

                await self._step(doc, "vector", vector)
            if (
                self._embeddings.keyword_required
                and stage in (None, "parse", "chunk", "keyword")
                and doc.stages["keyword"]["status"] != "completed"
            ):
                await self._step(
                    doc, "keyword", lambda: self._embeddings.index_keywords(doc.id, chunks)
                )
            return await self._documents.get_by_id(doc.id)

    async def rebuild_indices(self, include_vectors: bool = False) -> int:
        # 无法列出文档时让启动失败，避免带着上次进程的 ready 状态开始服务。
        documents = await self._documents.list_all()
        processed = 0
        for doc in documents:
            async with self.lock(doc.id):
                old = doc.stages
                if any(name not in old for name in ("parse", "chunk")):
                    raise ValueError("文档入库阶段未迁移，请先运行 cmd.migrate 再启动服务")
                doc.stages = self.new_stages()
                for name in ("parse", "chunk"):
                    doc.stages[name] = dict(old[name])
                    if old[name]["status"] == "running":
                        doc.stages[name] = stage_state("failed", "上次处理被中断，请重试此阶段")
                # 当前轮次的完成标记是权威，不能以旧正文或旧分块存在推断新轮完成。
                if doc.stages["parse"]["status"] == "completed" and (
                    not doc.parsed_content or not doc.parsed_content.strip()
                ):
                    doc.stages["parse"] = stage_state("failed", "缺少有效解析文本")
                await self._documents.update_stages(doc.id, doc.stages)
                if doc.stages["parse"]["status"] != "completed":
                    if doc.stages["parse"]["status"] == "failed":
                        await self._documents.update_status(
                            doc.id, DocumentStatus.ERROR, doc.stages["parse"].get("error")
                        )
                    continue

                # 只有实际替换完成的分块才可核实索引；pending 可能仍保留上一轮分块。
                if doc.stages["chunk"]["status"] != "completed":
                    continue

                async def verify_chunks(doc: Document = doc) -> None:
                    chunks = await self._chunks.list_by_document(doc.id)
                    if (
                        not chunks
                        or [c.position for c in chunks] != list(range(len(chunks)))
                        or any(not c.content.strip() for c in chunks)
                    ):
                        raise ValueError("缺少有效的连续分块，请重试分块")

                if not await self._step(doc, "chunk", verify_chunks):
                    continue

                async def verify_vectors(doc: Document = doc) -> None:
                    chunks = await self._chunks.list_by_document(doc.id)
                    if include_vectors:
                        await self._chunks.update_embedding_status(
                            [c.id for c in chunks], EmbeddingStatus.PENDING
                        )
                        chunks = await self._chunks.list_by_document(doc.id)
                        await self._embeddings.embed_document(doc.id, chunks)
                    if not await self._embeddings.reconcile_vectors(doc.id, chunks):
                        raise ValueError("实际向量记录缺失或归属不符，请重试向量索引")

                await self._step(doc, "vector", verify_vectors)
                if self._embeddings.keyword_required:
                    chunks = await self._chunks.list_by_document(doc.id)
                    await self._step(
                        doc,
                        "keyword",
                        lambda doc=doc, chunks=chunks: self._embeddings.index_keywords(
                            doc.id, chunks
                        ),
                    )
                if all(
                    doc.stages[name]["status"] == "completed"
                    for name in ("parse", "chunk", "vector")
                ) and doc.stages["keyword"]["status"] in ("completed", "skipped"):
                    processed += len(await self._chunks.list_by_document(doc.id))
        return processed
