"""在 Redis 中保存访问令牌、刷新令牌及按用户组织的令牌键集合。

Redis 键由 session_prefix 加 access:{token}、refresh:{token} 或 user:{user_id} 组成。
创建令牌时保存用户 ID、User-Agent 摘要和创建时间；刷新会话时删除旧刷新令牌键。
登出时通过用户键集合清除其关联会话；不能定位用户时只删除传入的访问令牌键。
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
    """规范化 User-Agent 后生成摘要；空字符串以 unknown 参与计算。"""
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
        """注入 Redis 客户端、会话键前缀配置及访问/刷新令牌的有效期配置。"""
        self._redis = redis_client
        self._redis_config = redis_config
        self._session_config = session_config

    def _access_key(self, token: str) -> str:
        """用统一会话前缀构造访问令牌在 Redis 中的存储键。"""
        return f"{self._redis_config.session_prefix}access:{token}"

    def _refresh_key(self, token: str) -> str:
        """用统一会话前缀构造刷新令牌在 Redis 中的存储键，与访问令牌分开存储。"""
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
        """读取访问令牌并返回用户 ID；键不存在时返回 None。

        传入非空 user_agent 时核对已保存指纹，不一致返回 None；空 user_agent 不执行此检查。
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
        """校验刷新令牌及 User-Agent 摘要，删除该刷新键后创建新会话。

        返回 (access_token, refresh_token, expires_in)，无效时返回 None。
        此方法不主动删除旧访问令牌键。
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
        """根据访问令牌查找用户，删除用户索引中登记的全部会话键及索引键。

        无法从传入令牌定位用户时，仅删除对应访问令牌键；不修改用户账号记录。
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

        # 无法定位用户时，只删除传入访问令牌对应的 Redis 键。
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
