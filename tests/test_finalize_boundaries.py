import random
import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.assembler import finalize_prompt, split_top_level_tags, PromptValidationError
from lib.models import PromptFragment

DATA_DIR = REPO_DIR / "data"


def validate_brackets_stack(s: str) -> bool:
    """使用栈结构严格校验括号嵌套闭合，防止交叉闭合（如 ([)]）。"""
    pairs = {')': '(', ']': '[', '>': '<'}
    stack = []
    in_quotes = False
    escaped = False

    for char in s:
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            continue
        if in_quotes:
            continue

        if char in "([<":
            stack.append(char)
        elif char in ")]>":
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()

    return len(stack) == 0 and not in_quotes


class TestFinalizeBoundaries(unittest.TestCase):
    def test_parser_escaped_commas_and_quotes(self):
        # 1. Backslash-escaped comma
        text = r"tag1, tag2\,with comma, tag3"
        tags = split_top_level_tags(text)
        self.assertEqual(tags, ["tag1", r"tag2\,with comma", "tag3"])

        # 2. Quotes with internal comma
        text2 = '"tag with, comma", another tag'
        tags2 = split_top_level_tags(text2)
        self.assertEqual(tags2, ['"tag with, comma"', "another tag"])

        # 3. Nested mixed brackets: ([<tag>])
        text3 = "([<lora:test:1.0>, highly detailed:1.2]), simple_tag"
        tags3 = split_top_level_tags(text3)
        self.assertEqual(tags3, ["([<lora:test:1.0>, highly detailed:1.2])", "simple_tag"])

        # 4. Trailing backslash safety
        text4 = "tag1, tag2\\"
        tags4 = split_top_level_tags(text4)
        self.assertEqual(tags4, ["tag1", "tag2\\"])

    def test_protected_syntax_byte_identical_through_finalize(self):
        """测试 5 个复审指出的关键受保护语法样例在 finalize_prompt 全链路中 100% 字节级原样保留"""
        test_cases = [
            "<lora:model  v2:0.5>",
            '"quoted  phrase, x"',
            "<lora:spinning room:1.0>",
            '"spinning room"',
            r"escaped\,  comma",
            "<lora:face_detailer,v2:0.5>",
            r"tag2\,with comma",
            "([<lora:test:1.0>, highly detailed:1.2])",
            "(solo:1.3)",
            "[blouse:sweater:10]",
        ]
        for tc in test_cases:
            res = finalize_prompt([PromptFragment(text=tc, source_slot="custom")], data_dir=DATA_DIR)
            self.assertEqual(res, tc, f"Protected syntax [{tc}] was mutated to [{res}]!")

    def test_regular_text_spinning_room_replaced(self):
        """普通非受保护文本中的 spinning room 必须被正确替换为 drunken stupor"""
        res = finalize_prompt([PromptFragment(text="in a spinning room", source_slot="custom")], data_dir=DATA_DIR)
        self.assertEqual(res, "in a drunken stupor")

    def test_word_count_boundaries(self):
        # 249 words
        words_249 = ["word"] * 249
        f249 = [PromptFragment(text=" ".join(words_249), source_slot="custom")]
        p249 = finalize_prompt(f249, data_dir=DATA_DIR)
        self.assertEqual(len(p249.split()), 249)

        # 250 words
        words_250 = ["word"] * 250
        f250 = [PromptFragment(text=" ".join(words_250), source_slot="custom")]
        p250 = finalize_prompt(f250, data_dir=DATA_DIR)
        self.assertEqual(len(p250.split()), 250)

        # 251 words -> trimmed to 250
        words_251 = ["word"] * 251
        f251 = [PromptFragment(text=" ".join(words_251), source_slot="custom")]
        p251 = finalize_prompt(f251, data_dir=DATA_DIR)
        self.assertEqual(len(p251.split()), 250)

        # 260 words -> trimmed to 250
        words_260 = ["word"] * 260
        f260 = [PromptFragment(text=" ".join(words_260), source_slot="custom")]
        p260 = finalize_prompt(f260, data_dir=DATA_DIR)
        self.assertEqual(len(p260.split()), 250)

    def test_structural_fragment_boundary_does_not_break_brackets(self):
        """249 个词 + 超预算结构化片段时原子性跳过，不能生成残缺或未闭合的括号"""
        words_249 = ["word"] * 249
        frags = [
            PromptFragment(text=" ".join(words_249), source_slot="custom"),
            PromptFragment(text="<lora:complex model with details:1.0>", source_slot="custom"),
        ]
        res = finalize_prompt(frags, data_dir=DATA_DIR, max_words=250)
        self.assertEqual(len(res.split()), 249)
        self.assertNotIn("<lora", res)
        self.assertTrue(validate_brackets_stack(res))

    def test_single_structural_fragment_over_250_raises_error(self):
        huge_struct = "<lora:" + " ".join(["word"] * 255) + ":1.0>"
        frags = [PromptFragment(text=huge_struct, source_slot="custom")]
        with self.assertRaises(PromptValidationError):
            finalize_prompt(frags, data_dir=DATA_DIR, max_words=250)

    def test_10000_random_structural_fragments_integrity(self):
        """随机生成 10,000 组包含各种嵌套结构语法的片段，断言词数永不超限且通过栈式嵌套验证"""
        rng = random.Random(42)
        templates = [
            "<lora:model_{i},v{i}:0.{i}>",
            "([<lora:test_{i}:1.0>, highly detailed_{i}:1.2])",
            "(masterpiece_{i}:{i}.1)",
            "[tag_a_{i}:tag_b_{i}:5]",
            r"escaped\,comma_{i}",
            '"quoted tag, number {i}"',
            "simple_word_{i}",
        ]

        for batch in range(1000):
            num_frags = rng.randint(10, 40)
            frags = []
            for j in range(num_frags):
                tpl = rng.choice(templates)
                frags.append(PromptFragment(text=tpl.format(i=j), source_slot="custom", order=j))

            res = finalize_prompt(frags, data_dir=DATA_DIR, max_words=250)
            words = res.split()
            self.assertLessEqual(len(words), 250)

            # 栈式括号嵌套闭合验证
            self.assertTrue(validate_brackets_stack(res), f"Bracket stack validation failed on: {res}")


if __name__ == "__main__":
    unittest.main()
