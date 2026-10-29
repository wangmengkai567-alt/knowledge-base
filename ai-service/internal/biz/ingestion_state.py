"""文档入库阶段及完成条件。status 字段仅表示解析状态。"""

from datetime import UTC, datetime

STAGES = ("parse", "chunk", "vector", "keyword")


def stage_state(status: str, error: str | None = None) -> dict:
    return {
        "status": status,
        "error": error[:1000] if error else None,
        "updated_at": datetime.now(UTC).isoformat(),
    }


def initial_stages(keyword_required: bool) -> dict:
    return {
        name: stage_state("skipped" if name == "keyword" and not keyword_required else "pending")
        for name in STAGES
    }


def ingestion_status(stages: dict) -> str:
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
