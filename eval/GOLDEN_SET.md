# 黄金问题草稿（100 条）— 按 knowleage-database

不可答题标准回答必须是：`我不知道`。

出题范围：有正文且大于 1KB 的 Markdown。空文件、≤1KB 的目录页不算来源。
评测按每题的 `relevant_filenames` / `kb_hint` 自动选知识库，不必改顶层 `knowledge_base_id`。

---

## A. Agent（sandbox / shell / security / context / memory / harness / 编排 / Multi-Agent / 面试）共 28 条

### A01
- 问：Agent 开发里 sandbox 是什么？用来解决什么问题？
- 来源：`03.sandbox.md`
- 可答：是
- 要点：Sandbox 是受限制的隔离执行环境，让 Agent 执行代码、Shell、读写文件，而不直接破坏宿主机。它不是某一种具体工具。
- 必须出现：隔离
- 幻觉陷阱：把 sandbox 说成 Docker 的别名且资料未写死
- 测：both

### A02
- 问：sandbox 里文件系统默认只允许 Agent 访问哪个目录？
- 来源：`03.sandbox.md`
- 可答：是
- 要点：只允许访问 /workspace。
- 必须出现：`/workspace`
- 幻觉陷阱：/home
- 幻觉陷阱：C:\
- 测：both

### A03
- 问：Shell、Process、Sandbox 分别是什么角色？
- 来源：`03.sandbox.md`
- 可答：是
- 要点：Shell 是命令解释器；Process 是程序运行实例；Sandbox 是限制这些程序运行范围的隔离环境。
- 必须出现：解释器
- 测：both

### A04
- 问：Agent 判断一条命令有没有执行成功，应该先看什么？有 stderr 就等于失败吗？
- 来源：`01.shell-bash.md`
- 可答：是
- 要点：首先看 exit code：0 成功，非 0 失败。有 stderr 不等于执行失败，成功时也可能往 stderr 输出。
- 必须出现：`exit code`
- 幻觉陷阱：有 stderr 就是失败
- 测：both

### A05
- 问：Linux 里 stdin、stdout、stderr 的文件描述符编号分别是多少？
- 来源：`01.shell-bash.md`
- 可答：是
- 要点：0 是 stdin，1 是 stdout，2 是 stderr。
- 必须出现：`0`、`1`、`2`
- 幻觉陷阱：1 stdin
- 测：both

### A06
- 问：SIGKILL 能不能被程序捕获后做清理？和 SIGINT、SIGTERM 有何不同？
- 来源：`01.shell-bash.md`
- 可答：是
- 要点：SIGKILL 不能被捕获、忽略或处理。SIGINT（Ctrl+C）和 SIGTERM 可以被捕获并做清理。
- 必须出现：忽略
- 幻觉陷阱：SIGKILL 可以捕获
- 测：both

### A07
- 问：PowerShell 里查看当前目录、列出文件分别是哪条命令？Bash 里对应什么？
- 来源：`02.powershell.md`
- 可答：是
- 要点：PowerShell：Get-Location、Get-ChildItem。Bash：pwd、ls。
- 必须出现：`Get-Location`
- 幻觉陷阱：PowerShell 也用 pwd 作为正式命令名
- 测：both

### A08
- 问：PowerShell 的管道和 Bash 管道最大的差别是什么？
- 来源：`02.powershell.md`
- 可答：是
- 要点：PowerShell 管道通常传递的是 .NET 对象，不是单纯文本。
- 必须出现：对象
- 测：both

### A09
- 问：Agent 有了工具、文件、Shell 之后，安全上要防的是什么？权限应该怎么给？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：防止 Agent 拥有工具、文件、Shell、网络等能力后做出不该做的事。Least Privilege：只获得完成任务的最小权限；低风险可自动，高风险必须人工确认。
- 必须出现：最小权限
- 测：both

### A10
- 问：什么是间接提示词注入？恶意指令可能藏在哪些地方？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：攻击者把恶意指令放进网页、PDF、GitHub Issue、README、邮件、数据库、搜索结果、文档、代码注释，Agent 读取后当成指令。外部数据不能都当可信指令。
- 必须出现：外部
- 测：both

### A11
- 问：哪些操作通常必须 Human-in-the-loop 确认？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：高风险通常要人确认：删除文件、git push、发送邮件、修改生产数据库、部署生产、购买资源、修改云服务器。
- 必须出现：`git push`
- 测：both

### A12
- 问：shell、sandbox、security 三者怎么区分？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：shell 是怎么执行命令；sandbox 是在哪里执行命令；security 是允许执行什么以及怎么防止滥用。
- 必须出现：哪里、允许
- 测：both

### A13
- 问：Context Engineering 和 Prompt Engineering 有什么区别？
- 来源：`05.Context_Engineering.md`
- 可答：是
- 要点：Prompt Engineering 关注怎么告诉模型；Context Engineering 关注给模型什么信息（历史、Memory、Tool Result、RAG 等），管理 LLM 看到什么，而不是堆尽可能多的信息。
- 必须出现：看到
- 测：both

### A14
- 问：Agent Memory 和这一轮 Context 有什么不同？Memory 和 RAG 的目标差在哪？
- 来源：`06.memory.md`、`05.Context_Engineering.md`
- 可答：是
- 要点：Memory 是跨时间该记住什么；Context 是这一轮 LLM 该看到什么。RAG 从外部知识库取知识，Memory 保存过去交互、用户信息和任务经验。
- 必须出现：跨
- 幻觉陷阱：Memory 就是 RAG
- 测：both

### A15
- 问：Agent Harness 是什么？它和 sandbox、和 LangChain 这类框架怎么区分？
- 来源：`07.agent-harness.md`
- 可答：是
- 要点：Harness 是 Agent 的运行时与控制层。Sandbox 是 Harness 的组成部分。LangChain/LangGraph/AutoGen 更偏编排框架，Harness 更强调实际运行与控制环境。
- 必须出现：运行时
- 幻觉陷阱：Harness 就是 LangChain
- 测：both

### A16
- 问：Agent Observability 的三大支柱是什么？它和 Evaluation 有何不同？
- 来源：`08.Observability.md`
- 可答：是
- 要点：Logs、Metrics、Traces。Observability 关注 Agent 是怎么执行的；Evaluation 关注做得好不好。
- 必须出现：`Trace`
- 测：both

### A17
- 问：Agent 开发里「编排」指什么？它要组织哪些事情？
- 来源：`09.编排方式.md`
- 可答：是
- 要点：编排是对多个 Agent 的执行顺序、任务分配、状态传递、通信方式和终止条件进行组织和控制。
- 必须出现：终止
- 测：both

### A18
- 问：Router 和 Supervisor 的核心差别是什么？
- 来源：`09.编排方式.md`
- 可答：是
- 要点：Router 的核心是选择谁，通常选完 Agent 就结束；Supervisor 会持续决定下一步做什么，形成选择 Agent → 执行 → 更新状态 → 再判断的循环。
- 必须出现：下一步
- 幻觉陷阱：Router 会一直调度到底
- 测：both

### A19
- 问：Handoff 和 Supervisor，谁来决定把任务交给另一个 Agent？
- 来源：`09.编排方式.md`、`10.Multi-Agent.md`
- 可答：是
- 要点：Handoff 是当前 Agent 主动把控制权交给另一个 Agent；Supervisor 模式下 Agent 本身不负责最终调度结构，由中心监督者调度。
- 必须出现：控制权
- 幻觉陷阱：Handoff 必须经过 Supervisor
- 测：both

### A20
- 问：Debate / 多 Agent 辩论适合什么、不适合什么？会增加哪些成本？
- 来源：`09.编排方式.md`
- 可答：是
- 要点：适合复杂推理、代码 Review、方案比较、风险分析；不适合简单任务和对延迟敏感的任务。多个 Agent 会增加 Token Cost、Latency 和系统复杂度。
- 必须出现：`Token`
- 测：both

### A21
- 问：Graph 编排相对传统 A→B→C→End 的 Workflow，多了哪两类能力？代码 Agent 测试失败后常见怎么走？
- 来源：`09.编排方式.md`
- 可答：是
- 要点：执行路径可以按条件分支，也可以循环迭代。例如 Coder → Tester，测试失败回到 Coder，通过再去 Reviewer。
- 必须出现：循环
- 测：both

### A22
- 问：固定流程、独立子任务、按问题类型选 Agent，分别该用哪种编排？
- 来源：`09.编排方式.md`
- 可答：是
- 要点：固定流程用 Sequential；独立任务用 Parallel；根据问题类型选择 Agent 用 Router。
- 必须出现：`Sequential`、`Parallel`、`Router`
- 测：both

### A23
- 问：Workflow 和 Agent 的执行路径分别由谁决定？
- 来源：`10.Multi-Agent.md`
- 可答：是
- 要点：Workflow 的执行路径主要由开发者定义，确定性高；Agent 的执行路径由模型根据当前状态动态决定。
- 必须出现：开发者
- 幻觉陷阱：Agent 路径也必须写死
- 测：both

### A24
- 问：Multi-Agent 编排从本质上有哪三种控制来源？各对应什么？
- 来源：`10.Multi-Agent.md`
- 可答：是
- 要点：Developer-controlled 对应 Workflow/Graph/Pipeline；Agent-controlled 对应 Handoff、Agent Loop、动态路由；Hybrid-controlled 是开发者规定整体边界，中间由 Agent 自己决定。
- 必须出现：`Handoff`
- 测：both

### A25
- 问：动态 Multi-Agent 怎么防止 Researcher 和 Reviewer 无限互相调用？常用终止条件有哪些？
- 来源：`10.Multi-Agent.md`
- 可答：是
- 要点：需要 max_iterations、max_steps、timeout、budget、termination_condition。常见终止：review 通过、步数到上限、Supervisor 发出 FINISH、task.status 完成。
- 必须出现：`timeout`
- 测：both

### A26
- 问：Multi-Agent 的核心是「有几个 Agent」吗？编排还要管哪些事？
- 来源：`10.Multi-Agent.md`
- 可答：是
- 要点：核心不是有几个 Agent，而是多个 Agent 如何被组织、调度和协作。编排 = 选择谁 + 做什么 + 什么时候做 + 怎么通信 + 保存什么状态 + 什么时候结束。
- 必须出现：调度
- 幻觉陷阱：堆几个 Agent 就是 Multi-Agent
- 测：both

### A27
- 问：Agent 五大核心组件是什么？
- 来源：`AI_Agent开发100道高频面试题.md`
- 可答：是
- 要点：感知、记忆、规划、工具执行、反思评估。感知负责输入，记忆分层存储，规划拆解目标，工具对接外部能力，反思复盘并修正后续决策。
- 必须出现：感知、反思
- 幻觉陷阱：五大组件是输入输出训练推理部署
- 测：both

### A28
- 问：ReAct 的闭环步骤是什么？它和 CoT 的核心差别？
- 来源：`AI_Agent开发100道高频面试题.md`
- 可答：是
- 要点：ReAct 是 Thought → Action → Observation 再迭代。CoT 只在模型内部推理、不能调工具；ReAct 能想也能做。
- 必须出现：`Observation`、工具
- 幻觉陷阱：CoT 可以调用外部工具
- 测：both

---

## B. Git / Linux / Python / FastAPI / 机器学习 / Docker / 数据结构 / 异步编程共 21 条

### B01
- 问：Git 真正的作用是备份工具吗？从工作区到 GitHub 要经过哪几个区域、用哪几条命令？
- 来源：`02.Git.md`
- 可答：是
- 要点：Git 是版本控制，不是备份工具。工作区 git add 到暂存区，git commit 到本地仓库，git push 到远程。
- 必须出现：`git add`、`git commit`、`git push`
- 幻觉陷阱：Git 等于备份
- 测：both

### B02
- 问：git clone 第一次会帮你做哪些事？
- 来源：`02.Git.md`
- 可答：是
- 要点：第一次下载远程仓库：创建目录、下载代码、下载历史版本、建立远程仓库连接。
- 必须出现：历史、远程
- 测：both

### B03
- 问：Linux 没有 C 盘 D 盘，所有文件挂在哪？配置和日志通常分别放哪个目录？
- 来源：`03.Linux.md`
- 可答：是
- 要点：都在同一棵以 / 为根的目录树。/etc 是系统配置，/var 是日志、数据库等可变数据。
- 必须出现：`/etc`、`/var`
- 测：both

### B04
- 问：Python 里 list 和 tuple 有什么区别？
- 来源：`01.python.md`
- 可答：是
- 要点：list 可修改，tuple 不可修改。
- 必须出现：不可
- 测：both

### B05
- 问：Python 装饰器是什么？
- 来源：`01.python.md`
- 可答：是
- 要点：在不修改原函数代码的情况下，为函数增加额外功能。
- 必须出现：不修改
- 测：both

### B06
- 问：FastAPI 为什么大量用 Pydantic？Depends 是干什么的？
- 来源：`05.FastAPI.md`
- 可答：是
- 要点：Pydantic 做数据校验和解析。Depends 是依赖注入。
- 必须出现：校验、依赖注入
- 测：both

### B07
- 问：机器学习相对传统编程，流程反过来的那一点是什么？
- 来源：`07.机器学习.md`
- 可答：是
- 要点：传统是数据加规则得到结果；机器学习是数据加正确答案训练出规则（模型），再对新数据预测。
- 必须出现：规则
- 测：both

### B08
- 问：FastAPI 开发服务常用什么命令启动？
- 来源：`05.FastAPI.md`
- 可答：是
- 要点：uvicorn main:app --reload
- 必须出现：`uvicorn`
- 幻觉陷阱：flask run
- 测：both

### B09
- 问：Dockerfile、镜像、容器三者什么关系？分别对应哪条命令？
- 来源：`04.Docker.md`
- 可答：是
- 要点：Dockerfile 经 docker build 得到 Image，Image 经 docker run 得到 Container。镜像是模板，容器是运行中的实例。
- 必须出现：`docker build`
- 幻觉陷阱：容器是镜像的模板
- 测：both

### B10
- 问：在 Agent 容器里访问 localhost:5432 等于访问宿主机 Postgres 吗？Compose 里应该写什么地址？
- 来源：`04.Docker.md`
- 可答：是
- 要点：不等于。容器里的 localhost 是当前容器自己。Compose 中应使用服务名，例如 postgres:5432。
- 必须出现：`postgres:5432`
- 幻觉陷阱：容器 localhost 就是宿主机
- 测：both

### B11
- 问：Docker Compose 的 depends_on 能保证数据库已经可以处理请求吗？
- 来源：`04.Docker.md`
- 可答：是
- 要点：不能。它主要控制启动顺序。要等数据库真正就绪，需要 healthcheck，并按健康状态控制依赖启动。
- 必须出现：`healthcheck`
- 幻觉陷阱：depends_on 等于已经 ready
- 测：both

### B12
- 问：为什么不要把 Docker Socket 直接挂给不可信 Agent？
- 来源：`04.Docker.md`
- 可答：是
- 要点：Docker Socket 是守护进程套接字。把 /var/run/docker.sock 挂进容器后，进程就能调用宿主机 Docker，等于拿到宿主机上的容器控制权。
- 必须出现：`docker.sock`
- 测：both

### B13
- 问：Dockerfile 里为什么先 COPY requirements.txt 再 pip install，最后才 COPY 业务代码？
- 来源：`04.Docker.md`
- 可答：是
- 要点：利用分层构建缓存。只改业务代码、依赖文件没变时，可以复用依赖安装层，不必每次重装全部依赖。
- 必须出现：缓存
- 测：both

### B14
- 问：Agent 里为什么常用 Hash Map 做工具注册表，而不是一长串 if-elif？平均复杂度？
- 来源：`06.数据结构.md`
- 可答：是
- 要点：用工具名映射到函数，调用时按名称直接查找。插入、查找、删除平均都是 O(1)。
- 必须出现：`O(1)`
- 测：both

### B15
- 问：Stack 和 Queue 进出顺序差在哪？嵌套子任务更适合哪个？
- 来源：`06.数据结构.md`
- 可答：是
- 要点：Stack 是 LIFO 后进先出，适合嵌套任务；Queue 是 FIFO 先进先出，适合按提交顺序调度。
- 必须出现：`LIFO`、`FIFO`
- 测：both

### B16
- 问：HNSW 用了图结构，所以它就是 Agent 工作流里的 Graph 吗？向量和向量数据库怎么区分？
- 来源：`06.数据结构.md`
- 可答：是
- 要点：不是。HNSW 的目的是加速近似最近邻搜索，不等同于工作流 Graph。向量是数值；向量索引加速相似度查询；向量数据库负责保存、索引和检索向量及相关数据。
- 必须出现：近似、索引
- 幻觉陷阱：HNSW 就是 LangGraph
- 测：both

### B17
- 问：异步编程加速的是一次 LLM 推理算得更快，还是什么？三个互不依赖的 2 秒请求，同步和异步总耗时怎么估？
- 来源：`08.异步编程.md`
- 可答：是
- 要点：加速的是 IO 等待的重叠，不是把单次推理算更快。三个互不依赖的 2 秒请求，同步大约 6 秒，异步大约 2 秒。
- 必须出现：重叠
- 幻觉陷阱：asyncio 会让单次 LLM 从 3 秒变成 0.3 秒
- 测：both

### B18
- 问：在 FastAPI 的 async def 接口里写 time.sleep 或 requests.get 会怎样？
- 来源：`08.异步编程.md`
- 可答：是
- 要点：会把事件循环锁死，所有用户一起卡住。应使用 await asyncio.sleep 和 httpx.AsyncClient 等异步库。
- 必须出现：事件循环
- 测：both

### B19
- 问：asyncio.gather 默认一个任务失败会怎样？并行调多个工具时该怎么设？
- 来源：`08.异步编程.md`
- 可答：是
- 要点：默认会取消还在跑的其它任务并把异常抛给调用方。并行工具应 gather(..., return_exceptions=True)，把失败包装成 tool_result 回给模型。
- 必须出现：`return_exceptions`
- 测：both

### B20
- 问：asyncio.Semaphore 限制的是 QPS 滑动窗口吗？它实际限制什么？
- 来源：`08.异步编程.md`
- 可答：是
- 要点：不是。它限制的是同时进行中的调用数，不是 QPS 滑动窗口。更细的令牌桶可以放在网关。
- 必须出现：同时
- 幻觉陷阱：Semaphore 就是令牌桶
- 幻觉陷阱：Semaphore 就是 QPS
- 测：both

### B21
- 问：调用协程函数却漏了 await，运行时常见什么警告？请求发出去了吗？
- 来源：`08.异步编程.md`
- 可答：是
- 要点：常见 RuntimeWarning: coroutine was never awaited。得到的是 coroutine 对象，请求根本没发出去。
- 必须出现：`never awaited`
- 测：both

---

## C. RAG共 10 条

### C01
- 问：标准 RAG 的写路径（索引）包含哪几步？
- 来源：`面试.md`
- 可答：是
- 要点：解析加载 → 分块 → Embedding → 写入向量库和/或 BM25 倒排。
- 必须出现：分块
- 测：both

### C02
- 问：向量检索和 BM25 的分数为什么不能直接加权？常用什么融合？
- 来源：`面试.md`
- 可答：是
- 要点：余弦大约 0 到 1，BM25 无上界，量纲不同。常用 RRF 按排名融合。
- 必须出现：`RRF`
- 幻觉陷阱：直接加权平均
- 测：both

### C03
- 问：RAG 解决什么问题？不解决什么问题？
- 来源：`面试.md`
- 可答：是
- 要点：推理时把私有资料塞进 Prompt，不更新权重。解决模型没见过的文档、上下文塞不下整库；不解决推理能力不够，也不替代微调和 Agent。
- 必须出现：私有、微调
- 测：both

### C04
- 问：没有相关块时该怎么做？生成失败能不能把检索摘录当成模型答案？
- 来源：`面试.md`
- 可答：是
- 要点：没有相关块就不要硬编，直接说不知道。生成失败不要把检索摘录伪装成模型答案。
- 必须出现：不知道
- 测：both

### C05
- 问：为什么向量数据库比传统数据库更适合语义检索？传统 B 树在高维会遇到什么？
- 来源：`面试.md`
- 可答：是
- 要点：传统库擅长精确匹配，语义检索要近似最近邻。高维时 B 树效率急剧下降，也叫维度灾难。向量库用 ANN 做余弦或欧氏距离检索。
- 必须出现：`ANN`
- 测：both

### C06
- 问：RAG 里检索器、索引构建器、生成器分别做什么？
- 来源：`面试.md`
- 可答：是
- 要点：检索器从知识库取出相关文档片段；索引构建器把文档变成可检索的向量；生成器结合检索结果写出最终回答。
- 必须出现：片段、向量
- 测：both

### C07
- 问：RAG 和 SFT 在数据更新、幻觉、透明度上有什么区别？
- 来源：`面试.md`
- 可答：是
- 要点：RAG 查外部源，数据可动态更新、不必频繁重训；SFT 相对静态。RAG 基于检索证据更不易幻觉、过程更透明；SFT 像黑盒，遇到不熟悉输入仍可能幻觉。
- 必须出现：动态、黑盒
- 幻觉陷阱：SFT 完全不会幻觉
- 测：both

### C08
- 问：RAG-Fusion 用来增强用户意图时，核心手段是什么？
- 来源：`面试.md`
- 可答：是
- 要点：采用多查询策略，生成多个查询变体再融合，捕捉用户没说清的多方面信息需求。
- 必须出现：多查询
- 测：both

### C09
- 问：大模型幻觉一般分成哪几类？
- 来源：`面试.md`
- 可答：是
- 要点：事实性幻觉、逻辑性幻觉、指令跟随偏差。
- 必须出现：逻辑性、指令
- 测：both

### C10
- 问：写索引时文档 Embedding 和之后的问句必须满足什么？块太大或太小各有什么问题？
- 来源：`面试.md`
- 可答：是
- 要点：必须用同一个 embedding 模型、同一维度。块太大主题混杂，块太小论据不完整。
- 必须出现：维度、混杂
- 测：both

---

## D. 计算机基础（数据库 / 网络 / 操作系统）共 12 条

### D01
- 问：事务 ACID 在 InnoDB 里分别主要靠什么？
- 来源：`database.md`
- 可答：是
- 要点：原子性靠 Undo Log 回滚；一致性是目标；隔离性靠锁 + MVCC；持久性靠 Redo Log（WAL）。
- 必须出现：`Undo`、`Redo`
- 幻觉陷阱：一致性只靠隔离级别
- 测：both

### D02
- 问：MySQL InnoDB 默认隔离级别是什么？脏读是读到了什么数据？
- 来源：`database.md`
- 可答：是
- 要点：默认 Repeatable Read（可重复读）。脏读是读到别的事务尚未提交的数据。
- 必须出现：`Repeatable`、未提交
- 幻觉陷阱：默认 Read Committed
- 测：both

### D03
- 问：MySQL 8.0 还有查询缓存吗？
- 来源：`database.md`
- 可答：是
- 要点：已经删除，不要当成现状来答。
- 必须出现：删除
- 幻觉陷阱：8.0 仍默认开查询缓存
- 测：both

### D04
- 问：TCP 和 UDP 在连接、可靠性和头部大小上有何不同？
- 来源：`network.md`
- 可答：是
- 要点：TCP 面向连接、可靠，头部至少 20 字节；UDP 无连接、不保证送达，头部 8 字节。
- 必须出现：`20`、`8`
- 测：both

### D05
- 问：三次握手为什么不是两次？
- 来源：`network.md`
- 可答：是
- 要点：两次会因延迟的旧 SYN 造成半开连接浪费资源；第三次才能让双方 ISN 都被对端确认。
- 必须出现：`SYN`
- 测：both

### D06
- 问：TCP 连接里谁会进入 TIME_WAIT？大概等多久、为什么？
- 来源：`network.md`
- 可答：是
- 要点：主动关闭方进入 TIME_WAIT，时长 2MSL（Linux 上常见约 60 秒量级）。保证最后 ACK 到达，并让旧连接报文死掉。
- 必须出现：主动、`2MSL`
- 幻觉陷阱：被动关闭方 TIME_WAIT
- 测：both

### D07
- 问：进程和线程在资源、地址空间、故障隔离上差在哪？
- 来源：`os.md`
- 可答：是
- 要点：进程是资源分配单位，地址空间相互独立；线程共享同一进程地址空间。一个线程踩内存，整个进程可能挂。
- 必须出现：独立、共享
- 测：both

### D08
- 问：共享内存作为 IPC 为什么快？要不要另外做同步？
- 来源：`os.md`
- 可答：是
- 要点：拷贝次数最少所以最快。本身不提供同步，必须再配信号量或互斥锁。
- 必须出现：拷贝、锁
- 测：both

### D09
- 问：InnoDB 索引为什么不用二叉树或红黑树？B+ 树相对 B 树有哪点优势被点名？
- 来源：`database.md`
- 可答：是
- 要点：二叉树太高；红黑树也不适合磁盘。B 树内节点带数据，同等页里键更少、树更高，范围也不如叶子链表方便。
- 必须出现：磁盘
- 幻觉陷阱：InnoDB 用红黑树当主键索引
- 测：both

### D10
- 问：二级索引找到主键后还要再查整行，这叫什么？查询列都在索引里、不用再查整行又叫什么？
- 来源：`database.md`
- 可答：是
- 要点：二级索引找到主键后再去聚簇索引拿整行叫回表。查询列都在该索引里、不用回表叫覆盖索引。EXPLAIN 里 Extra: Using index。
- 必须出现：回表、覆盖
- 测：both

### D11
- 问：Linux 的 CFS 怎么决定下一个跑哪个进程？
- 来源：`os.md`
- 可答：是
- 要点：用红黑树按 vruntime（虚拟运行时间）排序，每次跑 vruntime 最小的进程。
- 必须出现：红黑树、虚拟运行
- 测：both

### D12
- 问：TCP 为什么会粘包？UDP 有没有应用层消息边界？
- 来源：`network.md`
- 可答：是
- 要点：TCP 是字节流，没有消息边界所以会粘包；UDP 是数据报，保留边界。
- 必须出现：字节流、数据报
- 测：both

---

## E. 后端（Go / RPC / 加密 / 架构 / 设计模式）共 16 条

### E01
- 问：Go 的 slice 和 array 有什么区别？对 nil map 写入会怎样？
- 来源：`go.md`
- 可答：是
- 要点：数组长度是类型一部分，值类型整份拷贝。切片是指针、len、cap，共享底层数组。nil map 读安全，写会 panic，必须 make。
- 必须出现：底层数组、`panic`
- 幻觉陷阱：nil map 可以写入
- 测：both

### E02
- 问：Go 里 rune 是什么？
- 来源：`go.md`
- 可答：是
- 要点：rune 是 int32 的别名，表示 Unicode 码点；一个汉字一个 rune。
- 必须出现：`int32`
- 幻觉陷阱：rune 是 uint8
- 测：both

### E03
- 问：RPC 是一种协议吗？gRPC 默认用什么传输和序列化？
- 来源：`rpc.md`
- 可答：是
- 要点：RPC 是调用模型，不是协议。gRPC 默认 HTTP/2 + Protobuf。
- 必须出现：`HTTP/2`、`Protobuf`
- 幻觉陷阱：RPC 等于 HTTP/2
- 测：both

### E04
- 问：加密、哈希、编码、签名各解决什么？Base64 算加密吗？
- 来源：`加密算法.md`
- 可答：是
- 要点：加密可逆、保机密；哈希不可逆、做指纹；编码谁都能还原、零安全；签名做认证和不可否认。Base64 是编码，不是加密。
- 必须出现：可逆、指纹、不是加密
- 幻觉陷阱：Base64 是加密
- 测：both

### E05
- 问：用户密码能不能用 MD5 存储？应该用什么？
- 来源：`加密算法.md`
- 可答：是
- 要点：不能用 MD5。要用带盐的慢哈希，如 bcrypt、Argon2。
- 必须出现：`bcrypt`
- 幻觉陷阱：MD5 加盐就够
- 测：both

### E06
- 问：一开始就上微服务对不对？什么阶段更适合先不拆服务？
- 来源：`架构模式.md`
- 可答：是
- 要点：不要为了「高级」一开始就微服务。单体适合个人项目、中小型、MVP、校招项目和业务早期。
- 必须出现：单体、`MVP`
- 幻觉陷阱：校招项目必须微服务
- 测：both

### E07
- 问：分层架构里 Controller、Service、Repository 各自负责什么？判断库存够不够应该放哪一层？
- 来源：`架构模式.md`
- 可答：是
- 要点：Controller 处理 HTTP 请求和响应；Service 放真正业务逻辑；Repository 访问数据库等基础设施。库存是否足够属于业务逻辑，应放 Service，不放 Repository。
- 必须出现：`HTTP`、业务、数据库
- 幻觉陷阱：库存判断放在 Repository
- 测：both

### E08
- 问：DDD 的核心思想是围绕数据库表设计吗？领域层常见有哪些概念？
- 来源：`架构模式.md`
- 可答：是
- 要点：不是，是围绕业务领域设计。领域层有实体、值对象、聚合、领域服务、领域事件。
- 必须出现：实体、聚合
- 幻觉陷阱：DDD 就是按表拆模块
- 测：both

### E09
- 问：六边形架构还有一个常用别名是什么？它要让业务核心不要依赖什么？
- 来源：`架构模式.md`
- 可答：是
- 要点：也叫 Ports and Adapters（端口与适配器）。业务核心不应该依赖具体的外部技术，例如不要直接写死 mysql.Query。
- 必须出现：适配器
- 测：both

### E10
- 问：Clean Architecture 最重要的依赖方向是哪句话？
- 来源：`架构模式.md`
- 可答：是
- 要点：外层依赖内层，内层不要依赖外层。业务逻辑不应依赖 Gin、MySQL、Redis。
- 必须出现：外层、内层
- 测：both

### E11
- 问：CQRS 全称和核心思想是什么？普通 CRUD 一定要用吗？
- 来源：`架构模式.md`
- 可答：是
- 要点：Command Query Responsibility Segregation，读和写使用不同模型。普通 CRUD 通常不需要。
- 必须出现：不同模型
- 幻觉陷阱：所有项目都要 CQRS
- 测：both

### E12
- 问：支付方式一堆 if-else，更适合用哪种设计模式？工厂模式的核心思想是什么？
- 来源：`设计模式.md`
- 可答：是
- 要点：多种算法按情况选择用策略模式；工厂模式把对象的创建过程封装起来。
- 必须出现：策略、创建
- 测：both

### E13
- 问：Web 框架里的 Middleware 通常可以用哪种设计模式理解？
- 来源：`设计模式.md`
- 可答：是
- 要点：责任链模式。请求经过日志、CORS、JWT、权限、限流再到达业务 Handler。
- 必须出现：责任链
- 测：both

### E14
- 问：请求先经过 Nginx 再到后面的服务，这更接近哪种设计模式？装饰器模式的核心是什么？
- 来源：`设计模式.md`
- 可答：是
- 要点：Nginx 更接近代理。装饰器是在不修改原对象代码的情况下动态给对象增加功能。
- 必须出现：代理、不修改
- 幻觉陷阱：Nginx 是装饰器
- 测：both

### E15
- 问：能把 Kafka 直接说成观察者模式吗？消息队列和观察者是什么关系？
- 来源：`设计模式.md`
- 可答：是
- 要点：不能简单画等号。MQ 的事件驱动可以看成观察者思想在分布式系统里的扩展，但它们不是同一层东西。
- 必须出现：扩展、分布式
- 幻觉陷阱：Kafka 就是观察者模式
- 测：both

### E16
- 问：微服务之间常见哪三种通信方式？
- 来源：`架构模式.md`
- 可答：是
- 要点：HTTP（REST）、RPC（如 gRPC）、消息队列（RabbitMQ、Kafka、RocketMQ）。
- 必须出现：`gRPC`
- 测：both

---

## F. 易混数字 / 跨文档共 3 条

### F01
- 问：sandbox、agent_security、agent-harness 三处举例的 CPU 和内存限制是同一套数字吗？
- 来源：`03.sandbox.md`、`04.agent_security.md`、`07.agent-harness.md`
- 可答：是
- 要点：不一样，都是示例。sandbox 举例 2 核 / 2GB / 30 秒；security 举例 1 核 / 512MB / 30 秒；harness 举例 2 核 / 4GB / 10 分钟。不能合成一套标准配置。
- 必须出现：不一样
- 幻觉陷阱：统一说成 2GB 或 512MB
- 测：generation

### F02
- 问：DeepSeek Harness（dsh）里 Turn 和 Step 分别指什么？模型看见的历史必须从哪来？
- 来源：`dsh-工作原理.md`
- 可答：是
- 要点：Turn 是一次用户任务；Step 是一次模型请求加上它触发的工具。历史必须先写入 Session 日志再投影，不能在内存里偷偷拼 prompt。
- 必须出现：`Session`
- 测：both

### F03
- 问：Docker 基础沙箱示例的内存限制，和 agent_security 举的 512MB 是同一套标准吗？
- 来源：`04.Docker.md`、`04.agent_security.md`
- 可答：是
- 要点：不是同一套标准。Docker 基础沙箱示例是 --memory 256m、--cpus 1、--pids-limit 64；security 笔记另有 512MB / 1 核等示例，都只是示例。
- 必须出现：`256`
- 幻觉陷阱：统一答成 512MB
- 测：generation

---

## G. 不可答 / 拒答共 10 条

这些在已上传、有正文的笔记里**没有答案**。

### G01
- 问：Docker 的 overlay2 存储驱动具体怎么工作？
- 来源：无
- 可答：否
- 标准：我不知道
- 幻觉陷阱：用通用 Docker 常识编 overlay2
- 测：refusal

### G02
- 问：红黑树插入时的三种旋转要怎么画？
- 来源：无
- 可答：否
- 标准：我不知道
- 测：refusal

### G03
- 问：Trio 的 nursery、CancelScope 和 uvloop 事件循环的源码实现差异是什么？
- 来源：无
- 可答：否
- 标准：我不知道
- 幻觉陷阱：用通用 asyncio 常识作答
- 测：refusal

### G04
- 问：这个知识库作者的微信号是多少？
- 来源：无
- 可答：否
- 标准：我不知道
- 测：refusal

### G05
- 问：LeetCode 第 1 题 Two Sum 在这份库里的参考代码是什么？
- 来源：无
- 可答：否
- 标准：我不知道
- 幻觉陷阱：默写 Two Sum
- 测：refusal

### G06
- 问：Kubernetes Deployment 的 rollingUpdate maxSurge 默认值在这份知识库里是多少？
- 来源：无
- 可答：否
- 标准：我不知道
- 幻觉陷阱：用通用 K8s 常识作答
- 测：refusal

### G07
- 问：这个知识库作者的本科毕业论文题目是什么？
- 来源：无
- 可答：否
- 标准：我不知道
- 测：refusal

### G08
- 问：Redis Cluster 槽位迁移时源码里负责搬槽的函数名是什么？
- 来源：无
- 可答：否
- 标准：我不知道
- 测：refusal

### G09
- 问：这份知识库里有没有写出 BERT 预训练 MLM 的损失函数公式？
- 来源：无
- 可答：否
- 标准：我不知道
- 幻觉陷阱：默写交叉熵公式
- 测：refusal

### G10
- 问：Flink exactly-once 里 checkpoint barrier 对齐的实现写在这份库的哪一页？
- 来源：无
- 可答：否
- 标准：我不知道
- 测：refusal

改题时请核对实际上传文件名；多库时脚本按文件名/库名自动解析。
