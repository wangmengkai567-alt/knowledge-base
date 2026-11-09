"""问答 DTO 转换；同步与 SSE 共用来源序列化。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
from typing import TYPE_CHECKING

import anyio
import structlog

from api.openapi.schemas.rag import (
    EvidenceFragmentDTO,
    HitScoreDTO,
    MaterialSegmentDTO,
    RAGQueryResponse,
    RAGSourceDTO,
    RAGWebSourceDTO,
)
from internal.biz.materials import WebSource
from internal.biz.rag import retrieval_results
from internal.biz.tool_types import ANSWER_CONTRACT_VERSION
from internal.service.retrieval import retrieval_dto
from pkg.errors.diagnostics import unexpected_error_details

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from internal.biz.answer_run import AnswerRun, RunEvent
    from internal.biz.materials import AnswerSource
    from internal.biz.rag import RAGBiz
    from internal.data.answer_runs import LocalAnswerRunStore


def source_dto(source: AnswerSource | WebSource) -> RAGSourceDTO | RAGWebSourceDTO:
    """把知识库或网页来源转换为接口响应对象，保留实际送模正文、位置和真实评分信息。"""
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
    """从同一个 AnswerRun 构建响应，集中转换答案、终态、用量、来源、证据和事件。"""
    sources = [source_dto(source) for source in run.sources]
    return RAGQueryResponse(
        run_id=run.run_id, status=run.status, stop_reason=run.stop_reason,
        exploration_stop_reason=run.exploration_stop_reason,
        error_code=run.error_code, usage=asdict(run.usage),
        events=[asdict(event) for event in run.events], answer=run.answer, model=run.model,
        sources=sources, source_count=len(sources),
        evidence_catalog=[EvidenceFragmentDTO(**asdict(item)) for item in run.evidence_catalog],
        web_directory=[asdict(entry) for entry in run.web_directory],
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
            except OSError as exc:
                structlog.get_logger().error("answer_run_cleanup_failed", **unexpected_error_details(exc))

    async def _execute(self, *, on_event=None, **kwargs) -> AnswerRun:
        """执行一次问答，并在正常结束、失败或取消时保存该用户的本地运行快照。

        kwargs 传给业务入口 query；on_event 接收同一次执行的进度，供 SSE 推送使用。
        返回 AnswerRun；快照保存失败仍向上传播，避免对外声称运行记录已保存。
        """
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
                        "version": 5, "tool_contract_version": 2, "query": kwargs["query"],
                        "web_pages": {source_id: asdict(page) for source_id, page in observed.web_pages.items()},
                        "evidence_selection": observed.evidence_selection,
                        "final_response": observed.final_response,
                        "final_request": observed.final_request,
                        "answer_contract_version": ANSWER_CONTRACT_VERSION,
                        "answer_claims": observed.answer_claims,
                        "started_at": observed.started_at,
                        "scope": asdict(observed.scope), "limits": asdict(observed.limits),
                        "result": response_dto(observed).model_dump(),
                        "tool_results": [
                            {
                                "call_id": result.call_id, "tool": result.name,
                                "status": result.status,
                                "errors": [asdict(error) for error in result.errors],
                                "web_diagnostics": [asdict(item) for item in result.diagnostics],
                                "directory": [asdict(entry) for entry in result.directory],
                                "matches": result.matches, "selection": result.selection,
                                "sources": [source_dto(source).model_dump() for source in result.sources],
                            }
                            for result in observed.tool_results
                        ],
                    }
                    await asyncio.to_thread(
                        self._store.save, observed.scope.user_id, observed.run_id, snapshot
                    )

    async def query(self, **kwargs) -> RAGQueryResponse:
        """执行问答并保存快照，返回完整响应；与流式接口共用同一业务执行入口。"""
        return response_dto(await self._execute(**kwargs))

    async def query_stream(self, **kwargs) -> AsyncIterator[dict]:
        """同一次执行经队列推送过程事件，保存快照后再发送最终结果；答案一次发布。"""
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def emit(run: AnswerRun, event: RunEvent) -> None:
            payload = {"type": "event", "version": 5, "tool_contract_version": 2, "answer_contract_version": ANSWER_CONTRACT_VERSION, "run_id": run.run_id, **asdict(event)}
            if event.kind == "sources_updated":
                payload["data"] = {
                    **event.data,
                    "sources": [source_dto(source).model_dump() for source in run.sources],
                    "retrieval_results": [retrieval_dto(hit).model_dump() for hit in retrieval_results(run)],
                }
            queue.put_nowait(payload)

        task = asyncio.create_task(self._execute(**kwargs, on_event=emit))
        # 任务成功或异常都会唤醒消费者；队列结束后 await task 才取得结果或抛出错误。
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
                except Exception as exc:
                    structlog.get_logger().error("answer_run_execution_failed", **unexpected_error_details(exc))
