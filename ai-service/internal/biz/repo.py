"""定义业务层需要的存储、解析、分块、检索与模型调用接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from internal.biz.entity import (
        ChatResult,
        Chunk,
        Document,
        EmbeddingResult,
        KnowledgeBase,
        RetrievalResult,
        RetrievedChunk,
        User,
    )
    from internal.biz.tool_types import WebResult


class UserRepo(ABC):
    """创建、查询用户并更新最近登录时间的存储接口。"""

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
    """文档记录、解析正文和入库步骤状态的存储接口。"""

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
    async def list_all(self) -> list[Document]:
        """列出文档，供启动时核对入库阶段。"""
        ...

    @abstractmethod
    async def update_stages(self, document_id: int, stages: dict) -> None:
        """原子保存一个文档的全部阶段快照。"""
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
    """知识库记录及其文档、分块计数的存储接口。"""

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
    """保存和删除原始文件的接口；save 返回后续解析所需的存储路径。"""

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
    """将正文切分为内容列表或原文字符范围的接口；两种返回形式使用同一切分规则。"""

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """将文本切分为多个块，返回块内容列表。"""
        ...

    @abstractmethod
    def chunk_spans(self, text: str) -> list[tuple[int, int]]:
        """返回同一分块策略的原文字符范围，含原样 overlap。"""
        ...


class ChunkRepo(ABC):
    """保存分块、按文档替换分块集合及更新嵌入状态的存储接口。"""

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
        """原子替换文档的所有分块（先建后删，同一事务）。

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
    """将输入文本列表转换为同序向量列表，并报告模型、维度和用量的接口。"""

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
    """接收查询向量和来源过滤条件，返回候选分块 ID 与评分记录。

    纯向量实现忽略文本查询；混合实现使用 keyword_query 或 query_text 进行关键词检索。
    文本嵌入由调用方提前完成。
    """

    @abstractmethod
    async def retrieve(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
        query_text: str | None = None,
        keyword_query: str | None = None,
        *,
        document_ids: list[int] | None = None,
    ) -> list[RetrievedChunk]:
        """相似度检索。

        参数:
            query_vector: 查询向量，供纯向量检索或混合检索的向量分支使用
            top_k: 返回最相似的 k 个结果
            filter_metadata: 过滤条件（如 knowledge_base_id）
            query_text: 原始查询文本，混合检索的关键词分支使用；
                        纯向量实现可忽略。
            keyword_query: 可选的 BM25 查询（自然语言场景为抽词结果）；
                           未传时混合检索回退 query_text。
            document_ids: 各路召回取 top_k 前限制文档范围；None 不限制，空列表无候选。

        返回:
            RetrievedChunk 列表，包含分数类型、计算方式与各路评分
        """
        ...


class KeywordStore(ABC):
    """按分块 ID 建立、删除及查询关键词索引的接口，索引项携带文档和知识库元数据。"""

    @abstractmethod
    async def existing_ids(self, chunk_ids: list[int], document_id: int, knowledge_base_id: int) -> set[int]:
        """返回当前进程中真实已索引且归属正确的分块。"""
        ...

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
            metadatas: 可选的元数据列表（document_id, knowledge_base_id 等，用于过滤）
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
        *,
        document_ids: list[int] | None = None,
    ) -> list[tuple[int, float]]:
        """关键词相似度搜索，文档范围在评分与截断前生效。

        参数:
            query: 原始查询文本
            document_ids: None 不限制，空列表无候选；与 metadata 条件取交集。
            top_k: 返回最相似的 k 个结果
            filter_metadata: 过滤条件（如 knowledge_base_id）

        返回:
            (chunk_id, score) 列表，按相关性降序
        """
        ...


class LLMProvider(ABC):
    """模型调用接口，返回正文、工具调用、结束原因和供应商用量。

    普通生成、工具决策和结构化答案分别调用对应方法；不支持的能力由实现明确拒绝。
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

    @property
    def supports_native_tools(self) -> bool:
        """当前提供商是否实现原生工具决策；默认不支持，由具体实现声明能力。"""
        return False

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str = "auto",
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> ChatResult:
        """一次原生工具决策；未实现的提供商明确拒绝，不模拟自然语言调用。"""
        from pkg.errors.base import LLMError

        raise LLMError(reason="TOOL_CALLING_UNSUPPORTED", message="当前模型未配置原生工具调用能力")

    async def generate_answer(
        self,
        messages: list[dict],
        *,
        response_format: dict,
        temperature: float | None = None,
        max_tokens: int = 2048,
    ) -> ChatResult:
        """无工具定义的严格最终生成；不支持时明确失败。"""
        from pkg.errors.base import LLMError

        raise LLMError(reason="STRUCTURED_OUTPUT_UNSUPPORTED", message="当前模型未配置严格最终生成能力")

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
    """为检索候选重新评分的接口；返回更新后的排序分，并保留原始评分记录。"""

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
    """按分块 ID 保存、删除向量，并在来源范围内执行相似度检索的接口。"""

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
            metadatas: 可选的元数据列表（document_id, knowledge_base_id 等）
        """
        ...

    @abstractmethod
    async def delete(self, chunk_ids: list[int]) -> None:
        """删除指定分块的向量。"""
        ...

    @abstractmethod
    async def existing_ids(self, chunk_ids: list[int], document_id: int, knowledge_base_id: int) -> set[int]:
        """返回真实存在且文档、知识库元数据正确的向量 ID。"""
        ...

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filter_metadata: dict | None = None,
        *,
        document_ids: list[int] | None = None,
    ) -> list[tuple[int, float]]:
        """相似度搜索，文档范围在评分与截断前生效。

        参数:
            query_vector: 查询向量
            document_ids: None 不限制，空列表无候选；与 metadata 条件取交集。
            top_k: 返回最相似的 k 个结果
            filter_metadata: 过滤条件（如 knowledge_base_id）

        返回:
            (chunk_id, score) 列表，按相似度降序
        """
        ...


class WebProvider(ABC):
    """网页搜索摘要和正文读取是两个明确的只读动作。"""

    @abstractmethod
    async def search(self, query: str, max_results: int) -> WebResult:
        """按公开查询取得最多 max_results 条网页搜索摘要，返回来源及诊断信息。"""
        ...

    @abstractmethod
    async def read(self, urls: list[str]) -> WebResult:
        """读取指定网址列表的正文快照，返回已取得页面及错误，不在此接口中搜索其他网址。"""
        ...
