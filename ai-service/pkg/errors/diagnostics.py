"""记录内部错误的位置，不记录异常内容、请求正文或局部变量。"""

from __future__ import annotations

from traceback import extract_tb


# 提取异常类型和发生位置，供日志排查使用，避免记录异常原文或局部变量。
def unexpected_error_details(exc: Exception) -> dict:
    return {"exception_type": type(exc).__name__, "stack": [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in extract_tb(exc.__traceback__)
    ]}
