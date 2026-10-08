"""Rerank scores must not treat unrelated notes as relevant evidence.

Synthetic notes only — not golden eval questions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.entity import RetrievalResult
from internal.data.reranker.memory import MemoryReranker
from internal.data.reranker.zhipu import ZhipuReranker


def _chunk(chunk_id: int, text: str, filename: str, score: float = 0.2) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        score=score,
        chunk_content=text,
        chunk_position=0,
        document_id=chunk_id,
        document_filename=filename,
        knowledge_base_id=1,
    )


class MemoryRerankRelevanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_notes_score_below_generation_threshold(self):
        reranker = MemoryReranker()
        ranked = await reranker.rerank(
            query="盆栽怎么修剪才不会枯？",
            results=[
                _chunk(1, "退款只走原支付渠道，到账时间以渠道为准。", "refund.md"),
                _chunk(2, "发票申请需要财务审核，三个工作日内开具。", "invoice.md"),
            ],
        )
        self.assertTrue(ranked)
        self.assertLess(max(item.score for item in ranked), 0.5)


async def _zhipu_with_scores(raw_scores: list[float]) -> list[RetrievalResult]:
    reranker = ZhipuReranker(
        base_url="https://example.invalid",
        api_key="test-key",
        model="rerank",
    )
    payload = {
        "results": [
            {"index": i, "relevance_score": score}
            for i, score in enumerate(raw_scores)
        ]
    }
    response = Mock()
    response.status_code = 200
    response.json.return_value = payload
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    notes = [
        _chunk(i + 1, "退款只走原支付渠道，到账时间以渠道为准。", "refund.md")
        for i in range(len(raw_scores))
    ]
    with patch("internal.data.reranker.zhipu.httpx.AsyncClient", return_value=client):
        return await reranker.rerank(query="盆栽怎么修剪才不会枯？", results=notes)


class ZhipuRerankCollapseTests(unittest.IsolatedAsyncioTestCase):
    async def test_collapsed_near_one_scores_fall_back_to_lexical_floor(self):
        reranker = ZhipuReranker(
            base_url="https://example.invalid",
            api_key="test-key",
            model="rerank",
        )
        payload = {
            "results": [
                {"index": 0, "relevance_score": 0.999995},
                {"index": 1, "relevance_score": 0.999992},
            ]
        }
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload

        client = AsyncMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        notes = [
            _chunk(1, "退款只走原支付渠道，到账时间以渠道为准。", "refund.md"),
            _chunk(2, "发票申请需要财务审核，三个工作日内开具。", "invoice.md"),
        ]
        with patch("internal.data.reranker.zhipu.httpx.AsyncClient", return_value=client):
            ranked = await reranker.rerank(query="盆栽怎么修剪才不会枯？", results=notes)

        self.assertTrue(ranked)
        self.assertLess(max(item.score for item in ranked), 0.5)
        client.post.assert_awaited()
        sent = client.post.await_args.kwargs["json"]
        self.assertTrue(sent.get("return_raw_scores"))

    async def test_raw_logit_scores_drop_unrelated_below_threshold(self):
        ranked = await _zhipu_with_scores([12.171875, 11.304688])
        self.assertTrue(ranked)
        self.assertLess(max(item.score for item in ranked), 0.5)

    async def test_raw_logit_scores_keep_on_topic_above_threshold(self):
        ranked = await _zhipu_with_scores([22.2188, 19.9844])
        self.assertTrue(ranked)
        self.assertGreater(min(item.score for item in ranked), 0.5)


if __name__ == "__main__":
    unittest.main()
