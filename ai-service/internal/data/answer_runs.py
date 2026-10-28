"""本机运行快照；原子保存，七天后清理，不支持恢复执行。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import RLock
from time import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class LocalAnswerRunStore:
    retention_days = 7

    def __init__(self, directory: Path) -> None:
        self._lock = RLock()
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def cleanup(self) -> None:
        with self._lock:
            cutoff = time() - timedelta(days=self.retention_days).total_seconds()
            for folder in self.directory.glob("user-*"):
                if not folder.is_dir() or folder.is_symlink():
                    continue
                for path in folder.iterdir():
                    if path.suffix in (".json", ".pending") and path.stat().st_mtime <= cutoff:
                        path.unlink()

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
                pending.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
