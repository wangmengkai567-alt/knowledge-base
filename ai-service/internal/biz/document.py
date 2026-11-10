"""校验并保存上传文件，调用入库编排处理正文与索引，并协调文档删除。"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import structlog

from internal.biz.constants import DocumentStatus
from internal.biz.entity import Document
from internal.biz.ingestion_state import stage_state
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from internal.biz.ingestion import IngestionBiz
    from internal.biz.repo import DocumentRepo, FileStorageRepo

logger = structlog.get_logger()

# 允许上传的文件类型（MIME 类型白名单）
ALLOWED_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",                              # PDF
    "text/markdown",                                # Markdown
    "text/x-markdown",                              # Markdown (某些系统)
    "text/plain",                                   # 纯文本
})

# 允许的文件扩展名
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".md", ".markdown", ".txt",
})

# 扩展名 → 规范 MIME（避免 mimetypes 把 .md 误判为 application/octet-stream）
EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}

# 默认最大文件大小：10MB
MAX_FILE_SIZE: int = 10 * 1024 * 1024


# 负责文档上传、入库处理和删除，具体存储与索引操作交给注入的服务。
class DocumentBiz:
    def __init__(
        self,
        document_repo: DocumentRepo,
        file_storage_repo: FileStorageRepo,
        ingestion: IngestionBiz,
        on_document_delete: Callable[[int], Awaitable[None]],
        max_file_size: int = MAX_FILE_SIZE,
    ) -> None:
        """注入文档存储和入库流程；on_document_delete 清理关联数据，max_file_size 单位为字节。"""
        self._document_repo = document_repo
        self._file_storage_repo = file_storage_repo
        self._ingestion = ingestion
        self._max_file_size = max_file_size
        self._on_document_delete = on_document_delete

    async def upload(
        self,
        knowledge_base_id: int,
        user_id: int,
        filename: str,
        content: bytes,
    ) -> Document:
        """上传文档。

        1. 校验文件扩展名
        2. 校验文件大小
        3. 推断 MIME 类型并校验
        4. 保存文件到存储
        5. 创建文档记录（状态 = pending）
        """
        # 校验扩展名
        ext = PurePosixPath(filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="UNSUPPORTED_FILE_TYPE",
                message=f"不支持的文件类型 '{ext}'，仅允许：{', '.join(sorted(ALLOWED_EXTENSIONS))}",
            )

        # 校验文件大小
        file_size = len(content)
        if file_size == 0:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="EMPTY_FILE",
                message="不能上传空文件。",
            )
        if file_size > self._max_file_size:
            max_mb = self._max_file_size // (1024 * 1024)
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="FILE_TOO_LARGE",
                message=f"文件大小（{file_size} 字节）超过上限 {max_mb}MB。",
            )

        # 扩展名通过校验后，优先使用本模块的 MIME 映射；未映射时才调用 mimetypes。
        mime_type, _ = mimetypes.guess_type(filename)
        if ext in EXT_TO_MIME:
            mime_type = EXT_TO_MIME[ext]
        elif mime_type is None:
            mime_type = "application/octet-stream"
        if mime_type not in ALLOWED_MIME_TYPES and ext not in ALLOWED_EXTENSIONS:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="UNSUPPORTED_MIME_TYPE",
                message=f"不支持的文件 MIME 类型 '{mime_type}'。",
            )

        file_path = await self._file_storage_repo.save(filename, content)

        # 创建文档记录（失败时回滚已存文件，防止孤儿文件）
        document = Document(
            id=None,
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            filename=filename,
            file_path=file_path,
            file_size=file_size,
            mime_type=mime_type,
            status=DocumentStatus.PENDING,
            stages=self._ingestion.new_stages(),
        )
        try:
            created = await self._document_repo.create(document)
        except Exception:
            logger.error(
                "document_create_failed_rolling_back_file",
                file_path=file_path,
            )
            await self._file_storage_repo.delete(file_path)
            raise

        logger.info(
            "document_uploaded",
            document_id=created.id,
            knowledge_base_id=knowledge_base_id,
            filename=filename,
            file_size=file_size,
        )

        # 各阶段失败随文档状态返回，文件保存成功不等于入库完成。
        return await self.parse_document(created.id, user_id=user_id)

    async def parse_document(
        self,
        document_id: int,
        user_id: int,
        force: bool = False,
    ) -> Document:
        """解析并完成下游入库阶段；重解析显式撤销旧就绪状态。"""
        await self.get_document(document_id, user_id=user_id)
        return await self._ingestion.process(document_id, force=force)

    # 确认文档存在后，将指定阶段的重试交给入库流程。
    async def retry(self, document_id: int, user_id: int, stage: str | None = None) -> Document:
        await self.get_document(document_id, user_id=user_id)
        return await self._ingestion.process(document_id, stage=stage)

    async def list_documents(self, knowledge_base_id: int, user_id: int) -> list[Document]:
        """列出知识库下所有文档。

        调用前由 HTTP 路由校验知识库所有权；此处仅按知识库 ID 查询。
        """
        return await self._document_repo.list_by_kb(knowledge_base_id)

    async def get_document(self, document_id: int, user_id: int | None = None) -> Document:
        """查单个文档详情。

        这里只校验文档存在；HTTP 路由负责校验知识库所有权和文档归属。
        本方法不使用 user_id，直接调用时不能据此推断用户已有访问权限。
        """
        document = await self._document_repo.get_by_id(document_id)
        if document is None:
            raise APIError(
                code=ErrorCode.NOT_FOUND,
                reason="DOCUMENT_NOT_FOUND",
                message="Document not found.",
            )
        return document

    # 与解析和重试共用文档锁，防止删除时仍有任务写入分块或索引。
    async def delete_document(self, document_id: int, user_id: int) -> None:
        async with self._ingestion.lock(document_id):
            await self._delete_document(document_id, user_id)

    async def _delete_document(self, document_id: int, user_id: int) -> None:
        """撤销就绪，依次清理索引、分块、原文件和文档记录；失败保留修复入口。"""
        document = await self.get_document(document_id, user_id=user_id)

        doc_stages = document.stages
        doc_stages["chunk"] = stage_state("running")
        defaults = self._ingestion.new_stages()
        doc_stages["vector"] = defaults["vector"]
        doc_stages["keyword"] = defaults["keyword"]
        await self._document_repo.update_stages(document_id, doc_stages)
        try:
            await self._on_document_delete(document_id)
            await self._file_storage_repo.delete(document.file_path)
        except Exception as exc:
            doc_stages["chunk"] = stage_state("failed", f"删除未完成：{exc}")
            await self._document_repo.update_stages(document_id, doc_stages)
            raise

        # 删除记录
        await self._document_repo.delete(document_id)
        logger.info("document_deleted", document_id=document_id)


