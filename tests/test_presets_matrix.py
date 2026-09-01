import json
import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKPresetBrowser

DATA_DIR = REPO_DIR / "data"


class TestPresetsMatrix(unittest.TestCase):
    def setUp(self):
        self.browser = IYKYKPresetBrowser()
        presets_data = json.loads((DATA_DIR / "presets.json").read_text(encoding="utf-8"))
        self.presets = presets_data.get("presets", [])
        recipes_data = json.loads((DATA_DIR / "style_recipes.json").read_text(encoding="utf-8"))
        self.recipes = [r.get("style_name") for r in recipes_data.get("recipes", [])]

    def test_full_693_presets_recipes_matrix(self):
        """Test all 77 presets × (8 style recipes + 1 None) = 693 total combinations."""
        total_tested = 0
        all_recipe_options = ["无 (None)"] + self.recipes

        for p in self.presets:
            p_name = f"{p.get('id')} {p.get('name_zh')}"
            for r_name in all_recipe_options:
                pos, neg, desc = self.browser.browse(
                    prompt_seed=42,
                    预设模板=p_name,
                    风格配方=r_name,
                    画质等级="高清写真 (High)",
                )
                total_tested += 1
                self.assertTrue(pos, f"Empty prompt for preset {p_name} + recipe {r_name}")
                self.assertTrue(neg, f"Empty negative for preset {p_name} + recipe {r_name}")
                self.assertTrue(desc, f"Empty desc for preset {p_name} + recipe {r_name}")
                word_count = len(pos.split())
                self.assertLessEqual(
                    word_count,
                    250,
                    f"Preset {p_name} + Recipe {r_name} exceeded 250 words ({word_count} words)",
                )

        self.assertEqual(total_tested, 77 * 9, f"Expected 693 tests, got {total_tested}")


if __name__ == "__main__":
    unittest.main()
