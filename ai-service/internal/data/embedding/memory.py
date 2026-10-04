"""
internal/data/embedding/memory.py — 内存 Embedding 实现

生成确定性伪向量，无需外部 API 依赖。
相同文本始终生成相同向量，方便调试和测试。

改为**词袋（Bag-of-Words）确定性向量**：分词 → 词频累加到哈希桶 → L2 归一化。
这样含相同关键词的文本向量余弦相似度高（语义检索可用），且英文统一小写、
中文按单字，天然大小写无关。
"""

from __future__ import annotations

import hashlib
import math
import re
import structlog
from collections import defaultdict

from internal.biz.entity import EmbeddingResult, EmbeddingUsage
from internal.biz.repo import EmbeddingProvider

logger = structlog.get_logger()

# 英文/数字词 + 中文单字（与 BM25 分词口径保持一致，便于大小写无关召回）
_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list[str]:
    """分词：英文转小写、中文按单字，忽略标点。"""
    if not text:
        return []
    tokens: list[str] = []
    for m in _TOKEN_PATTERN.findall(text):
        tokens.append(m.lower())
    return tokens


class MemoryEmbedding(EmbeddingProvider):
    """内存 Embedding — 词袋（BoW）确定性伪向量。

    无需 API Key；相同文本生成相同向量。
    向量 = 词频在固定维度桶上的分布（L2 归一化），相同/相似文本 → 相近向量。
    """

    def __init__(self, dimension: int = 256) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return "memory-embedding"

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """基于词袋生成确定性向量。

        无外部 API 依赖，usage 基于字符数估算（约 4 字符/token）。
        """
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model=self.model_name,
                dimension=self._dimension,
                usage=EmbeddingUsage(),
            )

        vectors: list[list[float]] = []
        for text in texts:
            vector = self._bow_vector(text, self._dimension)
            vectors.append(vector)

        # usage 估算：总字符数 / 4（中英文混合近似值）
        estimated_tokens = max(1, sum(len(t) for t in texts) // 4)

        logger.debug(
            "memory_embedding_success",
            batch_size=len(texts),
            dimension=self._dimension,
            estimated_tokens=estimated_tokens,
        )
        return EmbeddingResult(
            vectors=vectors,
            model=self.model_name,
            dimension=self._dimension,
            usage=EmbeddingUsage(
                prompt_tokens=estimated_tokens,
                total_tokens=estimated_tokens,
            ),
        )

    @staticmethod
    def _bow_vector(text: str, dimension: int) -> list[float]:
        """词袋向量：token → MD5 哈希映射到 [0, dimension) 桶，累加词频，L2 归一化。"""
        counts: dict[int, float] = defaultdict(float)
        for tok in _tokenize(text):
            h = hashlib.md5(tok.encode("utf-8")).digest()
            bucket = int.from_bytes(h[:4], "big") % dimension
            counts[bucket] += 1.0

        raw = [0.0] * dimension
        for bucket, freq in counts.items():
            raw[bucket] = freq

        # L2 归一化到单位球面，便于余弦相似度比较
        norm = math.sqrt(sum(v * v for v in raw))
        if norm > 0:
            raw = [v / norm for v in raw]
        return raw

