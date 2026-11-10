"""进程内 BM25 关键词索引：保存分块词频、长度和来源元数据。

搜索先限制文档及元数据范围，再计算分数并返回前 top_k 项。
索引不持久化，进程启动时由入库编排重建。
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
    """将中文按单字、英文和数字按连续词串切分，英文转小写，忽略标点。"""
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
    """保存分块词频、长度和元数据的进程内索引。

    写入和删除持有 _lock；search 不挂起协程，同一事件循环中的写协程无法在其读取中途进入。
    若在读取过程中增加 await，必须同步调整读写互斥。
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        """初始化空索引及 BM25 评分参数。

        k1 控制词频增加时评分的饱和程度；b 控制文档长度归一化的影响。
        这里的文档长度是 _tokenize 产生的词项数量，不是模型 token 用量。
        """
        self._k1 = k1
        self._b = b
        # 词项 → {chunk_id: 词频}
        self._inverted: dict[str, dict[int, int]] = defaultdict(dict)
        # 分块 ID → 分词后的词项数量；只记录非空分块，不是模型 token 用量。
        self._doc_len: dict[int, int] = {}
        # chunk_id → metadata（与 _doc_len 同步：仅非空文档）
        self._metadata: dict[int, dict] = {}
        # 增量维护总长度，避免 search 时 O(N) 求和
        self._total_doc_len: int = 0
        # 用于写操作互斥
        self._lock = asyncio.Lock()

    @property
    def _avg_doc_len(self) -> float:
        # O(1) 计算，依赖 _total_doc_len 与 _doc_len 在 index/remove 同步维护。
        n = len(self._doc_len)
        return self._total_doc_len / n if n else 0.0

    @property
    def _num_docs(self) -> int:
        """返回当前关键词索引中的分块数量；BM25 将每个分块视为一个检索文档。"""
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
                    # 空分块不写入词频、长度或元数据，也不参与平均长度计算。
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
                self._total_doc_len += dl  # 增量维护
                if metadatas is not None:
                    self._metadata[chunk_id] = metadatas[i]

        logger.debug(
            "bm25_indexed",
            chunk_count=len(chunk_ids),
            total_docs=self._num_docs,
            avg_doc_len=round(self._avg_doc_len, 2),
        )

    async def existing_ids(self, chunk_ids: list[int], document_id: int, knowledge_base_id: int) -> set[int]:
        """返回已建关键词索引且元数据匹配指定文档、知识库的分块 ID。"""
        return {cid for cid in chunk_ids if cid in self._doc_len
                and self._metadata.get(cid, {}).get("document_id") == document_id
                and self._metadata.get(cid, {}).get("knowledge_base_id") == knowledge_base_id}

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
        *,
        document_ids: list[int] | None = None,
    ) -> list[tuple[int, float]]:
        """对匹配查询词的分块计算 BM25 分数，返回前 top_k 个 (chunk_id, score)。

        document_ids 和 filter_metadata 在评分前限制候选范围；空查询或无匹配时返回空列表。
        重复查询词只计一次贡献，分数使用本索引的词频与文档长度统计。
        """
        if not query or not query.strip():
            return []
        if self._num_docs == 0:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # 每个查询词在本次评分中只贡献一次，避免重复词增加权重。
        unique_terms = list(set(query_tokens))

        # 候选文档集：所有出现过任一 query term 的文档
        candidates: set[int] = set()
        for term in unique_terms:
            postings = self._inverted.get(term)
            if postings:
                candidates.update(postings.keys())

        if not candidates:
            return []

        if document_ids is not None:
            allowed_documents = set(document_ids)
            candidates = {
                cid for cid in candidates
                if self._metadata.get(cid, {}).get("document_id") in allowed_documents
            }
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
