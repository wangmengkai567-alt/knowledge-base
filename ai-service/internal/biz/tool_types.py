"""原生工具协议与只读工具结果；参数原文保留到集中校验时。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from internal.biz.materials import AnswerSource, WebSource


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["answered", "insufficient"]
    answer: str = Field(min_length=1)


ANSWER_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "grounded_answer", "strict": True, "schema": AnswerPayload.model_json_schema(),
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
    extract_timeout_seconds: float | None = None


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


@dataclass
class ToolResult:
    call_id: str
    name: str
    status: Literal["success", "empty", "partial", "error"]
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    errors: list[ToolFailure] = field(default_factory=list)
    diagnostics: list[WebDiagnostic] = field(default_factory=list)

    def to_message(self) -> dict:
        sources = []
        for source in self.sources:
            value = {"citation_id": source.citation_id, "source_type": source.source_type,
                     "content": source.content, "truncated": source.truncated}
            if source.source_type == "knowledge_base":
                value.update(document_filename=source.document_filename,
                             document_updated_at=source.document_updated_at)
            else:
                value.update(url=source.url, title=source.title, fetched_at=source.fetched_at)
            sources.append(value)
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "content": json.dumps(
                {
                    "tool": self.name,
                    "status": self.status,
                    "trust": "untrusted_material",
                    "sources": sources,
                    "errors": [asdict(error) for error in self.errors],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
