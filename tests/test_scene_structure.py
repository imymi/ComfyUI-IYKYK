import json
import random
import unittest
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.sampler import DataSampler

DATA_DIR = REPO_DIR / "data"


class TestSceneStructure(unittest.TestCase):
    def setUp(self):
        self.sampler = DataSampler(DATA_DIR)
        self.scenes_data = json.loads((DATA_DIR / "scenes.json").read_text(encoding="utf-8"))
        self.expectations = json.loads((REPO_DIR / "tests" / "fixtures" / "scene_context_expectations.json").read_text(encoding="utf-8"))

    def test_all_122_scenes_match_expectations(self):
        all_items = []
        seen_ids = set()
        seen_labels = set()

        for cat in self.scenes_data.get("scenes", []):
            for item in cat.get("items", []):
                all_items.append(item)
                sid = item.get("id")
                self.assertIsNotNone(sid)
                self.assertNotIn(sid, seen_ids, f"Duplicate scene id: {sid}")
                seen_ids.add(sid)

                label = item.get("label")
                self.assertNotIn(label, seen_labels, f"Duplicate scene label: {label}")
                seen_labels.add(label)

                # Match against expectations
                self.assertIn(sid, self.expectations, f"Scene {sid} missing from expectations")
                exp = self.expectations[sid]
                self.assertEqual(item["context_ids"][0], exp["expected_context"], f"Context mismatch for {sid}")
                self.assertEqual(item["exclusive_group"], exp["exclusive_group"], f"Exclusive group mismatch for {sid}")

                # Zero overlap between anchors and details
                anchors = set(item.get("anchor_tags", []))
                details = set(item.get("detail_tags", []))
                self.assertTrue(anchors.isdisjoint(details), f"Overlap in scene {sid}: {anchors & details}")
                self.assertGreater(len(anchors), 0, f"Empty anchors in {sid}")

        self.assertEqual(len(all_items), 122, f"Expected 122 scenes, found {len(all_items)}")

    def test_detect_context_no_substring_false_positives(self):
        # "small swimming pool" -> should detect onsen_bath or pool, NOT school from 'small'
        ctx_pool = self.sampler.detect_context("indoor swimming pool", "summer")
        self.assertIn(ctx_pool, ["onsen_bath", "outdoor", "generic"])
        self.assertNotEqual(ctx_pool, "school")

        # "cool drink" -> should NOT detect school or office
        ctx_drink = self.sampler.detect_context("street", "drinking tea")
        self.assertNotEqual(ctx_drink, "school")

    def test_5000_scene_samplings_no_none_context(self):
        rng = random.Random(42)
        for _ in range(5000):
            res = self.sampler.sample_scene_result("随机 (Random)", rng)
            self.assertIsNotNone(res)
            self.assertTrue(res.context_ids)
            self.assertNotIn(None, res.context_ids)
            self.assertGreaterEqual(len(res.tags), 1)


if __name__ == "__main__":
    unittest.main()
