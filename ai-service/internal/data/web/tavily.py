"""Tavily Search；不重试、不切换供应商，不将故障伪装为空结果。"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic

import httpx

from internal.biz.materials import WebSource
from internal.biz.tool_types import ToolError, WebDiagnostic, WebResult
from internal.biz.web_access import validate_public_url
from internal.data.web.http_errors import transport_failure


class TavilySearch:
    def __init__(
        self,
        api_key: str,
        timeout: float = 20,
        *,
        search_limit: int = 5,
        summary_chars: int = 1200,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._search_limit = search_limit
        self._summary_chars = summary_chars

    async def _post(self, payload: dict) -> tuple[dict, WebDiagnostic]:
        started = monotonic()
        diagnostic = WebDiagnostic(
            endpoint="search", phase="configuration", category="not_configured",
            elapsed_seconds=0, client_timeout_seconds=self._timeout,
        )

        def failure(code: str, message: str, category: str, exc=None) -> ToolError:
            return ToolError(code, message, diagnostics=replace(
                diagnostic, category=category, elapsed_seconds=round(monotonic() - started, 3),
                exception_type=type(exc).__name__ if exc is not None else None,
            ))

        if not self._api_key:
            raise failure("TOOL_NOT_CONFIGURED", "请在本地 .env 配置 TAVILY_API_KEY", "not_configured")
        try:
            diagnostic = replace(diagnostic, phase="request")
            # HTTPX 的 timeout 是各网络阶段的空闲时限；另外限制整个请求的墙钟时间。
            async with (
                asyncio.timeout(self._timeout),
                httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client,
                client.stream(
                    "POST",
                    "https://api.tavily.com/search",
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
            category, phase, message = transport_failure(exc, diagnostic.phase)
            diagnostic = replace(diagnostic, phase=phase)
            raise failure("TOOL_DEPENDENCY_FAILED", f"Tavily {message}", category, exc) from exc
        except (ValueError, TypeError) as exc:
            raise failure("TOOL_INVALID_RESPONSE", "Tavily 响应格式无效", "invalid_response", exc) from exc

    @staticmethod
    def _request_id(value: object) -> str | None:
        # 只接收明确的追踪字段；绝不保存响应头集合、异常原文或响应原文。
        return value if isinstance(value, str) and re.fullmatch(r"[\w.:-]{1,128}", value, re.ASCII) else None

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

    async def search(self, query: str, max_results: int) -> WebResult:
        data, diagnostic = await self._post(
            {
                "query": query,
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
            result.sources.append(
                WebSource(
                    citation_id=0,
                    url=url,
                    title=item["title"][:300],
                    fetched_at=fetched,
                    content=content[: self._summary_chars],
                    source_type="web_search_summary",
                    truncated=len(content) > self._summary_chars,
                    end=min(len(content), self._summary_chars), total_chars=len(content),
                )
            )
            if len(result.sources) >= min(max_results, self._search_limit):
                break
        return result
