"""无第三方依赖的 token 估算，供分块和元数据共用。"""

from __future__ import annotations

import re

# 中日韩字符近似 1 token；ASCII 连续串再切成短片段，避免超长标识符算成 1 个 token。
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[^\sA-Za-z0-9\u4e00-\u9fff]")


def token_spans(text: str) -> list[tuple[int, int]]:
    """估算单元在原字符串中的范围；切片必须保留其间的空白。"""
    spans: list[tuple[int, int]] = []
    for match in _TOKEN_PATTERN.finditer(text):
        item = match[0]
        if item.isascii() and item.isalnum() and len(item) > 4:
            spans.extend((match.start() + i, min(match.start() + i + 4, match.end()))
                         for i in range(0, len(item), 4))
        else:
            spans.append(match.span())
    return spans


def tokenize(text: str) -> list[str]:
    return [text[start:end] for start, end in token_spans(text or "")]


def count_tokens(text: str) -> int:
    return len(tokenize(text))
