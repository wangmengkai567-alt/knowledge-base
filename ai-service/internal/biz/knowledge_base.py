"""管理知识库记录与所有权检查，补齐存量文档的知识库记录，并协调级联删除。"""

from __future__ import annotations

import structlog

from internal.biz.constants import KbStatus
from internal.biz.document import DocumentBiz
from internal.biz.entity import KnowledgeBase
from internal.biz.repo import DocumentRepo, KnowledgeBaseRepo
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()

# 知识库名称的最大字符数。
_MAX_NAME_LEN = 40
# 知识库描述的最大字符数。
_MAX_DESC_LEN = 200
# 图标标识的最大字符数。
_MAX_ICON_LEN = 32


class KnowledgeBaseBiz:
    """知识库业务逻辑 — 不感知协议、不感知存储。"""

    def __init__(
        self,
        kb_repo: KnowledgeBaseRepo,
        document_repo: DocumentRepo,
        document_biz: DocumentBiz,
        default_embedding_model: str = "",
        default_chunk_size: int = 500,
        default_chunk_overlap: int = 50,
        default_kb_name: str = "默认知识库",
    ) -> None:
        """注入知识库和文档依赖，设置新建知识库的默认配置；分块大小和重叠量按估算 token 计。"""
        self._kb_repo = kb_repo
        self._document_repo = document_repo
        self._document_biz = document_biz
        self._default_embedding_model = default_embedding_model
        self._default_chunk_size = default_chunk_size
        self._default_chunk_overlap = default_chunk_overlap
        self._default_kb_name = default_kb_name

    async def list_knowledge_bases(self, user_id: int) -> list[KnowledgeBase]:
        """补齐知识库目录后，仅返回 owner_id 与当前用户一致的知识库。"""
        await self._ensure_catalog(user_id=user_id)
        kbs = await self._kb_repo.list_all()
        return [kb for kb in kbs if kb.owner_id == user_id]

    # 获取知识库并按需检查所有权；旧文档缺少知识库记录时先补齐记录。
    async def get_knowledge_base(self, kb_id: int, user_id: int | None = None) -> KnowledgeBase:
        kb = await self._kb_repo.get_by_id(kb_id)
        if kb is not None:
            if user_id is not None and kb.owner_id != user_id:
                raise APIError(code=ErrorCode.NOT_FOUND, reason="KNOWLEDGE_BASE_NOT_FOUND", message="Knowledge base not found.")
            return kb
        # 文档已引用此知识库 ID、但目录缺少对应记录时，先补齐记录，再检查所有权。
        docs = await self._document_repo.list_by_kb(kb_id)
        if docs:
            await self._backfill_one(kb_id, owner_id=docs[0].user_id)
            kb = await self._kb_repo.get_by_id(kb_id)
            if kb is not None:
                if user_id is not None and kb.owner_id != user_id:
                    raise APIError(code=ErrorCode.NOT_FOUND, reason="KNOWLEDGE_BASE_NOT_FOUND", message="Knowledge base not found.")
                return kb
        raise APIError(
            code=ErrorCode.NOT_FOUND,
            reason="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found.",
        )

    # 校验基本信息后创建知识库，并应用当前默认的嵌入和分块配置。
    async def create_knowledge_base(
        self,
        user_id: int,
        name: str,
        description: str = "",
        icon: str = "folder",
    ) -> KnowledgeBase:
        name = self._validate_name(name)
        description = self._validate_description(description)
        icon = self._validate_icon(icon)
        kb = KnowledgeBase(
            id=None,
            name=name,
            description=description,
            icon=icon,
            status=KbStatus.NORMAL,
            owner_id=user_id,
            embedding_model=self._default_embedding_model,
            chunk_strategy="recursive",
            chunk_size=self._default_chunk_size,
            chunk_overlap=self._default_chunk_overlap,
        )
        created = await self._kb_repo.create(kb)
        logger.info("knowledge_base_created", kb_id=created.id, owner_id=user_id)
        return created

    # 更新已有知识库，只修改本次明确提供的字段。
    async def update_knowledge_base(
        self,
        kb_id: int,
        user_id: int | None = None,
        name: str | None = None,
        description: str | None = None,
        icon: str | None = None,
    ) -> KnowledgeBase:
        kb = await self.get_knowledge_base(kb_id, user_id=user_id)
        if name is not None:
            kb.name = self._validate_name(name)
        if description is not None:
            kb.description = self._validate_description(description)
        if icon is not None:
            kb.icon = self._validate_icon(icon)
        updated = await self._kb_repo.update(kb)
        logger.info("knowledge_base_updated", kb_id=kb_id)
        return updated

    async def delete_knowledge_base(self, kb_id: int, user_id: int) -> None:
        """删除知识库及其下全部文档（文件 / chunks / 向量索引）。"""
        await self.get_knowledge_base(kb_id, user_id=user_id)
        docs = await self._document_repo.list_by_kb(kb_id)
        for doc in docs:
            if doc.id is None:
                continue
            await self._document_biz.delete_document(doc.id, user_id=user_id)
        deleted = await self._kb_repo.delete(kb_id)
        if not deleted:
            raise APIError(
                code=ErrorCode.NOT_FOUND,
                reason="KNOWLEDGE_BASE_NOT_FOUND",
                message="Knowledge base not found.",
            )
        logger.info(
            "knowledge_base_deleted",
            kb_id=kb_id,
            cascaded_documents=len(docs),
        )

    async def _ensure_catalog(self, user_id: int) -> None:
        """为文档已有的知识库 ID 补齐记录；目录仍为空时创建 ID 为 1 的默认知识库。"""
        existing = set(await self._kb_repo.list_ids())
        doc_kb_ids = set(await self._document_repo.list_distinct_kb_ids())
        missing = sorted(doc_kb_ids - existing)
        for kb_id in missing:
            await self._backfill_one(kb_id, owner_id=user_id)
            existing.add(kb_id)

        if existing:
            return

        await self._kb_repo.create(
            KnowledgeBase(
                id=1,
                name=self._default_kb_name,
                description="系统默认知识库",
                owner_id=user_id,
                embedding_model=self._default_embedding_model,
                chunk_strategy="recursive",
                chunk_size=self._default_chunk_size,
                chunk_overlap=self._default_chunk_overlap,
            )
        )
        logger.info("knowledge_base_seeded", kb_id=1)

    async def _backfill_one(self, kb_id: int, owner_id: int) -> None:
        """为已有文档引用、但缺少目录记录的知识库 ID 补建记录，归属指定用户。"""
        await self._kb_repo.create(
            KnowledgeBase(
                id=kb_id,
                name=f"知识库 #{kb_id}",
                description="由存量文档自动回填",
                owner_id=owner_id,
                embedding_model=self._default_embedding_model,
                chunk_strategy="recursive",
                chunk_size=self._default_chunk_size,
                chunk_overlap=self._default_chunk_overlap,
            )
        )
        logger.info("knowledge_base_backfilled", kb_id=kb_id)

    @staticmethod
    def _validate_name(name: str) -> str:
        """去除名称首尾空白；为空或超过字符数上限时拒绝请求。"""
        cleaned = (name or "").strip()
        if not cleaned:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="INVALID_KB_NAME",
                message="Knowledge base name is required.",
            )
        if len(cleaned) > _MAX_NAME_LEN:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="INVALID_KB_NAME",
                message=f"Knowledge base name must be at most {_MAX_NAME_LEN} characters.",
            )
        return cleaned

    @staticmethod
    def _validate_description(description: str) -> str:
        """去除描述首尾空白并检查最大字符数，允许返回空字符串。"""
        cleaned = (description or "").strip()
        if len(cleaned) > _MAX_DESC_LEN:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="INVALID_KB_DESCRIPTION",
                message=f"Description must be at most {_MAX_DESC_LEN} characters.",
            )
        return cleaned

    @staticmethod
    def _validate_icon(icon: str) -> str:
        """将空图标标识设为 folder，并检查标识字符串的最大字符数。"""
        cleaned = (icon or "folder").strip() or "folder"
        if len(cleaned) > _MAX_ICON_LEN:
            raise APIError(
                code=ErrorCode.INVALID_REQUEST,
                reason="INVALID_KB_ICON",
                message=f"Icon must be at most {_MAX_ICON_LEN} characters.",
            )
        return cleaned












