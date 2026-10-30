"""Run golden_set.json against a live ai-service. Writes eval/last_run.json."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from faithfulness import unsupported_sentences
from keyword_scoring import keyword_matches, keywords_ok
from run_metrics import score_run, summarize_runs

ROOT = Path(__file__).resolve().parent
GOLDEN = ROOT / "golden_set.json"
OUT = ROOT / "phase-6-golden-results.json"
LOCK = ROOT / ".run_eval.lock"
BASE = os.environ.get("EVAL_BASE_URL", "http://127.0.0.1:8084")
EMAIL = os.environ.get("EVAL_EMAIL", "admin@company.com")
PASSWORD = os.environ.get("EVAL_PASSWORD", "Admin@1234")
SLEEP_S = float(os.environ.get("EVAL_SLEEP_S", "3.0"))


_PID_WIDTH = 15


def acquire_run_lock(path: Path = LOCK) -> int:
    """Hold an OS lock on byte 0 for the life of the process; exit if another run holds it.

    The OS drops the lock when the holder dies, so an orphaned run blocks new ones
    only while it is still alive, and a crash never leaves a stale lock behind.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.lseek(fd, 1, os.SEEK_SET)
        holder = os.read(fd, _PID_WIDTH).decode("ascii", errors="replace").strip() or "?"
        os.close(fd)
        raise SystemExit(
            f"another run_eval.py (pid {holder}) is still running; "
            f"stop it before starting a new run (lock: {path})"
        ) from None
    os.lseek(fd, 1, os.SEEK_SET)
    os.write(fd, f"{os.getpid():<{_PID_WIDTH}}".encode("ascii"))
    return fd


def is_quota_denied(err: object) -> bool:
    """True when the upstream model returns 403 / Unpurchased / access denied."""
    text = str(err).upper()
    return (
        "LLM_AUTHORIZATION_FAILED" in text
        or "LLM_RATE_LIMITED" in text
        or "HTTP 403" in text
        or "HTTP 401" in text
        or "HTTP 429" in text
        or "RETURNED 403" in text
        or "RETURNED 401" in text
        or "ACCESSDENIED" in text
        or "UNPURCHASED" in text
        or "QUOTA EXHAUSTED" in text
        or ("403" in text and ("EMBEDDING" in text or "RERANK" in text))
    )


def http_json(method: str, path: str, token: str | None = None, body: dict | None = None, timeout: int = 180):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        raise RuntimeError(f"{method} {path} -> {exc.code} {payload[:400]!r}") from exc


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


def resolve_kb(item: dict, kbs: list[dict], baseline: dict[str, dict]) -> int:
    # 历史题目固定为历史运行的同一知识库范围；新场景显式指定范围。
    kid = item.get("kb_id") if "kb_id" in item else baseline.get(item["id"], {}).get("kb_id")
    if kid not in {kb["id"] for kb in kbs}:
        raise ValueError(f"{item['id']}: expected knowledge base {kid} is unavailable")
    return kid


def write_results(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    pending.replace(path)


def score_record(record: dict, item: dict) -> dict:
    record = dict(record)
    results = record.get("retrieval", {}).get("results", [])
    relevant = item.get("relevant_filenames") or []
    rank = first_hit_rank(results, relevant)
    record.update(
        hit_at_5=(rank is not None and rank <= 5) if relevant else None,
        hit_at_10=(rank is not None and rank <= 10) if relevant else None,
        mrr=(1 / rank if rank else 0.0) if relevant else None,
    )
    if "run_snapshot" not in record:
        if "error" in record:
            return record
        raise ValueError("saved row has no actual run snapshot; legacy results cannot be rescored")
    snapshot = record["run_snapshot"]
    rag = snapshot["result"]
    answer = rag["answer"]
    outcomes = [event["data"]["outcome"] for event in rag["events"] if event["kind"] == "answer_validated"]
    if answer and len(outcomes) != 1:
        raise ValueError("published answer has no unique validation outcome")
    refused = outcomes == ["insufficient"]
    # 与历史启发式规则相同，但证据仅为本次实际送模内容，不另扩邻块。
    evidence = "\n".join(source["content"] for source in rag["sources"])
    matches = keyword_matches(answer, item)
    traps = [t for t in item.get("hallucination_traps", []) if t and t in answer]
    answered = bool(answer) and not refused
    unsupported = unsupported_sentences(answer, evidence) if answered else []
    record.update(
        answer=answer, answerable=item["answerable"], refused=refused,
        must_contain_ok=keywords_ok(matches), keyword_matches=matches,
        source_files=[s.get("document_filename") for s in rag["sources"]],
        trap_hit=traps, missed_recall=bool(item["answerable"] and refused),
        refusal_correct=bool(not item["answerable"] and rag["status"] == "insufficient"),
        unsupported_sentences=unsupported, answered=answered,
        hallucinated=bool(answered and (traps or unsupported or not item["answerable"])),
        **score_run(snapshot, item),
    )
    if rag["status"] not in ("completed", "insufficient"):
        record["error"] = f"{rag['status']}: {rag['stop_reason']} ({rag['error_code']})"
    return record


def score_keyword_baseline(baseline: dict, items: list[dict]) -> dict:
    """Apply the current keyword rubric to saved historical answers, without inventing evidence."""
    previous = {row["id"]: row for row in baseline["rows"]}
    if len(previous) != len(baseline["rows"]) or set(previous) != {item["id"] for item in items}:
        raise ValueError("baseline must contain every question exactly once")
    scores = []
    for item in items:
        row = previous[item["id"]]
        if row["query"] != item["query"]:
            raise ValueError(f"{item['id']}: baseline query differs from current question")
        if item["answerable"]:
            matched = keywords_ok(keyword_matches(row.get("answer", ""), item))
            scores.append(float(matched is True and not row.get("error")))
    return {**baseline["summary"], "answer_correctness": _avg(scores)}


def summary(rows: list[dict], baseline: dict, expected: int) -> dict:
    recall = [r for r in rows if r.get("hit_at_10") is not None]
    answered = [r for r in rows if r.get("answered")]
    # 运行失败保留在质量分母中，不通过排除错误提高正确率。
    answerable = [r for r in rows if r.get("answerable")]
    unanswerable = [r for r in rows if r.get("answerable") is False]
    gen = [r for r in answerable if r.get("must_contain_ok") is not None or r.get("error")]
    result = {
        "ran_at": datetime.now(UTC).isoformat(),
        "n_items": len(rows), "expected_items": expected,
        "n_errors": sum(bool(r.get("error")) for r in rows),
        "hit_at_5": _avg([float(r["hit_at_5"]) for r in recall]),
        "hit_at_10": _avg([float(r["hit_at_10"]) for r in recall]),
        "mrr": _avg([r["mrr"] for r in recall]),
        "answer_correctness": _avg([float(bool(r.get("must_contain_ok")) and not r.get("error")) for r in gen]),
        "hallucination_rate": _avg([float(r["hallucinated"]) for r in answered]),
        "n_answered": len(answered),
        "refusal_accuracy": _avg([float(bool(r.get("refusal_correct"))) for r in unanswerable]),
        "missed_recall_rate": _avg([float(bool(r.get("missed_recall"))) for r in answerable]),
        "hallucination_judge": "historical_heuristic_actual_model_materials",
        **summarize_runs(rows),
    }
    gates = {}
    for metric in ("answer_correctness", "refusal_accuracy", "hit_at_5", "hit_at_10", "mrr"):
        target = baseline.get(metric)
        if target is not None:
            gates[metric] = result[metric] is not None and result[metric] >= target
    target = baseline.get("hallucination_rate")
    if target is not None:
        gates["heuristic_hallucination_rate"] = result["hallucination_rate"] is not None and result["hallucination_rate"] <= target
    gates.update(
        complete=len(rows) == expected,
        no_errors=not result["n_errors"],
        valid_evidence=all(r.get("evidence_valid") is True for r in rows),
        within_budget=all(r.get("budget_valid") is True for r in rows),
        valid_citations=all(r.get("citations_valid") is True for r in rows),
        ordered_events=all(r.get("sequence_valid") is True for r in rows),
        no_repeat_execution=not result["repeated_executions"],
        expected_tools=all(r.get("tool_selection_ok") is True for r in rows),
    )
    if any(r.get("protocol_version") == 5 for r in rows):
        gates["usage_ledger"] = all(r.get("ledger_valid") is True for r in rows)
    if not baseline:
        gates["scenario_outcomes"] = all(
            r.get("status") == ("completed" if r["answerable"] else "insufficient") for r in rows
        )
        gates["scenario_keywords"] = all(r.get("must_contain_ok") is not False for r in rows)
    result["gates"] = gates
    result["automated_gates_passed"] = all(gates.values())
    result["semantic_acceptance"] = "requires_manual_review"
    return result


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=GOLDEN)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--baseline", type=Path, default=ROOT / "last_run.json")
    parser.add_argument("--run-dir", type=Path, default=ROOT.parent / "ai-service/data/answer-runs")
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--ids", nargs="+", help="只复测指定题号；保留原题集与请求范围")
    args = parser.parse_args()
    lock_fd = acquire_run_lock()
    try:
        if args.rescore:
            if args.ids:
                raise SystemExit("--ids 只用于新实测，不得改变保存结果的离线评分范围")
            # 此分支在登录、读配置、列库或任何网络操作之前完成。
            payload = json.loads(args.out.read_text(encoding="utf-8"))
            if payload.get("version") != 6:
                raise SystemExit("only version 6 evidence snapshots support offline rescoring")
            items = {item["id"]: item for item in payload["dataset"]["items"]}
            if "selected_ids" in payload:
                items = {key: items[key] for key in payload["selected_ids"]}
            payload.setdefault("scoring_history", []).append(payload["summary"])
            payload["rows"] = [score_record(row, items[row["id"]]) for row in payload["rows"]]
            payload["summary"] = summary(payload["rows"], payload["baseline_summary"], len(items))
            payload["summary"]["rescore"] = True
            write_results(args.out, payload)
            print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
            return
        if args.out.exists():
            raise SystemExit(f"refusing to overwrite existing results: {args.out}")
        golden = json.loads(args.dataset.read_text(encoding="utf-8"))
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        previous = {row["id"]: row for row in baseline["rows"]}
        items = golden["items"]
        if args.ids:
            unknown = set(args.ids) - {item["id"] for item in items}
            if unknown or len(set(args.ids)) != len(args.ids):
                raise SystemExit(f"未知或重复题号：{args.ids}")
            items = [item for item in items if item["id"] in args.ids]
        for item in items:
            keyword_matches("", item)
        baseline_scores = score_keyword_baseline(baseline, golden["items"]) if args.dataset.resolve() == GOLDEN.resolve() else {}
        token = login()
        kbs = http_json("GET", "/api/v1/knowledge/bases", token)["knowledge_bases"]
        uploaded = {}
        for kb in kbs:
            uploaded[kb["id"]] = http_json("GET", f"/api/v1/knowledge/bases/{kb['id']}/documents", token)["documents"]
        # 全部范围先验证，避免中途才发现题目映射错误。
        for item in items:
            resolve_kb(item, kbs, previous)
        payload = {
            "version": 6, "dataset": golden,
            "baseline_summary": baseline_scores,
            "baseline_recorded_summary": baseline["summary"] if baseline_scores else {},
            "knowledge_bases": kbs, "documents_before": uploaded, "rows": [],
            "selected_ids": [item["id"] for item in items],
        }
        for index, item in enumerate(items, 1):
            kid = resolve_kb(item, kbs, previous)
            record = {"id": item["id"], "query": item["query"], "answerable": item["answerable"], "kb_id": kid}
            try:
                # 独立检索只用于召回/排名，绝不作为生成步骤的证据。
                if not item.get("allow_web", False):
                    record["retrieval"] = http_json("POST", f"/api/v1/knowledge/bases/{kid}/retrieve", token,
                                                     {"query": item["query"], "top_k": 10})
                request = {"query": item["query"], "top_k": 10, "knowledge_base_ids": item.get("knowledge_base_ids", [kid]),
                           "allow_web": item.get("allow_web", False), "user_urls": item.get("user_urls", [])}
                start = time.monotonic()
                rag = http_json("POST", f"/api/v1/knowledge/bases/{kid}/rag/query", token, request)
                record["elapsed_seconds"] = round(time.monotonic() - start, 3)
                paths = list(args.run_dir.glob(f"user-*/run-{rag['run_id']}.json"))
                if len(paths) != 1:
                    raise ValueError(f"expected one persisted snapshot for {rag['run_id']}, got {len(paths)}")
                snapshot = json.loads(paths[0].read_text(encoding="utf-8"))
                if snapshot["result"] != rag or snapshot["query"] != item["query"]:
                    raise ValueError("persisted snapshot does not match actual response")
                record["request"] = request
                record["run_snapshot"] = snapshot
                record = score_record(record, item)
            except Exception as exc:
                record["error"] = str(exc)
            payload["rows"].append(record)
            payload["summary"] = summary(payload["rows"], payload["baseline_summary"], len(items))
            write_results(args.out, payload)
            print(f"[{index}/{len(items)}] {item['id']} status={record.get('status')} keyword={record.get('must_contain_ok')} error={record.get('error')}", flush=True)
            if is_quota_denied(record.get("error", "")):
                print("ABORT: quota/access denied; no automatic retry", flush=True)
                break
            if index < len(items):
                time.sleep(SLEEP_S)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
