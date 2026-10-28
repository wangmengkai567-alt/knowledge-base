"""问答 DTO 转换；同步与 SSE 共用来源序列化。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
from typing import TYPE_CHECKING

import anyio
import structlog

from api.openapi.schemas.rag import (
    HitScoreDTO,
    MaterialSegmentDTO,
    RAGQueryResponse,
    RAGSourceDTO,
    RAGWebSourceDTO,
)
from internal.biz.materials import WebSource
from internal.biz.rag import retrieval_results
from internal.service.retrieval import retrieval_dto

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from internal.biz.answer_run import AnswerRun, RunEvent
    from internal.biz.materials import AnswerSource
    from internal.biz.rag import RAGBiz
    from internal.data.answer_runs import LocalAnswerRunStore


def source_dto(source: AnswerSource | WebSource) -> RAGSourceDTO | RAGWebSourceDTO:
    if isinstance(source, WebSource):
        return RAGWebSourceDTO(**asdict(source))
    return RAGSourceDTO(
        citation_id=source.citation_id,
        source_type=source.source_type,
        content=source.content,
        chunk_ids=[segment.chunk_id for segment in source.segments],
        segments=[MaterialSegmentDTO(**asdict(segment)) for segment in source.segments],
        truncated=source.truncated,
        document_id=source.document_id,
        document_filename=source.document_filename,
        knowledge_base_id=source.knowledge_base_id,
        document_updated_at=source.document_updated_at,
        retrieval_hits=[
            HitScoreDTO(
                chunk_id=hit.chunk_id,
                score=hit.score,
                score_type=hit.score_type,
                score_method=hit.score_method,
                scores=[asdict(score) for score in hit.scores],
            )
            for hit in source.retrieval_hits
        ],
    )


def response_dto(run: AnswerRun) -> RAGQueryResponse:
    sources = [source_dto(source) for source in run.sources]
    return RAGQueryResponse(
        run_id=run.run_id, status=run.status, stop_reason=run.stop_reason,
        error_code=run.error_code, usage=asdict(run.usage),
        events=[asdict(event) for event in run.events], answer=run.answer, model=run.model,
        sources=sources, source_count=len(sources),
        retrieval_results=[retrieval_dto(hit) for hit in retrieval_results(run)],
        input_measurement=run.input_measurement,
        estimated_input_tokens=run.usage.estimated_input_tokens,
        prompt_tokens=run.usage.prompt_tokens, completion_tokens=run.usage.completion_tokens,
    )


class RAGService:
    def __init__(self, rag_biz: RAGBiz, run_store: LocalAnswerRunStore) -> None:
        self._rag_biz = rag_biz
        self._store = run_store
        self._janitor: asyncio.Task | None = None

    async def start(self) -> None:
        await asyncio.to_thread(self._store.cleanup)
        self._janitor = asyncio.create_task(self._clean_periodically())

    async def close(self) -> None:
        if self._janitor is not None:
            self._janitor.cancel()
            with suppress(asyncio.CancelledError):
                await self._janitor

    async def _clean_periodically(self) -> None:
        while True:
            await asyncio.sleep(3600)
            try:
                await asyncio.to_thread(self._store.cleanup)
            except OSError:
                structlog.get_logger().exception("answer_run_cleanup_failed")

    async def _execute(self, *, on_event=None, **kwargs) -> AnswerRun:
        observed: AnswerRun | None = None

        def observe(run: AnswerRun, event: RunEvent) -> None:
            nonlocal observed
            observed = run
            if on_event is not None:
                on_event(run, event)

        # 屏蔽响应生成器的 AnyIO 取消域；断连时由生成器显式取消此任务，
        # 再等待模型/工具清理和快照保存，避免留下后台运行。
        with anyio.CancelScope(shield=True):
            try:
                return await self._rag_biz.query(**kwargs, on_event=observe)
            finally:
                if observed is not None:
                    snapshot = {
                        "version": 4, "query": kwargs["query"],
                        "started_at": observed.started_at,
                        "scope": asdict(observed.scope), "limits": asdict(observed.limits),
                        "result": response_dto(observed).model_dump(),
                        "tool_results": [
                            {
                                "call_id": result.call_id, "tool": result.name,
                                "status": result.status,
                                "errors": [asdict(error) for error in result.errors],
                                "sources": [source_dto(source).model_dump() for source in result.sources],
                            }
                            for result in observed.tool_results
                        ],
                    }
                    await asyncio.to_thread(
                        self._store.save, observed.scope.user_id, observed.run_id, snapshot
                    )

    async def query(self, **kwargs) -> RAGQueryResponse:
        return response_dto(await self._execute(**kwargs))

    async def query_stream(self, **kwargs) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def emit(run: AnswerRun, event: RunEvent) -> None:
            payload = {"type": "event", "version": 4, "run_id": run.run_id, **asdict(event)}
            if event.kind == "sources_updated":
                payload["data"] = {
                    **event.data,
                    "sources": [source_dto(source).model_dump() for source in run.sources],
                    "retrieval_results": [retrieval_dto(hit).model_dump() for hit in retrieval_results(run)],
                }
            queue.put_nowait(payload)

        task = asyncio.create_task(self._execute(**kwargs, on_event=emit))
        task.add_done_callback(lambda _: queue.put_nowait(None))
        try:
            while (event := await queue.get()) is not None:
                yield event
            run = await task
            yield {"type": "result", **response_dto(run).model_dump()}
        finally:
            with anyio.CancelScope(shield=True):
                if not task.done():
                    task.cancel()
                # task.result() 的错误在正常路径向上传播；取消路径记录保存失败。
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    structlog.get_logger().exception("answer_run_execution_failed")
