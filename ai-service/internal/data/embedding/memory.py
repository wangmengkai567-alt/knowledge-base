"""将文本词频映射到固定向量桶并归一化，供离线演示使用。

相同输入生成相同向量，不调用嵌入模型，也不提供模型语义理解。
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

# 英文和数字按连续词串提取，中文按单字提取；英文统一转小写。
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
    """将词频累加到固定维度的哈希桶并做 L2 归一化，生成确定性本地向量。"""

    def __init__(self, dimension: int = 256) -> None:
        """设置本地哈希向量的分量数量，不加载外部嵌入模型。"""
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        """返回本地哈希向量的固定分量数量。"""
        return self._dimension

    @property
    def model_name(self) -> str:
        """返回本地实现的固定标识，供日志和配置核对使用。"""
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

