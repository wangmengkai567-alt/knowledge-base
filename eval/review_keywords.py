"""Compare literal and explicit equivalent keyword matches on saved answers only."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from keyword_scoring import keyword_matches, keywords_ok
from run_eval import score_record, write_results

ROOT = Path(__file__).resolve().parent


def without_aliases(dataset: dict) -> dict:
    return {
        **dataset,
        "items": [{k: v for k, v in item.items() if k != "keyword_aliases"} for item in dataset["items"]],
    }


def rate(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def review_results(path: Path, dataset: dict) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in dataset["items"]}
    if len(items) != len(dataset["items"]):
        raise ValueError("rubric contains duplicate question IDs")
    rows = payload["rows"]
    if len({row["id"] for row in rows}) != len(rows) or {row["id"] for row in rows} != set(items):
        raise ValueError(f"{path}: saved rows must contain every rubric question exactly once")
    if "dataset" in payload:
        if without_aliases(payload["dataset"]) != without_aliases(dataset):
            raise ValueError(f"{path}: only keyword_aliases may differ from the saved dataset")
    details = []
    for row in rows:
        item = items[row["id"]]
        if row["query"] != item["query"]:
            raise ValueError(f"{path}: query differs for {item['id']}")
        answer = row.get("answer", "")
        if "run_snapshot" in row:
            if answer != row["run_snapshot"]["result"]["answer"]:
                raise ValueError(f"{path}: answer differs from snapshot for {item['id']}")
            rescored = score_record(row, item)
            # Every execution fact and non-keyword score must remain exactly as saved.
            for field in (row.keys() | rescored.keys()) - {"must_contain_ok", "keyword_matches"}:
                if rescored.get(field) != row.get(field):
                    raise ValueError(f"{path}: unrelated score changed: {item['id']} {field}")
        if not item["answerable"]:
            continue
        literal = keyword_matches(answer, {**item, "keyword_aliases": {}})
        expanded = keyword_matches(answer, item)
        before, after = keywords_ok(literal), keywords_ok(expanded)
        if before and not after:
            raise ValueError(f"{path}: aliases removed an original match for {item['id']}")
        details.append({
            "id": item["id"], "query": item["query"],
            "recorded_keyword_ok": row.get("must_contain_ok"),
            "literal_keyword_ok": before, "expanded_keyword_ok": after,
            "keyword_matches": expanded, "status": row.get("status"), "error": row.get("error"),
        })
    total = len(details)
    changed = [row["id"] for row in details if row["literal_keyword_ok"] != row["expanded_keyword_ok"]]
    result = {
        "file": str(path), "answerable_items": total, "total_items": len(rows),
        "recorded_answer_correctness": payload["summary"]["answer_correctness"],
        "recorded_hallucination_rate": payload["summary"]["hallucination_rate"],
        "recorded_refusal_accuracy": payload["summary"]["refusal_accuracy"],
        "status_counts": dict(Counter(row.get("status", "legacy_unrecorded") for row in rows)),
        "n_errors": sum(bool(row.get("error")) for row in rows),
        "changed_ids": changed, "rows": details,
    }
    for label, key in (("recorded", "recorded_keyword_ok"), ("literal", "literal_keyword_ok"),
                       ("expanded", "expanded_keyword_ok")):
        hits = sum(row[key] is True for row in details)
        eligible_hits = sum(row[key] is True and not row["error"] for row in details)
        result[f"{label}_keyword_hits"] = hits
        result[f"{label}_keyword_rate"] = rate(hits, total)
        result[f"{label}_existing_policy_hits"] = eligible_hits
        result[f"{label}_existing_policy_correctness"] = rate(eligible_hits, total)
        if all("status" in row for row in rows):
            completed_hits = sum(row[key] is True and row["status"] == "completed" and not row["error"]
                                 for row in details)
            result[f"{label}_completed_hits"] = completed_hits
            result[f"{label}_completed_correctness"] = rate(completed_hits, total)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "golden_set.json")
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite existing review: {args.out}")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    for item in dataset["items"]:
        keyword_matches("", item)
        if item["answerable"] and set(item.get("keyword_aliases", {})) != set(item["must_contain"]):
            raise ValueError(f"{item['id']}: explicitly review every required concept, including exact-only terms")
    runs = [review_results(path, dataset) for path in args.results]
    review = {
        "version": 1, "rubric": str(args.dataset),
        "rule": "AND across required concepts; OR within each concept's explicit equivalent expressions",
        "answerable_questions_reviewed": sum(item["answerable"] for item in dataset["items"]),
        "required_concepts": sum(len(item["must_contain"]) for item in dataset["items"] if item["answerable"]),
        "explicit_alternatives": sum(len(aliases) for item in dataset["items"]
                                     for aliases in item.get("keyword_aliases", {}).values()),
        "network_requests": 0,
        "limitations": [
            "Keyword matches do not establish factual correctness, completeness, negation or numeric relationships.",
            "Existing policy counts insufficient answers containing keywords; completed-only counts are separate.",
            "Historical records have no terminal-state evidence and cannot receive completed-only scores.",
            "Historical recorded scores may differ from today's literal normalization; both are shown.",
            "Original result files, snapshots, hallucination and refusal decisions are unchanged.",
        ],
        "runs": runs,
    }
    write_results(args.out, review)
    print(json.dumps({**{k: v for k, v in review.items() if k != "runs"},
                      "runs": [{k: v for k, v in run.items() if k != "rows"} for run in runs]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
