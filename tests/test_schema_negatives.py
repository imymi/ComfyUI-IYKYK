import copy
import json
import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from scripts.validate_data import validate_all, DATA_DIR


class TestSchemaNegatives(unittest.TestCase):
    def test_clean_data_passes_zero_errors(self):
        err_count, _, _ = validate_all()
        self.assertEqual(err_count, 0, "Clean dataset must pass with 0 errors")

    def test_invalid_context_id_fails(self):
        scenes_file = DATA_DIR / "scenes.json"
        original = scenes_file.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            data["scenes"][0]["items"][0]["context_ids"] = ["invalid_super_context"]
            scenes_file.write_text(json.dumps(data), encoding="utf-8")

            err_count, _, msgs = validate_all()
            self.assertGreater(err_count, 0)
            self.assertTrue(any("invalid context_id" in m for m in msgs))
        finally:
            scenes_file.write_text(original, encoding="utf-8")

    def test_duplicate_scene_id_fails(self):
        scenes_file = DATA_DIR / "scenes.json"
        original = scenes_file.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            first_id = data["scenes"][0]["items"][0]["id"]
            data["scenes"][0]["items"][1]["id"] = first_id
            scenes_file.write_text(json.dumps(data), encoding="utf-8")

            err_count, _, msgs = validate_all()
            self.assertGreater(err_count, 0)
            self.assertTrue(any("Duplicate scene id" in m for m in msgs))
        finally:
            scenes_file.write_text(original, encoding="utf-8")

    def test_empty_anchor_fails(self):
        scenes_file = DATA_DIR / "scenes.json"
        original = scenes_file.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            data["scenes"][0]["items"][0]["anchor_tags"] = []
            scenes_file.write_text(json.dumps(data), encoding="utf-8")

            err_count, _, msgs = validate_all()
            self.assertGreater(err_count, 0)
            self.assertTrue(any("anchor_tags must have at least 1 item" in m for m in msgs))
        finally:
            scenes_file.write_text(original, encoding="utf-8")

    def test_anchor_detail_overlap_fails(self):
        scenes_file = DATA_DIR / "scenes.json"
        original = scenes_file.read_text(encoding="utf-8")
        try:
            data = json.loads(original)
            anchor = data["scenes"][0]["items"][0]["anchor_tags"][0]
            data["scenes"][0]["items"][0]["detail_tags"].append(anchor)
            scenes_file.write_text(json.dumps(data), encoding="utf-8")

            err_count, _, msgs = validate_all()
            self.assertGreater(err_count, 0)
            self.assertTrue(any("overlapping tags between anchors and details" in m for m in msgs))
        finally:
            scenes_file.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
