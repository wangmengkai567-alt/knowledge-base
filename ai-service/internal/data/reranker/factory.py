"""根据明确配置的 provider 创建重排器；不根据密钥是否存在自动选择实现。"""

from __future__ import annotations

from internal.biz.repo import Reranker
from internal.data.reranker.memory import MemoryReranker
from internal.data.reranker.qwen import QwenReranker
from internal.data.reranker.zhipu import ZhipuReranker


def create_reranker(
    provider: str = "zhipu",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout: int = 30,
) -> Reranker:
    """根据 provider 创建千问、智谱或本地关键词覆盖率重排器，传入对应连接和模型参数。"""
    if provider == "memory":
        return MemoryReranker()

    if provider == "qwen":
        if not api_key or not base_url or not model:
            raise ValueError(
                "Reranker provider 'qwen' requires AI_RERANKER__API_KEY, "
                "AI_RERANKER__BASE_URL and AI_RERANKER__MODEL."
            )
        return QwenReranker(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )

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
