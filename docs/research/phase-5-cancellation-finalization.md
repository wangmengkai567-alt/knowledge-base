# 第五阶段取消期间终态持久化：源码调研与采用方案

日期：2026-10-29（Asia/Shanghai）。范围仅限第三个审查问题：阶段操作已经完成，但最后一次 completed 状态保存不在异常保护范围内；此时收到取消会留下 running。失败状态保存期间再次取消，也可能打断收尾并提前释放同文档锁。

本轮按 research 技能由后台代理通过 GitHub 插件实际读取六个既有参考项目的源码，主代理另行核对 LangGraph、Khoj 和 RAGFlow 的上述取消处理入口。搜索只用于定位；以下依据全部是与前两次记录一致的固定提交内容，搜索结果中更新的默认分支不作为结论依据。未运行第三方项目，不以 README 代替实现证据。

## 六个参考项目的核查结果

| 项目与固定提交 | 实际读取源码与行号 | 已确认机制及适用边界 |
|---|---|---|
| RAGFlow `1e6c3e12a37d368707005319c134aa6f73ded905` | [task_worker.go：167–188](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/syncer/task_worker.go#L167)；[sync_document_lock.go：59–67](https://github.com/infiniflow/ragflow/blob/1e6c3e12a37d368707005319c134aa6f73ded905/internal/service/document/sync_document_lock.go#L59) | 有直接可用的生命周期原则。任务执行因系统上下文取消返回后，在现有锁的 defer Unlock 之前，同步调用带 `context.WithoutCancel(ctx)` 的状态恢复，再确认队列消息及返回；释放同步文档锁时也使用脱离父取消、另设期限的清理上下文。这里恢复的是可调度任务，并非本地 failed；采用的是“必要收尾脱离父取消、完成收尾后释放锁”，不复制队列或自动重试。 |
| LangGraph `858891aaa164ebdacf33b8f5e7f88b38b9132211` | [main.py：astream，3587–3593](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/libs/langgraph/langgraph/pregel/main.py#L3587)；[_executor.py：AsyncBackgroundExecutor.__aexit__，194–219](https://github.com/langchain-ai/langgraph/blob/858891aaa164ebdacf33b8f5e7f88b38b9132211/libs/langgraph/langgraph/pregel/_executor.py#L194) | 有直接相关方式：成功回调 `on_chain_end` 也在 try 中；except BaseException 的错误回调通过 `await asyncio.shield(...)` 执行后重抛。后台执行器保存任务引用，在退出时先等待任务结束，再处理异常。采用成功收尾纳入异常范围及受保护收尾的结构；其单次 shield 和 executor 等待不等于本地任意重复取消下的持锁等待，也不证明外部索引事务。 |
| Khoj `ae229ca894c0b80ad84664afcfdde523b5e87057` | [api_chat.py：event_generator 内 monitor_disconnection，753–787](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/routers/api_chat.py#L753)；[同函数 WebSocket 分支，808–834](https://github.com/khoj-ai/khoj/blob/ae229ca894c0b80ad84664afcfdde523b5e87057/src/khoj/routers/api_chat.py#L808) | 有可直接采用的 asyncio 方式：检测断开或中断，先设置 cancellation_event，再 `await asyncio.shield(save_to_conversation_log(...))`，源码注释明确要求中断时保存部分状态、保护保存不受任务取消影响。采用保护状态保存的机制；此处保存聊天记录，未展示本地阶段状态或同文档锁，不能声称一行 shield 就解决锁释放竞态。 |
| Onyx `b8c2225f8eb0bb302b363e54d6c6458f0ed796bd` | [locks.py：async_cache_shared_lock，86–144](https://github.com/onyx-dot-app/onyx/blob/b8c2225f8eb0bb302b363e54d6c6458f0ed796bd/backend/onyx/cache/locks.py#L86) | shield 保护线程池中的锁获取 future；捕获取消后给 future 注册回调，迟到获取的锁仍会在线程中释放，然后重抛。该实现说明取消外层等待不能遗弃真实后台操作，但它保护的是锁获取，取消后允许回调收尾；不能原样用于本地“终态写完才准许下一轮进入”的约束。所读正常释放路径本身也未使用 shield，本次不宣称 Onyx 已完整覆盖重复取消收尾。 |
| Open Notebook `a0f6f3081ae06328f0a451b3a3ef2959ca25721d` | [command_service.py：cancel_command_job，83–92](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/api/command_service.py#L83)；[embedding_commands.py：批量嵌入写入补偿，396–414](https://github.com/lfnovo/open-notebook/blob/a0f6f3081ae06328f0a451b3a3ef2959ca25721d/commands/embedding_commands.py#L396) | cancel_command_job 明确仍依赖 surreal-commands 的取消支持，当前仅记录请求并返回 True；嵌入补偿只捕获 Exception 后 await 删除部分成果，没有所读链路内的 CancelledError/shield 保护。所读入口没有可直接解决本问题的实现；不把日志成功返回当作取消已落实。结论仅限上述入口，不断言外部命令库内部行为。 |
| GPT Researcher `3582a6841fe7dc801e2a34e34ee408641aea9bce` | [server_utils.py：handle_websocket_communication / safe_run，335–352](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/backend/server/server_utils.py#L335)；[finally，406–408](https://github.com/assafelovic/gpt-researcher/blob/3582a6841fe7dc801e2a34e34ee408641aea9bce/backend/server/server_utils.py#L406) | safe_run 捕获 CancelledError 后记录并重抛；连接退出 finally 仅 cancel 正在运行的任务，没有在该入口等待其完成，也没有阶段终态数据库收尾。该实现可佐证取消需传播，但不能直接解决本地残留 running；本次不采用它的仅 cancel 不等待方式。 |

## 采用决定与本地适配要求

有现成机制，采用 LangGraph 的“成功收尾也处于异常保护内”、Khoj 的“取消时 shield 保护持久化”以及 RAGFlow 的“受取消保护的状态收尾完成后才释放锁”。这是按现有 asyncio、仓储和同文档锁实现同一行为，不增加第三方框架或依赖，也不声称逐行移植第三方源码。

本地修复需同时满足：

1. running 保存、阶段 action、completed 保存位于同一个 try。completed 保存期间取消，必须进入取消收尾；不能只保护 action。
2. 收尾保存本阶段 failed。解析阶段还需要保存既有 legacy error 状态，整个必要收尾一起保护，不能只保护其中第一次 await。
3. 将必要收尾建为独立 Task 并保留强引用，使用 shield 防止外层取消传播到它。shield 保护的是内部 Task，外层仍会立即收到 CancelledError，因此外层必须在锁内继续等待同一个 Task 完成，并处理等待期间的重复取消。只创建 shield 后立刻 raise 会留下后台写状态与新一轮处理并发的窗口。
4. 收尾结束后向调用方传播取消；不得把取消当成功或吞掉。原业务异常的 failed 状态保存同样受保护，避免异常收尾遇取消留下 running。取消保护不代表自动重试数据库写入，真实持久化失败须可见。
5. 保护范围仅限短暂、必要的终态写入；解析、嵌入等长操作继续可取消。该机制不能保证进程强制终止或数据库故障期间写入成功，既有启动核实继续负责进程中断恢复。本次不引入备用路径、无限重跑阶段或额外轮次。

## 本地运行验证

主代理通过临时命令执行以下验证，未新增或修改测试、评测文件，未将检查命令保存为脚本。

- 原始复现使用真实 process(stage="keyword")、SQLite 仓储、EmbeddingBiz 与 BM25：索引建立成功后，在最后一次 completed 保存之前取消。修复前数据库仍为 keyword=running、ingestion_status=processing；同一命令修复后保存 failed 并传播 CancelledError，定向重试恢复 ready。保留的实际关键词索引不因取消被虚报为本轮就绪。
- 四个阶段分别在 running 保存前、保存后以及 completed 保存前、保存后取消，共 16 个时点，全部保存本阶段 failed、释放锁并可定向重试到 ready。解析另验证旧 status=ERROR。关键词重试不调用嵌入，已有向量也不重复嵌入。
- 四个阶段分别注入一次 completed 写入异常，全部保存原错误为 failed，重试恢复 ready。这同时验证完成写入已经进入同一个异常保护范围。
- 对四阶段分别覆盖“业务取消后收尾”和“完成写入报错后收尾”共 8 组场景，累计注入 72 次取消。包含失败快照等待和解析 legacy ERROR 等待期间的重复取消；收尾子任务被取消数为零，文档锁始终持有，已排队的定向重试在全部必要收尾完成后才开始，最终 ready，没有迟到写入覆盖新轮次。
- 注入真实持久化接口失败，确认错误向上传递，没有重试写入或声称已保存。检查完成后无遗留的收尾任务。
- 真实 PostgreSQL 使用连接内临时 documents/chunks 表、真实 SQLAlchemy 仓储和关键词索引，验证完成保存前取消后的 failed 持久化及重试 ready。仅外层事务内的临时表用于故障注入；事务回滚后核对临时关系不存在，未对真实资料注入取消或失败。
- 修改模块 Ruff、编译通过。所有故障复现均为协作式任务取消；未将这些检查等同断电、强杀进程或数据库不可用时的持久化保证。
- 仅重启 ai-service 后，health/ready HTTP 200。前后均为 6 个知识库、29 篇文档、1153 个分块、1153 条向量，29 篇 ready。向量及元数据指纹 `5789b33d2c95ee3565a4614f89e25763`、正文指纹 `3357db789d9313477e395f39bb139ff1`、分块内容指纹 `63f2a6e77cac27e1e1272d4df109a2cd` 完全一致。相关文件为 UTF-8 无 BOM、LF，git diff --check 通过；未提交或推送。
