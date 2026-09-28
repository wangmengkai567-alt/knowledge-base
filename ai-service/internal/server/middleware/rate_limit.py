"""
internal/server/middleware/rate_limit.py — 限流中间件

基于滑动窗口算法的 per-user 请求限流：
- Redis 存储请求时间戳，Lua 脚本保证原子性
- 无 Redis 客户端时退回内存 dict（FakeRateLimiter）

设计要点：
- 按 user_id 限流（已认证），未认证降级到 client IP
- 不同路由可配不同限额（heavy 路由更严格）
- 响应头返回限流信息：X-RateLimit-Limit / X-RateLimit-Remaining / X-RateLimit-Reset
- 限流触发时返回 429 + ErrorCode.RATE_LIMITED

为什么不用 slowapi？
- slowapi 基于装饰器模式，与 BaseHTTPMiddleware 集成不自然
- 自研滑动窗口更灵活，可直接用 Redis sorted set 实现
- 避免额外依赖，Redis 已在项目中使用

P0 修复（Sprint 15 Review）：
- 中间件注册顺序：RateLimit 必须在 Auth 之后注册（更内层），
  这样 Auth 先执行设置 user_id，RateLimit 后执行读取 user_id。
- Redis 限流器使用 Lua 脚本保证 check+add 原子性，消除 TOCTOU 竞态。
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
# 抽取原因：避免两个中间件各自维护相同列表（P2 修复）

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
        super().__init__(app)
        self._rate_limiter = rate_limiter
        self._standard_limit = standard_limit
        self._heavy_limit = heavy_limit
        self._window_seconds = window_seconds
        self._enabled = enabled

    # 知识库路由下真正消耗 LLM + 向量模型的检索增强接口才走 heavy 限额；
    # 文档列表 / 分块列表是轻量只读，归入 standard 限额（60/min），
    # 否则首页"推荐知识库"每次拉取 3 个库的 documents 都会命中 10/min 而被 429。
    HEAVY_SUBSTRINGS: tuple[str, ...] = (
        "/rag",        # /api/v1/knowledge/bases/{id}/rag/query（及 /query/stream）
        "/retrieve",   # /api/v1/knowledge/bases/{id}/retrieve
        "/parse",      # 重解析：走解析 + 分块 + 嵌入
        "/embed",      # 文档级重嵌入
    )

    def _get_rate_limit(self, path: str) -> int:
        """根据路径决定限额。"""
        # 知识库路由：仅 RAG / 检索类（消耗 LLM + embedding）走 heavy
        if path.startswith("/api/v1/knowledge/bases/"):
            for sub in self.HEAVY_SUBSTRINGS:
                if sub in path:
                    return self._heavy_limit
        return self._standard_limit

    # 知识库子路由按 kb 维度隔离限流桶：retrieve / rag 是 heavy 接口，前端检索会向
    # 全部知识库扇出（N 个库同时发 N 个请求）。若所有 kb 共享同一个 per-user 全局桶，
    # 一次检索动作（retrieve×N + rag×N）会瞬间打满 heavy_limit，导致大面积 429。
    # 改为按 kb 独立成桶后，单次动作每个库只占 1 个名额，扇出不再互相挤占，
    # 从根本上消除"一搜就 429"。
    _KB_PATH_RE = re.compile(r"/knowledge/bases/(\d+)/")

    def _get_client_key(self, request: Request) -> str:
        """获取限流 key：优先 user_id（由 AuthMiddleware 设置），降级到 client IP。

        知识库子路由额外追加 kb:{id}，使每个知识库拥有独立的限流桶，
        避免跨库扇出请求互相挤占同一个全局 heavy 桶。
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
    """限流器接口 — ABC 模式，与 Reranker/LLMProvider 保持一致（P2 修复）。"""

    @abstractmethod
    async def check(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, int]:
        """检查是否允许请求。

        返回:
            (allowed, remaining, reset_seconds)
        """
        ...


# Lua 脚本：原子化 check + add，消除 TOCTOU 竞态（P1 修复）
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
    """Redis 滑动窗口限流器 — 基于 sorted set + Lua 脚本原子操作。

    使用 ZSET：score = 时间戳，member = 唯一请求 ID。
    通过 Lua 脚本保证 check + add 在单次 EVAL 中原子完成。
    """

    def __init__(self, redis_client: Any) -> None:
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
        self._windows: dict[str, list[float]] = {}

    async def check(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int, int]:
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
    """工厂函数：有 Redis 客户端则用 Redis 滑动窗口，否则用内存实现。"""
    if use_redis and redis_client is not None:
        return RedisRateLimiter(redis_client)
    return FakeRateLimiter()


@dataclass
class RateLimitSettings:
    """限流配置聚合 — 封装限流器实例 + 参数，避免 HTTPServer 参数膨胀（P2 修复）。"""

    rate_limiter: RateLimiter
    enabled: bool = True
    standard_limit: int = 60
    heavy_limit: int = 120
    window_seconds: int = 60
