"""
internal/conf/config.py — 全局配置模型

使用 Pydantic Settings：自动类型校验、默认值填充、env 覆盖、yaml 加载。
- env_prefix="AI_" + env_nested_delimiter="__"：AI_SERVER__HTTP_PORT → server.http_port
- env 优先级 > yaml > 代码默认值，密钥始终走 env
- extra="ignore"：忽略无关环境变量
- 安全默认值原则：代码默认值始终最安全（空密码、空 CORS、debug=False），
  yaml/env 显式开放。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


def _yaml_config_path() -> list[str]:
    """返回唯一的 YAML 配置文件路径（文件不存在则跳过）。"""
    path = Path(__file__).resolve().parent.parent.parent / "configs" / "config.yaml"
    return [str(path)] if path.exists() else []


# ──────────────────────────────────────────────
# 嵌套配置子模型
# 拆分原因：语义分组 + 嵌套覆盖 + 验证隔离 + 后续扩展不互相影响


class ServerConfig(BaseSettings):
    """HTTP 服务配置。"""

    host: str = Field(
        default="0.0.0.0",
        description="服务监听地址，0.0.0.0 表示接受所有网卡请求",
    )
    http_port: int = Field(
        default=8084,
        gt=0,
        le=65535,
        description="HTTP 监听端口，ai-service 默认 8084",
    )
    workers: int = Field(
        default=1,
        ge=1,
        description="uvicorn worker 数量。BM25 在进程内存里，必须为 1",
    )
    debug: bool = Field(
        default=False,
        description="调试模式，开启后输出详细日志+禁用限流",
    )
    app_title: str = Field(
        default="AI Knowledge Base Service",
        description="FastAPI 应用标题，展示在 OpenAPI / Swagger 文档中",
    )
    app_version: str = Field(
        default="0.1.0",
        description="应用版本号，与 Git tag 同步，通过 AI_SERVER__APP_VERSION 覆盖",
    )
    app_description: str = Field(
        default="AI Knowledge Base Service — RAG-powered intelligent Q&A",
        description="FastAPI 应用描述，展示在 OpenAPI / Swagger 文档中",
    )
    service_name: str = Field(
        default="ai-service",
        description="服务标识，用于日志和监控",
    )

    # CORS 配置 — 安全默认值原则：代码默认值最安全（空列表），
    # yaml/env 显式开放。
    cors_origins: list[str] = Field(
        default_factory=list,
        description="CORS 允许的来源列表，默认空（最安全），需通过 yaml/env 显式配置",
    )
    cors_credentials: bool = Field(
        default=False,
        description="CORS 是否允许携带 Cookie，origins=[*] 时必须 False",
    )
    cors_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "DELETE", "PATCH"],
        description="CORS 允许的 HTTP 方法",
    )
    cors_headers: list[str] = Field(
        default_factory=lambda: ["Content-Type", "Authorization", "X-Request-ID"],
        description="CORS 允许的请求头，默认白名单",
    )


class DatabaseConfig(BaseSettings):
    """数据库配置。改 url 即可在 SQLite 与 PostgreSQL 之间切换。"""

    url: str = Field(
        default="sqlite+aiosqlite:///./data/ai_kb.db",
        description="异步数据库连接 URL，如 sqlite+aiosqlite:///./data/ai_kb.db 或 postgresql+asyncpg://…",
    )
    echo: bool = Field(
        default=False,
        description="开启后打印 SQL",
    )
    pool_size: int = Field(
        default=5,
        ge=1,
        le=50,
        description="连接池大小（SQLite 忽略此参数）",
    )


class LLMConfig(BaseSettings):
    """LLM 推理服务配置 — 最频繁变更的参数，拆出便于 A/B 实验。"""

    provider: Literal["openai", "deepseek", "local", "memory", "aliyun", "tencent", "zhipu"] = Field(
        default="deepseek",
        description="LLM 提供商：openai / deepseek / local（Ollama）/ memory / aliyun / tencent（TokenHub）/ zhipu（智谱）",
    )
    base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="LLM API 基础 URL，OpenAI / DeepSeek / Aliyun 兼容 OpenAI 格式。使用 Aliyun 时需配置其兼容 OpenAI 格式的 BASE_URL，包含 WorkspaceId（如果需要）。",
    )
    api_key: str = Field(
        default="",
        min_length=0,
        description="LLM API Key，通过环境变量 AI_LLM__API_KEY 注入",
    )
    chat_model: str = Field(
        default="deepseek-chat",
        description="对话模型名称",
    )
    temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="LLM 生成温度，RAG 场景用 0.1 保证事实准确",
    )
    max_tokens: int = Field(
        default=4096,
        ge=1,
        le=8192,
        description="LLM 单次回复最大 token 数",
    )
    timeout: int = Field(
        default=30,
        gt=0,
        le=120,
        description="LLM API 调用超时秒数",
    )
    keep_alive: str = Field(
        default="5m",
        description="local(Ollama) 模式下模型保活时长，如 5m / 1h / -1",
    )

    @field_validator("api_key")
    @classmethod
    def api_key_required_for_cloud_provider(cls, v: str, info: ValidationInfo) -> str:
        """云服务提供商必须提供 API Key，本地模式可空"""
        provider = info.data.get("provider", "deepseek")
        if provider in ("openai", "deepseek", "aliyun", "tencent", "zhipu") and not v:
            raise ValueError(
                f"api_key is required when provider={provider}. "
                "Set it via AI_LLM__API_KEY environment variable."
            )
        return v


class RedisConfig(BaseSettings):
    """Redis 配置 — 用于 Session 存储、缓存、限流。"""

    url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis 连接 URL，通过 AI_REDIS__URL 环境变量覆盖",
    )
    session_prefix: str = Field(
        default="session:",
        description="Session key 前缀，session:{token} → user_id",
    )


class SessionConfig(BaseSettings):
    """Session 配置 — Opaque Token 模式，token 存 Redis。"""

    access_token_expire: int = Field(
        default=7200,
        gt=0,
        description="Access token 有效期（秒），默认 2 小时",
    )
    refresh_token_expire: int = Field(
        default=604800,
        gt=0,
        description="Refresh token 有效期（秒），默认 7 天",
    )
    token_header: str = Field(
        default="Authorization",
        description="携带 token 的 HTTP header 名称",
    )
    token_prefix: str = Field(
        default="Bearer ",
        description="token 前缀，解析时去除",
    )


class SecurityConfig(BaseSettings):
    """安全配置 — bcrypt cost、请求体大小限制等。"""

    bcrypt_cost: int = Field(
        default=12,
        ge=4,
        le=15,
        description="bcrypt cost factor，默认 12（约 250ms/次），不建议低于 10",
    )
    max_request_body_size: int = Field(
        default=10_000_000,
        gt=0,
        description="请求体最大字节数，默认 10MB",
    )


class PromptConfig(BaseSettings):
    """Prompt 模板配置 — 模板目录与默认模板名。"""

    template_dir: str = Field(
        default="./internal/prompt/templates",
        description="YAML 模板文件目录路径",
    )
    default_rag_template: str = Field(
        default="rag",
        description="默认 RAG 检索增强模板名称",
    )


class LogConfig(BaseSettings):
    """日志配置。"""

    level: str = Field(
        default="INFO",
        description="日志级别：DEBUG / INFO / WARNING / ERROR / CRITICAL",
    )
    format: Literal["json", "console"] = Field(
        default="json",
        description="日志输出格式：json 或 console（带颜色）",
    )


class StorageConfig(BaseSettings):
    """文件存储配置 — 文档上传存储位置。"""

    upload_dir: str = Field(
        default="./data/uploads",
        description="文件上传存储目录",
    )


class RAGConfig(BaseSettings):
    """RAG 核心参数 — 效果调优的关键变量，拆出便于 A/B 实验。"""

    chunk_size: int = Field(
        default=500,
        gt=0,
        le=2000,
        description="分段目标 token 数",
    )
    chunk_overlap: int = Field(
        default=50,
        ge=0,
        le=200,
        description="相邻分段重叠 token 数（估算值），50 保证上下文衔接",
    )
    retriever_provider: Literal["vector", "hybrid"] = Field(
        default="hybrid",
        description="检索策略：vector 纯向量 / hybrid 向量+关键词 RRF 融合",
    )
    rrf_k: int = Field(
        default=60,
        gt=0,
        description="RRF 融合参数 k，论文推荐 60",
    )
    rrf_candidate_multiplier: int = Field(
        default=2,
        gt=0,
        le=10,
        description="RRF 每一路候选数倍率：candidate_k = 请求 top_k * multiplier",
    )
    query_expand_enabled: bool = Field(
        default=True,
        description="是否对自然语言 query 做变体扩展并并行召回",
    )
    max_query_variants: int = Field(
        default=3,
        gt=0,
        le=5,
        description="并行检索的 query 变体上限（含原句）",
    )
    context_window: int = Field(
        default=1,
        ge=0,
        le=3,
        description="RAG 生成时向两侧扩展的邻块数，0 表示不扩展",
    )

    rerank_enabled: bool = Field(
        default=False,
        description="是否启用 Reranker 重排序（Sprint 14）",
    )
    rerank_top_k: int = Field(
        default=5,
        gt=0,
        le=20,
        description="重排序后保留的结果数",
    )
    min_relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "搜索结果最低相关度门槛（仅重排序开启时生效，作用于重排模型返回的 0~1 分数）。"
            "低于该值的结果被丢弃，避免跨知识库扇出时把各库'最不差'的无关 chunk 也展示出来。"
            "0 表示不丢任何结果。配置文件建议 0.5。"
        ),
    )


class RerankerConfig(BaseSettings):
    """文本重排序配置。密钥走环境变量，模型名可随时替换。"""

    provider: Literal["zhipu", "memory"] = Field(
        default="zhipu",
        description="重排序提供商：zhipu（智谱文本重排序）/ memory（关键词重叠）",
    )
    base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4",
        description="重排序 API 基础 URL，实际请求 POST {base_url}/rerank",
    )
    api_key: str = Field(
        default="",
        min_length=0,
        description="重排序 API Key，通过环境变量 AI_RERANKER__API_KEY 注入",
    )
    model: str = Field(
        default="rerank",
        description="重排序模型编码，对应请求体 model 字段，可按智谱控制台更换",
    )
    timeout: int = Field(
        default=30,
        gt=0,
        le=120,
        description="重排序 API 调用超时秒数",
    )


# TokenHub 向量模型输出维度
# 文档：https://cloud.tencent.com/document/product/1823/133515
_TENCENT_EMBEDDING_DIMENSIONS: dict[str, int] = {
    "kinfra-text-embedding-0.6b": 1024,
    "kinfra-text-embedding-4b": 2560,
    "kinfra-vl-embedding-2b": 2048,
    "kinfra-vl-embedding-8b": 4096,
}

_ZHIPU_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
# embedding-2 固定 1024；embedding-3 可选 256/512/1024/2048（默认 2048）
_ZHIPU_EMBEDDING_3_DIMS = frozenset({256, 512, 1024, 2048})
_STALE_API_HOSTS = ("deepseek.com", "openai.com", "tokenhub.tencentmaas.com")
_STALE_CHAT_MODELS = frozenset({"", "deepseek-chat", "hy3", "qwen2.5:7b"})
_STALE_EMBED_MODELS = frozenset({
    "",
    "text-embedding-3-small",
    "deepseek-embedding",
    "kinfra-text-embedding-0.6b",
    "nomic-embed-text",
})


def _needs_zhipu_base(url: str) -> bool:
    if not url.strip():
        return True
    lowered = url.lower()
    return any(host in lowered for host in _STALE_API_HOSTS)


class EmbeddingConfig(BaseSettings):
    """文本嵌入配置 — Embedding API 参数。"""

    provider: Literal["openai", "deepseek", "local", "memory", "aliyun", "tencent", "zhipu"] = Field(
        default="memory",
        description="Embedding 提供商：openai / deepseek / local（Ollama）/ memory / aliyun / tencent（TokenHub）/ zhipu（智谱）",
    )
    base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="Embedding API 基础 URL，local(Ollama) 模式通常为 http://localhost:11434",
    )
    api_key: str = Field(
        default="",
        min_length=0,
        description="Embedding API Key，走云厂商时通过环境变量注入，Ollama 可留空",
    )
    model: str = Field(
        default="text-embedding-3-small",
        description="Embedding 模型名称",
    )
    dimension: int = Field(
        default=1024,
        gt=0,
        le=4096,
        description="向量维度，需与模型匹配",
    )
    batch_size: int = Field(
        default=32,
        gt=0,
        le=256,
        description="单次 API 调用的最大文本数",
    )
    timeout: int = Field(
        default=30,
        gt=0,
        le=120,
        description="Embedding API 调用超时秒数",
    )
    keep_alive: str = Field(
        default="5m",
        description="local(Ollama) 模式下 embedding 模型保活时长，如 5m / 1h / -1",
    )
    max_concurrency: int = Field(
        default=5,
        gt=0,
        le=32,
        description="local(Ollama) 批量 embedding 的最大并发请求数",
    )


class RateLimitConfig(BaseSettings):
    """限流配置。"""

    enabled: bool = Field(
        default=True,
        description="是否启用限流中间件",
    )
    standard_limit: int = Field(
        default=60,
        gt=0,
        le=1000,
        description="标准接口每窗口最大请求数",
    )
    heavy_limit: int = Field(
        default=120,
        gt=0,
        le=1000,
        description="高消耗接口（检索/RAG/解析/嵌入）每窗口最大请求数",
    )
    window_seconds: int = Field(
        default=60,
        gt=0,
        le=3600,
        description="滑动窗口时长（秒）",
    )


class VectorStoreConfig(BaseSettings):
    """向量存储配置 — 向量数据库选择。"""

    provider: Literal["memory", "pgvector"] = Field(
        default="memory",
        description="向量存储提供商：memory（进程内存）/ pgvector（PostgreSQL）",
    )


class KeywordStoreConfig(BaseSettings):
    """混合检索里的 BM25 参数。仅当 rag.retriever_provider == hybrid 时使用。"""

    bm25_k1: float = Field(
        default=1.5,
        gt=0,
        le=3.0,
        description="BM25 词频饱和参数 k1，经验值 1.2~2.0",
    )
    bm25_b: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="BM25 文档长度归一化参数 b，经验值 0.75",
    )


# ──────────────────────────────────────────────
# 全局配置聚合根


class AppConfig(BaseSettings):
    """全局应用配置 — 所有子模型的聚合根。

    子模型嵌套：Pydantic Settings 自动解析 AI_SERVER__HTTP_PORT → server.http_port。
    YAML 源通过 settings_customise_sources 注册，不在 model_config 中设置 yaml_file。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="AI_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    keyword_store: KeywordStoreConfig = Field(default_factory=KeywordStoreConfig)
    prompt: PromptConfig = Field(default_factory=PromptConfig)
    log: LogConfig = Field(default_factory=LogConfig)

    @model_validator(mode="after")
    def _align_embedding_with_llm(self) -> AppConfig:
        """云厂商共用密钥；智谱补齐 base_url / 默认模型；校验向量维度。"""
        cloud = ("openai", "deepseek", "aliyun", "tencent", "zhipu")

        if self.llm.provider == "zhipu":
            if _needs_zhipu_base(self.llm.base_url):
                self.llm.base_url = _ZHIPU_API_BASE
            if self.llm.chat_model in _STALE_CHAT_MODELS:
                self.llm.chat_model = "glm-4-flash"

        if self.embedding.provider in cloud:
            if not self.embedding.api_key and self.llm.api_key:
                self.embedding.api_key = self.llm.api_key
            if self.embedding.provider == "tencent":
                if (
                    not self.embedding.base_url
                    or "deepseek.com" in self.embedding.base_url
                    or "openai.com" in self.embedding.base_url
                ):
                    if self.llm.provider == "tencent" and self.llm.base_url:
                        self.embedding.base_url = self.llm.base_url
                    else:
                        self.embedding.base_url = "https://tokenhub.tencentmaas.com/v1"
                if self.embedding.model in ("", "text-embedding-3-small", "deepseek-embedding"):
                    self.embedding.model = "kinfra-text-embedding-0.6b"
            if self.embedding.provider == "zhipu":
                if _needs_zhipu_base(self.embedding.base_url):
                    self.embedding.base_url = (
                        self.llm.base_url
                        if self.llm.provider == "zhipu" and self.llm.base_url
                        else _ZHIPU_API_BASE
                    )
                if self.embedding.model in _STALE_EMBED_MODELS:
                    self.embedding.model = "embedding-3"
                if self.embedding.batch_size > 64:
                    self.embedding.batch_size = 64
            if not self.embedding.api_key:
                raise ValueError(
                    f"api_key is required when embedding.provider={self.embedding.provider}. "
                    "Set AI_EMBEDDING__API_KEY or AI_LLM__API_KEY."
                )

        if self.reranker.provider == "zhipu":
            if not self.reranker.api_key and self.llm.provider == "zhipu" and self.llm.api_key:
                self.reranker.api_key = self.llm.api_key
            if _needs_zhipu_base(self.reranker.base_url):
                self.reranker.base_url = (
                    self.llm.base_url
                    if self.llm.provider == "zhipu" and self.llm.base_url
                    else _ZHIPU_API_BASE
                )

        expected = _TENCENT_EMBEDDING_DIMENSIONS.get(self.embedding.model)
        if self.embedding.provider == "tencent" and expected is not None:
            if self.embedding.dimension != expected:
                raise ValueError(
                    f"AI_EMBEDDING__DIMENSION={self.embedding.dimension} does not match "
                    f"model '{self.embedding.model}' ({expected}d). "
                    f"Set AI_EMBEDDING__DIMENSION={expected}."
                )
        if self.embedding.provider == "zhipu":
            if self.embedding.model == "embedding-2" and self.embedding.dimension != 1024:
                raise ValueError(
                    "智谱 embedding-2 固定 1024 维，请设置 AI_EMBEDDING__DIMENSION=1024。"
                )
            if (
                self.embedding.model == "embedding-3"
                and self.embedding.dimension not in _ZHIPU_EMBEDDING_3_DIMS
            ):
                raise ValueError(
                    "智谱 embedding-3 维度只能是 256 / 512 / 1024 / 2048，"
                    f"当前 AI_EMBEDDING__DIMENSION={self.embedding.dimension}。"
                )
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """自定义配置源优先级：env > .env > yaml > 代码默认值。"""
        yaml_source = YamlConfigSettingsSource(
            settings_cls,
            yaml_file=_yaml_config_path(),
            yaml_file_encoding="utf-8",
        )
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            yaml_source,
            file_secret_settings,
        )


@lru_cache
def get_settings() -> AppConfig:
    return AppConfig()


