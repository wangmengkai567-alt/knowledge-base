"""Actual evidence, explicit failure and offline rescoring regression checks."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_eval
from audit_sources import audit
from run_metrics import score_run


def snapshot():
    return {
        "limits": {"model_calls": 6, "tool_calls": 10, "total_tokens": 64000, "timeout": 120},
        "result": {
            "answer": "容器是运行实例。[来源 1]", "status": "completed", "stop_reason": "answered",
            "error_code": None,
            "sources": [{"citation_id": 1, "source_type": "web_page", "url": "https://example.com/",
                         "fetched_at": "2026-10-29T00:00:00Z", "content": "容器是运行实例。"}],
            "usage": {"model_calls": 1, "tool_calls": 1, "charged_tokens": 100,
                      "unreported_model_calls": 0, "prompt_tokens": 20, "completion_tokens": 10},
            "events": [
                {"sequence": 1, "kind": "tool_started", "elapsed_ms": 0,
                 "data": {"tool": "web_read", "arguments_summary": '{"urls":["https://example.com/"]}'}},
                {"sequence": 2, "kind": "materials_prepared", "data": {"citation_ids": [1]}},
                {"sequence": 3, "kind": "sources_updated", "data": {"citation_ids": [1]}},
                {"sequence": 4, "kind": "model_started", "elapsed_ms": 1,
                 "data": {"estimated_input_tokens": 100, "output_limit": 100}},
                {"sequence": 5, "kind": "model_finished", "data": {"prompt_tokens": 20, "completion_tokens": 10}},
                {"sequence": 6, "kind": "answer_validated", "data": {"outcome": "answered"}},
                {"sequence": 7, "kind": "run_finished", "data": {"status": "completed", "stop_reason": "answered"}},
            ],
        },
    }


class RunMetricsTest(unittest.TestCase):
    def test_valid_saved_evidence(self):
        result = score_run(snapshot(), {"allow_web": True})
        for key in ("evidence_valid", "citations_valid", "budget_valid", "sequence_valid"):
            self.assertTrue(result[key], key)

    def test_unused_evidence_is_not_valid(self):
        value = snapshot()
        value["result"]["events"][2]["data"]["citation_ids"] = []
        self.assertFalse(score_run(value, {"allow_web": True})["evidence_valid"])

    def test_missing_citation_and_budget_violation(self):
        value = snapshot()
        value["result"]["answer"] = "无依据的答案[来源 2]"
        value["result"]["usage"]["charged_tokens"] = 64001
        result = score_run(value, {"allow_web": True})
        self.assertFalse(result["citations_valid"])
        self.assertFalse(result["budget_valid"])

    def test_missing_snapshot_fails_source_audit_without_network(self):
        with patch("audit_sources.http_json", side_effect=AssertionError("network forbidden")):
            result = audit({"version": 6, "rows": [{"id": "failed request"}]}, "unused")
        self.assertFalse(result["all_kb_sources_match"])

    def test_rescore_never_logs_in_retrieves_or_calls_model(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "results.json"
            value = {"version": 6, "dataset": {"items": [{"id": "X", "answerable": True,
                     "allow_web": True, "must_contain": ["容器"]}]}, "baseline_summary": {}, "summary": {},
                     "rows": [{"id": "X", "run_snapshot": snapshot()}]}
            out.write_text(json.dumps(value), encoding="utf-8")
            acquire = run_eval.acquire_run_lock
            with (
                patch.object(sys, "argv", ["run_eval.py", "--rescore", "--out", str(out)]),
                patch.object(run_eval, "acquire_run_lock", side_effect=lambda: acquire(Path(folder) / "lock")),
                patch.object(run_eval, "http_json", side_effect=AssertionError("network forbidden")),
                patch.object(run_eval, "login", side_effect=AssertionError("login forbidden")),
            ):
                run_eval.main()
            rescored = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(rescored["summary"]["rescore"])
            self.assertEqual(rescored["rows"][0]["run_snapshot"], value["rows"][0]["run_snapshot"])


if __name__ == "__main__":
    unittest.main()
