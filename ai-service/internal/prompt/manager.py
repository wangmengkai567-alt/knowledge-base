"""
internal/prompt/manager.py — PromptManager 模板管理器

职责：
- 启动时从 YAML 目录批量加载模板
- 支持按名称获取模板（带内存缓存）
- 提供 list_templates() 查询可用模板
- 支持热重载（reload）

设计：
- 加载阶段：扫描 template_dir 下所有 *.yaml 文件 → 解析为 PromptTemplate
- 运行阶段：get_template(name) 从缓存返回，无缓存则重新加载
- 错误处理：加载失败记录日志，不阻塞启动（降级到空模板库）
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from internal.prompt.base import PromptTemplate

logger = structlog.get_logger()


class PromptManager:
    """Prompt 模板管理器 — 加载、缓存、获取模板。"""

    def __init__(self, template_dir: str) -> None:
        """初始化 PromptManager。

        参数:
            template_dir: YAML 模板文件目录路径
        """
        self._template_dir = Path(template_dir)
        self._templates: dict[str, PromptTemplate] = {}
        self._loaded = False

    def load_all(self) -> None:
        """加载目录下所有 YAML 模板。

        启动时调用一次，失败不阻塞（记录日志后降级）。
        """
        if not self._template_dir.exists():
            logger.warning(
                "prompt_template_dir_not_found",
                template_dir=str(self._template_dir),
            )
            self._loaded = True
            return

        yaml_files = list(self._template_dir.glob("*.yaml"))
        if not yaml_files:
            logger.warning(
                "no_prompt_templates_found",
                template_dir=str(self._template_dir),
            )
            self._loaded = True
            return

        for yaml_file in yaml_files:
            try:
                template = self._load_single(yaml_file)
                self._templates[template.name] = template
                logger.info(
                    "prompt_template_loaded",
                    name=template.name,
                    variables=template.variables,
                )
            except Exception as e:
                logger.error(
                    "prompt_template_load_failed",
                    file=str(yaml_file),
                    error=str(e),
                )

        self._loaded = True
        logger.info(
            "prompt_templates_loaded",
            count=len(self._templates),
            names=list(self._templates.keys()),
        )

    def get_template(self, name: str) -> PromptTemplate:
        """按名称获取模板。

        参数:
            name: 模板名称（不含 .yaml 后缀）

        返回:
            PromptTemplate 实例

        异常:
            KeyError: 模板不存在
            RuntimeError: 未调用 load_all()
        """
        if not self._loaded:
            raise RuntimeError("PromptManager.load_all() must be called first")

        template = self._templates.get(name)
        if template is None:
            available = list(self._templates.keys())
            raise KeyError(
                f"Prompt template '{name}' not found. Available: {available}"
            )

        return template

    def list_templates(self) -> list[str]:
        """列出所有已加载的模板名称。"""
        return list(self._templates.keys())

    def has_template(self, name: str) -> bool:
        """检查模板是否存在。"""
        return name in self._templates

    def reload(self) -> None:
        """重新加载所有模板。"""
        self._templates.clear()
        self._loaded = False
        self.load_all()
        logger.info("prompt_templates_reloaded", count=len(self._templates))

    def _load_single(self, yaml_file: Path) -> PromptTemplate:
        """加载单个 YAML 模板文件。

        YAML 格式：
        ```yaml
        name: chat
        description: 通用对话模板
        variables:
          - query
        system_prompt: |
          你是一个 AI 助手...
        user_template: |
          {{ query }}
        metadata:
          version: "1.0"
        ```
        """
        with open(yaml_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid template format in {yaml_file}")

        # 必填字段校验
        if "name" not in data:
            raise ValueError(f"Template missing 'name' field: {yaml_file}")
        if "system_prompt" not in data:
            raise ValueError(f"Template missing 'system_prompt' field: {yaml_file}")

        return PromptTemplate(
            name=data["name"],
            system_prompt=data["system_prompt"],
            user_template=data.get("user_template", ""),
            variables=data.get("variables", []),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )
