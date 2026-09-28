"""
internal/server/middleware/auth.py — Session 认证中间件

解析 Authorization header 中的 Bearer token，
通过 Redis 验证 Session，注入 request.state.user_id。

白名单路径不需要鉴权（注册/登录/health/docs）。

为什么用 JSONResponse 而非 raise APIError？
BaseHTTPMiddleware 中 raise 的异常不会被 FastAPI 的 @app.exception_handler 捕获。
必须直接返回 JSONResponse 保证统一错误格式。

安全设计：
- 验证 access_token 时同时校验 user_agent fingerprint，防止 token 被盗后跨环境使用。
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from internal.conf.config import SessionConfig
from pkg.errors.base import safe_http_status
from pkg.errors.codes import ErrorCode
from pkg.session import SessionManager


class AuthMiddleware(BaseHTTPMiddleware):
    """Session 认证中间件 — 解析 token + 验证 Session + 注入 user_id。"""

    PUBLIC_PATHS = frozenset({
        "/api/v1/users/register",
        "/api/v1/users/login",
        "/api/v1/users/token/refresh",
        "/health/live",
        "/health/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
    })

    def __init__(
        self, app, session_manager: SessionManager, session_config: SessionConfig
    ) -> None:
        super().__init__(app)
        self._session_manager = session_manager
        self._session_config = session_config

    def _error_response(self, code: int, reason: str, message: str) -> JSONResponse:
        """构造统一错误响应 — BaseHTTPMiddleware 中不能 raise，必须返回。"""
        return JSONResponse(
            status_code=safe_http_status(code),
            content={"code": code, "reason": reason, "message": message},
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path

        # 放行白名单：精确匹配或前缀匹配（如 /docs/）
        if any(path == p or path.startswith(p + "/") for p in self.PUBLIC_PATHS):
            return await call_next(request)

        auth_header = request.headers.get(self._session_config.token_header, "")
        if not auth_header.startswith(self._session_config.token_prefix):
            return self._error_response(
                ErrorCode.UNAUTHORIZED, "TOKEN_MISSING",
                "Authorization header is missing or invalid",
            )

        token = auth_header[len(self._session_config.token_prefix):]

        # 提取 User-Agent 用于 fingerprint 校验
        user_agent = request.headers.get("User-Agent", "")

        user_id = await self._session_manager.verify_access_token(token, user_agent)
        if user_id is None:
            return self._error_response(
                ErrorCode.UNAUTHORIZED, "TOKEN_INVALID",
                "Token is invalid or expired",
            )

        request.state.user_id = user_id

        return await call_next(request)
