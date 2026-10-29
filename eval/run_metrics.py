"""Evaluate saved execution facts. Heuristics never establish semantic truth."""

from __future__ import annotations

import json
import re
from collections import Counter
from math import ceil

from faithfulness import unsupported_sentences

CITATION = re.compile(r"\[来源\s*([1-9]\d*)\s*\]")


def score_run(snapshot: dict, item: dict) -> dict:
    run = snapshot["result"]
    sources, events, usage = run["sources"], run["events"], run["usage"]
    ids = [source["citation_id"] for source in sources]
    cited = [int(value) for value in CITATION.findall(run["answer"])]
    valid = len(ids) == len(set(ids)) and all(type(i) is int and i > 0 for i in ids)
    evidence_errors = []
    for source in sources:
        if not source["content"]:
            evidence_errors.append("empty material")
        if source["source_type"] == "knowledge_base":
            if source["knowledge_base_id"] not in snapshot["scope"]["knowledge_base_ids"]:
                evidence_errors.append("source outside allowed knowledge base scope")
            segments = source["segments"]
            if source["content"] != "\n".join(s["content"] for s in segments):
                evidence_errors.append("segment/content mismatch")
            if source["chunk_ids"] != [s["chunk_id"] for s in segments]:
                evidence_errors.append("chunk mapping mismatch")
            if any(s["start"] < 0 or s["end"] - s["start"] != len(s["content"]) for s in segments):
                evidence_errors.append("invalid segment range")
            if not source["document_updated_at"]:
                evidence_errors.append("missing document version")
        elif source["source_type"] in ("web_search_summary", "web_page"):
            if not source["url"].startswith("https://") and not source["url"].startswith("http://"):
                evidence_errors.append("invalid web URL")
            if not source["fetched_at"]:
                evidence_errors.append("missing fetch time")
        else:
            evidence_errors.append("unknown source type")
    prepared = set()
    sent = set()
    for event in events:
        if event["kind"] == "materials_prepared":
            prepared.update(event["data"]["citation_ids"])
        if event["kind"] == "sources_updated":
            sent.update(event["data"]["citation_ids"])
    if set(ids) != sent or not sent.issubset(prepared):
        evidence_errors.append("source registration differs from sent materials")
    valid = valid and not evidence_errors
    citations_valid = (
        all(i in ids for i in cited)
        and run["answer"].count("[来源") == len(cited)
        and (run["status"] != "completed" or bool(cited))
    )
    by_id = {s["citation_id"]: s["content"] for s in sources}
    claim_checks = []
    # 每段只核对该段引用的资料；关键词重合仍只作为人工抽查线索。
    for paragraph in run["answer"].splitlines():
        paragraph_ids = [int(value) for value in CITATION.findall(paragraph)]
        if paragraph_ids:
            evidence = "\n".join(by_id.get(i, "") for i in paragraph_ids)
            claim_checks.append({"text": paragraph, "citations": paragraph_ids,
                                 "heuristic_unsupported": unsupported_sentences(paragraph, evidence)})
    model_starts = [e for e in events if e["kind"] == "model_started"]
    model_finishes = [e for e in events if e["kind"] == "model_finished"]
    tool_starts = [e for e in events if e["kind"] == "tool_started"]
    limits = snapshot["limits"]
    budget_valid = (
        usage["model_calls"] == len(model_starts) <= limits["model_calls"] <= 6
        and usage["tool_calls"] == len(tool_starts) <= limits["tool_calls"] <= 10
        and usage["charged_tokens"] <= limits["total_tokens"] <= 64000
        and all(e["data"]["estimated_input_tokens"] <= 16000
                and e["data"]["output_limit"] <= 2048 for e in model_starts)
        and all(e["elapsed_ms"] < limits["timeout"] * 1000 for e in [*model_starts, *tool_starts])
        and all(e["data"]["prompt_tokens"] <= 16000
                and e["data"]["completion_tokens"] <= 2048 for e in model_finishes)
        and len(model_finishes) + usage["unreported_model_calls"] == len(model_starts)
        and sum(e["data"]["prompt_tokens"] for e in model_finishes) == usage["prompt_tokens"]
        and sum(e["data"]["completion_tokens"] for e in model_finishes) == usage["completion_tokens"]
    )
    sequence_valid = (
        [e["sequence"] for e in events] == list(range(1, len(events) + 1))
        and bool(events) and events[-1]["kind"] == "run_finished"
        and events[-1]["data"]["status"] == run["status"]
        and events[-1]["data"]["stop_reason"] == run["stop_reason"]
    )
    keys = []
    for event in tool_starts:
        data = event["data"]
        # 参数摘要可能截断；无法完整解析时显式保留未知，不伪造重复判断。
        try:
            args = json.loads(data["arguments_summary"])
        except (ValueError, KeyError):
            keys.append(None)
            continue
        if data["tool"] == "knowledge_base_search":
            value = " ".join(args["query"].casefold().split())
        elif data["tool"] == "web_search":
            value = sorted({" ".join(word.casefold().split()) for word in args["keywords"]})
        else:
            value = sorted(set(args["urls"]))
        keys.append(json.dumps([data["tool"], value], ensure_ascii=False))
    known = [key for key in keys if key is not None]
    tools = [event["data"]["tool"] for event in tool_starts]
    tool_selection_ok = (
        (item.get("allow_web", False) or not any(tool.startswith("web_") for tool in tools))
        and all(tool in tools for tool in item.get("required_tools", []))
        and all(any(s["source_type"] == kind for s in sources)
                for kind in item.get("required_source_types", []))
        and all(any(s.get("document_filename") == filename for s in sources)
                for filename in item.get("required_source_files", []))
    )
    return {
        "status": run["status"], "stop_reason": run["stop_reason"],
        "evidence_valid": valid, "evidence_errors": evidence_errors,
        "citations_valid": citations_valid, "citation_count": len(cited),
        "cited_source_fraction": len(set(cited) & set(ids)) / len(ids) if ids else None,
        "claim_checks": claim_checks, "semantic_review": "pending",
        "budget_valid": budget_valid, "sequence_valid": sequence_valid,
        "tool_selection_ok": tool_selection_ok, "tools": tools,
        "repeated_executions": len(known) - len(set(known)),
        "unknown_call_keys": len(keys) - len(known),
        "usage": usage,
        "actual_peak_input_tokens": max((e["data"]["prompt_tokens"] for e in model_finishes), default=0),
        "actual_peak_output_tokens": max((e["data"]["completion_tokens"] for e in model_finishes), default=0),
    }


def summarize_runs(rows: list[dict]) -> dict:
    durations = sorted(r["elapsed_seconds"] for r in rows if "elapsed_seconds" in r)

    def percentile(q: float) -> float | None:
        return durations[max(0, ceil(len(durations) * q) - 1)] if durations else None

    return {
        "statuses": dict(Counter(r.get("status", "request_error") for r in rows)),
        "stop_reasons": dict(Counter(r.get("stop_reason", "request_error") for r in rows)),
        "latency_p50_seconds": percentile(0.5), "latency_p95_seconds": percentile(0.95),
        "latency_max_seconds": max(durations) if durations else None,
        "usage_totals": {key: sum(r.get("usage", {}).get(key, 0) for r in rows) for key in
                         ("model_calls", "tool_calls", "prompt_tokens", "completion_tokens",
                          "estimated_input_tokens", "charged_tokens", "unreported_model_calls")},
        "repeated_executions": sum(r.get("repeated_executions", 0) for r in rows),
        "semantic_review_pending": sum(r.get("semantic_review") == "pending" for r in rows),
        "cost_currency": None,
        "cost_note": "Only model usage is recorded; no unverified prices or currency costs.",
    }
