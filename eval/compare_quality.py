"""使用相同题集与评分规则比较两轮已保存的真实问答结果。"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from keyword_scoring import keyword_matches, keywords_ok
from review_keywords import without_aliases
from run_eval import score_record, summary, write_results


def evaluate(path: Path, dataset: dict) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in dataset["items"]}
    if without_aliases(payload["dataset"]) != without_aliases(dataset):
        raise ValueError(f"{path}: 题集除等价表达外发生变化，不能直接比较")
    if len(payload["rows"]) != len(items) or {row["id"] for row in payload["rows"]} != set(items):
        raise ValueError(f"{path}: 需要完整且没有重复题号的一轮结果")
    rows = [score_record(row, items[row["id"]]) for row in payload["rows"]]
    for old, row in zip(payload["rows"], rows, strict=True):
        for key in (old.keys() | row.keys()) - {"must_contain_ok", "keyword_matches"}:
            if old.get(key) != row.get(key):
                raise ValueError(f"{path}: 非关键词字段发生变化：{row['id']} {key}")
    answerable = [row for row in rows if items[row["id"]]["answerable"]]
    completed = sum(row.get("status") == "completed" and row.get("must_contain_ok") is True
                    and not row.get("error") for row in answerable)
    details = [{
        "题号": row["id"], "运行编号": row["run_snapshot"]["result"]["run_id"],
        "可回答": items[row["id"]]["answerable"], "终态": row.get("status"),
        "停止原因": row["run_snapshot"]["result"]["stop_reason"],
        "关键词全部命中": keywords_ok(keyword_matches(row.get("answer", ""), items[row["id"]])),
        "幻觉疑点": row.get("hallucinated"), "疑点句": row.get("unsupported_sentences", []),
        "模型": row["run_snapshot"]["result"]["model"],
        "模型调用次数": row["run_snapshot"]["result"]["usage"]["model_calls"],
        "工具调用次数": row["run_snapshot"]["result"]["usage"]["tool_calls"],
    } for row in rows]
    return {
        "原始文件": str(path), "原始摘要": payload["summary"],
        "统一标准摘要": summary(rows, {}, len(items)),
        "可答题数": len(answerable), "正常完成且关键词全部命中数": completed,
        "正常完成且关键词全部命中率": round(completed / len(answerable), 4),
        "停止原因分布": dict(Counter(row["停止原因"] for row in details)),
        "逐题明细": details,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).parent / "golden_set.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"拒绝覆盖已有对照：{args.out}")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    old_payload = json.loads(args.before.read_text(encoding="utf-8"))
    new_payload = json.loads(args.after.read_text(encoding="utf-8"))
    previous = {row["id"]: row for row in old_payload["rows"]}
    for row in new_payload["rows"]:
        old = previous[row["id"]]
        if old["request"] != row["request"]:
            raise ValueError(f"{row['id']}: 问答请求范围或参数发生变化")
        for key in ("scope", "limits"):
            if old["run_snapshot"][key] != row["run_snapshot"][key]:
                raise ValueError(f"{row['id']}: 授权范围或运行上限发生变化")
    models = [{row["run_snapshot"]["result"]["model"] for row in payload["rows"]
               if row["run_snapshot"]["result"]["model"]} for payload in (old_payload, new_payload)]
    if models[0] != models[1]:
        raise ValueError("两轮已报告的实际模型不同")
    before, after = evaluate(args.before, dataset), evaluate(args.after, dataset)
    old = {row["题号"]: row for row in before["逐题明细"]}
    changes = [{"题号": row["题号"], "优化前": old[row["题号"]], "优化后": row}
               for row in after["逐题明细"]
               if any(old[row["题号"]][key] != row[key]
                      for key in ("终态", "关键词全部命中", "幻觉疑点"))]
    result = {"评分说明": "两轮使用同一补词标准和原幻觉规则；错误计入分母；另列仅正常完成口径。",
              "优化前": before, "优化后": after, "变化题目": changes}
    write_results(args.out, result)
    for name, value in (("优化前", before), ("优化后", after)):
        print(name, json.dumps({key: val for key, val in value.items()
                                if key not in ("逐题明细", "原始摘要")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
