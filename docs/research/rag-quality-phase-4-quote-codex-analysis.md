# F01/F03 引文失败的具体原因与 Codex 源码对照

日期：2026-11-02（中国标准时间）。本轮只做分析：未修改业务代码、配置、测试、评分或旧结果，未追加真实问答、模型或联网问答请求。保留上一轮未提交改动，仅新增本笔记。外部调研只读取官方 GitHub 源码。

## 1. 结论与对上一轮归因的补充

F01 不能只归因于模型摘录不忠实：当前知识库原文抽取还有一个可确定复现的围栏识别缺陷，先把完整配置代码块拆散；模型随后又把不连续的文本整理成看似完整的引用。F03 是模型返回的已解码字符串真的多出反斜杠，此外原题仍受历史单库范围限制。两题的 `unsupported_quote` 拦截均符合现行协议，删除校验无法解决原因。

Codex 提供可借鉴的“位置引用、原文与归属共同保留、程序读取和渲染”机制，但本次有范围核查没有找到可直接移植的 RAG 逐字/事实语义校验器。建议先修原文单元边界，再让模型选择已送模的证据编号，由程序取得和渲染原文，减少模型重抄代码、围栏和反斜杠的失败面。这是对本项目的设计推导，不是 Codex 已提供完整 RAG 方案。

## 2. 离线复现与排除项

使用[阶段 4 本地结果](../../eval/rag-quality-phase-4-acceptance-local-results.json)和[来源审计快照](../implementation/evidence/rag-quality-phase-4-acceptance-local-audit.json)，直接调用当前 `AnswerPayload.model_validate_json()` 与 `render_answer()`。两题各重放两次，均确定返回 `unsupported_quote`；最小化后 F01 只留第 2 条 claim、F03 只留第 1 条 claim，仍然失败。所有诊断均通过 `uv run --project ai-service --no-sync python -` 执行，没有新增或修改测试文件。

排序检查的三个假设及结果：

| 假设 | 可证伪信号 | 实际结果 |
|---|---|---|
| 模型改变了摘录内容 | JSON 解码后的 quote 不匹配实际原文 | 两题均确认 |
| 压缩与校验登记不同导致误拒 | quote 匹配最终压缩片段，却不匹配 run.sources | 两题失败引文均不匹配最终片段，排除此项为本次直接原因 |
| 只是 JSON 表示层重复转义 | 解码一次后反斜杠恢复为原文数量 | F03 解码后仍为两条反斜杠，原文只有一条，排除此解释 |

F01 实际用量 12532、模型 4 次、工具 3 次，耗时 11.437 秒；F03 为 12843、4 次、3 次，9.859 秒。两题都没有耗尽 64000 实际词元、6 次模型或 120 秒限制，不能归因于预算不足。停止发布的位置在[逐字检查](../../ai-service/internal/biz/answer_rendering.py:54)，最终状态由[发布入口](../../ai-service/internal/biz/rag.py:653)保留为 invalid_answer。

## 3. F01：本地边界识别缺陷与模型改写叠加

### 3.1 原文代码块本来完整

审计保存的 `07.agent-harness.md` 规范正文，以及分块 2808，都含有同一完整配置块：

````text
```text
CPU：2 cores
Memory：4GB
Disk：10GB
Network：禁止
Runtime：10min
```
````

这不是原文缺失、CPU/内存未检索到，或来源版本已经变化。原分块中此围栏范围为 `[487,556)`。

### 3.2 当前抽取器把围栏状态识别反了

[ `_text_ranges()` ](../../ai-service/internal/biz/materials.py:293)对每个分块独立从“当前不在代码块内”开始，只识别行首围栏。分块 2808 的重叠前缀含有 `##2.ToolRegistry…```text`，这不是抽取器认可的行首开围栏。后续真正的闭围栏被当作开围栏，之后的开闭配对错位。

实测得到的单元包括：

```text
[433,495)  前一个围栏 + sandbox 标题 + 当前开围栏
[495,507)  CPU：2 cores
[507,518)  Memory：4GB
[518,528)  Disk：10GB
[528,539)  Network：禁止
[539,553)  Runtime：10min
[553,592)  当前闭围栏 + 后续 Permission 标题
```

`WHOLE_CODE_UNIT_PRESERVED=False`。将输入最小化为“含行内围栏的前缀 + 该块闭围栏 + 下一份配置代码块”，仍然复现；从同一分块的干净 `### 4.sandbox` 章节起点开始解析，则 `WHOLE_CODE_UNIT_PRESERVED=True`。这证明围栏状态依赖错误的分块起始上下文。

因此，“完整代码块作为不可分割单元”的保证对该知识库路径没有成立。抽取优先保留了 CPU、Memory 行，却把 Disk/Network/Runtime 与真正闭围栏分开。模型看到的合并正文没有明确标出这些不连续边界；[AnswerSource.content](../../ai-service/internal/biz/materials.py:52)用普通换行拼接，工具[送模字段](../../ai-service/internal/biz/tool_types.py:129)只给合并 content，没有逐片段编号或边界。

进一步追到前缀的生成路径：[RecursiveCharacterChunker._with_overlap()](../../ai-service/internal/data/chunker/recursive.py:81)用 `"".join(tokenize(previous)[-overlap:])` 重建尾部，再直接 `tail + chunk`；[tokenize()](../../ai-service/internal/biz/tokenizer.py:8)不保留空白字符。因此 overlap 前缀会丢掉空格/换行，并与下一块直接相连，能够产生本题看到的行内围栏。这是已有分块表示与第 4 阶段围栏假设叠加的问题；不能把它称为原始 Markdown 本身缺闭围栏。

这是上一轮实施记录需要补充的本地实现缺陷。来源审计验证了每段对应已保存分块，但它既没有验证分块的合成 overlap 与规范原文逐字一致，也没有验证代码块未被拆分，不能用“分块字符范围全部匹配”证明规范原文及代码结构完整。

### 3.3 模型再补上了错误位置的闭围栏

模型生成的 harness quote 在 `Memory：4GB` 下一行立即出现闭围栏，而原文该位置后面还有 Disk/Network/Runtime。删除模型 quote 末尾的 `\n``` `（实际为换行加三个反引号，不含尾部空格）后，剩余内容能匹配已送模资料；保留它则不能。

这是引用文本被重新组织，不能作为连续逐字原文发布。正确 CPU/内存数值出现在草稿中，不等于这次回答已经通过来源协议。修边界能消除这一已知诱因，但不能仅凭离线重放承诺模型以后绝不会改写引用。

## 4. F03：实际转义错误，另有独立的范围限制

### 4.1 解码后的反斜杠数量确实改变

原文 Docker 命令的 `--memory 256m` 和 `--cpus 1` 行末，各有 **1 个**反斜杠。模型的最终 JSON 合法，通过一次正常 JSON 解码后，对应两行仍各有 **2 个**反斜杠。

```text
quote 解码后的反斜杠计数：[2, 2]
原文命令行末的反斜杠计数：1
quote in 原送模原文：False
quote in 最终压缩片段：False
```

因此不是查看日志时看到 `\\` 就误以为多了一个，也没有证据表明应用的 `json.dumps()` 或 Pydantic 多解码了一次；当前失败内容来自模型输出字符串本身。不能用全局 unescape 把坏 quote 改到能匹配，真实代码中的多个反斜杠可能有语义。

### 4.2 修好引文仍不能完整回答历史 F03

本次保存的实际范围只有知识库 `[3]`，允许联网为 false；Docker 文档在此范围内，安全文档属于知识库 2。模型最终 payload 原本就声明 insufficient。即使该 quote 合法，也只应发布有根据的部分结论和缺失说明，不能变成完整双库回答。

保留历史 F03 的请求、评分和结果；跨库执行能力单独使用明确授权 `[2,3]` 的 CROSS01，其阶段 4 样本已完成双方数值和引用核对。不能静默扩大 F03 范围或改写其历史评分。

## 5. 当前协议为什么反复出现这种机械失败

[AnswerEvidence.quote](../../ai-service/internal/biz/tool_types.py:23)要求模型输出最长 4000 字符的任意摘录字符串；代码还可能在 quote 与 code 中各复制一次。JSON Schema 能约束类型、长度、字段，不能表达“该字符串必须恰好是本次指定原文的子串”。最终检查使用 `.find(quote)`，因此复制、围栏整理、换行或反斜杠的任何改变都会导致整份答案失败。

这种门禁对错误引用是必要的，但把精确搬运原文交给生成模型，使生成质量和机械复制能力绑在一起。仅重复“逐字复制”的提示，无法消除这个结构上的失败面。

## 6. Codex 已有机制及可借鉴边界

核查固定提交：[a09812981de703f18f38015cd5d500ab373f616c](https://github.com/openai/codex/commit/a09812981de703f18f38015cd5d500ab373f616c)。以下链接均固定到该提交，不复用旧笔记的 main 行号。

| 官方实现 | 能借鉴什么 | 不能据此声称什么 |
|---|---|---|
| [MemoryCitationEntry](https://github.com/openai/codex/blob/a09812981de703f18f38015cd5d500ab373f616c/codex-rs/protocol/src/memory_citation.rs#L13-L20) | 用 path、起止行和 note 定位，无需重抄整段 quote | 该结构不自动证明路径存在、版本一致、资料已送模或断言正确 |
| [本地 memory read](https://github.com/openai/codex/blob/a09812981de703f18f38015cd5d500ab373f616c/codex-rs/ext/memories/src/local/read.rs#L12-L51) | 程序验证读取参数和作用域，再从实际文件切取对应行范围 | 通用截断仍不保证 Markdown 代码结构完整 |
| [AnnotatedContent](https://github.com/openai/codex/blob/a09812981de703f18f38015cd5d500ab373f616c/codex-rs/context-fragments/src/annotated_content.rs#L10-L69) | 内容、分类、归属元数据共同保留；程序省略标记单独归 harness | 这里的归属不是我们所需的 document/chunk/version 坐标，也不能直接搬头尾截断 |
| [类型化工具输出与 Serializer](https://github.com/openai/codex/blob/a09812981de703f18f38015cd5d500ab373f616c/codex-rs/protocol/src/models.rs#L2367-L2443) | 程序持有字符串，标准序列化负责 JSON 表示，避免应用手工转义 | 无法修复模型已经改变的字符串；本项目现有标准序列化无需被指为故障根因 |
| [TUI 程序渲染引用](https://github.com/openai/codex/blob/a09812981de703f18f38015cd5d500ab373f616c/codex-rs/tui/src/markdown_render/inline_directives.rs#L218-L244) | 解析引用位置与展示逻辑分开 | 它不是逐字引文核查器或事实语义证明器 |

代码搜索和相关路径核对未找到现成的 `unsupported_quote`/evidence_id RAG 校验或通用事实蕴含门禁；这是有范围的未发现，不是对整个仓库的穷尽证明。网页工具的部分来源逻辑在远程服务中，开源客户端不能证明其内部实现。Codex 中某些语法容错或自动闭合逻辑不适用于本项目的严格原文协议，不应照搬。

## 7. 建议的正式改造顺序（本轮未实施）

1. **修原文表示和知识库代码块边界。** overlap 必须从原字符串按字符范围取得，不能从丢失空白的 token 列表重建；保留其原位置与身份。以规范文档的连续结构和明确的分块映射确定代码边界，避免对带重叠前缀的每块独立猜围栏状态。已有合成分块是否需要重新解析，应作为后续实施的数据一致性事项单独评估，不在本轮改写旧资料。抽取单元不可切开该范围；非连续片段明确分开，程序省略标记不可充当原文。先用保存的分块 2808 和上述最小模式验证，再核对尾部限制、否定和单位没有遗漏。
2. **改为模型选证据、程序取原文。** 服务器为实际送模的连续片段分配稳定的运行内 evidence_id，例如 E1/E2；条目携带知识库、文档版本、chunk_id、原始 start/end、content，以及代码边界。模型只返回 claim 文本及 evidence_ids，代码展示也选择代码证据编号，不重新生成 quote/code。
3. **保留严格映射与事实约束。** 程序验证编号属于本次最终请求、在所选授权范围和指定版本内，从登记原文取得并渲染引文。展示用围栏由渲染器生成，与事实原文分开。无效 ID、越界、未送模或版本变化明确拒绝。替换旧 quote/code 生产协议，不并留两套线上路径，不在坏输出后修复、模糊匹配、自动反转义或追加重答。
4. **分别验收内容与范围。** 原文编号能消除重抄造成的机械错误，不能证明 claim 语义正确；F01 仍要核对三份各自 CPU/内存配置，F03 仍按单库资料不足处理，CROSS01 验证双库比较。先做离线重放，再少量真实题；不因为引用编号合法就把错误结论判为通过。

以上不要求提高模型、工具、时间或 token 上限，也不需要新增摘要模型阶段。实施后仍需真实验证效果，本轮没有实现或重跑问答来宣告修复。

## 8. 后续实施记录

顺序 1 的代码与 29 篇资料重建见[实施报告](../implementation/quote-step-1-report.md)。顺序 2 已接入 evidence_id 与程序取原文，见[实施和少量验证报告](../implementation/quote-step-2-report.md)；F03 范围与调用上限、联网 TLS 阻塞仍保留，未宣告整体验收通过。
