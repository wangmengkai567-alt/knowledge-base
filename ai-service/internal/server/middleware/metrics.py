"""
internal/server/middleware/metrics.py — Prometheus 指标中间件

记录以下指标供 Prometheus 抓取：
- http_requests_total{method,endpoint,status} — HTTP 请求计数
- http_request_duration_seconds{method,endpoint} — HTTP 请求耗时直方图
- llm_requests_total{operation,model} — LLM 调用计数
- llm_request_duration_seconds{operation,model} — LLM 调用耗时直方图
- llm_tokens_total{type,model} — Token 用量累计

设计要点：
- prometheus_client 为可选依赖，未安装时指标为空操作（不阻断应用）
- /metrics 端点暴露 Prometheus 文本格式
- 排除 /health、/docs、/metrics 等运维路径，避免噪声
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
# Prometheus 可选导入 — 未安装时降级为 no-op

try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )

    REGISTRY = CollectorRegistry()

    # HTTP 指标
    HTTP_REQUESTS = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status"],
        registry=REGISTRY,
    )
    HTTP_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "endpoint"],
        registry=REGISTRY,
    )

    # LLM 指标
    LLM_REQUESTS = Counter(
        "llm_requests_total",
        "Total LLM requests",
        ["operation", "model"],
        registry=REGISTRY,
    )
    LLM_DURATION = Histogram(
        "llm_request_duration_seconds",
        "LLM request duration in seconds",
        ["operation", "model"],
        registry=REGISTRY,
    )
    LLM_TOKENS = Counter(
        "llm_tokens_total",
        "Total tokens consumed",
        ["type", "model"],
        registry=REGISTRY,
    )

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
