"""
internal/prompt/ — Prompt 模板模块

职责：
- 从 YAML 文件加载 Prompt 模板
- 支持变量替换（{{ variable }}）
- 构建 LLM messages 列表（system + user）
- 支持 RAG context 注入

设计：
- PromptTemplate：单个模板的数据类，包含 system_prompt / user_template / variables
- PromptManager：模板管理器，负责加载、缓存、获取模板
"""
