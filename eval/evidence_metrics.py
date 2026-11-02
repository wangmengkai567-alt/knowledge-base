"""Audit v3 evidence against the saved final request and original source coordinates."""

from __future__ import annotations

import json
import re


def audit_evidence(snapshot: dict) -> dict:
    run = snapshot["result"]
    catalog = run["evidence_catalog"]
    request = snapshot["final_request"]
    errors, claim_errors = [], []
    ids = [entry["evidence_id"] for entry in catalog]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"E[1-9][0-9]*", value) for value in ids):
        errors.append("invalid evidence identifiers")
    by_id = {entry["evidence_id"]: entry for entry in catalog}
    sources = {source["citation_id"]: source for source in run["sources"]}
    if request:
        payload = json.loads(request[-1]["content"])
        expected = {part["evidence_id"]: {**part, "citation_id": source["citation_id"]}
                    for source in payload["sources"] for part in source["evidence"]}
        if len(expected) != sum(len(source["evidence"]) for source in payload["sources"]):
            errors.append("duplicate evidence in final request")
        if set(expected) != set(ids):
            errors.append("catalog differs from final request")
        if payload["selection"] is not None and any(
            entry["citation_id"] not in payload["selection"]["citation_ids"] for entry in catalog
        ):
            errors.append("evidence outside final source selection")
    else:
        expected = {}
        if catalog or any(event["kind"] == "evidence_catalog_prepared" for event in run["events"]):
            errors.append("missing final request")
    for entry in catalog:
        try:
            source = sources[entry["citation_id"]]
            origin = entry["origin"]
            left, right = origin["start"], origin["end"]
            content = entry["content"]
            if entry["run_id"] != run["run_id"] or left < 0 or right - left != len(content):
                errors.append("invalid evidence run or range")
            sent = expected[entry["evidence_id"]]
            if any(entry[key] != sent[key] for key in ("citation_id", "kind", "content")):
                errors.append("evidence text differs from actual final request")
            if origin["source_type"] != source["source_type"]:
                errors.append("evidence source type mismatch")
            if source["source_type"] == "knowledge_base":
                if (origin["knowledge_base_id"] not in snapshot["scope"]["knowledge_base_ids"]
                        or origin["knowledge_base_id"] != source["knowledge_base_id"]
                        or origin["document_id"] != source["document_id"]
                        or origin["document_version"] != source["document_updated_at"]):
                    errors.append("evidence document scope or version mismatch")
                matching = [part for part in source["segments"]
                            if part["chunk_id"] == origin["chunk_id"] and part["position"] == origin["position"]
                            and part["start"] <= left < right <= part["end"]
                            and part["content"][left-part["start"]:right-part["start"]] == content]
                if not matching:
                    errors.append("evidence differs from original chunk slice")
                if entry["kind"] == "code" and not any([left, right] in [list(pair) for pair in part["code_ranges"]] for part in matching):
                    errors.append("code is not a registered complete block")
            else:
                if (not snapshot["scope"]["allow_web"] or origin["url"] != source["url"]
                        or origin["source_id"] != source["source_id"] or origin["fetched_at"] != source["fetched_at"]
                        or not source["start"] <= left < right <= source["end"]
                        or source["content"][left-source["start"]:right-source["start"]] != content):
                    errors.append("evidence differs from authorized web source")
                if source["source_type"] == "web_page":
                    page = snapshot["web_pages"][source["source_id"]]
                    if page["content"][left:right] != content:
                        errors.append("evidence differs from web snapshot")
                if entry["kind"] == "code" and [left, right] not in [list(pair) for pair in source["code_ranges"]]:
                    errors.append("web code block is incomplete")
        except (KeyError, TypeError, ValueError):
            errors.append("invalid evidence origin or request mapping")
    published = bool(run["answer"]) or any(event["kind"] == "answer_validated" for event in run["events"])
    if published:
        try:
            generated = json.loads(snapshot["final_response"])
            records = snapshot["answer_claims"]
            if len(generated["claims"]) != len(records):
                claim_errors.append("rendered claims differ from model response")
            for claim, record in zip(generated["claims"], records, strict=True):
                if (set(claim) != {"scope", "text", "evidence_ids", "code_evidence_id"}
                        or claim["scope"] != record["scope"] or claim["text"].strip() != record["text"]
                        or claim["evidence_ids"] != record["evidence_ids"]
                        or claim["code_evidence_id"] != record["code_evidence_id"] or record["quote_owner"] != "program"):
                    claim_errors.append("invalid v3 claim or quote ownership")
                if [support["evidence_id"] for support in record["evidence"]] != claim["evidence_ids"]:
                    claim_errors.append("claim evidence selection differs from model response")
                for support in record["evidence"]:
                    entry = by_id[support["evidence_id"]]
                    if (support["quote"] != entry["content"] or support["origin"] != entry["origin"]
                            or support["citation_id"] != entry["citation_id"]):
                        claim_errors.append("program quote differs from registered evidence")
                code_id = claim["code_evidence_id"]
                if code_id is not None:
                    entry = by_id[code_id]
                    if code_id not in claim["evidence_ids"] or entry["kind"] != "code":
                        claim_errors.append("unselected code evidence")
                    raw = entry["content"]
                    lines = raw.splitlines(keepends=True)
                    opening = re.match(r" {0,3}(`{3,}|~{3,})", lines[0])
                    if opening is None:
                        raise ValueError("invalid saved code opening")
                    closing = re.fullmatch(r" {0,3}" + re.escape(opening[1][0]) + "{" + str(len(opening[1])) + r",}[ \t\r\n]*", lines[-1]) if len(lines) > 1 else None
                    body = "".join(lines[1:-1] if closing else lines[1:])
                    if record["code"] != body:
                        claim_errors.append("display code differs from original code body")
        except (KeyError, TypeError, ValueError, IndexError):
            claim_errors.append("invalid published evidence claims")
    return {"evidence_binding_valid": not errors, "evidence_binding_errors": errors,
            "program_claim_binding_valid": not claim_errors if published else None,
            "program_claim_binding_errors": claim_errors, "evidence_catalog_count": len(catalog)}
