"""业务对象和调用结果的数据结构；持久化转换与接口序列化由各自调用层完成。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from internal.biz.constants import (
    DocumentStatus,
    EmbeddingStatus,
    KbStatus,
    UserStatus,
)

if TYPE_CHECKING:
    from datetime import datetime

    from internal.biz.tool_types import ToolCall


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
    """知识库的业务对象，记录所有者、基本信息和默认嵌入及分块配置。"""

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
    # 文档列表由仓储聚合分块总数及各嵌入状态数量，前端无需逐文档请求分块接口。
    stages: dict = field(default_factory=dict)
    chunk_count: int = 0
    embedding_pending_count: int = 0
    embedding_failed_count: int = 0
    embedding_completed_count: int = 0


@dataclass
class Chunk:
    """文档分块领域对象 — 解析后文本的切片。

    每个 chunk 属于一个 document，有独立 ID、内容、位置序号。
    embedding_status 记录该分块的向量索引状态，供入库重试使用。
    """

    id: int | None
    document_id: int
    position: int           # 第几块（从 0 开始）
    content: str            # 分块文本内容
    summary: str | None = None  # 可选摘要，当前入库流程不生成。
    related_questions: list[str] | None = None  # 可选关联问题，当前入库流程不生成。
    token_count: int = 0    # token 估算值（与分块器使用同一估算器）
    embedding_status: EmbeddingStatus = EmbeddingStatus.PENDING
    created_at: datetime | None = None


# 保留每一路检索或重排的原始分数，便于追踪最终排序的依据。
@dataclass(frozen=True)
class SourceScore:
    kind: str
    value: float
    method: str


# 在检索器与业务层之间传递候选分块，业务层随后补齐正文和文档信息。
@dataclass
class RetrievedChunk:
    chunk_id: int
    score: float
    score_type: str
    score_method: str
    scores: list[SourceScore] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """检索结果值对象。

    包含分块信息、相似度分数、来源文档信息。
    """

    chunk_id: int
    score: float | None       # 当前排序分；类型与计算方法见 score_type / score_method
    chunk_content: str        # 分块文本内容
    chunk_position: int       # 分块位置
    document_id: int          # 来源文档 ID
    document_filename: str    # 来源文档文件名
    knowledge_base_id: int    # 来源知识库 ID
    document_updated_at: datetime | None = None  # 来源文档更新/创建时间（前端时间筛选）

    score_type: str | None = None
    score_method: str | None = None
    scores: list[SourceScore] = field(default_factory=list)

    def with_score(self, new_score: float, method: str) -> RetrievalResult:
        """保留召回分，另登记真实重排分及计算方式。"""
        return replace(self, score=new_score, score_type="rerank", score_method=method,
                       scores=[*self.scores, SourceScore("rerank", new_score, method)])


@dataclass
class ChatResult:
    """LLM 对话结果值对象。

    包含模型回复内容、token 用量、模型名称。
    """

    content: str              # LLM 回复内容
    model: str                # 使用的模型名称
    prompt_tokens: int = 0    # Prompt 消耗 token 数
    completion_tokens: int = 0  # Completion 消耗 token 数
    total_tokens: int = 0     # 总消耗 token 数
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

    def to_message(self) -> dict:
        """保留原生调用编号、名称和参数，供对应 tool 结果接回模型。"""
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        return message


@dataclass
class EmbeddingUsage:
    """嵌入 API token 用量

    来源优先级：Provider API 真实 usage > 基于字符数的估算值。
    Embedding 场景下 prompt_tokens 与 total_tokens 通常相等。
    """

    prompt_tokens: int = 0    # 输入文本消耗 token 数
    total_tokens: int = 0     # 总消耗 token 数（embedding 场景等同 prompt_tokens）


@dataclass
class EmbeddingResult:
    """嵌入调用结果，包含按输入顺序排列的向量、模型名、维度及用量记录。"""

    vectors: list[list[float]]              # 向量列表，顺序与输入一致
    model: str                              # 使用的模型名称
    dimension: int                          # 向量维度
    usage: EmbeddingUsage = field(default_factory=EmbeddingUsage)
