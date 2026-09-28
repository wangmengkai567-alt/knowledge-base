"""
internal/conf/logging.py — structlog 日志初始化

设计原则：
- 业务日志用事件名加字段，再按配置输出 JSON 或彩色文本。
- 日志格式和级别由 LogConfig 决定。
- uvicorn、SQLAlchemy、httpx 的标准 logging 也走同一条处理链。
"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import ExtraAdder, ProcessorFormatter
from structlog.types import Processor

from internal.conf.config import LogConfig

# 预定义处理器链前缀（structlog 和标准 logging 共用）。
# tuple 而非 list：模块级常量不应可变，防止意外修改影响全局日志配置。
_SHARED_PROCESSORS: tuple[Processor, ...] = (
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    ExtraAdder(),
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
    structlog.processors.UnicodeEncoder(),
)


def _get_renderer(fmt: str) -> Processor:
    """根据配置选择最终渲染器：json 或 console。"""
    if fmt == "json":
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer(colors=True)


def setup_logging(config: LogConfig) -> None:
    """初始化 structlog + 标准 logging 的全局日志配置。

    必须在 FastAPI app 创建之前调用。
    只接收 LogConfig（ISP），不强迫接收完整的 AppConfig。
    """
    level = _map_level(config.level)
    renderer = _get_renderer(config.format)

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 桥接标准 logging：第三方库日志也走 structlog 处理器链。
    # foreign_pre_chain 不含 renderer，渲染由 processors 完成。
    formatter = ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # uvicorn 的 error/access logger 有独立 handler，需单独配置。
    for name in ("uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.addHandler(handler)
        uvicorn_logger.setLevel(level)
        uvicorn_logger.propagate = False


def _map_level(level_str: str) -> int:
    """把日志级别字符串映射到标准 logging 的 int 常量。

    非法值 fallback 到 INFO，保证日志系统不会因配置错误而崩溃。
    """
    level_map: dict[str, int] = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str.upper(), logging.INFO)
