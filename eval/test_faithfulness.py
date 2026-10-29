import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from faithfulness import (
    novel_numbers,
    split_claims,
    term_covered,
    unsupported_sentences,
)


class FaithfulnessTest(unittest.TestCase):
    def test_operator_is_not_a_sentence_break(self):
        answer = "- `exit code = 0` 表示成功；\n- `exit code != 0` 表示失败；"
        parts = split_claims(answer)
        self.assertTrue(any("!= 0" in p and "失败" in p for p in parts))
        evidence = "真正判断命令是否成功，通常首先看 exit code。exit code = 0 表示成功，exit code != 0 表示失败。"
        self.assertEqual(unsupported_sentences(answer, evidence), [])

    def test_paraphrase_is_supported(self):
        evidence = "容器是镜像启动之后正在运行的实例。Dockerfile 用 docker build 构建镜像。"
        answer = "容器是基于镜像运行起来的实例，不是镜像的模板。"
        self.assertEqual(unsupported_sentences(answer, evidence), [])

    def test_summary_with_shared_terms_is_supported(self):
        evidence = "Graph 编排相比固定的 A 到 B 到 C，多了条件分支，也可以循环迭代，测试失败则回到 Coder。"
        answer = "循环 / 迭代执行：流程可以从后续节点回到前面的节点重复执行，而不是只能单向走到结束。"
        self.assertEqual(unsupported_sentences(answer, evidence), [])

    def test_invented_number_is_unsupported(self):
        evidence = "UDP 无连接，头部长度为 8 字节。TCP 头部至少 20 字节。"
        answer = "UDP 头部为 99 字节，不保证可靠性。"
        bad = unsupported_sentences(answer, evidence)
        self.assertTrue(any("99" in s for s in bad))
        self.assertEqual(novel_numbers("UDP 头部为 99 字节", evidence), ["99"])

    def test_lead_in_and_meta_are_not_claims(self):
        evidence = "Depends 表示依赖注入。"
        answer = "根据资料，能确定的部分如下：\n参考资料没有明确说明它的具体用途。\nDepends 对应依赖注入。"
        self.assertEqual(unsupported_sentences(answer, evidence), [])

    def test_bilingual_gloss_and_table_header_are_supported(self):
        evidence = (
            "|  | Prompt Engineering | Context Engineering |\n"
            "| 核心 | 怎么告诉模型 | 给模型什么信息 |\n"
            "Debate 会增加 Token Cost、Latency。"
        )
        answer = (
            "| 维度 | Prompt Engineering | Context Engineering |\n"
            "会增加 Token Cost（令牌成本）"
        )
        self.assertEqual(unsupported_sentences(answer, evidence), [])

    def test_identifier_paraphrase_is_supported(self):
        evidence = "nil map 不能直接写，会 panic，要先 make。数组和切片见上文。"
        answer = "对 nil map 写入会触发 panic。nil map 读取是安全的，但写入必须先用 make 初始化 map。"
        self.assertEqual(unsupported_sentences(answer, evidence), [])

    def test_single_name_plus_invented_fact_is_unsupported(self):
        evidence = "Docker 用 Dockerfile 构建镜像。"
        answer = "Docker 是谷歌在西雅图的内部实验室发明的容器产品。"
        self.assertTrue(unsupported_sentences(answer, evidence))

    def test_off_topic_sentence_is_unsupported(self):
        evidence = "Git 是版本控制工具，用来记录每一次修改。"
        answer = "今天下午深圳会下一场很大的雷阵雨，出门记得带伞。"
        self.assertTrue(unsupported_sentences(answer, evidence))

    def test_keyword_folding(self):
        self.assertTrue(term_covered("SFT 更像一个黑匣子", "黑盒"))
        self.assertTrue(term_covered("读到了还没有提交的数据", "未提交"))
        self.assertTrue(term_covered("Base64 不算加密", "不是加密"))
        self.assertTrue(term_covered("读和写使用不同的模型", "不同模型"))
        self.assertTrue(term_covered("所以，三处并不是同一套数字", "不一样"))
        self.assertFalse(term_covered("只谈到了镜像", "容器"))

    def test_comparison_wrapup_is_supported(self):
        evidence = (
            "sandbox 举例 CPU <= 2 cores、Memory <= 2 GB。"
            "agent-harness 举例 CPU：2 cores、Memory：4GB。"
            "agent_security 举例最大 CPU：1 core、最大内存：512 MB。"
        )
        answer = (
            "不是同一套数字。\n"
            "sandbox 举例 2 cores / 2 GB，agent-harness 举例 2 cores / 4GB，"
            "agent_security 举例 1 core / 512 MB。\n"
            "所以，三处并不是同一套数字。"
        )
        self.assertTrue(term_covered(answer, "不一样"))
        self.assertEqual(unsupported_sentences(answer, evidence), [])


if __name__ == "__main__":
    unittest.main()
