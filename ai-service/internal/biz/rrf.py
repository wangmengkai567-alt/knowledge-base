"""倒数排名融合（RRF）：按各检索列表中的排名累计分数，合并同一分块。"""

from __future__ import annotations


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[int, float]]],
    k: int = 60,
) -> list[tuple[int, float]]:
    """按各路排名合并候选分块，返回按融合分降序排列的 (chunk_id, score) 列表。

    每路名次从 1 开始，单个分块的贡献为 1/(k+rank)，在多路出现时累加。
    k 为正数平滑参数，不表示返回条数；输入中的原始 score 不参与融合计算。
    """
    if k <= 0:
        raise ValueError(f"rrf k must be positive, got {k}")
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (chunk_id, _) in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
