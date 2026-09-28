"""检索命中后再向两侧取邻块，拼成连续段落交给 LLM。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from internal.biz.entity import Chunk, RetrievalResult


@dataclass(frozen=True)
class ContextWindow:
    """由相邻分块拼成的连续段落。"""

    document_id: int
    document_filename: str
    score: float
    contents: list[str]
    chunk_ids: list[int]

    @property
    def text(self) -> str:
        return "".join(self.contents).strip()


def build_windows(
    sources: list[RetrievalResult],
    chunks_by_doc: dict[int, list[Chunk]],
    window: int,
) -> list[ContextWindow]:
    """每个命中块向两侧扩展 ±window 个位置，再把连续区间合并。

    窗口分数取区间内命中块的最高检索分，邻块只补上下文、不抬排名。
    """
    if not sources:
        return []
    if window <= 0:
        return [
            ContextWindow(
                document_id=s.document_id,
                document_filename=s.document_filename,
                score=s.score,
                contents=[s.chunk_content],
                chunk_ids=[s.chunk_id],
            )
            for s in sources
        ]

    hit_score = {s.chunk_id: s.score for s in sources}
    by_doc: dict[int, list[RetrievalResult]] = defaultdict(list)
    for source in sources:
        by_doc[source.document_id].append(source)

    windows: list[ContextWindow] = []
    for doc_id, hits in by_doc.items():
        filename = hits[0].document_filename
        by_pos = {
            chunk.position: chunk
            for chunk in chunks_by_doc.get(doc_id, [])
            if chunk.id is not None
        }
        if not by_pos:
            for hit in hits:
                windows.append(
                    ContextWindow(
                        document_id=doc_id,
                        document_filename=filename,
                        score=hit.score,
                        contents=[hit.chunk_content],
                        chunk_ids=[hit.chunk_id],
                    )
                )
            continue

        selected: set[int] = set()
        for hit in hits:
            for pos in range(hit.chunk_position - window, hit.chunk_position + window + 1):
                if pos in by_pos:
                    selected.add(pos)

        run: list[int] = []

        def flush() -> None:
            if not run:
                return
            ordered = [by_pos[pos] for pos in run]
            score = max(
                (hit_score.get(chunk.id, 0.0) for chunk in ordered if chunk.id is not None),
                default=0.0,
            )
            windows.append(
                ContextWindow(
                    document_id=doc_id,
                    document_filename=filename,
                    score=score,
                    contents=[chunk.content for chunk in ordered],
                    chunk_ids=[chunk.id for chunk in ordered if chunk.id is not None],
                )
            )

        for pos in sorted(selected):
            if run and pos != run[-1] + 1:
                flush()
                run = []
            run.append(pos)
        flush()

    windows.sort(key=lambda item: item.score, reverse=True)
    return windows


def windows_to_context_chunks(windows: list[ContextWindow]) -> list[dict]:
    """把窗口转成 ``build_context_block`` 需要的结构。"""
    return [
        {
            "content": window.text,
            "document_filename": window.document_filename,
            "score": window.score,
        }
        for window in windows
        if window.text
    ]
