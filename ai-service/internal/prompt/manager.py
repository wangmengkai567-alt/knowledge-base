"""从目录加载 YAML 提示模板，按模板声明的名称缓存并提供查询。

单文件加载失败时记录错误并继续处理其他文件；获取不存在的模板时抛出 KeyError。
模板变化后需显式调用 reload，不会因缓存未命中自动重载。
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
        """扫描目录并缓存成功解析的 YAML 模板；目录缺失或单文件失败时记录日志，不自动生成替代模板。"""
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
        """读取一个 UTF-8 YAML 文件，要求其为映射且包含 name、system_prompt，再构造 PromptTemplate。"""
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
