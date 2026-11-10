"""一次问答的状态、用量和最小事件；不保存模型内部思维链。"""
# 管理 Agent 运行状态、模型/工具调用次数、Token 用量、预算预留和执行轨迹

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Callable

    from internal.biz.evidence import EvidenceFragment
    from internal.biz.materials import AnswerSource, WebSource
    from internal.biz.tool_types import ToolResult
    from internal.biz.tools import ToolScope
    from internal.biz.web_sources import WebEntry, WebPage

# 问答运行状态的取值范围。
RunStatus = Literal[
    "running",
    "completed",
    "insufficient",
    "budget_exhausted",
    "usage_unknown",
    "tool_failed",
    "model_failed",
    "internal_error",
    "invalid_answer",
    "timeout",
    "cancelled",
]


# 规定一次问答允许使用的调用次数、用量和时间，防止执行循环无限继续。
@dataclass(frozen=True)
class RunLimits:
    model_calls: int = 6       # 整次问答的模型请求次数，包含最终答案生成。
    tool_calls: int = 10       # 整次问答的工具执行次数，包含 finish_research。
    total_tokens: int = 64000  # 所有对话模型调用的实际输入与输出 token 累计上限。
    timeout: float = 120       # 从本次运行开始计时的执行时限，单位秒。

    def __post_init__(self) -> None:
        # 总次数包含取证决策和最终回答；预留回答后至少还能发起一次取证。
        """校验调用次数、累计 token 和运行秒数均在程序允许的范围内。"""
        if type(self.model_calls) is not int or not 2 <= self.model_calls <= 6:
            raise ValueError("模型调用总次数必须为 2 到 6 次，至少包含一次取证决策和一次最终回答")
        if not (
            1 <= self.tool_calls <= 10
            and 1 <= self.total_tokens <= 64000
            and 0 < self.timeout <= 120
        ):
            raise ValueError("运行限制必须在已确认的程序上限内")


# 跟踪单次模型请求从预留预算到结算用量的过程。
@dataclass
class ModelCallUsage:
    run_id: str
    call: int
    phase: Literal["exploration", "final"]
    estimated_input_tokens: int
    output_limit: int
    reservation: int
    content_tokens: int
    protocol_tokens: int
    safety_tokens: int
    state: Literal["reserved", "settled", "unknown"] = "reserved"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    provider_total_tokens: int | None = None
    unknown_reason: str | None = None


@dataclass
class RunUsage:
    """分开记录请求估算、在途预留和已结算用量；实际用量未知时不以估算代替。"""

    model_calls: int = 0
    tool_calls: int = 0
    estimated_input_tokens: int = 0  # 各次请求发出前的输入估算累计值。
    prompt_tokens: int = 0           # 供应商已报告的实际输入 token 累计值。
    completion_tokens: int = 0       # 供应商已报告的实际输出 token 累计值。
    settled_tokens: int = 0          # 已结算的实际输入与输出 token 合计。
    actual_total_tokens: int | None = 0  # 仍有未结算或未知调用时为 None。
    reserved_tokens: int = 0         # 尚未完成调用的输入估算与最大输出预留。
    final_reserved_tokens: int = 0   # 单独为最终回答保留的输入估算与最大输出。
    calls: list[ModelCallUsage] = field(default_factory=list)
    unreported_model_calls: int = 0


# 记录问答执行中的进度节点，供前端推送和本地运行记录共用。
@dataclass(frozen=True)
class RunEvent:
    sequence: int
    kind: str
    elapsed_ms: int
    data: dict = field(default_factory=dict)


@dataclass
class AnswerRun:
    """一次请求的共享状态；同步响应、过程事件和本地运行快照均从这里取值。"""

    scope: ToolScope
    limits: RunLimits
    input_measurement: str
    counting: dict
    token_limits: dict
    run_id: str = field(default_factory=lambda: str(uuid4()))
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started: float = field(default_factory=monotonic)
    status: RunStatus = "running"
    stop_reason: str = ""
    exploration_stop_reason: str = ""
    error_code: str | None = None
    answer: str = ""
    final_response: str = ""  # 仅最终响应，用于本机验收；不发布未通过检查的答案。
    answer_claims: list[dict] = field(default_factory=list)
    evidence_catalog: list[EvidenceFragment] = field(default_factory=list)
    model_requests: list[dict] = field(default_factory=list)
    final_request: list[dict] = field(default_factory=list)
    model: str = ""
    usage: RunUsage = field(default_factory=RunUsage)
    # 仅登记真正进入模型请求的内容；获取但未送模的结果另保存在 tool_results。
    sources: list[AnswerSource | WebSource] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    web_directory: list[WebEntry] = field(default_factory=list)
    web_pages: dict[str, WebPage] = field(default_factory=dict)
    evidence_selection: dict | None = None
    events: list[RunEvent] = field(default_factory=list)
    on_event: Callable[[AnswerRun, RunEvent], None] | None = field(default=None, repr=False)

    def reserve_model(self, estimate: int, output_limit: int, final: bool, *, details: dict) -> int:
        """在模型请求发出前预留本次额度并登记调用，返回从 1 开始的调用编号。

        estimate 是输入估算，output_limit 是最大输出额度；final 标明是否为最终生成。
        存在未结算调用或加上收尾预留后超过总额度时抛出异常，不允许继续请求。
        """
        usage = self.usage
        if usage.unreported_model_calls:
            raise ValueError("不能在用量未知或调用未完成时继续执行")
        call = usage.model_calls + 1
        # 发请求前预留输入估算与最大输出，同时保留独立的最终回答额度。
        reservation = estimate + output_limit
        if final:
            usage.final_reserved_tokens = 0
        if usage.settled_tokens + reservation + usage.final_reserved_tokens > self.limits.total_tokens:
            raise ValueError("调用及收尾预留超过剩余额度")
        if details["content_tokens"] + details["protocol_tokens"] + details["safety_tokens"] != estimate:
            raise ValueError("请求估算明细与总量不一致")
        usage.calls.append(ModelCallUsage(self.run_id, call, "final" if final else "exploration",
                                         estimate, output_limit, reservation, **details))
        usage.model_calls = call
        usage.estimated_input_tokens += estimate
        usage.reserved_tokens += reservation
        usage.unreported_model_calls += 1
        usage.actual_total_tokens = None
        return call

    def settle_model(self, call: int, prompt: int, completion: int, total: int) -> bool:
        """用供应商实际输入、输出和总用量结算指定调用，释放预留并更新累计值。

        call 是 reserve_model 返回的编号。首次结算返回 True；相同用量重复结算返回 False。
        重复结算数据不一致、用量非法或调用已标记未知时抛出异常。
        """
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
        # 返回真实用量后释放本次预留，再按输入与输出的实际消耗累计。
        usage.reserved_tokens -= entry.reservation
        usage.unreported_model_calls -= 1
        usage.prompt_tokens += prompt
        usage.completion_tokens += completion
        usage.settled_tokens += prompt + completion
        usage.actual_total_tokens = usage.settled_tokens if not usage.unreported_model_calls else None
        return True

    def unknown_model(self, call: int, reason: str) -> None:
        """将指定调用标为用量未知，保留未结算计数并发送事件，阻止继续发起模型请求。"""
        entry = self.usage.calls[call - 1]
        if entry.state != "reserved":
            return
        entry.state, entry.unknown_reason = "unknown", reason
        self.usage.reserved_tokens -= entry.reservation
        self.usage.actual_total_tokens = None
        self.emit("model_usage_unknown", call=call, reason=reason)

    def stop_exploration(self, reason: str) -> None:
        """仅锁定首次取证停止原因，运行仍可进入最终回答阶段。"""
        if not self.exploration_stop_reason:
            self.exploration_stop_reason = reason
            self.emit("exploration_stopped", reason=reason)

    def emit(self, kind: str, **data) -> None:
        """追加一个带连续序号和运行耗时的过程事件，并立即调用已注册的 on_event 回调。"""
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
        """终态只设置一次，防止清理或取消逻辑覆盖已经确定的结果。"""
        if self.status != "running":
            return
        self.usage.final_reserved_tokens = 0
        self.status, self.stop_reason, self.error_code = status, reason, error_code
        self.emit("run_finished", status=status, stop_reason=reason, error_code=error_code,
                  exploration_stop_reason=self.exploration_stop_reason)
