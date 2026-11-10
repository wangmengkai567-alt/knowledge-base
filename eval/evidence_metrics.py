"""Audit v3/v4 evidence against the saved final request and original source coordinates."""

from __future__ import annotations

import json
import re


def _source_contains(original: dict, selected: dict) -> bool:
    """Compare source identity and verbatim ranges without requiring the whole original."""
    kind = original["source_type"]
    keys = (("knowledge_base_id", "document_id", "document_updated_at", "document_filename")
            if kind == "knowledge_base" else ("source_id", "url", "fetched_at", "total_chars"))
    if selected["source_type"] != kind or any(original[key] != selected[key] for key in keys):
        return False
    if kind != "knowledge_base":
        left, right = selected["start"], selected["end"]
        return (original["start"] <= left < right <= original["end"]
                and original["content"][left-original["start"]:right-original["start"]] == selected["content"])
    if "segments" in original and "segments" in selected:
        return all(any(part["chunk_id"] == span["chunk_id"] and part["position"] == span["position"]
                       and part["start"] <= span["start"] < span["end"] <= part["end"]
                       and part["content"][span["start"]-part["start"]:span["end"]-part["start"]] == span["content"]
                       for part in original["segments"]) for span in selected["segments"])

    def excerpts(source):
        if "segments" in source:
            return [part["content"] for part in source["segments"]]
        return [source["content"][span["start"]:span["end"]] for span in source["excerpt_ranges"]]

    return all(any(text in part for part in excerpts(original)) for text in excerpts(selected))


def audit_source_delivery(snapshot: dict) -> list[str]:
    """Distinguish ever-delivered materials from the final, possibly smaller selection."""
    run = snapshot["result"]
    prepared, sent = set(), set()
    prepared_calls = {}
    errors = []
    for event in run["events"]:
        data = event["data"]
        if event["kind"] == "materials_prepared":
            prepared.update(data["citation_ids"])
            if "call_id" in data:
                prepared_calls.setdefault(data["call_id"], set()).update(data["citation_ids"])
        if event["kind"] == "sources_updated":
            delivered = set(data["citation_ids"])
            if not delivered <= prepared:
                errors.append("source sent before material preparation")
            sent.update(delivered)
    ids = {source["citation_id"] for source in run["sources"]}
    if not ids <= sent or not sent <= prepared:
        errors.append("source registration differs from sent materials")
    # Older snapshots did not record complete prepared requests. Read only their saved facts.
    if run.get("version") != 6:
        return errors
    try:
        requests = snapshot["model_requests"]
        originals = {result["call_id"]: result for result in snapshot["tool_results"]}
        actual_ids = set()
        delivered_sources = {}
        final_requests = []
        for request in requests:
            if request["phase"] == "final":
                final_requests.append(request)
                continue
            for message in request["messages"]:
                if message["role"] != "tool":
                    continue
                call_id = message["tool_call_id"]
                payload = json.loads(message["content"])
                raw = originals[call_id]
                for source in payload["sources"]:
                    citation_id = source["citation_id"]
                    actual_ids.add(citation_id)
                    delivered_sources.setdefault(citation_id, []).append(source)
                    if (citation_id not in prepared_calls.get(call_id, set())
                            or not any(_source_contains(original, source) for original in raw["sources"])):
                        errors.append("actual tool source differs from prepared material")
        if len(final_requests) > 1:
            errors.append("multiple final requests")
        if final_requests:
            final = final_requests[0]["messages"]
            if final != snapshot["final_request"]:
                errors.append("saved final request differs from actual model request")
            payload = json.loads(final[-1]["content"])
            final_ids = {source["citation_id"] for source in payload["sources"]}
            if final_ids != ids:
                errors.append("actual final source selection differs from displayed sources")
            selection = payload["selection"]
            if selection is not None and not set(selection["citation_ids"]) <= actual_ids:
                errors.append("final selection includes previously unsent sources")
            for source in run["sources"]:
                if not any(_source_contains(original, source)
                           for call_id, result in originals.items()
                           if source["citation_id"] in prepared_calls.get(call_id, set())
                           for original in result["sources"]):
                    errors.append("final source differs from original prepared tool material")
                if selection is not None and not any(
                    _source_contains(candidate, source)
                    for candidate in delivered_sources.get(source["citation_id"], [])
                ):
                    errors.append("selected source differs from actual tool messages")
            actual_ids.update(final_ids)
        elif snapshot["final_request"]:
            errors.append("missing actual final model request")
        for source in run["sources"]:
            candidates = delivered_sources.get(source["citation_id"], [])
            if source["citation_id"] not in (final_ids if final_requests else set()) and not any(
                _source_contains(candidate, source) for candidate in candidates
            ):
                errors.append("displayed source differs from actual tool messages")
        if actual_ids != sent:
            errors.append("source events differ from actual model messages")
    except (KeyError, TypeError, ValueError):
        errors.append("invalid saved material delivery records")
    return errors


def audit_evidence(snapshot: dict) -> dict:
    version = snapshot.get("answer_contract_version")
    if version not in (3, 4):
        raise ValueError("unsupported saved answer contract")
    run = snapshot["result"]
    if run.get("answer_contract_version", version) != version:
        raise ValueError("inconsistent saved answer contract")
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
        request_ids = [source["citation_id"] for source in payload["sources"]]
        if (len(set(request_ids)) != len(request_ids) or not set(request_ids) <= set(sources)
                or (run.get("version") == 6 and set(request_ids) != set(sources))
                or {entry["citation_id"] for entry in catalog} != set(request_ids)):
            errors.append("final sources differ from evidence catalog or request")
        if payload["selection"] != snapshot["evidence_selection"]:
            errors.append("final request differs from recorded source selection")
        if payload["selection"] is not None:
            selected = payload["selection"]["citation_ids"]
            if len(set(selected)) != len(selected) or set(selected) != set(request_ids):
                errors.append("evidence outside final source selection")
        for source in payload["sources"]:
            saved = sources.get(source["citation_id"], {})
            if any(saved.get(key) != value for key, value in source.items() if key != "evidence"):
                errors.append("final source identity differs from saved source")
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
            fields = {"text", "evidence_ids", "code_evidence_id"} | ({"scope"} if version == 3 else set())
            if (set(generated) != {"status", "claims", "missing"}
                    or generated["status"] not in ("answered", "insufficient")
                    or len(generated["claims"]) > 8 or len(generated["missing"]) > 5
                    or (generated["status"] == "answered" and (not generated["claims"] or generated["missing"]))
                    or (generated["status"] == "insufficient" and not generated["missing"])):
                claim_errors.append("invalid saved answer outcome")
            expected_citations = []
            if len(generated["claims"]) != len(records):
                claim_errors.append("rendered claims differ from model response")
            for claim, record in zip(generated["claims"], records, strict=True):
                if (set(claim) != fields
                        or (version == 3 and claim["scope"] != record["scope"])
                        or (version == 4 and "scope" in record)
                        or claim["text"].strip() != record["text"]
                        or claim["evidence_ids"] != record["evidence_ids"]
                        or claim["code_evidence_id"] != record["code_evidence_id"] or record["quote_owner"] != "program"):
                    claim_errors.append(f"invalid v{version} claim or quote ownership")
                evidence_ids = claim["evidence_ids"]
                if not 1 <= len(evidence_ids) <= 6 or len(set(evidence_ids)) != len(evidence_ids):
                    claim_errors.append("invalid claim evidence identifiers")
                citation_ids = sorted({by_id[value]["citation_id"] for value in evidence_ids})
                if record["citation_ids"] != citation_ids:
                    claim_errors.append("program citations differ from selected evidence")
                expected_citations.extend(citation_ids)
                if claim["code_evidence_id"] is not None:
                    expected_citations.extend(citation_ids)
                if version == 3:
                    origins = [by_id[value]["origin"] for value in evidence_ids]
                    identities = {(origin["source_type"], origin["knowledge_base_id"], origin["document_id"])
                                  if origin["source_type"] == "knowledge_base" else ("web", origin["url"])
                                  for origin in origins}
                    if (claim["scope"] not in ("source", "shared")
                            or (claim["scope"] == "source" and len(identities) != 1)
                            or (claim["scope"] == "shared" and len(identities) < 2)):
                        claim_errors.append("invalid historical source scope")
                if [support["evidence_id"] for support in record["evidence"]] != claim["evidence_ids"]:
                    claim_errors.append("claim evidence selection differs from model response")
                for support in record["evidence"]:
                    entry = by_id[support["evidence_id"]]
                    if (support["quote"] != entry["content"] or support["origin"] != entry["origin"]
                            or support["citation_id"] != entry["citation_id"]):
                        claim_errors.append("program quote differs from registered evidence")
                code_id = claim["code_evidence_id"]
                if code_id is None and record["code"] is not None:
                    claim_errors.append("display code has no selected evidence")
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
            actual_citations = [int(value) for value in re.findall(r"\[来源\s*([1-9]\d*)\s*\]", run["answer"])]
            if actual_citations != expected_citations or run["answer"].count("[来源") != len(actual_citations):
                claim_errors.append("display citations differ from program claims")
        except (KeyError, TypeError, ValueError, IndexError):
            claim_errors.append("invalid published evidence claims")
    return {"evidence_binding_valid": not errors, "evidence_binding_errors": errors,
            "program_claim_binding_valid": not claim_errors if published else None,
            "program_claim_binding_errors": claim_errors, "evidence_catalog_count": len(catalog)}
