"""
internal/server/middleware/security.py — 安全响应头中间件

添加标准安全 HTTP 头，防止常见的 Web 攻击：
- X-Content-Type-Options: nosniff — 防止 MIME 类型嗅探
- X-Frame-Options: DENY — 防止点击劫持
- X-XSS-Protection: 0 — 现代浏览器建议关闭（用 CSP 替代）
- Referrer-Policy: strict-origin-when-cross-origin — 限制 Referer 泄露
- Strict-Transport-Security — 强制 HTTPS（仅 HTTPS 有效）
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """安全响应头中间件 — 为所有响应添加标准安全头。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        return response
