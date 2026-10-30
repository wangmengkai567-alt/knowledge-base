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
    "usage_unknown",
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
class ModelCallUsage:
    run_id: str
    call: int
    phase: Literal["exploration", "final"]
    estimated_input_tokens: int
    output_limit: int
    reservation: int
    state: Literal["reserved", "settled", "unknown"] = "reserved"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    provider_total_tokens: int | None = None
    unknown_reason: str | None = None


@dataclass
class RunUsage:
    model_calls: int = 0
    tool_calls: int = 0
    estimated_input_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    settled_tokens: int = 0
    actual_total_tokens: int | None = 0
    reserved_tokens: int = 0
    final_reserved_tokens: int = 0
    calls: list[ModelCallUsage] = field(default_factory=list)
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
    exploration_stop_reason: str = ""
    error_code: str | None = None
    answer: str = ""
    final_response: str = ""  # 仅最终响应，用于本机验收；不发布未通过检查的答案。
    model: str = ""
    usage: RunUsage = field(default_factory=RunUsage)
    # 仅登记真正进入模型请求的内容；获取但未送模的结果另保存在 tool_results。
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    events: list[RunEvent] = field(default_factory=list)
    on_event: Callable[[AnswerRun, RunEvent], None] | None = field(default=None, repr=False)

    def reserve_model(self, estimate: int, output_limit: int, final: bool) -> int:
        usage = self.usage
        if usage.unreported_model_calls:
            raise ValueError("不能在用量未知或调用未完成时继续执行")
        call = usage.model_calls + 1
        reservation = estimate + output_limit
        if final:
            usage.final_reserved_tokens = 0
        if usage.settled_tokens + reservation + usage.final_reserved_tokens > self.limits.total_tokens:
            raise ValueError("调用及收尾预留超过剩余额度")
        usage.calls.append(ModelCallUsage(self.run_id, call, "final" if final else "exploration",
                                         estimate, output_limit, reservation))
        usage.model_calls = call
        usage.estimated_input_tokens += estimate
        usage.reserved_tokens += reservation
        usage.unreported_model_calls += 1
        usage.actual_total_tokens = None
        return call

    def settle_model(self, call: int, prompt: int, completion: int, total: int) -> bool:
        usage = self.usage
        entry = usage.calls[call - 1]
        if entry.state == "settled":
            if (entry.prompt_tokens, entry.completion_tokens, entry.provider_total_tokens) != (prompt, completion, total):
                raise ValueError("同一调用不能使用不同用量重复结算")
            return False
        if entry.state != "reserved":
            raise ValueError("未知用量不能静默补记")
        if not all(type(value) is int and value >= 0 for value in (prompt, completion, total)):
            raise ValueError("用量必须是非负整数")
        entry.state = "settled"
        entry.prompt_tokens, entry.completion_tokens, entry.provider_total_tokens = prompt, completion, total
        usage.reserved_tokens -= entry.reservation
        usage.unreported_model_calls -= 1
        usage.prompt_tokens += prompt
        usage.completion_tokens += completion
        usage.settled_tokens += prompt + completion
        usage.actual_total_tokens = usage.settled_tokens if not usage.unreported_model_calls else None
        return True

    def unknown_model(self, call: int, reason: str) -> None:
        entry = self.usage.calls[call - 1]
        if entry.state != "reserved":
            return
        entry.state, entry.unknown_reason = "unknown", reason
        self.usage.reserved_tokens -= entry.reservation
        self.usage.actual_total_tokens = None
        self.emit("model_usage_unknown", call=call, reason=reason)

    def stop_exploration(self, reason: str) -> None:
        if not self.exploration_stop_reason:
            self.exploration_stop_reason = reason
            self.emit("exploration_stopped", reason=reason)

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
        self.usage.final_reserved_tokens = 0
        self.status, self.stop_reason, self.error_code = status, reason, error_code
        self.emit("run_finished", status=status, stop_reason=reason, error_code=error_code,
                  exploration_stop_reason=self.exploration_stop_reason)
