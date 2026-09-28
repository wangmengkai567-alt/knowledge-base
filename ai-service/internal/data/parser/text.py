"""
internal/data/parser/text.py — 纯文本文档解析器

直接读取文本。竖线表格与 Markdown 一样展开成带表头的行，其余内容保持原样。
"""

from __future__ import annotations

import structlog

from internal.data.parser.base import BaseDocumentParser
from internal.data.parser.table_struct import enrich_pipe_tables

logger = structlog.get_logger()


class TextParser(BaseDocumentParser):
    """纯文本解析器。"""

    def _parse_sync(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        try:
            return enrich_pipe_tables(content)
        except Exception as e:
            logger.warning("text_table_enrich_failed", file_path=file_path, error=str(e))
            return content
