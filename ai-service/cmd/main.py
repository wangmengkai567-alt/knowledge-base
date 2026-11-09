"""
cmd/main.py — 应用启动入口

只负责拿到组装好的 Server 并启动。
所有依赖组装逻辑在 wire.py 中完成。

uvicorn 通过 "cmd.wire:app" 导入统一组装的应用。
当前配置要求 workers=1，因为关键词索引和文档互斥锁均保存在进程内。
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
