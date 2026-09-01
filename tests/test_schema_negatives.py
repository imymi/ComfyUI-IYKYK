"""
test_schema_negatives.py — JSON Schema 负向拦截测试（安全隔离于 TemporaryDirectory）
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

from scripts.validate_data import validate_all, DATA_DIR, SCHEMAS_DIR


def _hash_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestSchemaNegatives(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real_scenes_hash = _hash_file(DATA_DIR / "scenes.json")
        cls.real_clothing_hash = _hash_file(DATA_DIR / "clothing.json")
        cls.real_props_hash = _hash_file(DATA_DIR / "props.json")
        cls.real_rules_hash = _hash_file(DATA_DIR / "conflict_rules.json")

    def tearDown(self):
        # 严格断言测试过程中真实数据文件绝未受到任何篡改
        self.assertEqual(self.real_scenes_hash, _hash_file(DATA_DIR / "scenes.json"), "Real scenes.json modified!")
        self.assertEqual(self.real_clothing_hash, _hash_file(DATA_DIR / "clothing.json"), "Real clothing.json modified!")
        self.assertEqual(self.real_props_hash, _hash_file(DATA_DIR / "props.json"), "Real props.json modified!")
        self.assertEqual(self.real_rules_hash, _hash_file(DATA_DIR / "conflict_rules.json"), "Real conflict_rules.json modified!")

    def _run_with_mutated_file(self, filename: str, mutate_fn) -> tuple[int, list[str]]:
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_data = Path(tmp_dir_str) / "data"
            shutil.copytree(DATA_DIR, tmp_data)

            target_file = tmp_data / filename
            data = json.loads(target_file.read_text(encoding="utf-8"))
            mutate_fn(data)
            target_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            res = validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
            return len(res.errors), res.errors

    def test_clean_data_passes_zero_errors(self):
        res = validate_all(strict_jsonschema=True)
        self.assertEqual(len(res.errors), 0, "Clean dataset must pass with 0 errors")

    def test_empty_schemas_directory_fails_in_strict_mode(self):
        """测试在严格模式下空 schemas 目录必须被立即拦截报错"""
        with tempfile.TemporaryDirectory() as empty_schemas:
            res = validate_all(data_dir=DATA_DIR, schemas_dir=Path(empty_schemas), strict_jsonschema=True)
            self.assertGreater(len(res.errors), 0)
            self.assertTrue(any("Missing required schema file" in m for m in res.errors))

    def test_scene_id_pattern_violation_fails(self):
        """测试场景 ID 违反 pattern 正则时被 Draft-7 正则校验拦截"""
        def mutate(data):
            data["scenes"][0]["items"][0]["id"] = "INVALID ID !"

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("pattern" in m or "INVALID ID !" in m for m in msgs))

    def test_mutation_delete_rule10_conflicts_fails(self):
        """测试删除 Rule 10 的必需字段 conflicts 时被拦截"""
        def mutate(data):
            for r in data["rules"]:
                if r["id"] == "emotion_gaze_affinity":
                    del r["conflicts"]

        err_count, msgs = self._run_with_mutated_file("conflict_rules.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("emotion_gaze_affinity" in m and "conflicts" in m for m in msgs))

    def test_mutation_replace_required_rule_with_typo_id_fails(self):
        """测试必需规则 ID 被未知 ID 替换时被拦截"""
        def mutate(data):
            for r in data["rules"]:
                if r["id"] == "emotion_gaze_affinity":
                    r["id"] = "typo_rule_id"

        err_count, msgs = self._run_with_mutated_file("conflict_rules.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("Missing required rule 'emotion_gaze_affinity'" in m for m in msgs))

    def test_invalid_context_id_fails(self):
        def mutate(data):
            data["scenes"][0]["items"][0]["context_ids"] = ["invalid_super_context"]

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("invalid context_id" in m for m in msgs))

    def test_duplicate_scene_id_fails(self):
        def mutate(data):
            first_id = data["scenes"][0]["items"][0]["id"]
            data["scenes"][0]["items"][1]["id"] = first_id

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("Duplicate scene id" in m for m in msgs))

    def test_empty_anchor_fails(self):
        def mutate(data):
            data["scenes"][0]["items"][0]["anchor_tags"] = []

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("anchor_tags must have at least 1 item" in m or "minItems" in m for m in msgs))

    def test_anchor_detail_overlap_fails(self):
        def mutate(data):
            anchor = data["scenes"][0]["items"][0]["anchor_tags"][0]
            data["scenes"][0]["items"][0]["detail_tags"].append(anchor)

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("overlapping tags between anchors and details" in m for m in msgs))

    def test_mutation_delete_rule9_busy_pose_triggers_fails(self):
        """负向测试: 删除 Rule 9 的 busy_pose_triggers 必须被严格拦截"""
        def mutate(data):
            for r in data["rules"]:
                if r["id"] == "pose_hand_occupation":
                    del r["busy_pose_triggers"]

        err_count, msgs = self._run_with_mutated_file("conflict_rules.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("busy_pose_triggers" in m for m in msgs))

    def test_mutation_duplicate_clothing_category_id_fails(self):
        """负向测试: 制造重复服装 category ID 必须被严格拦截"""
        def mutate(data):
            data["categories"][1]["id"] = data["categories"][0]["id"]

        err_count, msgs = self._run_with_mutated_file("clothing.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("Duplicate clothing category id" in m for m in msgs))

    def test_mutation_empty_clothing_linkage_l2_fails(self):
        """负向测试: 将 clothing_nudity_linkage.L2 置为空对象必须被严格拦截"""
        def mutate(data):
            data["clothing_nudity_linkage"]["L2"] = {}

        err_count, msgs = self._run_with_mutated_file("clothing.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("L2" in m or "style_overrides" in m for m in msgs))

    def test_mutation_duplicate_extension_tier_id_fails(self):
        """负向测试: 重复追加 extension tier ID 必须被严格拦截"""
        def mutate(data):
            data["sfw_exposure_tiers"][1]["id"] = data["sfw_exposure_tiers"][0]["id"]

        err_count, msgs = self._run_with_mutated_file("clothing.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("Duplicate sfw_exposure_tier id" in m for m in msgs))

    def test_empty_prop_tags_and_items_fails(self):
        def mutate(data):
            data["categories"].append({"id": "broken_prop", "name_zh": "损坏道具"})

        err_count, msgs = self._run_with_mutated_file("props.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("Must have non-empty 'tags' or 'items'" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
