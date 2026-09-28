"""
internal/streaming/sse.py — SSE 格式化器

提供 SSE 协议格式化函数：
- format_sse_data(): 将数据格式化为 SSE data 行
- SSE_DONE: 流结束标记

SSE 协议格式：
  data: {json}\n\n
  data: [DONE]\n\n
"""

from __future__ import annotations

import json


def format_sse_data(data: dict | str) -> str:
    """将数据格式化为 SSE data 行。

    参数:
        data: dict（序列化为 JSON）或 str（直接输出）

    返回:
        SSE 格式字符串，如 "data: {...}\n\n"
    """
    if isinstance(data, dict):
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    return f"data: {data}\n\n"


# 流结束标记
SSE_DONE = "data: [DONE]\n\n"
