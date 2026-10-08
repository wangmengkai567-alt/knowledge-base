"""RAG must not reply 我不知道 once retrieval already returned evidence.

Uses synthetic notes only — not the golden eval questions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.entity import ChatChunk, ChatResult, RetrievalResult
from internal.biz.rag import RAGBiz
from internal.prompt.base import PromptTemplate


def _source() -> RetrievalResult:
    return RetrievalResult(
        chunk_id=11,
        score=0.91,
        chunk_content="组件启动顺序是：先加载配置，再连接存储，最后对外提供服务。",
        chunk_position=0,
        document_id=3,
        document_filename="runtime-notes.md",
        knowledge_base_id=1,
    )


class _StubPrompts:
    def get_template(self, name: str) -> PromptTemplate:
        return PromptTemplate(
            name=name,
            system_prompt="资料：{{ context }}",
            user_template="{{ query }}",
            variables=["query", "context"],
        )


class _StubRetrieval:
    _min_relevance = 0.5

    def __init__(self, sources: list[RetrievalResult]) -> None:
        self._sources = sources

    async def retrieve(self, **kwargs):
        return list(self._sources)

    async def load_by_ids(self, chunk_ids, allowed_kb_ids=None):
        return list(self._sources)


class _ScriptedLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list[dict[str, str]]] = []

    @property
    def model_name(self) -> str:
        return "scripted"

    async def generate(self, messages, temperature=None, max_tokens=None) -> ChatResult:
        self.calls.append(messages)
        text = self._replies.pop(0) if self._replies else "我不知道"
        return ChatResult(content=text, model=self.model_name)

    async def generate_stream(self, messages, temperature=None, max_tokens=None):
        result = await self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        yield ChatChunk(delta=result.content, model=result.model, finish_reason="stop")


def _biz(sources: list[RetrievalResult], replies: list[str]) -> tuple[RAGBiz, _ScriptedLLM]:
    llm = _ScriptedLLM(replies)
    biz = RAGBiz(
        retrieval_biz=_StubRetrieval(sources),
        llm_provider=llm,
        prompt_manager=_StubPrompts(),
    )
    return biz, llm


class RAGGroundedAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_sources_still_refuse_without_llm(self):
        biz, llm = _biz([], ["不应被调用"])
        result = await biz.query(query="启动顺序是什么？", knowledge_base_id=1)
        self.assertEqual(result.answer, "我不知道")
        self.assertEqual(llm.calls, [])

    async def test_nonempty_sources_retry_when_model_refuses(self):
        biz, llm = _biz([_source()], ["我不知道", "先加载配置，再连接存储。"])
        result = await biz.query(query="启动顺序是什么？", knowledge_base_id=1)
        self.assertNotEqual(result.answer.strip(), "我不知道")
        self.assertIn("加载配置", result.answer)
        self.assertEqual(len(llm.calls), 2)

    async def test_nonempty_sources_never_surface_refusal_after_retry(self):
        biz, llm = _biz([_source()], ["我不知道", "我不知道。"])
        result = await biz.query(query="启动顺序是什么？", knowledge_base_id=1)
        self.assertFalse((result.answer or "").strip().startswith("我不知道"))
        self.assertEqual(result.answer, "模型回答失败")
        self.assertEqual(len(llm.calls), 2)

    async def test_low_score_sources_refuse_without_llm(self):
        weak = RetrievalResult(
            chunk_id=21,
            score=0.01,
            chunk_content="退款只走原支付渠道，到账时间以渠道为准。",
            chunk_position=0,
            document_id=8,
            document_filename="refund.md",
            knowledge_base_id=1,
        )
        biz, llm = _biz([weak], ["不应被调用"])
        result = await biz.query(query="盆栽怎么修剪才不会枯？", knowledge_base_id=1)
        self.assertEqual(result.answer, "我不知道")
        self.assertEqual(llm.calls, [])
        self.assertEqual(result.sources, [])

    async def test_stream_nonempty_sources_does_not_emit_refusal(self):
        biz, llm = _biz([_source()], ["我不知道", "先加载配置，再连接存储。"])
        events = [event async for event in biz.query_stream(query="启动顺序是什么？", knowledge_base_id=1)]
        text = "".join(
            (event.chunk.delta if event.chunk else "") for event in events if event.event_type == "chunk"
        )
        self.assertNotEqual(text.strip(), "我不知道")
        self.assertIn("加载配置", text)
        self.assertEqual(len(llm.calls), 2)


if __name__ == "__main__":
    unittest.main()
