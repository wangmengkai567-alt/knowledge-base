"""Tavily Search / Extract；不重试、不切换供应商，不将故障伪装为空结果。"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic

import httpx

from internal.biz.materials import WebSource
from internal.biz.repo import WebProvider
from internal.biz.tool_types import ToolError, ToolFailure, WebDiagnostic, WebResult
from internal.biz.web_access import normalize_public_url, validate_public_url


class TavilyWeb(WebProvider):
    def __init__(
        self,
        api_key: str,
        timeout: float = 20,
        *,
        search_limit: int = 5,
        summary_chars: int = 1200,
        page_chars: int = 5000,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._search_limit = search_limit
        self._summary_chars = summary_chars
        self._page_chars = page_chars

    async def _post(self, endpoint: str, payload: dict) -> tuple[dict, WebDiagnostic]:
        started = monotonic()
        diagnostic = WebDiagnostic(
            endpoint=endpoint, phase="configuration", category="not_configured",
            elapsed_seconds=0, client_timeout_seconds=self._timeout,
            extract_timeout_seconds=payload.get("timeout") if endpoint == "extract" else None,
        )

        def failure(code: str, message: str, category: str, exc=None) -> ToolError:
            return ToolError(code, message, diagnostics=replace(
                diagnostic, category=category, elapsed_seconds=round(monotonic() - started, 3),
                exception_type=type(exc).__name__ if exc is not None else None,
            ))

        if not self._api_key:
            raise failure("TOOL_NOT_CONFIGURED", "请在本地 .env 配置 TAVILY_API_KEY", "not_configured")
        if endpoint == "extract" and not 1 <= payload["timeout"] < self._timeout:
            raise failure("TOOL_INVALID_CONFIGURATION", "正文提取时限必须小于客户端时限且至少为 1 秒", "configuration")
        try:
            diagnostic = replace(diagnostic, phase="request")
            # HTTPX 的 timeout 是各网络阶段的空闲时限；另外限制整个请求的墙钟时间。
            async with (
                asyncio.timeout(self._timeout),
                httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client,
                client.stream(
                    "POST",
                    f"https://api.tavily.com/{endpoint}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as response,
            ):
                diagnostic = replace(
                    diagnostic, phase="response_headers", http_status=response.status_code,
                    request_id=self._request_id(response.headers.get("x-request-id")),
                )
                if response.status_code != 200:
                    raise failure(
                        "TOOL_DEPENDENCY_FAILED", f"Tavily 返回 HTTP {response.status_code}",
                        "http_status",
                    )
                diagnostic = replace(diagnostic, phase="response_body")
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000:
                        raise failure("TOOL_RESPONSE_TOO_LARGE", "Tavily 返回资料超过接收上限", "response_too_large")
            diagnostic = replace(diagnostic, phase="response_format")
            data = json.loads(body)
            if isinstance(data, dict):
                diagnostic = replace(
                    diagnostic, request_id=self._request_id(data.get("request_id"))
                    or diagnostic.request_id,
                )
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError
            return data, replace(
                diagnostic, phase="complete", category="success",
                elapsed_seconds=round(monotonic() - started, 3),
            )
        except httpx.TimeoutException as exc:
            phase = (
                "connect" if isinstance(exc, httpx.ConnectTimeout) else
                "write" if isinstance(exc, httpx.WriteTimeout) else
                "pool" if isinstance(exc, httpx.PoolTimeout) else "read"
            )
            diagnostic = replace(diagnostic, phase=phase, timeout_layer="client_io")
            raise failure("TOOL_TIMEOUT", "Tavily 客户端网络阶段超时", "timeout", exc) from exc
        except TimeoutError as exc:
            diagnostic = replace(diagnostic, timeout_layer="client_total")
            raise failure("TOOL_TIMEOUT", "Tavily 请求总时限已到", "timeout", exc) from exc
        except httpx.HTTPError as exc:
            if isinstance(exc, httpx.DecodingError):
                raise failure("TOOL_INVALID_RESPONSE", "Tavily 响应解码失败", "invalid_response", exc) from exc
            category, phase, message = self._transport_failure(exc, diagnostic.phase)
            diagnostic = replace(diagnostic, phase=phase)
            raise failure("TOOL_DEPENDENCY_FAILED", message, category, exc) from exc
        except (ValueError, TypeError) as exc:
            raise failure("TOOL_INVALID_RESPONSE", "Tavily 响应格式无效", "invalid_response", exc) from exc

    @staticmethod
    def _request_id(value: object) -> str | None:
        # 只接收明确的追踪字段；绝不保存响应头集合、异常原文或响应原文。
        return value if isinstance(value, str) and re.fullmatch(r"[\w.:-]{1,128}", value, re.ASCII) else None

    @staticmethod
    def _transport_failure(exc: httpx.HTTPError, phase: str) -> tuple[str, str, str]:
        cause = exc
        while cause is not None:
            if isinstance(cause, ssl.SSLError):
                return "tls", "tls", "Tavily TLS 握手或证书校验失败"
            cause = cause.__cause__ or cause.__context__
        if isinstance(exc, httpx.ConnectError):
            return "connection", "connect", "Tavily 无法建立连接"
        if isinstance(exc, httpx.ProxyError):
            return "proxy", "connect", "Tavily 代理连接失败"
        if isinstance(exc, httpx.ReadError):
            return "read", "read", "Tavily 读取响应时连接中断"
        if isinstance(exc, httpx.WriteError):
            return "write", "write", "Tavily 发送请求失败"
        if isinstance(exc, httpx.RemoteProtocolError):
            return "remote_protocol", phase, "Tavily 响应协议异常或连接提前关闭"
        return "transport", phase, "Tavily HTTP 传输失败"

    @staticmethod
    def _invalid_response(message: str, diagnostic: WebDiagnostic) -> ToolError:
        return ToolError("TOOL_INVALID_RESPONSE", message, diagnostics=replace(
            diagnostic, phase="response_format", category="invalid_response",
        ))

    @staticmethod
    async def _validate_url(
        url: str, endpoint: str, diagnostic: WebDiagnostic | None = None
    ) -> str:
        started = monotonic()
        try:
            return await validate_public_url(url)
        except ToolError as exc:
            base = diagnostic or WebDiagnostic(endpoint, "url_validation", "", 0)
            raise ToolError(exc.code, exc.message, diagnostics=replace(
                base, phase="url_validation",
                category="dns" if exc.code == "URL_DNS_FAILED" else "url_not_allowed",
                elapsed_seconds=round(base.elapsed_seconds + monotonic() - started, 3),
                exception_type=type(exc.__cause__ or exc).__name__,
            )) from exc

    @classmethod
    def _result_url(cls, value: str, diagnostic: WebDiagnostic) -> str:
        try:
            return normalize_public_url(value)
        except ToolError as exc:
            raise cls._invalid_response("Tavily 响应包含无效网址", diagnostic) from exc

    async def search(self, keywords: str, max_results: int) -> WebResult:
        data, diagnostic = await self._post(
            "search",
            {
                "query": keywords,
                "max_results": min(max_results, self._search_limit),
                "search_depth": "basic",
                "topic": "general",
                "auto_parameters": False,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
            },
        )
        result = WebResult(diagnostics=[diagnostic])
        seen: set[str] = set()
        fetched = datetime.now(UTC).isoformat()
        for item in data["results"]:
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key), str) for key in ("url", "title", "content")
            ):
                raise self._invalid_response("Tavily 搜索结果缺少网址、标题或摘要", diagnostic)
            url = await self._validate_url(item["url"], "search", diagnostic)
            if url in seen:
                continue
            seen.add(url)
            content = item["content"].strip()
            if not content:
                continue
            result.sources.append(
                WebSource(
                    citation_id=0,
                    url=url,
                    title=item["title"][:300],
                    fetched_at=fetched,
                    content=content[: self._summary_chars],
                    source_type="web_search_summary",
                    truncated=len(content) > self._summary_chars,
                )
            )
            if len(result.sources) >= min(max_results, self._search_limit):
                break
        return result

    async def read(self, urls: list[str]) -> WebResult:
        # 即使通过其他内部调用入口使用，也先验证，绝不直接在本机抓取目标网页。
        normalized = list(dict.fromkeys([await self._validate_url(url, "extract") for url in urls]))
        data, diagnostic = await self._post(
            "extract",
            {
                "urls": normalized,
                "extract_depth": "basic",
                "format": "text",
                "include_images": False,
                "timeout": min(self._timeout - 1, 15),
            },
        )
        if not isinstance(data.get("failed_results"), list):
            raise self._invalid_response("Tavily 正文响应缺少逐网址失败信息", diagnostic)
        result = WebResult(diagnostics=[diagnostic])
        seen: set[str] = set()
        fetched = datetime.now(UTC).isoformat()
        for item in data["results"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("url"), str)
                or not isinstance(item.get("raw_content"), str)
            ):
                raise self._invalid_response("Tavily 正文结果格式无效", diagnostic)
            url = self._result_url(item["url"], diagnostic)
            if url not in normalized or url in seen:
                raise self._invalid_response("Tavily 正文网址与请求不对应", diagnostic)
            seen.add(url)
            content = item["raw_content"].strip()
            if not content:
                result.errors.append(ToolFailure(
                    "WEB_CONTENT_EMPTY", "网页正文为空", url,
                    replace(diagnostic, phase="extract_result", category="empty_content"),
                ))
                continue
            # Extract 不保证返回标题；缺失时保留空标题，执行器可复用搜索标题。
            title = item.get("title", "")
            if not isinstance(title, str):
                raise self._invalid_response("Tavily 网页标题格式无效", diagnostic)
            result.sources.append(
                WebSource(
                    citation_id=0,
                    url=url,
                    title=title[:300],
                    fetched_at=fetched,
                    content=content[: self._page_chars],
                    source_type="web_page",
                    truncated=len(content) > self._page_chars,
                )
            )
        for item in data["failed_results"]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                raise self._invalid_response("Tavily 失败记录格式无效", diagnostic)
            url = self._result_url(item["url"], diagnostic)
            if url not in normalized or url in seen:
                raise self._invalid_response("Tavily 失败网址与请求不对应", diagnostic)
            seen.add(url)
            result.errors.append(
                ToolFailure(
                    "WEB_EXTRACT_FAILED", "Tavily 无法取得该网页正文", url,
                    replace(diagnostic, phase="extract_result", category="extract_failed"),
                )
            )
        if seen != set(normalized):
            raise self._invalid_response("Tavily 未返回全部请求网址的结果", diagnostic)
        return result
