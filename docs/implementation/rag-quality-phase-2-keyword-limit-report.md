# 第 2 阶段：关键词长度调整

日期：2026-10-30（中国标准时间）。

用户确认将搜索关键词单项上限由 32 放宽至 64 字符，总长度仍为 120 字符。该调整已实施并加载到 `ai-service`；W02 的首轮搜索及网页读取成功，但后续另一个 71 字符短语被本地拒绝，整题仍未通过。

## 调整范围

`SearchKeyword` 参数类型、模型可见 Schema、字段说明及工具描述同步采用 64 字符。数组数量、总长度、类型校验、严格工具模式、知识库与联网授权范围、模型及执行预算不变。方案和评测说明同步更新；之前 32 字符规则下的结果及证据保留，不重写为新规则下的成功。

本次是用户确认后的规则调整，不是执行失败时自动放宽限制。没有截断、拆分或转换模型的非法参数，没有重答、重试或备用执行路径，也没有新增或修改测试文件。已有阶段工作区变更保持保留，见[开始时文件指纹](evidence/rag-quality-phase-2-keyword-limit-inputs.json)。

## 必要验证

- 10 项临时边界核对通过：64 字符接受、65 字符拒绝、总计 120 接受、121 拒绝、原 W02 的 34 / 37 字符参数接受，以及实际工具 Schema 和说明同步。
- 现有只读工具的 8 项检查及修改文件 Ruff 检查通过。
- 仅新增 1 次真实 W02 运行，未跑完整题集，也未追加其他模型探针。

这次 W02 的首轮搜索参数为：

| 短语 | 字符数 |
|---|---:|
| `Tavily Search API documentation search_depth include_answer` | 59 |
| `Tavily API reference parameters` | 31 |

连接后共 91 字符，满足 64 / 120 的新规则。搜索成功，返回 3 条来源；随后两次 `web_read` 均成功，取得 Tavily 官方搜索端点与 Python SDK 页面内容。抓取成功不等于已证明最终答案具备完整证据。

第 4 次模型调用继续搜索，生成单项：

```text
Tavily "search_depth" "basic" "advanced" "include_answer" API reference
```

该短语长 71 字符，总计未超过 120，但超过单项 64 字符上限，准确返回 `keywords.1: string_too_long (max_length=64, actual_length=71)`。本次工具未请求 Tavily，问答明确停止为 `tool_failed / INVALID_TOOL_ARGUMENTS`，未发布最终答案。

运行编号为 `3cb78971-4e83-4e78-bef9-64b30dfa1f55`；共 4 次模型、4 次工具，17.765 秒，实际输入 10789、输出 204，合计 10993。账本、预算、事件顺序和生成协议检查通过，无未知用量。实际执行失败保留，不能把成功搜索和读取网页说成整题成功。

结论：适度放宽确实让此前会被拦截的合理短语得以执行，但这一个样本不能证明整体质量改善。模型仍可能生成超过新上限的参数，严格约束未稳定兑现的问题继续保留；本次不再扩大上限或进入第 3、4 阶段实施。

证据：[原始运行](../../eval/rag-quality-phase-2-keyword-limit-results.json)、[边界核对](evidence/rag-quality-phase-2-keyword-limit-diagnostics.json)、[交付核对](evidence/rag-quality-phase-2-keyword-limit-verification.json)。
