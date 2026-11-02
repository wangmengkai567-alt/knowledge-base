"""原生工具协议与只读工具结果；参数原文保留到集中校验时。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from internal.biz.materials import AnswerSource, WebSource
    from internal.biz.web_sources import WebEntry, WebMatch, WebPage


ANSWER_CONTRACT_VERSION = 2
FINAL_ANSWER_INSTRUCTIONS = """最终只输出 Schema 规定的 status/claims/missing，不增加 answer、总结或备注。
claims 每条是一段事实：text 不写换行、引用或代码围栏。每份 evidence 提供已送模且已选定的 citation_id 及连续原样 quote；摘录须直接支持本条事实，不能只与主题相关。
scope=source 只归于一个原始来源，可引用它的多个片段。多来源对照优先拆成各自的事实；只有双方都直接支持同一结论才用 shared，并提供至少两个独立来源的摘录；同页两段不算两来源。禁止用“隐含”“都说明”等补出共识。
完整代码放 code，须在本条非摘要摘录中原样出现；不用代码时 code=null。answered 的事实不得引用搜索摘要。
answered 须有 claims 且 missing=[]；insufficient 可保留有依据的 claims，missing 写尚缺的来源与具体问题主题，不能写事实或对整份原文的断言。缺少一方依据时说明本次片段未覆盖，不能宣称双方一致。"""


class AnswerEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    citation_id: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["source", "shared"]
    text: str = Field(min_length=1, max_length=1600, pattern=r"^[^\r\n]+$")
    evidence: list[AnswerEvidence] = Field(min_length=1, max_length=6)
    code: str | None = Field(max_length=4000)


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["answered", "insufficient"]
    claims: list[AnswerClaim] = Field(max_length=8)
    missing: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_outcome(self) -> AnswerPayload:
        if self.status == "answered" and (not self.claims or self.missing):
            raise ValueError("answered requires claims and no missing topics")
        if self.status == "insufficient" and not self.missing:
            raise ValueError("insufficient requires missing topics")
        return self


ANSWER_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "grounded_answer_v2", "strict": True, "schema": AnswerPayload.model_json_schema(),
}}


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class WebDiagnostic:
    endpoint: str
    phase: str
    category: str
    elapsed_seconds: float
    http_status: int | None = None
    request_id: str | None = None
    exception_type: str | None = None
    timeout_layer: str | None = None
    client_timeout_seconds: float | None = None
    connected_ip: str | None = None
    redirect_count: int = 0
    final_url: str | None = None


@dataclass(frozen=True)
class ToolFailure:
    code: str
    message: str
    url: str | None = None
    diagnostics: WebDiagnostic | None = None


class ToolError(Exception):
    def __init__(
        self, code: str, message: str, *, diagnostics: WebDiagnostic | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = diagnostics


@dataclass
class WebResult:
    sources: list[WebSource] = field(default_factory=list)
    errors: list[ToolFailure] = field(default_factory=list)
    diagnostics: list[WebDiagnostic] = field(default_factory=list)
    pages: list[WebPage] = field(default_factory=list)


@dataclass
class ToolResult:
    call_id: str
    name: str
    status: Literal["success", "empty", "partial", "error"]
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    errors: list[ToolFailure] = field(default_factory=list)
    diagnostics: list[WebDiagnostic] = field(default_factory=list)
    directory: list[WebEntry] = field(default_factory=list)
    matches: list[WebMatch] = field(default_factory=list)
    selection: dict | None = None

    def source_values(self) -> list[dict]:
        sources = []
        for source in self.sources:
            value = {"citation_id": source.citation_id, "source_type": source.source_type,
                     "content": source.content, "truncated": source.truncated}
            if source.source_type == "knowledge_base":
                value.update(document_filename=source.document_filename,
                             document_id=source.document_id,
                             knowledge_base_id=source.knowledge_base_id,
                             document_updated_at=source.document_updated_at)
            else:
                value.update(url=source.url, title=source.title, fetched_at=source.fetched_at,
                             source_id=source.source_id, start=source.start, end=source.end,
                             total_chars=source.total_chars)
            sources.append(value)
        return sources

    def to_message(self) -> dict:
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "content": json.dumps(
                {
                    "tool": self.name,
                    "status": self.status,
                    "trust": "untrusted_material",
                    "sources": self.source_values(),
                    "directory": [asdict(entry) for entry in self.directory],
                    "matches": self.matches,
                    "selection": self.selection,
                    "errors": [asdict(error) for error in self.errors],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
