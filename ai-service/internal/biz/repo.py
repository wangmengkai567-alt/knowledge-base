"""
internal/biz/repo.py — 仓储接口定义（依赖倒置）

biz 层定义接口，data 层实现。
依赖方向：data → biz（而非 biz → data）。
换 ORM 只改 data 层，biz 零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from internal.biz.entity import (
    Chunk,
    ChatResult,
    ChatChunk,
    Document,
    EmbeddingResult,
    KnowledgeBase,
    RetrievalResult,
    User,
)


class UserRepo(ABC):
    """用户仓储接口 — biz 层定义，data 层实现。"""

    @abstractmethod
    async def create(self, user: User) -> User:
        """创建用户，返回带 id 的 User。"""
        ...

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None:
        """根据邮箱查用户，不存在返回 None。"""
        ...

    @abstractmethod
    async def get_by_id(self, user_id: int) -> User | None:
        """根据 ID 查用户，不存在返回 None。"""
        ...

    @abstractmethod
    async def update_last_login(self, user_id: int, login_at: datetime) -> None:
        """更新最近登录时间。"""
        ...


class DocumentRepo(ABC):
    """文档仓储接口 — biz 层定义，data 层实现。"""

    @abstractmethod
    async def create(self, document: Document) -> Document:
        """创建文档记录，返回带 id 的 Document。"""
        ...

    @abstractmethod
    async def get_by_id(self, document_id: int) -> Document | None:
        """根据 ID 查文档，不存在返回 None。"""
        ...

    @abstractmethod
    async def get_by_ids(self, document_ids: list[int]) -> list[Document]:
        """根据 ID 列表批量查文档，返回存在的文档列表。"""
        ...

    @abstractmethod
    async def list_by_kb(self, knowledge_base_id: int) -> list[Document]:
        """列出知识库下所有文档。"""
        ...

    @abstractmethod
    async def list_distinct_kb_ids(self) -> list[int]:
        """列出 documents 表中出现过的 knowledge_base_id（用于回填知识库表）。"""
        ...

    @abstractmethod
    async def delete(self, document_id: int) -> bool:
        """删除文档记录，返回是否删除成功。"""
        ...

    @abstractmethod
    async def update_status(
        self, document_id: int, status: int, error_message: str | None = None
    ) -> None:
        """更新文档状态（解析进度）。"""
        ...

    @abstractmethod
    async def update_parsed_content(self, document_id: int, parsed_content: str) -> None:
        """解析成功后保存纯文本内容，同时将状态置为 PARSED。"""
        ...


class KnowledgeBaseRepo(ABC):
    """知识库仓储接口 — biz 层定义，data 层实现。"""

    @abstractmethod
    async def create(self, kb: KnowledgeBase) -> KnowledgeBase:
        """创建知识库。若 kb.id 已指定则按该 id 插入（回填存量文档）。"""
        ...

    @abstractmethod
    async def get_by_id(self, kb_id: int) -> KnowledgeBase | None:
        """按 ID 查询，不存在返回 None。"""
        ...

    @abstractmethod
    async def list_all(self) -> list[KnowledgeBase]:
        """列出全部知识库（含文档数 / 分块数聚合）。"""
        ...

    @abstractmethod
    async def list_ids(self) -> list[int]:
        """列出已有知识库 ID。"""
        ...

    @abstractmethod
    async def update(self, kb: KnowledgeBase) -> KnowledgeBase:
        """更新名称 / 描述 / 图标等可变字段。"""
        ...

    @abstractmethod
    async def delete(self, kb_id: int) -> bool:
        """删除知识库行，返回是否删除成功。"""
        ...


class FileStorageRepo(ABC):
    """文件存储仓储接口 — biz 层定义，data 层实现。

    抽象文件存储，支持本地/ S3/ OSS 等实现。
    """

    @abstractmethod
    async def save(self, filename: str, content: bytes) -> str:
        """保存文件，返回存储路径。"""
        ...

    @abstractmethod
    async def delete(self, file_path: str) -> None:
        """删除文件。"""
        ...


class DocumentParser(ABC):
    """文档解析器接口 — biz 层定义，data 层实现。

    不同文件类型有不同实现（PDF / Markdown / Text）。
    接口返回纯文本 str，实现内部用 asyncio.to_thread 包装阻塞调用。
    """

    @abstractmethod
    async def parse(self, file_path: str) -> str:
        """解析文件，返回纯文本内容。"""
        ...


class TextChunker(ABC):
    """文本分块器接口 — biz 层定义，data 层实现。

    将长文本切分为多个 chunk，支持不同分块策略（固定大小 / 语义等）。
    """

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """将文本切分为多个块，返回块内容列表。"""
        ...


class ChunkRepo(ABC):
    """分块仓储接口 — biz 层定义，data 层实现。"""

    @abstractmethod
    async def create_batch(self, chunks: list[Chunk]) -> list[Chunk]:
        """批量创建分块记录，返回带 id 的 Chunk 列表。"""
        ...

    @abstractmethod
    async def list_by_document(self, document_id: int) -> list[Chunk]:
        """列出文档下所有分块，按 position 排序。"""
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: int) -> int:
        """删除文档下所有分块，返回删除数量。"""
        ...

    @abstractmethod
    async def replace_for_document(
        self, document_id: int, chunks: list[Chunk]
    ) -> list[Chunk]:
        """原子替换文档的所有分块（先删后建，同一事务）。

        防止 delete 成功但 create 失败导致数据丢失。
        """
        ...

    @abstractmethod
    async def update_embedding_status(
        self, chunk_ids: list[int], status: int
    ) -> int:
        """批量更新分块的嵌入状态，返回更新数量。"""
        ...

    @abstractmethod
    async def get_by_ids(self, chunk_ids: list[int]) -> list[Chunk]:
        """根据 ID 列表批量查分块，返回存在的分块列表。"""
        ...

    @abstractmethod
    async def list_all(self) -> list[Chunk]:
        """列出全部分块（用于启动时重建关键词索引等运维场景）。"""
        ...


class EmbeddingProvider(ABC):
    """文本嵌入提供商接口 — biz 层定义，data 层实现。

    将文本转换为向量，支持不同提供商（OpenAI / DeepSeek / Ollama）。
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> EmbeddingResult:
        """将文本列表转换为向量列表。

        参数:
            texts: 待嵌入的文本列表

        返回:
            EmbeddingResult，包含向量列表 + 模型元信息 + token 用量（真实或估算）。
            vectors 顺序与输入一致。

        异常:
            APIError: 嵌入失败
        """
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """返回向量维度。"""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回模型名称。"""
        ...


class Retriever(ABC):
    """检索器接口 — biz 层定义，data 层实现。

    接收已嵌入的查询向量，返回相似度排序的分块 ID + 分数。
    不负责嵌入文本（那是 EmbeddingProvider 的职责）。

    Sprint 18：`query_text` 是可选参数，纯向量检索器忽略；
    混合检索器（HybridRetriever）用它执行关键词分支（BM25）。
    这样保留单一接口，避免调用方针对不同实现分支判断。
    """

    @abstractmethod
    async def retrieve(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
        query_text: str | None = None,
        keyword_query: str | None = None,
    ) -> list[tuple[int, float]]:
        """相似度检索。

        参数:
            query_vector: 查询向量（关键词分支不使用，但保留必填以简化调用）
            top_k: 返回最相似的 k 个结果
            filter_metadata: 过滤条件（如 knowledge_base_id）
            query_text: 原始查询文本，混合检索的关键词分支使用；
                        纯向量实现可忽略。
            keyword_query: 可选的 BM25 查询（自然语言场景为抽词结果）；
                           未传时混合检索回退 query_text。

        返回:
            (chunk_id, score) 列表，按相似度降序
        """
        ...


class KeywordStore(ABC):
    """关键词索引存储接口 — biz 层定义，data 层实现（Sprint 18）。

    与 VectorStore 对称设计：负责关键词全文检索（BM25 等）的存储与查询。
    分块创建/删除时由 EmbeddingBiz 同步维护索引。
    """

    @abstractmethod
    async def index(
        self,
        chunk_ids: list[int],
        texts: list[str],
        metadatas: list[dict] | None = None,
    ) -> None:
        """建立/更新分块的关键词索引。

        参数:
            chunk_ids: 分块 ID 列表
            texts: 分块原文（用于分词与词频统计）
            metadatas: 可选的元数据列表（document_id, kb_id 等，用于过滤）
        """
        ...

    @abstractmethod
    async def delete(self, chunk_ids: list[int]) -> None:
        """删除指定分块的关键词索引。"""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 10,
        filter_metadata: dict | None = None,
    ) -> list[tuple[int, float]]:
        """关键词相似度搜索。

        参数:
            query: 原始查询文本
            top_k: 返回最相似的 k 个结果
            filter_metadata: 过滤条件（如 knowledge_base_id）

        返回:
            (chunk_id, score) 列表，按相关性降序
        """
        ...


class LLMProvider(ABC):
    """LLM 推理提供商接口 — biz 层定义，data 层实现。

    接收 messages 列表，返回 LLM 生成内容。
    支持 OpenAI / DeepSeek / Ollama 等兼容 OpenAI API 的提供商。
    支持非流式 generate() 和流式 generate_stream()。
    """

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """调用 LLM 生成回复。

        参数:
            messages: LLM messages 列表，如 [{"role": "system", "content": ...}]
            temperature: 生成温度（覆盖默认值）
            max_tokens: 最大生成 token 数（覆盖默认值）

        返回:
            ChatResult 值对象

        异常:
            APIError: LLM 调用失败
        """
        ...

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """调用 LLM 生成回复（流式）。

        逐步 yield ChatChunk，每个 chunk 包含增量文本 delta。
        最后一个 chunk 的 finish_reason 为 "stop"。

        参数:
            messages: LLM messages 列表
            temperature: 生成温度（覆盖默认值）
            max_tokens: 最大生成 token 数（覆盖默认值）

        产出:
            ChatChunk 值对象（delta + model + finish_reason）

        异常:
            APIError: LLM 调用失败
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回当前使用的模型名称。"""
        ...

    @property
    def is_mock(self) -> bool:
        """是否为伪模型（不产生真实推理结果）。

        默认 False；MemoryLLM 覆盖为 True，便于上层识别伪模型。
        """
        return False


class Reranker(ABC):
    """重排序器接口 — biz 层定义，data 层实现。

    对检索结果进行二次排序，提高相关性。
    支持 Cross-Encoder 模型 / 关键词重叠 / 外部 API 等实现。
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """对检索结果重排序。

        参数:
            query: 用户查询文本
            results: 待排序的检索结果列表
            top_k: 返回前 k 个结果，None 表示返回全部（已重排序）

        返回:
            按相关性重排序后的 RetrievalResult 列表（score 已更新）

        异常:
            APIError: 重排序服务调用失败
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """返回重排序模型名称。"""
        ...


class VectorStore(ABC):
    """向量存储接口 — biz 层定义，data 层实现。

    负责向量的存储与相似度检索。
    """

    @abstractmethod
    async def upsert(
        self,
        chunk_ids: list[int],
        vectors: list[list[float]],
        metadatas: list[dict] | None = None,
    ) -> None:
        """插入或更新向量。

        参数:
            chunk_ids: 分块 ID 列表
            vectors: 对应的向量列表
            metadatas: 可选的元数据列表（document_id, kb_id 等）
        """
        ...

    @abstractmethod
    async def delete(self, chunk_ids: list[int]) -> None:
        """删除指定分块的向量。"""
        ...

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
    ) -> list[tuple[int, float]]:
        """相似度搜索。

        参数:
            query_vector: 查询向量
            top_k: 返回最相似的 k 个结果
            filter_metadata: 过滤条件（如 knowledge_base_id）

        返回:
            (chunk_id, score) 列表，按相似度降序
        """
        ...
