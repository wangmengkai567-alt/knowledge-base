"""原生工具协议与只读工具结果；参数原文保留到集中校验时。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from internal.biz.materials import AnswerSource, WebSource


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
class ToolFailure:
    code: str
    message: str
    url: str | None = None


class ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class WebResult:
    sources: list[WebSource] = field(default_factory=list)
    errors: list[ToolFailure] = field(default_factory=list)


@dataclass
class ToolResult:
    call_id: str
    name: str
    status: Literal["success", "empty", "partial", "error"]
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    errors: list[ToolFailure] = field(default_factory=list)

    def to_message(self) -> dict:
        sources = []
        for source in self.sources:
            value = asdict(source)
            value["content"] = source.content
            # KB 来源不重复发送命中正文；实际文字仅来自裁剪后的 segments。
            if "retrieval_hits" in value:
                value["retrieval_hits"] = [
                    {
                        "chunk_id": hit.chunk_id,
                        "score": hit.score,
                        "score_type": hit.score_type,
                        "score_method": hit.score_method,
                        "scores": [asdict(score) for score in hit.scores],
                    }
                    for hit in source.retrieval_hits
                ]
                for segment in value["segments"]:
                    del segment["content"]
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
            ),
        }
