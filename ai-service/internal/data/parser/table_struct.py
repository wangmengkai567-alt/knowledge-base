"""把二维表转成可检索的结构：原始 Markdown、字段对应行、表格级摘要。

不调用模型。空字符串是单元格真的为空；span_mask 为 True 才表示合并单元格留下的空位。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

_PIPE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_SEP_CELL = re.compile(r"^:?-{3,}:?$")
_YEAR = re.compile(r"^\d{4}$")


@dataclass
class StructuredTable:
    """一张表的原始网格和按表头展开的行。"""

    table_id: str
    title: str = ""
    section: str = ""
    page: int | None = None
    end_page: int | None = None
    headers: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    raw_markdown: str = ""

    def retrieval_text(self) -> str:
        """表格级摘要、原始表、每一行的「表头：值」，用空行隔开。"""
        blocks = [self._summary_text(), "原始表格：\n" + self.raw_markdown]
        blocks.extend(self.row_texts())
        return "\n\n".join(block for block in blocks if block.strip())

    def row_texts(self) -> list[str]:
        lines_per_row: list[str] = []
        for index, row in enumerate(self.rows, start=1):
            pairs = [f"{key}：{value}" for key, value in row.items() if value.strip()]
            if not pairs:
                continue
            head = [
                "类型：表格行",
                f"表格编号：{self.table_id}",
                f"行号：{index}",
            ]
            if self.page is not None:
                page = str(self.page) if self.end_page in (None, self.page) else f"{self.page}-{self.end_page}"
                head.append(f"页码：{page}")
            if self.section:
                head.append(f"章节：{self.section}")
            if self.title:
                head.append(f"表格标题：{self.title}")
            lines_per_row.append("\n".join(head + pairs))
        return lines_per_row

    def _summary_text(self) -> str:
        head = ["类型：表格", f"表格编号：{self.table_id}"]
        if self.page is not None:
            page = str(self.page) if self.end_page in (None, self.page) else f"{self.page}-{self.end_page}"
            head.append(f"页码：{page}")
        if self.section:
            head.append(f"章节：{self.section}")
        if self.title:
            head.append(f"表格标题：{self.title}")
        if self.headers:
            head.append("表头：" + "、".join(self.headers))
        return "\n".join(head)


def build_table(
    grid: list[list[str | None]],
    *,
    table_id: str,
    title: str = "",
    section: str = "",
    page: int | None = None,
    span_mask: list[list[bool]] | None = None,
) -> StructuredTable | None:
    """由单元格网格生成结构化表。失败或空表返回 None，不向外抛。"""
    try:
        normalized, mask = _normalize(grid, span_mask)
        if not normalized or not any(any(cell.strip() for cell in row) for row in normalized):
            logger.warning("table_empty", table_id=table_id)
            return None
        filled = _fill_spans(normalized, mask)
        header_rows, data_rows = _split_header(filled)
        headers = _flatten_headers(header_rows)
        records = _records(headers, data_rows)
        raw_header, raw_data = _split_header(normalized)
        raw_markdown = _to_markdown(raw_header + raw_data, header_count=max(len(raw_header), 1))
        if not records and len(filled) == 1:
            logger.info("table_header_only", table_id=table_id)
        return StructuredTable(
            table_id=table_id,
            title=title.strip(),
            section=section.strip(),
            page=page,
            end_page=page,
            headers=headers,
            rows=records,
            raw_markdown=raw_markdown,
        )
    except Exception as e:
        logger.warning("table_structure_failed", table_id=table_id, error=str(e))
        return None


def try_merge_cross_page(previous: StructuredTable, continuation: StructuredTable, *, y0: float, page_height: float) -> bool:
    """下一页表头相同且表格靠近页首时，并入上一张表。不确定则不合并。"""
    if previous.page is None or continuation.page is None:
        return False
    if continuation.page != (previous.end_page or previous.page) + 1:
        logger.info(
            "table_cross_page_skipped",
            reason="pages_not_consecutive",
            previous=previous.table_id,
            continuation=continuation.table_id,
        )
        return False
    if page_height > 0 and y0 > page_height * 0.35:
        logger.info(
            "table_cross_page_skipped",
            reason="not_at_page_top",
            table_id=continuation.table_id,
            y0=round(y0, 1),
        )
        return False
    if previous.headers != continuation.headers or not previous.headers:
        logger.info(
            "table_cross_page_skipped",
            reason="header_mismatch",
            previous=previous.table_id,
            continuation=continuation.table_id,
        )
        return False
    previous.rows.extend(continuation.rows)
    extra = _markdown_body(continuation.raw_markdown)
    if extra:
        previous.raw_markdown = previous.raw_markdown.rstrip() + "\n" + extra
    previous.end_page = continuation.page
    logger.info(
        "table_cross_page_merged",
        table_id=previous.table_id,
        from_page=previous.page,
        to_page=continuation.page,
        row_count=len(previous.rows),
    )
    return True


def enrich_pipe_tables(text: str) -> str:
    """把 Markdown / TXT 里的竖线表换成结构化检索文本。失败则保留原文。"""
    if not text or "|" not in text:
        return text
    try:
        return _replace_pipe_tables(text)
    except Exception as e:
        logger.warning("pipe_table_enrich_failed", error=str(e))
        return text


def _replace_pipe_tables(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    table_no = 0
    while index < len(lines):
        if _is_pipe_line(lines[index]) and _table_run_length(lines, index) >= 2:
            block: list[str] = []
            while index < len(lines) and _is_pipe_line(lines[index]):
                block.append(lines[index])
                index += 1
            table_no += 1
            title, section = _context_from_previous(output)
            grid = [_split_pipe_row(line) for line in block]
            table = build_table(grid, table_id=f"t{table_no}", title=title, section=section)
            output.append(table.retrieval_text() if table is not None else "\n".join(block))
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def _normalize(
    grid: list[list[str | None]],
    span_mask: list[list[bool]] | None,
) -> tuple[list[list[str]], list[list[bool]]]:
    width = max((len(row) for row in grid), default=0)
    texts: list[list[str]] = []
    mask: list[list[bool]] = []
    for row_index, row in enumerate(grid):
        text_row: list[str] = []
        mask_row: list[bool] = []
        for col in range(width):
            cell = row[col] if col < len(row) else None
            if cell is None:
                text_row.append("")
                mask_row.append(True)
            else:
                text_row.append(str(cell).replace("\n", " ").strip())
                if span_mask is not None and row_index < len(span_mask) and col < len(span_mask[row_index]):
                    mask_row.append(bool(span_mask[row_index][col]))
                else:
                    mask_row.append(False)
        texts.append(text_row)
        mask.append(mask_row)
    return texts, mask


def _fill_spans(grid: list[list[str]], mask: list[list[bool]]) -> list[list[str]]:
    filled = [row[:] for row in grid]
    for row_index in range(1, len(filled)):
        for col in range(len(filled[row_index])):
            if not mask[row_index][col]:
                continue
            above = filled[row_index - 1][col].strip()
            if above:
                filled[row_index][col] = above
    _fill_leading_groups(filled, mask)
    return filled


def _fill_leading_groups(grid: list[list[str]], mask: list[list[bool]]) -> None:
    """文本表没有 span 标记时，只补行首连续空单元格，不动中间和末尾的空值。"""
    if any(any(flags) for flags in mask):
        return
    header_rows, _ = _split_header(grid)
    start = len(header_rows)
    for row_index in range(start, len(grid)):
        row = grid[row_index]
        if not any(cell.strip() for cell in row):
            continue
        for col, cell in enumerate(row):
            if cell.strip():
                break
            if col > 0 and row[col - 1].strip():
                break
            above = grid[row_index - 1][col].strip() if row_index else ""
            if above and any(item.strip() for item in row[col + 1 :]):
                row[col] = above
                mask[row_index][col] = True


def _split_header(grid: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    separator = next((i for i, row in enumerate(grid) if _is_separator(row)), None)
    if separator is not None:
        headers = grid[:separator] or [grid[0]]
        return headers, grid[separator + 1 :]
    if len(grid) >= 3 and _looks_like_two_level_header(grid[0], grid[1]):
        return grid[:2], grid[2:]
    return grid[:1], grid[1:]


def _looks_like_two_level_header(first: list[str], second: list[str]) -> bool:
    if not any(cell.strip() for cell in first) or not any(cell.strip() for cell in second):
        return False
    if first[0].strip() and not any(not cell.strip() for cell in first[1:]):
        return False
    year_like = sum(1 for cell in first[1:] if _YEAR.match(cell.strip()))
    label_like = sum(1 for cell in second[1:] if cell.strip() and not _YEAR.match(cell.strip()))
    return year_like >= 2 and label_like >= 2


def _flatten_headers(header_rows: list[list[str]]) -> list[str]:
    width = max((len(row) for row in header_rows), default=0)
    names: list[str] = []
    for col in range(width):
        parts: list[str] = []
        for row in header_rows:
            cell = row[col].strip() if col < len(row) else ""
            if cell and cell not in parts:
                parts.append(cell)
        if len(parts) >= 2 and _YEAR.match(parts[0]) and "年" not in parts[1]:
            names.append(f"{parts[0]}年{''.join(parts[1:])}")
        elif parts:
            names.append("".join(parts))
        else:
            names.append(f"列{col + 1}")
    return names


def _records(headers: list[str], data_rows: list[list[str]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in data_rows:
        if not any(cell.strip() for cell in row):
            continue
        record: dict[str, str] = {}
        for col, header in enumerate(headers):
            record[header] = row[col].strip() if col < len(row) else ""
        records.append(record)
    return records


def _to_markdown(grid: list[list[str]], header_count: int = 1) -> str:
    if not grid:
        return ""
    width = max(len(row) for row in grid)
    header_count = min(max(header_count, 1), len(grid))
    lines: list[str] = []
    for row_index, row in enumerate(grid):
        padded = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(padded) + " |")
        if row_index == header_count - 1:
            lines.append("| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


def _markdown_body(raw: str) -> str:
    """跨页合并时只追加分隔线之后的数据行。"""
    body: list[str] = []
    seen_separator = False
    for line in raw.splitlines():
        if not line.startswith("|"):
            continue
        compact = set(line.replace("|", "").replace(" ", "").replace(":", ""))
        if compact <= {"-"} and "-" in line:
            seen_separator = True
            continue
        if seen_separator:
            body.append(line)
    return "\n".join(body)


def _is_separator(row: list[str]) -> bool:
    cells = [cell.strip() for cell in row if cell.strip()]
    return bool(cells) and all(_SEP_CELL.match(cell) for cell in cells)


def _is_pipe_line(line: str) -> bool:
    return bool(_PIPE_LINE.match(line))


def _table_run_length(lines: list[str], start: int) -> int:
    count = 0
    while start + count < len(lines) and _is_pipe_line(lines[start + count]):
        count += 1
    return count


def _split_pipe_row(line: str) -> list[str]:
    inner = line.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in inner.split("|")]


def _context_from_previous(output: list[str]) -> tuple[str, str]:
    previous = [line.strip() for line in "\n".join(output).splitlines() if line.strip()]
    title = ""
    section = ""
    for line in reversed(previous[-8:]):
        if line.startswith("类型：") or line.startswith("原始表格"):
            break
        if not section and ("章" in line or line.startswith("#")):
            section = line.lstrip("#").strip()
        if not title and line.startswith("表") and len(line) <= 60:
            title = line
    return title, section
