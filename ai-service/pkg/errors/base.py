"""业务异常的数据结构与 HTTP 状态映射。

code 表示错误分类，reason 表示具体原因，message 用于对外响应，metadata 提供内部诊断信息。
HTTP 状态统一通过 safe_http_status() 计算，部分业务码需使用显式映射。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pkg.errors.codes import ErrorCode


@dataclass
class APIError(Exception):
    """统一错误模型。

    code + reason 双层定位：code 是大类（HTTP status + 业务分类），
    reason 是具体原因（如 LLM_RATE_LIMITED）。
    """

    code: int
    reason: str
    message: str
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        """将错误码、原因和公开说明组合成异常字符串。"""
        return f"code={self.code} reason={self.reason}: {self.message}"


# ── 业务错误子类 ──
# 每个子类有默认 code/reason/message，防止开发者传错 code。
# 调用方仍可覆盖默认值：LLMError(reason="LLM_TIMEOUT", message="Request timed out")


@dataclass
class LLMError(APIError):
    """LLM 推理相关错误（rate limit / timeout / invalid request）。"""

    code: int = ErrorCode.LLM_ERROR
    reason: str = "LLM_ERROR"
    message: str = "LLM inference error"
    metadata: dict = field(default_factory=dict)


@dataclass
class EmbeddingError(APIError):
    """Embedding 相关错误（API 调用失败 / 维度不匹配 / 批量失败）。"""

    code: int = ErrorCode.EMBEDDING_ERROR
    reason: str = "EMBEDDING_ERROR"
    message: str = "Embedding service error"
    metadata: dict = field(default_factory=dict)


@dataclass
class RAGError(APIError):
    """RAG 检索相关错误（向量库连接 / 检索无结果 / 分块 / 重排序）。"""

    code: int = ErrorCode.RAG_ERROR
    reason: str = "RAG_ERROR"
    message: str = "RAG retrieval error"
    metadata: dict = field(default_factory=dict)


@dataclass
class DatabaseError(APIError):
    """数据库相关错误（连接失败 / 查询超时 / 数据校验）。"""

    code: int = ErrorCode.DATABASE_ERROR
    reason: str = "DATABASE_ERROR"
    message: str = "Database error"
    metadata: dict = field(default_factory=dict)


# 400xxx 中包含认证、权限和不存在等不同错误，不能仅用除以 1000 得到 HTTP 状态。
# 这些业务码先查下表；其他业务码按分类计算。
_STATUS_OVERRIDES: dict[int, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.USER_DISABLED: 403,
    ErrorCode.RATE_LIMITED: 429,
}


def safe_http_status(code: int) -> int:
    """优先查找业务码的显式 HTTP 映射，其余取 code // 1000，并将结果限制在 400～599。"""
    if code in _STATUS_OVERRIDES:
        return _STATUS_OVERRIDES[code]
    return min(max(code // 1000, 400), 599)
