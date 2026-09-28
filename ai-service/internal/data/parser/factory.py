"""
internal/data/parser/factory.py — 文档解析器工厂

根据文件扩展名选择对应的解析器（PDF / Markdown / Text）。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from internal.biz.repo import DocumentParser
from internal.data.parser.markdown import MarkdownParser
from internal.data.parser.pdf import PDFParser
from internal.data.parser.text import TextParser


def create_document_parser(file_path: str) -> DocumentParser:
    """根据文件扩展名创建对应的文档解析器。

    .pdf → PDFParser
    .md / .markdown → MarkdownParser
    .txt → TextParser
    其他 → ValueError
    """
    ext = PurePosixPath(file_path).suffix.lower()

    if ext == ".pdf":
        return PDFParser()
    if ext in (".md", ".markdown"):
        return MarkdownParser()
    if ext == ".txt":
        return TextParser()

    raise ValueError(f"Unsupported file type for parsing: '{ext}'")
