"""无第三方依赖的 token 估算，供分块和元数据共用。"""

from __future__ import annotations

import re

# 中日韩字符近似 1 token；ASCII 连续串再切成短片段，避免超长标识符算成 1 个 token。
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[^\sA-Za-z0-9\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for item in _TOKEN_PATTERN.findall(text or ""):
        if item.isascii() and item.isalnum() and len(item) > 4:
            tokens.extend(item[i : i + 4] for i in range(0, len(item), 4))
        else:
            tokens.append(item)
    return tokens


def count_tokens(text: str) -> int:
    return len(tokenize(text))
