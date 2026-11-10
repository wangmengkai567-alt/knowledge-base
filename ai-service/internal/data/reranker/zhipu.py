"""调用配置地址下的 /rerank 接口，为输入候选获取原始分数。

按响应 index 关联候选，将原始分数经本模块的 sigmoid 公式转换后排序。
原始分数与转换方法均保留在评分记录中。
"""

from __future__ import annotations

import math
import uuid
from typing import TYPE_CHECKING

import httpx
import structlog

from internal.biz.repo import Reranker
from pkg.errors.base import RAGError

# _squash_raw_score 使用 sigmoid(steepness × (raw - midpoint)) 映射原始分数。
_RAW_SCORE_MIDPOINT = 16.0   # 此原始分数映射为 0.5。
_RAW_SCORE_STEEPNESS = 0.4   # 控制原始分数变化时转换结果的变化幅度；转换值不是概率。

logger = structlog.get_logger()

_MAX_DOCUMENTS = 128  # 一次请求最多发送的候选数量；超出时截取输入列表前部。
_MAX_CHARS = 4096     # 查询及每条“文件名 + 正文”的字符数上限。

if TYPE_CHECKING:
    from internal.biz.entity import RetrievalResult


def _clip(text: str, limit: int = _MAX_CHARS) -> str:
    """将发送给重排服务的文本截到 limit 个字符；limit 不是 token 数量。"""
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
        """配置智谱重排接口的基础地址、密钥和模型；timeout 为单次请求超时秒数。"""
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        """返回请求智谱重排接口时使用的模型名。"""
        return self._model

    # 请求智谱为候选分块打分，将原始分数转换到统一范围后排序。
    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """向智谱提交查询和候选文本，校验响应索引与分数，再转换分数并排序。

        候选列表超出本模块数量上限时截取前部；top_k=None 或非正数时请求全部候选评分。
        服务、格式或数量错误抛出 RAGError，不返回未经重排的原候选。
        """
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
            "return_raw_scores": True,
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
            # 保留 RERANK_TIMEOUT 分类，供检索工具和问答层识别超时终止。
            raise RAGError(
                reason="RERANK_TIMEOUT",
                message=f"智谱重排序超时（{self._timeout}s）",
                metadata={"url": url, "model": self._model},
            ) from e
        except httpx.ConnectError as e:
            # 网络失败直接作为重排失败上报，不使用原检索排序替代重排结果。
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
            # HTTP 成功仍可能返回无法解析的 JSON，此时明确报告响应格式错误。
            raise RAGError(
                reason="RERANK_BAD_RESPONSE",
                message="智谱重排序返回了无法解析的响应",
            ) from e

        api_results = data.get("results") or []
        if not api_results:
            raise RAGError(reason="RERANK_BAD_RESPONSE", message="智谱重排返回空结果")

        reranked: list[RetrievalResult] = []
        seen: set[int] = set()
        for item in api_results:
            idx = item.get("index")
            if type(idx) is not int or idx in seen or not (0 <= idx < len(results)):
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="智谱重排索引无效")
            seen.add(idx)
            try:
                score = float(item["relevance_score"])
            except (KeyError, TypeError, ValueError) as exc:
                # 候选索引或真实分数缺失时拒绝结果，不按返回位置补配候选或生成分数。
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="智谱重排缺少分数") from exc
            if not math.isfinite(score):
                raise RAGError(reason="RERANK_BAD_RESPONSE", message="智谱重排分数无效")
            reranked.append(results[idx].with_score(score, f"zhipu:{self._model}:raw_logit"))

        if len(reranked) != (top_n or len(results)):
            raise RAGError(reason="RERANK_BAD_RESPONSE", message="智谱重排结果不完整")
        reranked = _calibrate_zhipu_scores(reranked)
        reranked.sort(key=lambda item: item.score, reverse=True)

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


# 将供应商原始分数映射到 0～1，供相关性筛选使用。
def _squash_raw_score(raw: float) -> float:
    return round(1.0 / (1.0 + math.exp(-_RAW_SCORE_STEEPNESS * (raw - _RAW_SCORE_MIDPOINT))), 6)


# 保留原始评分记录，并登记转换后的分数及计算方式。
def _calibrate_zhipu_scores(reranked: list[RetrievalResult]) -> list[RetrievalResult]:
    scores = [item.score for item in reranked]
    calibrated = [item.with_score(_squash_raw_score(item.score), f"{item.score_method}:sigmoid(0.4*(raw-16))") for item in reranked]
    logger.info(
        "zhipu_rerank_raw_scores_squashed",
        raw_top=max(scores),
        unit_top=calibrated[0].score if calibrated else 0.0,
    )
    return calibrated


def _error_message(response: httpx.Response) -> str:
    """提取智谱响应中的错误说明；无法解析时保留状态码及最多 200 字符响应文本。"""
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return f"智谱重排序 HTTP {response.status_code}：{err['message']}"
    except ValueError:
        pass
    return f"智谱重排序 HTTP {response.status_code}：{response.text[:200]}"
