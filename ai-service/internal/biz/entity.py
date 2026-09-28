"""
internal/biz/entity.py — 领域对象（DO）

biz 层操作的核心数据结构，用 dataclass 定义。
与 API DTO（Pydantic Model）和持久化对象（PO）分离。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from internal.biz.constants import (
    DocumentStatus,
    EmbeddingStatus,
    KbStatus,
    UserStatus,
)


@dataclass
class User:
    """用户领域对象。业务层只使用这里的字段，不直接碰数据库行或 HTTP 模型。"""

    id: int | None
    email: str
    nickname: str
    password_hash: str
    status: UserStatus = UserStatus.ACTIVE
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        """用户是否处于正常状态。"""
        return self.status == UserStatus.ACTIVE


@dataclass
class KnowledgeBase:
    """知识库领域对象 — 文档的容器。

    原先只有 documents.knowledge_base_id 整数，没有实体表；
    前端用 localStorage 自增 id，换设备/清缓存即丢，删除也不级联文档。
    """

    id: int | None
    name: str
    description: str = ""
    icon: str = "folder"
    status: KbStatus = KbStatus.NORMAL
    owner_id: int = 0
    embedding_model: str = ""
    chunk_strategy: str = "recursive"
    chunk_size: int = 500
    chunk_overlap: int = 50
    created_at: datetime | None = None
    updated_at: datetime | None = None
    doc_count: int = 0
    chunk_count: int = 0


@dataclass
class Document:
    """文档领域对象 — 知识库中的文件。解析后产生分块。"""

    id: int | None
    knowledge_base_id: int
    user_id: int
    filename: str           # 原始文件名
    file_path: str          # 存储路径（相对或绝对）
    file_size: int          # 文件大小（字节）
    mime_type: str          # MIME 类型（如 application/pdf）
    status: DocumentStatus = DocumentStatus.PENDING
    parsed_content: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 分块（向量）数量 — 由 data 层在 list_by_kb 时聚合注入，
    # 避免前端逐文档请求 chunks 接口触发限流（heavy 限额 10/分钟）。
    chunk_count: int = 0
    embedding_pending_count: int = 0
    embedding_failed_count: int = 0
    embedding_completed_count: int = 0


@dataclass
class Chunk:
    """文档分块领域对象 — 解析后文本的切片。

    每个 chunk 属于一个 document，有独立 ID、内容、位置序号。
    Sprint 7 新增 embedding_status 追踪嵌入进度。
    """

    id: int | None
    document_id: int
    position: int           # 第几块（从 0 开始）
    content: str            # 分块文本内容
    token_count: int = 0    # token 估算值（与分块器使用同一估算器）
    embedding_status: EmbeddingStatus = EmbeddingStatus.PENDING
    created_at: datetime | None = None


@dataclass
class RetrievalResult:
    """检索结果值对象 — Sprint 8。

    包含分块信息、相似度分数、来源文档信息。
    """

    chunk_id: int
    score: float              # 相似度分数（0~1，越高越相关）
    chunk_content: str        # 分块文本内容
    chunk_position: int       # 分块位置
    document_id: int          # 来源文档 ID
    document_filename: str    # 来源文档文件名
    knowledge_base_id: int    # 来源知识库 ID
    document_updated_at: datetime | None = None  # 来源文档更新/创建时间（前端时间筛选）

    def with_score(self, new_score: float) -> RetrievalResult:
        """创建新实例并更新 score — 用于 Reranker 重排序时更新分数。"""
        return replace(self, score=round(new_score, 6))


@dataclass
class ChatResult:
    """LLM 对话结果值对象 — Sprint 10。

    包含模型回复内容、token 用量、模型名称。
    """

    content: str              # LLM 回复内容
    model: str                # 使用的模型名称
    prompt_tokens: int = 0    # Prompt 消耗 token 数
    completion_tokens: int = 0  # Completion 消耗 token 数
    total_tokens: int = 0     # 总消耗 token 数


@dataclass
class ChatChunk:
    """LLM 流式对话分片值对象 — Sprint 11。

    每个 chunk 包含一小段增量内容（delta），
    客户端逐步接收并拼接为完整回复。
    """

    delta: str                # 增量文本内容
    model: str                # 使用的模型名称
    finish_reason: str | None = None  # 结束原因（null=进行中，"stop"=结束）


@dataclass
class EmbeddingUsage:
    """嵌入 API token 用量 — Sprint 17 重构。

    来源优先级：Provider API 真实 usage > 基于字符数的估算值。
    Embedding 场景下 prompt_tokens 与 total_tokens 通常相等。
    """

    prompt_tokens: int = 0    # 输入文本消耗 token 数
    total_tokens: int = 0     # 总消耗 token 数（embedding 场景等同 prompt_tokens）


@dataclass
class EmbeddingResult:
    """嵌入结果值对象 — Sprint 17 重构。

    封装向量列表 + 模型元信息 + token 用量，
    避免 Provider 接口丢失 usage 导致 Service 层只能估算。
    """

    vectors: list[list[float]]              # 向量列表，顺序与输入一致
    model: str                              # 使用的模型名称
    dimension: int                          # 向量维度
    usage: EmbeddingUsage = field(default_factory=EmbeddingUsage)

