"""一次运行内的网页目录、不可变正文快照及原文窗口。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, TypedDict

from internal.biz.materials import WebSource
from internal.biz.tool_types import ToolError


class WebMatch(TypedDict):
    source_id: str
    start: int
    end: int
    preview_start: int
    preview_end: int
    preview: str
    in_code: bool


@dataclass
class WebEntry:
    source_id: str
    url: str
    title: str
    origin: Literal["user", "search"]
    discovered_at: str
    read: bool = False


@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    fetched_at: str
    content: str
    code_ranges: list[tuple[int, int]]

    def window(self, source_id: str, start: int, size: int) -> WebSource:
        if start >= len(self.content):
            raise ToolError("WEB_RANGE_INVALID", "正文起点超出该页字符范围")
        for left, right in self.code_ranges:
            if left < start < right:
                start = left
        end = min(start + size, len(self.content))
        for left, right in self.code_ranges:
            if left < end < right:
                end = left
        if end <= start:
            raise ToolError("WEB_CODE_BLOCK_TOO_LARGE", "完整代码块超过本次片段上限，不能返回半段代码")
        if not self.content[start:end].strip():
            raise ToolError("WEB_CONTENT_EMPTY", "所选正文范围没有可用文字")
        return WebSource(
            0, self.url, self.title, self.fetched_at, self.content[start:end], "web_page",
            start > 0 or end < len(self.content), source_id, start, end, len(self.content),
            [(left, right) for left, right in self.code_ranges if start <= left and right <= end],
        )


def unseen_web_ranges(source: WebSource, existing: list) -> list[WebSource]:
    portions = [source]
    for old in existing:
        if not isinstance(old, WebSource) or (old.source_id, old.fetched_at, old.source_type) != (source.source_id, source.fetched_at, source.source_type):
            continue
        remaining = []
        for part in portions:
            left, right = max(part.start, old.start), min(part.end, old.end)
            if left >= right or part.content[left-part.start:right-part.start] != old.content[left-old.start:right-old.start]:
                remaining.append(part)
                continue
            for start, end in ((part.start, left), (right, part.end)):
                if start < end:
                    if not part.content[start-part.start:end-part.start].strip():
                        continue
                    remaining.append(replace(
                        part, start=start, end=end, content=part.content[start-part.start:end-part.start],
                        truncated=True, code_ranges=[(a, b) for a, b in part.code_ranges if start <= a and b <= end],
                    ))
        portions = remaining
    return portions


@dataclass
class WebRegistry:
    entries: list[WebEntry] = field(default_factory=list)
    pages: dict[str, WebPage] = field(default_factory=dict)
    _url_ids: dict[str, str] = field(default_factory=dict)
    _cached_bytes: int = 0

    def register(self, url: str, title: str, origin: Literal["user", "search"]) -> WebEntry:
        if url in self._url_ids:
            entry = self.get(self._url_ids[url])
            if title and not entry.title:
                entry.title = title
            return entry
        if len(self.entries) >= 103:
            raise ToolError("WEB_DIRECTORY_LIMIT", "本次网页目录超过运行允许的数量")
        entry = WebEntry(f"web_{len(self.entries) + 1}", url, title, origin, datetime.now(UTC).isoformat())
        self.entries.append(entry)
        self._url_ids[url] = entry.source_id
        return entry

    def get(self, source_id: str) -> WebEntry:
        for entry in self.entries:
            if entry.source_id == source_id:
                return entry
        raise ToolError("SOURCE_NOT_APPROVED", "只能选择本次目录中实际登记的来源编号")

    def cache(self, source_id: str, page: WebPage) -> None:
        entry = self.get(source_id)
        if page.url != entry.url or source_id in self.pages:
            raise ToolError("WEB_SNAPSHOT_INVALID", "正文快照必须对应已登记网址且每次运行只保存一版")
        size = len(page.content.encode("utf-8"))
        if self._cached_bytes + size > 8_000_000:
            raise ToolError("WEB_CACHE_LIMIT", "本次正文快照超过 8000000 字节上限")
        self.pages[source_id] = replace(page, title=page.title or entry.title)
        self._cached_bytes += size
        entry.title, entry.read = self.pages[source_id].title, True

    def page(self, source_id: str) -> WebPage:
        self.get(source_id)
        if source_id not in self.pages:
            raise ToolError("WEB_PAGE_NOT_READ", "请先用 web_read 取得该来源正文，再进行页内查找")
        return self.pages[source_id]

    def find(self, source_id: str, pattern: str) -> list[WebMatch]:
        """只返回定位导航；预览允许省略代码，不能作为引用正文。"""
        page = self.page(source_id)
        matches: list[WebMatch] = []
        start = 0
        while len(matches) < 10:
            offset = page.content.find(pattern, start)
            if offset < 0:
                break
            preview_start = max(0, offset - (120 - len(pattern)) // 2)
            preview_end = min(len(page.content), preview_start + 120)
            matches.append({
                "source_id": source_id,
                "start": offset,
                "end": offset + len(pattern),
                "preview_start": preview_start,
                "preview_end": preview_end,
                "preview": page.content[preview_start:preview_end],
                "in_code": any(left <= offset < right for left, right in page.code_ranges),
            })
            start = offset + len(pattern)
        return matches
