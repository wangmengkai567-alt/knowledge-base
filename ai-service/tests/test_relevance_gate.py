"""Relevance gate must keep on-topic notes even when rerank scores sit in the gray zone.

Synthetic notes only — not golden eval questions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.entity import RetrievalResult
from internal.biz.relevance import apply_relevance_gate


def _chunk(chunk_id: int, text: str, filename: str, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        score=score,
        chunk_content=text,
        chunk_position=0,
        document_id=chunk_id,
        document_filename=filename,
        knowledge_base_id=1,
    )


class RelevanceGateTests(unittest.TestCase):
    def test_keeps_below_threshold_when_all_query_terms_are_in_the_note(self):
        items = [
            _chunk(
                1,
                "checkout uses widget-token to bind the cart.",
                "checkout.md",
                0.32,
            )
        ]
        kept = apply_relevance_gate(
            "What is widget-token in checkout?",
            items,
            0.5,
        )
        self.assertEqual([item.document_filename for item in kept], ["checkout.md"])

    def test_drops_below_threshold_when_a_query_term_is_missing(self):
        items = [
            _chunk(
                1,
                "checkout binds the cart with a session cookie.",
                "checkout.md",
                0.32,
            )
        ]
        kept = apply_relevance_gate(
            "What is widget-token in checkout?",
            items,
            0.5,
        )
        self.assertEqual(kept, [])

    def test_drops_above_threshold_neighbor_that_has_none_of_the_query_terms(self):
        items = [
            _chunk(
                1,
                "InnoDB redo log is written before commit.",
                "database.md",
                0.62,
            )
        ]
        kept = apply_relevance_gate(
            "How does overlay2 store layers?",
            items,
            0.5,
        )
        self.assertEqual(kept, [])

    def test_keeps_above_threshold_when_query_has_no_english_terms(self):
        items = [_chunk(1, "红黑树的插入需要变色和旋转。", "ds.md", 0.62)]
        kept = apply_relevance_gate("红黑树插入时三种旋转怎么画？", items, 0.5)
        self.assertEqual([item.document_filename for item in kept], ["ds.md"])

    def test_chinese_only_query_does_not_rescue_below_threshold(self):
        items = [_chunk(1, "红黑树的插入需要变色和旋转。", "ds.md", 0.32)]
        kept = apply_relevance_gate("红黑树插入时三种旋转怎么画？", items, 0.5)
        self.assertEqual(kept, [])

    def test_keeps_term_hit_and_drops_high_score_neighbor(self):
        items = [
            _chunk(1, "InnoDB redo log is written before commit.", "database.md", 0.61),
            _chunk(2, "In Go, rune is an alias of int32.", "go.md", 0.28),
        ]
        kept = apply_relevance_gate("What is a rune in Go?", items, 0.5)
        self.assertEqual([item.document_filename for item in kept], ["go.md"])


if __name__ == "__main__":
    unittest.main()
