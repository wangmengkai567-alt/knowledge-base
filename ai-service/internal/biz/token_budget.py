"""模型文本计数、请求估算和具有明确单位的问答预算。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class TokenCounter(Protocol):
    """文本 token 计数接口；调用者不依赖具体分词器实现。"""
    def count_text(self, text: str) -> int:
        """返回输入文本的 token 数量，不包含额外的对话协议标记。"""
        ...


@dataclass(frozen=True)
class TokenLimits:
    """集中保存各层 token 上限和工具结果的字节上限，实例化时检查额度关系。"""
    # 每次模型请求的输入 token 上限，包含历史消息及工具定义。
    input_limit: int = 16000
    # 每次模型请求的输出 token 上限。
    output_limit: int = 2048
    # 一次完整问答允许累计使用的输入与输出 token 总额度。
    total_tokens: int = 64000
    # 模型单次请求可容纳的输入与输出 token 总量。
    context_capacity: int = 1_000_000
    # 工具消息 content 字符串的 token 上限，包含来源和必要结果元数据。
    tool_result_max_tokens: int = 3000
    # 一次工具结果序列化后的最大 UTF-8 字节数，与 token 上限分别检查。
    tool_result_max_bytes: int = 12000
    # 搜索摘要的 token 上限。
    summary_tokens: int = 512
    # 单次网页正文读取窗口的 token 上限。
    page_tokens: int = 2000
    # 网页目录预览文本的 token 上限。
    preview_tokens: int = 64

    def __post_init__(self) -> None:
        """限制每项为正整数且不超过程序上限，并核对摘要、分页、请求及总额度的关系。"""
        for name, value in asdict(self).items():
            if type(value) is not int or not 0 < value <= getattr(TokenLimits, name):
                raise ValueError(f"{name} 必须在已确认的程序上限内")
        if not (self.summary_tokens <= self.tool_result_max_tokens <= self.input_limit
                and 64 <= self.page_tokens <= self.tool_result_max_tokens
                and self.preview_tokens <= self.page_tokens
                and self.output_limit < self.context_capacity):
            raise ValueError("Token 预算的嵌套额度不一致")


@dataclass(frozen=True)
class RequestEstimate:
    """一次模型请求的本地输入估算：内容计数、模板差值、安全余量及三者合计。"""
    # 消息正文、工具参数、工具定义及答案 Schema 分别计数后的 token 合计。
    content_tokens: int
    # 完整模板计数与各内容独立计数的差值，包含特殊标记和分词合并差异，可为负。
    protocol_tokens: int
    # 按比例加固定值计算的额外预留 token 数。
    safety_tokens: int
    # 完整模板计数加安全余量，即 content_tokens + protocol_tokens + safety_tokens。
    estimated_input_tokens: int


@dataclass(frozen=True)
class InputBudget:
    """使用固定模型分词器和对话模板估算完整请求输入；估算值不代表模型实际报告用量。"""
    model: str
    counter: TokenCounter
    # 把消息和工具定义渲染为模型对话模板文本，供完整请求计数。
    render_request: Callable[[list[dict], list[dict]], str]
    # 本地分词器资源的固定版本标识，写入估算元数据。
    tokenizer_ref: str
    limits: TokenLimits = field(default_factory=TokenLimits)
    # 在完整模板计数之上额外预留的比例。
    safety_ratio: float = 0.1
    # 比例余量之外另加的固定预留 token 数。
    fixed_reserve_tokens: int = 256
    measurement: str = field(default="model_tokenizer_estimate", init=False)

    def __post_init__(self) -> None:
        """检查模型、资源标识和请求安全余量配置；比例须为有限数且在 0～1 内。"""
        if self.model != "qwen3.8-flash" or not self.tokenizer_ref:
            raise ValueError("模型与已确认计数资源不匹配")
        if not math.isfinite(self.safety_ratio) or not 0 <= self.safety_ratio <= 1:
            raise ValueError("请求估算余量比例无效")
        if type(self.fixed_reserve_tokens) is not int or not 0 <= self.fixed_reserve_tokens < self.input_limit:
            raise ValueError("请求固定预留无效")

    @property
    def input_limit(self) -> int:
        """单次请求允许输入的 token 上限。"""
        return self.limits.input_limit

    @property
    def output_limit(self) -> int:
        """单次模型回答允许输出的 token 上限。"""
        return self.limits.output_limit

    @property
    def context_capacity(self) -> int:
        """模型输入与输出合计可占用的上下文 token 容量。"""
        return self.limits.context_capacity

    @property
    def metadata(self) -> dict:
        """返回本地估算所用模型、分词器版本、模板投影版本和安全余量，供运行记录追溯。"""
        return {"measurement": self.measurement, "model": self.model,
                "tokenizer_ref": self.tokenizer_ref, "request_projection_version": 1,
                "safety_ratio": self.safety_ratio, "fixed_reserve_tokens": self.fixed_reserve_tokens}

    def input_ceiling(self, output_tokens: int) -> int:
        """先为 output_tokens 预留上下文，再与程序输入上限取较小值。"""
        return min(self.input_limit, self.context_capacity - output_tokens)

    def measure(self, messages: list[dict], tools: list[dict], history: list[dict],
                *, response_format: dict | None = None) -> RequestEstimate:
        """按模型模板估算完整输入，返回内容计数、模板差值、安全余量及合计 token 数。"""
        messages = [*history, *messages]
        content = sum(self.counter.count_text(message.get("content") or "") for message in messages)
        for message in messages:
            for call in message.get("tool_calls", []):
                content += self.counter.count_text(call["function"]["name"])
                content += self.counter.count_text(call["function"]["arguments"])
        if tools:
            content += self.counter.count_text(json.dumps(tools, ensure_ascii=False, separators=(",", ":")))
        base = self.counter.count_text(self.render_request(messages, tools))
        if response_format is not None:
            schema = self.counter.count_text(json.dumps(response_format, ensure_ascii=False, separators=(",", ":")))
            content += schema
            base += schema
        # 残差包括模板标记及相邻内容的 BPE 合并差异，不重复编码 HTTP JSON 转义。
        protocol = base - content
        safety = math.ceil(base * self.safety_ratio) + self.fixed_reserve_tokens
        return RequestEstimate(content, protocol, safety, base + safety)

    def estimate(self, messages: list[dict], tools: list[dict], history: list[dict],
                 *, response_format: dict | None = None) -> int:
        """返回 measure 计算的输入总估算值，包含模板差值与安全余量。"""
        base = self.counter.count_text(self.render_request([*history, *messages], tools))
        if response_format is not None:
            base += self.counter.count_text(json.dumps(response_format, ensure_ascii=False, separators=(",", ":")))
        return base + math.ceil(base * self.safety_ratio) + self.fixed_reserve_tokens


def original_window(text: str, counter: TokenCounter, max_tokens: int, *, start: int = 0,
                    end: int | None = None, code_ranges: tuple[tuple[int, int], ...] | list[tuple[int, int]] = ()) -> tuple[int, int]:
    """在有限原文候选中选连续窗口；所有坐标为字符，不通过 decode 重建原文。"""
    end = len(text) if end is None else min(end, len(text))
    if text[end - 1:end + 1] == "\r\n":
        end -= 1
    for left, right in code_ranges:
        if left < start < right:
            start = left
    if text[start - 1:start + 1] == "\r\n":
        start += 1
    if start >= end or max_tokens <= 0:
        return start, start
    total = counter.count_text(text[start:end])
    if total <= max_tokens:
        return start, end
    length = end - start
    # 比例只用于生成候选，不能代替真实计数或作为单调性假设。
    center = max(1, min(length, length * max_tokens // total))
    lengths = {1, length, center}
    lengths.update(min(length, 2 ** power) for power in range(length.bit_length()))
    lengths.update(max(1, min(length, center * step // 64)) for step in range(1, 129))
    lengths.update(length * step // 64 for step in range(1, 65))
    # 优先尝试句/行和完整代码边界；按既有长度目标采样，避免候选无限增长。
    semantic = set()
    targets = sorted(start + size for size in lengths)
    target = 0
    for match in re.finditer(r"[。！？]|[.!?](?=\s|$)|\r\n|\n", text[start:end]):
        boundary = start + match.end()
        if target < len(targets) and boundary >= targets[target]:
            semantic.add(boundary)
            while target < len(targets) and boundary >= targets[target]:
                target += 1
    code_candidates = 0
    for left, right in code_ranges:
        if start <= left < right <= end:
            semantic.add(right)
            code_candidates += 1
            if code_candidates == 32:
                break
    candidates = set()
    for size in lengths | {right - start for right in semantic}:
        right = start + size
        for left, code_end in code_ranges:
            if left < right < code_end:
                right = left
        if text[right - 1:right + 1] == "\r\n":
            right -= 1
        candidates.add(right)
    for right in sorted(candidates, key=lambda position: (position in semantic, position), reverse=True):
        if right > start and counter.count_text(text[start:right]) <= max_tokens:
            return start, right
    return start, start
