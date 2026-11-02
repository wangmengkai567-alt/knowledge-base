# 顺序 2：模型选证据，程序取原文

日期：2026-11-02。对应[原因分析与改造顺序](../research/rag-quality-phase-4-quote-codex-analysis.md)第 7 节第 2 项。

## 实施结果

已部署回答协议 `answer_contract_version=3`。最终模型只返回 `status/claims/missing`；每条 claim 包含 `scope/text/evidence_ids/code_evidence_id`。线上删除模型自由填写 `quote/code` 的旧协议，不接受旧字段，不保留旧协议分支。

服务器在最终预算和来源选择确定后，为实际送模的连续原文分配本次运行内的 E1/E2 编号。预算估算使用同一目录构造函数，目录元数据也计入输入；探索阶段仍使用来源引用编号，尚未分配最终证据编号。最终仅生成一次，不增加摘要或修复模型阶段，不提高预算。

[证据目录](../../ai-service/internal/biz/evidence.py)登记运行编号、来源引用编号、类型、原文和原始位置。知识库位置包含知识库、文档版本、chunk_id、position、start/end；网页位置绑定本次抓取的 source_id、URL、时间及字符范围。送模条目提供 E 编号、类型和连续原文，来源身份保留在上层目录；原始位置由服务器保管。非连续片段不拼成单条证据，完整代码与其前后正文分别登记。

[渲染器](../../ai-service/internal/biz/answer_rendering.py)从本次目录取引文和代码，模型不再搬运这些字符串。代码正文保持原样，展示围栏单独生成。快照里的 quote/code 是程序生成的审计记录，带有 `quote_owner=program`；不属于模型输出字段。

生成前及发布前均复核访问权限、文档就绪状态、当前版本和实际分块切片，网页核对本次缓存正文。无效编号、跨运行或范围、未送模片段、版本变化拒绝发布；保留已发生的模型用量。必要的严格绑定检查随新协议接入，无模糊匹配、反转义、补围栏或自动追加回答。

API、SSE、前端类型、保存快照及离线审计同步采用 v3。快照保存实际最终请求、完整证据目录及程序生成的 claim 引文，能逐条回查送模内容。历史 v1/v2 保存记录仍可离线读取，不构成线上旧协议兼容路径。

## 小量验证

共 6 个不同问题、7 次实际运行；仅代码展示题在提示调整后复测一次。未运行全量题集，原历史结果和本次初次结果均保留。

| 问题 | 最终记录 | 实际情况 |
|---|---|---|
| F01 | completed | 三份 CPU/内存配置分别引用，明确数字不同；原 harness 的 CPU/Memory/Disk/Network/Runtime 整块作为证据，没有在 Memory 后补闭围栏 |
| F03 | budget_exhausted / model_call_limit | 原请求固定 `[3]`；Docker 引文完整，单个反斜杠保持原样；未读取知识库 2 的安全文档，不能完整回答。最终内容声明 insufficient，保留部分答案与缺失说明 |
| CROSS01 | completed | 明确授权 `[2,3]`，分别引用 256m 和 512 MB，明确并非同一套数字 |
| W01 / W02 | tool_failed | 网页与 Tavily 分别发生 TLS 握手失败，均未进入最终生成；联网证据路径此次没有实测通过 |
| S2CODE | completed | 程序展示完整 Docker 命令，11 处行末单个反斜杠与引号保留；最终复测不在说明文字重复拼接完整命令 |

初次 S2CODE 的程序代码块正确，但模型同时在说明文字里拼接了丢失引号的一行命令；该条保留为需要纠正。收紧规则，要求选用代码编号时只简述用途，不再抄完整命令，随后仅复测此题。该规则属于生成约束，仍不能证明未来所有说明文字都语义正确。

[逐题复核](evidence/quote-step-2-question-review.json)记录范围、运行编号、用量和局限。所有已生成目录的原文绑定检查通过；所有已渲染答案（包括 F03 的部分答案）的程序引文检查通过。12 项知识库来源记录与当前版本/分块匹配，见[本地](evidence/quote-step-2-local-source-audit.json)、[跨库](evidence/quote-step-2-cross-source-source-audit.json)、[首次代码](evidence/quote-step-2-code-source-audit.json)及[最终代码](evidence/quote-step-2-code-final-source-audit.json)审计。网页审计没有来源记录，不能将空检查判成联网成功。

CROSS01 的历史数字启发式把文件名 `04` 标为疑点；核对原文与各 claim 后，未发现配置数字错误。自动评分不改写，实施代理复核不冒充独立人工评级。

离线诊断覆盖完整代码、CRLF、反斜杠、非法 E 编号、跨运行编号、版本中途变化、旧字段拒绝、网页偏移位置和关闭联网权限，见[集成证据](evidence/quote-step-2-offline-verification.json)和[边界证据](evidence/quote-step-2-edge-verification.json)。模拟供应商验证每次仅一次最终生成，无真实模型请求。

Ruff、前端类型检查及现有 eval 24 项检查通过。后端现有 47 项检查仍为 10 失败、1 错误，与顺序 1 的失败用例名称相同；旧测试数据/协议未适配现行工具与预算约束，不能声称后端全绿。未新增或修改正式测试文件。见[交付检查](evidence/quote-step-2-delivery-checks.json)和[后端原始检查](evidence/quote-step-2-existing-checks.json)。

## 数据与验收边界

[部署前](evidence/quote-step-2-before-runtime.json)与[部署后](evidence/quote-step-2-after-runtime.json)的文档、分块和向量内容指纹完全一致：29 篇、1167 块、1167 向量。顺序 2 未重新分块或重建嵌入。运行容器源码与工作区逐文件一致，ready 检查通过。

保留 qwen3.8-flash、qwen3.7-text-embedding-flash、qwen3.7-text-rerank，嵌入批量 20、分块 256/重叠 32。模型/工具/时间/实际词元等上限未变。7 次运行合计 22 次对话模型、17 次工具、72309 个已结算对话词元；所有用量已知，无自动重试。这里不包含检索嵌入或重排的未独立计量用量，不估算货币费用。

顺序 2 的协议和程序取原文路径已完成。证据编号与逐字绑定不证明断言蕴含关系，也不解决知识库范围不足或外部 TLS 故障；F03 和联网题仍未通过，整体验收保持未通过。
