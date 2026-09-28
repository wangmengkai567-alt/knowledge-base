"""
internal/data/parser/pdf.py — PDF 文档解析器

使用 PyMuPDF 抽取文字。扫描页几乎没有文字层时，用 OCR 识别文字和能对齐的表格。
单张表或 OCR 失败只记日志，不中断整份文档。
"""

from __future__ import annotations

import structlog

from internal.data.parser.base import BaseDocumentParser
from internal.data.parser.ocr import native_text_is_sparse, recognize_page
from internal.data.parser.table_struct import StructuredTable, build_table, try_merge_cross_page

logger = structlog.get_logger()


class PDFParser(BaseDocumentParser):
    """PDF 解析器 — 版式文本，表格保留表头和单元格的对应关系。"""

    def _parse_sync(self, file_path: str) -> str:
        import pymupdf as fitz

        doc = fitz.open(file_path)
        try:
            items = _collect_items(doc, fitz)
            content = "\n\n".join(part for part in _render_items(items) if part.strip())
            if not content.strip():
                logger.warning("pdf_no_text_content", file_path=file_path)
            return content
        except Exception as e:
            logger.error("pdf_parse_failed", file_path=file_path, error=str(e))
            return ""
        finally:
            doc.close()


def _collect_items(doc, fitz_mod) -> list[dict]:
    items: list[dict] = []
    for page_index, page in enumerate(doc):
        page_no = page_index + 1
        try:
            page_items = _page_items(page, fitz_mod, page_no)
        except Exception as e:
            logger.warning("pdf_page_failed", page=page_no, error=str(e))
            continue
        items.extend(page_items)
    items.sort(key=lambda item: (item["page"], item["y"]))
    return items


def _page_items(page, fitz_mod, page_no: int) -> list[dict]:
    try:
        found = page.find_tables()
        tables = list(getattr(found, "tables", found) or [])
    except Exception as e:
        logger.warning("pdf_find_tables_skipped", page=page_no, error=str(e))
        tables = []

    table_rects = []
    items: list[dict] = []
    page_height = float(page.rect.height or 0)
    try:
        blocks = page.get_text("blocks") or []
    except Exception as e:
        logger.warning("pdf_text_blocks_failed", page=page_no, error=str(e))
        blocks = []

    for table_index, table in enumerate(tables, start=1):
        try:
            bbox = fitz_mod.Rect(table.bbox)
            grid = table.extract()
        except Exception as e:
            logger.warning("pdf_table_extract_failed", page=page_no, error=str(e))
            continue
        title, section = _context_above(blocks, float(bbox.y0))
        structured = build_table(
            grid,
            table_id=f"p{page_no}-t{table_index}",
            title=title,
            section=section,
            page=page_no,
        )
        if structured is None:
            continue
        table_rects.append(bbox)
        items.append({
            "page": page_no,
            "y": float(bbox.y0),
            "y0": float(bbox.y0),
            "page_height": page_height,
            "kind": "table",
            "table": structured,
            "skipped": False,
        })

    for block in blocks:
        if not isinstance(block, (list, tuple)) or len(block) < 5:
            continue
        x0, y0, x1, y1, text = block[:5]
        raw = str(text or "").strip()
        if not raw:
            continue
        rect = fitz_mod.Rect(x0, y0, x1, y1)
        if any(_mostly_inside(rect, tb) for tb in table_rects):
            continue
        items.append({
            "page": page_no,
            "y": float(y0),
            "kind": "text",
            "text": raw,
        })
    native = "\n".join(item["text"] for item in items if item["kind"] == "text")
    has_table = any(item["kind"] == "table" for item in items)
    if not has_table and native_text_is_sparse(native):
        ocr_items = recognize_page(page, page_no, page_height)
        if ocr_items:
            return ocr_items
    if not items:
        fallback = (page.get_text("text") or "").strip()
        if fallback:
            items.append({"page": page_no, "y": 0.0, "kind": "text", "text": fallback})
    return items


def _render_items(items: list[dict]) -> list[str]:
    open_table: StructuredTable | None = None
    parts: list[str] = []
    for item in items:
        if item["kind"] == "text":
            parts.append(item["text"])
            continue
        table: StructuredTable = item["table"]
        if open_table is not None and try_merge_cross_page(
            open_table,
            table,
            y0=item["y0"],
            page_height=item["page_height"],
        ):
            continue
        open_table = table
        parts.append(table)
    rendered: list[str] = []
    for part in parts:
        if isinstance(part, StructuredTable):
            rendered.append(part.retrieval_text())
        else:
            rendered.append(str(part))
    return rendered


def _context_above(blocks, table_top: float) -> tuple[str, str]:
    above: list[tuple[float, str]] = []
    for block in blocks:
        if not isinstance(block, (list, tuple)) or len(block) < 5:
            continue
        y1 = float(block[3])
        line = str(block[4] or "").strip().splitlines()[0].strip() if str(block[4] or "").strip() else ""
        if not line or y1 > table_top + 1:
            continue
        above.append((y1, line))
    above.sort()
    title = ""
    section = ""
    for y1, line in reversed(above):
        if table_top - y1 > 140:
            break
        if not section and "章" in line and len(line) <= 40:
            section = line
            continue
        if not title and len(line) <= 60 and table_top - y1 <= 48:
            title = line
            break
    return title, section


def _mostly_inside(inner, outer) -> bool:
    """inner 矩形是否大部分落在表格区域内。"""
    try:
        overlap = inner & outer
        inner_area = inner.get_area()
        if inner_area <= 0:
            return False
        return overlap.get_area() / inner_area >= 0.6
    except Exception:
        return False
