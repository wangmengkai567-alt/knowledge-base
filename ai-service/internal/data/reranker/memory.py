"""按查询词在文件名和正文中的覆盖率，为检索结果重新评分。

无词匹配时，将本批原始检索分映射到 0.001～0.009 以保留相对顺序。
此实现不调用重排模型。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from internal.biz.query_plan import lexical_query
from internal.biz.repo import Reranker

logger = structlog.get_logger()

# 提取英文和数字词项。
_EN_WORD = re.compile(r"[a-zA-Z0-9]+")
# 提取连续中文片段。
_ZH_RUN = re.compile(r"[\u4e00-\u9fff]+")

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult


def _tokenize(text: str) -> set[str]:
    """提取小写英文和数字词；中文连续片段长度为 1 时保留单字，其余生成相邻双字组合。"""
    lowered = text.lower()
    tokens: set[str] = set(_EN_WORD.findall(lowered))
    for run in _ZH_RUN.findall(lowered):
        if len(run) == 1:
            tokens.add(run)
        else:
            for i in range(len(run) - 1):
                tokens.add(run[i : i + 2])
    return tokens


class MemoryReranker(Reranker):
    """不调用外部模型，按查询词覆盖率评分；无词匹配时依据本批原检索分生成低分。"""

    @property
    def model_name(self) -> str:
        """返回本地词项覆盖率重排实现的标识。"""
        return "memory-reranker"

    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """根据查询词在文件名与正文中的覆盖比例更新分数，稳定排序后按 top_k 选择返回项。"""
        if not results:
            return []

        query_tokens = _tokenize(lexical_query(query) or query)
        if not query_tokens:
            # query 无有效词（如纯标点）：保持原顺序、score 归零，避免除零
            return [
                r.with_score(0.0, "memory:query_term_recall")
                for r in results
            ][: top_k if top_k else len(results)]

        # 无关键词匹配时，根据本批原始检索分的相对位置生成低分。
        SEMANTIC_FLOOR_BASE = 0.001  # 映射区间起点。
        SEMANTIC_FLOOR_SPAN = 0.008  # 区间宽度，最大映射值为 0.009。

        sem_scores = [r.score for r in results]
        sem_max = max(sem_scores)
        sem_min = min(sem_scores)

        scored: list[tuple[float, RetrievalResult]] = []
        for r in results:
            text = f"{r.chunk_content or ''} {r.document_filename or ''}"
            text_tokens = _tokenize(text)
            if not text_tokens:
                kw_score = 0.0
            else:
                intersection = query_tokens & text_tokens
                kw_score = len(intersection) / len(query_tokens)

            if kw_score > 0:
                final_score = kw_score
            else:
                norm = (r.score - sem_min) / (sem_max - sem_min) if sem_max > sem_min else 0.0
                final_score = SEMANTIC_FLOOR_BASE + SEMANTIC_FLOOR_SPAN * norm

            scored.append((final_score, r.with_score(round(final_score, 6), "memory:query_term_recall+normalized_retrieval_floor")))

        scored.sort(key=lambda x: x[0], reverse=True)
        reranked = [r for _, r in scored]

        if top_k is not None and top_k > 0:
            reranked = reranked[:top_k]

        logger.debug(
            "memory_reranker_completed",
            query_length=len(query),
            input_count=len(results),
            output_count=len(reranked),
            top_score=reranked[0].score if reranked else 0.0,
        )

        return reranked
