"""配置 structlog 和标准 logging 的共同处理链，按设置选择级别与 JSON/控制台输出。"""

from __future__ import annotations

import logging
import sys

import structlog
from structlog.stdlib import ExtraAdder, ProcessorFormatter
from structlog.types import Processor

from internal.conf.config import LogConfig

# 预定义处理器链前缀（structlog 和标准 logging 共用）。
# 初始化日志时复用处理顺序，不在调用方修改此序列。
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
    """根据日志配置设置 structlog 和标准 logging；应用创建前由启动代码调用。"""
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
    """把级别名称转换为 logging 数值；未识别名称返回 INFO。"""
    level_map: dict[str, int] = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    return level_map.get(level_str.upper(), logging.INFO)
