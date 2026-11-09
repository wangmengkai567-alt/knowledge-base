"""最终请求的连续原文目录；模型只选编号，程序绑定位置和内容。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from internal.biz.materials import AnswerSource, WebSource
from internal.biz.tool_types import ToolResult

if TYPE_CHECKING:
    from internal.biz.tools import ToolScope


class EvidenceBindingError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceFragment:
    evidence_id: str
    run_id: str
    citation_id: int
    kind: Literal["text", "code"]
    content: str
    origin: dict


@dataclass(frozen=True)
class EvidenceUnit:
    evidence_id: str
    citation_id: int
    excerpt_index: int
    start: int
    end: int
    kind: Literal["text", "code"]
    content: str

    def model_value(self) -> dict:
        return {"evidence_id": self.evidence_id, "kind": self.kind, "content": self.content}


def final_sources(messages: list[dict]) -> tuple[list[dict], list[dict], dict | None]:
    sources, errors, selection = [], [], None
    for message in messages:
        if message["role"] != "tool":
            continue
        payload = json.loads(message["content"])
        sources.extend(payload["sources"])
        errors.extend(payload["errors"])
        if payload["selection"] is not None:
            selection = payload["selection"]
    if selection is not None:
        selected = set(selection["citation_ids"])
        sources = [source for source in sources if source["citation_id"] in selected]
    if len({source["citation_id"] for source in sources}) != len(sources):
        raise EvidenceBindingError("duplicate_final_source")
    return sources, errors, selection


def catalog_units(sources: list[dict]) -> list[EvidenceUnit]:
    """每段连续原文按完整代码和其前后正文分开，跨片段不拼接。"""
    units = []
    for source in sources:
        content = source["content"]
        ranges = (source["excerpt_ranges"] if source["source_type"] == "knowledge_base"
                  else [{"start": 0, "end": len(content)}])
        codes = source["code_ranges"]
        for index, span in enumerate(ranges):
            start, end = span["start"], span["end"]
            if not 0 <= start < end <= len(content):
                raise EvidenceBindingError("invalid_final_excerpt")
            cursor = start
            portions = []
            for left, right in codes:
                if left >= end or right <= start:
                    continue
                if not cursor <= left < right <= end:
                    raise EvidenceBindingError("partial_final_code")
                portions.extend([(cursor, left, "text"), (left, right, "code")])
                cursor = right
            portions.append((cursor, end, "text"))
            for left, right, kind in portions:
                if content[left:right].strip():
                    units.append(EvidenceUnit(f"E{len(units) + 1}", source["citation_id"], index,
                                              left, right, kind, content[left:right]))
    return units


def catalog_sources(sources: list[dict], units: list[EvidenceUnit]) -> list[dict]:
    return [
        {**{key: value for key, value in source.items()
            if key not in {"content", "excerpt_ranges", "excerpt_note", "code_ranges"}},
         "evidence": [unit.model_value() for unit in units if unit.citation_id == source["citation_id"]]}
        for source in sources
    ]


def bind_catalog(
    messages: list[dict], active_sources: list[AnswerSource | WebSource], scope: ToolScope, run_id: str,
) -> list[EvidenceFragment]:
    """仅绑定本次最终请求实际包含的片段，不接受模型提供坐标或原文。"""
    sources, _, _ = final_sources(messages)
    active = {source.citation_id: source for source in active_sources}
    payloads = {source["citation_id"]: source for source in sources}
    bound = []
    # 先比对送模资料与服务器登记值，再将最终请求中的偏移还原到原分块或网页。
    for unit in catalog_units(sources):
        source = active.get(unit.citation_id)
        if source is None or ToolResult("", "", "success", sources=[source]).source_values()[0] != payloads[unit.citation_id]:
            raise EvidenceBindingError("final_source_mismatch")
        if isinstance(source, AnswerSource):
            if source.knowledge_base_id not in scope.knowledge_base_ids or not source.document_updated_at:
                raise EvidenceBindingError("evidence_outside_scope")
            segment = source.segments[unit.excerpt_index]
            span = payloads[unit.citation_id]["excerpt_ranges"][unit.excerpt_index]
            left, right = unit.start - span["start"], unit.end - span["start"]
            if segment.content[left:right] != unit.content:
                raise EvidenceBindingError("evidence_slice_mismatch")
            origin = {"source_type": source.source_type, "knowledge_base_id": source.knowledge_base_id,
                      "document_id": source.document_id, "document_version": source.document_updated_at,
                      "chunk_id": segment.chunk_id, "position": segment.position,
                      "start": segment.start + left, "end": segment.start + right}
            if unit.kind == "code" and (origin["start"], origin["end"]) not in segment.code_ranges:
                raise EvidenceBindingError("unregistered_code_range")
        else:
            if not scope.allow_web:
                raise EvidenceBindingError("evidence_outside_scope")
            origin = {"source_type": source.source_type, "url": source.url, "source_id": source.source_id,
                      "fetched_at": source.fetched_at,
                      "start": source.start + unit.start, "end": source.start + unit.end}
            if unit.kind == "code" and (origin["start"], origin["end"]) not in source.code_ranges:
                raise EvidenceBindingError("unregistered_code_range")
        if origin["end"] - origin["start"] != len(unit.content):
            raise EvidenceBindingError("evidence_range_mismatch")
        bound.append(EvidenceFragment(unit.evidence_id, run_id, unit.citation_id, unit.kind, unit.content, origin))
    return bound


def code_body(fragment: EvidenceFragment) -> tuple[str, str]:
    """展示围栏与原文分开；返回原代码正文及语言，不改变引文。"""
    if fragment.kind != "code":
        raise EvidenceBindingError("code_evidence_required")
    lines = fragment.content.splitlines(keepends=True)
    marker = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*", lines[0])
    if marker is None:
        raise EvidenceBindingError("invalid_code_evidence")
    fence = marker[1]
    closed = len(lines) > 1 and re.fullmatch(
        r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*[\r\n]*", lines[-1],
    ) is not None
    body = "".join(lines[1:-1] if closed else lines[1:])
    language = marker[2].strip()
    return body, language if re.fullmatch(r"[a-zA-Z0-9_+.-]*", language) else ""
