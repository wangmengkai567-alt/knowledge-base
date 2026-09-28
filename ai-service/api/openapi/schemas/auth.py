"""
api/openapi/schemas/auth.py — 认证相关 DTO（Pydantic Model）

DTO ↔ DO 转换在 Service 层完成。
DTO 对外暴露（OpenAPI schema），DO 内部使用。
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from internal.biz.entity import User


class RegisterRequest(BaseModel):
    """注册请求 DTO。"""

    email: EmailStr = Field(description="登录邮箱，全局唯一")
    password: str = Field(min_length=8, max_length=64, description="密码，8~64 字符")
    nickname: str = Field(min_length=1, max_length=64, description="显示名称")


class LoginRequest(BaseModel):
    """登录请求 DTO。"""

    email: EmailStr = Field(description="登录邮箱")
    password: str = Field(description="密码")


class RefreshTokenRequest(BaseModel):
    """刷新 token 请求 DTO。"""

    refresh_token: str = Field(description="刷新令牌")


class UserResponse(BaseModel):
    """用户信息响应 DTO。"""

    user_id: int = Field(description="用户 ID")
    email: str = Field(description="登录邮箱")
    nickname: str = Field(description="显示名称")
    status: int = Field(description="用户状态：1=正常, 2=禁用, 3=待激活")

    @classmethod
    def from_domain(cls, user: User) -> UserResponse:
        """DO → DTO 转换。"""
        return cls(
            user_id=user.id,
            email=user.email,
            nickname=user.nickname,
            status=user.status,
        )


class LoginResponse(BaseModel):
    """登录响应 DTO。"""

    access_token: str = Field(description="访问令牌，用于 API 鉴权")
    refresh_token: str = Field(description="刷新令牌，用于刷新 access_token")
    expires_in: int = Field(description="access_token 有效期（秒）")
    user: UserResponse = Field(description="用户信息")


class RefreshTokenResponse(BaseModel):
    """刷新 token 响应 DTO。"""

    access_token: str = Field(description="新的访问令牌")
    refresh_token: str = Field(description="新的刷新令牌")
    expires_in: int = Field(description="access_token 有效期（秒）")
