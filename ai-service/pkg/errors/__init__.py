"""
pkg/errors — 统一错误模型 & 异常处理器。

公开 API：
    from pkg.errors import APIError, LLMError, register_exception_handlers
"""

from pkg.errors.base import (
    APIError,
    DatabaseError,
    EmbeddingError,
    LLMError,
    RAGError,
    safe_http_status,
)
from pkg.errors.codes import ErrorCode
from pkg.errors.fastapi import register_exception_handlers

__all__ = [
    "APIError",
    "DatabaseError",
    "EmbeddingError",
    "LLMError",
    "RAGError",
    "ErrorCode",
    "register_exception_handlers",
    "safe_http_status",
]
