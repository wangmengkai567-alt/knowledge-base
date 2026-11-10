"""无第三方依赖的 token 估算，供分块和元数据共用。"""

from __future__ import annotations

import re

# 匹配中文单字、连续英文数字串及其他非空白字符；tokenize 再把英文数字串每四字符切分。
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9]+|[^\sA-Za-z0-9\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    """按中文单字、最多四字符的英文数字片段及其他非空白字符生成估算词项。"""
    tokens: list[str] = []
    for item in _TOKEN_PATTERN.findall(text or ""):
        if item.isascii() and item.isalnum() and len(item) > 4:
            tokens.extend(item[i : i + 4] for i in range(0, len(item), 4))
        else:
            tokens.append(item)
    return tokens


def count_tokens(text: str) -> int:
    """返回本地估算词项数量，不是供应商 tokenizer 的精确 token 数。"""
    return len(tokenize(text))
