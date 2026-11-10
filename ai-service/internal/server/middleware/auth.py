"""校验非白名单请求的 Bearer 访问令牌，将用户 ID 写入 request.state。

将请求 User-Agent 传给 SessionManager 检查会话；认证失败直接构造统一 JSON 错误响应。
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

    # 无需访问令牌的公开路径；dispatch 同时放行这些路径的子路径。
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
        """注入会话校验器，以及令牌请求头名称和前缀等会话配置。"""
        super().__init__(app)
        self._session_manager = session_manager
        self._session_config = session_config

    def _error_response(self, code: int, reason: str, message: str) -> JSONResponse:
        """直接返回 code/reason/message 错误响应，避免依赖路由层异常处理器接收中间件异常。"""
        return JSONResponse(
            status_code=safe_http_status(code),
            content={"code": code, "reason": reason, "message": message},
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """放行公开路径；其余请求校验访问令牌及 User-Agent，成功后写入当前用户 ID。"""
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
