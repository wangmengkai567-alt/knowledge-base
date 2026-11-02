"""原生工具协议与只读工具结果；参数原文保留到集中校验时。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

if TYPE_CHECKING:
    from internal.biz.materials import AnswerSource, WebSource
    from internal.biz.web_sources import WebEntry, WebMatch, WebPage


ANSWER_CONTRACT_VERSION = 3
FINAL_ANSWER_INSTRUCTIONS = """最终只输出 Schema 规定的 status/claims/missing，不增加 answer、总结或备注。
claims 每条是一段事实：text 不写换行、来源引用或代码围栏。evidence_ids 只选择最终资料目录 sources[].evidence 中直接支持本条事实的 E 编号，不能填写 citation_id、原文 quote 或代码字符串。
scope=source 只归于一个原始来源，可选择它的多个证据。多来源对照分别有据；shared 必须选择至少两个独立来源直接支持的证据，同一文档/网页的两段不是两来源，不能补写共识。
需要展示代码时，code_evidence_id 选择本条 evidence_ids 中 kind=code 的编号，由程序原样取得代码正文并渲染；text 只简短说明代码用途，不得再抄完整命令、压成一行或省略引号。没有代码展示时 code_evidence_id 为 null。text 中的行内命令也必须在所选非摘要原文中存在。
说明中的数值与单位必须采用本条 evidence_ids 所选原文的写法，只允许数字与单位之间的空白差异；不得自行展开单位缩写、改写大小写或换算。原文为 256m 时保留 256m，不能凭常识写成 256MB 或 256MiB；其他未选证据中的数值也不能作为本条依据。
answered 须有 claims 且 missing=[]，不得引用搜索摘要。insufficient 可保留有依据的 claims，missing 仅列具体缺失主题，不能写事实或声称整份原文不存在信息。
比较题除了分别列值，还须基于所选双方原文明确回答是否相同；不得把不同来源示例数字合成统一标准。"""


EvidenceId = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]*$", max_length=32)]
MissingTopic = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"^[^\r\n`]+$")]


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["source", "shared"]
    text: str = Field(min_length=1, max_length=1600, pattern=r"^[^\r\n]+$")
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=6)
    code_evidence_id: EvidenceId | None


class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["answered", "insufficient"]
    claims: list[AnswerClaim] = Field(max_length=8)
    missing: list[MissingTopic] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_outcome(self) -> AnswerPayload:
        if self.status == "answered" and (not self.claims or self.missing):
            raise ValueError("answered requires claims and no missing topics")
        if self.status == "insufficient" and not self.missing:
            raise ValueError("insufficient requires missing topics")
        return self


ANSWER_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "grounded_answer_v3", "strict": True, "schema": AnswerPayload.model_json_schema(),
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
                # 位置目录属于程序元数据，不拼入原文；不同条目不表示原文连续。
                offset, excerpts, codes = 0, [], []
                for segment in source.segments:
                    excerpts.append({"start": offset, "end": offset + len(segment.content)})
                    codes.extend([[offset + left - segment.start, offset + right - segment.start]
                                  for left, right in segment.code_ranges])
                    offset += len(segment.content) + 1
                value.update(document_filename=source.document_filename,
                             document_id=source.document_id,
                             knowledge_base_id=source.knowledge_base_id,
                             document_updated_at=source.document_updated_at,
                             excerpt_ranges=excerpts, code_ranges=codes,
                             excerpt_note="不同条目之间可能省略原文；逐字摘录不可跨越非连续片段或补写围栏。")
            else:
                value.update(url=source.url, title=source.title, fetched_at=source.fetched_at,
                             source_id=source.source_id, start=source.start, end=source.end,
                             total_chars=source.total_chars,
                             code_ranges=[[left - source.start, right - source.start]
                                          for left, right in source.code_ranges])
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
