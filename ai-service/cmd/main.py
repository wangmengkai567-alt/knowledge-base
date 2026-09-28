"""
cmd/main.py — 应用启动入口

启动入口：读配置并启动 HTTP 服务。
所有依赖组装逻辑在 wire.py 中完成。

为什么 uvicorn.run 传字符串 "cmd.wire:app" 而非 app 对象？
- 多进程模式（workers>1）下，uvicorn 需要 fork 子进程，
  每个子进程独立 import 模块并创建 app 实例。
- 传 app 对象时 workers 参数不生效（只能单进程）。
- 传字符串引用时，每个 worker 独立 import cmd.wire 并获取 app，
  实现真正的多进程。
- 单进程模式（workers=1）下两者行为一致。
"""

from __future__ import annotations

import uvicorn

from cmd.wire import _settings


def main() -> None:
    """启动 AI Service HTTP Server。"""
    uvicorn.run(
        "cmd.wire:app",
        host=_settings.server.host,
        port=_settings.server.http_port,
        workers=_settings.server.workers,
        log_level=_settings.log.level.lower(),
    )


if __name__ == "__main__":
    main()
