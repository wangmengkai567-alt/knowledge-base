"""Material merge and clipping preserve the content actually sent to the model."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.materials import (
    AnswerSource,
    InputBudget,
    MaterialProcessor,
    MaterialSegment,
    clip_source,
)


class ContextMaterialTest(unittest.TestCase):
    def source(self):
        return AnswerSource(0, 1, "notes.md", 1,
                            [MaterialSegment(11, 0, 0, 5, "abcde"), MaterialSegment(12, 1, 0, 5, "fghij")], [])

    def test_clip_preserves_all_original_ranges(self):
        source = clip_source(self.source(), 8)
        self.assertEqual(source.content, "abcde\nfg")
        self.assertEqual([(s.chunk_id, s.start, s.end) for s in source.segments], [(11, 0, 5), (12, 0, 2)])
        self.assertTrue(source.truncated)

    def test_clipped_away_chunks_never_appear_in_citation(self):
        source = clip_source(self.source(), 5)
        self.assertEqual([s.chunk_id for s in source.segments], [11])
        self.assertEqual(source.content, "abcde")

    def test_budget_counts_complete_messages(self):
        budget = InputBudget("qwen3.8-flash", input_limit=700)
        processor = MaterialProcessor(None, 0, budget)
        source = AnswerSource(0, 1, "notes.md", 1, [MaterialSegment(11, 0, 0, 5000, "甲" * 5000)], [])
        def messages(context):
            return [{"role": "system", "content": "fixed prompt"}, {"role": "user", "content": context}]
        prepared = processor.prepare_sources([source], messages, 200)
        self.assertLessEqual(prepared.estimated_input_tokens, 700)
        self.assertEqual(prepared.estimated_input_tokens, budget.estimate(prepared.messages, [], []))
        self.assertEqual(prepared.sources[0].citation_id, 1)
        self.assertIn(prepared.sources[0].content, prepared.messages[-1]["content"])
        self.assertTrue(prepared.sources[0].truncated)


if __name__ == "__main__":
    unittest.main()
