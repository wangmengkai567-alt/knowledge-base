"""Tavily Search / Extract；不重试、不切换供应商，不将故障伪装为空结果。"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from internal.biz.materials import WebSource
from internal.biz.repo import WebProvider
from internal.biz.tool_types import ToolError, ToolFailure, WebResult
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

    async def _post(self, endpoint: str, payload: dict) -> dict:
        if not self._api_key:
            raise ToolError("TOOL_NOT_CONFIGURED", "请在本地 .env 配置 TAVILY_API_KEY")
        try:
            async with (
                httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client,
                client.stream(
                    "POST",
                    f"https://api.tavily.com/{endpoint}",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as response,
            ):
                if response.status_code != 200:
                    raise ToolError(
                        "TOOL_DEPENDENCY_FAILED", f"Tavily 返回 HTTP {response.status_code}"
                    )
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2_000_000:
                        raise ToolError("TOOL_RESPONSE_TOO_LARGE", "Tavily 返回资料超过接收上限")
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise ValueError
            return data
        except httpx.TimeoutException as exc:
            raise ToolError("TOOL_TIMEOUT", "Tavily 请求超时") from exc
        except httpx.HTTPError as exc:
            raise ToolError("TOOL_DEPENDENCY_FAILED", "Tavily 连接失败") from exc
        except (ValueError, TypeError) as exc:
            raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 响应格式无效") from exc

    async def search(self, keywords: str, max_results: int) -> WebResult:
        data = await self._post(
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
        result = WebResult()
        seen: set[str] = set()
        fetched = datetime.now(UTC).isoformat()
        for item in data["results"]:
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key), str) for key in ("url", "title", "content")
            ):
                raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 搜索结果缺少网址、标题或摘要")
            url = await validate_public_url(item["url"])
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
        normalized = list(dict.fromkeys([await validate_public_url(url) for url in urls]))
        data = await self._post(
            "extract",
            {
                "urls": normalized,
                "extract_depth": "basic",
                "format": "text",
                "include_images": False,
                "timeout": min(self._timeout, 15),
            },
        )
        if not isinstance(data.get("failed_results"), list):
            raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 正文响应缺少逐网址失败信息")
        result = WebResult()
        seen: set[str] = set()
        fetched = datetime.now(UTC).isoformat()
        for item in data["results"]:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("url"), str)
                or not isinstance(item.get("raw_content"), str)
            ):
                raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 正文结果格式无效")
            url = normalize_public_url(item["url"])
            if url not in normalized or url in seen:
                raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 正文网址与请求不对应")
            seen.add(url)
            content = item["raw_content"].strip()
            if not content:
                result.errors.append(ToolFailure("WEB_CONTENT_EMPTY", "网页正文为空", url))
                continue
            # Extract 不保证返回标题；缺失时保留空标题，执行器可复用搜索标题。
            title = item.get("title", "")
            if not isinstance(title, str):
                raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 网页标题格式无效")
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
                raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 失败记录格式无效")
            url = normalize_public_url(item["url"])
            if url not in normalized or url in seen:
                raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 失败网址与请求不对应")
            seen.add(url)
            result.errors.append(
                ToolFailure("WEB_EXTRACT_FAILED", "Tavily 无法取得该网页正文", url)
            )
        if seen != set(normalized):
            raise ToolError("TOOL_INVALID_RESPONSE", "Tavily 未返回全部请求网址的结果")
        return result
