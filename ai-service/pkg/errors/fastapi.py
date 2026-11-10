"""注册业务异常、请求参数异常和未处理异常的 HTTP 响应处理器，统一返回 code/reason/message。"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pkg.errors.base import APIError, safe_http_status
from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


def register_exception_handlers(app: FastAPI) -> None:
    # 将业务错误、请求校验错误和未预期异常转换为统一的 HTTP 错误响应。

    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        # 返回已定义的业务错误；额外诊断信息只写日志。
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
        # 将请求格式校验失败统一返回为参数错误。
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
        # 记录内部异常，对外仅返回统一的服务错误信息。
        # traceback 交给配置好的 structlog 异常处理器格式化。
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
