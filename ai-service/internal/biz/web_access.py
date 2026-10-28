"""网页工具访问边界；仅向已选供应商提交公开 URL。"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from internal.biz.tool_types import ToolError


def normalize_public_url(url: str) -> str:
    try:
        if len(url) > 2048 or any(ord(char) <= 32 for char in url) or "\\" in url:
            raise ValueError
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError
        if parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.port not in (None, 80 if parts.scheme == "http" else 443):
            raise ValueError
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if "%" in host or host in ("localhost", "metadata.google.internal"):
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith(
                (".localhost", ".local", ".internal", ".test", ".invalid")
            ):
                raise ValueError from None
            if any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not all(char.isascii() and (char.isalnum() or char == "-") for char in label)
                for label in host.split(".")
            ):
                raise ValueError from None
        else:
            if not address.is_global:
                raise ValueError
        netloc = f"[{host}]" if ":" in host else host
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError) as exc:
        raise ToolError("URL_NOT_ALLOWED", "仅允许无凭据、标准端口的公开 HTTP(S) 网页") from exc


async def validate_public_url(url: str) -> str:
    normalized = normalize_public_url(url)
    host = urlsplit(normalized).hostname
    try:
        records = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ToolError("URL_DNS_FAILED", "网页域名无法解析") from exc
    if not records or any(not ipaddress.ip_address(record[4][0]).is_global for record in records):
        raise ToolError("URL_NOT_ALLOWED", "网页域名必须仅解析到公网地址")
    return normalized
