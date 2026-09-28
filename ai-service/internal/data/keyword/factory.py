"""
internal/data/keyword/factory.py — KeywordStore 工厂
"""

from __future__ import annotations

from internal.biz.repo import KeywordStore
from internal.data.keyword.bm25 import BM25KeywordStore


def create_keyword_store(k1: float = 1.5, b: float = 0.75) -> KeywordStore:
    """创建进程内 BM25 关键词索引。"""
    return BM25KeywordStore(k1=k1, b=b)
