"""仅从部署内固定资源加载已确认的 Qwen tokenizer 与请求模板。"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from transformers import Qwen2TokenizerFast

from pkg.errors.base import LLMError

# 分词器来源仓库。
TOKENIZER_REPOSITORY = "Qwen/Qwen3.8-Flash-Next"
# 分词器资源的固定提交版本。
TOKENIZER_REVISION = "812d96e10a0dd82b69d749e68693698cf6c36daa"
# 分词器版本标识：仓库名@提交版本。
TOKENIZER_REF = f"{TOKENIZER_REPOSITORY}@{TOKENIZER_REVISION}"


class ModelTokenCounter:
    """从固定本地资源加载分词器，提供纯文本计数及完整对话模板渲染。"""
    def __init__(self, directory: str, *, model: str, tokenizer_ref: str) -> None:
        """从本地目录加载分词器，校验模型、资源版本和文件完整性。"""
        folder = Path(directory)
        if model != "qwen3.8-flash" or tokenizer_ref != TOKENIZER_REF:
            raise ValueError("模型或 tokenizer_ref 与已确认资源不匹配")
        try:
            manifest = json.loads((folder / "manifest.json").read_bytes())
            if (manifest["model"] != model or manifest["revision"] != TOKENIZER_REVISION
                    or manifest["model_repository"] != TOKENIZER_REPOSITORY):
                raise ValueError("tokenizer 资源清单与配置不匹配")
            resources = {entry["name"]: entry for entry in manifest["resources"]}
            for name in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "LICENSE"):
                data = (folder / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != resources[name]["sha256"]:
                    raise ValueError("tokenizer 资源内容校验失败：" + name)
            self._tokenizer = Qwen2TokenizerFast.from_pretrained(folder, local_files_only=True)
            self._tokenizer.chat_template = (folder / "chat_template.jinja").read_text(encoding="utf-8")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("固定 tokenizer 资源不可用，请核对部署资源与版本") from exc

    def count_text(self, text: str) -> int:
        """统计文本编码后的 token 数，不额外添加句首、句尾等特殊 token。"""
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def render_request(self, messages: list[dict], tools: list[dict]) -> str:
        """用资源中的对话模板渲染消息及工具定义，并追加回答起始标记；关闭思考模式。"""
        projected = deepcopy(messages)
        for message in projected:
            for call in message.get("tool_calls", []):
                try:
                    arguments = json.loads(call["function"]["arguments"])
                    if not isinstance(arguments, dict):
                        raise ValueError("tool arguments must be an object")
                except (ValueError, TypeError) as exc:
                    raise LLMError(reason="INVALID_TOOL_HISTORY", message="工具历史参数无法按模型模板计量") from exc
                call["function"]["arguments"] = arguments
        rendered: object = self._tokenizer.apply_chat_template(
            projected, tools=tools, tokenize=False, add_generation_prompt=True, enable_thinking=False,
        )
        if not isinstance(rendered, str):
            raise ValueError("模型请求模板未生成文本")
        return rendered
