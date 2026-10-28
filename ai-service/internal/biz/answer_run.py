"""一次问答的状态、用量和最小事件；不保存模型内部思维链。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

    from internal.biz.materials import AnswerSource, WebSource
    from internal.biz.tool_types import ToolResult
    from internal.biz.tools import ToolScope

RunStatus = Literal[
    "running",
    "completed",
    "insufficient",
    "budget_exhausted",
    "repeated_call",
    "tool_failed",
    "model_failed",
    "invalid_answer",
    "timeout",
    "cancelled",
]


@dataclass(frozen=True)
class RunLimits:
    model_calls: int = 6
    tool_calls: int = 10
    total_tokens: int = 64000
    timeout: float = 120

    def __post_init__(self) -> None:
        if not (
            1 <= self.model_calls <= 6
            and 1 <= self.tool_calls <= 10
            and 1 <= self.total_tokens <= 64000
            and 0 < self.timeout <= 120
        ):
            raise ValueError("运行限制必须在已确认的程序上限内")


@dataclass
class RunUsage:
    model_calls: int = 0
    tool_calls: int = 0
    estimated_input_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # 按每次 max(估算输入, 实际输入) + 实际输出扣减；实际与估算另存。
    charged_tokens: int = 0
    unreported_model_calls: int = 0


@dataclass(frozen=True)
class RunEvent:
    sequence: int
    kind: str
    elapsed_ms: int
    data: dict = field(default_factory=dict)


@dataclass
class AnswerRun:
    scope: ToolScope
    limits: RunLimits
    input_measurement: str
    run_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started: float = field(default_factory=monotonic)
    status: RunStatus = "running"
    stop_reason: str = ""
    error_code: str | None = None
    answer: str = ""
    model: str = ""
    usage: RunUsage = field(default_factory=RunUsage)
    # 仅登记真正进入模型请求的内容；获取但未送模的结果另保存在 tool_results。
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)
    on_event: Callable[[AnswerRun, RunEvent], None] | None = field(default=None, repr=False)

    def emit(self, kind: str, **data) -> None:
        self.events.append(
            RunEvent(
                len(self.events) + 1,
                kind,
                int((monotonic() - self.started) * 1000),
                data,
            )
        )

        if self.on_event is not None:
            self.on_event(self, self.events[-1])

    def finish(self, status: RunStatus, reason: str, error_code: str | None = None) -> None:
        if self.status != "running":
            return
        self.status, self.stop_reason, self.error_code = status, reason, error_code
        self.emit("run_finished", status=status, stop_reason=reason, error_code=error_code)
