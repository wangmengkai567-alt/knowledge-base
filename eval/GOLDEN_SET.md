# 黄金问题草稿（45 条）— 按 knowleage-database

不可答题标准回答必须是：`我不知道`。

---

## A. Agent（sandbox / shell / security / context / memory / harness / 可观测）共 16 条

### A01
- 问：Agent 开发里 sandbox 是什么？用来解决什么问题？
- 来源：`03.sandbox.md`
- 可答：是
- 要点：受限制的隔离执行环境，让 Agent 跑代码/Shell/读写文件，不直接破坏宿主机。不是某一种具体工具。
- 必须出现：隔离 或 sandbox
- 测：both

### A02
- 问：sandbox 里文件系统默认只允许 Agent 访问哪个目录？
- 来源：`03.sandbox.md`
- 可答：是
- 要点：只允许 `/workspace`
- 必须出现：`/workspace`
- 测：both

### A03
- 问：Shell、Process、Sandbox 分别是什么角色？
- 来源：`03.sandbox.md`
- 可答：是
- 要点：Shell 是命令解释器；Process 是程序运行实例；Sandbox 限制这些程序的运行范围。
- 必须出现：解释器
- 测：both

### A04
- 问：Agent 判断一条命令有没有执行成功，应该先看 stderr 还是 exit code？有 stderr 就等于失败吗？
- 来源：`01.shell-bash.md`
- 可答：是
- 要点：首先看 exit code（0 成功，非 0 失败）。有 stderr 不等于失败，成功时也可能往 stderr 打日志。
- 必须出现：exit code
- 幻觉陷阱：有 stderr 就是失败
- 测：both

### A05
- 问：Linux 里 stdin、stdout、stderr 的文件描述符编号分别是多少？
- 来源：`01.shell-bash.md`
- 可答：是
- 要点：0 stdin，1 stdout，2 stderr
- 必须出现：`0`、`1`、`2`
- 测：both

### A06
- 问：SIGKILL 能不能被程序捕获后做清理？和 SIGINT、SIGTERM 有何不同？
- 来源：`01.shell-bash.md`
- 可答：是
- 要点：SIGKILL 不能被捕获、忽略或处理。SIGINT（Ctrl+C）和 SIGTERM 可以被捕获并清理。
- 必须出现：不能
- 测：both

### A07
- 问：PowerShell 里查看当前目录、列出文件，分别对应哪条命令？Bash 里又是什么？
- 来源：`02.powershell.md`
- 可答：是
- 要点：Get-Location / Get-ChildItem；Bash 是 pwd / ls
- 必须出现：`Get-Location`
- 测：both

### A08
- 问：PowerShell 的管道和 Bash 管道最大的差别是什么？
- 来源：`02.powershell.md`
- 可答：是
- 要点：PowerShell 管道通常传 .NET 对象，不是单纯文本
- 必须出现：对象
- 测：both

### A09
- 问：Agent Security 要防止的是什么？最小权限原则怎么理解？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：防止 Agent 有了工具/文件/Shell/网络等能力后做不该做的事。Least Privilege：只给完成任务的最小权限。低风险自动、高风险必须人工确认。
- 必须出现：最小权限
- 测：both

### A10
- 问：什么是间接提示词注入？恶意指令可能藏在哪些地方？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：攻击者把指令放进网页、PDF、GitHub Issue、README、邮件、数据库、搜索结果、文档、代码注释，Agent 自己读到后当指令执行。外部数据不能都当可信指令。
- 必须出现：注入
- 测：both

### A11
- 问：哪些操作通常必须 Human-in-the-loop 确认？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：高风险：删文件、git push、发邮件、改生产库、部署生产、买资源、改云服务器。中风险如改代码、装依赖、建文件可按策略确认。
- 必须出现：git push 或 邮件 或 生产
- 测：both

### A12
- 问：笔记里怎么区分 shell、sandbox、security？
- 来源：`04.agent_security.md`
- 可答：是
- 要点：shell = 怎么执行命令；sandbox = 在哪里执行；security = 允许执行什么、怎么防滥用
- 必须出现：哪里 或 允许
- 测：both

### A13
- 问：Context Engineering 和 Prompt Engineering 有什么区别？
- 来源：`05.Context_Engineering.md`
- 可答：是
- 要点：Prompt 管「怎么告诉模型」；Context Engineering 管「给模型什么信息」（历史、Memory、Tool Result、RAG 等）。核心是管理 LLM 看到什么，不是堆尽可能多的信息。
- 必须出现：信息
- 测：both

### A14
- 问：Agent Memory 和这一轮 Context 有什么不同？Memory 和 RAG 目标差在哪？
- 来源：`06.memory.md`、`05.Context_Engineering.md`
- 可答：是
- 要点：Memory = 跨时间该记住什么；Context = 这一轮 LLM 该看到什么。RAG 从外部知识库取知识；Memory 记过去交互、用户信息和任务经验。
- 必须出现：长期 或 跨
- 测：both

### A15
- 问：Agent Harness 是什么？它和 sandbox、和 LangChain 这类框架怎么区分？
- 来源：`07.agent-harness.md`
- 可答：是
- 要点：Harness 是 Agent 的运行时与控制层（工具、状态、权限、超时、HITL、可观测）。Sandbox 是 Harness 的组成部分。LangChain/LangGraph 更偏编排框架，Harness 更偏实际运行与控制环境。
- 必须出现：运行 或 控制
- 测：both

### A16
- 问：Agent Observability 的三大支柱是什么？它和 Evaluation 有何不同？
- 来源：`08.Observability.md`
- 可答：是
- 要点：Logs / Metrics / Traces。Observability 看 Agent 怎么执行的；Evaluation 看做得好不好。
- 必须出现：Trace 或 追踪
- 测：both

---

## B. Git / Linux / Python / FastAPI / 机器学习 共 8 条

### B01
- 问：Git 真正的作用是备份工具吗？工作区到 GitHub 要经过哪几个区域、哪几条命令？
- 来源：`02.Git.md`
- 可答：是
- 要点：是版本控制不是备份。工作区 → git add 暂存区 → git commit 本地仓库 → git push 远程。
- 必须出现：`git add`、`git commit`、`git push`
- 测：both

### B02
- 问：git clone 第一次会帮你做哪些事？
- 来源：`02.Git.md`
- 可答：是
- 要点：第一次下载远程仓库：建目录、下代码、下历史、建立远程连接
- 必须出现：clone
- 测：both

### B03
- 问：Linux 没有 C 盘 D 盘，所有文件挂在哪？`/etc` 和 `/var` 分别放什么？
- 来源：`03.Linux.md`
- 可答：是
- 要点：同一棵目录树，根是 `/`。/etc 系统配置；/var 日志、数据库等可变数据
- 必须出现：`/etc`
- 测：both

### B04
- 问：Python 里 list 和 tuple 有什么区别？
- 来源：`01.python.md`
- 可答：是
- 要点：list 可修改；tuple 不可修改
- 必须出现：不可
- 测：both

### B05
- 问：什么是装饰器？
- 来源：`01.python.md`
- 可答：是
- 要点：不修改原函数代码的情况下为函数增加额外功能
- 必须出现：不修改 或 额外
- 测：both

### B06
- 问：FastAPI 为什么大量用 Pydantic？Depends 是干什么的？
- 来源：`05.FastAPI.md`
- 可答：是
- 要点：Pydantic 做数据校验和解析。Depends 是依赖注入。
- 必须出现：Pydantic
- 测：both

### B07
- 问：机器学习相对传统编程，流程反过来的那一点是什么？
- 来源：`07.机器学习.md`
- 可答：是
- 要点：传统是数据+规则→结果；机器学习是数据+正确答案→训练出规则（模型）再预测
- 必须出现：数据
- 测：both

### B08
- 问：笔记里写 FastAPI 开发服务常用什么命令启动？
- 来源：`05.FastAPI.md`
- 可答：是
- 要点：`uvicorn main:app --reload`
- 必须出现：`uvicorn`
- 测：both

---

## C. RAG 共 4 条

### C01
- 问：标准 RAG 的写路径（索引）包含哪几步？
- 来源：`面试.md`（RAG 目录）
- 可答：是
- 要点：解析 → 分块 → Embedding → 写入向量库和/或 BM25 倒排
- 必须出现：分块、embedding 或 向量
- 测：both

### C02
- 问：向量检索和 BM25 的分数为什么不能直接加权？常用什么融合？
- 来源：`面试.md`
- 可答：是
- 要点：余弦大约 0–1，BM25 无上界，量纲不同。常用 RRF 按排名融合。
- 必须出现：RRF
- 测：both

### C03
- 问：RAG 解决什么问题？不解决什么问题？
- 来源：`面试.md`
- 可答：是
- 要点：推理时塞私有资料，不更新权重。不解决推理能力不够，不替代微调和 Agent。
- 必须出现：权重 或 推理
- 测：both

### C04
- 问：没有相关块时该怎么做？生成失败能不能把检索摘录当成模型答案？
- 来源：`面试.md`
- 可答：是
- 要点：不要硬编，直接说不知道。生成失败不要把摘录伪装成模型答案。
- 必须出现：不知道
- 测：both

---

## D. 计算机基础（数据库 / 网络 / 操作系统）共 8 条

### D01
- 问：事务 ACID 四个字母各靠 InnoDB 什么机制？
- 来源：`database.md`
- 可答：是
- 要点：A Undo；C 是目标靠 A/I/D+约束；I 锁+MVCC；D Redo（WAL）
- 必须出现：Undo、Redo
- 测：both

### D02
- 问：MySQL InnoDB 默认隔离级别是什么？脏读、不可重复读、幻读分别怎么解释？
- 来源：`database.md`
- 可答：是
- 要点：默认 Repeatable Read。脏读=读到未提交；不可重复读=同一行两次结果不同（UPDATE/DELETE）；幻读=范围多出/少了行（INSERT/DELETE）
- 必须出现：Repeatable 或 RR 或 可重复读
- 测：both

### D03
- 问：MySQL 8.0 还有查询缓存吗？
- 来源：`database.md`
- 可答：是
- 要点：已删除，别当现状答
- 必须出现：删除 或 没有
- 测：both

### D04
- 问：TCP 和 UDP 在连接、可靠、头部大小上有何不同？
- 来源：`network.md`
- 可答：是
- 要点：TCP 面向连接可靠，头≥20B；UDP 无连接不保证，头 8B。TCP 是字节流会粘包，UDP 是数据报。
- 必须出现：`20`、`8`
- 测：both

### D05
- 问：三次握手为什么不是两次？
- 来源：`network.md`
- 可答：是
- 要点：两次会因延迟的旧 SYN 造成半开连接；第三次让双方 ISN 都被确认
- 必须出现：SYN 或 半开 或 ISN
- 测：both

### D06
- 问：谁会进入 TIME_WAIT？大概等多久、为什么是 2MSL？
- 来源：`network.md`
- 可答：是
- 要点：主动关闭方；约 2MSL（Linux 常见约 60s 量级）。保证最后 ACK 到达，并让旧报文死掉。
- 必须出现：2MSL 或 主动
- 测：both

### D07
- 问：进程和线程在资源、地址空间、故障隔离上差在哪？
- 来源：`os.md`
- 可答：是
- 要点：进程是资源分配单位，地址空间独立；线程共享进程地址空间，一个线程踩内存可能拖垮整个进程。
- 必须出现：地址空间
- 测：both

### D08
- 问：共享内存作为 IPC 为什么快？它自带同步吗？
- 来源：`os.md`
- 可答：是
- 要点：拷贝次数最少、最快；本身不提供同步，必须再配信号量/互斥锁
- 必须出现：同步 或 锁
- 测：both

---

## E. 后端（Go / RPC / 加密）共 5 条

### E01
- 问：Go 的 slice 和 array 有什么区别？nil map 能写吗？
- 来源：`go.md`
- 可答：是
- 要点：数组长度是类型一部分、值类型整份拷；切片是指针+len+cap，共享底层数组。nil map 读安全，写会 panic，必须 make。map 不是并发安全。
- 必须出现：len 或 cap
- 测：both

### E02
- 问：Go 里 rune 是什么？
- 来源：`go.md`
- 可答：是
- 要点：int32 别名，表示 Unicode 码点；一个汉字一个 rune
- 必须出现：int32 或 Unicode
- 测：both

### E03
- 问：RPC 是一种协议吗？gRPC 默认用什么传输和序列化？
- 来源：`rpc.md`
- 可答：是
- 要点：RPC 是调用模型不是协议。gRPC 默认 HTTP/2 + Protobuf。
- 必须出现：HTTP/2、Protobuf
- 幻觉陷阱：RPC 等于 HTTP/2
- 测：both

### E04
- 问：加密、哈希、编码、签名各解决什么？Base64 算加密吗？
- 来源：`加密算法.md`
- 可答：是
- 要点：加密可逆保密；哈希不可逆指纹；编码谁都能还原零安全；签名认证+不可否认。Base64 不是加密。
- 必须出现：可逆 或 Base64
- 测：both

### E05
- 问：用户密码能不能用 MD5 存？应该用什么思路？
- 来源：`加密算法.md`
- 可答：是
- 要点：不能。要用带盐的慢哈希（bcrypt / Argon2）等 KDF。
- 必须出现：不能 或 bcrypt 或 Argon
- 测：both

---

## F. 易混数字 / 跨文档 共 2 条

### F01
- 问：sandbox、agent_security、agent-harness 三篇笔记里举的 CPU/内存限制数字一样吗？
- 来源：`03.sandbox.md`、`04.agent_security.md`、`07.agent-harness.md`
- 可答：是
- 要点：**不一样**，都是示例。sandbox 举例 CPU≤2 核、内存≤2GB、时间≤30s；security 举例 1 核、512MB、30s、20 进程、1GB 盘；harness 举例 2 核、4GB、10GB 盘、10min。不能混成一套「标准配置」。
- 必须出现：不一样 或 示例 或 不同
- 幻觉陷阱：统一答成 2GB 或 512MB
- 测：generation（很容易幻觉）

### F02
- 问：DeepSeek Harness（dsh）里 Turn 和 Step 分别指什么？模型看见的历史必须从哪来？
- 来源：`dsh-工作原理.md`
- 可答：是
- 要点：Turn=一次用户任务；Step=一次模型请求+它触发的工具。历史必须先写进 Session 日志，用 deriveMessages 投影，不能内存里偷偷拼 prompt。
- 必须出现：Session 或 日志
- 测：both

---

## G. 不可答 / 拒答 共 5 条

这些在已上传、有正文的笔记里**没有答案**（对应文件为空，或完全是资料外事实）。

### G01
- 问：Docker 的 overlay2 存储驱动具体怎么工作？
- 来源：无（`04.Docker.md` 是空文件；Linux 笔记只提到 Docker 这个词）
- 可答：否
- 标准：我不知道
- 幻觉陷阱：用通用 Docker 常识编一套 overlay2 原理
- 测：refusal

### G02
- 问：红黑树插入时的三种旋转要怎么画？
- 来源：无（`06.数据结构.md` 为空）
- 可答：否
- 标准：我不知道
- 测：refusal

### G03
- 问：Python asyncio 里 Task 和 Future 的区别，以及事件循环调度细节？
- 来源：无（`08.异步编程.md` 为空）
- 可答：否
- 标准：我不知道
- 测：refusal

### G04
- 问：这个知识库作者的微信号是多少？
- 来源：无
- 可答：否
- 标准：我不知道
- 测：refusal

### G05
- 问：LeetCode 第 1 题 Two Sum 在这份库里的参考代码是什么？
- 来源：无（`LeetCode.md` 为空）
- 可答：否
- 标准：我不知道
- 测：refusal

---

改题时请核对：实际上传文件名是否带 `01.` 前缀；多库的话把题分到对应 `knowledge_base_id`。
