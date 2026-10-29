"""
internal/biz/retrieval.py — 向量检索业务逻辑

RetrievalBiz 负责：
- retrieve：query → embed → search → enrich → 返回检索结果

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

# 重排/闸门前至少留够候选。过小的融合截断会让「stdin/stderr」这类
# 在多篇笔记里都出现的术语，把真正带编号的段落挤出重排窗口。
_RERANK_CANDIDATE_CAP = 64
_RERANK_CANDIDATE_FLOOR = 32

if TYPE_CHECKING:
    from internal.biz.repo import ChunkRepo, DocumentRepo, EmbeddingProvider, Reranker, Retriever


class RetrievalBiz:
    """向量检索业务逻辑 — 不感知协议、不感知存储。

    注意：检索结果在返回前会经过可选的 Reranker 重排序。
    此前 Reranker 只接在 RAG 对话链路，搜索（retrieve）接口完全没用上，
    导致纯向量语义检索对短关键词（如 "docker"）召回不相关文档排在前。
    把 Reranker 接入检索链路后，关键词精确命中会被重排序提到最前。
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
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._chunk_repo = chunk_repo
        self._document_repo = document_repo
        # Reranker 重排序：仅当开启且实例存在时启用。
        self._reranker = reranker
        if rerank_enabled and reranker is None:
            raise ValueError("rerank_enabled requires a reranker")
        self._rerank_enabled = rerank_enabled
        # 最低相关度门槛：重排后分数 < 该值的结果被丢弃（仅重排开启时生效）。
        # 防止跨知识库扇出时把各库"最不差"的无关 chunk 也展示出来。
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
        if len({(hit.score_type, hit.score_method) for hit in hits}) > 1:
            raise RAGError(reason="INCOMPARABLE_SCORES", message="不同类型的检索分不能混合排序")
        hits.sort(key=lambda hit: (hit.score is not None, hit.score), reverse=True)
        return hits[:top_k]

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

        filter_metadata = {"knowledge_base_id": knowledge_base_id}
        raw_results = await self._multi_retrieve(
            plan=plan,
            top_k=top_k,
            filter_metadata=filter_metadata,
        )

        if not raw_results:
            logger.info("no_retrieval_results", knowledge_base_id=knowledge_base_id)
            return []

        # 3. 批量获取 chunk 详情
        chunk_ids = [item.chunk_id for item in raw_results]
        chunks = await self._chunk_repo.get_by_ids(chunk_ids)
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

            # 过滤：只返回指定知识库内的结果
            # defense-in-depth——存储层已按 metadata 过滤，
            # 这里保留作为二道保险：历史写入时未带 kb_id 的 chunk（旧数据）
            # 可能绕过存储层过滤，在此统一拦截。
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

        # 5.5 文档级去重（仅搜索列表）。
        # 检索命中以 chunk 为单位，同一文档常被多个 chunk 命中。
        # 搜索结果需要每篇文档只展示一条；RAG 必须保留多 chunk，否则
        # 长文档里排第二的相关段落进不了 context。
        if dedupe_by_document:
            doc_best: dict[int, RetrievalResult] = {}
            for r in candidates:
                prev = doc_best.get(r.document_id)
                if prev is None or r.score > prev.score:
                    doc_best[r.document_id] = r
            candidates = sorted(doc_best.values(), key=lambda x: x.score, reverse=True)

        # 6. 可选重排序：先给全部候选打分，再由门槛/术语闸门截断。
        # 若这里按请求 top_k 截断，灰区分数的对题块会在进闸门前就被丢掉。
        if self._rerank_enabled and candidates:
            candidates = await self._maybe_rerank(
                plan.rerank_query or plan.original,
                candidates,
                top_k=None,
            )

        # 6.5 相关性闸门：重排分过线，或问句英文术语全在笔记里。
        # 过线但术语一个都碰不到的邻居丢掉，避免无关最近邻进生成。
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
    ) -> list[RetrievedChunk]:
        """对查询变体做 embedding，并行检索，再用 RRF 融合。"""
        embed_result = await self._embedding_provider.embed(plan.embed_texts)
        if len(embed_result.vectors) != len(plan.embed_texts):
            raise ValueError("embedding response count does not match query variants")

        branch_k = min(_RERANK_CANDIDATE_CAP, max(top_k * 4, _RERANK_CANDIDATE_FLOOR))
        tasks = [
            self._retriever.retrieve(
                query_vector=vector,
                top_k=branch_k,
                filter_metadata=filter_metadata,
                query_text=text,
                keyword_query=plan.lexical,
            )
            for text, vector in zip(plan.embed_texts, embed_result.vectors, strict=True)
        ]
        ranked_lists = await asyncio.gather(*tasks)

        if len(ranked_lists) == 1:
            return ranked_lists[0]

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
