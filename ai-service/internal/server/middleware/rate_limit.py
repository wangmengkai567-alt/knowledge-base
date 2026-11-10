"""根据用户、客户端 IP 及知识库路径，对请求次数执行滑动窗口限流。

命中高开销路由时使用 heavy_limit，其余使用 standard_limit；运维路径不计入。
Redis 实现通过 Lua 原子执行过期清理、计数和写入；内存实现仅保存当前进程状态。
注册中间件时先添加 RateLimit 再添加 Auth，使认证先执行并设置 user_id。
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from pkg.errors.codes import ErrorCode

logger = structlog.get_logger()


# ──────────────────────────────────────────────
# 共享运维路径常量 — rate_limit + metrics 共用
# 限流和指标中间件共用这些运维路径。

OPERATIONS_PATHS: frozenset[str] = frozenset({
    "/health/live",
    "/health/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """滑动窗口限流中间件 — per-user / per-IP。"""

    def __init__(
        self,
        app,
        rate_limiter: RateLimiter,
        standard_limit: int = 60,
        heavy_limit: int = 120,
        window_seconds: int = 60,
        enabled: bool = True,
    ) -> None:
        """配置普通/高开销路由的请求次数上限、窗口秒数及限流开关。"""
        super().__init__(app)
        self._rate_limiter = rate_limiter
        self._standard_limit = standard_limit
        self._heavy_limit = heavy_limit
        self._window_seconds = window_seconds
        self._enabled = enabled

    # 检索、问答和入库处理使用 heavy 限额；文档及分块列表使用 standard 限额。
    HEAVY_SUBSTRINGS: tuple[str, ...] = (
        "/rag",        # /api/v1/knowledge/bases/{id}/rag/query（及 /query/stream）
        "/retrieve",   # /api/v1/knowledge/bases/{id}/retrieve
        "/parse",      # 重解析：走解析 + 分块 + 嵌入
        "/retry",      # 入库阶段定向重试
    )

    def _get_rate_limit(self, path: str) -> int:
        """根据路径决定限额。"""
        # 知识库路径含下列处理子路径时使用 heavy_limit，其余路径使用 standard_limit。
        if path.startswith("/api/v1/knowledge/bases/"):
            for sub in self.HEAVY_SUBSTRINGS:
                if sub in path:
                    return self._heavy_limit
        return self._standard_limit

    # 按用户和路径中的知识库分别计数，避免访问不同知识库的请求挤占同一限流额度。
    _KB_PATH_RE = re.compile(r"/knowledge/bases/(\d+)/")

    def _get_client_key(self, request: Request) -> str:
        """生成请求计数键：已认证请求使用用户 ID，其余使用客户端 IP。

        路径中存在知识库 ID 时追加该 ID，使同一用户访问不同知识库时分别计数。
        """
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            base = f"user:{user_id}"
        else:
            client = request.client
            base = f"ip:{client.host}" if client else "ip:unknown"
        m = self._KB_PATH_RE.search(request.url.path)
        if m:
            base = f"{base}:kb:{m.group(1)}"
        return base

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """跳过关闭限流和运维路径；其余请求按计数键检查次数，超限返回 429，放行时附剩余额度。"""
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        if path in OPERATIONS_PATHS:
            return await call_next(request)

        key = self._get_client_key(request)
        limit = self._get_rate_limit(path)

        allowed, remaining, reset_at = await self._rate_limiter.check(
            key=key,
            limit=limit,
            window_seconds=self._window_seconds,
        )

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                key=key,
                path=path,
                limit=limit,
                window_seconds=self._window_seconds,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "code": ErrorCode.RATE_LIMITED,
                    "reason": "RATE_LIMITED",
                    "message": f"Rate limit exceeded. Try again in {reset_at}s.",
                },
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(reset_at),
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(
            int(time.time()) + self._window_seconds
        )
        return response


# ──────────────────────────────────────────────
# RateLimiter 抽象 + 实现


class RateLimiter(ABC):
    """请求次数检查接口，返回是否允许、剩余次数和等待秒数。"""

    @abstractmethod
    async def check(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """检查是否允许请求。

        返回:
            (allowed, remaining, reset_seconds)
        """
        ...


# 同一次 Lua 调用完成计数检查和请求登记，避免并发请求在检查后同时写入。
# 在单个 Redis EVAL 中完成：清除过期 → 计数 → 判断 → 写入，全程原子
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_start = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local window_seconds = tonumber(ARGV[4])
local member = ARGV[5]

-- 清除窗口外的旧记录
redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

-- 统计窗口内请求数
local count = redis.call('ZCARD', key)

if count >= limit then
    -- 超限：计算最早记录的 reset 时间
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_seconds = window_seconds
    if #oldest >= 2 then
        reset_seconds = math.ceil(tonumber(oldest[2]) + window_seconds - now) + 1
    end
    if reset_seconds < 1 then reset_seconds = 1 end
    return {0, 0, reset_seconds}
end

-- 允许：写入新记录 + 设置过期
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window_seconds + 1)

local remaining = limit - count - 1
if remaining < 0 then remaining = 0 end
return {1, remaining, window_seconds}
"""


class RedisRateLimiter(RateLimiter):
    """使用 Redis 有序集合保存请求时间戳，并在同一 Lua 调用中完成过期清理、限额检查和登记。"""

    def __init__(self, redis_client: Any) -> None:
        """注入异步 Redis 客户端，首次检查时加载滑动窗口 Lua 脚本。"""
        self._redis = redis_client
        self._lua_sha: str | None = None

    async def _ensure_script(self) -> str:
        """懒加载：首次调用时 SCRIPT LOAD Lua 脚本，后续复用 SHA。"""
        if self._lua_sha is None:
            self._lua_sha = await self._redis.script_load(_SLIDING_WINDOW_LUA)
        return self._lua_sha

    async def check(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """原子检查并登记请求，返回是否允许、剩余次数和等待秒数。"""
        now = time.time()
        window_start = now - window_seconds
        redis_key = f"ratelimit:{key}"
        member = f"{now}:{id(now)}"  # 唯一 member 避免 ZSET 覆盖

        sha = await self._ensure_script()
        result = await self._redis.evalsha(
            sha,
            1,             # numkeys
            redis_key,     # KEYS[1]
            now,           # ARGV[1]
            window_start,  # ARGV[2]
            limit,         # ARGV[3]
            window_seconds,# ARGV[4]
            member,        # ARGV[5]
        )

        allowed = bool(result[0])
        remaining = int(result[1])
        reset_seconds = int(result[2])
        return allowed, remaining, max(reset_seconds, 1)


class FakeRateLimiter(RateLimiter):
    """内存滑动窗口限流器。"""

    def __init__(self) -> None:
        """创建本进程内的请求时间戳字典，不跨进程共享计数。"""
        self._windows: dict[str, list[float]] = {}

    async def check(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """清除窗口外的时间戳，检查并登记当前请求；返回允许标记、剩余次数和等待秒数。"""
        now = time.time()
        window_start = now - window_seconds

        if key not in self._windows:
            self._windows[key] = []

        # 清除过期记录
        self._windows[key] = [t for t in self._windows[key] if t > window_start]

        if len(self._windows[key]) >= limit:
            oldest = self._windows[key][0]
            reset_seconds = int(oldest + window_seconds - now) + 1
            return False, 0, max(reset_seconds, 1)

        self._windows[key].append(now)
        remaining = limit - len(self._windows[key])
        return True, max(remaining, 0), window_seconds


def create_rate_limiter(use_redis: bool = False, redis_client: Any = None) -> RateLimiter:
    """use_redis=True 且传入客户端时创建 Redis 限流器；其余情况创建进程内限流器。"""
    if use_redis and redis_client is not None:
        return RedisRateLimiter(redis_client)
    return FakeRateLimiter()


@dataclass
class RateLimitSettings:
    """保存限流器、启用标记、两类请求额度和统计窗口秒数，供 HTTP 中间件使用。"""

    rate_limiter: RateLimiter
    enabled: bool = True
    standard_limit: int = 60
    heavy_limit: int = 120
    window_seconds: int = 60
