# 问答评测与第六阶段验收

`golden_set.json` 的 100 题（90 可答、10 资料内无答案）保留原题目、参考答案、必答词、陷阱和范围；90 道可答题另补显式 `keyword_aliases`，不把同义表述当作新的必答要点。历史 `last_run.json` 与原始阶段实测文件保持不变。知识库限定题始终 `allow_web=false`，并按历史结果中的知识库编号使用同一范围；禁止自动挑一个非空库代替缺失范围。

`web_scenarios.json` 单独覆盖用户指定网页、搜索后读正文、知识库与网页联合取证、联网后仍资料不足。联网成功必须有实际请求和抓取内容，固定资料检查不能替代供应商实测。`cross_source_scenarios.json` 另外覆盖两个已授权知识库中的冲突数字对照；历史 F03 的单库范围不因此改写。

## 运行

从仓库根目录执行，后端需已就绪；Python 环境与依赖只使用 uv：

```bash
uv sync --project ai-service --locked --extra dev
uv run --project ai-service --no-sync python -m unittest discover -s ai-service/tests
uv run --project ai-service --no-sync python -m unittest discover -s eval
uv run --project ai-service --no-sync python eval/run_eval.py --out eval/phase-6-final-golden-results.json
uv run --project ai-service --no-sync python eval/run_eval.py --dataset eval/web_scenarios.json --out eval/phase-6-final-web-results.json
uv run --project ai-service --no-sync python eval/run_eval.py --dataset eval/cross_source_scenarios.json --out eval/phase-6-cross-source-results.json
```

设置 `EVAL_EMAIL`、`EVAL_PASSWORD` 可更换本机评测账号；默认沿用历史本机开发账号。`EVAL_BASE_URL` 默认为 `http://127.0.0.1:8084`，`EVAL_SLEEP_S` 为题间等待秒数。脚本不重试失败请求，不自动切换供应商；凭据或额度失败会停止整轮，避免继续消耗。

同一时间只允许一轮评测，系统锁 `eval/.run_eval.lock` 随进程退出释放。已存在的结果文件不会被覆盖，重新实测需显式指定另一个清楚命名的输出路径。每题结束后保存进度，即使后续中断也保留已发生的用量与失败。

## 证据与版本

独立 `/retrieve` 请求只衡量召回与排名；生成只提交问题和允许范围，不提交 `chunk_ids`。最终答案证据直接读取该次后端持久化快照，并核对同步响应与快照完全一致。默认快照目录为 `ai-service/data/answer-runs`，其他部署用 `--run-dir` 指定可读取的本地目录。

每行保留实际运行编号、请求、工具动作、明确终态、模型实际用量、保守预算扣减、实际送模 `sources`，以及工具取得但可能未送模的结果。知识库证据包含文档版本时间、原分块与字符范围；网页证据包含 URL、获取时间和本次内容。评测不再扩邻块或用另一份检索结果重造答案依据。

逐段核对知识库当前文档版本与原分块：

```bash
uv run --project ai-service --no-sync python eval/audit_sources.py --results eval/phase-6-final-golden-results.json --out eval/phase-6-final-source-audit.json
```

这一步只校验原有证据，不改写答案材料；文档版本变化或原文片段不符明确记为失败。网页直接核对保存的抓取内容，不在第二天重新抓取后替换原证据。

服务运行快照保留七天。评测导出的原始结果是为验收显式保留的本地资料，包含实际文档片段，应按私有评测资料保管；它们不由服务的七天清理器管理，不自动提交或上传。

## 离线重评分

```bash
uv run --project ai-service --no-sync python eval/run_eval.py --rescore --out eval/phase-6-final-golden-results.json
```

此分支在登录、读取模型配置、列知识库或任何网络请求之前运行，只读取结果中保存的题目、答案、检索结果和实际送模快照。单元检查将网络及登录替换成直接报错，证明离线分支不发请求。历史 `last_run.json` 没有实际证据快照，不能伪造证据替它重评分。

## 关键词等价表达复评

新实测使用题集的 `keyword_aliases`，每条结果的 `keyword_matches` 保存原词、候选表达和实际命中项。普通 `--rescore` 使用结果内部保存的题集；不会自动替换成后来修订的标准。

对旧结果显式应用当前补齐的标准，使用只读离线对照脚本，输出文件必须不存在：

```bash
uv run --project ai-service --no-sync python eval/review_keywords.py --results eval/last_run.json eval/phase-6-golden-results.json eval/phase-6-final-golden-results.json --out docs/implementation/evidence/keyword-review-new.json
```

脚本不登录、不检索、不调用模型，不改原始结果和快照；有快照时只允许题集的 `keyword_aliases` 改变，所有非关键词评分须与保存记录一致。旧历史没有快照，只比较关键词，不推造新版执行状态或证据。

90 道可答题的 130 个原要点已补充 536 条等价表达。历史按当前同一规则为 94.44%，最终轮仍为 50.00%；最终轮新增词面命中的两题均预算停止。若进一步要求正常 `completed`，最终轮为 43/90（47.78%），原 50% 口径另计了两道含关键词的资料不足答案。历史没有完成状态，不能计算该口径。

原始目标 91.11% 和原始实测数值保留为归档对照；补词后的同规则历史对照是 94.44%，新的完整黄金题集实测会以当前标准重算历史关键词门槛，并另存原历史摘要；不得继续用旧的较低目标声称质量恢复。启发式幻觉率保持原判断。完整差异、失败分布和验证见 [关键词复评](../docs/implementation/keyword-scoring-review.md)。

## 指标与门槛

本阶段用户确认：历史质量不退步，遵守既定预算；费用只记录用量，不估造货币金额。

| 指标 | 规则与目标 |
|---|---|
| Hit@5、Hit@10、MRR | 沿用历史按不同文档去重排名；目标各为 1.0，单独衡量检索 |
| 关键词正确率 | 原 `must_contain` 保留，要点间 AND、同一要点的显式等价表达间 OR；输出逐项命中明细。历史和当前须用同一标准比较，运行失败仍在分母；目标与旧口径见上方补词复评 |
| 拒答正确率 | 资料内无答案题以明确 `insufficient` 终态判断，目标 100%；原固定“我不知道”另保留为历史格式参考，不强迫新界面返回固定句 |
| 启发式幻觉率 | 原文本重合、数字和陷阱规则不变，只替换证据来源为实际送模片段；目标不超过历史 1.11%，不能视作语义真实性保证 |
| 引用有效性 | 编号存在、实际送模、分块范围与内容对应；缺失或无效引用不得发布为完成 |
| 来源覆盖 | 记录被引用来源占实际送模来源的比例；逐段对该段所引来源另列启发式抽查线索 |
| 工具选择与重复 | 是否遵守联网范围、是否执行场景要求的工具、是否重复执行相同动作；明确记录参数摘要截断后的未知项 |
| 停止与事件 | 完成、资料不足、故障、超时、取消、预算停止和引用失败分开；事件顺序与运行终态一致 |
| 时间与用量 | 记录 p50/p95/max、模型/工具次数、实际输入输出 token、保守估算和未知用量；最多 6/10 次、120 秒、输入 16000、输出 2048、累计扣减 64000 |

旧答案正确率曾排除请求错误；新报告对运行失败更严格，需同时展示错误数量，不将口径差异隐藏为模型质量变化。原拒答固定字符串要求不适用于现在允许说明缺失信息的协议；明确终态能区分拒答和依赖故障，不通过回答字面猜测。

多来源、引用错配和冲突案例必须人工逐条核对实际快照。`semantic_review=pending` 表示自动流程没有完成语义验收，不能把“有编号”或“关键词命中”当作全部结论正确。

## 产物

- `phase-6-golden-results.json`、`phase-6-web-results.json`：首轮原始实测，保留失败。
- `phase-6-final-golden-results.json`、`phase-6-final-web-results.json`：修正后验收记录。
- `phase-6-source-audit.json`、`phase-6-final-source-audit.json`：知识库版本与片段核对。
- `phase-6-diagnostic-results.json`、`phase-6-diagnostic-after.json`：限定题目排查，不作为完整题集的替代。
- 最终数值、人工抽查和已知限制见 [第六阶段记录](../docs/implementation/phase-6-report.md)。

## 问答质量优化对照

原始 `phase-6-final-golden-results.json` 不覆盖。实现优化后另存完整 100 题至 `rag-quality-optimized-golden-results.json`，使用同一补词标准和原幻觉规则复算：

```bash
uv run --project ai-service --no-sync python eval/compare_quality.py --before eval/phase-6-final-golden-results.json --after eval/rag-quality-optimized-golden-results.json --out docs/implementation/evidence/rag-quality-comparison-new.json
```

输出文件必须不存在。脚本逐题校验请求、授权范围、运行上限及实际模型一致，除关键词匹配外的所有原始评分字段须保持一致。前后结果不重新调用模型、不覆盖快照。

本轮正确率 50.00% → 65.56%，正常完成且命中全部要点 47.78% → 65.56%，自动幻觉疑点率 9.59% → 1.33%，拒答正确率 30% → 100%。仍有 17 次预算停止和 9 次无效格式，质量门槛未通过；联网和跨库补充场景也单独保留失败。完整原因、参考项目、用量、退步题目与语义核查见 [质量优化报告](../docs/implementation/rag-quality-optimization-report.md)。

## 问答质量整改第 1 阶段：预算与终态协议 v5

本节描述新的线上协议；前文的 `charged_tokens` 和预算终态数值仅是历史记录。

- `usage.calls` 按 `run_id` 与调用序号记录预检估算、请求输出上限、调用预留和供应商实际输入输出。每次只结算一次；`provider_total_tokens` 仅核对，不重复相加。
- `settled_tokens` 是已知实际输入输出合计，`reserved_tokens` 与 `final_reserved_tokens` 是临时空间。收尾预留按当前完整请求估算；保守输入估算仍采用 UTF-8 字节，不能称为精确词元数。
- `actual_total_tokens=null` 表示存在未返回用量的调用。已知小计可以为零，但这不表示该运行实际消耗为零；传输失败、超时、取消或缺失用量后停止继续调用。
- `exploration_stop_reason` 表示停止继续取证的原因；`status`、`stop_reason` 表示回答最终结果。合法收尾回答可结束为 `completed` 或 `insufficient`，次数或空间导致的软停止原因另存。实际超限、故障、格式错误、超时和取消继续失败。
- API、SSE 和运行快照统一为 v5，前端拒绝其他协议。评测按保存结果的明确版本读取：v4 只解释历史扣减字段，v5 校验实际账本；线上没有两套执行策略。

阶段结果分别另存，不覆盖任何历史文件：

```bash
uv run --project ai-service --no-sync python eval/run_eval.py --ids A09 A13 A20 A21 A25 D09 A15 A16 A22 B06 B12 B14 C08 C09 D08 E15 F03 --out eval/rag-quality-phase-1-budget-results.json
uv run --project ai-service --no-sync python eval/run_eval.py --out eval/rag-quality-phase-1-golden-results.json
uv run --project ai-service --no-sync python eval/compare_quality.py --before eval/rag-quality-optimized-golden-results.json --after eval/rag-quality-phase-1-golden-results.json --out docs/implementation/evidence/rag-quality-phase-1-comparison.json
```

`--ids` 只筛选本轮实测题号，保留原题集、题干、评分和授权范围，结果标记 `selected_ids`；小样本不能替代完整题集。对照要求题集（含等价表达）、请求、范围、限制和模型一致。旧答案的终态重分类仅另列分析，不修改历史快照，也不等同新运行成功。

本阶段不授权新增或修改测试。已有检查若断言旧的预算/重复调用终态，应原样运行并在实施报告列明冲突；不得修改期望来制造通过。

## 问答质量整改第 2 阶段：工具探索与严格最终生成

工具参数 Schema 与本地解析共用参数模型；经用户确认，关键词单项由 32 放宽至 64 字符，连接后仍最多 120 字符，数组、类型和额外字段限制均显式声明。正式工具定义请求 `function.strict=true`，本地校验仍是执行前的必要条件；非法参数明确报字段与约束，不裁剪或转换后再执行。

问答先执行原生工具探索，首轮 `tool_choice=required`。正式最终生成使用独立的严格回答 Schema，不携带工具定义。`model_started.request_mode` 分别记录 `native_tools` / `strict_answer`，同时记录工具定义数量及工具选择。最终调用从开始即纳入原 6 次、64000 实际词元、120 秒预算；没有输出失败后的修复调用或备用协议。

评测增加 `generation_protocol_valid` 及对应门槛，只核对显式记录新阶段的快照。历史快照不补造阶段信息，题集、要点评分与幻觉规则不变。按本次用户要求只进行必要小样本；例如另存一组四题：

```bash
uv run --project ai-service --no-sync python eval/run_eval.py --ids A18 A22 B04 G01 --out eval/rag-quality-phase-2-selected-results.json
```

放宽前的第 2 阶段测试覆盖 5 道不同问题、7 次运行，分清初轮和最终实现。放宽前的最终 A22 正常完成；当时 W02 即使请求严格工具模式仍生成 37 字符关键词，被 32 字符的本地限制拒绝。放宽至 64 字符后的独立结果见[长度调整记录](../docs/implementation/rag-quality-phase-2-keyword-limit-report.md)。单题子集未覆盖的基线指标可能仍显示未满足；保留原始摘要，不为小样本改分或将其当作完整质量评估。结果、服务能力缺口与检查详见[第 2 阶段实施记录](../docs/implementation/rag-quality-phase-2-report.md)。

## 问答质量整改第 3 阶段：联网诊断

网页请求诊断随 `tool_finished.web_diagnostics` 与快照 `tool_results.web_diagnostics` 保存；错误项的 `diagnostics` 保留端点、发生阶段、类别、异常类名、耗时、HTTP 状态和经白名单检查的 request_id。未收到响应时状态为 null。HTTP 200 后仍分别检查逐网址提取失败、空正文和格式错误；成功传输不代表取得了完整可答正文。

默认时限为供应商提取 15 秒、客户端阶段及请求总时限 20 秒、工具 30 秒、单题 120 秒；外层可能按单题剩余时间提前终止，记录具体超时层级。无自动重试、直接抓取或供应商切换。

本次按用户要求只运行 W01—W04 四题各一次，结果另存 `rag-quality-phase-3-web-results.json`。W01 正文取得但 COPY 代码参数缺失；W02 选择未授权网址；W03 正文读取成功后生成超长关键词；W04 正确说明资料不足。阶段验收未通过，自动 completed 不能替代正文支撑核对。改动、有限探针及逐题复核见[第 3 阶段实施记录](../docs/implementation/rag-quality-phase-3-report.md)。

## 网页取证修复顺序 2：当前工具契约

历史 `tool_contract_version=2`：网页搜索使用最多 120 字符的单字符串 `query`，读取使用本次目录的 `source_id`，`web_find` 只查本次已保存的正文快照。旧 `keywords`/`urls` 工具参数已删除。目录与实际证据分离；范围字段只描述实际送模片段，完整规范正文保存在快照 `web_pages` 中。

`web_find` 只返回最多 10 项导航命中：`source_id`、匹配范围 `start/end`、原样短预览及其 `preview_start/preview_end`、`in_code`。预览最多 120 字符，不生成引用编号，也不加入送模证据来源。模型选择命中后用 `web_read` 的 `start/max_chars` 取得可引用正文；多个子问题可以在同轮发出多个工具调用。新命中位置计为导航进展，同次快照中重复位置不重复计数。API 和事件外层字段保持不变；历史结果中的 find 正文保留为当时的执行证据。

完整请求预算同时核对原生工具阶段（含工具定义和完整消息历史）与独立最终阶段（含 JSON Schema 和停止原因说明）。输入上限仍为 16000，输出上限仍为 2048；输出只从模型总上下文容量中扣除，不再从独立输入上限重复扣减。材料装包不再固定占一半剩余空间。探索与最终回答是两次请求，总额度仍分别为它们预留输入和输出，实际消费继续按供应商用量结算，UTF-8 字节估算不等于精确 token 数。

同一批工具结果保留全部实际元数据后统一分配正文空间；网页预算裁剪遵守段落和完整代码块边界。后续 `web_read` 已选择覆盖某次 find 的命中位置时，该次导航在后续请求中只保留匹配位置和代码标记，标记 `preview_omitted_after_read_selection`；尚未选择读取的其他查找保留预览。原始完整预览仍保存在事件和运行快照，所有调用/结果消息对及正文证据保持原样。估算和实际发送共用这一请求视图，避免用已消费的导航预览反复占据正文空间。

单个正文窗口无法容纳时返回明确窗口限制，不自动重试或结束全部取证；模型可在同次快照中显式选择其他位置。只有完整请求或本次额度不足等真实终止条件满足时才收尾。预算原因终止后若仍不能作答，状态为 `budget_exhausted`，停止原因保留 `input_budget`、`token_budget` 或调用次数上限，不冒充 `insufficient_material`；模型收到的控制消息与界面说明使用程序记录的原因。

取证通过 `finish_research` 选择实际已送模引用后进入独立最终生成；仍遵守原有模型、工具、用量和时间上限。目录不可引用，围栏代码必须有引用正文支撑。新契约评测核对目录身份、完整快照、原文范围及代码块边界，并直接读取规范调用键。旧历史结果按原契约只读解析，不补造新字段或改写历史答案。

最终模型协议为 `answer_contract_version=2`：输出 `status/claims/missing`，不再接受自由 `answer` 字段。每条事实包含 `scope/text/evidence/code`，每份依据包含已选 `citation_id` 与实际送模内容中的连续原样 `quote`；程序校验后统一生成展示答案及引用。`source` 只能归于一个原始来源，`shared` 必须有至少两个独立来源，同一网页多个窗口或同一知识库文档多个块不重复计数。缺失项只作为限定于本次已读资料的问题主题呈现，不能附加自由事实尾段。

代码须在该事实自己的非摘要摘录中原样出现；answered 的每条事实均不能以搜索摘要替代正文。任何条目校验失败都不发布整份答案，不修复格式或自动重答。快照保留原始最终 JSON、规范事实及摘录的送模内容相对范围；`answer_validated` 记录原文绑定通过，但 `semantic_support` 仍为 `not_evaluated`。原样摘录、引用身份和来源数量正确并不证明自然语言结论得到语义蕴含，现有关键词/重合率也不能充当语义门禁。外部 API/SSE 继续返回程序渲染的 Markdown `answer`，工具契约仍为 2；历史结果保持原样。

本次只做必要诊断、确定性集成及公开端点探针；未执行四题完整问答。模拟流程通过，真实 Tavily 搜索与 Docker 页面读取在 TLS 阶段失败，原结果保留。现有只读工具有一项旧 `urls` 参数断言失败，未修改测试。结果和边界见[顺序 2 实施记录](../docs/implementation/web-reader-step-2-report.md)。

## 当前 Token 预算与计量契约

同步/SSE 版本为 6，工具契约为 3，答案契约维持 3。`counting` 标记 `model_tokenizer_estimate`，保存模型、固定 tokenizer_ref、请求投影版本、比例及固定余量；调用账本保存内容、协议及安全余量，供应商实际用量另列。

`web_read.max_tokens` 为网页窗口额度（64～2000），字符 `start/end` 与 `total_chars` 保持原义；搜索摘要上限 512 token，定位预览上限 64 token，工具完整 content 上限 3000 token，完整消息另受 12000 字节限制。仅根据后续成功读取的实际范围逐项省略已读预览；请求失败和未读预览仍保留。

`score_run` 按版本核验新账本与计量元数据。`summarize_runs` 按计量方法、tokenizer_ref、投影版本及余量分组输入估算；不混加旧字节估算，也不改写历史快照或既有评测分数。上文有关工具契约 2、字符窗口和字节估算的内容描述历史阶段，不代表当前运行协议。
