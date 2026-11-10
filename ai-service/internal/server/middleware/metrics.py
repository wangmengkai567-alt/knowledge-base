"""声明 Prometheus 指标，记录非运维路由的 HTTP 次数与耗时。

模型指标由调用方显式调用 record_llm_* 更新，metrics_endpoint 提供文本响应。
未安装 prometheus_client 时不创建指标对象。
"""

from __future__ import annotations

import time

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import PlainTextResponse

from internal.server.middleware.rate_limit import OPERATIONS_PATHS

logger = structlog.get_logger()

# ──────────────────────────────────────────────
# 未安装 prometheus_client 时不创建指标，辅助函数直接跳过记录。

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )

    # 本服务独立的 Prometheus 指标注册表，供 /metrics 导出。
    REGISTRY = CollectorRegistry()

    # HTTP 指标
    # HTTP 请求累计次数，按请求方法、归一化路径和响应状态分组。
    HTTP_REQUESTS = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status"],
        registry=REGISTRY,
    )
    # HTTP 请求处理耗时分布，单位秒，按方法和归一化路径分组。
    HTTP_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"],
        registry=REGISTRY,
    )

    # LLM 指标
    # 模型请求累计次数，按操作类型和模型标识分组。
    LLM_REQUESTS = Counter(
        "llm_requests_total",
        "Total LLM requests",
        ["operation", "model"],
        registry=REGISTRY,
    )
    # 模型调用耗时分布，单位秒，按操作类型和模型标识分组。
    LLM_DURATION = Histogram(
        "llm_request_duration_seconds",
        "LLM request duration in seconds",
        ["operation", "model"],
        registry=REGISTRY,
    )
    # 模型 token 累计消耗，按 token 类型和模型标识分组。
    LLM_TOKENS = Counter(
        "llm_tokens_total",
        "Total tokens consumed",
        ["type", "model"],
        registry=REGISTRY,
    )

    # 标记 Prometheus 依赖已成功导入，记录函数和中间件据此决定是否采集。
    PROMETHEUS_AVAILABLE = True

except ImportError:
    PROMETHEUS_AVAILABLE = False
    REGISTRY = None
    HTTP_REQUESTS = None
    HTTP_DURATION = None
    LLM_REQUESTS = None
    LLM_DURATION = None
    LLM_TOKENS = None
    logger.warning("prometheus_client_not_installed_metrics_disabled")


# 复用共享运维路径常量（与 rate_limit 中间件一致）
# OPERATIONS_PATHS 从 rate_limit.py 导入，避免重复定义


def _normalize_path(path: str) -> str:
    """路径归一化：将路径参数替换为占位符，避免指标基数爆炸。

    如 /api/v1/knowledge/bases/42/rag/query → /api/v1/knowledge/bases/:id/rag/query
    """
    parts = path.strip("/").split("/")
    normalized = []
    for part in parts:
        if part.isdigit():
            normalized.append(":id")
        else:
            normalized.append(part)
    return "/" + "/".join(normalized)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Prometheus 指标中间件 — 记录 HTTP 请求计数和耗时。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """为非运维请求记录方法、归一化路径、响应状态和耗时；未启用指标时直接转发。"""
        if not PROMETHEUS_AVAILABLE:
            return await call_next(request)

        path = request.url.path
        if path in OPERATIONS_PATHS:
            return await call_next(request)

        method = request.method
        normalized_path = _normalize_path(path)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = str(response.status_code)
        except Exception:
            status = "500"
            raise
        finally:
            duration = time.perf_counter() - start
            HTTP_REQUESTS.labels(method=method, endpoint=normalized_path, status=status).inc()
            HTTP_DURATION.labels(method=method, endpoint=normalized_path).observe(duration)

        return response


def metrics_endpoint() -> PlainTextResponse:
    """创建 /metrics 端点响应 — Prometheus 文本格式。"""
    if not PROMETHEUS_AVAILABLE:
        return PlainTextResponse(
            "# prometheus_client not installed\n",
            status_code=200,
            media_type="text/plain",
        )

    output = generate_latest(REGISTRY)
    return PlainTextResponse(
        output,
        status_code=200,
        media_type=CONTENT_TYPE_LATEST,
    )


# ──────────────────────────────────────────────
# LLM 指标记录辅助函数 — 供 biz/service 层调用


def record_llm_request(operation: str, model: str) -> None:
    """记录 LLM 调用开始。"""
    if PROMETHEUS_AVAILABLE:
        LLM_REQUESTS.labels(operation=operation, model=model).inc()


def record_llm_duration(operation: str, model: str, duration: float) -> None:
    """记录 LLM 调用耗时。"""
    if PROMETHEUS_AVAILABLE:
        LLM_DURATION.labels(operation=operation, model=model).observe(duration)


def record_llm_tokens(token_type: str, model: str, count: int) -> None:
    """记录 Token 用量。token_type: prompt / completion。"""
    if PROMETHEUS_AVAILABLE:
        LLM_TOKENS.labels(type=token_type, model=model).inc(count)
