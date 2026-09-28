"""
internal/data/storage/factory.py — FileStorageRepo 工厂
"""

from __future__ import annotations

from internal.biz.repo import FileStorageRepo
from internal.data.storage.local import LocalFileStorageRepo


def create_file_storage_repo(upload_dir: str) -> FileStorageRepo:
    """创建本地文件存储。"""
    return LocalFileStorageRepo(upload_dir=upload_dir)
