"""
pkg/errors/fastapi.py — FastAPI 异常处理器注册

设计原则：
- 异常处理器依赖 FastAPI，与纯错误模型（base.py）隔离。
- 拆分原因：pkg/ 应可跨框架复用，模型定义不应绑定具体 Web 框架。
- 三层异常处理：APIError（业务）→ RequestValidationError（DTO 校验）→ Exception（兜底），
  保证前端永远收到统一 JSON 格式 {"code", "reason", "message"}。
- metadata 只写日志，不返回给客户端。
- 兜底异常用 exc_info=True 让 structlog 处理 traceback 格式化，
  避免在 async 路径中同步调用 traceback.format_exc() 阻塞事件循环。
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pkg.errors.base import APIError, safe_http_status
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI app 上注册全局异常处理器。

    必须在路由注册之前调用，保证路由中的异常一定能被捕获。
    """

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        logger.error(
            "api_error",
            code=exc.code,
            reason=exc.reason,
            message=exc.message,
            metadata=exc.metadata,
            path=str(request.url.path),
            method=request.method,
        )
        return JSONResponse(
            status_code=safe_http_status(exc.code),
            content={
                "code": exc.code,
                "reason": exc.reason,
                "message": exc.message,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        logger.warning(
            "validation_error",
            errors=exc.errors(),
            path=str(request.url.path),
            method=request.method,
        )
        return JSONResponse(
            status_code=400,
            content={
                "code": ErrorCode.INVALID_REQUEST,
                "reason": "INVALID_REQUEST",
                "message": str(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        # exc_info=True 让 structlog 的 format_exc_info 处理器自动格式化 traceback，
        # 避免在 async 路径中同步调用 traceback.format_exc() 阻塞事件循环。
        logger.error(
            "unexpected_error",
            error=str(exc),
            exc_info=True,
            path=str(request.url.path),
            method=request.method,
        )
        return JSONResponse(
            status_code=500,
            content={
                "code": ErrorCode.INTERNAL_ERROR,
                "reason": "INTERNAL_ERROR",
                "message": "Internal server error",
            },
        )
