"""
internal/data/storage/local.py — 本地文件系统存储实现

FileStorageRepo 的本地实现，文件存储到指定目录。
文件命名：UUID + 原始扩展名，避免文件名冲突。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import structlog

from internal.biz.repo import FileStorageRepo

logger = structlog.get_logger()


class LocalFileStorageRepo(FileStorageRepo):
    """本地文件系统存储 — 文件保存到 upload_dir 目录。

    文件命名策略：{uuid4_hex[:12]}_{safe_filename}
    - 12 位 UUID 前缀避免碰撞
    - 保留原始文件名方便调试
    """

    def __init__(self, upload_dir: str) -> None:
        self._upload_dir = Path(upload_dir).resolve()
        # 确保目录存在
        self._upload_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, filename: str, content: bytes) -> str:
        """保存文件到本地，返回绝对路径。"""
        # 生成安全文件名：UUID 前缀 + 原始文件名
        safe_name = f"{uuid.uuid4().hex[:12]}_{Path(filename).name}"
        file_path = self._upload_dir / safe_name

        # 异步写入（IO 操作用 to_thread）
        await asyncio.to_thread(self._write_file, file_path, content)

        # 返回绝对路径（避免 relative_to 跨路径崩溃）
        absolute_path = str(file_path.resolve())
        logger.info("file_saved", path=absolute_path, size=len(content))
        return absolute_path

    async def delete(self, file_path: str) -> None:
        """删除本地文件。"""
        full_path = Path(file_path)
        if not full_path.is_absolute():
            full_path = full_path.resolve()

        if full_path.exists():
            await asyncio.to_thread(full_path.unlink)
            logger.info("file_deleted", path=str(full_path))

    @staticmethod
    def _write_file(file_path: Path, content: bytes) -> None:
        """同步写文件（供 to_thread 调用）。"""
        file_path.write_bytes(content)

