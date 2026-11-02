# 引文问题顺序 1：原文保真与代码块边界修复

日期：2026-11-02（中国标准时间）。范围为[原因分析](../research/rag-quality-phase-4-quote-codex-analysis.md)中的顺序 1；没有实施 evidence_id 引用协议，没有修改 F03 范围或模型/工具/时间/词元上限。

## 实现

- `tokenizer.token_spans()` 返回估算 token 在原字符串中的位置；普通正文硬切、overlap 均按字符范围切片，不再从丢弃空白的 token 字符串重建正文。删除旧的合成 overlap 实现。
- 从完整规范正文识别围栏范围，闭围栏必须没有语言标签，支持长围栏、波浪线、CRLF 和未闭合至文末的代码。分块边界及 overlap 起点不能落在代码内部。普通文本仍按原 token 目标拆分，单个超长代码块单独保留；不能通过截短代码满足目标。
- `TextChunker.chunk_spans()` 将同一策略的原范围提供给资料处理器；知识库取证核对规范正文、实际分块、文档归属与版本。旧合成分块明确报 `SOURCE_RECHUNK_REQUIRED`，没有运行时补空白、猜位置或兼容旧文本的分支。
- 代码范围映射回原分块，并随 `MaterialSegment`、API、快照及前端类型保留。抽取只使用登记的完整代码范围，删除按每块初始状态猜围栏的旧逻辑；再次压缩、去重与裁剪保留相应元数据。
- 模型上下文增加片段范围目录，明确不同片段可能不连续；范围说明属于程序元数据，没有拼入真实原文。现有 quote/code 格式及逐字检查继续保留，未实施后续协议改造。

## 验证

[离线验证](evidence/quote-step-1-offline-verification.json)共 115 个不同长度目标与 Unicode/空格/制表符/CRLF/反斜杠文本检查，原文硬切可完全拼回，各块和 overlap 均为连续原文切片；5 类代码边界检查通过。实际保存的 sandbox、harness、安全与 Docker 四份规范正文完成重新分块、真实 MaterialProcessor 取证和预算抽取重放。

F01 的 harness 配置原文从开围栏到闭围栏整体进入抽取单元，CPU、Memory、Disk、Network、Runtime 均完整保留；原分块映射与版本核对通过。旧合成分块被明确拒绝，没有修改历史结果或为旧模型答案修复引文。这里只证明顺序 1 的材料边界修复，不证明 F01/F03 已通过最终问答，模型重抄围栏/反斜杠仍属于后续协议问题。

现有后端 47 项检查仍有同名 10 项历史失败、1 项旧字符截断断言错误，没有新增失败名称；现有评测检查 24 项通过。修改模块 Ruff、Python AST 和 `git diff --check` 通过；未新增或修改测试文件。wire.py 的 4 条既有 lint 问题不在本次逻辑修改内。详见[后端检查](evidence/quote-step-1-existing-checks.json)及[交付检查](evidence/quote-step-1-verification.json)。代码离线验证阶段没有真实问答请求；用户确认数据应用后，新增的三题实测见下节。前端类型检查通过。

## 已确认并完成的现有数据重建

用户已明确确认重建全部 29 篇文档。[只读盘点](evidence/quote-step-1-rechunk-preflight.json)保存的文档版本与正文摘要全部匹配；先保存[重建前的文档与分块快照](evidence/quote-step-1-before-rebuild.json)，随后重新加载修复代码，通过原有 `/retry` 指定 chunk 阶段重新分块及建立索引，没有重新解析原文件，也没有直接修改 SQL。

[完整重建记录](evidence/quote-step-1-rebuild-completed-results.json)确认 29/29 篇已完成，分块从 1153 增至 1167，向量为 1167 条。所有分块逐字对应规范正文的连续切片，代码块边界完整；原文件字节指纹、规范正文和文档版本均未变化。[最终存储审计](evidence/quote-step-1-final-storage-audit.json)确认全部文档 ready，未嵌入分块、缺失向量、孤立向量及归属映射错误均为 0；容器源码与工作区一致。

### 嵌入接口限制与定向恢复

首篇重新分块完成后，原批大小 32 导致前三个批次 HTTP 400，最后 15 条成功，后续文档未执行。[短文本诊断](evidence/quote-step-1-embedding-diagnostic.json)明确返回最大 25；将固定配置设为 25 后，25 条短文本成功、单条最长文档文本也成功，但实际 25 条文档批次仍被服务[明确拒绝并要求最大 20](evidence/quote-step-1-embedding-batch-diagnostic.json)。两类响应的限制不同，均保留真实结果，不把 25 当作已经稳定确认的服务契约。

最终将本机 `.env` 中的 `AI_EMBEDDING__BATCH_SIZE` 固定为 **20**，仅重建 ai-service 容器以应用该配置；其余 Compose 环境值不变，模型、服务地址、嵌入、重排、文档范围及单题执行上限不变。配置检查见[最终配置记录](evidence/quote-step-1-batch-configuration-final.json)。应用代码没有加入自动缩批、供应商切换或失败兜底。

保留首篇已成功的 36 条新向量，通过已有 vector 阶段补齐剩余 75 条，未再替换其分块；其余 28 篇按原计划依次重建。固定批大小 20 后全部完成，没有再次出现该批次错误。两次中止的[首轮记录](evidence/quote-step-1-rebuild-results.json)、[恢复记录](evidence/quote-step-1-rebuild-resumed-results.json)和[输入诊断](evidence/quote-step-1-embedding-input-diagnostic.json)均保留。阶段重建使用原有显式 retry 入口，不等于在问答坏输出后重答，也没有自动重复失败请求。

## 三题真实问答复核

只运行 F01、F03、CROSS01 各一次，没有跑完整 100 题或重复失败答案。[本地两题](../../eval/quote-step-1-rebuilt-local-results.json)、[跨库题](../../eval/quote-step-1-rebuilt-cross-source-results.json)保持原始题干、知识库范围、评分和模型配置。

| 题号 | 终态 | 模型 / 工具 | 实际词元 | 核查结论 |
|---|---|---:|---:|---|
| F01 | completed | 4 / 3 | 13476 | 三份 CPU/内存数据及引用正确；harness 原配置块完整送模；回答缺少明确“不是同一套数字”的结论，原关键词门槛未通过 |
| F03 | insufficient | 5 / 4 | 17363 | Docker 引文不再有反斜杠不一致；范围仍只有知识库 3，安全文档不在范围内，不能算完整回答历史可答题 |
| CROSS01 | completed | 5 / 4 | 17137 | 实际使用知识库 2/3，256m 与 512 MB 各有引用；回答列出双方数值，但没有明确作比较结论 |

三题账本、边界、逐字引文与来源检查通过，总计 14 次对话模型、11 次工具、47976 实际词元，无未知用量；单题最高 17363。模型重抄原文的风险并未因一次通过而被证明消除。F01 的原关键词门槛未通过；F03 继续按原题评分保留未完整作答；CROSS01 的文件名数字启发式疑点经原文核对不构成错误数值，自动结果不修改。

[本地来源审计](evidence/quote-step-1-rebuilt-local-source-audit.json)与[跨库来源审计](evidence/quote-step-1-rebuilt-cross-source-audit.json)共 7 项来源记录通过，另做 54 项代码范围与规范正文核对，均未发现代码块被截断。逐题判断、运行编号、范围、资源及限制见[问答复核](evidence/quote-step-1-rebuilt-question-review.json)。该复核由实施代理完成，不是独立人工评级。

顺序 1 的代码修复与 29 篇数据应用已完成；原文保真、代码结构和实际存储检查通过。未实施 evidence_id 协议，没有为补充比较结论追加生成请求，未宣告完整质量验收通过。嵌入重建未取得独立供应商用量账本，不编造整次重建的 token 或货币费用。
