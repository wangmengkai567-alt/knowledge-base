"""
internal/data/converter.py — PO ↔ DO 转换器

data 层负责 PO ↔ DO 转换，biz 层永远只操作 DO。
"""

from __future__ import annotations

from internal.biz.constants import (
    DocumentStatus,
    EmbeddingStatus,
    KbStatus,
    UserStatus,
)
from internal.biz.entity import Chunk, Document, KnowledgeBase, User
from internal.data.models import ChunkPO, DocumentPO, KnowledgeBasePO, UserPO


def user_po_to_do(po: UserPO) -> User:
    """PO → DO 转换。"""
    return User(
        id=po.id,
        email=po.email,
        nickname=po.nickname,
        password_hash=po.password_hash,
        status=UserStatus(po.status),
        last_login_at=po.last_login_at,
        created_at=po.created_at,
        updated_at=po.updated_at,
    )


def knowledge_base_po_to_do(po: KnowledgeBasePO) -> KnowledgeBase:
    """KnowledgeBase PO → DO 转换。"""
    return KnowledgeBase(
        id=po.id,
        name=po.name,
        description=po.description or "",
        icon=po.icon or "folder",
        status=KbStatus(po.status),
        owner_id=po.owner_id,
        embedding_model=po.embedding_model or "",
        chunk_strategy=po.chunk_strategy or "recursive",
        chunk_size=po.chunk_size,
        chunk_overlap=po.chunk_overlap,
        created_at=po.created_at,
        updated_at=po.updated_at,
    )


def knowledge_base_do_to_po(kb: KnowledgeBase) -> KnowledgeBasePO:
    """KnowledgeBase DO → PO 转换（用于创建）。"""
    po = KnowledgeBasePO(
        name=kb.name,
        description=kb.description,
        icon=kb.icon,
        status=int(kb.status),
        owner_id=kb.owner_id,
        embedding_model=kb.embedding_model,
        chunk_strategy=kb.chunk_strategy,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
    )
    if kb.id is not None:
        po.id = kb.id
    return po


def document_po_to_do(po: DocumentPO) -> Document:
    """Document PO → DO 转换。"""
    return Document(
        id=po.id,
        knowledge_base_id=po.knowledge_base_id,
        user_id=po.user_id,
        filename=po.filename,
        file_path=po.file_path,
        file_size=po.file_size,
        mime_type=po.mime_type,
        status=DocumentStatus(po.status),
        parsed_content=po.parsed_content,
        error_message=po.error_message,
        created_at=po.created_at,
        updated_at=po.updated_at,
    )


def document_do_to_po(document: Document) -> DocumentPO:
    """Document DO → PO 转换（用于创建）。"""
    return DocumentPO(
        knowledge_base_id=document.knowledge_base_id,
        user_id=document.user_id,
        filename=document.filename,
        file_path=document.file_path,
        file_size=document.file_size,
        mime_type=document.mime_type,
        status=document.status,
        parsed_content=document.parsed_content,
        error_message=document.error_message,
    )


def chunk_po_to_do(po: ChunkPO) -> Chunk:
    """Chunk PO → DO 转换。"""
    return Chunk(
        id=po.id,
        document_id=po.document_id,
        position=po.position,
        content=po.content,
        token_count=po.token_count,
        embedding_status=EmbeddingStatus(po.embedding_status),
        created_at=po.created_at,
    )


def chunk_do_to_po(chunk: Chunk) -> ChunkPO:
    """Chunk DO → PO 转换（用于创建）。"""
    return ChunkPO(
        document_id=chunk.document_id,
        position=chunk.position,
        content=chunk.content,
        token_count=chunk.token_count,
        embedding_status=chunk.embedding_status,
    )
