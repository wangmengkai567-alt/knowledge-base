"""一次运行内的网页目录、不可变正文快照及原文窗口。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal, TypedDict

from internal.biz.materials import WebSource
from internal.biz.token_budget import TokenCounter, original_window
from internal.biz.tool_types import ToolError


# 返回页内查找命中的位置和预览，帮助模型决定接下来读取哪一段。
class WebMatch(TypedDict):
    source_id: str
    start: int
    end: int
    preview_start: int
    preview_end: int
    preview: str
    in_code: bool


# 登记本次允许访问的网页，并跟踪正文是否已经读取。
@dataclass
class WebEntry:
    source_id: str
    url: str
    title: str
    origin: Literal["user", "search"]
    discovered_at: str
    read: bool = False


# 固定一次抓取的完整正文，后续片段读取和页内查找都使用这一份内容。
@dataclass(frozen=True)
class WebPage:
    url: str
    title: str
    fetched_at: str
    content: str
    code_ranges: list[tuple[int, int]]

    def window(self, source_id: str, start: int, max_tokens: int, counter: TokenCounter) -> WebSource:
        """从固定网页快照中读取一个正文窗口，返回带来源编号和绝对字符范围的 WebSource。

        start 为原文字符起点，max_tokens 为窗口 token 额度；返回实际字符范围。
        越界、空白窗口或完整代码无法放入窗口时抛出工具错误，不发起新的网页请求。
        """
        if start >= len(self.content):
            raise ToolError("WEB_RANGE_INVALID", "正文起点超出该页字符范围")
        start, end = original_window(self.content, counter, max_tokens, start=start, code_ranges=self.code_ranges)
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
    """去掉相同网页版本中已见且文字一致的范围，返回尚未送模的非空片段副本。"""
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


# 统一管理一次问答中的网页目录和正文缓存，防止读取未登记地址或替换已有快照。
@dataclass
class WebRegistry:
    entries: list[WebEntry] = field(default_factory=list)
    pages: dict[str, WebPage] = field(default_factory=dict)
    _url_ids: dict[str, str] = field(default_factory=dict)
    _cached_bytes: int = 0

    def register(self, url: str, title: str, origin: Literal["user", "search"]) -> WebEntry:
        """把允许读取的网址登记到本次目录并返回 WebEntry；同一网址复用编号，不抓取正文。"""
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
        """按来源编号读取本次已登记目录项；编号不在目录中时拒绝访问。"""
        for entry in self.entries:
            if entry.source_id == source_id:
                return entry
        raise ToolError("SOURCE_NOT_APPROVED", "只能选择本次目录中实际登记的来源编号")

    def cache(self, source_id: str, page: WebPage) -> None:
        """为目录来源保存唯一一版正文快照，并将目录项标记已读取。

        校验快照网址和缓存总大小；拒绝覆盖已缓存正文，确保后续位置与引文始终对应同一版内容。
        """
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
        """返回来源已经缓存的正文快照；尚未读取时抛出错误，不在此处自动抓取。"""
        self.get(source_id)
        if source_id not in self.pages:
            raise ToolError("WEB_PAGE_NOT_READ", "请先用 web_read 取得该来源正文，再进行页内查找")
        return self.pages[source_id]

    def find(self, source_id: str, pattern: str, counter: TokenCounter, preview_tokens: int) -> list[WebMatch]:
        """在已缓存网页中做区分大小写的字符串查找，返回最多十个命中的位置与短预览。

        不发起网络请求；预览用于选择后续 web_read 窗口，不能直接作为引用正文。
        """
        page = self.page(source_id)
        matches: list[WebMatch] = []
        start = 0
        while len(matches) < 10:
            offset = page.content.find(pattern, start)
            if offset < 0:
                break
            preview_start, preview_end = original_window(page.content, counter, preview_tokens, start=offset)
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
