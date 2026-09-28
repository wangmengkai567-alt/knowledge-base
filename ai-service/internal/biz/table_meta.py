"""从表格检索文本里读出可放进向量 metadata 的标记。"""

from __future__ import annotations


def index_metadata(content: str) -> dict:
    """读不到标记时返回空字典，不影响原有 document_id / knowledge_base_id。"""
    meta: dict = {}
    for line in content.splitlines()[:16]:
        if "：" not in line:
            continue
        key, value = line.split("：", 1)
        value = value.strip()
        if key == "类型" and "content_type" not in meta:
            if value == "表格行":
                meta["content_type"] = "table_row"
            elif value == "表格":
                meta["content_type"] = "table"
        elif key == "表格编号" and "table_id" not in meta and value:
            meta["table_id"] = value
        elif key == "行号" and value.isdigit() and "row_id" not in meta:
            meta["row_id"] = int(value)
        elif key == "页码" and "page" not in meta:
            first = value.split("-", 1)[0]
            if first.isdigit():
                meta["page"] = int(first)
    return meta
