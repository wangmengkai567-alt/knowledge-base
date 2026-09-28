"""
internal/data/keyword/bm25.py — BM25 关键词索引（内存实现）

零外部依赖的 BM25 实现，用于混合检索。
- 中英文混合分词：中文按字符切分，英文按单词切分（正则匹配）
- 参数：k1=1.5, b=0.75（业界经验值）
- 倒排索引结构：term → {chunk_id → term_freq}
- 支持 metadata 过滤（如 knowledge_base_id），与 VectorStore 语义一致

内存实现的定位：
- 与 MemoryVectorStore 对称，进程内倒排，零外部依赖
- 索引在进程内维护，重启后需要重建（EmbeddingBiz 已通过分块流程保证）
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict

import structlog

from internal.biz.repo import KeywordStore

logger = structlog.get_logger()

# 中文单字 或 英文/数字词
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fa5]|[a-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    """中英文混合分词。

    - 中文：按单字切分（BM25 不依赖分词质量，字符级足以召回）
    - 英文/数字：按连续字母数字切分，转小写
    - 忽略标点与其他符号
    """
    if not text:
        return []
    tokens: list[str] = []
    for m in _TOKEN_PATTERN.findall(text):
        if m.isascii():
            tokens.append(m.lower())
        else:
            tokens.append(m)
    return tokens


class BM25KeywordStore(KeywordStore):
    """BM25 内存关键词索引。

    并发安全：通过 asyncio.Lock 序列化写操作。
    search 无需加锁——安全依赖于“asyncio 单线程 + search 内部本身无 await”，
    使得 search 执行不会被写协程抓断（GIL 仅保证单次读写原子，不保护迭代）。
    INVARIANT: search 及其内部方法不得引入 await——否则需同时加读锁。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        # 词项 → {chunk_id: 词频}
        self._inverted: dict[str, dict[int, int]] = defaultdict(dict)
        # chunk_id → 文档长度（token 数）；仅包含非空文档
        self._doc_len: dict[int, int] = {}
        # chunk_id → metadata（与 _doc_len 同步：仅非空文档）
        self._metadata: dict[int, dict] = {}
        # Sprint 18-fix (P1)：增量维护总长度，避免 search 时 O(N) 求和
        self._total_doc_len: int = 0
        # 用于写操作互斥
        self._lock = asyncio.Lock()

    @property
    def _avg_doc_len(self) -> float:
        # Sprint 18-fix (P1)：O(1) 计算，依赖 _total_doc_len 与 _doc_len 在 index/remove 同步维护。
        n = len(self._doc_len)
        return self._total_doc_len / n if n else 0.0

    @property
    def _num_docs(self) -> int:
        return len(self._doc_len)

    async def index(
        self,
        chunk_ids: list[int],
        texts: list[str],
        metadatas: list[dict] | None = None,
    ) -> None:
        """建立/更新分块的关键词索引（upsert 语义）。"""
        if len(chunk_ids) != len(texts):
            raise ValueError(
                f"chunk_ids and texts length mismatch: {len(chunk_ids)} vs {len(texts)}"
            )
        if metadatas is not None and len(metadatas) != len(chunk_ids):
            raise ValueError(
                f"metadatas length mismatch: {len(metadatas)} vs {len(chunk_ids)}"
            )

        async with self._lock:
            # 先移除旧索引（upsert 需要）
            self._remove_locked(chunk_ids)

            for i, chunk_id in enumerate(chunk_ids):
                tokens = _tokenize(texts[i])
                if not tokens:
                    # Sprint 18-fix (P1)：空文档不入索引也不占位。
                    # 旧实现将 doc_len[cid]=0 写入，会拉低 avg_dl，
                    # 导致非空文档的 BM25 长度惩罚项被放大、得分偏低。
                    # delete 已是 pop(cid, None) 幂等，无需占位保证。
                    continue

                # 词频统计
                tf: dict[str, int] = defaultdict(int)
                for tok in tokens:
                    tf[tok] += 1

                # 写入倒排索引
                for term, freq in tf.items():
                    self._inverted[term][chunk_id] = freq

                dl = len(tokens)
                self._doc_len[chunk_id] = dl
                self._total_doc_len += dl  # Sprint 18-fix (P1)：增量维护
                if metadatas is not None:
                    self._metadata[chunk_id] = metadatas[i]

        logger.debug(
            "bm25_indexed",
            chunk_count=len(chunk_ids),
            total_docs=self._num_docs,
            avg_doc_len=round(self._avg_doc_len, 2),
        )

    async def delete(self, chunk_ids: list[int]) -> None:
        """删除指定分块的关键词索引。"""
        if not chunk_ids:
            return
        async with self._lock:
            self._remove_locked(chunk_ids)
        logger.debug("bm25_deleted", chunk_count=len(chunk_ids))

    def _remove_locked(self, chunk_ids: list[int]) -> None:
        """从索引中移除指定分块（调用方需持有 _lock）。"""
        target = set(chunk_ids)
        # 从倒排索引中移除
        empty_terms: list[str] = []
        for term, postings in self._inverted.items():
            for cid in target:
                postings.pop(cid, None)
            if not postings:
                empty_terms.append(term)
        for term in empty_terms:
            del self._inverted[term]
        # 清理长度与元数据
        for cid in target:
            old_dl = self._doc_len.pop(cid, None)
            if old_dl is not None:
                self._total_doc_len -= old_dl
            self._metadata.pop(cid, None)

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filter_metadata: dict | None = None,
    ) -> list[tuple[int, float]]:
        """BM25 相关性搜索。

        BM25 公式：
            score(D, Q) = Σ IDF(q) × (tf × (k1 + 1)) / (tf + k1 × (1 - b + b × |D| / avgdl))
            IDF(q) = log((N - df + 0.5) / (df + 0.5) + 1)   # Okapi BM25 平滑
        """
        if not query or not query.strip():
            return []
        if self._num_docs == 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # 去重（BM25 通常对同一 term 只计一次贡献）
        unique_terms = list(set(query_tokens))

        # 候选文档集：所有出现过任一 query term 的文档
        candidates: set[int] = set()
        for term in unique_terms:
            postings = self._inverted.get(term)
            if postings:
                candidates.update(postings.keys())

        if not candidates:
            return []

        # metadata 过滤
        if filter_metadata:
            candidates = {
                cid for cid in candidates
                if self._match_metadata(cid, filter_metadata)
            }
            if not candidates:
                return []

        avg_dl = self._avg_doc_len
        n_docs = self._num_docs
        scores: dict[int, float] = defaultdict(float)

        for term in unique_terms:
            postings = self._inverted.get(term)
            if not postings:
                continue
            df = len(postings)
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
            for cid, tf in postings.items():
                if cid not in candidates:
                    continue
                dl = self._doc_len.get(cid, 0)
                denom = tf + self._k1 * (1 - self._b + self._b * (dl / avg_dl if avg_dl else 0))
                if denom <= 0:
                    continue
                scores[cid] += idf * (tf * (self._k1 + 1)) / denom

        if not scores:
            return []

        # 按分数降序 + 截断 top_k
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        logger.debug(
            "bm25_search",
            query_terms=len(unique_terms),
            candidate_count=len(candidates),
            result_count=len(ranked),
        )
        return ranked

    def _match_metadata(self, chunk_id: int, filter_metadata: dict) -> bool:
        """判断分块元数据是否满足过滤条件（全等匹配）。"""
        meta = self._metadata.get(chunk_id)
        if meta is None:
            # 索引时未提供 metadata，无法过滤（视为不匹配，语义与 VectorStore 一致）
            return False
        for k, v in filter_metadata.items():
            if meta.get(k) != v:
                return False
        return True
