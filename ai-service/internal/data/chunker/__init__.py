"""
internal/data/chunker/ — 文本分块器

递归分块：按段落/句子切分 + overlap。
RAG 生成时的邻块扩展由 rag.context_window 控制，与分块无关。
"""

from __future__ import annotations
