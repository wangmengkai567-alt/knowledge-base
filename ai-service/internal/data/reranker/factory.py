"""
internal/data/reranker/factory.py — Reranker 工厂

zhipu：智谱文本重排序 API。
memory：关键词重叠精排，仅作无密钥时的对照实现。
"""

from __future__ import annotations

from internal.biz.repo import Reranker
from internal.data.reranker.memory import MemoryReranker
from internal.data.reranker.zhipu import ZhipuReranker


def create_reranker(
    provider: str = "zhipu",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout: int = 30,
) -> Reranker:
    """按配置创建重排序器。更换智谱模型只传 model，不必改代码。"""
    if provider == "memory":
        return MemoryReranker()

    if provider == "zhipu":
        if not api_key:
            raise ValueError(
                "Reranker provider 'zhipu' requires api_key. "
                "Set AI_RERANKER__API_KEY."
            )
        if not base_url:
            raise ValueError(
                "Reranker provider 'zhipu' requires base_url. "
                "Set AI_RERANKER__BASE_URL."
            )
        if not model:
            raise ValueError(
                "Reranker provider 'zhipu' requires model. "
                "Set AI_RERANKER__MODEL."
            )
        return ZhipuReranker(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )

    raise ValueError(f"Unsupported reranker provider: '{provider}'")
