"""
internal/biz/retrieval.py — 向量检索业务逻辑

RetrievalBiz 负责：
- retrieve：query → embed → search → enrich → 返回检索结果

不感知 HTTP（不 import fastapi）、不感知存储（不 import sqlalchemy）。
"""

from __future__ import annotations

import asyncio

import structlog

from internal.biz.entity import RetrievalResult
from internal.biz.query_plan import QueryPlan, distinctive_terms, plan_query
from internal.biz.repo import ChunkRepo, DocumentRepo, EmbeddingProvider, Reranker, Retriever
from internal.biz.rrf import reciprocal_rank_fusion

logger = structlog.get_logger()


def _mentions_any(result: RetrievalResult, terms: tuple[str, ...]) -> bool:
    """命中文件名或正文里出现过的对译词，用于重排门槛误杀后的召回。"""
    blob = f"{result.document_filename}\n{result.chunk_content}".casefold()
    for term in terms:
        token = (term or "").strip().casefold()
        if token and token in blob:
            return True
    return False


def _filename_mentions_any(result: RetrievalResult, terms: tuple[str, ...]) -> bool:
    name = (result.document_filename or "").casefold()
    for term in terms:
        token = (term or "").strip().casefold()
        if token and token in name:
            return True
    return False



class RetrievalBiz:
    """向量检索业务逻辑 — 不感知协议、不感知存储。

    注意：检索结果在返回前会经过可选的 Reranker 重排序（Sprint 18 修复）。
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
        rerank_top_k: int = 5,
        min_relevance: float = 0.0,
        query_expand_enabled: bool = False,
        max_query_variants: int = 3,
        rrf_k: int = 60,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._chunk_repo = chunk_repo
        self._document_repo = document_repo
        # Reranker 重排序（Sprint 18 修复）：仅当开启且实例存在时启用。
        # 与 RAGBiz 一致——Reranker 是增强组件，不应成为检索单点故障。
        self._reranker = reranker
        self._rerank_enabled = rerank_enabled and reranker is not None
        self._rerank_top_k = rerank_top_k
        # 最低相关度门槛：重排后分数 < 该值的结果被丢弃（仅重排开启时生效）。
        # 防止跨知识库扇出时把各库"最不差"的无关 chunk 也展示出来。
        self._min_relevance = min_relevance if self._rerank_enabled else 0.0
        self._query_expand_enabled = query_expand_enabled
        self._max_query_variants = max(1, max_query_variants)
        self._rrf_k = rrf_k

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
        chunk_ids = [chunk_id for chunk_id, _ in raw_results]
        chunks = await self._chunk_repo.get_by_ids(chunk_ids)
        chunk_map = {c.id: c for c in chunks}

        # 4. 批量获取 document 详情
        doc_ids = list({c.document_id for c in chunks})
        documents = await self._document_repo.get_by_ids(doc_ids)
        doc_map = {d.id: d for d in documents}

        # 5. 组装候选结果（不过早截断，保留全部候选供 Reranker 重排序）
        candidates: list[RetrievalResult] = []
        for chunk_id, score in raw_results:
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                continue

            doc = doc_map.get(chunk.document_id)
            if doc is None:
                continue

            # 过滤：只返回指定知识库内的结果
            # Sprint 18-fix (P0)：defense-in-depth——存储层已按 metadata 过滤，
            # 这里保留作为二道保险：历史写入时未带 kb_id 的 chunk（旧数据）
            # 可能绕过存储层过滤，在此统一拦截。
            if doc.knowledge_base_id != knowledge_base_id:
                continue

            candidates.append(RetrievalResult(
                chunk_id=chunk_id,
                score=score,
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

        # 6. 可选重排序：整条 RAG/搜索链路只在此处排一次。
        # 重排查询带上中英对译词，避免「沙箱」对英文 sandbox 正文被打成低分。
        if self._rerank_enabled and candidates:
            candidates = await self._maybe_rerank(
                plan.rerank_query or plan.original,
                candidates,
            )

        # 6.5 相关性门槛：重排模型给出的 0~1 分数低于 min_relevance 则丢弃。
        # apply_threshold=False 时跳过。RAG 默认开启，避免把噪声送给生成模型。
        if apply_threshold and self._min_relevance > 0:
            before = len(candidates)
            kept = [r for r in candidates if r.score >= self._min_relevance]
            anchor_terms = distinctive_terms(plan)
            if not kept and candidates and anchor_terms:
                by_name = [r for r in candidates if _filename_mentions_any(r, anchor_terms)]
                if by_name:
                    kept = by_name
                else:
                    floor = min(self._min_relevance, 0.35)
                    kept = [
                        r
                        for r in candidates
                        if r.score >= floor and _mentions_any(r, anchor_terms)
                    ]
                if kept:
                    logger.info(
                        "relevance_threshold_rescued_terms",
                        min_relevance=self._min_relevance,
                        before=before,
                        rescued=len(kept),
                        anchor_terms=list(anchor_terms),
                    )
            candidates = kept
            logger.info(
                "relevance_threshold_filtered",
                min_relevance=self._min_relevance,
                before=before,
                after=len(candidates),
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
            synonym_terms=list(plan.synonym_terms),
        )

        return results

    async def hydrate_chunk_ids(
        self,
        chunk_ids: list[int],
        knowledge_base_id: int,
        allowed_kb_ids: list[int] | None = None,
    ) -> list[RetrievalResult]:
        """按调用方给出的顺序，把已检索的 chunk id 还原成 RetrievalResult。

        只收 allowed 知识库内的分块，丢弃缺失或越权 id。不再做向量检索。
        """
        allowed = set(allowed_kb_ids) if allowed_kb_ids else {knowledge_base_id}
        ordered: list[int] = []
        seen: set[int] = set()
        for chunk_id in chunk_ids:
            if chunk_id <= 0 or chunk_id in seen:
                continue
            seen.add(chunk_id)
            ordered.append(chunk_id)
        if not ordered:
            return []

        chunks = await self._chunk_repo.get_by_ids(ordered)
        chunk_map = {c.id: c for c in chunks if c.id is not None}
        documents = await self._document_repo.get_by_ids(
            list({c.document_id for c in chunks})
        )
        doc_map = {d.id: d for d in documents}

        results: list[RetrievalResult] = []
        for chunk_id in ordered:
            chunk = chunk_map.get(chunk_id)
            if chunk is None:
                continue
            doc = doc_map.get(chunk.document_id)
            if doc is None or doc.knowledge_base_id not in allowed:
                continue
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    score=0.0,
                    chunk_content=chunk.content,
                    chunk_position=chunk.position,
                    document_id=doc.id or 0,
                    document_filename=doc.filename,
                    knowledge_base_id=doc.knowledge_base_id,
                    document_updated_at=doc.updated_at or doc.created_at,
                )
            )
        return results

    async def _multi_retrieve(
        self,
        plan: QueryPlan,
        top_k: int,
        filter_metadata: dict,
    ) -> list[tuple[int, float]]:
        """对查询变体做 embedding，并行检索，再用 RRF 融合。"""
        embed_result = await self._embedding_provider.embed(plan.embed_texts)
        if not embed_result.vectors:
            logger.warning("empty_query_embedding", query_length=len(plan.original))
            return []

        branch_k = top_k * 2
        tasks = [
            self._retriever.retrieve(
                query_vector=vector,
                top_k=branch_k,
                filter_metadata=filter_metadata,
                query_text=text,
                keyword_query=plan.lexical,
            )
            for text, vector in zip(plan.embed_texts, embed_result.vectors)
        ]
        branch_results = await asyncio.gather(*tasks, return_exceptions=True)

        ranked_lists: list[list[tuple[int, float]]] = []
        for index, item in enumerate(branch_results):
            if isinstance(item, BaseException):
                if index == 0:
                    raise item
                logger.warning(
                    "query_variant_retrieve_failed",
                    variant=plan.embed_texts[index],
                    error=str(item),
                )
                continue
            ranked_lists.append(item)

        if len(ranked_lists) == 1:
            return ranked_lists[0]

        fused = reciprocal_rank_fusion(ranked_lists, k=self._rrf_k)
        logger.info(
            "parallel_query_fused",
            variant_count=len(ranked_lists),
            fused_count=len(fused),
        )
        return fused

    async def _maybe_rerank(
        self,
        query: str,
        candidates: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """调用重排序模型给全部候选打分。失败时回退到检索原序。"""
        try:
            reranked = await self._reranker.rerank(
                query=query,
                results=candidates,
                top_k=None,
            )

            logger.info(
                "retrieval_rerank_completed",
                query_length=len(query),
                candidate_count=len(candidates),
                output_count=len(reranked),
                top_score=reranked[0].score if reranked else 0.0,
            )

            return reranked

        except Exception as e:
            logger.warning(
                "retrieval_rerank_failed_fallback_to_original",
                error=str(e),
                candidate_count=len(candidates),
            )
            return candidates
