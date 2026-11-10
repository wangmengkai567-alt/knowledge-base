"""
internal/service/auth.py — 认证应用层

接收认证请求，调用认证业务，并将结果转换为接口响应。
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


# 连接认证接口与业务逻辑，统一返回用户信息和登录令牌。
class AuthService:
    def __init__(self, auth_biz: AuthBiz) -> None:
        """注入认证业务，负责把接口请求和业务返回值转换为认证接口的数据结构。"""
        self._auth_biz = auth_biz

    async def register(self, request: RegisterRequest) -> UserResponse:
        # 调用注册业务，并将新用户转换为接口响应。
        user = await self._auth_biz.register(
            email=request.email,
            password=request.password,
            nickname=request.nickname,
        )
        return UserResponse.from_domain(user)

    async def login(
        self, request: LoginRequest, user_agent: str = ""
    ) -> LoginResponse:
        # 调用登录业务，将用户信息和登录令牌一起返回。
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
