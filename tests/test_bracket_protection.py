import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.assembler import split_top_level_tags


class TestBracketProtection(unittest.TestCase):
    def test_basic_comma_splitting(self):
        text = "masterpiece, best quality, 1girl, smiling"
        tags = split_top_level_tags(text)
        self.assertEqual(tags, ["masterpiece", "best quality", "1girl", "smiling"])

    def test_parentheses_weight_protection(self):
        text = "(masterpiece:1.2), (high quality, highly detailed:1.1), 1girl, (solo:1.3)"
        tags = split_top_level_tags(text)
        self.assertEqual(
            tags,
            [
                "(masterpiece:1.2)",
                "(high quality, highly detailed:1.1)",
                "1girl",
                "(solo:1.3)",
            ],
        )

    def test_bracket_mixing_protection(self):
        text = "[blouse:sweater:10], [red hair, blue eyes:green eyes:5], smiling"
        tags = split_top_level_tags(text)
        self.assertEqual(
            tags,
            [
                "[blouse:sweater:10]",
                "[red hair, blue eyes:green eyes:5]",
                "smiling",
            ],
        )

    def test_lora_tag_protection(self):
        text = "<lora:asian_beauty_v1:0.8>, <lora:face_detailer, v2:0.5>, 1girl"
        tags = split_top_level_tags(text)
        self.assertEqual(
            tags,
            [
                "<lora:asian_beauty_v1:0.8>",
                "<lora:face_detailer, v2:0.5>",
                "1girl",
            ],
        )


if __name__ == "__main__":
    unittest.main()
