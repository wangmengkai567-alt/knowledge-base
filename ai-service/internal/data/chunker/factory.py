"""
internal/data/chunker/factory.py — 分块器工厂

只提供递归分块（按段落/句子拆分，再合并到 token 预算）。
"""

from __future__ import annotations

from internal.biz.repo import TextChunker
from internal.data.chunker.recursive import RecursiveCharacterChunker


def create_text_chunker(
    chunk_size: int,
    chunk_overlap: int,
) -> TextChunker:
    """创建递归分块器。

    参数:
        chunk_size: 目标分块大小（token 估算数）
        chunk_overlap: 相邻块重叠 token 估算数
    """
    return RecursiveCharacterChunker(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
