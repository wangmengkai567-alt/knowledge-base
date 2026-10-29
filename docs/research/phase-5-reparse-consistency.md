# 第五阶段重解析中断一致性：源码调研与采用方案

日期：2026-10-29（Asia/Shanghai）。范围仅限第一个审查问题：重解析保存新正文后、替换分块前中断，启动核实误把旧分块和旧索引标为当前就绪。其他审查问题未纳入本次修复。

本轮使用 research 技能，由后台代理通过 GitHub 插件真实读取固定提交的源码；根节点补充读取 Onyx 与 RAGFlow，另一代理读取 Khoj、GPT Researcher 与 LangGraph。以下结论来自实际源码入口，不以 README 的产品能力描述代替实现证据。未部署或运行这些第三方项目；运行验证针对本地修复。

## 六个参考项目的核查结果

| 项目与固定提交 | 实际源码、函数及行号 | 已确认机制与适用边界 |
|---|---|---|
| RAGFlow `1e6c3e12a37d368707005319c134aa6f73ded905` | [ingestion_service.go：docRunFromTasks，1150–1173](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/ingestion/service/ingestion_service.go#L1150)；[ingestion_task_service.go：ensureRunIdentity，701–764](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/service/ingestion_task_service.go#L701) | 最新解析轮次的任务状态是文档运行状态的权威依据，历史任务被忽略；创建运行身份时在文档行锁事务中分配 run_count，并绑定 task 与 pipeline log。采用其“当前轮次未完成，不能靠历史成果升级完成”的原则；本地无需引入其队列、Go 服务或独立运行日志系统。 |
| Onyx `b8c2225f8eb0bb302b363e54d6c6458f0ed796bd` | [indexing_pipeline.py，1662–1751](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/indexing/indexing_pipeline.py#L1662)；[document_indexing_adapter.py：post_index，234–293](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/indexing/adapters/document_indexing_adapter.py#L234) | write_chunks 后核实完整性，再执行 post_index；273–280 行只为确认索引写成功的文档更新 chunk count，pipeline 1736–1751 行仅为 insertion_records 成功的文档写 hash，并在锁上下文中提交。采用“实际写成功后提交完成证据”的顺序；其计数/hash 标记不等于本地 ready，也不能据此声称它为本地全部索引提供代际原子性。 |
| Open Notebook `a0f6f3081ae06328f0a451b3a3ef2959ca25721d` | [source.py：save_source，270–298](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/open_notebook/graphs/source.py#L270)；[notebook.py：Source.vectorize，538–567](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/open_notebook/domain/notebook.py#L538)；[embedding_commands.py：embed_source_command，314–414](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/commands/embedding_commands.py#L314) | save_source 先保存 full_text，再提交后台向量化任务；向量命令先删除旧 source_embedding，生成完整嵌入后批量写入，写入异常时删除部分成果再抛错。这里的异常补偿能避免已捕获的部分写入被误当完整索引，但所读链路没有正文/分块的共同代际提交证据，不能原样解决本地启动误判窗口。 |
| Khoj `ae229ca894c0b80ad84664afcfdde523b5e87057` | [text_to_entries.py：TextToEntries.update_embeddings，142–254](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/processor/content/text_to_entries.py#L142)；[database/adapters/__init__.py：update_raw_text，1849–1852](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/database/adapters/__init__.py#L1849) | 按内容 hash 复用 Entry；生成新增嵌入，更新 FileObject.raw_text，逐批 bulk_create 新 Entry 后删除旧条目。块文本与向量位于同一 Entry，但上述普通文档链路没有包住全过程的统一事务或处理代际；raw_text 的独立保存仍不能证明其与既有条目属于同轮。散列复用也不等于代际提交。本次不采用其散列或文件命名方式。 |
| GPT Researcher `3582a6841fe7dc801e2a34e34ee408641aea9bce` | [deep_research.py：deep_research，386–583](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/gpt_researcher/skills/deep_research.py#L386) | 所读入口复制并汇总内存 learnings/citations/URL，执行网页研究、递归查询并归并来源。该入口没有普通文档正文、分块提交或启动核实机制，不能作为此次一致性修复的直接实现依据。此结论仅限所读入口。 |
| LangGraph `858891aaa164ebdacf33b8f5e7f88b38b9132211` | [langgraph_agentic_rag.ipynb，89–114、447](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/examples/rag/langgraph_agentic_rag.ipynb#L89)；[langgraph_adaptive_rag.ipynb，113–148、782](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/examples/rag/langgraph_adaptive_rag.ipynb#L113) | 两个既有参考示例都是一次性加载网页、切分并建立 Chroma，workflow.compile() 没有传 checkpointer。它们演示问答检索图，没有本地重解析旧成果替换和启动一致性边界；不能把图节点或 checkpoint 等同外部索引事务。本次不引入 LangGraph。 |

## 对所采用机制的进一步核实

RAGFlow 的 [ReloadAndValidateRunIdentity，383–411](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/service/ingestion_task_service.go#L383) 会检查绑定日志存在、文档及知识库归属、正数 run_count；[advanceOpenLog，771–787](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/service/ingestion_task_service.go#L771) 仅推进已绑定日志，不能由 worker 收养或创造另一轮身份。因此本次借鉴有明确源码基础：旧运行成果不能替代当前运行完成证据。

其 [pipeline_executor.go，340–362](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/ingestion/task/pipeline_executor.go#L340) 在写入分块、协调编译产物等步骤成功后才返回结果，并延后完成事件；[ingestion_service.go，899–904](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/ingestion/service/ingestion_service.go#L899) 在任务完成后才发布编译事件。但 [reconcileDocumentCompiledProducts，567–603](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/ingestion/task/pipeline_executor.go#L567) 的旧代际清理针对编译产物，不能外推为所有普通分块的原子发布。

Khoj 还存在 [atomic_update_agent，846–908](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/database/adapters/__init__.py#L846) 的真实 transaction.atomic 示例，事务中删除/复制 FileObject 与 Entry。它属于 Agent 参考文件复制，不能冒称普通文档入库已使用该事务；只能说明同库原子替换的边界，不能直接覆盖本项目分开写入的向量存储与进程内 BM25。

## 本地采用与适配

采用 RAGFlow 的“当前轮次状态权威”及 Onyx 的“实际写成功后提交标记”原则，适配现有单后端进程、同文档互斥和持久化 stages。每个文档已有唯一当前阶段快照；本次不新增 generation 字段、任务队列、第三方框架或运行日志系统，也不移植第三方源码。

1. 启动时保留当前 parse/chunk 状态；running 转为可重试的 failed，pending/failed 不因旧正文、连续旧分块或旧向量存在而升级 completed。
2. 仅对已明确 completed 的解析和分块阶段核查实际正文与连续分块，再核实向量并重建关键词。旧成果存在只是物理存在，不能为当前未完成轮次提供完成证据。
3. 新分块仍由现有同库替换事务实际提交后，才发布 chunk 完成状态。替换前中断，当前阶段保持未完成；替换成功而完成标记尚未保存便中断，也保持未完成并允许定向重试，不将不确定结果自动发布为就绪。
4. 旧记录无 stages 的一次性事实核实放在显式迁移；迁移不覆盖现代 pending/failed。启动遇到未迁移的空阶段记录明确拒绝服务，不设运行时旧状态兼容分支。

这直接阻断已复现的 NEW 正文 / OLD 分块窗口：即使旧分块连续且旧向量完整，当前 chunk=pending 仍不是 completed，文档不会 ready，旧块不会在本次启动被加入新的关键词索引。重试分块后才建立与新正文对应的新分块和索引。

## 本地验证记录

以下由根节点执行并提供结果，未新增或修改测试文件，未修改真实原文件；隔离检查通过临时命令完成。

- 在真实 process(force=True) 保存 parse=completed 后模拟进程终止：原实现重启得到 NEW 正文 / OLD 分块 / ready；修复后得到 NEW / OLD / pending，嵌入调用为 0，新关键词库没有 OLD。
- 定向 chunk 重试后得到 NEW 内容及新分块 ID，解析累计调用 1 次、嵌入调用 1 次；再次启动没有新增嵌入调用。
- 使用真实 SQLite 仓储核查 parse/chunk 的 pending、running、failed 六种边界，均未被旧成果升级为完成。
- 旧结构添加 stages、仅旧记录回填、重复执行迁移均通过，现代 pending 保持原状；空 stages 未迁移时启动明确拒绝。
- 生产显式迁移成功，仅重启 ai-service；health/ready 返回 200。仍为 6 个知识库、29 篇文档、1153 个分块、1153 条向量，29 篇四阶段均 completed；向量内容与元数据整体指纹前后相同。
- Python 编译、核心 Ruff 与 git diff --check 通过；未提交或推送。

代码修复范围为 internal/biz/ingestion.py 与 internal/data/ingestion_migration.py。此记录只支持本次第一个 bug 的修复，不代表其余审查问题或第六阶段完整质量评测已经完成。
