# Agentic RAG 参考项目调研

调研日期：2026-10-27（Asia/Shanghai）。目标范围：现有个人知识库扩展为“知识库检索 + 网页搜索 + 外部只读工具”。本轮只读核查 GitHub README 与实际源码，未部署、运行第三方项目或验证运行效果；以下“建议”均为架构判断。通过 GitHub connector 直接读取一手仓库，固定 commit 防止后续代码变化影响追溯。

## 完整产品：Onyx

- 仓库：[onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx)。完整知识检索与 Agent 产品，README 明确列有 Agentic RAG、网页搜索、MCP 与外部 Actions；它包含写操作能力，并非只读工具产品。[README](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/README.md)
- 核查版本：`b8c2225f8eb0bb302b363e54d6c6458f0ed796bd`；查询时默认分支最新提交时间为 2026-10-27 01:08:23 UTC。[提交](https://github.com/onyx-dot-app/onyx/commit/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd)
- 技术栈：Python/FastAPI 后端、Next.js/React 前端。[后端依赖](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/pyproject.toml)、[前端依赖](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/web/package.json)
- 许可证：根 LICENSE 指明非 `ee` 等排除部分为 MIT Expat，`ee` 目录使用 Onyx Enterprise License；不宜把整个仓库统一称作 MIT。[原文](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/LICENSE)

实际核查的三个入口：

| 源码入口 | 已确认行为 | 对本项目的参考 |
|---|---|---|
| [chat/llm_loop.py](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/chat/llm_loop.py#L915) | 有上限的多轮模型调用，执行工具后把工具结果放回历史，更新引用映射，再继续模型调用 | 后端持有一次 run；回答与检索由同一循环协调 |
| [tools/tool_runner.py](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/tools/tool_runner.py#L233) | 按工具名分发，限制并发，设置执行超时；在执行边界发送开始、完成事件并处理工具异常 | 抽出独立工具执行层和结构化事件，不靠答案文字判断失败 |
| [web_search_tool.py](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/tools/tool_implementations/web_search/web_search_tool.py#L319) | 网页结果转为统一 SearchDoc，发出文档事件，同时生成模型上下文与 citation mapping | 本地分块、网页证据可共用来源登记与引用链路 |

建议：优先学习“工具结果同时服务模型、来源展示、引用映射”的边界设计。不要整套引入其企业连接器、多租户、代码执行与 Actions；也不要复制其文本解析工具调用的兼容分支。本项目已要求单一预期行为，应选择明确支持原生工具调用的模型接口。

## 完整产品：RAGFlow

- 仓库：[infiniflow/ragflow](https://github.com/infiniflow/ragflow)。完整 RAG 与 Agent 工作流产品；README 有入库、Agent 和部署说明。[README](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/README.md)
- 核查版本：`1e6c3e12a37d368707005319c134aa6f73ded905`；查询时默认分支最新提交时间为 2026-10-27 11:08:07 UTC。[提交](https://github.com/infiniflow/ragflow/commit/1e6c3e12a37d368707005319c134aa6f73ded905)
- 当前版本实际使用 Go/Eino 执行 Agent；`go.mod` 声明 Go 1.27 和 Eino，前端依赖 React。不要按旧版 Python 路径寻找本次核查的实现。[go.mod](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/go.mod)、[前端依赖](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/web/package.json)
- 许可证：Apache License 2.0。[原文](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/LICENSE)

实际核查的三个入口：

| 源码入口 | 已确认行为 | 对本项目的参考 |
|---|---|---|
| [component/agent.go](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/agent/component/agent.go#L196) | Eino ReAct；按轮数限制工具调用，预留最终回答；模型流另行收集；启用引用处理时会暂缓正文输出 | 为“继续查找/最终作答”定义硬上限，并明确引用处理对流式输出的影响 |
| [tool/tavily.go](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/agent/tool/tavily.go#L293) | Tavily 搜索和内容提取；搜索结果保留 url/title/content；实现 ReferenceBuilder 接口 | 搜索工具返回可追踪来源，错误有结构化结果 |
| [component/reference_recording_tool.go](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/agent/component/reference_recording_tool.go) | 包装 ReAct 使用的工具，把工具产出的 references 写入 canvas state；源码说明若缺少这一层，引用阶段看不到来源 | 来源采集必须覆盖 agent 内部调用，不能只在检索 HTTP 接口登记引用 |

建议：借鉴来源登记、工具预算与最终回答保留机制。当前项目实际是 Python/FastAPI + Vue，RAGFlow 这版属于跨语言架构参考；不要因此引入 Go 服务或 Eino。其 Canvas、DSL、子 Agent 与多服务部署也不适合第一版整体照搬。

## 完整产品：Open Notebook（个人知识库体验参考）

- 仓库真实地址：[lfnovo/open-notebook](https://github.com/lfnovo/open-notebook)。完整个人研究/知识库产品，支持来源、笔记、全文/向量检索和基于资料的对话。[README](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/README.md)
- 核查版本：`a0f6f3081ae06328f0a451b3a3ef2959ca25721d`；查询时默认分支最新提交时间为 2026-10-05 02:29:29 UTC。[提交](https://github.com/lfnovo/open-notebook/commit/a0f6f3081ae06328f0a451b3a3ef2959ca25721d)
- 技术栈：Python/FastAPI、LangGraph、Next.js/React、SurrealDB。[README](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/README.md)、[Ask API](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/api/routers/search.py)
- 许可证：MIT。[原文](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/LICENSE)

实际核查的三个入口：

| 源码入口 | 已确认行为 | 对本项目的参考 |
|---|---|---|
| [graphs/ask.py](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/open_notebook/graphs/ask.py) | 先生成检索策略，再并行执行多个 notebook 范围内的 vector_search，最后综合答案；该图没有网页工具选择或检索后重规划边 | 可作为“问题拆解→多查询→综合”模式参考，不能当通用工具 agent 实现 |
| [api/routers/search.py](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/api/routers/search.py#L77) | SSE 输出 strategy、answer、final_answer、complete、error；消费 LangGraph updates | 过程事件让用户看见在搜什么；此处是阶段更新，不等同逐 token 流 |
| [source-references.tsx](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/frontend/src/lib/utils/source-references.tsx#L46) | 解析 source、note、source_insight 引用，转换为可点击链接和编号 | 知识库来源、用户笔记、衍生洞见应保留类型和 ID，点击回到原资料 |

建议：用它校准个人知识库交互复杂度与来源跳转。页面抓取/导入资料不能直接算作运行时网页搜索；本次已核查 Ask 图不足以支持“模型自主选择知识库、网页或外部只读工具”。不要因为项目用 LangGraph，就认定已拥有所需 agent loop。

## 补充核查：个人助手、网页研究与最小示例

以下三个项目由主 agent 读取固定 commit 的源码核查；所有“适合本项目”均为结合本地 Python FastAPI + Vue3 实现作出的判断，没有运行这些项目。

### Khoj：最贴近个人知识库的产品流程

- 仓库：[khoj-ai/khoj](https://github.com/khoj-ai/khoj)。
- 定位依据：[README](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/README.md) 明确面向个人 AI，同时使用个人文档与互联网；实际主问答路径在 Python 中。
- 核查快照：`ae229ca894c0b80ad84664afcfdde523b5e87057`；查询时默认分支最近提交时间为 2026-08-02 01:55:40 UTC（不是项目未来活跃性的保证）。
- 许可证文件：[AGPL v3](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/LICENSE)，这里只记录原文标识，不作法律解释。

源码入口：

1. [research.py](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/routers/research.py#L476)：`research` 使用最大迭代次数限制循环，挑选下一工具，执行并收集状态与结果；`execute_tool` 分派文档搜索、网页搜索、网页读取及 MCP 等调用。
2. [online_search.py](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/processor/tools/online_search.py#L56)：将网页搜索与正文读取分开，保存 URL、摘要或提取内容，并发送 browsing 状态；搜索次数和网页读取数有参数限制。
3. [api_chat.py](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/routers/api_chat.py#L1039)：消费研究迭代，将文档参考与网上结果汇总到回答流程，并继续发送状态事件。

**适合参考的判断：** 从现有个人文档问答逐步加入联网研究，借鉴“挑工具 → 执行 → 记录结果 → 下一步”的运行流程；对应架构候选 02、03、04。文档与网页证据保留不同来源信息，可为候选 01 提供参考。

**不要照搬的判断：** 它还支持运行代码、自动化、操作类能力，整体工具集合超过本项目已确认的只读范围；其网页读取含 fallback，本项目的 No Fallbacks 约束不允许直接沿用。研究工具调用形状与权限策略仍需围绕本项目 owner 范围单独设计。

### GPT Researcher：网页研究与资料汇总的专项参考

- 仓库：[assafelovic/gpt-researcher](https://github.com/assafelovic/gpt-researcher)。
- 核查快照：`3582a6841fe7dc801e2a34e34ee408641aea9bce`；查询时默认分支最近提交时间为 2026-10-10 22:03:51 UTC。
- 许可证文件：[Apache 2.0](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/LICENSE)。

源码入口：

1. [skills/researcher.py](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/gpt_researcher/skills/researcher.py#L97)：支持网页、本地文档与 hybrid 研究路径；hybrid 并行收集两类资料，并复用 visited URL 去重。后续还会生成子查询和汇总上下文。
2. [skills/deep_research.py](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/gpt_researcher/skills/deep_research.py#L386)：递归研究具有 depth、breadth 与 semaphore 并发限制；跟踪来源与 learning→URL 引用，后续层减少 depth。
3. [agent.py](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/gpt_researcher/agent.py#L343)：提供研究执行与报告生成入口；维护 research_sources、visited_urls、costs 和事件记录；也提供 MCP 配置路径。

**适合参考的判断：** 候选 02/03 可参考子问题驱动的网页研究；候选 01 可参考 URL 去重及结论对应来源；候选 04/06 可参考进度与费用记录。

**不要照搬的判断：** 长篇研究报告不是每个个人知识库问题都需要的交互，默认全量深度研究会增加延迟与模型调用；费用记录也不等于自动执行硬预算。其兼容分支、解析修复和 fallback 不作为本项目实现模板。未核查 MCP 是否把全部工具强制限制为只读。

### LangGraph 官方 RAG 示例：最小执行逻辑参考

- 仓库：[langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)；这里推荐的是仓库内示例，不是完整知识库产品。
- 核查快照：`858891aaa164ebdacf33b8f5e7f88b38b9132211`；查询时默认分支最近提交时间为 2026-10-26 23:35:13 UTC。
- 许可证文件：[MIT](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/LICENSE)。

两个实际读过的入口：

1. [langgraph_agentic_rag.ipynb](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/examples/rag/langgraph_agentic_rag.ipynb)：agent 绑定 retriever tool，工具结果经过相关性判定，选择生成或改写问题再返回 agent。这里主要是检索工具示例，不能声称本 notebook 已有网页搜索。
2. [langgraph_adaptive_rag.ipynb](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/examples/rag/langgraph_adaptive_rag.ipynb)：问题路由到向量检索或网页搜索；有资料筛选、问题改写、生成支撑性与答题情况的判定，并用条件边继续执行。

**适合参考的判断：** 最容易抽取候选 02 的控制逻辑，先把“下一步为什么继续/停止”画清楚，再决定 implementation 是否使用 LangGraph。结合 agentic notebook 的工具选择与 adaptive notebook 的网页路径，可作为最小纵向流程的设计参照，但两个 notebook 并非已经组合好的成品。

**不要照搬的判断：** 这两份 notebook 含旧模型名与旧导入方式，没有本项目需要的知识库 owner 约束、完整引用协议、前端、部署和显式业务预算；adaptive 示例还把网页内容拼成单个 Document，会损失本项目重视的细粒度来源追踪。适合学流程，不应直接复制为生产代码。

## 对本项目六个重构候选的参考映射

此表为研究后的建议，不是已批准的实现方案。

| 本地候选 | 首选参考 | 具体阅读目标 |
|---|---|---|
| 01 检索证据 module | Onyx；GPT Researcher | 检索与网页来源归一后的身份、引用；URL 去重及结论来源 |
| 02 后端问答执行 module | LangGraph 示例；Khoj | 继续/停止的条件，工具结果如何驱动下一步 |
| 03 只读工具执行 module | Khoj；Onyx | 工具选择与执行集中；按本项目范围限制可用工具 |
| 04 运行事件 module | Onyx；Khoj | 工具阶段状态、来源、答案与明确终态的传递 |
| 05 文档入库 module | RAGFlow；Open Notebook | 入库与检索模块组织；不能将文档问答等同完整 agent 循环 |
| 06 运行评估 module | GPT Researcher（仅费用记录部分） | 跟踪整次运行成本；质量评估仍须沿本项目实际证据另行设计 |

建议阅读顺序：**LangGraph 的两个小示例 → Khoj 的研究执行路径 → Onyx 的来源与事件实现 → GPT Researcher 的网页研究模块**。RAGFlow 与 Open Notebook 按需要作专项补充。优先借鉴已经核实的 module 行为，不移植整个产品的架构。

## 本次研究的交付范围

- 只新增这份参考文档；未添加功能、改变依赖或修改测试。
- 没有选定 agent 框架、搜索供应商或模型，也没有批准实施六个重构候选。
- 源码已固定到核查 commit；未来开始实现时，针对选中的那一段再次核查依赖版本和当前实现。
- 外部项目的 fallback、兼容逻辑、写工具和代码执行能力不自动进入本项目。
