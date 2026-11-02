# 旧接口清理与内部故障分类：顺序 3 修复

日期：2026-11-02。接续[调用预算修复](call-budget-step-1-report.md)和[取证结束流程修复](research-finish-step-2-report.md)。

## 完成内容

移除问答测试中旧的 status/answer 生成器、缺少当前接口的假执行器、未完成流程便结束的旧模拟数据。所有问答循环测试统一经过真实 ReadOnlyToolExecutor、MaterialProcessor、分块器、ToolModel 和 RAGBiz；仓储、检索供应商、LLM 返回值使用固定数据。模拟模型明确实现 generate_with_tools 和 generate_answer；对执行器的监测保留真实 execute/validate_sources 调用，不再把来源验证替换成空成功。

工具取证固定使用 required，ToolModel 中未使用的 auto 分支和 require_tool 参数已删除；最终生成接口不接收工具。旧模式测试改为分别检查这两个阶段，SSE 保存一致性、联网权限、后续请求空间等此前可能提前失败却通过断言的测试，均要求流程实际完成，并检查真实的当前请求。

网页测试改用 query/max_results 与 source_id/start/max_chars。未登记的合法编号确实进入来源授权检查，返回 SOURCE_NOT_APPROVED；已登记编号的正例实际调用提供商并返回对应窗口。原 keywords/urls 仅保留为明确拒绝的负例，没有增加生产兼容分支。原网页提供商自身的 read(urls) 是程序内部已授权调用接口，仍为有效用途。

## 内部错误与 SSE

新增 internal_error 运行状态。RAG 中意外发生的程序异常不再标成 model_failed；工具执行器也不再把接口缺失等未知内部错误统一包装成 TOOL_DEPENDENCY_FAILED。已知的模型、工具依赖和超时错误继续使用各自明确的分类。测试分别验证内部模型接口异常、内部工具接口缺失及正常依赖失败，并保留实际用量或未知用量的事实。

[内部诊断](../../ai-service/pkg/errors/diagnostics.py)记录异常类型和堆栈位置（文件、行号、函数）；不记录异常原文、请求内容、源码行或局部变量。对应日志在 RAG、SSE 和记录保存路径统一使用该结构。前端显示“服务内部错误”，不把堆栈传给最终用户。

修复 HTTP SSE 异常分支残留的 version=4，统一为 version=5、tool_contract_version=2、answer_contract_version=3。此前前端会把真实保存/传输失败误显示成“协议已更新，请刷新页面”。本次通过真实路由、服务和模拟保存失败复现，并验证当前错误协议与内容；前端实际 searchApi 源码也验证错误信息能正确呈现、internal_error 终态保留、旧版线上协议仍被拒绝。

旧 UI 的 unsupported_quote、invalid_claim_citation 提示已移除，改为当前证据编号、代码关联与内部错误提示。

## 检查结果与边界

后端 65 项检查：64 通过、0 失败、1 错误。原先工具模式和旧网页参数的两个失败已解决；唯一剩余为 test_budget_counts_complete_messages，即顺序 4 的无句子边界长文本处理，本次未更改其实现或测试。

评估检查 26 项通过，包括新增的实际 v3 保存快照重放与跨运行/未送模证据篡改检查。历史 v1 快照明确命名为 legacy_snapshot，仅验证历史结果的离线读取，不影响当前线上协议，也未改写历史评分或问答记录。

前端类型检查通过；核心及测试修改文件 Ruff 通过。HTTP 路由文件仍有与修改前完全相同的 11 项既有 Ruff 提示（类型导入及默认参数等），没有新增诊断，未将该文件声称为全绿。编码、换行与 git diff --check 通过。

本次检查没有真实 LLM 调用或公网网页请求；网页正例使用固定正文，前端 SSE 使用模拟 fetch。没有运行全量真实问答，没有提高模型、工具、时间或词元上限，没有处理 TLS 网络配置或重建知识库数据。

本地服务已重启，ready 检查通过，运行容器源码与工作区一致。验证记录见[后端与离线检查](evidence/interface-cleanup-step-3-verification.json)、[前端解析检查](evidence/interface-cleanup-step-3-ui.json)和[运行核对](evidence/interface-cleanup-step-3-runtime.json)。
