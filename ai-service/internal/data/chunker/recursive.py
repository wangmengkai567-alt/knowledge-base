"""按 token 预算递归分块，优先在语义边界切开。"""

from __future__ import annotations

import structlog

from internal.biz.repo import TextChunker
from internal.biz.text_structure import fenced_code_ranges
from internal.biz.tokenizer import count_tokens, token_spans

logger = structlog.get_logger()

_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "！", "？", ". ", "? ", "! ", "；", ";", " ", "")


class RecursiveCharacterChunker(TextChunker):
    """递归分块：chunk_size/overlap 表示 token 预算，而非字符数。

    正文和 overlap 均按原字符范围切片。完整文档上的围栏代码不可拆开；
    chunk_size 是普通正文目标，单个超长代码块独立保留，不截断或改写。
    tokenizer 为无外部依赖的估算器，不冒充供应商精确计量。
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and less than chunk_size")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[str]:
        return [text[start:end] for start, end in self.chunk_spans(text)]

    def chunk_spans(self, text: str) -> list[tuple[int, int]]:
        if not text or not text.strip():
            return []
        codes = fenced_code_ranges(text)
        pieces = []
        cursor = 0
        for start, end in codes:
            pieces.extend(self._split(text[cursor:start], list(_SEPARATORS)))
            pieces.append(text[start:end])
            cursor = end
        pieces.extend(self._split(text[cursor:], list(_SEPARATORS)))
        merged = self._merge(pieces)
        ranges = []
        cursor = 0
        for index, part in enumerate(merged):
            start, end = cursor, cursor + len(part)
            if index and self._chunk_overlap:
                previous_start = cursor - len(merged[index - 1])
                tokens = token_spans(merged[index - 1])
                if tokens:
                    start = previous_start + tokens[max(0, len(tokens) - self._chunk_overlap)][0]
                # overlap 也不能从代码块内部开始，否则下一块缺少开围栏。
                for left, right in codes:
                    if left < start < right:
                        start = left
                        break
            ranges.append((start, end))
            cursor = end
        return ranges

    def _split(self, text: str, separators: list[str]) -> list[str]:
        if count_tokens(text) <= self._chunk_size:
            return [text] if text else []
        sep = next((candidate for candidate in separators if candidate and candidate in text), "")
        if not sep:
            return self._hard_split(text)
        rest = separators[separators.index(sep) + 1 :]
        parts = text.split(sep)
        atoms: list[str] = []
        for i, part in enumerate(parts):
            atom = part if i == len(parts) - 1 else part + sep
            if not atom:
                continue
            if count_tokens(atom) <= self._chunk_size:
                atoms.append(atom)
            else:
                atoms.extend(self._split(atom, rest))
        return atoms

    def _hard_split(self, text: str) -> list[str]:
        spans = token_spans(text)
        boundaries = [0, *(spans[i][0] for i in range(self._chunk_size, len(spans), self._chunk_size)), len(text)]
        return [text[start:end] for start, end in zip(boundaries, boundaries[1:], strict=False) if start < end]

    def _merge(self, pieces: list[str]) -> list[str]:
        chunks: list[str] = []
        buf = ""
        for piece in pieces:
            if not piece:
                continue
            if not buf:
                buf = piece
            elif count_tokens(buf) + count_tokens(piece) <= self._chunk_size:
                buf += piece
            else:
                chunks.append(buf)
                buf = piece
        if buf:
            chunks.append(buf)
        return chunks
