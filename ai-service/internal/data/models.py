"""
internal/data/models.py — SQLAlchemy ORM 模型（PO — Persistent Object）

PO 与 DO（entity.User）分离：
- PO 映射数据库表结构，data 层专用
- DO 是领域对象，biz 层专用
- converter.py 负责 PO ↔ DO 转换
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类。"""

    pass


class UserPO(Base):
    """users 表 ORM 模型。"""

    __tablename__ = "users"
    __table_args__ = (
        # 与 constants.UserStatus(1=ACTIVE, 2=DISABLED, 3=PENDING) 保持一致
        CheckConstraint("status IN (1, 2, 3)", name="ck_users_status"),
    )

    # BigInteger().with_variant(Integer, "sqlite")：
    # SQLite 需要 INTEGER PRIMARY KEY 才能自增，BigInteger 不行。
    # PostgreSQL 用 BIGINT，SQLite 用 INTEGER。
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeBasePO(Base):
    """knowledge_bases 表 ORM 模型。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint("status IN (1, 2, 3)", name="ck_kb_status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    icon: Mapped[str] = mapped_column(String(32), nullable=False, default="folder")
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    owner_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
        index=True,
    )
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    chunk_strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="recursive")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=500)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DocumentPO(Base):
    """documents 表 ORM 模型。"""

    __tablename__ = "documents"
    __table_args__ = (
        # 与 constants.DocumentStatus(1=PENDING, 2=PARSED, 3=ERROR, 4=PROCESSING) 保持一致
        CheckConstraint("status IN (1, 2, 3, 4)", name="ck_documents_status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    knowledge_base_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    parsed_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ChunkPO(Base):
    """chunks 表 ORM 模型。"""

    __tablename__ = "chunks"
    __table_args__ = (
        # 与 constants.EmbeddingStatus(0=PENDING, 1=COMPLETED, 2=FAILED) 保持一致
        CheckConstraint("embedding_status IN (0, 1, 2)", name="ck_chunks_embedding_status"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    document_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

