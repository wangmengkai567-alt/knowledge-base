"""Run golden_set.json against a live ai-service. Writes eval/last_run.json."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden_set.json"
OUT = ROOT / "last_run.json"
BASE = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8084")
EMAIL = os.environ.get("EVAL_EMAIL", "admin@company.com")
PASSWORD = os.environ.get("EVAL_PASSWORD", "Admin@1234")
REFUSAL_RE = re.compile(r"^我不知道[。.!！]?$")
CITE_RE = re.compile(r"\[来源\s*\d+\]")


def http_json(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 180):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            payload = e.read()
            if e.code == 429 and attempt < 5:
                time.sleep(2 ** attempt)
                req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
                continue
            raise RuntimeError(f"{method} {path} -> {e.code} {payload[:400]!r}") from e
        except TimeoutError:
            if attempt < 5:
                time.sleep(2)
                req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
                continue
            raise


def login() -> str:
    data = http_json("POST", "/api/v1/users/login", body={"email": EMAIL, "password": PASSWORD}, timeout=20)
    return data["access_token"]


def filename_key(name: str) -> str:
    return Path(str(name).replace("\\", "/")).name.lower()


def names_match(expected: str, actual: str) -> bool:
    e = filename_key(expected)
    a = filename_key(actual)
    return e == a or a.endswith(e) or e.endswith(a)


def first_hit_rank(results: list[dict], relevant: list[str]) -> int | None:
    if not relevant:
        return None
    seen_docs: list[str] = []
    for row in results:
        fn = row.get("document_filename") or ""
        if fn not in seen_docs:
            seen_docs.append(fn)
        if any(names_match(rel, fn) for rel in relevant):
            return len(seen_docs)
    return None


def is_refusal(answer: str) -> bool:
    return bool(REFUSAL_RE.match((answer or "").strip()))


def char_ngrams(text: str, n: int = 4) -> set[str]:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def unsupported_sentences(answer: str, evidence: str) -> list[str]:
    cleaned = CITE_RE.sub("", answer or "")
    parts = [p.strip() for p in re.split(r"[。！？!?；;]\s*", cleaned) if p.strip()]
    ev_grams = char_ngrams(evidence, 4)
    bad: list[str] = []
    if not evidence.strip():
        return [p for p in parts if len(p) >= 6] or ["<empty-evidence>"]
    for part in parts:
        if len(part) < 8:
            continue
        grams = char_ngrams(part, 4)
        if not grams:
            continue
        overlap = len(grams & ev_grams) / len(grams)
        if overlap < 0.08:
            bad.append(part)
    return bad


def resolve_kb(item: dict, kbs: list[dict], file_to_kbs: dict[str, list[int]]) -> tuple[int | None, str]:
    relevant = item.get("relevant_filenames") or []
    for fn in relevant:
        ids = file_to_kbs.get(filename_key(fn), [])
        if ids:
            return ids[0], "filename"
    hint = (item.get("kb_hint") or "").strip()
    if hint:
        for kb in kbs:
            if hint.lower() in kb["name"].lower() or kb["name"].lower() in hint.lower():
                return kb["id"], "hint"
    for kb in kbs:
        if kb.get("chunk_count"):
            return kb["id"], "fallback_nonempty"
    return (kbs[0]["id"] if kbs else None), "fallback_first"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    items = golden["items"]
    token = login()
    kb_payload = http_json("GET", "/api/v1/knowledge/bases", token=token, timeout=20)
    kbs = kb_payload.get("knowledge_bases") or []
    file_to_kbs: dict[str, list[int]] = defaultdict(list)
    uploaded: dict[int, list[str]] = {}
    for kb in kbs:
        docs = http_json("GET", f"/api/v1/knowledge/bases/{kb['id']}/documents", token=token, timeout=20)
        names = [d["filename"] for d in docs.get("documents") or []]
        uploaded[kb["id"]] = names
        for name in names:
            file_to_kbs[filename_key(name)].append(kb["id"])

    rows = []
    for i, item in enumerate(items, 1):
        kb_id, kb_how = resolve_kb(item, kbs, file_to_kbs)
        relevant = item.get("relevant_filenames") or []
        missing_files = [fn for fn in relevant if filename_key(fn) not in file_to_kbs]
        record: dict = {
            "id": item["id"],
            "query": item["query"],
            "answerable": item["answerable"],
            "kb_id": kb_id,
            "kb_resolve": kb_how,
            "missing_files": missing_files,
        }
        if kb_id is None:
            record["error"] = "no knowledge base"
            rows.append(record)
            continue
        try:
            retrieved = http_json(
                "POST",
                f"/api/v1/knowledge/bases/{kb_id}/retrieve",
                token=token,
                body={"query": item["query"], "top_k": 10},
                timeout=60,
            )
            results = retrieved.get("results") or []
            rank = first_hit_rank(results, relevant)
            record["hit_at_5"] = rank is not None and rank <= 5 if relevant else None
            record["hit_at_10"] = rank is not None and rank <= 10 if relevant else None
            record["mrr"] = (1.0 / rank) if rank else (0.0 if relevant else None)
            record["retrieve_files"] = [r.get("document_filename") for r in results]
            chunk_ids = [r["chunk_id"] for r in results[:10]]
            evidence = "\n".join(r.get("content") or "" for r in results)
            rag = http_json(
                "POST",
                f"/api/v1/knowledge/bases/{kb_id}/rag/query",
                token=token,
                body={
                    "query": item["query"],
                    "top_k": 10,
                    "chunk_ids": chunk_ids or None,
                    "knowledge_base_ids": [kb_id],
                },
                timeout=180,
            )
            answer = rag.get("answer") or ""
            record["answer"] = answer
            record["source_files"] = [s.get("document_filename") for s in rag.get("sources") or []]
            refused = is_refusal(answer)
            record["refused"] = refused
            must = item.get("must_contain") or []
            record["must_contain_ok"] = all(term.lower() in answer.lower() for term in must) if must else None
            traps = [t for t in (item.get("hallucination_traps") or []) if t and t in answer]
            record["trap_hit"] = traps
            missed = bool(item["answerable"] and refused)
            record["missed_recall"] = missed
            record["refusal_correct"] = (not item["answerable"] and refused)
            unsupported = [] if refused else unsupported_sentences(answer, evidence)
            record["unsupported_sentences"] = unsupported
            answered = not refused
            record["answered"] = answered
            record["hallucinated"] = bool(
                answered
                and (
                    bool(traps)
                    or bool(unsupported)
                    or (not item["answerable"])
                )
            )
        except Exception as e:
            record["error"] = str(e)
        rows.append(record)
        print(f"[{i}/{len(items)}] {item['id']} kb={kb_id} refused={record.get('refused')} hit10={record.get('hit_at_10')} hallu={record.get('hallucinated')} err={record.get('error')}", flush=True)
        time.sleep(1.2)

    recall_items = [r for r in rows if r.get("hit_at_10") is not None]
    answered = [r for r in rows if r.get("answered")]
    answerable = [r for r in rows if r.get("answerable") and "error" not in r]
    unanswerable = [r for r in rows if r.get("answerable") is False and "error" not in r]
    gen_items = [r for r in rows if r.get("answerable") and r.get("must_contain_ok") is not None and "error" not in r]
    summary = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "n_items": len(rows),
        "n_errors": sum(1 for r in rows if r.get("error")),
        "hit_at_5": _avg([1.0 if r["hit_at_5"] else 0.0 for r in recall_items]),
        "hit_at_10": _avg([1.0 if r["hit_at_10"] else 0.0 for r in recall_items]),
        "mrr": _avg([r["mrr"] for r in recall_items if r.get("mrr") is not None]),
        "answer_correctness": _avg([1.0 if r["must_contain_ok"] else 0.0 for r in gen_items]),
        "hallucination_rate": _avg([1.0 if r["hallucinated"] else 0.0 for r in answered]),
        "n_answered": len(answered),
        "refusal_accuracy": _avg([1.0 if r["refusal_correct"] else 0.0 for r in unanswerable]),
        "missed_recall_rate": _avg([1.0 if r["missed_recall"] else 0.0 for r in answerable]),
        "missing_source_files": sorted({fn for r in rows for fn in r.get("missing_files") or []}),
        "knowledge_bases": [{"id": kb["id"], "name": kb["name"], "doc_count": kb.get("doc_count"), "chunk_count": kb.get("chunk_count"), "files": uploaded.get(kb["id"], [])} for kb in kbs],
    }
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {OUT}")


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


if __name__ == "__main__":
    main()
