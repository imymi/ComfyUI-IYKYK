import json
import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from nodes import IYKYKPresetBrowser, IYKYKPromptGenerator, IYKYKPromptDiagnostics
from tests.audit_oracle import validate_audit_json_oracle

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

DATA_DIR = REPO_DIR / "data"
SCHEMA_PATH = REPO_DIR / "schemas" / "diagnostics.schema.json"


class TestPresetsMatrix(unittest.TestCase):
    def setUp(self):
        self.browser = IYKYKPresetBrowser()
        self.generator = IYKYKPromptGenerator()
        self.diagnostics = IYKYKPromptDiagnostics()
        presets_data = json.loads((DATA_DIR / "presets.json").read_text(encoding="utf-8"))
        self.presets = presets_data.get("presets", [])
        recipes_data = json.loads((DATA_DIR / "style_recipes.json").read_text(encoding="utf-8"))
        self.recipes = [r.get("style_name") for r in recipes_data.get("recipes", [])]
        self.schema_doc = json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) if SCHEMA_PATH.exists() else None

    def test_full_693_presets_recipes_matrix(self):
        """Test all 77 presets × (8 style recipes + 1 None) = 693 combinations across generator, browser, and diagnostics."""
        total_tested = 0
        all_recipe_options = ["无 (None)"] + self.recipes
        schema = self.schema_doc if HAS_JSONSCHEMA else None

        for p in self.presets:
            p_name = f"{p.get('id')} {p.get('name_zh')}"
            for r_name in all_recipe_options:
                b_pos, b_neg, b_desc = self.browser.browse(
                    prompt_seed=42,
                    预设模板=p_name,
                    风格配方=r_name,
                    画质等级="高清写真 (High)",
                )
                g_pos, g_neg, g_desc = self.generator.generate(
                    prompt_seed=42,
                    预设模板=p_name,
                    风格配方=r_name,
                    画质等级="高清写真 (High)",
                )
                d_pos, d_neg, d_desc, d_audit = self.diagnostics.diagnose(
                    prompt_seed=42,
                    预设模板=p_name,
                    风格配方=r_name,
                    画质等级="高清写真 (High)",
                )

                # 逐字节一致性
                self.assertEqual(b_pos, g_pos, f"Browser vs Generator positive mismatch for {p_name} + {r_name}")
                self.assertEqual(g_pos, d_pos, f"Generator vs Diagnostics positive mismatch for {p_name} + {r_name}")
                self.assertEqual(b_neg, g_neg, f"Browser vs Generator negative mismatch for {p_name} + {r_name}")
                self.assertEqual(g_neg, d_neg, f"Generator vs Diagnostics negative mismatch for {p_name} + {r_name}")
                self.assertEqual(b_desc, g_desc, f"Browser vs Generator desc mismatch for {p_name} + {r_name}")
                self.assertEqual(g_desc, d_desc, f"Generator vs Diagnostics desc mismatch for {p_name} + {r_name}")

                # Oracle 审计校验与闭包验证
                validate_audit_json_oracle(
                    d_audit,
                    schema_doc=schema,
                    expected_positive=d_pos,
                    trusted_inputs={
                        "预设模板": p_name,
                        "风格配方": r_name,
                        "画质等级": "高清写真 (High)",
                    },
                )

                total_tested += 1
                self.assertTrue(b_pos, f"Empty prompt for preset {p_name} + recipe {r_name}")
                word_count = len(b_pos.split())
                self.assertLessEqual(
                    word_count,
                    250,
                    f"Preset {p_name} + Recipe {r_name} exceeded 250 words ({word_count} words)",
                )

        self.assertEqual(total_tested, 77 * 9, f"Expected 693 tests, got {total_tested}")


if __name__ == "__main__":
    unittest.main()
