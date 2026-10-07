# RAG 黄金评测集

题目只根据该目录里**有正文的 Markdown** 出。下面这些文件是空的或几乎为空，**不要当成可答来源**：

- `AI  应用开发/04.Docker.md`（0 字节）
- `AI  应用开发/06.数据结构.md`（0 字节）
- `AI  应用开发/08.异步编程.md`（0 字节）
- `LeetCode/LeetCode.md`（0 字节）
- `Harness/harness.md`、`RAG/RAG.md`（几乎空）


评测前：把 `golden_set.json` 的 `knowledge_base_id` 改成你库里对应库的 ID（Agent / AI 应用开发 / 计算机基础 / 后端 / RAG 可能是多个库）。`relevant_filenames` 按实际上传文件名核对。

## 指标

| 指标 | 算法 |
|------|------|
| Hit@5 / Hit@10 | retrieve top-K 是否出现 `relevant_filenames` 中任意一篇 |
| MRR | 第一篇相关文档排名倒数 |
| 答案正确率 | `must_contain` 是否都在回答里 |
| 幻觉率 | 有实质断言但本次 sources 撑不住 / **有回答的条数**（拒答不算） |
| 拒答正确率 | `answerable=false`（资料内没有答案）且整句「我不知道」 |
| 漏召回 | 资料内有答案，却回答「我不知道」（不算幻觉） |

`answerable` / 「资料内有答案」只表示**文档里有没有答案**，不是题干是非问的「是/否」。例如 A04 问「有 stderr 就等于失败吗」，资料内有答案 = 是，但正确答案是「不等于失败」。
