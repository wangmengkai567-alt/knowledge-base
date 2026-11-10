"""原生工具协议与只读工具结果；参数原文保留到集中校验时。"""
# 定义最终答案的 JSON Schema，通过 Pydantic 约束输出结构

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

if TYPE_CHECKING:
    from internal.biz.materials import AnswerSource, WebSource
    from internal.biz.web_sources import WebEntry, WebMatch, WebPage


# 标识最终答案的数据格式，供接口响应和本地运行记录保持一致。
ANSWER_CONTRACT_VERSION = 4
# 最终回答的格式与引用提示词。
FINAL_ANSWER_INSTRUCTIONS = """最终只输出 Schema 规定的 status/claims/missing，不增加 answer、总结或备注。
claims 每条是一段事实：text 不写换行、来源引用或代码围栏。evidence_ids 只选择最终资料目录 sources[].evidence 中直接支持本条事实的 E 编号，不能填写 citation_id、原文 quote 或代码字符串。
一条事实可以选择一个或多个相关 E 编号，不同文档、网页以及知识库和网页的证据可以共同或互补支持本条事实。来源有分歧时分别准确表达，不把不同观点写成一致结论，不根据来源数量声称共识。
需要展示代码时，code_evidence_id 选择本条 evidence_ids 中 kind=code 的编号，由程序原样取得代码正文并渲染；text 只简短说明代码用途，不得再抄完整命令、压成一行或省略引号。没有代码展示时 code_evidence_id 为 null。text 中的行内命令也必须在所选证据中存在。
说明中的数值与单位必须采用本条 evidence_ids 所选原文的写法，只允许数字与单位之间的空白差异；不得自行展开单位缩写、改写大小写或换算。原文为 256m 时保留 256m，不能凭常识写成 256MB 或 256MiB；其他未选证据中的数值也不能作为本条依据。
解释概念、原理或规则时，按问题所问的各项分别组织事实条目，每项写清定义、具体要求及必要前提；资料提到的其他类别不属于问题时不要展开。输出前逐项核对是否都有实质解释，某项仅提及名称或前置关系仍算缺失。不能只列名称、重复题目或提供泛泛背景就声称答全。资料只支持部分内容时，保留已有依据的解释，并用 insufficient 和 missing 明确尚缺的主题。每条选择直接支持该解释的相关证据，可由多个片段补充必要前提和具体要求，避免堆叠无关或重复资料。
answered 须有 claims 且 missing=[]，可引用搜索摘要，但只能陈述所选片段支持的内容。摘要与正文均可概括转述，摘要不能冒充逐字正文。用户明确要求读取网页时，未实际读到不能声称已经读取。insufficient 可保留有依据的 claims，missing 仅列具体缺失主题，不能写事实或声称整份原文不存在信息。
资料目录 errors 中的网页读取失败不是正文证据，不能据此声称网页没有相关信息；仅凭已有成功资料作答，必要依据缺失时返回 insufficient 并在 missing 中列出待核实主题。程序会另外显示网页读取失败的提示，无需在 claims 中编造错误说明。
比较题除了分别列值，还须基于所选双方原文明确回答是否相同；不得把不同来源示例数字合成统一标准。"""


# 证据编号类型，如 E1。
EvidenceId = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]*$", max_length=32)]
# 未回答主题的文本及格式限制。
MissingTopic = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"^[^\r\n`]+$")]


# 约束模型生成的单条事实，使正文与支持它的证据编号一起提交。
class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=1, max_length=1600, pattern=r"^[^\r\n]+$")
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=6)
    code_evidence_id: EvidenceId | None


# 校验模型提交的完整答案，拒绝结论与资料缺失情况相互矛盾的输出。
class AnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["answered", "insufficient"]
    claims: list[AnswerClaim] = Field(max_length=8)
    missing: list[MissingTopic] = Field(max_length=5)

    @model_validator(mode="after")
    def validate_outcome(self) -> AnswerPayload:
        # 回答完成和资料不足各有固定条件，不能只改状态标签。
        if self.status == "answered" and (not self.claims or self.missing):
            raise ValueError("answered requires claims and no missing topics")
        if self.status == "insufficient" and not self.missing:
            raise ValueError("insufficient requires missing topics")
        return self


# 将答案结构交给模型接口，要求最终响应符合这份 Schema。
ANSWER_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "grounded_answer_v4", "strict": True, "schema": AnswerPayload.model_json_schema(),
}}


# 保存模型提出的工具调用；参数原文由统一执行器解析和校验。
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def to_dict(self) -> dict:
        # 转成模型接口要求的格式，供后续对话保留这次调用。
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


# 记录网页请求在哪一步成功或失败，供日志和运行记录定位问题。
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


# 统一保存可对外返回的工具错误，避免直接传播底层异常原文。
@dataclass(frozen=True)
class ToolFailure:
    code: str
    message: str
    url: str | None = None
    diagnostics: WebDiagnostic | None = None


# 在工具执行内部传递受控错误，由执行入口转换为 ToolFailure。
class ToolError(Exception):
    def __init__(
        self, code: str, message: str, *, diagnostics: WebDiagnostic | None = None
    ) -> None:
        """保存稳定错误码、可公开说明及可选诊断信息，供工具入口转换成失败结果。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.diagnostics = diagnostics


# 汇总网页服务取得的摘要、正文快照及请求错误，交给工具执行器处理。
@dataclass
class WebResult:
    sources: list[WebSource] = field(default_factory=list)
    errors: list[ToolFailure] = field(default_factory=list)
    diagnostics: list[WebDiagnostic] = field(default_factory=list)
    pages: list[WebPage] = field(default_factory=list)
    budget_diagnostics: list[dict] = field(default_factory=list)


# 统一承载一次工具执行的结果，供问答流程记录并发送回模型。
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
    # 程序记录的工具裁剪原因、实际 token/字节数和保留的原文范围，不作为网页正文证据。
    budget_diagnostics: list[dict] = field(default_factory=list)

    def source_values(self) -> list[dict]:
        # 将内部来源对象转成模型可读取的资料，同时保留可追溯的位置。
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
        # 将工具结果封装成对话消息，通过调用编号与模型此前的请求关联。
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
