"""倒数排名融合（RRF）：不依赖分数量纲，按排名合并多路检索结果。"""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[int, float]]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """按排名融合 N 路 (chunk_id, score) 列表，忽略原始分数。

    score(d) = Σ 1 / (k + rank_i(d))，rank 从 1 起。
    各路可有重叠；某路未出现的文档在该路贡献为 0。
    """
    if k <= 0:
        raise ValueError(f"rrf k must be positive, got {k}")
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (chunk_id, _) in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
