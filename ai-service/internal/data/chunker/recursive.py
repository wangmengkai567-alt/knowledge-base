"""按 token 预算递归分块，优先在语义边界切开。"""

from __future__ import annotations

import structlog

from internal.biz.repo import TextChunker
from internal.biz.tokenizer import count_tokens, tokenize

logger = structlog.get_logger()

_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。", "！", "？", ". ", "? ", "! ", "；", ";", " ", "")


class RecursiveCharacterChunker(TextChunker):
    """递归分块：chunk_size/overlap 表示 token 预算，而非字符数。

    先按段落、换行、句子和空格递归拆分，再合并到 token 预算；
    无法找到边界时按 token 窗口硬切。这样既保护语义边界，也能控制
    送入 Embedding/LLM 的真实输入规模。tokenizer 为无外部依赖的估算器。
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be non-negative and less than chunk_size")
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def chunk(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        pieces = self._split(text.strip(), list(_SEPARATORS))
        merged = self._merge(pieces)
        return self._with_overlap(merged)

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
        tokens = tokenize(text)
        out: list[str] = []
        for start in range(0, len(tokens), self._chunk_size):
            out.append("".join(tokens[start : start + self._chunk_size]).strip())
        return [item for item in out if item]

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
        return [c.strip() for c in chunks if c.strip()]

    def _with_overlap(self, chunks: list[str]) -> list[str]:
        if self._chunk_overlap <= 0 or len(chunks) <= 1:
            return chunks
        out = [chunks[0]]
        for chunk in chunks[1:]:
            tail = "".join(tokenize(out[-1])[-self._chunk_overlap :])
            out.append((tail + chunk).strip() if tail else chunk)
        return out

