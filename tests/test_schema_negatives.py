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

import scripts.validate_data as val_module
from scripts.validate_data import DATA_DIR, SCHEMAS_DIR, validate_all


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

    def test_strict_mode_fails_fast_when_jsonschema_missing(self):
        """测试在严格模式下若缺少 jsonschema 库必须直接报错退出，禁止静默回退"""
        orig = val_module.HAS_JSONSCHEMA
        try:
            val_module.HAS_JSONSCHEMA = False
            res = validate_all(strict_jsonschema=True)
            self.assertFalse(res.is_valid)
            self.assertTrue(any("jsonschema>=4.23,<5.0" in err for err in res.errors))
        finally:
            val_module.HAS_JSONSCHEMA = orig

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
        self.assertTrue(any("emotion_gaze_affinity" in m for m in msgs))

    def test_alias_forward_and_backward_collision_fails(self):
        """
        测试 Alias 全局两阶段防冲突：
        - 前向冲突：Scene 0 的 alias 撞了 Scene 1 的 ID
        - 后向冲突：Scene 1 的 alias 撞了 Scene 0 的 ID
        """
        # 前向冲突
        def mutate_forward(data):
            target_id = data["scenes"][0]["items"][1]["id"]
            data["scenes"][0]["items"][0].setdefault("aliases", []).append(target_id)

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate_forward)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("collides with" in m for m in msgs))

        # 后向冲突
        def mutate_backward(data):
            target_id = data["scenes"][0]["items"][0]["id"]
            data["scenes"][0]["items"][1].setdefault("aliases", []).append(target_id)

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate_backward)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("collides with" in m for m in msgs))

    def test_alias_case_insensitive_collision_fails(self):
        """测试 Alias 忽略大小写碰撞 (strip + casefold)"""
        def mutate(data):
            target_id = data["scenes"][0]["items"][0]["id"]
            data["scenes"][0]["items"][1].setdefault("aliases", []).append(target_id.upper())

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("collides with" in m for m in msgs))

    def test_duplicate_alias_within_same_item_fails(self):
        """测试单个条目内部配置重复 alias 时被严格拦截"""
        def mutate(data):
            data["scenes"][0]["items"][0]["aliases"] = ["same_alias", "same_alias"]

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("duplicate alias" in m or "uniqueItems" in m for m in msgs))

    def test_duplicate_scene_label_fails(self):
        """测试跨条目重复 label 升级为 ERROR 拦截"""
        def mutate(data):
            first_label = data["scenes"][0]["items"][0]["label"]
            data["scenes"][0]["items"][1]["label"] = first_label

        err_count, msgs = self._run_with_mutated_file("scenes.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("label" in m and "collides with" in m for m in msgs))

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
        self.assertTrue(any("collides with" in m or "Duplicate" in m for m in msgs))

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

    def test_mutation_delete_rule9_catalog_busy_pose_triggers_fails(self):
        """负向测试: 删除 Rule 9 的 catalog_busy_pose_triggers 必须被严格拦截"""
        def mutate(data):
            for r in data["rules"]:
                if r["id"] == "pose_hand_occupation":
                    del r["catalog_busy_pose_triggers"]

        err_count, msgs = self._run_with_mutated_file("conflict_rules.json", mutate)
        self.assertGreater(err_count, 0)
        self.assertTrue(any("catalog_busy_pose_triggers" in m for m in msgs))


if __name__ == "__main__":
    unittest.main()
