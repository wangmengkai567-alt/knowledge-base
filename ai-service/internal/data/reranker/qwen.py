"""通过千问原生重排接口获取候选分块的相关性分数。

根据响应 index 关联输入候选，校验分数与数量后返回重排结果。
接口地址从 base_url 取协议和域名，再使用本模块固定的重排路径。
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

# 千问原生重排接口路径。
_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"
# 千问重排请求的默认任务说明。
_DEFAULT_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query."
_MAX_DOCUMENTS = 500  # 一次重排请求最多发送的候选数量；超出时截取输入列表前部。
_MAX_CHARS = 8000     # 查询及每条“文件名 + 正文”的字符数上限，不是 token 上限。

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult


def rerank_endpoint(base_url: str) -> str:
    """保留已含原生重排路径的地址；否则从有效 base_url 取协议与域名，追加固定重排路径。"""
    raw = (base_url or "").strip().rstrip("/")
    if _RERANK_PATH in raw:
        return raw
    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"Invalid reranker base_url: {base_url!r}")
    return f"{parts.scheme}://{parts.netloc}{_RERANK_PATH}"


def _document_text(item: RetrievalResult) -> str:
    """把文件名与分块正文组合成一条重排文本，并按单条字符数上限截取。"""
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
        """配置千问重排地址、密钥和模型；timeout 为超时秒数，instruct 为重排任务说明。"""
        self._url = rerank_endpoint(base_url)
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._instruct = instruct

    @property
    def model_name(self) -> str:
        """返回请求千问重排接口时使用的模型名。"""
        return self._model

    # 请求千问对候选分块重新打分，核对返回索引和数量后按分数排序。
    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """向千问提交查询和候选文本，通过响应 index 取得原候选并更新重排分。

        候选列表超出本模块数量上限时截取前部；top_k=None 或非正数时请求全部候选评分。
        响应缺失、重复索引或分数不在 0～1 范围内时抛出 RAGError。
        """
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
            # 保留 RERANK_TIMEOUT 分类，供检索工具和问答层识别超时终止。
            raise RAGError(
                reason="RERANK_TIMEOUT",
                message=f"千问重排序超时（{self._timeout}s）",
                metadata={"url": self._url, "model": self._model},
            ) from e
        except httpx.HTTPError as e:
            # 网络失败直接作为重排失败上报，不使用原检索排序替代重排结果。
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
            # HTTP 成功仍可能返回无法解析的 JSON，此时明确报告响应格式错误。
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
        # 供应商返回的是输入位置，通过它关联原分块，不能按返回顺序直接配对。
        seen: set[int] = set()
        for item in api_results:
            idx = item.get("index")
            if type(idx) is not int or idx in seen or not (0 <= idx < len(results)):
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="重排返回无效分块索引")
            seen.add(idx)
            try:
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError) as exc:
                # 候选索引或真实分数缺失时拒绝结果，不按返回位置补配候选或生成分数。
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
    """从响应中提取千问错误码和说明；无法解析时保留状态码及最多 200 字符响应文本。"""
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("message"):
            return f"千问重排序 HTTP {response.status_code}：{body.get('code', '')} {body['message']}"
    except ValueError:
        pass
    return f"千问重排序 HTTP {response.status_code}：{response.text[:200]}"
