"""build_context_block must never hand the LLM an empty context."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.prompt.base import build_context_block


def _block(text: str, name: str = "note.md") -> dict:
    return {"content": text, "document_filename": name, "score": 0.9}


class ContextBlockTests(unittest.TestCase):
    def test_oversized_first_block_is_truncated_not_dropped(self):
        text = "Get-Location 查看当前目录。" + "填充" * 3000
        context = build_context_block([_block(text), _block("第二段")], max_length=1000)
        self.assertIn("[来源 1: note.md", context)
        self.assertIn("Get-Location", context)
        self.assertIn("展示前 1 条", context)
        self.assertLessEqual(len(context), 1100)

    def test_later_block_is_cut_to_remaining_room(self):
        first = "甲" * 400
        second = "乙" * 2000
        context = build_context_block([_block(first, "a.md"), _block(second, "b.md")], max_length=1000)
        self.assertIn("[来源 2: b.md", context)
        self.assertIn("展示前 2 条", context)

    def test_tiny_leftover_room_stops_without_partial_block(self):
        first = "甲" * 950
        context = build_context_block([_block(first, "a.md"), _block("乙" * 500, "b.md")], max_length=1000)
        self.assertNotIn("[来源 2", context)
        self.assertIn("展示前 1 条", context)

    def test_fits_without_truncation_note(self):
        context = build_context_block([_block("短段落一"), _block("短段落二")])
        self.assertIn("[来源 2", context)
        self.assertNotIn("已截断", context)


if __name__ == "__main__":
    unittest.main()
