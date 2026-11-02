"""按事实条目核对本次送模摘录并渲染引用；不执行语义蕴含判断。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from internal.biz.materials import AnswerSource, WebSource

if TYPE_CHECKING:
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


def _source_identity(source: AnswerSource | WebSource) -> tuple:
    if isinstance(source, AnswerSource):
        return "knowledge_base", source.knowledge_base_id, source.document_id
    # 同一网址的摘要、多个正文窗口都不是独立来源。
    return "web", source.url


def render_answer(
    payload: AnswerPayload, allowed: dict[int, AnswerSource | WebSource],
) -> RenderedAnswer:
    paragraphs: list[str] = []
    records: list[dict] = []
    used: set[int] = set()
    labels = {"knowledge_base": "知识库", "web_page": "网页正文", "web_search_summary": "搜索摘要"}
    for claim in payload.claims:
        text = claim.text.strip()
        if not text or "[来源" in text or re.search(r"`{3,}|~{3,}", text):
            raise AnswerEvidenceError("invalid_citation_or_format")
        sources = []
        evidence = []
        for support in claim.evidence:
            if support.citation_id not in allowed:
                raise AnswerEvidenceError("invalid_claim_citation")
            source = allowed[support.citation_id]
            quote = support.quote
            start = source.content.find(quote)
            if not quote.strip() or start < 0:
                raise AnswerEvidenceError("unsupported_quote")
            if source.source_type == "web_search_summary" and (payload.status == "answered" or claim.scope == "shared"):
                raise AnswerEvidenceError("web_body_required")
            sources.append(source)
            evidence.append({
                "citation_id": support.citation_id, "quote": quote,
                "start": start, "end": start + len(quote), "range_basis": "sent_content",
            })
        identities = {_source_identity(source) for source in sources}
        if (claim.scope == "source" and len(identities) != 1) or (claim.scope == "shared" and len(identities) < 2):
            raise AnswerEvidenceError("invalid_source_scope")
        ids = sorted({part["citation_id"] for part in evidence})
        refs = "".join(f"[来源 {number}]" for number in ids)
        label = "多来源对照" if claim.scope == "shared" else labels[sources[0].source_type]
        # 不允许 HTML 在一个事实条目中另起无引用段落。
        paragraphs.append(f"{label}：{html.escape(text, quote=False)}{refs}")
        codes = re.findall(r"`([^`]+)`", text)
        code = claim.code
        if code is not None:
            if not code.strip() or "[来源" in code:
                raise AnswerEvidenceError("invalid_citation_or_format")
            codes.append(code)
        for snippet in codes:
            if not any(snippet in part["quote"] and source.source_type != "web_search_summary"
                       for source, part in zip(sources, evidence, strict=True)):
                raise AnswerEvidenceError("unsupported_code")
        if code is not None:
            fence = "`" * max(3, max((len(match.group()) + 1 for match in re.finditer(r"`+", code)), default=3))
            paragraphs.append(f"{fence}\n{code}" + ("" if code.endswith("\n") else "\n") + f"{fence}\n\n{refs}")
        records.append({"scope": claim.scope, "text": text, "code": code,
                        "citation_ids": ids, "evidence": evidence})
        used.update(ids)
    for topic in payload.missing:
        if not topic.strip() or len(topic) > 120 or any(part in topic for part in ("\r", "\n", "[来源", "`")):
            raise AnswerEvidenceError("invalid_citation_or_format")
        escaped = re.sub(r"([\\*\[\]_!])", r"\\\1", html.escape(topic.strip(), quote=False))
        paragraphs.append(f"本次已读取的资料尚未覆盖“{escaped}”的充分依据。")
    return RenderedAnswer("\n\n".join(paragraphs), records, sorted(used))
