"""扫描页 OCR。文字层已经够用的页面不走这里。

使用 RapidOCR（内置中文模型）。未安装或识别失败时返回空列表，不中断解析。
"""

from __future__ import annotations

import re
import threading

import structlog

from internal.data.parser.table_struct import build_table

logger = structlog.get_logger()

# PDF 原生文字去掉空白后少于此字符数时，判定需要 OCR。
_MIN_NATIVE_CHARS = 20
# 进程内复用的 OCR 引擎实例，首次识别时由 _get_engine 创建。
_engine = None
# 记录本进程是否已发生引擎初始化失败；失败后不再重复尝试加载。
_engine_failed = False
# 保护 OCR 引擎的首次初始化，避免多个线程同时创建模型实例。
_engine_lock = threading.Lock()


def native_text_is_sparse(text: str) -> bool:
    """几乎没有可选中文字时，才把这一页交给 OCR。"""
    compact = re.sub(r"\s+", "", text or "")
    return len(compact) < _MIN_NATIVE_CHARS


def recognize_page(page, page_no: int, page_height: float) -> list[dict]:
    """把扫描页渲染成图再识别。失败返回空列表。"""
    engine = _get_engine()
    if engine is None:
        return []
    try:
        import numpy as np

        pixmap = page.get_pixmap(dpi=150, alpha=False)
        if pixmap.width <= 0 or pixmap.height <= 0:
            return []
        image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
        result, _elapsed = engine(image)
    except Exception as e:
        logger.warning("pdf_ocr_failed", page=page_no, error=str(e))
        return []
    if not result:
        logger.info("pdf_ocr_empty", page=page_no)
        return []

    scale_x = float(page.rect.width) / float(pixmap.width)
    scale_y = float(page.rect.height) / float(pixmap.height)
    # 将识别框的图片坐标换回 PDF 页面坐标，供正文排序和表格定位使用。
    boxes: list[tuple[float, float, float, float, str]] = []
    for item in result:
        try:
            points, text, score = item[0], str(item[1] or "").strip(), float(item[2])
        except (TypeError, ValueError, IndexError):
            continue
        if score < 0.5 or not text:
            continue
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        boxes.append((min(xs) * scale_x, min(ys) * scale_y, max(xs) * scale_x, max(ys) * scale_y, text))
    logger.info("pdf_ocr_done", page=page_no, boxes=len(boxes))
    return items_from_ocr_boxes(boxes, page_no=page_no, page_height=page_height)


def items_from_ocr_boxes(
    boxes: list[tuple[float, float, float, float, str]],
    *,
    page_no: int,
    page_height: float,
) -> list[dict]:
    """按纵坐标分行。能对齐成多列的连续行收成表格，其余按阅读顺序输出。"""
    rows = _cluster_rows(boxes)
    items: list[dict] = []
    table_index = 0
    index = 0
    while index < len(rows):
        if len(rows[index]) < 2:
            items.append(_text_item(page_no, rows[index]))
            index += 1
            continue
        end = index
        while end < len(rows) and len(rows[end]) >= 2:
            end += 1
        grid = _align_columns(rows[index:end])
        table_index += 1
        table = build_table(grid, table_id=f"p{page_no}-ocr-{table_index}", page=page_no) if grid else None
        if table is None:
            for row in rows[index:end]:
                items.append(_text_item(page_no, row))
        else:
            y0 = min(cell[1] for row in rows[index:end] for cell in row)
            items.append({
                "page": page_no,
                "y": y0,
                "y0": y0,
                "page_height": page_height,
                "kind": "table",
                "table": table,
                "skipped": False,
            })
        index = end
    return items


# 延迟创建并复用 OCR 引擎；初始化失败后不再反复加载模型。
def _get_engine():
    global _engine, _engine_failed
    if _engine_failed:
        return None
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None or _engine_failed:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        except Exception as e:
            _engine_failed = True
            logger.warning(
                "ocr_unavailable",
                error=str(e),
                hint="使用 uv sync --locked 安装依赖；Docker 环境需重建镜像",
            )
            return None
    return _engine


# 按纵向位置将文字框聚成行，再按从左到右的顺序排列。
def _cluster_rows(boxes: list[tuple[float, float, float, float, str]]) -> list[list[tuple]]:
    if not boxes:
        return []
    heights = [max(box[3] - box[1], 1.0) for box in boxes]
    tolerance = sorted(heights)[len(heights) // 2] * 0.6
    ordered = sorted(boxes, key=lambda box: ((box[1] + box[3]) / 2, box[0]))
    rows: list[list[tuple]] = []
    centers: list[float] = []
    for box in ordered:
        center = (box[1] + box[3]) / 2
        if rows and abs(center - centers[-1]) <= tolerance:
            rows[-1].append(box)
            centers[-1] = (centers[-1] * (len(rows[-1]) - 1) + center) / len(rows[-1])
        else:
            rows.append([box])
            centers.append(center)
    for row in rows:
        row.sort(key=lambda box: box[0])
    return rows


# 以文字框最多的一行为列位置参照，将连续多行整理成表格网格。
def _align_columns(rows: list[list[tuple]]) -> list[list[str]] | None:
    if len(rows) < 2:
        return None
    width = max(len(row) for row in rows)
    if width < 2:
        return None
    anchors = max(rows, key=len)
    columns = [box[0] for box in anchors]
    grid: list[list[str]] = []
    for row in rows:
        cells = [""] * width
        for box in row:
            nearest = min(range(width), key=lambda col: abs(box[0] - columns[col]))
            cells[nearest] = f"{cells[nearest]} {box[4]}".strip() if cells[nearest] else box[4]
        grid.append(cells)
    return grid


def _text_item(page_no: int, row: list[tuple]) -> dict:
    """把同一行 OCR 文字框合成正文条目，记录页码和最上方纵坐标，供页面内容排序。"""
    y0 = min(box[1] for box in row)
    return {
        "page": page_no,
        "y": y0,
        "kind": "text",
        "text": " ".join(box[4] for box in row if box[4]),
    }
