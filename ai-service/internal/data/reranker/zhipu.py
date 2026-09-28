"""
internal/data/reranker/zhipu.py — 智谱文本重排序

协议见：https://docs.bigmodel.cn/api-reference/模型-api/文本重排序
POST {base_url}/rerank
Authorization: Bearer {api_key}

请求：model / query / documents，可选 top_n。
响应：results[].index、results[].relevance_score。

模型名由配置传入，不写死；更换智谱重排模型只改 AI_RERANKER__MODEL。
"""

from __future__ import annotations

import uuid

import httpx
import structlog

from internal.biz.entity import RetrievalResult
from internal.biz.repo import Reranker
from pkg.errors.base import RAGError

logger = structlog.get_logger()

# 智谱文档约束：documents 最多 128 条，query / 单条文本最多 4096 字符。
_MAX_DOCUMENTS = 128
_MAX_CHARS = 4096


def _clip(text: str, limit: int = _MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _document_text(item: RetrievalResult) -> str:
    """把标题和正文拼成一条候选，方便重排模型看到文件名。"""
    name = (item.document_filename or "").strip()
    content = (item.chunk_content or "").strip()
    if name and content:
        return _clip(f"{name}\n{content}")
    return _clip(content or name)


class ZhipuReranker(Reranker):
    """调用智谱 /paas/v4/rerank，按相关性分数重排检索结果。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 30,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        if not results:
            return []

        if len(results) > _MAX_DOCUMENTS:
            logger.warning(
                "zhipu_rerank_documents_truncated",
                original=len(results),
                kept=_MAX_DOCUMENTS,
            )
            results = results[:_MAX_DOCUMENTS]

        documents = [_document_text(item) for item in results]
        top_n = 0 if top_k is None or top_k <= 0 else min(top_k, len(documents))
        request_id = uuid.uuid4().hex
        url = f"{self._base_url}/rerank"
        payload: dict = {
            "model": self._model,
            "query": _clip(query),
            "documents": documents,
            "return_documents": False,
            "return_raw_scores": False,
            "request_id": request_id,
        }
        if top_n > 0:
            payload["top_n"] = top_n

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        logger.debug(
            "zhipu_rerank_request",
            url=url,
            model=self._model,
            document_count=len(documents),
            top_n=top_n or "all",
            request_id=request_id,
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise RAGError(
                reason="RERANK_TIMEOUT",
                message=f"智谱重排序超时（{self._timeout}s）",
                metadata={"url": url, "model": self._model},
            ) from e
        except httpx.ConnectError as e:
            raise RAGError(
                reason="RERANK_CONNECT_ERROR",
                message=f"智谱重排序连接失败：{e}",
                metadata={"url": url, "model": self._model},
            ) from e

        if response.status_code != 200:
            raise RAGError(
                reason="RERANK_API_ERROR",
                message=_error_message(response),
                metadata={
                    "status_code": response.status_code,
                    "model": self._model,
                    "request_id": request_id,
                },
            )

        try:
            data = response.json()
        except ValueError as e:
            raise RAGError(
                reason="RERANK_BAD_RESPONSE",
                message="智谱重排序返回了无法解析的响应",
            ) from e

        api_results = data.get("results") or []
        if not api_results:
            logger.warning("zhipu_rerank_empty_results", model=self._model)
            limit = top_n if top_n > 0 else len(results)
            return results[:limit]

        reranked: list[RetrievalResult] = []
        seen: set[int] = set()
        for item in api_results:
            idx = item.get("index")
            if not isinstance(idx, int) or idx in seen or not (0 <= idx < len(results)):
                continue
            seen.add(idx)
            score = float(item.get("relevance_score") or 0.0)
            reranked.append(results[idx].with_score(score))

        usage = data.get("usage") or {}
        logger.info(
            "zhipu_rerank_completed",
            model=self._model,
            input_count=len(results),
            output_count=len(reranked),
            top_score=reranked[0].score if reranked else 0.0,
            prompt_tokens=usage.get("prompt_tokens"),
            total_tokens=usage.get("total_tokens"),
            request_id=data.get("request_id") or request_id,
        )
        return reranked


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return f"智谱重排序 HTTP {response.status_code}：{err['message']}"
    except ValueError:
        pass
    return f"智谱重排序 HTTP {response.status_code}：{response.text[:200]}"
