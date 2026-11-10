"""用户、文档解析、分块嵌入和知识库的状态枚举；数值与数据库记录对应。"""

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

    # 正常可用的知识库状态。
    NORMAL = 1
    # 知识库归档状态，对应持久化状态值 2。
    ARCHIVED = 2
    # 知识库索引重建中的状态，对应持久化状态值 3。
    REINDEXING = 3
