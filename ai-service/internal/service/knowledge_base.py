"""
internal/service/knowledge_base.py — 知识库应用层

将知识库操作交给业务层，并将结果转换为接口响应。
"""

from __future__ import annotations

from api.openapi.schemas.knowledge_base import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
)
from internal.biz.knowledge_base import KnowledgeBaseBiz


# 连接知识库接口与业务逻辑，统一转换知识库及列表响应。
class KnowledgeBaseService:
    def __init__(self, kb_biz: KnowledgeBaseBiz) -> None:
        """注入知识库业务，负责接口请求字段与知识库响应结构的转换。"""
        self._kb_biz = kb_biz

    async def list_knowledge_bases(self, user_id: int) -> KnowledgeBaseListResponse:
        """列出指定用户的知识库，转换为响应列表并附总数。"""
        kbs = await self._kb_biz.list_knowledge_bases(user_id=user_id)
        return KnowledgeBaseListResponse(
            knowledge_bases=[KnowledgeBaseResponse.from_domain(kb) for kb in kbs],
            total=len(kbs),
        )

    async def get_knowledge_base(self, kb_id: int, user_id: int | None = None) -> KnowledgeBaseResponse:
        """获取知识库详情；提供 user_id 时由业务层核对所有权。"""
        kb = await self._kb_biz.get_knowledge_base(kb_id, user_id=user_id)
        return KnowledgeBaseResponse.from_domain(kb)

    async def create_knowledge_base(self, user_id: int, body: KnowledgeBaseCreateRequest) -> KnowledgeBaseResponse:
        """将创建请求中的名称、描述和图标交给业务层，返回新知识库。"""
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
        """把本次提供的可修改字段交给业务层，返回更新后的知识库。"""
        kb = await self._kb_biz.update_knowledge_base(
            kb_id=kb_id,
            user_id=user_id,
            name=body.name,
            description=body.description,
            icon=body.icon,
        )
        return KnowledgeBaseResponse.from_domain(kb)

    async def delete_knowledge_base(self, kb_id: int, user_id: int) -> None:
        """传入知识库 ID 与当前用户 ID，由业务层校验归属并执行关联删除。"""
        await self._kb_biz.delete_knowledge_base(kb_id=kb_id, user_id=user_id)







