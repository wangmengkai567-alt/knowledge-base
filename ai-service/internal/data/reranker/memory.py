"""
internal/data/reranker/memory.py — 内存重排序器实现

基于关键词重叠度（Keyword Overlap）打分，无需外部 API 依赖。

打分逻辑：
1. 对 query 分词：英文按单词；中文按连续二字（bigram），单字查询才退回单字。
   若按中文单字打分，query="微服务架构" 会变成 {微,服,务,架,构}，
   任何带「服务」的文档（FastAPI、systemd）都能拿到 40%+，把无关结果顶上来。
2. 对每个 chunk 同时取「正文 chunk_content」+「文档标题/文件名 document_filename」分词，
   计算查询词覆盖率(recall) = |query ∩ (正文∪标题)| / |query|。
   英文词独立切分，保证 query="agent" 能命中标题 "Agent进入深水区后的十大趋势"。
3. 字面无关键词命中时给极低的地板分：用归一化语义分映射到固定区间
   [SEMANTIC_FLOOR_BASE, SEMANTIC_FLOOR_BASE+SEMANTIC_FLOOR_SPAN] = [0.001, 0.009]
   （恒定低于检索阈值 min_relevance，默认 0.5）。
   这样既保留 RAG 链路的语义相对排序（问句关键词稀疏时回退到语义序），
   又确保无关文档仍低于全局阈值、被搜索链路过滤。
4. 按 score 降序排序（同分稳定，保留原 RRF 顺序作为区分度）
"""

from __future__ import annotations

import re
from dataclasses import replace

import structlog

from internal.biz.entity import RetrievalResult
from internal.biz.query_plan import lexical_query
from internal.biz.repo import Reranker

logger = structlog.get_logger()

# 英文/数字按整词切；中文按连续字串切，再在打分时拆成二字。
_EN_WORD = re.compile(r"[a-zA-Z0-9]+")
_ZH_RUN = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> set[str]:
    """将文本分词并转为小写词集。

    - 英文/数字：整词（"agent" 可命中 "Agent进入…" 标题）
    - 中文：连续二字。单字查询才保留单字，避免「服/务」这种常见字误伤。
    """
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
    """内存重排序器 — 基于查询词覆盖率（recall）打分。

    无需外部 API。
    相同 query + content 始终产生相同 score，便于调试。
    score 取值 0~1。
    """

    @property
    def model_name(self) -> str:
        return "memory-reranker"

    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """基于查询词覆盖率（recall）+ 语义托底重排序。"""
        if not results:
            return []

        query_tokens = _tokenize(lexical_query(query) or query)
        if not query_tokens:
            # query 无有效词（如纯标点）：保持原顺序、score 归零，避免除零
            return [
                replace(r, score=0.0)
                for r in results
            ][: top_k if top_k else len(results)]

        # 字面无关键词命中时的地板分：用「按 RRF 归一化的原始语义分」映射到
        # [0.001, 0.009]，恒定低于检索阈值 min_relevance。
        SEMANTIC_FLOOR_BASE = 0.001
        SEMANTIC_FLOOR_SPAN = 0.008

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

            scored.append((final_score, replace(r, score=round(final_score, 6))))

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
