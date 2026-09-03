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
from lib.rule_contract import PatternSpec
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


def _check_pattern_reachability(item: dict, tag_set: set[str], desc: str) -> None:
    pat = item["pattern"]
    mode = item.get("match_mode", "phrase")
    spec = PatternSpec(pattern=pat, match_mode=mode)
    matched = any(spec.matches(t) or t == pat.lower() for t in tag_set)
    assert matched, f"Catalog item [{pat}] ({mode}) not reachable in {desc} tags!"


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
        cls.clothing_tags = _extract_all_tags(DATA_DIR / "clothing.json")
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
        """Rule 9: 逐项校验 catalog_busy_pose_triggers 与 catalog_handheld_patterns 精确匹配真实 tags 集合"""
        rule = self.rules.get("pose_hand_occupation")
        self.assertIsNotNone(rule)

        cat_trigs = rule.get("catalog_busy_pose_triggers", [])
        cat_hands = rule.get("catalog_handheld_patterns", [])

        self.assertGreater(len(cat_trigs), 0)
        self.assertGreater(len(cat_hands), 0)

        for trig in cat_trigs:
            _check_pattern_reachability(trig, self.poses_tags, "poses")

        for hand in cat_hands:
            _check_pattern_reachability(hand, self.props_tags, "props")

    def test_emotion_gaze_affinity_catalog_terms_reachability(self):
        """Rule 10: 逐项校验 catalog_emotion_triggers 与 catalog_banned_gaze 在 expressions tags 集合中精确匹配"""
        rule = self.rules.get("emotion_gaze_affinity")
        self.assertIsNotNone(rule)
        conflicts = rule.get("conflicts", [])
        self.assertGreater(len(conflicts), 0)

        for c in conflicts:
            cat_emotions = c.get("catalog_emotion_triggers", [])
            cat_gazes = c.get("catalog_banned_gaze", [])
            for emo in cat_emotions:
                _check_pattern_reachability(emo, self.expr_tags, "expressions")
            for gaze in cat_gazes:
                _check_pattern_reachability(gaze, self.expr_tags, "expressions (gaze)")

    def test_environmental_lighting_catalog_terms_reachability(self):
        """Rule 11: 逐项校验 catalog_daylight_triggers (lighting) 与 catalog_banned_night_elements (scenes) 精确匹配"""
        rule = self.rules.get("environmental_lighting_coherence")
        self.assertIsNotNone(rule)

        day_trigs = rule.get("catalog_daylight_triggers", [])
        night_elems = rule.get("catalog_banned_night_elements", [])

        self.assertGreater(len(day_trigs), 0)
        self.assertGreater(len(night_elems), 0)

        for dt in day_trigs:
            _check_pattern_reachability(dt, self.light_tags, "lighting")

        for ne in night_elems:
            _check_pattern_reachability(ne, self.scenes_tags, "scenes")

    def test_makeup_details_catalog_terms_reachability(self):
        """Rule 12: 逐项校验 catalog_no_makeup_triggers 与 catalog_banned_makeup_smudge 在 makeup tags 中精确匹配"""
        rule = self.rules.get("makeup_details_coherence")
        self.assertIsNotNone(rule)

        no_makeup = rule.get("catalog_no_makeup_triggers", [])
        smudge = rule.get("catalog_banned_makeup_smudge", [])

        self.assertGreater(len(no_makeup), 0)
        self.assertGreater(len(smudge), 0)

        for nm in no_makeup:
            _check_pattern_reachability(nm, self.makeup_tags, "makeup")

        for sm in smudge:
            _check_pattern_reachability(sm, self.makeup_tags, "makeup (smudge)")

    def test_framing_lower_body_catalog_terms_reachability(self):
        """Rule 13: 逐项校验 catalog_close_up_triggers (shot_types) 与 catalog_banned_lower_body (clothing) 精确匹配"""
        rule = self.rules.get("framing_lower_body_coherence")
        self.assertIsNotNone(rule)

        close_ups = rule.get("catalog_close_up_triggers", [])
        lower_body = rule.get("catalog_banned_lower_body", [])

        self.assertGreater(len(close_ups), 0)
        self.assertGreater(len(lower_body), 0)

        for cu in close_ups:
            _check_pattern_reachability(cu, self.shots_tags, "shot_types")

        for lb in lower_body:
            _check_pattern_reachability(lb, self.clothing_tags, "clothing")

    def test_accessory_occlusion_catalog_terms_reachability(self):
        """Rule 14: 逐项校验 catalog_occlusion_triggers (accessories) 与 catalog_banned_gaze_actions (expressions) 精确匹配"""
        rule = self.rules.get("accessory_occlusion_gaze_coherence")
        self.assertIsNotNone(rule)

        occl = rule.get("catalog_occlusion_triggers", [])
        gaze = rule.get("catalog_banned_gaze_actions", [])

        self.assertGreater(len(occl), 0)
        self.assertGreater(len(gaze), 0)

        for o in occl:
            _check_pattern_reachability(o, self.acc_tags, "accessories")

        for g in gaze:
            _check_pattern_reachability(g, self.expr_tags, "expressions")

    def test_monochrome_film_chroma_catalog_terms_reachability(self):
        """Rule 15: 逐项校验 catalog_monochrome_triggers (film_stocks) 与 catalog_banned_chroma (lighting) 精确匹配"""
        rule = self.rules.get("monochrome_film_chroma_coherence")
        self.assertIsNotNone(rule)

        monos = rule.get("catalog_monochrome_triggers", [])
        chromas = rule.get("catalog_banned_chroma", [])

        self.assertGreater(len(monos), 0)
        self.assertGreater(len(chromas), 0)

        for m in monos:
            _check_pattern_reachability(m, self.films_tags, "film_stocks")

        for c in chromas:
            _check_pattern_reachability(c, self.light_tags, "lighting")

    def test_end_to_end_sampler_to_resolver_cascade(self):
        """真正从 DataSampler 采样 pose + props，进入 resolver 验证消解 (无硬编码覆盖)"""
        # 1. 采样四肢着地姿势（双手双膝着地/占用）+ 采样数码微单手持道具
        busy_pose_tags = self.sampler.sample_pose("🐾 四肢着地", Random(42))
        prop_tags = self.sampler.sample_prop("📸 黑色数码微单/记录互动", Random(42))

        self.assertIn("on all fours", busy_pose_tags)
        self.assertTrue(any("camera" in p.lower() for p in prop_tags))

        frags = [
            PromptFragment(text=", ".join(busy_pose_tags), source_slot="pose", order=1),
            PromptFragment(text=", ".join(prop_tags), source_slot="props", order=2),
        ]
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        res_text = " ".join(f.text.lower() for f in resolved)

        # 断言手持相机动作被剔除，四肢着地姿势保留
        self.assertNotIn("holding", res_text)
        self.assertIn("on all fours", res_text)

    def test_custom_aliases_fallback_resolution(self):
        """测试自定义 alias 输入在 resolver 中正确触发消解"""
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
