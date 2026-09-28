"""
internal/biz/constants.py — 业务常量定义

用 IntEnum 替代散落的魔法数字，提高可读性和可维护性。
"""

from __future__ import annotations

from enum import IntEnum


class UserStatus(IntEnum):
    """用户状态。"""

    ACTIVE = 1     # 正常
    DISABLED = 2   # 禁用
    PENDING = 3    # 待激活


class DocumentStatus(IntEnum):
    """文档处理状态。"""

    PENDING = 1    # 待解析（刚上传）
    PARSED = 2     # 已解析（文本已提取）
    ERROR = 3      # 解析失败
    PROCESSING = 4 # 解析中


class EmbeddingStatus(IntEnum):
    """分块嵌入状态。"""

    PENDING = 0    # 待嵌入（刚分块）
    COMPLETED = 1  # 嵌入完成
    FAILED = 2     # 嵌入失败


class KbStatus(IntEnum):
    """知识库状态。"""

    NORMAL = 1
    ARCHIVED = 2
    REINDEXING = 3
