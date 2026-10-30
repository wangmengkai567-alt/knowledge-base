"""静态 HTML 正文；逐文本节点保留 pre 内的代码和空白。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_VOID = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"])
_OMIT = frozenset(["script", "style", "nav", "aside", "header", "footer", "form", "button", "svg", "template", "noscript"])
_BLOCK = frozenset(["div", "section", "article", "main", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "tr", "dl", "dt", "dd", "br", "hr"])


@dataclass
class _Node:
    tag: str
    hidden: bool = False
    children: list[_Node | str] = field(default_factory=list)


class _Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.nodes: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self.nodes) >= 50000 or len(self.stack) >= 256:
            raise ValueError("HTML structure exceeds limit")
        attributes = dict(attrs)
        node = _Node(tag, self.stack[-1].hidden or tag in _OMIT or "hidden" in attributes
                     or attributes.get("aria-hidden") == "true")
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _text(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    if node.hidden:
        return ""
    if node.tag == "br":
        return "\n"
    return "".join(_text(child) for child in node.children)


@dataclass(frozen=True)
class ParsedHTML:
    title: str
    content: str
    code_ranges: list[tuple[int, int]]


def parse_html(html: str) -> ParsedHTML:
    document = _Document()
    document.feed(html)
    document.close()
    title = next((_text(node).strip() for node in document.nodes if node.tag == "title"), "")
    # 只支持有语义正文容器的静态页，容器缺失明确失败。
    roots = [node for node in document.nodes if node.tag in ("article", "main") and not node.hidden]
    if not roots:
        raise ValueError("HTML has no main/article content")
    root = min(roots, key=lambda node: node.tag != "article")
    blocks: list[tuple[str, bool]] = []
    prose: list[str] = []

    def flush() -> None:
        text = " ".join("".join(prose).split())
        prose.clear()
        if text:
            blocks.append((text, False))

    def visit(node: _Node | str) -> None:
        if isinstance(node, str):
            prose.append(node)
            return
        if node.hidden:
            return
        if node.tag == "pre":
            flush()
            code = _text(node)
            if code.strip():
                fence = "`" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"`+", code)), default=3))
                blocks.append((f"{fence}\n{code}" + ("" if code.endswith("\n") else "\n") + fence, True))
            return
        if node.tag in _BLOCK:
            flush()
        for child in node.children:
            visit(child)
        if node.tag in _BLOCK:
            flush()

    visit(root)
    flush()
    code_ranges = []
    parts = []
    offset = 0
    for text, is_code in blocks:
        if parts:
            offset += 2
        if is_code:
            code_ranges.append((offset, offset + len(text)))
        parts.append(text)
        offset += len(text)
    return ParsedHTML(title[:300], "\n\n".join(parts), code_ranges)
