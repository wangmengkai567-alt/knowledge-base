"""知识库问答协议：问题与知识库范围，返回检索命中及实际送模资料。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.openapi.schemas.retrieval import RetrievalResultDTO, SourceScoreDTO


# 校验前端发来的问答请求，并限定本次可使用的知识库和联网能力。
class RAGQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4000)
    allow_web: bool = Field(default=False, strict=True, description="用户明确开启后才允许联网")
    user_urls: list[str] = Field(default_factory=list, max_length=3, description="用户明确提供的公开网址")
    top_k: int = Field(default=5, ge=1, le=10)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=2048)
    knowledge_base_ids: list[Annotated[int, Field(gt=0)]] | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        description="本次问答允许检索的知识库；缺省为路径中的知识库",
    )


# 向前端返回实际使用的原文片段及其在文档分块中的位置。
class MaterialSegmentDTO(BaseModel):
    chunk_id: int
    position: int
    start: int = Field(description="原分块内字符起点（含）")
    end: int = Field(description="原分块内字符终点（不含）")
    content: str = Field(description="该范围实际送模的原文")
    code_ranges: list[tuple[int, int]] = Field(default_factory=list, description="规范正文映射到原分块的完整代码范围")


# 返回检索命中的评分记录，供前端展示资料为何被选中。
class HitScoreDTO(BaseModel):
    chunk_id: int
    score: float | None
    score_type: str | None
    score_method: str | None
    scores: list[SourceScoreDTO]


# 返回答案使用的知识库资料，支持前端展示引用和定位原文。
class RAGSourceDTO(BaseModel):
    citation_id: int
    source_type: Literal["knowledge_base"]
    content: str = Field(description="完整的实际送模片段，合并与截断后与上下文一致")
    chunk_ids: list[int]
    segments: list[MaterialSegmentDTO]
    truncated: bool
    document_id: int
    document_filename: str
    knowledge_base_id: int
    document_updated_at: str | None
    retrieval_hits: list[HitScoreDTO] = Field(
        description="该资料所含检索命中的评分元数据；已使用原文仅见 content/segments"
    )


# 返回本次发现的网页清单，区分已发现网址和已读取正文。
class WebDirectoryDTO(BaseModel):
    source_id: str
    url: str
    title: str
    origin: Literal["user", "search"]
    discovered_at: str
    read: bool


# 返回实际使用的网页摘要或正文，供前端展示网页引用。
class RAGWebSourceDTO(BaseModel):
    citation_id: int
    source_type: Literal["web_search_summary", "web_page"]
    url: str
    title: str
    fetched_at: str
    content: str
    truncated: bool
    source_id: str
    start: int
    end: int
    total_chars: int
    code_ranges: list[tuple[int, int]]


# 返回单次模型请求的预算及结算情况，便于核对实际消耗。
class ModelCallUsageDTO(BaseModel):
    run_id: str
    call: int
    phase: Literal["exploration", "final"]
    estimated_input_tokens: int
    output_limit: int
    reservation: int
    content_tokens: int
    protocol_tokens: int
    safety_tokens: int
    state: Literal["reserved", "settled", "unknown"]
    prompt_tokens: int | None
    completion_tokens: int | None
    provider_total_tokens: int | None
    unknown_reason: str | None


# 汇总一次问答中全部模型和工具调用的消耗。
class RunUsageDTO(BaseModel):
    model_calls: int
    tool_calls: int
    estimated_input_tokens: int
    prompt_tokens: int
    completion_tokens: int
    settled_tokens: int
    actual_total_tokens: int | None
    reserved_tokens: int
    final_reserved_tokens: int
    calls: list[ModelCallUsageDTO]
    unreported_model_calls: int


# 向前端传递问答进度，同步结果和流式响应使用相同的事件结构。
class RunEventDTO(BaseModel):
    sequence: int
    kind: str
    elapsed_ms: int
    data: dict


# 返回已登记的证据原文，让答案中的证据编号能够追溯到来源。
class EvidenceFragmentDTO(BaseModel):
    evidence_id: str
    run_id: str
    citation_id: int
    kind: Literal["text", "code"]
    content: str
    origin: dict


# 向客户端说明输入估算的方法、模型与分词器版本，以及估算中使用的安全余量。
class TokenCountingDTO(BaseModel):
    measurement: Literal["model_tokenizer_estimate"]
    model: str
    tokenizer_ref: str
    request_projection_version: Literal[1]
    safety_ratio: float
    fixed_reserve_tokens: int


# 统一返回问答终态、答案、引用和用量，供同步接口及流式最终结果使用。
class RAGQueryResponse(BaseModel):
    version: Literal[6] = 6
    tool_contract_version: Literal[3] = 3
    answer_contract_version: Literal[3] = 3
    evidence_catalog: list[EvidenceFragmentDTO]
    run_id: str
    status: str
    stop_reason: str
    exploration_stop_reason: str
    error_code: str | None
    usage: RunUsageDTO
    events: list[RunEventDTO]
    answer: str
    model: str
    web_directory: list[WebDirectoryDTO]
    sources: list[RAGSourceDTO | RAGWebSourceDTO]
    source_count: int
    retrieval_results: list[RetrievalResultDTO]
    counting: TokenCountingDTO
    input_measurement: Literal["model_tokenizer_estimate"]
    estimated_input_tokens: int
    prompt_tokens: int
    completion_tokens: int
