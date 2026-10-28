"""
internal/data/reranker/qwen.py — 千问文本重排序（DashScope 原生接口）

协议见：https://platform.qianwenai.com/docs/api-reference/rerank/dashscope-rerank
POST {origin}/api/v1/services/rerank/text-rerank/text-rerank
Authorization: Bearer {api_key}

请求：model / input.query / input.documents，可选 parameters.top_n / instruct。
响应：output.results[].index、output.results[].relevance_score（0~1）。

qwen3.7-text-rerank 不支持 OpenAI 兼容的 /compatible-api/v1/reranks，
base_url 填 /compatible-mode/v1 也只取其域名。
"""

from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
import structlog

from internal.biz.repo import Reranker
from pkg.errors.base import RAGError

logger = structlog.get_logger()

_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"
_DEFAULT_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query."
# qwen3.7-text-rerank：最多 500 篇；单篇上限远高于分块长度，按字符截断只防异常大块。
_MAX_DOCUMENTS = 500
_MAX_CHARS = 8000

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult


def rerank_endpoint(base_url: str) -> str:
    """任意千问 base_url（含 /compatible-mode/v1）都落到 DashScope 原生重排路径。"""
    raw = (base_url or "").strip().rstrip("/")
    if _RERANK_PATH in raw:
        return raw
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Invalid reranker base_url: {base_url!r}")
    return f"{parts.scheme}://{parts.netloc}{_RERANK_PATH}"


def _document_text(item: RetrievalResult) -> str:
    name = (item.document_filename or "").strip()
    content = (item.chunk_content or "").strip()
    text = f"{name}\n{content}" if name and content else (content or name)
    return text[:_MAX_CHARS]


class QwenReranker(Reranker):
    """调用千问 DashScope 文本重排序，按 relevance_score 重排检索结果。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 30,
        instruct: str = _DEFAULT_INSTRUCT,
    ) -> None:
        self._url = rerank_endpoint(base_url)
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._instruct = instruct

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
                "qwen_rerank_documents_truncated",
                original=len(results),
                kept=_MAX_DOCUMENTS,
            )
            results = results[:_MAX_DOCUMENTS]

        top_n = 0 if top_k is None or top_k <= 0 else min(top_k, len(results))
        parameters: dict = {"return_documents": False}
        if top_n > 0:
            parameters["top_n"] = top_n
        if self._instruct:
            parameters["instruct"] = self._instruct
        payload = {
            "model": self._model,
            "input": {
                "query": (query or "")[:_MAX_CHARS],
                "documents": [_document_text(item) for item in results],
            },
            "parameters": parameters,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "X-Request-Id": uuid.uuid4().hex,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, json=payload, headers=headers)
        except httpx.TimeoutException as e:
            raise RAGError(
                reason="RERANK_TIMEOUT",
                message=f"千问重排序超时（{self._timeout}s）",
                metadata={"url": self._url, "model": self._model},
            ) from e
        except httpx.HTTPError as e:
            raise RAGError(
                reason="RERANK_CONNECT_ERROR",
                message=f"千问重排序连接失败：{e}",
                metadata={"url": self._url, "model": self._model},
            ) from e

        if response.status_code != 200:
            raise RAGError(
                reason="RERANK_API_ERROR",
                message=_error_message(response),
                metadata={"status_code": response.status_code, "model": self._model},
            )

        try:
            data = response.json()
        except ValueError as e:
            raise RAGError(
                reason="RERANK_BAD_RESPONSE",
                message="千问重排序返回了无法解析的响应",
            ) from e

        api_results = (data.get("output") or {}).get("results") or []
        if not api_results:
            raise RAGError(
                reason="RERANK_BAD_RESPONSE",
                message="千问重排序返回空结果",
                metadata={"model": self._model},
            )

        reranked: list[RetrievalResult] = []
        seen: set[int] = set()
        for item in api_results:
            idx = item.get("index")
            if type(idx) is not int or idx in seen or not (0 <= idx < len(results)):
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="重排返回无效分块索引")
            seen.add(idx)
            try:
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="重排缺少真实分数") from exc
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="重排分数无效")
            reranked.append(results[idx].with_score(score, f"qwen:{self._model}:relevance_score"))
        expected = top_n or len(results)
        if len(reranked) != expected:
            raise RAGError(reason="RERANK_BAD_RESPONSE", message="重排返回结果数量不完整")
        reranked.sort(key=lambda r: r.score, reverse=True)

        usage = data.get("usage") or {}
        logger.info(
            "qwen_rerank_completed",
            model=self._model,
            input_count=len(results),
            output_count=len(reranked),
            top_score=reranked[0].score if reranked else 0.0,
            total_tokens=usage.get("total_tokens"),
            request_id=data.get("request_id"),
        )
        return reranked


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("message"):
            return f"千问重排序 HTTP {response.status_code}：{body.get('code', '')} {body['message']}"
    except ValueError:
        pass
    return f"千问重排序 HTTP {response.status_code}：{response.text[:200]}"
