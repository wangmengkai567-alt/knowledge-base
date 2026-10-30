"""不包含异常原文的 HTTP 故障分类。"""

from __future__ import annotations

import ssl

import httpx


def transport_failure(exc: httpx.HTTPError, phase: str) -> tuple[str, str, str]:
    cause = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return "tls", "tls", "TLS 握手或证书校验失败"
        cause = cause.__cause__ or cause.__context__
    if isinstance(exc, httpx.ConnectError):
        return "connection", "connect", "无法建立连接"
    if isinstance(exc, httpx.ProxyError):
        return "proxy", "connect", "代理连接失败"
    if isinstance(exc, httpx.ReadError):
        return "read", "read", "读取响应时连接中断"
    if isinstance(exc, httpx.WriteError):
        return "write", "write", "发送请求失败"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "remote_protocol", phase, "响应协议异常或连接提前关闭"
    return "transport", phase, "HTTP 传输失败"
