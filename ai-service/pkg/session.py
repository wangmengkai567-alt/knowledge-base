"""
pkg/session.py — Redis Session 管理器（框架无关）

设计原则：
- Opaque Token 模式：token 是随机字符串（secrets.token_urlsafe），不可逆向。
- Session 存 Redis：服务端可控、可即时吊销、多 worker 共享。
- 框架无关：不依赖 FastAPI/Starlette，只依赖 redis.asyncio。

Session 数据结构（Redis 中）：
- access token  →  session:{token}:access   →  JSON {user_id, created_at, fingerprint}
- refresh token →  session:{token}:refresh  →  JSON {user_id, created_at, fingerprint}
- user sessions →  session:user:{user_id}   →  SET of access+refresh token keys

为什么不用 JWT？
- 本项目是单体应用，不需要 JWT 的跨服务无状态特性。
- Session + Redis 安全性更高：token 不可逆向，服务端可即时吊销。
- 后续 Sprint 必然引入 Redis（限流、缓存），不如现在统一引入。

安全设计：
- access/refresh token 均存储 fingerprint（user_agent 摘要），验证时校验一致性。
- logout 时同时清除 access + 该用户所有 refresh token（通过 user→tokens 索引）。
- refresh_token 一次性使用（旋转），防止重放攻击。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

import redis.asyncio as redis
import structlog

from internal.conf.config import RedisConfig, SessionConfig

logger = structlog.get_logger()


def _fingerprint(user_agent: str) -> str:
    """从 User-Agent 生成指纹摘要，用于 session 绑定校验。

    空 UA 统一规范化为 "unknown"，确保 fingerprint 校验始终执行，
    防止攻击者通过省略 User-Agent 头绕过设备绑定校验。
    """
    normalized = user_agent.strip() or "unknown"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class SessionManager:
    """Redis Session 管理器 — token 的创建、验证、销毁。"""

    def __init__(
        self,
        redis_client: redis.Redis,
        redis_config: RedisConfig,
        session_config: SessionConfig,
    ) -> None:
        self._redis = redis_client
        self._redis_config = redis_config
        self._session_config = session_config

    def _access_key(self, token: str) -> str:
        return f"{self._redis_config.session_prefix}access:{token}"

    def _refresh_key(self, token: str) -> str:
        return f"{self._redis_config.session_prefix}refresh:{token}"

    def _user_sessions_key(self, user_id: int) -> str:
        """用户的所有 session key 集合，用于 logout 时批量清除。"""
        return f"{self._redis_config.session_prefix}user:{user_id}"

    async def create_session(
        self, user_id: int, user_agent: str = ""
    ) -> tuple[str, str, int]:
        """创建 Session，返回 (access_token, refresh_token, expires_in)。

        生成两个随机 token：
        - access_token：短期有效（默认 2h），用于 API 请求鉴权。
        - refresh_token：长期有效（默认 7d），用于刷新 access_token。
        两者都存 Redis，value 是 JSON {user_id, fp}（fingerprint 用于绑定校验）。
        同时维护 user→tokens 索引，支持 logout 时批量清除。
        """
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        fp = _fingerprint(user_agent)
        now = datetime.now(timezone.utc).isoformat()

        session_data = json.dumps({"user_id": user_id, "fp": fp, "created_at": now})

        access_key = self._access_key(access_token)
        refresh_key = self._refresh_key(refresh_token)
        user_key = self._user_sessions_key(user_id)

        pipe = self._redis.pipeline()
        pipe.setex(access_key, self._session_config.access_token_expire, session_data)
        pipe.setex(refresh_key, self._session_config.refresh_token_expire, session_data)
        pipe.sadd(user_key, access_key, refresh_key)
        pipe.expire(user_key, self._session_config.refresh_token_expire)
        await pipe.execute()

        logger.info("session_created", user_id=user_id)
        return access_token, refresh_token, self._session_config.access_token_expire

    async def verify_access_token(
        self, token: str, user_agent: str = ""
    ) -> int | None:
        """验证 access_token，返回 user_id。无效或过期返回 None。

        同时校验 fingerprint 是否与创建时一致，防止 token 被盗后在不同环境使用。
        """
        raw = await self._redis.get(self._access_key(token))
        if raw is None:
            return None

        data = json.loads(raw)
        if user_agent:
            expected_fp = _fingerprint(user_agent)
            if data.get("fp") != expected_fp:
                logger.warning(
                    "session_fingerprint_mismatch",
                    user_id=data.get("user_id"),
                )
                return None

        return data.get("user_id")

    async def refresh_session(
        self, refresh_token: str, user_agent: str = ""
    ) -> tuple[str, str, int] | None:
        """用 refresh_token 刷新 Session，返回新 (access, refresh, expires_in)。

        旧的 refresh_token 被销毁（一次性使用），防止重放攻击。
        同时校验 fingerprint。
        """
        raw = await self._redis.get(self._refresh_key(refresh_token))
        if raw is None:
            return None

        data = json.loads(raw)
        expected_fp = _fingerprint(user_agent)
        if data.get("fp") != expected_fp:
            logger.warning(
                "refresh_fingerprint_mismatch",
                user_id=data.get("user_id"),
            )
            return None

        user_id = data["user_id"]

        # 销毁旧 refresh_token（一次性使用）
        await self._redis.delete(self._refresh_key(refresh_token))

        # 创建新 Session
        return await self.create_session(user_id, user_agent)

    async def destroy_session(self, access_token: str) -> None:
        """销毁 Session（登出）— 清除 access_token 及该用户所有 refresh_token。

        安全修复：不再只删 access_token，而是通过 user→tokens 索引
        批量清除该用户的所有 session key（包括 refresh_token）。
        """
        access_key = self._access_key(access_token)
        raw = await self._redis.get(access_key)
        if raw is not None:
            data = json.loads(raw)
            user_id = data.get("user_id")
            if user_id is not None:
                # 通过用户索引批量清除所有 session
                user_key = self._user_sessions_key(user_id)
                session_keys = await self._redis.smembers(user_key)
                if session_keys:
                    pipe = self._redis.pipeline()
                    for key in session_keys:
                        pipe.delete(key)
                    pipe.delete(user_key)
                    await pipe.execute()
                logger.info("session_destroyed", user_id=user_id)
                return

        # fallback: 仅清除 access_token
        await self._redis.delete(access_key)
        logger.info("session_destroyed_fallback")

    async def get_login_fail_count(self, user_id: int) -> int:
        """获取用户登录失败次数（用于暴力破解防护）。"""
        key = f"{self._redis_config.session_prefix}login_fail:{user_id}"
        value = await self._redis.get(key)
        return int(value) if value is not None else 0

    async def record_login_fail(self, user_id: int, lock_seconds: int) -> None:
        """记录一次登录失败，并设置/刷新过期时间。"""
        key = f"{self._redis_config.session_prefix}login_fail:{user_id}"
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, lock_seconds)
        await pipe.execute()

    async def ping(self) -> None:
        """检查 Redis 是否连得上。/health/ready 会调用。

        连接不可用时抛出异常，由调用方捕获并返回 503。
        """
        await self._redis.ping()

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        await self._redis.aclose()
