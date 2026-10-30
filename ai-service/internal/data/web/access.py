"""搜索与正文读取的固定接线，不在失败后更换读取路径。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from internal.biz.repo import WebProvider

if TYPE_CHECKING:
    from internal.biz.tool_types import WebResult
    from internal.data.web.html_reader import HTMLPageReader
    from internal.data.web.tavily import TavilySearch


class PublicWeb(WebProvider):
    def __init__(self, search: TavilySearch, reader: HTMLPageReader) -> None:
        self._search = search
        self._reader = reader

    async def search(self, query: str, max_results: int) -> WebResult:
        return await self._search.search(query, max_results)

    async def read(self, urls: list[str]) -> WebResult:
        return await self._reader.read(urls)
