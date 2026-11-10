"""
internal/biz/retrieval.py — 知识库检索业务逻辑

RetrievalBiz 负责：
- 规划查询、召回候选、补齐来源，并按配置重排和筛选结果。

"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from internal.biz.entity import RetrievalResult, RetrievedChunk, SourceScore
from internal.biz.ingestion_state import ingestion_status
from internal.biz.materials import select_materials
from internal.biz.query_plan import QueryPlan, plan_query
from internal.biz.rrf import reciprocal_rank_fusion
from pkg.errors.base import RAGError

logger = structlog.get_logger()

# 每个查询变体向检索器请求的候选分块数，按 top_k 的 4 倍计算，再限制在以下范围内。
# 这是进入后续重排和筛选的候选数量；最终结果仍按请求的 top_k 截取。
_RERANK_CANDIDATE_CAP = 64    # 请求候选数量的上限，限制每个查询变体的检索规模。
_RERANK_CANDIDATE_FLOOR = 32  # 请求候选数量的下限，避免 top_k 较小时只检索少量分块。
# 下限仅约束请求数量；符合条件的分块不足时，检索器实际返回的数量可以少于 32。

if TYPE_CHECKING:
    from internal.biz.repo import ChunkRepo, DocumentRepo, EmbeddingProvider, Reranker, Retriever


class RetrievalBiz:
    """统一检索业务：规划查询、召回候选、补全来源，再按配置重排与筛选。

    搜索列表可按文档去重，问答保留多个相关分块；底层检索策略由注入的 Retriever 决定。
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        retriever: Retriever,
        chunk_repo: ChunkRepo,
        document_repo: DocumentRepo,
        reranker: Reranker | None = None,
        rerank_enabled: bool = False,
        min_relevance: float = 0.0,
        query_expand_enabled: bool = False,
        max_query_variants: int = 3,
        rrf_k: int = 60,
    ) -> None:
        """配置召回、重排和查询扩展；min_relevance 为重排分阈值，rrf_k 为排名融合参数。"""
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._chunk_repo = chunk_repo
        self._document_repo = document_repo
        # Reranker 重排序：仅当开启且实例存在时启用。
        self._reranker = reranker
        if rerank_enabled and reranker is None:
            raise ValueError("rerank_enabled requires a reranker")
        self._rerank_enabled = rerank_enabled
        # 开启重排后，结合分数门槛和问题中的术语筛选资料，避免保留无关候选。
        self._min_relevance = min_relevance if self._rerank_enabled else 0.0
        self._query_expand_enabled = query_expand_enabled
        self._max_query_variants = max(1, max_query_variants)
        self._rrf_k = rrf_k

    async def retrieve_many(
        self, query: str, knowledge_base_ids: list[int] | tuple[int, ...], top_k: int
    ) -> list[RetrievalResult]:
        """问答和只读工具共用跨库协调；任何分支失败都停止其余分支。"""
        tasks = [asyncio.create_task(self.retrieve(
            query=query, knowledge_base_id=kid, top_k=top_k,
            apply_threshold=True, dedupe_by_document=False,
        )) for kid in dict.fromkeys(knowledge_base_ids)]
        try:
            batches = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        hits = [hit for batch in batches for hit in batch]
        # 只有同类型、同计算方式的分数可跨库比较，不能直接混排余弦分与 RRF 分。
        if len({(hit.score_type, hit.score_method) for hit in hits}) > 1:
            raise RAGError(reason="INCOMPARABLE_SCORES", message="不同类型的检索分不能混合排序")
        hits.sort(key=lambda hit: (hit.score is not None, hit.score), reverse=True)
        # 跨库比较先为每个有相关命中的授权库、再为不同文档保留候选；
        # 避免同库或同文档的多个分块占满全局 top_k；这里只分配各库已通过相关性筛选的结果。
        by_kb, by_doc, rest = [], [], []
        seen_kbs, seen_docs = set(), set()
        for hit in hits:
            key = (hit.knowledge_base_id, hit.document_id)
            if hit.knowledge_base_id not in seen_kbs:
                by_kb.append(hit)
            elif key not in seen_docs:
                by_doc.append(hit)
            else:
                rest.append(hit)
            seen_kbs.add(hit.knowledge_base_id)
            seen_docs.add(key)
        return [*by_kb, *by_doc, *rest][:top_k]

    async def retrieve(
        self,
        query: str,
        knowledge_base_id: int,
        top_k: int = 10,
        apply_threshold: bool = True,
        dedupe_by_document: bool = True,
    ) -> list[RetrievalResult]:
        """检索与 query 最相关的 chunks。

        流程：
        1. 规划 query：自然语言拆成语义变体 + BM25 主题词
        2. 并行嵌入变体并多路召回，RRF 融合
        3. 批量获取 chunk / document 详情
        4. 组装 RetrievalResult 列表
        5. 可选：按 document_id 去重（搜索列表每篇文档只展示一次；
            RAG 必须关闭，否则长文档其余相关块进不了 LLM）
        6. 可选重排序（Reranker，整条链路只在这里排一次）
        """
        if not query or not query.strip():
            logger.warning("empty_query_skip_retrieval")
            return []

        plan = plan_query(
            query,
            expand=self._query_expand_enabled,
            max_variants=self._max_query_variants,
        )
        if not plan.original:
            logger.warning("empty_query_skip_retrieval")
            return []

        # 各路召回在 top-k 前限定就绪文档，避免保留的旧索引耗尽候选。
        documents = await self._document_repo.list_by_kb(knowledge_base_id)
        document_ids = [
            doc.id for doc in documents
            if doc.id is not None and ingestion_status(doc.stages) == "ready"
        ]
        if not document_ids:
            return []

        filter_metadata = {"knowledge_base_id": knowledge_base_id}
        # 根据查询计划执行检索，返回分块 ID 和各路检索分数。
        raw_results = await self._multi_retrieve(
            plan=plan,
            top_k=top_k,
            filter_metadata=filter_metadata,
            document_ids=document_ids,
        )

        if not raw_results:
            logger.info("no_retrieval_results", knowledge_base_id=knowledge_base_id)
            return []

        # 3. 批量获取 chunk 详情
        chunk_ids = [item.chunk_id for item in raw_results]
        chunks = await self._chunk_repo.get_by_ids(chunk_ids)
        # 按分块 ID 建立索引，供后面组装正文和来源信息。
        chunk_map = {c.id: c for c in chunks}

        # 4. 批量获取 document 详情
        doc_ids = list({c.document_id for c in chunks})
        documents = await self._document_repo.get_by_ids(doc_ids)
        doc_map = {d.id: d for d in documents}

        # 5. 组装候选结果（不过早截断，保留全部候选供 Reranker 重排序）
        candidates: list[RetrievalResult] = []
        for raw in raw_results:
            chunk_id, score = raw.chunk_id, raw.score
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                continue

            doc = doc_map.get(chunk.document_id)
            if doc is None or ingestion_status(doc.stages) != "ready":
                continue

            # 根据数据库中的文档归属再次核对知识库 ID，不仅依赖索引 metadata。
            if doc.knowledge_base_id != knowledge_base_id:
                continue

            candidates.append(RetrievalResult(
                chunk_id=chunk_id,
                score=score,
                score_type=raw.score_type,
                score_method=raw.score_method,
                scores=raw.scores,
                chunk_content=chunk.content,
                chunk_position=chunk.position,
                document_id=doc.id,
                document_filename=doc.filename,
                knowledge_base_id=doc.knowledge_base_id,
                document_updated_at=doc.updated_at or doc.created_at,
            ))

        # 搜索列表每篇文档保留最高分分块；问答调用关闭此选项，保留同文档的多个相关分块。
        if dedupe_by_document:
            doc_best: dict[int, RetrievalResult] = {}
            for r in candidates:
                prev = doc_best.get(r.document_id)
                if prev is None or r.score > prev.score:
                    doc_best[r.document_id] = r
            candidates = sorted(doc_best.values(), key=lambda x: x.score, reverse=True)

        # 6. 开启重排时，为当前候选逐项计算问题相关性分数。
        # top_k=None 表示此处不截取前 top_k 项：先执行下方筛选，再从保留结果中取 top_k 项。
        if self._rerank_enabled and candidates:
            candidates = await self._maybe_rerank(
                plan.rerank_query or plan.original,
                candidates,
                top_k=None,
            )

        # 7. 根据重排分数和问题关键词筛选结果，具体规则由 select_materials 调用 apply_relevance_gate 执行。
        # 关键词由 query_english_terms 从问题中提取（英文或数字，去除停用词）；文件名和分块正文均参与匹配。
        # 问题没有这类关键词：仅保留分数 >= min_relevance 的结果。
        # 问题有这类关键词：分数 >= min_relevance 时至少匹配一个词；分数低于阈值时须匹配全部词。
        # 未满足对应条件的结果被移除；apply_threshold=False 或阈值为 0 时不执行此筛选。
        if apply_threshold and self._min_relevance > 0:
            logger.info(
                "retrieval_candidates_before_gate",
                knowledge_base_id=knowledge_base_id,
                count=len(candidates),
                files=[item.document_filename for item in candidates[:20]],
            )
            candidates = select_materials(
                plan.original,
                candidates,
                self._min_relevance,
            )

        results = candidates[:top_k]

        logger.info(
            "retrieval_completed",
            knowledge_base_id=knowledge_base_id,
            query_length=len(query),
            reranked=self._rerank_enabled,
            dedupe_by_document=dedupe_by_document,
            result_count=len(results),
            query_variants=len(plan.variants),
            lexical=plan.lexical,
        )

        return results

    async def _multi_retrieve(
        self,
        plan: QueryPlan,
        top_k: int,
        filter_metadata: dict,
        document_ids: list[int],
    ) -> list[RetrievedChunk]:
        """对查询变体做 embedding，并行检索，再用 RRF 融合。"""
        embed_result = await self._embedding_provider.embed(plan.embed_texts)
        if len(embed_result.vectors) != len(plan.embed_texts):
            raise ValueError("embedding response count does not match query variants")

        # 例如 top_k=5 或 8 时请求 32 个候选，top_k=10 时请求 40 个候选。
        # 该数量分别应用于每个查询变体；多个变体融合后的候选总数可以超过 64。
        branch_k = min(_RERANK_CANDIDATE_CAP, max(top_k * 4, _RERANK_CANDIDATE_FLOOR))
        tasks = [
            self._retriever.retrieve(
                query_vector=vector,
                top_k=branch_k,
                filter_metadata=filter_metadata,
                document_ids=document_ids,
                query_text=text,
                keyword_query=plan.lexical,
            )
            for text, vector in zip(plan.embed_texts, embed_result.vectors, strict=True)
        ]
        ranked_lists = await asyncio.gather(*tasks)

        if len(ranked_lists) == 1:
            return ranked_lists[0]

        # 多个查询变体按名次融合；原始各路分数保留在 scores 中供追溯。
        fused = reciprocal_rank_fusion(
            [[(item.chunk_id, item.score) for item in ranked] for ranked in ranked_lists],
            k=self._rrf_k,
        )
        logger.info(
            "parallel_query_fused",
            variant_count=len(ranked_lists),
            fused_count=len(fused),
        )
        method = f"query_variant_rrf(k={self._rrf_k}; rank starts at 1)"
        traces: dict[int, list[SourceScore]] = {}
        for index, ranked in enumerate(ranked_lists):
            for item in ranked:
                traces.setdefault(item.chunk_id, []).extend(
                    SourceScore(score.kind, score.value, f"query[{index}]:{score.method}")
                    for score in item.scores
                )
        return [RetrievedChunk(cid, score, "rrf", method,
                               [*traces[cid], SourceScore("rrf", score, method)])
                for cid, score in fused]

    async def _maybe_rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """重排失败直接向调用方报告，不混用原始候选与重排分。"""
        return await self._reranker.rerank(query=query, results=candidates, top_k=top_k)
