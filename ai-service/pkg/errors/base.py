"""
pkg/errors/base.py — 统一错误模型（框架无关）

设计原则：
- 错误模型是"契约"，放在 pkg/ 供所有层（server/service/biz/data）共用。
- 本文件只定义纯数据结构 + Exception 继承，不依赖任何 Web 框架。
  FastAPI 异常处理器在 pkg/errors/fastapi.py 中，与模型定义隔离。
- 用 dataclass 而非 Pydantic BaseModel：异常需要继承 Exception，
  BaseModel 不支持与 Exception 多继承。
- code 是 int（非 str/enum），编码规则：HTTP status * 1000 + 业务序号。
  int 可直接做算术（code // 1000 → HTTP status）。
- metadata 只写日志，不返回给客户端。
- 业务子类有默认 code/reason/message，避免开发者手动传错 code。
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


# 错误码 → HTTP status 的显式映射。
# 注意：codes.py 中 4xx 错误码实际编码为 400xxx（未按 HTTP*1000 规则分段），
# 直接 code // 1000 会把 401/403/404 都算成 400，导致前端无法区分错误类型
# （例如 token 过期应返回 401 触发登出，却被压成 400）。这里显式纠正，
# 既不改变错误码数值（保持契约稳定），又让 HTTP status 正确。
_STATUS_OVERRIDES: dict[int, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.USER_DISABLED: 403,
    ErrorCode.RATE_LIMITED: 429,
}


def safe_http_status(code: int) -> int:
    """把错误码映射到合法的 HTTP status code（约束到 400~599）。

    优先使用 _STATUS_OVERRIDES 中针对 4xx/429 的显式映射（见上方说明），
    其余按编码规则 code // 1000 推算（如 400001 // 1000 = 400、409001 -> 409）。
    约束到 400~599：业务错误一定是 4xx 或 5xx。
    """
    if code in _STATUS_OVERRIDES:
        return _STATUS_OVERRIDES[code]
    return min(max(code // 1000, 400), 599)
