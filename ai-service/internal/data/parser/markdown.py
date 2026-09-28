"""
internal/data/parser/markdown.py — Markdown 文档解析器

读取原文。竖线表格会展开成「表头：单元格」的检索文本，其余内容保持原样。
"""

from __future__ import annotations

import structlog

from internal.data.parser.base import BaseDocumentParser
from internal.data.parser.table_struct import enrich_pipe_tables

logger = structlog.get_logger()


class MarkdownParser(BaseDocumentParser):
    """Markdown 解析器。"""

    def _parse_sync(self, file_path: str) -> str:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        try:
            return enrich_pipe_tables(content)
        except Exception as e:
            logger.warning("markdown_table_enrich_failed", file_path=file_path, error=str(e))
            return content
