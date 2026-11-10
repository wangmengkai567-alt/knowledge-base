"""搜索与正文读取的固定接线，不在失败后更换读取路径。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from internal.biz.repo import WebProvider

if TYPE_CHECKING:
    from internal.biz.tool_types import WebResult
    from internal.data.web.html_reader import HTMLPageReader
    from internal.data.web.tavily import TavilySearch


# 将搜索和正文读取分别交给固定服务，实现工具执行器所需的网页访问接口。
class PublicWeb(WebProvider):
    def __init__(self, search: TavilySearch, reader: HTMLPageReader) -> None:
        """分别注入公开网页搜索服务和静态 HTML 正文读取器。"""
        self._search = search
        self._reader = reader

    async def search(self, query: str, max_results: int) -> WebResult:
        """执行搜索并返回摘要来源，max_results 为本次期望的最大结果数。"""
        return await self._search.search(query, max_results)

    async def read(self, urls: list[str]) -> WebResult:
        """读取指定 URL 的正文快照；具体公网校验和请求限制由 reader 执行。"""
        return await self._reader.read(urls)
