"""Material merge and clipping preserve the content actually sent to the model."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from internal.biz.evidence import bind_catalog, code_body
from internal.biz.materials import (
    AnswerSource,
    InputBudget,
    MaterialProcessor,
    MaterialSegment,
    WebSource,
    clip_source,
    pack_sources,
)
from internal.biz.text_structure import fenced_code_ranges
from internal.biz.tool_types import ToolResult
from internal.biz.tools import ToolScope
from pkg.errors.base import RAGError


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
        part = prepared.sources[0].segments[0]
        self.assertGreater(len(part.content), 0)
        self.assertEqual(source.segments[0].content[part.start:part.end], part.content)
        self.assertEqual(len(source.segments[0].content), 5000)


    def prepare(self, source, *, query="", limit=700):
        budget = InputBudget("qwen3.8-flash", input_limit=limit)
        prepared = MaterialProcessor(None, 0, budget).prepare_sources(
            [source], lambda context: [{"role": "system", "content": "fixed prompt"},
                                     {"role": "user", "content": context}], 200, query=query,
        )
        self.assertLessEqual(prepared.estimated_input_tokens, limit)
        return prepared.sources[0]

    def test_long_prose_keeps_tail_query_and_original_chunk_offsets(self):
        content = "甲" * 3000 + "内存限制为512MB，不能超过" + "乙" * 2000
        source = AnswerSource(0, 1, "notes.md", 1,
                              [MaterialSegment(11, 4, 73, 73 + len(content), content)], [])
        result = self.prepare(source, query="内存限制")
        part = result.segments[0]
        self.assertIn("内存限制为512MB，不能超过", part.content)
        self.assertGreater(part.start, 73)
        self.assertEqual(content[part.start - 73:part.end - 73], part.content)
        self.assertEqual((part.chunk_id, part.position), (11, 4))
        self.assertTrue(result.truncated)

    def test_web_long_prose_preserves_absolute_offsets(self):
        content = "甲" * 3000 + '内存配置为512MB，路径为"C:\\data\\file"' + "乙" * 2000
        source = WebSource(0, "https://example.com/", "配置", "2026-11-02T00:00:00Z", content,
                           "web_page", source_id="web_1", start=1000, end=1000 + len(content), total_chars=1000 + len(content))
        result = self.prepare(source, query="内存配置")
        self.assertIn('路径为"C:\\data\\file"', result.content)
        self.assertEqual(content[result.start - 1000:result.end - 1000], result.content)
        self.assertEqual(result.end - result.start, len(result.content))
        self.assertEqual(result.source_id, "web_1")
        self.assertTrue(result.truncated)

    def test_complete_code_survives_partial_prose_and_evidence_binding(self):
        code = '```sh\r\necho "a\\b"\r\n```\r\n'
        content = "甲" * 3000 + "\r\n" + code + "乙" * 3000
        left, right = fenced_code_ranges(content)[0]
        sources = [
            AnswerSource(1, 1, "notes.md", 1, [MaterialSegment(11, 0, 0, len(content), content, [(left, right)])], [],
                         document_updated_at="2026-11-02T00:00:00Z"),
            WebSource(1, "https://example.com/", "示例", "2026-11-02T00:00:00Z", content, "web_page",
                      source_id="web_1", start=1000, end=1000 + len(content), total_chars=1000 + len(content),
                      code_ranges=[(1000 + left, 1000 + right)]),
        ]
        for source in sources:
            with self.subTest(kind=source.source_type):
                result = self.prepare(source, query="echo")
                self.assertIn(code, result.content)
                message = ToolResult("read", "read", "success", sources=[result]).to_message()
                catalog = bind_catalog([message], [result], ToolScope(1, (1,), True), "run")
                fragment = next(entry for entry in catalog if entry.kind == "code")
                self.assertEqual(fragment.content, code)
                self.assertEqual(code_body(fragment), ('echo "a\\b"\r\n', 'sh'))
                offset = 1000 if isinstance(source, WebSource) else 0
                self.assertEqual((fragment.origin["start"], fragment.origin["end"]), (offset + left, offset + right))
                self.assertTrue(result.truncated)

    def test_query_code_keeps_priority_over_its_prose_neighbors(self):
        code = "```sh\n" + "echo ok\n" * 30 + "```\n"
        content = "甲" * 3000 + "\n" + code + "命令用途说明" * 12 + "。\n"
        source = AnswerSource(1, 1, "notes.md", 1, [MaterialSegment(
            11, 0, 0, len(content), content, fenced_code_ranges(content),
        )], [])
        result = self.prepare(source, query="echo")
        self.assertIn(code, result.content)
        self.assertEqual([pair for part in result.segments for pair in part.code_ranges], fenced_code_ranges(content))

    def test_oversized_code_is_rejected_instead_of_truncated(self):
        content = "```text\n" + "甲" * 5000 + "\n```\n"
        source = AnswerSource(0, 1, "notes.md", 1,
                              [MaterialSegment(11, 0, 0, len(content), content, [(0, len(content))])], [])
        with self.assertRaises(RAGError) as error:
            self.prepare(source)
        self.assertEqual(error.exception.reason, "MATERIAL_BUDGET_EXCEEDED")
        self.assertIn("代码块", error.exception.message)
        self.assertEqual(source.segments[0].content, content)

    def test_prose_can_be_retained_without_part_of_adjacent_oversized_code(self):
        code = "```text\n" + "乙" * 5000 + "\n```\n"
        content = "说明" + "甲" * 3000 + "\n" + code
        source = AnswerSource(0, 1, "notes.md", 1,
                              [MaterialSegment(11, 0, 0, len(content), content, fenced_code_ranges(content))], [])
        result = self.prepare(source, query="说明")
        self.assertIn("说明", result.content)
        self.assertNotIn("```", result.content)
        self.assertEqual(result.segments[0].code_ranges, [])
        self.assertEqual(content[result.segments[0].start:result.segments[0].end], result.content)

    def test_complete_other_source_keeps_space_before_long_prose(self):
        long = "内存限制为512MB" + "甲" * 5000
        short = "内存限制为256MB。"
        sources = [AnswerSource(0, index, f"note{index}.md", 1,
                                [MaterialSegment(index, 0, 0, len(text), text)], [])
                   for index, text in ((1, long), (2, short))]
        budget = InputBudget("qwen3.8-flash", input_limit=900)
        prepared = MaterialProcessor(None, 0, budget).prepare_sources(
            sources, lambda text: [{"role": "user", "content": text}], 200, query="内存限制",
        )
        self.assertLessEqual(prepared.estimated_input_tokens, 900)
        self.assertEqual([source.document_id for source in prepared.sources], [1, 2])
        self.assertEqual(prepared.sources[1].content, short)
        self.assertIn("内存限制为512MB", prepared.sources[0].content)

    def test_character_budget_does_not_split_crlf(self):
        content = "A" * 40 + "\r\n" + "B" * 5000
        source = AnswerSource(1, 1, "notes.md", 1, [MaterialSegment(11, 0, 0, len(content), content)], [])
        result = pack_sources([source], lambda sources: sum(len(source.content) for source in sources) <= 41)
        self.assertTrue(result)
        self.assertEqual(result[0].content, "A" * 40)
        self.assertFalse(result[0].content.endswith("\r"))

    def test_unaffordable_source_metadata_still_reports_budget_error(self):
        budget = InputBudget("qwen3.8-flash")
        def messages(text):
            return [{"role": "user", "content": text}]
        limit = budget.estimate(messages(""), [], []) + 1
        processor = MaterialProcessor(None, 0, InputBudget("qwen3.8-flash", input_limit=limit))
        source = AnswerSource(0, 1, "notes.md", 1, [MaterialSegment(11, 0, 0, 5000, "甲" * 5000)], [])
        with self.assertRaises(RAGError) as error:
            processor.prepare_sources([source], messages, 200)
        self.assertEqual(error.exception.reason, "MATERIAL_BUDGET_EXCEEDED")


if __name__ == "__main__":
    unittest.main()
