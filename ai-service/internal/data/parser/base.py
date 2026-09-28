"""
internal/data/parser/base.py — 文档解析器基类

提供通用的同步解析逻辑，子类只需实现 _parse_sync() 方法。
基类负责 asyncio.to_thread 包装，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from internal.biz.repo import DocumentParser

logger = structlog.get_logger()


class BaseDocumentParser(DocumentParser):
    """文档解析器基类 — 封装 asyncio.to_thread 逻辑。

    子类实现 _parse_sync(file_path) -> str 即可，
    基类自动用 asyncio.to_thread 包装，避免阻塞事件循环。
    """

    async def parse(self, file_path: str) -> str:
        """解析文件，返回纯文本内容。

        用 asyncio.to_thread 包装同步解析，防止阻塞事件循环。
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        if path.stat().st_size == 0:
            raise ValueError(f"File is empty: {file_path}")

        logger.info("parser_starting", file_path=file_path, parser=type(self).__name__)
        content = await asyncio.to_thread(self._parse_sync, str(path))
        logger.info(
            "parser_completed",
            file_path=file_path,
            parser=type(self).__name__,
            content_length=len(content),
        )
        return content

    def _parse_sync(self, file_path: str) -> str:
        """同步解析逻辑 — 子类实现。"""
        raise NotImplementedError
