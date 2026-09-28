"""
internal/data/llm/factory.py — LLMProvider 工厂

根据配置创建对应的 LLMProvider 实例。
支持 openai / deepseek / local(Ollama) / memory（内存伪模型）。
"""

from __future__ import annotations

from internal.biz.repo import LLMProvider
from internal.data.llm.local import OllamaLLM
from internal.data.llm.memory import MemoryLLM
from internal.data.llm.openai_patch import OpenAILLM


def create_llm_provider(
    provider: str = "memory",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4096,
    timeout: int = 60,
    keep_alive: str = "5m",
) -> LLMProvider:
    """根据配置创建 LLMProvider 实例。

    参数:
        provider: LLM 提供商（openai / deepseek / local / memory / aliyun / tencent / zhipu）
        base_url: API 基础 URL
        api_key: API 密钥
        model: 模型名称
        temperature: 默认生成温度
        max_tokens: 默认最大生成 token 数
        timeout: API 调用超时秒数
        keep_alive: local(Ollama) 模式的模型保活时长

    返回:
        LLMProvider 实例
    """
    if provider == "memory":
        return MemoryLLM(model=model or "memory-llm-v1")

    if provider == "local":
        return OllamaLLM(
            base_url=base_url or "http://localhost:11434",
            model=model or "qwen2.5:7b",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            keep_alive=keep_alive,
        )

    if provider in ("openai", "deepseek", "aliyun", "tencent", "zhipu"):
        if not api_key:
            raise ValueError(
                f"LLM provider '{provider}' requires api_key. "
                f"Set AI_LLM__API_KEY environment variable."
            )
        return OpenAILLM(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    raise ValueError(f"Unsupported LLM provider: '{provider}'")
