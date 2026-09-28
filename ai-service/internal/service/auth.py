"""
internal/service/auth.py — 认证应用层

职责：DTO 校验 → 调 biz → DTO 返回。
不含业务逻辑，只做 DTO ↔ DO 转换。
"""

from __future__ import annotations

from api.openapi.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshTokenResponse,
    RegisterRequest,
    UserResponse,
)
from internal.biz.auth import AuthBiz


class AuthService:
    """认证应用层 — DTO 校验 + 调 biz + DTO 返回。"""

    def __init__(self, auth_biz: AuthBiz) -> None:
        self._auth_biz = auth_biz

    async def register(self, request: RegisterRequest) -> UserResponse:
        """注册 — DTO → DO → 调 biz → DO → DTO。"""
        user = await self._auth_biz.register(
            email=request.email,
            password=request.password,
            nickname=request.nickname,
        )
        return UserResponse.from_domain(user)

    async def login(
        self, request: LoginRequest, user_agent: str = ""
    ) -> LoginResponse:
        """登录 — DTO → DO → 调 biz → DO → DTO。"""
        user, access_token, refresh_token, expires_in = await self._auth_biz.login(
            email=request.email,
            password=request.password,
            user_agent=user_agent,
        )
        return LoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            user=UserResponse.from_domain(user),
        )

    async def logout(self, access_token: str) -> None:
        """登出。"""
        await self._auth_biz.logout(access_token)

    async def get_current_user(self, user_id: int) -> UserResponse:
        """获取当前用户信息。"""
        user = await self._auth_biz.get_current_user(user_id)
        return UserResponse.from_domain(user)

    async def refresh_token(
        self, refresh_token: str, user_agent: str = ""
    ) -> RefreshTokenResponse:
        """刷新 token。"""
        access, refresh, expires_in = await self._auth_biz.refresh_token(
            refresh_token, user_agent
        )
        return RefreshTokenResponse(
            access_token=access,
            refresh_token=refresh,
            expires_in=expires_in,
        )
