"""Faithfulness for RAG eval.

A sentence is grounded when the retrieved text supports it, including a
paraphrase or a short summary. Literal 4-gram overlap is not required.
Neighbor chunks are included by the caller, matching rag.context_window.
"""
from __future__ import annotations

import re

CITE_RE = re.compile(r"\[来源\s*\d+\]")

# Function-word bigrams. They show up in almost every sentence and do not
# show that a claim is about the same fact as the evidence.
_STOP_BIGRAMS = {
    "一个", "一种", "一下", "一些", "这个", "这种", "这样", "那个", "我们", "它们", "他们",
    "可以", "可能", "需要", "应该", "必须", "如果", "因为", "所以", "但是", "不过", "然后",
    "以及", "或者", "并且", "而且", "通过", "对于", "作为", "就是", "也是", "不是", "没有",
    "不会", "不能", "不要", "进行", "使用", "包括", "表示", "用于", "其中", "因此", "同时",
    "已经", "还是", "只是", "主要", "一般", "通常", "例如", "比如", "的是", "是指", "来说",
    "方面", "情况", "时候", "之后", "之前", "当中", "自己", "什么", "怎么", "哪些", "是否",
    "也会", "就会", "都是", "还有", "从而", "以便", "不是", "不会",
}

# Recall of content bigrams at or above this counts as a paraphrase.
# Tuned on answers that failed a literal 4-gram check but restated the evidence.
PARAPHRASE_RECALL = 0.10

_META_RE = re.compile(
    r"(资料|参考资料|文档).{0,16}(没有|未|无).{0,12}(说明|提到|写|覆盖|给出|进一步)"
)
_ENUM_RE = re.compile(r"(?:^|\n)\s*\d+[.、)]\s*")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_UNIT_RE = re.compile(r"\s*(?:个|次|字节|秒|分钟|小时|毫秒|层|维|条|年|MB|GB|KB|ms|%)")
_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{2,}")
_ID_STOP = {
    "true", "false", "none", "null", "http", "https", "www", "com",
    "the", "and", "for", "with",
}
# A heading or bilingual label. Longer sentences stay under the recall check.
_SHORT_HAN = 8

_SYNONYMS = (
    ("不是同一套数字", "不一样"),
    ("并不是同一套数字", "不一样"),
    ("不是同一套", "不一样"),
    ("并不是同一套", "不一样"),
    ("黑匣子", "黑盒"),
    ("黑箱子", "黑盒"),
    ("黑箱", "黑盒"),
    ("还没有", "未"),
    ("尚未", "未"),
    ("没有", "未"),
    ("不算", "不是"),
    ("并非", "不是"),
)

# Comparison wrap-up: restates "the listed examples differ" without a new fact.
_WRAPUP_RE = re.compile(r"(并)?不是同一套|并不?一样|各不相同")


def normalize_match(text: str) -> str:
    """Fold wording that should not fail an exact keyword check."""
    folded = (text or "").lower()
    folded = re.sub(r"\s+", "", folded)
    folded = folded.replace("的", "")
    for src, dst in _SYNONYMS:
        folded = folded.replace(src, dst)
    folded = re.sub(r"[*_`#\[\]（）(){}「」\"'：:，,。！？、]", "", folded)
    return folded


def term_covered(answer: str, term: str) -> bool:
    if not term:
        return True
    return normalize_match(term) in normalize_match(answer)


def split_claims(answer: str) -> list[str]:
    """Split on Chinese sentence breaks. Do not split on `!` inside `!=`."""
    text = CITE_RE.sub("", answer or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    parts = re.split(r"[。！？；\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def is_substantive_claim(claim: str) -> bool:
    stripped = claim.strip().strip("-*> ")
    if not stripped or re.fullmatch(r"[\s|:\-]+", stripped):
        return False
    compact = re.sub(r"\s+", "", stripped)
    if compact.endswith(("：", ":")) and len(compact) <= 40:
        return False
    if _META_RE.search(stripped):
        return False
    alnum = re.sub(r"[^\w\u4e00-\u9fff]", "", compact, flags=re.UNICODE)
    if len(alnum) < 8 and not _NUMBER_RE.search(compact):
        return False
    return True


def _content_bigrams(text: str) -> set[str]:
    chars = [c for c in re.sub(r"\s+", "", text or "") if "\u4e00" <= c <= "\u9fff"]
    grams = {chars[i] + chars[i + 1] for i in range(len(chars) - 1)}
    return {g for g in grams if g not in _STOP_BIGRAMS}


def content_bigram_recall(claim: str, evidence: str) -> float:
    grams = _content_bigrams(claim)
    if not grams:
        return 1.0
    return len(grams & _content_bigrams(evidence)) / len(grams)


def _han_count(text: str) -> int:
    return sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")


def _identifiers(text: str) -> list[str]:
    plain = re.sub(r"`([^`]*)`", r" \1 ", text or "")
    found: list[str] = []
    seen: set[str] = set()
    for match in _ID_RE.finditer(plain):
        token = match.group(0).lower().strip("._-+")
        if len(token) < 3 or token in _ID_STOP or token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found


def identifiers_grounded(claim: str, evidence: str) -> bool:
    """Same technical names as the evidence, even if the Chinese is a gloss."""
    names = _identifiers(claim)
    if len(names) < 2:
        return False
    folded = (evidence or "").lower()
    return all(name in folded for name in names)


def novel_numbers(claim: str, evidence: str) -> list[str]:
    """Digits the claim adds: multi-digit values, or a number tied to a unit."""
    present = set(_NUMBER_RE.findall(evidence or ""))
    body = _ENUM_RE.sub(" ", claim or "")
    missing: list[str] = []
    for match in _NUMBER_RE.finditer(body):
        number = match.group(0)
        if number in present:
            continue
        unit = _UNIT_RE.match(body[match.end() : match.end() + 8])
        if unit or len(number) >= 2 or "." in number:
            missing.append(number)
    return missing


def unsupported_sentences(answer: str, evidence: str) -> list[str]:
    """Claims the evidence does not support.

    Paraphrase and summary are supported. A claim is unsupported when it
    shares almost no content words with the evidence, or it introduces a
    number the evidence does not contain.
    """
    claims = [p for p in split_claims(answer) if is_substantive_claim(p)]
    if not (evidence or "").strip():
        return claims or ["<empty-evidence>"]
    bad: list[str] = []
    for claim in claims:
        numbers = novel_numbers(claim, evidence)
        if numbers:
            bad.append(claim)
            continue
        if _WRAPUP_RE.search(claim) and not numbers:
            continue
        if _han_count(claim) <= _SHORT_HAN:
            continue
        if content_bigram_recall(claim, evidence) >= PARAPHRASE_RECALL:
            continue
        if identifiers_grounded(claim, evidence):
            continue
        bad.append(claim)
    return bad


def neighbor_evidence(
    results: list[dict],
    chunks_by_doc: dict[int, list[dict]],
    window: int,
) -> str:
    """Hit chunks plus ±window neighbors, the same span generation reads."""
    if window <= 0:
        return "\n".join((row.get("content") or "") for row in results)
    pos_maps: dict[int, dict[int, dict]] = {}
    for doc_id, chunks in chunks_by_doc.items():
        pos_maps[doc_id] = {int(chunk["position"]): chunk for chunk in chunks}
    seen: set[int] = set()
    parts: list[str] = []
    for row in results:
        doc_id = row.get("document_id")
        pos = row.get("position")
        pos_map = pos_maps.get(doc_id) or {}
        if pos is None or not pos_map:
            cid = row.get("chunk_id")
            if cid not in seen:
                if cid is not None:
                    seen.add(cid)
                parts.append(row.get("content") or "")
            continue
        for position in range(int(pos) - window, int(pos) + window + 1):
            chunk = pos_map.get(position)
            if chunk is None:
                continue
            cid = chunk.get("id")
            if cid in seen:
                continue
            if cid is not None:
                seen.add(cid)
            parts.append(chunk.get("content") or "")
    return "\n".join(parts)
