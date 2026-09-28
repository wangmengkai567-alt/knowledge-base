"""
internal/prompt/base.py — PromptTemplate 数据类 + build_messages()

职责：
- 定义 PromptTemplate 数据类（从 YAML 解析后的结构）
- 提供 build_messages() 方法：变量替换 → 构建 LLM messages 列表
- 支持 RAG context 注入（将检索结果拼接到 system prompt 或 user prompt）

变量语法：{{ variable_name }}
内置变量：
- {{ query }}：用户查询
- {{ context }}：RAG 检索到的上下文（Sprint 12 注入）
- {{ history }}：对话历史（Sprint 10 注入）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# 变量匹配正则：{{ variable_name }}
_VARIABLE_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass
class PromptTemplate:
    """单个 Prompt 模板 — 对应一个 YAML 文件。

    属性:
        name: 模板名称（如 chat / rag / summarize）
        system_prompt: system 消息模板（支持 {{ variable }}）
        user_template: user 消息模板（支持 {{ variable }}）
        variables: 模板声明的变量列表（从 YAML 的 variables 字段解析）
        description: 模板描述（可选）
        metadata: 额外元数据（如 version、author）
    """

    name: str
    system_prompt: str
    user_template: str = ""
    variables: list[str] = field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def build_messages(
        self,
        variables: dict[str, str] | None = None,
    ) -> list[dict[str, str]]:
        """构建 LLM messages 列表。

        执行变量替换后返回 [{"role": "system", "content": ...}, {"role": "user", "content": ...}]。

        参数:
            variables: 变量值字典，如 {"query": "什么是向量", "context": "..."}

        返回:
            messages 列表，可直接传给 LLM API

        异常:
            ValueError: 必填变量未提供
        """
        variables = variables or {}

        # 检查必填变量
        required = set(self.variables)
        provided = set(variables.keys())
        missing = required - provided
        if missing:
            # 过滤掉有默认值的变量（context / history 允许为空）
            optional = {"context", "history"}
            missing = missing - optional
            if missing:
                raise ValueError(
                    f"Template '{self.name}' missing required variables: {missing}"
                )

        # 执行变量替换
        system_content = self._render(self.system_prompt, variables)
        messages: list[dict[str, str]] = []

        if system_content.strip():
            messages.append({"role": "system", "content": system_content})

        if self.user_template.strip():
            user_content = self._render(self.user_template, variables)
            messages.append({"role": "user", "content": user_content})

        logger.debug(
            "prompt_template_built",
            template=self.name,
            message_count=len(messages),
        )

        return messages

    def _render(self, template: str, variables: dict[str, str]) -> str:
        """执行变量替换。

        - 已知变量：替换为值
        - 未知变量：保留原样（如 {{ context }} 在 Sprint 12 才注入）
        - 空字符串变量：替换为空（不报错）
        """

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            if var_name in variables:
                return variables[var_name]
            # 未知变量保留原样
            return match.group(0)

        return _VARIABLE_PATTERN.sub(_replace, template)


def build_context_block(chunks: list[dict[str, Any]], max_length: int = 4000) -> str:
    """将检索结果构建为 context 文本块。

    供 RAG 模板使用，将多个 chunk 拼接为带来源标注的文本。

    参数:
        chunks: 检索结果列表，每项包含 content / document_filename / score
        max_length: 最大字符数，超出则截断

    返回:
        格式化的 context 文本
    """
    if not chunks:
        return ""

    parts: list[str] = []
    current_length = 0

    for i, chunk in enumerate(chunks, 1):
        content = chunk.get("content", "")
        filename = chunk.get("document_filename", "unknown")
        score = chunk.get("score", 0)

        block = f"[来源 {i}: {filename} (相关度: {score:.2f})]\n{content}\n"

        if current_length + len(block) > max_length:
            parts.append(f"\n... (已截断，共 {len(chunks)} 条结果，展示前 {i - 1} 条)")
            break

        parts.append(block)
        current_length += len(block)

    return "\n".join(parts)
