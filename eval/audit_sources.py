"""Read-only source-version audit; validates saved evidence without rebuilding it."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from run_eval import http_json, login, write_results


def audit(payload: dict, token: str) -> dict:
    if payload["version"] != 6:
        raise ValueError("source audit requires version 6 snapshots")
    documents = {}
    chunks = {}
    checks = []
    for row in payload["rows"]:
        if "run_snapshot" not in row:
            checks.append({"item": row["id"], "valid": False, "failures": ["missing run snapshot"]})
            continue
        for source in row["run_snapshot"]["result"]["sources"]:
            if source["source_type"] != "knowledge_base":
                checks.append({"item": row["id"], "citation_id": source["citation_id"],
                               "kind": source["source_type"], "url": source["url"],
                               "fetched_at": source["fetched_at"],
                               "basis": "persisted fetch snapshot; never refetch changed web content"})
                continue
            key = (source["knowledge_base_id"], source["document_id"])
            base = f"/api/v1/knowledge/bases/{key[0]}/documents/{key[1]}"
            if key not in documents:
                documents[key] = http_json("GET", base, token)
                chunks[key] = http_json("GET", base + "/chunks", token)["chunks"]
            doc = documents[key]
            version = doc["updated_at"] or doc["created_at"]
            by_id = {chunk["id"]: chunk for chunk in chunks[key]}
            failures = []
            # ISO timestamps may represent the same instant as Z or +00:00.
            if datetime.fromisoformat(version) != datetime.fromisoformat(source["document_updated_at"]):
                failures.append("document version changed")
            for segment in source["segments"]:
                chunk = by_id.get(segment["chunk_id"])
                if (
                    chunk is None or chunk["position"] != segment["position"]
                    or chunk["content"][segment["start"]:segment["end"]] != segment["content"]
                ):
                    failures.append(f"segment mismatch: {segment['chunk_id']}")
            checks.append({"item": row["id"], "citation_id": source["citation_id"],
                           "kind": "knowledge_base", "document_id": key[1],
                           "version": version, "valid": not failures, "failures": failures})
    return {"version": 6, "checks": checks,
            "all_kb_sources_match": all(check.get("valid", True) for check in checks),
            "documents": [{"knowledge_base_id": key[0], "document": value, "chunks": chunks[key]}
                          for key, value in documents.items()]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite source audit: {args.out}")
    result = audit(json.loads(args.results.read_text(encoding="utf-8")), login())
    write_results(args.out, result)
    print(json.dumps({"checks": len(result["checks"]),
                      "all_kb_sources_match": result["all_kb_sources_match"]}))


if __name__ == "__main__":
    main()
