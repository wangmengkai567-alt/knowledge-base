# 第五阶段检索候选挤占：源码调研与采用方案

日期：2026-10-29（Asia/Shanghai）。范围仅限第二个审查问题：尚未就绪文档保留的旧向量或 BM25 分块，先占满检索分支的 top-k，随后业务层删除这些结果，导致本来可用的就绪文档进不了候选集合。

本轮使用 research 技能，由后台代理通过 GitHub 插件真实读取六个参考项目的固定提交源码；根节点另外亲自读取 RAGFlow 和 Onyx 的相关实现。搜索仅用于定位文件，结论以固定提交实际内容为准。未部署或运行第三方项目；以下不将 README 或接口声明当作底层执行顺序的证明。

## 六个参考项目的实际核查

| 项目与固定提交 | 实际读取入口 | 确认的机制与适用边界 |
|---|---|---|
| RAGFlow `1e6c3e12a37d368707005319c134aa6f73ded905` | [search.go：Search，57–100](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/engine/serenedb/search.go#L57)；[searchFilters，175–195](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/engine/serenedb/search.go#L175)；[buildFulltextSQL / buildVectorSQL，208–245](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/engine/serenedb/search.go#L208)；[buildFusionSQL，280–308](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/engine/serenedb/search.go#L280) | 有直接可用做法。评分检索默认加入 `available_int=1`，同一 WHERE 条件进入全文、向量以及混合检索的两个子分支，然后各自排序和 LIMIT。所采用的是“候选资格必须在各分支截断前生效”。其 available 标记不是本项目四阶段 ready，不复制其字段或整套引擎。 |
| Onyx `b8c2225f8eb0bb302b363e54d6c6458f0ed796bd` | [search.py：get_hybrid_search_query，474–525](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/document_index/opensearch/search.py#L474)；[get_keyword_search_query，573–604](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/document_index/opensearch/search.py#L573)；[get_semantic_search_query，662–696](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/document_index/opensearch/search.py#L662)；[底层查询助手，870–946](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/document_index/opensearch/search.py#L870)；[默认 hidden 过滤，1468–1471](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/document_index/opensearch/search.py#L1468) | 有直接可用做法。统一可见性、权限及范围过滤用于每个 hybrid 子查询；508–510 行明确说明这样避免聚合时才丢弃大量候选。单向量检索将过滤放入 KNN 查询内部，关键词放入 bool.filter，再由请求 size 限定结果。采用其统一约束所有召回分支的方式，不引入 OpenSearch 或其 ACL/隐藏模型。 |
| Open Notebook `a0f6f3081ae06328f0a451b3a3ef2959ca25721d` | [notebook.py：vector_search，888–925](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/open_notebook/domain/notebook.py#L888)；[24.surrealql：fn::vector_search，92–160](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/open_notebook/database/migrations/24.surrealql#L92)；[25.surrealql：fn::text_search，4–78](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/open_notebook/database/migrations/25.surrealql#L4) | 也有适用的范围过滤实现。向量函数先一次性解析 scoped_sources；source_embedding 在 WHERE 中按父 source 过滤，再 ORDER BY similarity DESC / LIMIT。文本函数对源、分块、洞见和笔记先限定范围，再合并排序截断。这里过滤的是笔记本归属，并非入库完成状态；本地借用执行顺序，不搬入关系图或 SurrealDB。曾读迁移 9 的旧函数，随后沿现行调用六参数追到迁移 24/25，以上以后者为依据。 |
| Khoj `ae229ca894c0b80ad84664afcfdde523b5e87057` | [database/adapters/__init__.py：EntryAdapters.apply_filters，2105–2147](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/database/adapters/__init__.py#L2105)；[search_with_embeddings，2150–2178](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/database/adapters/__init__.py#L2150) | 向量查询先创建带 owner、词、文件和日期范围约束的 QuerySet，再标注余弦距离、过滤距离、排序，最后 `[:max_results]`。进一步佐证范围条件应进入数据库查询，而不是对截断后的结果做筛选。所读函数没有本项目阶段状态；不据此声称 Khoj 已有相同 ready 模型。 |
| GPT Researcher `3582a6841fe7dc801e2a34e34ee408641aea9bce` | [compression.py：VectorstoreCompressor，36–82](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/gpt_researcher/context/compression.py#L36)；[vector_store.py：VectorStoreWrapper.asimilarity_search，61–64](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/gpt_researcher/vector_store/vector_store.py#L61) | 将 filter 与 k 在同一次调用传给注入的 LangChain 向量存储，支持过滤下推的接口设计。但实际 prefilter 行为由外部具体存储实现，不能只凭这一包装层证明全部后端的执行顺序；也没有所读入口的 ready 计算。可作接口佐证，不作为本地正确性的唯一来源。 |
| LangGraph `858891aaa164ebdacf33b8f5e7f88b38b9132211` | [langgraph_agentic_rag.ipynb，89–114、132–140](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/examples/rag/langgraph_agentic_rag.ipynb#L89)；[langgraph_adaptive_rag.ipynb，113–148](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/examples/rag/langgraph_adaptive_rag.ipynb#L113) | 两个先前参考的 RAG 示例建立一次性 Chroma，使用无过滤参数的 as_retriever()。所读示例没有文档就绪范围、旧成果保留或截断前阶段过滤，不能直接解决本问题。此结论限于实际读取的两个示例，不代表框架其他存储或用户实现无过滤能力。 |

## 采用决定与本地适配边界

直接采用 RAGFlow、Onyx 在每个召回分支截断前应用资格条件的机制，Open Notebook 的预先解析父文档范围与 Khoj 的数据库过滤顺序提供交叉核实。本项目已有 stages 权威快照，因此以当前 `ingestion_status(stages) == "ready"` 的文档集合表达候选资格：parse、chunk、vector 为 completed，keyword 为 completed，或在纯向量模式为 skipped。这里的“采用”是按现有模块接口落地同一执行顺序，不是复制完整第三方源码、增加依赖或引入第三方框架。

本地实现约束：

1. 业务层在检索开始前解析指定知识库当前 ready 文档集合，同一范围传给所有查询变体、向量与 BM25 分支，且在各分支 top-k 和 RRF 融合之前生效。
2. 显式的 document_ids 约束应区分 None（调用者未限定范围）与空列表（没有合格文档，结果为空）。文档范围与知识库范围同时约束，不扩大任何既有范围。
3. 内存存储在候选排序/截断之前过滤；PostgreSQL 需保证实际排序输入限于合格集合。单纯将条件放在带近似索引的 WHERE 处，不足以自动证明近似扫描不会先耗尽候选。使用 MATERIALIZED 合格集合后精确余弦排序，可使本地适配的执行边界明确；这是本地 pgvector 的执行计划选择，并非声称上述第三方项目使用相同 SQL。
4. 保留返回前对实际文档状态的复核，用于拒绝检索执行期间已转为未就绪的文档；它不再承担拯救召回候选的职责。本修复不承诺检索期间持续并发改变状态时补足 top-k，也不新增重试或备用召回路径。
5. 仅限制检索资格，旧分块和向量继续保留供既有定向重试使用。既有分数定义、查询规划和 RRF 计算规则不因此改变。

根节点补充实际读取与运行扩展一致的 [pgvector v0.8.5 官方 Filtering 文档](https://github.com/pgvector/pgvector/blob/v0.8.5/README.md#filtering)（README 425–470 行）：近似索引在扫描后应用过滤，因而可能返回不足量结果；迭代扫描虽可继续搜索，但仍受扫描上限约束。本次采用先物化合格集合再精确排序，避免把调大候选或有限迭代当作资格完整性的保证。

## 本地验证

以下隔离验证由根节点执行并提供实际结果，未新增或修改测试文件：

- 为未就绪文档设置 80 个保留旧分块，另设 1 个就绪文档分块（ID 81），旧块占据原召回窗口。相同命令在修复前对纯向量和混合检索均返回空列表，修复后两路均返回 `[81]`，证实 ready 分块进入了实际候选集合，而不只是出口继续删除旧块。
- 真实 SQLite 仓储覆盖 24 种未就绪组合（4 个阶段 × pending/running/failed × 2 种检索器），均不召回未就绪内容；没有 ready 文档时不调用 embedding。
- None、空范围、不存在的文档 ID、文档范围与知识库范围的交集、纯向量模式 keyword=skipped、2 个查询变体透传相同范围均通过；查询执行期间撤销 ready 后，出口复核拒绝返回。检查前后 82 条物理索引保持不变。
- 真实 PostgreSQL 临时表包含 80 个旧分块、1 个 ready 分块和 1 个其他知识库分块。强制使用 HNSW 索引计划、设置 ef_search=1 且 iterative_scan=off 后，普通 WHERE 查询返回空列表，修复后的 MATERIALIZED 查询返回分块 `[81]`、相似度 0.8。None、空范围、未知 ID、跨知识库和重复 ID 均通过。事务回滚后以 to_regclass 确认临时关系不存在。
- 使用现有资料、真实 PostgreSQL 仓储与新进程 BM25，纯向量检索返回 1 条有效结果，混合检索返回 2 条有效结果。知识库 3 有 8 篇 ready 文档，精确向量查询抽查 10 次，中位耗时 5.02 ms、最大 15.82 ms；此结果仅反映当前规模，不能外推大规模性能。使用存量向量作为查询，没有调用外部嵌入服务。
- 修改模块编译通过。Ruff 与 HEAD 逐项比较，3 项既有诊断完全相同，新增诊断为 0；未为消除旧诊断扩改其他代码。

仅重启 ai-service 后，就绪检查 HTTP 200，1153 个分块完成关键词预热。前后均为 6 个知识库、29 篇文档、1153 个分块、1153 条向量、29 篇 ready。向量与元数据整体指纹 `5789b33d2c95ee3565a4614f89e25763`、正文指纹 `3357db789d9313477e395f39bb139ff1`、分块内容指纹 `63f2a6e77cac27e1e1272d4df109a2cd` 前后完全一致。本文不以第三方源码阅读代替本地运行结果，也不把临时表检查冒充生产数据变更。
