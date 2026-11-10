# 问答模型计数资源

运行模型：`qwen3.8-flash`。分词资源来源：Qwen 官方 `Qwen/Qwen3.8-Flash-Next`，固定 revision 为 `812d96e10a0dd82b69d749e68693698cf6c36daa`。

对应依据为官方模型关系说明与历史实际用量核对，已由用户确认采用；完整请求仍标记为 `model_tokenizer_estimate`，不宣称托管服务的完整请求计数精确可复现。

- [官方模型说明](https://huggingface.co/Qwen/Qwen3.8-Flash-Next)。
- [固定版本资源目录](https://huggingface.co/Qwen/Qwen3.8-Flash-Next/tree/812d96e10a0dd82b69d749e68693698cf6c36daa)。
- 本目录 `manifest.json` 保存固定来源、版本、文件长度及内容校验值。
- 上游 `LICENSE` 原样附带，保留其 CRLF；其他新增说明使用 UTF-8 无 BOM、LF。

只打包 tokenizer、配置、Chat Template 与许可，没有模型推理权重。Python 依赖通过项目 `pyproject.toml`、`uv.lock` 固定，使用 `uv sync --locked` 准备环境。Docker 构建会复制这些资源并执行一次本地加载与完整性校验。

启动时校验模型、tokenizer_ref、清单版本和资源内容；资源不匹配或缺失则报配置错误。请求期间不联网下载，也没有备用计数器。

计数与切片使用同一份原文，坐标始终为原文字符位置。tokenize/decode 可能规范化组合字符，不能用 decode 结果替代引用原文。完整请求按模板、工具与答案 Schema 估算后只增加一次比例及固定余量，实际消费以供应商 usage 结算。
