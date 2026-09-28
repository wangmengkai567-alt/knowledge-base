"""
internal/service/knowledge_base.py — 知识库应用层

职责：DTO 校验 → 调 biz → DTO 返回。
"""

from __future__ import annotations

from api.openapi.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from internal.biz.knowledge_base import KnowledgeBaseBiz


class KnowledgeBaseService:
    """知识库应用层 — DTO 校验 + 调 biz + DTO 返回。"""

    def __init__(self, kb_biz: KnowledgeBaseBiz) -> None:
        self._kb_biz = kb_biz

    async def list_knowledge_bases(self, user_id: int) -> KnowledgeBaseListResponse:
        kbs = await self._kb_biz.list_knowledge_bases(user_id=user_id)
        return KnowledgeBaseListResponse(
            knowledge_bases=[KnowledgeBaseResponse.from_domain(kb) for kb in kbs],
            total=len(kbs),
        )

    async def get_knowledge_base(self, kb_id: int, user_id: int | None = None) -> KnowledgeBaseResponse:
        kb = await self._kb_biz.get_knowledge_base(kb_id, user_id=user_id)
        return KnowledgeBaseResponse.from_domain(kb)

    async def create_knowledge_base(self, user_id: int, body: KnowledgeBaseCreateRequest) -> KnowledgeBaseResponse:
        kb = await self._kb_biz.create_knowledge_base(
            user_id=user_id,
            name=body.name,
            description=body.description,
            icon=body.icon,
        )
        return KnowledgeBaseResponse.from_domain(kb)

    async def update_knowledge_base(
        self, kb_id: int, body: KnowledgeBaseUpdateRequest, user_id: int | None = None
    ) -> KnowledgeBaseResponse:
        kb = await self._kb_biz.update_knowledge_base(
            kb_id=kb_id,
            user_id=user_id,
            name=body.name,
            description=body.description,
            icon=body.icon,
        )
        return KnowledgeBaseResponse.from_domain(kb)

    async def delete_knowledge_base(self, kb_id: int, user_id: int) -> None:
        await self._kb_biz.delete_knowledge_base(kb_id=kb_id, user_id=user_id)







