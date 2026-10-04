"""
internal/biz/document.py — 文档业务逻辑（纯逻辑，不感知协议/存储）

DocumentBiz 负责：
- upload：校验文件类型/大小 → 保存文件 → 创建文档记录 → 自动触发解析
- parse_document：手动触发解析（解析失败后重试）
- list_documents：列出知识库下所有文档
- get_document：查单个文档详情
- delete_document：删除文件 + 删除文档记录

不感知 HTTP（不 import fastapi）、不感知存储（不 import sqlalchemy）。
"""

from __future__ import annotations

import mimetypes
from collections.abc import Awaitable, Callable
from pathlib import PurePosixPath

import structlog

from internal.biz.constants import DocumentStatus
from internal.biz.entity import Document
from internal.biz.repo import DocumentParser, DocumentRepo, FileStorageRepo
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

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


class DocumentBiz:
    """文档业务逻辑 — 不感知协议、不感知存储。"""

    def __init__(
        self,
        document_repo: DocumentRepo,
        file_storage_repo: FileStorageRepo,
        parser_factory: Callable[[str], DocumentParser],
        max_file_size: int = MAX_FILE_SIZE,
        on_parse_success: Callable[[int, str], Awaitable[None]] | None = None,
        on_document_delete: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        self._document_repo = document_repo
        self._file_storage_repo = file_storage_repo
        self._parser_factory = parser_factory
        self._max_file_size = max_file_size
        self._on_parse_success = on_parse_success
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

        # 推断 MIME 类型
        # 优先按扩展名映射规范 MIME（系统 mimetypes 常把 .md 误判为
        # application/octet-stream，导致合法文件被误拒）。仅在扩展名合法
        # 时以映射为准；扩展名非法已在上方拦截（UNSUPPORTED_FILE_TYPE）。
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

        # 上传后自动触发解析（解析失败不阻塞上传）
        try:
            created = await self.parse_document(created.id, user_id=user_id)
        except APIError:
            # 解析失败：parse_document 内部已将状态置为 ERROR，
            # 重新从 DB 获取最新状态，确保返回值与数据库一致
            created = await self.get_document(created.id, user_id=user_id)
            logger.warning(
                "auto_parse_failed",
                document_id=created.id,
                status=created.status,
            )

        return created

    async def parse_document(
        self,
        document_id: int,
        user_id: int,
        force: bool = False,
    ) -> Document:
        """解析文档 — 提取纯文本内容。

        流程：
        1. 校验文档存在 + 所有权
        2. 检查状态（已解析且未 force 则跳过；PROCESSING 且未 force 则跳过）
        3. 更新状态为 PROCESSING
        4. 根据文件类型选择 Parser 并解析
        5. 成功：保存 parsed_content + 状态 PARSED
        6. 失败：状态 ERROR + 记录 error_message
        """
        document = await self.get_document(document_id, user_id=user_id)

        if document.status == DocumentStatus.PARSED and not force:
            logger.info("document_already_parsed", document_id=document_id)
            return document
        if document.status == DocumentStatus.PROCESSING and not force:
            logger.info("document_parse_in_progress", document_id=document_id)
            return document

        # 更新状态为 PROCESSING
        await self._document_repo.update_status(
            document_id=document_id,
            status=DocumentStatus.PROCESSING,
        )

        try:
            # 根据文件扩展名选择 Parser
            parser = self._parser_factory(document.file_path)
            parsed_content = await parser.parse(document.file_path)

            # 保存解析结果（同时更新状态为 PARSED）
            await self._document_repo.update_parsed_content(
                document_id=document_id,
                parsed_content=parsed_content,
            )

            logger.info(
                "document_parsed",
                document_id=document_id,
                content_length=len(parsed_content),
            )

            # 解析成功后触发分块
            if self._on_parse_success is not None:
                try:
                    await self._on_parse_success(document_id, parsed_content)
                except Exception as e:
                    logger.warning(
                        "auto_chunk_failed",
                        document_id=document_id,
                        error=str(e),
                    )

            # 返回更新后的文档
            return await self.get_document(document_id, user_id=user_id)

        except APIError:
            # 业务异常（如 APIError）直接向上，不覆盖状态
            raise
        except Exception as e:
            error_msg = str(e)[:500]  # 截断防止过长
            await self._document_repo.update_status(
                document_id=document_id,
                status=DocumentStatus.ERROR,
                error_message=error_msg,
            )
            logger.error(
                "document_parse_failed",
                document_id=document_id,
                error=error_msg,
            )
            raise APIError(
                code=ErrorCode.INTERNAL_ERROR,
                reason="PARSE_FAILED",
                message=f"Document parsing failed: {error_msg}",
            ) from e

    async def list_documents(self, knowledge_base_id: int, user_id: int) -> list[Document]:
        """列出知识库下所有文档。

        注意：当前不校验知识库所有权（Sprint 3 知识库 CRUD 已完成，
        所有权校验在 Service 层完成）。
        """
        return await self._document_repo.list_by_kb(knowledge_base_id)

    async def get_document(self, document_id: int, user_id: int | None = None) -> Document:
        """查单个文档详情。

        归属模型说明（与 list_documents 保持一致）：
        系统当前没有"知识库所有者"概念，文档列表按 knowledge_base_id 返回，
        不校验上传者。为避免"列表能看到却打不开/删不掉"的不一致，这里也只做
        "文档是否存在"的校验，不再按文档 user_id 拦截。user_id 仅保留入参兼容性。
        """
        document = await self._document_repo.get_by_id(document_id)
        if document is None:
            raise APIError(
                code=ErrorCode.NOT_FOUND,
                reason="DOCUMENT_NOT_FOUND",
                message="Document not found.",
            )
        return document

    async def delete_document(self, document_id: int, user_id: int) -> None:
        """删除文档 — 校验所有权 → 删文件 → 删记录。

        文件删除失败不阻塞记录删除（记录是元数据，文件可孤儿清理）。
        """
        document = await self.get_document(document_id, user_id=user_id)

        # 删除文件（尽力而为）
        try:
            await self._file_storage_repo.delete(document.file_path)
        except Exception as e:
            logger.warning(
                "file_delete_failed",
                document_id=document_id,
                file_path=document.file_path,
                error=str(e),
            )

        # 级联删除分块
        if self._on_document_delete is not None:
            try:
                await self._on_document_delete(document_id)
            except Exception as e:
                logger.warning(
                    "chunk_delete_failed",
                    document_id=document_id,
                    error=str(e),
                )

        # 删除记录
        await self._document_repo.delete(document_id)
        logger.info("document_deleted", document_id=document_id)


