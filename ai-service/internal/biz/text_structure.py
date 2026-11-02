"""从完整规范正文识别围栏代码范围，不从分块起点推断围栏状态。"""

from __future__ import annotations

import re


def fenced_code_ranges(text: str) -> list[tuple[int, int]]:
    """范围包含围栏；闭围栏不能带语言标签，未闭合块延伸到正文末尾。"""
    ranges = []
    offset, block_start = 0, 0
    fence = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})([^\r\n]*)", line)
        if marker and not fence:
            if marker[1][0] != "`" or "`" not in marker[2]:
                fence, block_start = marker[1], offset
        elif marker and fence and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
            ranges.append((block_start, offset + len(line)))
            fence = ""
        offset += len(line)
    if fence:
        ranges.append((block_start, len(text)))
    return ranges
