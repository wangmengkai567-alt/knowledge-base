"""静态 HTML 正文；逐文本节点保留 pre 内的代码和空白。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# 无需闭合的 HTML 标签。
_VOID = frozenset(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"])
# 正文提取时跳过的 HTML 标签。
_OMIT = frozenset(["script", "style", "nav", "aside", "header", "footer", "form", "button", "svg", "template", "noscript"])
# 提取正文时用于分段的 HTML 标签。
_BLOCK = frozenset(["div", "section", "article", "main", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "tr", "dl", "dt", "dd", "br", "hr"])


# 保存解析后的 HTML 节点，供正文提取时跳过隐藏内容并保留结构。
@dataclass
class _Node:
    tag: str
    hidden: bool = False
    children: list[_Node | str] = field(default_factory=list)


# 将 HTML 转成简化的节点树，供后续选择正文容器和提取代码。
class _Document(HTMLParser):
    def __init__(self) -> None:
        """启用 HTML 实体解码，并初始化根节点、待闭合节点栈和节点列表。"""
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.nodes: list[_Node] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # 限制节点数量和嵌套深度，防止异常页面造成过大的解析开销。
        """建立子节点并继承父节点的隐藏状态；非空元素压栈，等待后续结束标签。"""
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
        """从栈顶寻找匹配的开始标签，移除该节点及其尚未闭合的后代。"""
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        """把原始文字节点追加到当前栈顶节点，暂不归一化代码中的空白。"""
        self.stack[-1].children.append(data)


# 合并节点内的可见文字；代码块通过这里保留原有空白。
def _text(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    if node.hidden:
        return ""
    if node.tag == "br":
        return "\n"
    return "".join(_text(child) for child in node.children)


# 将提取出的正文和代码位置一起返回，供网页快照保存。
@dataclass(frozen=True)
class ParsedHTML:
    title: str
    content: str
    code_ranges: list[tuple[int, int]]


# 从静态 HTML 提取正文，并标记完整代码块在正文中的位置。
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
        """合并当前普通文本并归一化空白，非空时保存为一个正文段，再清空缓冲。"""
        text = " ".join("".join(prose).split())
        prose.clear()
        if text:
            blocks.append((text, False))

    def visit(node: _Node | str) -> None:
        """递归提取可见正文；普通块分段，pre 中代码单独保留并添加 Markdown 围栏。"""
        if isinstance(node, str):
            prose.append(node)
            return
        if node.hidden:
            return
        if node.tag == "pre":
            # 代码单独保留；围栏要长于代码中的反引号，避免破坏 Markdown 结构。
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
