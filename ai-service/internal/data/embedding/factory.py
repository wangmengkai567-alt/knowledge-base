"""
internal/data/embedding/factory.py — EmbeddingProvider 工厂

根据配置创建对应的 EmbeddingProvider 实例。
"""

from __future__ import annotations

from internal.biz.repo import EmbeddingProvider
from internal.data.embedding.local import OllamaEmbedding
from internal.data.embedding.memory import MemoryEmbedding
from internal.data.embedding.openai import OpenAIEmbedding


def create_embedding_provider(
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    dimension: int,
    timeout: int = 30,
    keep_alive: str = "5m",
    max_concurrency: int = 5,
) -> EmbeddingProvider:
    """根据提供商创建 EmbeddingProvider 实例。

    参数:
        provider: 提供商名称（openai / deepseek / local / memory / aliyun / tencent / zhipu）
        base_url: API 基础 URL
        api_key: API 密钥
        model: 模型名称
        dimension: 向量维度
        timeout: 超时秒数
        keep_alive: local(Ollama) 模式的模型保活时长
        max_concurrency: local(Ollama) 模式的最大并发请求数

    返回:
        EmbeddingProvider 实例
    """
    if provider == "local":
        return OllamaEmbedding(
            base_url=base_url or "http://localhost:11434",
            model=model or "nomic-embed-text",
            dimension=dimension,
            timeout=timeout,
            max_concurrency=max_concurrency,
            keep_alive=keep_alive,
        )

    if provider in ("openai", "deepseek", "aliyun", "tencent", "zhipu"):
        if not api_key:
            raise ValueError(
                f"Embedding provider '{provider}' requires api_key. "
                "Set AI_EMBEDDING__API_KEY environment variable."
            )
        return OpenAIEmbedding(
            base_url=base_url,
            api_key=api_key,
            model=model,
            dimension=dimension,
            timeout=timeout,
            request_dimensions=provider == "zhipu" and model.startswith("embedding-3"),
        )

    if provider == "memory":
        return MemoryEmbedding(dimension=dimension)

    raise ValueError(f"Unsupported embedding provider: '{provider}'")
