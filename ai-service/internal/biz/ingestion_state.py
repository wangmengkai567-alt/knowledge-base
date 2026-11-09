"""文档入库阶段及完成条件。status 字段仅表示解析状态。"""

from datetime import UTC, datetime

STAGES = ("parse", "chunk", "vector", "keyword")


def stage_state(status: str, error: str | None = None) -> dict:
    """生成单个步骤的状态记录，包含状态、最多 1000 字符的错误和 UTC 更新时间。"""
    return {
        "status": status,
        "error": error[:1000] if error else None,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def initial_stages(keyword_required: bool) -> dict:
    """返回四个入库步骤的初始状态；keyword_required=False 时跳过关键词索引。"""
    return {
        name: stage_state("skipped" if name == "keyword" and not keyword_required else "pending")
        for name in STAGES
    }


def ingestion_status(stages: dict) -> str:
    """把各步骤状态汇总为供页面和检索层使用的文档入库状态。

    先判断 processing，再判断 failed；解析、分块、向量完成且关键词完成或跳过才返回 ready。
    状态缺失或仍有待处理步骤时返回 pending；不修改传入的状态字典。
    """
    if not stages or any(name not in stages for name in STAGES):
        return "pending"
    statuses = [stages[name]["status"] for name in STAGES]
    if "running" in statuses:
        return "processing"
    if "failed" in statuses:
        return "failed"
    if all(status == "completed" for status in statuses[:3]) and statuses[3] in (
        "completed",
        "skipped",
    ):
        return "ready"
    return "pending"
