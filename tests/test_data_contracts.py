import json
import unittest
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

EXPECTED_JSON_FILES = [
    "accessories.json",
    "characters.json",
    "clothing.json",
    "conflict_rules.json",
    "expressions.json",
    "film_stocks.json",
    "imperfections.json",
    "lighting.json",
    "makeup.json",
    "negative_prompts.json",
    "nudity_levels.json",
    "poses.json",
    "presets.json",
    "props.json",
    "scenes.json",
    "shot_types.json",
    "style_recipes.json",
    "tattoos.json",
    "themes.json",
]


class TestDataContracts(unittest.TestCase):
    def test_all_19_json_files_exist_and_are_valid(self):
        self.assertTrue(DATA_DIR.is_dir(), f"Data directory {DATA_DIR} does not exist")
        for filename in EXPECTED_JSON_FILES:
            filepath = DATA_DIR / filename
            self.assertTrue(filepath.is_file(), f"Missing required data file: {filename}")
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                self.assertIsInstance(data, dict, f"{filename} root must be a JSON object")
            except Exception as e:
                self.fail(f"Failed to parse JSON file {filename}: {e}")

    def test_presets_structure(self):
        data = json.loads((DATA_DIR / "presets.json").read_text(encoding="utf-8"))
        presets = data.get("presets", [])
        self.assertGreaterEqual(len(presets), 77, "Should contain at least 77 presets")
        seen_ids = set()
        for p in presets:
            pid = p.get("id")
            self.assertIsNotNone(pid, f"Preset missing id: {p}")
            self.assertNotIn(pid, seen_ids, f"Duplicate preset id: {pid}")
            seen_ids.add(pid)
            self.assertTrue(p.get("name_zh"), f"Preset {pid} missing name_zh")
            self.assertTrue(p.get("positive") or p.get("prompt"), f"Preset {pid} missing positive prompt")

    def test_style_recipes_structure(self):
        data = json.loads((DATA_DIR / "style_recipes.json").read_text(encoding="utf-8"))
        recipes = data.get("recipes", [])
        self.assertGreaterEqual(len(recipes), 8, "Should contain at least 8 style recipes")
        for r in recipes:
            self.assertTrue(r.get("style_name") or r.get("name_zh"), f"Recipe missing name: {r}")


if __name__ == "__main__":
    unittest.main()
