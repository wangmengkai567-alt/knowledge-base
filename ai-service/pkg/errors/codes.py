"""业务错误码常量；HTTP 状态由 base.safe_http_status() 的显式映射和分类规则确定。"""

from __future__ import annotations

from typing import ClassVar


class ErrorCode:
    """错误码常量。

    这是常量容器类，不是 enum，不应实例化。
    所有属性是 ClassVar[int]，通过 ErrorCode.INVALID_REQUEST 访问。
    """

    __init__ = None  # type: ignore[assignment]  # 阻止实例化

    # ── 4xx 客户端错误 ──

    INVALID_REQUEST: ClassVar[int] = 400001      # 请求参数不合法（DTO 校验失败）
    UNAUTHORIZED: ClassVar[int] = 400002          # 未认证（缺少/无效 token）
    FORBIDDEN: ClassVar[int] = 400003             # 禁止访问（权限不足）
    NOT_FOUND: ClassVar[int] = 400004             # 资源不存在
    USER_DISABLED: ClassVar[int] = 400005         # 用户账户已禁用
    RATE_LIMITED: ClassVar[int] = 400006           # 请求过于频繁（限流）
    CONFLICT: ClassVar[int] = 409001              # 资源冲突（通用）
    EMAIL_EXISTS: ClassVar[int] = 409002          # 邮箱已注册（冲突）

    # ── 5xx 服务端错误 ──
    
    INTERNAL_ERROR: ClassVar[int] = 500001        # 未分类的服务端内部错误
    LLM_ERROR: ClassVar[int] = 500002             # LLM 推理服务错误
    LLM_TIMEOUT: ClassVar[int] = 500003           # LLM 推理服务超时
    EMBEDDING_ERROR: ClassVar[int] = 500004       # Embedding 服务错误
    RAG_ERROR: ClassVar[int] = 500005             # RAG 检索服务错误
    DATABASE_ERROR: ClassVar[int] = 500010        # 数据库服务错误
