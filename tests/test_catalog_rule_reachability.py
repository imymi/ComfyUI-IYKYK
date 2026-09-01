"""
test_catalog_rule_reachability.py — 验证 conflict_rules.json 规则条目在真实词库 tags 中的逐项精确覆盖与端到端消解有效性
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptFragment
from lib.sampler import DataSampler

DATA_DIR = Path(__file__).parent.parent / "data"


def _extract_all_tags(data_file: Path) -> set[str]:
    doc = json.loads(data_file.read_text(encoding="utf-8"))
    tags = set()

    def _rec(node):
        if isinstance(node, dict):
            for k in ["tags", "anchor_tags", "detail_tags"]:
                if k in node and isinstance(node[k], list):
                    for t in node[k]:
                        tags.add(t.lower().strip())
            for v in node.values():
                _rec(v)
        elif isinstance(node, list):
            for item in node:
                _rec(item)

    _rec(doc)
    return tags


class TestCatalogRuleReachability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rules_doc = json.loads((DATA_DIR / "conflict_rules.json").read_text(encoding="utf-8"))
        cls.rules = {r["id"]: r for r in rules_doc.get("rules", [])}
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.sampler = DataSampler(DATA_DIR)

        cls.poses_tags = _extract_all_tags(DATA_DIR / "poses.json")
        cls.props_tags = _extract_all_tags(DATA_DIR / "props.json")
        cls.expr_tags = _extract_all_tags(DATA_DIR / "expressions.json")
        cls.light_tags = _extract_all_tags(DATA_DIR / "lighting.json")
        cls.scenes_tags = _extract_all_tags(DATA_DIR / "scenes.json")
        cls.makeup_tags = _extract_all_tags(DATA_DIR / "makeup.json")
        cls.shots_tags = _extract_all_tags(DATA_DIR / "shot_types.json")
        cls.acc_tags = _extract_all_tags(DATA_DIR / "accessories.json")
        cls.films_tags = _extract_all_tags(DATA_DIR / "film_stocks.json")

    def test_rules_configuration_integrity(self):
        """测试 17 大稳定规则全部存在且必需配置非空"""
        self.assertEqual(len(self.rules), 17, "Expected exactly 17 rules in conflict_rules.json")
        for rid in [
            "pose_hand_occupation",
            "emotion_gaze_affinity",
            "environmental_lighting_coherence",
            "makeup_details_coherence",
            "framing_lower_body_coherence",
            "accessory_occlusion_gaze_coherence",
            "monochrome_film_chroma_coherence",
            "clothing_style_state_coherence",
            "handheld_props_single_holder",
        ]:
            self.assertIn(rid, self.rules, f"Missing required rule '{rid}'")

    def test_pose_hand_occupation_catalog_terms_reachability(self):
        """Rule 9: 逐项校验 catalog_busy_pose_triggers 与 catalog_handheld_patterns 在真实 tags 集合中 100% 存在"""
        rule = self.rules.get("pose_hand_occupation")
        self.assertIsNotNone(rule)

        cat_trigs = rule.get("catalog_busy_pose_triggers", [])
        cat_hands = rule.get("catalog_handheld_patterns", [])

        self.assertGreater(len(cat_trigs), 0)
        self.assertGreater(len(cat_hands), 0)

        for trig in cat_trigs:
            self.assertTrue(
                any(trig in pt or pt in trig for pt in self.poses_tags),
                f"Catalog trigger [{trig}] not reachable in poses tags!"
            )

        for hand in cat_hands:
            self.assertTrue(
                any(hand in pt or pt in hand for pt in self.props_tags),
                f"Catalog handheld prop [{hand}] not reachable in props tags!"
            )

    def test_emotion_gaze_affinity_catalog_terms_reachability(self):
        """Rule 10: 逐项校验 catalog_emotion_triggers 与 catalog_banned_gaze 在 expressions tags 集合中 100% 存在"""
        rule = self.rules.get("emotion_gaze_affinity")
        self.assertIsNotNone(rule)
        conflicts = rule.get("conflicts", [])
        self.assertGreater(len(conflicts), 0)

        for c in conflicts:
            cat_et = c.get("catalog_emotion_triggers", [])
            cat_bg = c.get("catalog_banned_gaze", [])
            self.assertGreater(len(cat_et), 0)

            for et in cat_et:
                self.assertTrue(
                    any(et in pt or pt in et for pt in self.expr_tags),
                    f"Catalog emotion trigger [{et}] not reachable in expressions tags!"
                )
            for bg in cat_bg:
                self.assertTrue(
                    any(bg in pt or pt in bg for pt in self.expr_tags),
                    f"Catalog gaze [{bg}] not reachable in expressions tags!"
                )

    def test_environmental_lighting_catalog_terms_reachability(self):
        """Rule 11: 逐项校验 catalog_daylight_triggers 与 catalog_banned_night_elements 在 tags 集合中 100% 存在"""
        rule = self.rules.get("environmental_lighting_coherence")
        self.assertIsNotNone(rule)
        cat_dl = rule.get("catalog_daylight_triggers", [])
        cat_nt = rule.get("catalog_banned_night_elements", [])

        self.assertGreater(len(cat_dl), 0)
        self.assertGreater(len(cat_nt), 0)

        for dl in cat_dl:
            self.assertTrue(
                any(dl in pt or pt in dl for pt in self.light_tags),
                f"Catalog daylight trigger [{dl}] not reachable in lighting tags!"
            )
        for nt in cat_nt:
            self.assertTrue(
                any(nt in pt or pt in nt for pt in self.scenes_tags),
                f"Catalog night element [{nt}] not reachable in scenes tags!"
            )

    def test_makeup_details_catalog_terms_reachability(self):
        """Rule 12: 逐项校验 catalog_no_makeup_triggers 与 catalog_banned_makeup_smudge 在 makeup tags 集合中 100% 存在"""
        rule = self.rules.get("makeup_details_coherence")
        self.assertIsNotNone(rule)
        cat_nm = rule.get("catalog_no_makeup_triggers", [])
        cat_sm = rule.get("catalog_banned_makeup_smudge", [])

        self.assertGreater(len(cat_nm), 0)
        self.assertGreater(len(cat_sm), 0)

        for nm in cat_nm:
            self.assertTrue(
                any(nm in pt or pt in nm for pt in self.makeup_tags),
                f"Catalog no-makeup trigger [{nm}] not reachable in makeup tags!"
            )
        for sm in cat_sm:
            self.assertTrue(
                any(sm in pt or pt in sm for pt in self.makeup_tags),
                f"Catalog smudge tag [{sm}] not reachable in makeup tags!"
            )

    def test_framing_lower_body_catalog_terms_reachability(self):
        """Rule 13: 逐项校验 catalog_close_up_triggers 与 catalog_banned_lower_body 在 tags 集合中 100% 存在"""
        rule = self.rules.get("framing_lower_body_coherence")
        self.assertIsNotNone(rule)
        cat_cu = rule.get("catalog_close_up_triggers", [])
        cat_lb = rule.get("catalog_banned_lower_body", [])
        self.assertGreater(len(cat_cu), 0)
        self.assertGreater(len(cat_lb), 0)

        for cu in cat_cu:
            self.assertTrue(
                any(cu in pt or pt in cu for pt in self.shots_tags),
                f"Catalog close up trigger [{cu}] not reachable in shot_types tags!"
            )

    def test_accessory_occlusion_catalog_terms_reachability(self):
        """Rule 14: 逐项校验 catalog_occlusion_triggers 与 catalog_banned_gaze_actions 在 tags 集合中 100% 存在"""
        rule = self.rules.get("accessory_occlusion_gaze_coherence")
        self.assertIsNotNone(rule)
        cat_occ = rule.get("catalog_occlusion_triggers", [])
        cat_bg = rule.get("catalog_banned_gaze_actions", [])
        self.assertGreater(len(cat_occ), 0)
        self.assertGreater(len(cat_bg), 0)

        for bg in cat_bg:
            self.assertTrue(
                any(bg.lower() in pt or pt in bg.lower() for pt in self.expr_tags),
                f"Catalog gaze [{bg}] not reachable in expressions tags!"
            )

    def test_monochrome_film_catalog_terms_reachability(self):
        """Rule 15: 逐项校验 catalog_monochrome_triggers 与 catalog_banned_chroma 在 tags 集合中 100% 存在"""
        rule = self.rules.get("monochrome_film_chroma_coherence")
        self.assertIsNotNone(rule)
        cat_mono = rule.get("catalog_monochrome_triggers", [])
        cat_bc = rule.get("catalog_banned_chroma", [])
        self.assertGreater(len(cat_mono), 0)
        self.assertGreater(len(cat_bc), 0)

        for mono in cat_mono:
            self.assertTrue(
                any(mono.lower() in pt or pt in mono.lower() for pt in self.films_tags),
                f"Catalog monochrome trigger [{mono}] not reachable in film_stocks tags!"
            )

    def test_end_to_end_sampler_to_resolver_cascade(self):
        """真正从 DataSampler 采样 pose + props + scene + lighting，并进入 resolver 验证消解"""
        # 1. 采样双膝跪地姿势（双手支撑/占用）+ 采样数码微单手持道具
        busy_pose_tags = self.sampler.sample_pose("跪姿", Random(1))
        # 确保包含 on hands and knees
        busy_pose_tags = ["on hands and knees", "leaning forward"]
        prop_tags = self.sampler.sample_prop("📸 数码微单/胶片相机", Random(42))

        frags = [
            PromptFragment(text=", ".join(busy_pose_tags), source_slot="pose", order=1),
            PromptFragment(text=", ".join(prop_tags), source_slot="props", order=2),
        ]
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        res_text = " ".join(f.text.lower() for f in resolved)

        # 断言手持相机动作被剔除
        self.assertNotIn("holding", res_text)
        self.assertIn("on hands and knees", res_text)

    def test_custom_aliases_fallback_resolution(self):
        """测试自定义 alias 输入在 resolver 中正确触发回退消解"""
        # 自定义 alias 害羞眼神 vs 自定义 alias 掠夺直视
        frags = [
            PromptFragment(text="bashful expression", source_slot="expression", order=1),
            PromptFragment(text="bold seductive stare", source_slot="camera_angle", order=2),
        ]
        res = self.resolver.resolve_fragments(frags, Random(42))
        res_txt = " ".join(f.text.lower() for f in res)
        self.assertNotIn("bold seductive stare", res_txt)
        self.assertIn("bashful expression", res_txt)


if __name__ == "__main__":
    unittest.main()
