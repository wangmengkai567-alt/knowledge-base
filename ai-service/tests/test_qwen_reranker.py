"""Qwen DashScope rerank protocol and score handling. Synthetic notes only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.entity import RetrievalResult
from internal.data.reranker.factory import create_reranker
from internal.data.reranker.qwen import QwenReranker, rerank_endpoint
from pkg.errors.base import RAGError

_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


def _chunk(chunk_id: int, text: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        score=0.01,
        chunk_content=text,
        chunk_position=0,
        document_id=chunk_id,
        document_filename=f"note{chunk_id}.md",
        knowledge_base_id=1,
    )


def _client(status: int, payload: dict) -> AsyncMock:
    response = Mock()
    response.status_code = status
    response.json.return_value = payload
    response.text = ""
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class QwenEndpointTests(unittest.TestCase):
    def test_compatible_mode_base_maps_to_native_rerank_path(self):
        self.assertEqual(
            rerank_endpoint("https://maas.qianwenaiapi.com/compatible-mode/v1"),
            "https://maas.qianwenaiapi.com" + _PATH,
        )

    def test_full_rerank_url_is_kept(self):
        url = "https://ws.cn-beijing.maas.aliyuncs.com" + _PATH
        self.assertEqual(rerank_endpoint(url + "/"), url)

    def test_factory_builds_qwen(self):
        reranker = create_reranker(
            provider="qwen",
            base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
            api_key="k",
            model="qwen3.7-text-rerank",
        )
        self.assertIsInstance(reranker, QwenReranker)


class QwenRerankTests(unittest.IsolatedAsyncioTestCase):
    def _reranker(self) -> QwenReranker:
        return QwenReranker(
            base_url="https://example.invalid/compatible-mode/v1",
            api_key="test-key",
            model="qwen3.7-text-rerank",
        )

    async def test_sends_native_body_and_reorders_by_score(self):
        client = _client(
            200,
            {
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.97},
                        {"index": 0, "relevance_score": 0.003},
                    ]
                },
                "usage": {"total_tokens": 10},
            },
        )
        notes = [_chunk(1, "退款只走原支付渠道。"), _chunk(2, "客户端发 SYN，服务端回 SYN+ACK。")]
        with patch("internal.data.reranker.qwen.httpx.AsyncClient", return_value=client):
            ranked = await self._reranker().rerank("三次握手为什么不是两次？", notes, top_k=None)

        url = client.post.await_args.args[0]
        body = client.post.await_args.kwargs["json"]
        self.assertTrue(url.endswith(_PATH))
        self.assertEqual(body["model"], "qwen3.7-text-rerank")
        self.assertEqual(body["input"]["query"], "三次握手为什么不是两次？")
        self.assertEqual(len(body["input"]["documents"]), 2)
        self.assertNotIn("top_n", body["parameters"])
        self.assertEqual([r.chunk_id for r in ranked], [2, 1])
        self.assertGreater(ranked[0].score, 0.5)
        self.assertLess(ranked[1].score, 0.5)

    async def test_http_error_is_explicit(self):
        client = _client(404, {"code": "NotFound", "message": "no route"})
        with (
            patch("internal.data.reranker.qwen.httpx.AsyncClient", return_value=client),
            self.assertRaises(RAGError),
        ):
            await self._reranker().rerank("q", [_chunk(1, "x")])

    async def test_empty_results_raise(self):
        client = _client(200, {"output": {"results": []}})
        with (
            patch("internal.data.reranker.qwen.httpx.AsyncClient", return_value=client),
            self.assertRaises(RAGError),
        ):
            await self._reranker().rerank("q", [_chunk(1, "x")])


if __name__ == "__main__":
    unittest.main()
