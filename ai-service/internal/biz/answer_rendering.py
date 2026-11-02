"""由登记的证据编号取得原文和代码；不让模型重新构造引文。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from internal.biz.evidence import EvidenceBindingError, code_body

if TYPE_CHECKING:
    from internal.biz.evidence import EvidenceFragment
    from internal.biz.tool_types import AnswerPayload


class AnswerEvidenceError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RenderedAnswer:
    answer: str
    claims: list[dict]
    citation_ids: list[int]


def _source_identity(origin: dict) -> tuple:
    if origin["source_type"] == "knowledge_base":
        return "knowledge_base", origin["knowledge_base_id"], origin["document_id"]
    return "web", origin["url"]


def render_answer(payload: AnswerPayload, allowed: dict[str, EvidenceFragment]) -> RenderedAnswer:
    paragraphs, records, used = [], [], set()
    labels = {"knowledge_base": "知识库", "web_page": "网页正文", "web_search_summary": "搜索摘要"}
    for claim in payload.claims:
        text = claim.text.strip()
        if not text or "[来源" in text or re.search(r"`{3,}|~{3,}", text):
            raise AnswerEvidenceError("invalid_citation_or_format")
        if len(set(claim.evidence_ids)) != len(claim.evidence_ids):
            raise AnswerEvidenceError("duplicate_evidence_id")
        fragments, evidence = [], []
        for evidence_id in claim.evidence_ids:
            if evidence_id not in allowed:
                raise AnswerEvidenceError("invalid_evidence_id")
            fragment = allowed[evidence_id]
            if fragment.origin["source_type"] == "web_search_summary" and (payload.status == "answered" or claim.scope == "shared"):
                raise AnswerEvidenceError("web_body_required")
            fragments.append(fragment)
            evidence.append({"evidence_id": evidence_id, "citation_id": fragment.citation_id,
                             "quote": fragment.content, "origin": fragment.origin,
                             "range_basis": "registered_original_slice"})
        identities = {_source_identity(fragment.origin) for fragment in fragments}
        if (claim.scope == "source" and len(identities) != 1) or (claim.scope == "shared" and len(identities) < 2):
            raise AnswerEvidenceError("invalid_source_scope")
        ids = sorted({fragment.citation_id for fragment in fragments})
        refs = "".join(f"[来源 {number}]" for number in ids)
        label = "多来源对照" if claim.scope == "shared" else labels[fragments[0].origin["source_type"]]
        paragraphs.append(f"{label}：{html.escape(text, quote=False)}{refs}")
        for inline in re.findall(r"`([^`]+)`", text):
            if not any(inline in fragment.content and fragment.origin["source_type"] != "web_search_summary" for fragment in fragments):
                raise AnswerEvidenceError("unsupported_code")
        code = None
        if claim.code_evidence_id is not None:
            if claim.code_evidence_id not in claim.evidence_ids:
                raise AnswerEvidenceError("code_evidence_not_selected")
            try:
                code, language = code_body(allowed[claim.code_evidence_id])
            except EvidenceBindingError as exc:
                raise AnswerEvidenceError(str(exc)) from exc
            if not code.strip():
                raise AnswerEvidenceError("empty_code_evidence")
            fence = "`" * max(3, max((len(match.group()) + 1 for match in re.finditer(r"`+", code)), default=3))
            paragraphs.append(f"{fence}{language}\n{code}" + ("" if code.endswith("\n") else "\n") + f"{fence}\n\n{refs}")
        records.append({"scope": claim.scope, "text": text, "evidence_ids": claim.evidence_ids,
                        "code_evidence_id": claim.code_evidence_id, "code": code,
                        "citation_ids": ids, "evidence": evidence, "quote_owner": "program"})
        used.update(ids)
    for topic in payload.missing:
        if not topic.strip() or len(topic) > 120 or any(part in topic for part in ("\r", "\n", "[来源", "`")):
            raise AnswerEvidenceError("invalid_citation_or_format")
        escaped = re.sub(r"([\\*\[\]_!])", r"\\\1", html.escape(topic.strip(), quote=False))
        paragraphs.append(f"本次已读取的资料尚未覆盖“{escaped}”的充分依据。")
    return RenderedAnswer("\n\n".join(paragraphs), records, sorted(used))
