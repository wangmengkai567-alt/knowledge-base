"""用户注册、密码校验、登录失败次数检查及会话管理。

用户记录由 UserRepo 读写，访问令牌和刷新令牌由 SessionManager 管理。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import bcrypt
import structlog

from internal.biz.constants import UserStatus
from internal.biz.entity import User
from internal.biz.repo import UserRepo
from internal.conf.config import SecurityConfig, SessionConfig
from pkg.errors.base import APIError
from pkg.errors.codes import ErrorCode
from pkg.session import SessionManager

logger = structlog.get_logger()

# ── 密码强度正则 ──
# 至少包含：1 个大写字母 + 1 个小写字母 + 1 个数字
# 检查密码是否包含大写字母。
_UPPERCASE_RE = re.compile(r"[A-Z]")
# 检查密码是否包含小写字母。
_LOWERCASE_RE = re.compile(r"[a-z]")
# 检查密码是否包含数字。
_DIGIT_RE = re.compile(r"\d")

# 额外拒绝的弱密码；大小写及数字要求仍分别校验。
_WEAK_PASSWORDS = frozenset({
    "password", "Password", "password1", "Password1", "P@ssw0rd", "Passw0rd", "passw0rd",
    "123456", "1234567", "12345678", "123456789", "1234567890",
    "111111", "11111111", "000000", "00000000",
    "qwerty", "qwertyui", "qwerty123", "asdfgh", "zxcvbn", "qazwsx",
    "1q2w3e4r", "1qaz2wsx", "q1w2e3r4", "123qwe",
    "abc123", "abcd1234", "admin", "admin123", "administrator", "root",
    "welcome", "welcome1", "letmein", "iloveyou", "sunshine", "princess",
    "monkey", "dragon", "football", "baseball", "shadow", "superman",
    "default", "test123", "user123",
})

# 登录失败锁定阈值
_LOGIN_FAIL_THRESHOLD = 5  # 达到此失败次数时拒绝该用户继续登录。
_LOGIN_FAIL_LOCK_SECONDS = 900  # 15 分钟


# 处理用户注册、密码校验和登录会话，账号存储与令牌管理交给注入的服务。
class AuthBiz:
    def __init__(
        self,
        user_repo: UserRepo,
        session_manager: SessionManager,
        session_config: SessionConfig,
        security_config: SecurityConfig | None = None,
    ) -> None:
        """注入用户仓储、会话管理器和认证安全配置。"""
        self._user_repo = user_repo
        self._session_manager = session_manager
        self._session_config = session_config
        self._security_config = security_config or SecurityConfig()

    async def register(self, email: str, password: str, nickname: str) -> User:
        """用户注册。

        1. 检查邮箱是否已注册
        2. 密码强度校验
        3. bcrypt 加密密码（用 asyncio.to_thread 包装，避免阻塞事件循环）
        4. 创建用户
        """
        existing = await self._user_repo.get_by_email(email)
        if existing is not None:
            raise APIError(
                code=ErrorCode.EMAIL_EXISTS,
                reason="EMAIL_EXISTS",
                message="Email already registered",
            )

        # 密码强度校验
        _validate_password_strength(password)

        # bcrypt 是 CPU 密集操作，用 to_thread 包装避免阻塞事件循环
        password_hash = await asyncio.to_thread(
            _hash_password, password, self._security_config.bcrypt_cost
        )

        user = User(
            id=None,
            email=email,
            nickname=nickname,
            password_hash=password_hash,
            status=UserStatus.ACTIVE,
        )

        created = await self._user_repo.create(user)
        logger.info("user_registered", user_id=created.id, email=created.email)
        return created

    async def login(
        self, email: str, password: str, user_agent: str = ""
    ) -> tuple[User, str, str, int]:
        """用户登录。

        1. 查用户
        2. 校验密码（bcrypt verify，用 to_thread 包装）
        3. 创建 Session（access_token + refresh_token）
        4. 更新 last_login_at
        5. 返回 (User, access_token, refresh_token, expires_in)

        安全设计：
        - 用户不存在和密码错误返回相同消息，防止枚举。
        - 登录失败计数，超过阈值锁定账户。
        """
        user = await self._user_repo.get_by_email(email)
        if user is None:
            raise APIError(
                code=ErrorCode.UNAUTHORIZED,
                reason="INVALID_CREDENTIALS",
                message="Invalid email or password",
            )

        # 暴力破解防护：检查失败次数
        await self._check_login_fail_count(user.id)

        # bcrypt 校验也是 CPU 密集操作
        valid = await asyncio.to_thread(
            _verify_password, password, user.password_hash
        )
        if not valid:
            await self._record_login_fail(user.id)
            raise APIError(
                code=ErrorCode.UNAUTHORIZED,
                reason="INVALID_CREDENTIALS",
                message="Invalid email or password",
            )

        if user.status == UserStatus.DISABLED:
            raise APIError(
                code=ErrorCode.USER_DISABLED,
                reason="USER_DISABLED",
                message="User account is disabled",
            )

        access_token, refresh_token, expires_in = await self._session_manager.create_session(
            user.id, user_agent
        )

        await self._user_repo.update_last_login(user.id, datetime.now(timezone.utc))

        logger.info("user_login", user_id=user.id, email=user.email)
        return user, access_token, refresh_token, expires_in

    async def logout(self, access_token: str) -> None:
        """用户登出 — 销毁 Session（含 access + refresh token）。"""
        await self._session_manager.destroy_session(access_token)
        logger.info("user_logout")

    async def get_current_user(self, user_id: int) -> User:
        """根据 user_id 查用户。"""
        user = await self._user_repo.get_by_id(user_id)
        if user is None:
            raise APIError(
                code=ErrorCode.NOT_FOUND,
                reason="USER_NOT_FOUND",
                message="User not found",
            )
        return user

    async def refresh_token(
        self, refresh_token: str, user_agent: str = ""
    ) -> tuple[str, str, int] | None:
        """校验刷新令牌并创建新会话，返回 (access_token, refresh_token, expires_in)。

        SessionManager 返回无效结果时抛出未认证错误，不返回 None。
        """
        result = await self._session_manager.refresh_session(refresh_token, user_agent)
        if result is None:
            raise APIError(
                code=ErrorCode.UNAUTHORIZED,
                reason="REFRESH_TOKEN_INVALID",
                message="Refresh token is invalid or expired",
            )
        return result

    async def _check_login_fail_count(self, user_id: int) -> None:
        """读取 Redis 中的登录失败次数；达到阈值时抛出限流错误，不修改用户账户状态。"""
        count = await self._session_manager.get_login_fail_count(user_id)
        if count >= _LOGIN_FAIL_THRESHOLD:
            raise APIError(
                code=ErrorCode.RATE_LIMITED,
                reason="ACCOUNT_LOCKED",
                message="Too many failed login attempts. Please try again later.",
            )

    async def _record_login_fail(self, user_id: int) -> None:
        """记录登录失败次数。"""
        await self._session_manager.record_login_fail(user_id, _LOGIN_FAIL_LOCK_SECONDS)
        logger.warning("login_failed", user_id=user_id)


def _validate_password_strength(password: str) -> None:
    """检查密码是否命中弱密码表，并要求包含大写字母、小写字母和数字；失败抛出 APIError。"""
    if password in _WEAK_PASSWORDS:
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            reason="WEAK_PASSWORD",
            message="Password is too common. Please choose a stronger password.",
        )

    if not _UPPERCASE_RE.search(password):
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            reason="WEAK_PASSWORD",
            message="Password must contain at least one uppercase letter.",
        )

    if not _LOWERCASE_RE.search(password):
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            reason="WEAK_PASSWORD",
            message="Password must contain at least one lowercase letter.",
        )

    if not _DIGIT_RE.search(password):
        raise APIError(
            code=ErrorCode.INVALID_REQUEST,
            reason="WEAK_PASSWORD",
            message="Password must contain at least one digit.",
        )


def _hash_password(password: str, cost: int = 12) -> str:
    """使用 cost 指定的 bcrypt 工作因子生成密码哈希；调用方通过工作线程执行。"""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=cost)
    ).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    """bcrypt 校验密码 — 同步函数，通过 asyncio.to_thread 调用。"""
    return bcrypt.checkpw(
        password.encode("utf-8"), password_hash.encode("utf-8")
    )
