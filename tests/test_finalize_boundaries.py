import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.assembler import finalize_prompt, split_top_level_tags, PromptValidationError
from lib.models import PromptFragment

DATA_DIR = REPO_DIR / "data"


class TestFinalizeBoundaries(unittest.TestCase):
    def test_parser_escaped_commas_and_quotes(self):
        # 1. Backslash-escaped comma
        text = r"tag1, tag2\, with comma, tag3"
        tags = split_top_level_tags(text)
        self.assertEqual(tags, ["tag1", r"tag2\, with comma", "tag3"])

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

    def test_word_count_boundaries(self):
        # Construct exact word count fragments
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

    def test_single_structural_fragment_over_250_raises_error(self):
        long_words = " ".join(["word"] * 255)
        long_structural = f"({long_words}:1.2)"
        f = [PromptFragment(text=long_structural, source_slot="custom")]
        with self.assertRaises(PromptValidationError):
            finalize_prompt(f, data_dir=DATA_DIR)

    def test_structural_fragment_skipping_not_broken(self):
        # 240 words of plain text + 9 words of structural bracket
        base_words = ["tag"] * 240
        f_base = PromptFragment(text=" ".join(base_words), source_slot="custom")
        f_struct = PromptFragment(text="(this structural bracket has exactly nine words in it:1.2)", source_slot="custom")
        f_short = PromptFragment(text="short tag", source_slot="custom")

        # f_struct is 9 words -> 240 + 9 = 249 <= 250 -> fits
        p_fit = finalize_prompt([f_base, f_struct, f_short], data_dir=DATA_DIR)
        self.assertLessEqual(len(p_fit.split()), 250)
        self.assertIn("(this structural bracket has exactly nine words in it:1.2)", p_fit)


if __name__ == "__main__":
    unittest.main()
