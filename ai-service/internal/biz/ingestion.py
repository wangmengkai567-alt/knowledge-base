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
    """解析 → 分块后分别建立向量与关键词索引，各步骤独立持久化成功或失败。"""

    def __init__(
        self,
        documents: DocumentRepo,
        chunks: ChunkRepo,
        chunk_biz: ChunkBiz,
        embeddings: EmbeddingBiz,
        parser_factory: Callable[[str], DocumentParser],
    ) -> None:
        """注入入库依赖；parser_factory 根据文件路径选择解析器。"""
        self._documents = documents
        self._chunks = chunks
        self._chunk_biz = chunk_biz
        self._embeddings = embeddings
        self._parser_factory = parser_factory
        # 同文档的解析、重试与删除共用锁；无等待者和持有者后可自动回收。
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    def lock(self, document_id: int) -> asyncio.Lock:
        """返回指定文档专用的异步互斥锁；本函数只取出锁，不会等待或占用它。

        document_id 是文档的数据库 ID。同一 ID 在锁仍被任务持有或等待时复用同一把锁，
        不同文档使用不同的锁，因此可以并行入库。
        调用方使用 async with self.lock(document_id): 才会等待并占用锁，退出时自动释放。
        这样可防止同一文档的解析、重试和删除同时改写分块及索引。
        锁只协调本服务进程内的协程，不提供跨进程或跨服务的互斥。
        """
        # 先复用仍存活的文档锁，确保操作同一文档的协程排在同一把锁上。
        lock = self._locks.get(document_id)
        if lock is None:
            # 第一次使用或旧锁已回收时创建；此时仅登记锁，不会阻塞当前协程。
            lock = asyncio.Lock()
            self._locks[document_id] = lock
        return lock

    def new_stages(self) -> dict:
        """创建一份新的入库状态字典，不写入数据库。

        解析、分块和向量索引初始为 pending；未启用关键词索引时，该步骤标记 skipped。
        """
        return initial_stages(self._embeddings.keyword_required)

    async def _record_failure(self, doc: Document, name: str, error: str, parse_error: str) -> None:
        """将指定步骤标记为失败，并确保错误状态在释放文档锁之前写入数据库。

        name 指定失败步骤，error 保存步骤错误；解析失败时还使用 parse_error 更新文档状态。
        即使外层任务再次被取消，也等待本次写入结束，随后继续抛出取消异常。
        """
        doc.stages[name] = stage_state("failed", error)

        async def persist() -> None:
            """写入失败阶段；若解析失败，同时保存文档错误状态和解析错误说明。"""
            await self._documents.update_stages(doc.id, doc.stages)
            if name == "parse":
                await self._documents.update_status(doc.id, DocumentStatus.ERROR, parse_error)

        task = asyncio.create_task(persist())
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancelled = exc
        task.result()
        if cancelled is not None:
            raise cancelled

    async def _step(
        self,
        doc: Document,
        name: str,
        action: Callable[[], Awaitable[object]],
    ) -> bool:
        """执行一个入库步骤，并将 running → completed/failed 的变化写入数据库。

        name 是步骤名，action 是不带参数的异步操作。成功返回 True；普通异常记录后返回 False，
        由调用方决定是否继续其他步骤；取消异常在记录失败后继续抛出。
        调用方须先持有文档锁，确保步骤状态和对应分块、索引不会被并发操作覆盖。
        """
        # 分块一旦变化，两个下游索引都需重新核实，不能继承旧的完成状态。
        if name == "chunk":
            defaults = self.new_stages()
            doc.stages["vector"] = defaults["vector"]
            doc.stages["keyword"] = defaults["keyword"]
        doc.stages[name] = stage_state("running")
        try:
            # 先保存“处理中”，再执行当前入库操作。
            await self._documents.update_stages(doc.id, doc.stages)
            await action()
            # 操作成功后保存“已完成”；异常由下方分支记录为失败。
            doc.stages[name] = stage_state("completed")
            await self._documents.update_stages(doc.id, doc.stages)
        except asyncio.CancelledError:
            await self._record_failure(doc, name, "处理被中断，请重试此阶段", "解析被中断")
            raise
        except Exception as exc:
            await self._record_failure(doc, name, str(exc) or type(exc).__name__, str(exc)[:1000])
            logger.error("ingestion_stage_failed", document_id=doc.id, stage=name, error=str(exc))
            return False
        return True

    async def process(
        self, document_id: int, *, force: bool = False, stage: str | None = None
    ) -> Document:
        """对指定文档执行入库或重试，返回数据库中最新的文档及各步骤状态。

        默认跳过已完成步骤，继续解析、分块及所需索引；force=True 会重置状态并重新解析。
        stage 可指定 parse/chunk/vector/keyword：解析或分块后继续处理下游索引，
        单独重试 vector 或 keyword 时只重建该索引，且要求解析、分块已经完成。
        整个流程持有该文档的锁；步骤失败可能通过返回文档的 stages 表达，返回不代表已就绪。
        """
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
                # 向量与关键词都依赖解析和分块，彼此独立，允许只重试其中一个索引。
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
                    """调用文件解析器，拒绝空正文，并把正文同时写入数据库和当前文档对象。"""
                    # 根据文件路径选择解析器，读取并提取文档正文。
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
            # 后续仅执行请求范围内且未完成的步骤；关键词重试不会重新调用嵌入模型。
            chunks = await self._chunks.list_by_document(doc.id)
            if (
                stage in (None, "parse", "chunk", "vector")
                and doc.stages["vector"]["status"] != "completed"
            ):

                async def vector() -> None:
                    """核对当前向量记录，补齐分块向量，再确认每个当前分块都有对应向量。"""
                    await self._embeddings.reconcile_vectors(doc.id, chunks)
                    current = await self._chunks.list_by_document(doc.id)
                    # 为当前分块生成向量并写入向量索引。
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
        """服务启动时核实文档和索引状态，返回已就绪文档的分块总数。

        将上次进程遗留的运行中步骤标为失败，并核对已完成分块是否实际存在。
        include_vectors=True 时重新生成内存向量；False 时只核对持久化向量，不自动补算。
        启用关键词检索时重建进程内关键词索引；全部必要步骤通过后才允许文档被检索。
        """
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
                    """检查文档分块非空、位置编号从零连续且正文有效；失败时要求重新分块。"""
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
                    """按需重建向量，再核实向量记录存在且归属正确；doc 默认值固定本轮文档。"""
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
