"""本机运行快照；原子保存，七天后清理，不支持恢复执行。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import RLock
from time import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


# 按用户保存问答执行快照，并清理超过保留时间的本地记录。
class LocalAnswerRunStore:
    # 本地问答快照的保留天数。
    retention_days = 7

    def __init__(self, directory: Path) -> None:
        """创建快照目录，并用可重入锁串行化本进程内的保存、读取和清理操作。"""
        self._lock = RLock()
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    # 删除过期的正式记录和未完成写入的临时文件。
    def cleanup(self) -> None:
        with self._lock:
            cutoff = time() - timedelta(days=self.retention_days).total_seconds()
            for folder in self.directory.glob("user-*"):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                for path in folder.iterdir():
                    if path.suffix in (".json", ".pending") and path.stat().st_mtime <= cutoff:
                        path.unlink()

    # 补充保存及到期时间后，将本次问答快照写入对应用户目录。
    def save(self, user_id: int, run_id: str, snapshot: dict) -> None:
        with self._lock:
            self.cleanup()
            folder = self.directory / f"user-{user_id}"
            folder.mkdir(exist_ok=True)
            path = folder / f"run-{run_id}.json"
            pending = path.with_suffix(".pending")
            now = datetime.now(UTC)
            snapshot = {
                **snapshot,
                "saved_at": now.isoformat(),
                "expires_at": (now + timedelta(days=self.retention_days)).isoformat(),
            }
            try:
                # 先写临时文件再替换正式记录，避免读到只写了一半的 JSON。
                pending.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
