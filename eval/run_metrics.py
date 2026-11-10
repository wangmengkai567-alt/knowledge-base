"""Evaluate saved execution facts. Heuristics never establish semantic truth."""

from __future__ import annotations

import json
import re
from collections import Counter
from math import ceil

from evidence_metrics import audit_evidence
from faithfulness import unsupported_sentences

CITATION = re.compile(r"\[来源\s*([1-9]\d*)\s*\]")


def ledger_valid(run: dict, starts: list[dict], finishes: list[dict]) -> bool:
    """Version 5/6: reservations are not consumption; unknown totals stay null."""
    usage = run["usage"]
    calls = usage["calls"]
    settled = [c for c in calls if c["state"] == "settled"]
    unknown = [c for c in calls if c["state"] == "unknown"]
    by_call = {e["data"]["call"]: e["data"] for e in finishes}
    return (
        len(calls) == len(starts) == usage["model_calls"]
        and [c["call"] for c in calls] == list(range(1, len(calls) + 1))
        and all(c["run_id"] == run["run_id"] for c in calls)
        and len(settled) + len(unknown) == len(calls)
        and len(by_call) == len(finishes) == len(settled)
        and len(unknown) == usage["unreported_model_calls"]
        and all(c["prompt_tokens"] is None and c["completion_tokens"] is None
                and c["provider_total_tokens"] is None and c["unknown_reason"] for c in unknown)
        and all(c["reservation"] == c["estimated_input_tokens"] + c["output_limit"]
                and c["estimated_input_tokens"] == e["data"]["estimated_input_tokens"]
                and c["output_limit"] == e["data"]["output_limit"]
                and c["phase"] == ("final" if e["data"]["final"] else "exploration")
                for c, e in zip(calls, starts, strict=True))
        and all(c["call"] in by_call
                and all(c[k] == by_call[c["call"]][k] for k in
                        ("prompt_tokens", "completion_tokens", "provider_total_tokens")) for c in settled)
        and usage["settled_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
        and usage["actual_total_tokens"] == (None if unknown else usage["settled_tokens"])
        and usage["reserved_tokens"] == usage["final_reserved_tokens"] == 0
    )


def score_run(snapshot: dict, item: dict) -> dict:
    run = snapshot["result"]
    sources, events, usage = run["sources"], run["events"], run["usage"]
    contract = snapshot.get("tool_contract_version", 1)
    if contract not in (1, 2, 3):
        raise ValueError("unsupported saved tool contract")
    directory = {entry["source_id"]: entry for entry in run.get("web_directory", [])}
    ids = [source["citation_id"] for source in sources]
    cited = [int(value) for value in CITATION.findall(run["answer"])]
    valid = len(ids) == len(set(ids)) and all(type(i) is int and i > 0 for i in ids)
    evidence_errors = []
    if contract in (2, 3) and (len(directory) != len(run["web_directory"]) or len({entry["url"] for entry in directory.values()}) != len(directory) or (directory and not snapshot["scope"]["allow_web"])):
        evidence_errors.append("invalid web directory scope or identity")
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
            if any(not segment["start"] <= left < right <= segment["end"]
                   for segment in segments for left, right in segment.get("code_ranges", [])):
                evidence_errors.append("code boundary outside sent segment")
            if not source["document_updated_at"]:
                evidence_errors.append("missing document version")
        elif source["source_type"] in ("web_search_summary", "web_page"):
            if not source["url"].startswith("https://") and not source["url"].startswith("http://"):
                evidence_errors.append("invalid web URL")
            if not source["fetched_at"]:
                evidence_errors.append("missing fetch time")
            if contract in (2, 3):
                entry = directory.get(source["source_id"])
                start, end = source["start"], source["end"]
                if not entry or entry["url"] != source["url"] or start < 0 or end - start != len(source["content"]) or end > source["total_chars"]:
                    evidence_errors.append("invalid web directory or range")
                if source["source_type"] == "web_page":
                    page = snapshot["web_pages"].get(source["source_id"])
                    if not page or page["url"] != source["url"] or page["fetched_at"] != source["fetched_at"] or page["content"][start:end] != source["content"] or len(page["content"]) != source["total_chars"]:
                        evidence_errors.append("web snapshot/content mismatch")
                    elif any(a < boundary < b for a, b in page["code_ranges"] for boundary in (start, end)):
                        evidence_errors.append("web code block cut by window")
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
    binding = audit_evidence(snapshot) if snapshot.get("answer_contract_version") == 3 else {}
    evidence_errors.extend(binding.get("evidence_binding_errors", []))
    evidence_errors.extend(binding.get("program_claim_binding_errors", []))
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
    current = run.get("version", 4) in (5, 6)
    ledger_ok = ledger_valid(run, model_starts, model_finishes) if current else None
    usage_consistent = (all(c["provider_total_tokens"] == c["prompt_tokens"] + c["completion_tokens"]
                            for c in usage["calls"] if c["state"] == "settled") if current else None)
    consumption = usage["settled_tokens"] if current else usage["charged_tokens"]
    measurement = run.get("input_measurement", "unknown")
    counting = run.get("counting")
    token_limits = snapshot.get("token_limits")
    measurement_valid = None
    if run.get("version") == 6:
        measurement_valid = (
            contract == 3 and measurement == "model_tokenizer_estimate"
            and isinstance(token_limits, dict)
            and 0 < token_limits["input_limit"] <= 16000
            and 0 < token_limits["output_limit"] <= 2048
            and limits["total_tokens"] <= token_limits["total_tokens"] <= 64000
            and 0 < token_limits["context_capacity"] <= 1000000
            and all(0 < call["output_limit"] <= token_limits["output_limit"]
                    and call["estimated_input_tokens"] <= min(token_limits["input_limit"], token_limits["context_capacity"] - call["output_limit"])
                    for call in usage["calls"])
            and all(call["prompt_tokens"] <= token_limits["input_limit"]
                    and call["completion_tokens"] <= token_limits["output_limit"]
                    for call in usage["calls"] if call["state"] == "settled")
            and isinstance(counting, dict) and counting == snapshot.get("counting")
            and counting.get("measurement") == measurement
            and all(request["model"] == counting.get("model") for request in snapshot.get("model_requests", []))
            and bool(counting.get("tokenizer_ref")) and counting.get("request_projection_version") == 1
            and all(c["estimated_input_tokens"] == c["content_tokens"] + c["protocol_tokens"] + c["safety_tokens"]
                    and c["safety_tokens"] == ceil((c["content_tokens"] + c["protocol_tokens"]) * counting["safety_ratio"]) + counting["fixed_reserve_tokens"]
                    for c in usage["calls"])
            and len(snapshot.get("model_requests", [])) == usage["model_calls"] == len(model_starts)
            and all(all(call[key] == event["data"][key] for key in ("content_tokens", "protocol_tokens", "safety_tokens"))
                    for call, event in zip(usage["calls"], model_starts, strict=True))
            and all(request["estimate"]["estimated_input_tokens"] == call["estimated_input_tokens"]
                    and request["output_limit"] == call["output_limit"]
                    and request["phase"] == call["phase"]
                    and (request["response_format"] is not None and not request["tools"]
                         if call["phase"] == "final" else request["response_format"] is None and bool(request["tools"]))
                    for request, call in zip(snapshot.get("model_requests", []), usage["calls"], strict=True))
        )
    budget_valid = (
        measurement_valid is not False
        and usage["model_calls"] == len(model_starts) <= limits["model_calls"] <= 6
        and usage["tool_calls"] == len(tool_starts) <= limits["tool_calls"] <= 10
        and consumption <= limits["total_tokens"] <= 64000
        and (not current or (ledger_ok and usage_consistent and usage["actual_total_tokens"] is not None))
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
        and (not current or events[-1]["data"]["exploration_stop_reason"] == run["exploration_stop_reason"])
    )
    # 新执行在每次调用显式记录阶段；历史快照不补造此信息或改写评分。
    generation_protocol_valid = None
    if any("request_mode" in e["data"] for e in model_starts):
        final_calls = [e for e in model_starts if e["data"]["final"]]
        generation_protocol_valid = (
            len(final_calls) <= 1
            and model_starts[0]["data"].get("tool_choice") == "required"
            and all(
                e["data"].get("request_mode") == ("strict_answer" if e["data"]["final"] else "native_tools")
                and (e["data"].get("tool_definition_count") == 0 and e["data"].get("tool_choice") is None
                     if e["data"]["final"] else e["data"].get("tool_definition_count", 0) > 0)
                for e in model_starts
            )
            and (not final_calls or final_calls[0] == model_starts[-1])
            and (run["status"] not in ("completed", "insufficient") or bool(final_calls))
        )
    keys = []
    for event in tool_starts:
        data = event["data"]
        if contract in (2, 3):
            keys.append(data["call_key"])
            continue
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
    coverage_events = [event["data"] for event in events if event["kind"] == "evidence_coverage_updated"]
    compression_events = [event["data"] for event in events
                          if event["kind"] == "materials_budgeted" and "strategy" in event["data"]]
    final_calls = ([event["data"] for event in model_starts if event["data"]["final"]]
                   if compression_events else [])
    return {
        **binding,
        **({"evidence_coverage": coverage_events[-1] if coverage_events else None, "material_compression": compression_events,
            "final_estimated_input_tokens": final_calls[-1]["estimated_input_tokens"] if final_calls else None}
           if compression_events else {}),
        "status": run["status"], "stop_reason": run["stop_reason"],
        "input_measurement": measurement, "counting": counting,
        **({"measurement_valid": measurement_valid} if measurement_valid is not None else {}),
        **({"generation_protocol_valid": generation_protocol_valid}
           if generation_protocol_valid is not None else {}),
        **({"protocol_version": run["version"], "ledger_valid": ledger_ok,
            "exploration_stop_reason": run["exploration_stop_reason"],
            "actual_usage_known": usage["actual_total_tokens"] is not None} if current else {}),
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

    current = [r for r in rows if r.get("protocol_version") in (5, 6)]
    usage_keys = ["model_calls", "tool_calls", "prompt_tokens", "completion_tokens",
                  "estimated_input_tokens", "unreported_model_calls"]
    usage_keys += ["settled_tokens"] if current else ["charged_tokens"]
    measurement_groups = {}
    for row in rows:
        metadata = row.get("counting") or {}
        key = json.dumps([row.get("input_measurement", "unknown"), metadata.get("tokenizer_ref"),
                          metadata.get("request_projection_version"), metadata.get("safety_ratio"),
                          metadata.get("fixed_reserve_tokens")], ensure_ascii=False)
        measurement_groups[key] = measurement_groups.get(key, 0) + row.get("usage", {}).get("estimated_input_tokens", 0)
    if len(measurement_groups) > 1:
        usage_keys.remove("estimated_input_tokens")
    return {
        "estimated_input_tokens_by_measurement": measurement_groups,
        "statuses": dict(Counter(r.get("status", "request_error") for r in rows)),
        "stop_reasons": dict(Counter(r.get("stop_reason", "request_error") for r in rows)),
        **({"protocol_versions": dict(Counter(r.get("protocol_version", "legacy") for r in rows)),
            "exploration_stop_reasons": dict(Counter(r.get("exploration_stop_reason", "") for r in current)),
            "actual_total_tokens": (sum(r["usage"]["actual_total_tokens"] for r in current)
                                    if len(current) == len(rows) and all(r["actual_usage_known"] for r in current) else None),
            "usage_note": "settled_tokens is known consumption only; null actual totals cannot be certified."} if current else {}),
        "latency_p50_seconds": percentile(0.5), "latency_p95_seconds": percentile(0.95),
        "latency_max_seconds": max(durations) if durations else None,
        "usage_totals": {key: sum(r.get("usage", {}).get(key, 0) for r in rows) for key in usage_keys},
        "repeated_executions": sum(r.get("repeated_executions", 0) for r in rows),
        "semantic_review_pending": sum(r.get("semantic_review") == "pending" for r in rows),
        "cost_currency": None,
        "cost_note": "Only model usage is recorded; no unverified prices or currency costs.",
    }
