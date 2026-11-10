"""唯一的网页正文读取路径：公网 IP 固定、同源重定向、静态 HTML 解析。"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx

from internal.biz.tool_types import ToolError, WebDiagnostic, WebResult
from internal.biz.web_access import normalize_public_url, resolve_public_url
from internal.biz.web_sources import WebPage
from internal.data.web.html_content import parse_html
from internal.data.web.http_errors import transport_failure


# 抓取经过公网检查的静态网页，将正文整理成可重复读取的快照。
class HTMLPageReader:
    def __init__(self, timeout: float = 20) -> None:
        """设置本批网页读取的统一超时秒数，包含地址校验、请求和正文解析。"""
        self._timeout = timeout

    # 在统一时限内完成地址检查、请求和正文解析，失败时附带出错阶段。
    async def read(self, urls: list[str]) -> WebResult:
        started = monotonic()
        diagnostic = WebDiagnostic("web_page", "url_validation", "", 0,
                                   client_timeout_seconds=self._timeout)

        def failure(code: str, message: str, category: str, exc=None) -> ToolError:
            """结合当前请求阶段、累计耗时和异常类型，生成带诊断信息的工具错误。"""
            return ToolError(code, message, diagnostics=replace(
                diagnostic, category=category, elapsed_seconds=round(monotonic() - started, 3),
                exception_type=type(exc).__name__ if exc is not None else None,
            ))

        result = WebResult()
        try:
            async with asyncio.timeout(self._timeout):
                for original in dict.fromkeys(normalize_public_url(url) for url in urls):
                    page_started = monotonic()
                    diagnostic = replace(diagnostic, phase="url_validation", http_status=None,
                                         connected_ip=None, redirect_count=0)
                    current = original
                    seen: set[str] = set()
                    for hop in range(4):
                        if current in seen:
                            raise failure("WEB_REDIRECT_FAILED", "网页重定向形成循环", "redirect")
                        seen.add(current)
                        diagnostic = replace(diagnostic, phase="url_validation", http_status=None,
                                             connected_ip=None, final_url=current)
                        normalized, addresses = await resolve_public_url(current)
                        address = addresses[0]  # 只连接这一个已校验地址，不轮换地址或重试。
                        # 请求连接固定 IP，但保留原域名供 HTTP 路由和 HTTPS 证书校验使用。
                        target = httpx.URL(normalized).copy_with(host=address)
                        hostname = urlsplit(normalized).hostname
                        diagnostic = replace(diagnostic, phase="request", redirect_count=hop)
                        async with (
                            httpx.AsyncClient(timeout=self._timeout, trust_env=False,
                                              follow_redirects=False) as client,
                            client.stream("GET", target,
                                          headers={"Host": httpx.URL(normalized).netloc.decode("ascii"),
                                                   "Accept": "text/html, application/xhtml+xml",
                                                   "User-Agent": "AI-Knowledge-Base/0.1"},
                                          extensions={"sni_hostname": hostname}) as response,
                        ):
                            diagnostic = replace(diagnostic, phase="response_headers",
                                                 http_status=response.status_code)
                            stream = response.extensions.get("network_stream")
                            peer = stream.get_extra_info("server_addr") if stream is not None else None
                            # 再次核对实际连接地址，确保没有绕过前面的公网检查。
                            if not peer or ipaddress.ip_address(peer[0]) != ipaddress.ip_address(address):
                                raise failure("URL_NOT_ALLOWED", "无法确认网页连接使用已校验的公网地址", "peer_address")
                            diagnostic = replace(diagnostic, connected_ip=peer[0])
                            if response.status_code in (301, 302, 303, 307, 308):
                                location = response.headers.get("location")
                                if not location or hop == 3:
                                    raise failure("WEB_REDIRECT_FAILED", "网页重定向缺少目标或超过三次", "redirect")
                                destination = normalize_public_url(urljoin(normalized, location))
                                # 跳转后仍须保持原来的协议、域名和端口，并重新检查公网地址。
                                first, next_url = httpx.URL(original), httpx.URL(destination)
                                if (first.scheme, first.host, first.port) != (next_url.scheme, next_url.host, next_url.port):
                                    raise failure("URL_NOT_ALLOWED", "网页重定向必须保持同源", "redirect_scope")
                                current = destination
                                continue
                            if response.status_code != 200:
                                raise failure("TOOL_DEPENDENCY_FAILED", f"网页返回 HTTP {response.status_code}", "http_status")
                            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                            if media_type not in ("text/html", "application/xhtml+xml"):
                                raise failure("WEB_UNSUPPORTED_CONTENT", "正文读取仅支持静态 HTML", "content_type")
                            diagnostic = replace(diagnostic, phase="response_body")
                            # 边接收边检查大小，避免先将过大的响应全部载入内存。
                            body = bytearray()
                            async for part in response.aiter_bytes(chunk_size=32768):
                                body.extend(part)
                                if len(body) > 2_000_000:
                                    raise failure("TOOL_RESPONSE_TOO_LARGE", "网页正文超过 2000000 字节接收上限", "response_too_large")
                            diagnostic = replace(diagnostic, phase="html_parse")
                            parsed = parse_html(bytes(body).decode(response.encoding))
                            if len(parsed.content.encode("utf-8")) > 2_000_000:
                                raise failure("TOOL_RESPONSE_TOO_LARGE", "解析正文超过快照上限", "response_too_large")
                            if not parsed.content:
                                raise failure("WEB_CONTENT_EMPTY", "网页未取得可用正文", "empty_content")
                            result.pages.append(WebPage(
                                original, parsed.title, datetime.now(UTC).isoformat(),
                                parsed.content, parsed.code_ranges,
                            ))
                            result.diagnostics.append(replace(
                                diagnostic, phase="complete", category="success",
                                elapsed_seconds=round(monotonic() - page_started, 3),
                            ))
                            break
            return result
        except ToolError as exc:
            # 保留已有的受控错误分类；仅在缺少诊断信息时补充当前出错阶段。
            if exc.diagnostics is not None:
                raise
            raise failure(exc.code, exc.message, "url_validation", exc.__cause__ or exc) from exc
        except httpx.TimeoutException as exc:
            # 区分连接、发送、连接池等待和读取超时，并保留当前网络阶段诊断。
            phase = ("connect" if isinstance(exc, httpx.ConnectTimeout) else
                     "write" if isinstance(exc, httpx.WriteTimeout) else
                     "pool" if isinstance(exc, httpx.PoolTimeout) else "read")
            diagnostic = replace(diagnostic, phase=phase, timeout_layer="client_io")
            raise failure("TOOL_TIMEOUT", "网页客户端网络阶段超时", "timeout", exc) from exc
        except TimeoutError as exc:
            # 网页读取整体超过 asyncio.timeout 的截止时间，与单个网络阶段超时分别记录。
            diagnostic = replace(diagnostic, timeout_layer="client_total")
            raise failure("TOOL_TIMEOUT", "网页读取总时限已到", "timeout", exc) from exc
        except httpx.HTTPError as exc:
            # 响应解码失败记为格式错误；其余网络异常交统一传输错误分类器处理。
            if isinstance(exc, httpx.DecodingError):
                raise failure("TOOL_INVALID_RESPONSE", "网页响应解码失败", "invalid_response", exc) from exc
            category, phase, message = transport_failure(exc, diagnostic.phase)
            diagnostic = replace(diagnostic, phase=phase)
            raise failure("TOOL_DEPENDENCY_FAILED", message, category, exc) from exc
        except (ValueError, LookupError) as exc:
            # 内容无法按预期编码或结构解析时返回格式错误，不把失败当成空资料。
            raise failure("TOOL_INVALID_RESPONSE", "网页正文编码或 HTML 结构无效", "invalid_response", exc) from exc
