"""
test_conflict_engine_matrix.py — 17 大冲突消解规则与 15 槽位全量交叉消解矩阵测试套件
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptFragment

DATA_DIR = Path(__file__).parent.parent / "data"


class TestConflictEngineMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.rules_doc = json.loads((DATA_DIR / "conflict_rules.json").read_text(encoding="utf-8"))
        cls.rules = {r["id"]: r for r in cls.rules_doc.get("rules", [])}

    def test_all_17_rules_present_in_dataset(self):
        """断言数据集严格定义全部 17 项冲突消解规则"""
        self.assertEqual(len(self.rules), 17, "Expected exactly 17 rules in conflict_rules.json")
        expected_ids = [
            "nudity_clothing_conflicts",
            "material_penetration",
            "gaze_angle_geometry",
            "gaze_mutual_exclusion",
            "liquid_restrictions",
            "device_quality_compatibility",
            "tattoo_dermal_fusion",
            "spatial_environmental_mutual_exclusion",
            "pose_hand_occupation",
            "emotion_gaze_affinity",
            "environmental_lighting_coherence",
            "makeup_details_coherence",
            "framing_lower_body_coherence",
            "accessory_occlusion_gaze_coherence",
            "monochrome_film_chroma_coherence",
            "clothing_style_state_coherence",
            "handheld_props_single_holder",
        ]
        for rid in expected_ids:
            self.assertIn(rid, self.rules, f"Rule '{rid}' missing from conflict_rules.json")

    # ─── Rule 13: 景别特写与下肢/足部自洽 ───
    def test_rule13_close_up_purges_lower_body_accessories(self):
        """测试面部/极致特写时，自动剔除高跟鞋、大腿袜、吊袜带等下半身足部干扰词条"""
        frags = [
            PromptFragment(text="extreme close-up, focused on facial expression", source_slot="shot_type", order=1),
            PromptFragment(text="thigh-high stockings, stiletto heels, garter belt", source_slot="clothing", order=2),
            PromptFragment(text="subtle smile, soft lips", source_slot="expression", order=3),
        ]
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        res_text = " ".join(f.text.lower() for f in resolved)

        self.assertIn("extreme close-up", res_text)
        self.assertIn("subtle smile", res_text)
        self.assertNotIn("thigh-high stockings", res_text)
        self.assertNotIn("stiletto heels", res_text)
        self.assertNotIn("garter belt", res_text)

    # ─── Rule 14: 饰品遮挡与视线动作自洽 ───
    def test_rule14_blindfold_purges_direct_gaze_and_winking(self):
        """测试蒙眼布/遮眼状态下，自动剔除直视镜头与眨眼等矛盾动作"""
        frags = [
            PromptFragment(text="black lace blindfold covering eyes", source_slot="accessories", order=1),
            PromptFragment(text="direct eye contact with camera, playful wink, sparkling eyes", source_slot="expression", order=2),
            PromptFragment(text="parted lips, heavy breathing", source_slot="expression", order=3),
        ]
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        res_text = " ".join(f.text.lower() for f in resolved)

        self.assertIn("black lace blindfold covering eyes", res_text)
        self.assertIn("parted lips", res_text)
        self.assertNotIn("direct eye contact", res_text)
        self.assertNotIn("playful wink", res_text)
        self.assertNotIn("sparkling eyes", res_text)

    # ─── Rule 15: 黑白胶片与高饱和色彩互斥 ───
    def test_rule15_monochrome_film_purges_chroma_and_preserves_contrast(self):
        """测试黑白胶片下消解彩虹/霓虹色调，保留明暗反差与影调词"""
        frags = [
            PromptFragment(text="kodak tri-x 400, high contrast B&W, fine grain", source_slot="film_stock", order=1),
            PromptFragment(text="vibrant neon cyan and magenta, rainbow prism flares", source_slot="lighting", order=2),
            PromptFragment(text="dramatic rim lighting, chiaroscuro, deep shadows", source_slot="lighting", order=3),
        ]
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        res_text = " ".join(f.text.lower() for f in resolved)

        self.assertIn("kodak tri-x 400", res_text)
        self.assertIn("dramatic rim lighting", res_text)
        self.assertIn("chiaroscuro", res_text)
        self.assertNotIn("vibrant neon cyan and magenta", res_text)
        self.assertNotIn("rainbow prism flares", res_text)

    # ─── Rule 16: 服装款式与解构状态互斥 ───
    def test_rule16_swimsuit_and_pants_state_exclusion(self):
        """测试连体泳装禁止掀裙/解衬衫纽扣，牛仔裤禁止裙开衩"""
        # Case A: 连体泳衣
        frags_swim = [
            PromptFragment(text="school swimsuit (sukumizu), form-fitting", source_slot="clothing", order=1),
            PromptFragment(text="unbuttoned dress shirt, skirt lifted, button undone", source_slot="clothing", order=2),
        ]
        res_swim = self.resolver.resolve_fragments(frags_swim, Random(42))
        txt_swim = " ".join(f.text.lower() for f in res_swim)
        self.assertIn("school swimsuit (sukumizu)", txt_swim)
        self.assertNotIn("unbuttoned dress shirt", txt_swim)
        self.assertNotIn("skirt lifted", txt_swim)

        # Case B: 牛仔裤
        frags_pants = [
            PromptFragment(text="skinny jeans, tight denim", source_slot="clothing", order=1),
            PromptFragment(text="skirt slit revealing, pleated skirt floating", source_slot="clothing", order=2),
        ]
        res_pants = self.resolver.resolve_fragments(frags_pants, Random(42))
        txt_pants = " ".join(f.text.lower() for f in res_pants)
        self.assertIn("skinny jeans", txt_pants)
        self.assertNotIn("skirt slit revealing", txt_pants)
        self.assertNotIn("pleated skirt floating", txt_pants)

    # ─── Rule 17: 多手持道具唯一性消解 ───
    def test_rule17_multiple_handheld_props_keep_first_only(self):
        """测试同时出现多个手持道具时，只保留首个主手持动作，消除多肢体异常"""
        frags = [
            PromptFragment(text="holding black compact digital camera", source_slot="props", order=1),
            PromptFragment(text="holding folding fan in hand", source_slot="props", order=2),
            PromptFragment(text="holding wine glass in hand", source_slot="props", order=3),
        ]
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        self.assertEqual(len(resolved), 1)
        self.assertIn("holding black compact digital camera", resolved[0].text)

    # ─── 综合多规则交叉级联消解测试 ───
    def test_cross_slot_full_cascade_15_slots(self):
        """测试 15 槽位极端冲突下，17 大规则流水线级联消解的终极稳定性"""
        frags = [
            # 1. 景别特写 (Rule 13 触发)
            PromptFragment(text="extreme close-up, focused on facial expression", source_slot="shot_type", order=1),
            # 2. 视角仰拍 (Rule 4 触发)
            PromptFragment(text="low angle, looking up from below", source_slot="camera_angle", order=2),
            # 3. 饰品眼罩 (Rule 14 触发)
            PromptFragment(text="black lace blindfold covering eyes", source_slot="accessories", order=3),
            # 4. 眼神直视冲突 (Rule 4/14 需消解)
            PromptFragment(text="direct eye contact with camera, playful wink", source_slot="expression", order=4),
            # 5. 服装泳衣 + 冲突掀裙 (Rule 16 需消解)
            PromptFragment(text="school swimsuit (sukumizu)", source_slot="clothing", order=5),
            PromptFragment(text="skirt lifted, unbuttoned dress shirt", source_slot="clothing", order=6),
            # 6. 下半身足部饰品 (Rule 13 需消解)
            PromptFragment(text="thigh-high stockings, stiletto heels", source_slot="clothing", order=7),
            # 7. 双手占用姿势 (Rule 9 触发)
            PromptFragment(text="hands clasped behind back", source_slot="pose", order=8),
            # 8. 两个手持道具 (Rule 9/17 需消解)
            PromptFragment(text="holding smartphone recording", source_slot="props", order=9),
            PromptFragment(text="holding wine glass", source_slot="props", order=10),
            # 9. 黑白胶片 (Rule 15 触发)
            PromptFragment(text="ilford hp5 plus, classic monochrome", source_slot="film_stock", order=11),
            # 10. 冲突霓虹彩光 (Rule 15 需消解)
            PromptFragment(text="vibrant neon cyan and magenta", source_slot="lighting", order=12),
            PromptFragment(text="dramatic chiaroscuro lighting", source_slot="lighting", order=13),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        res_text = " ".join(f.text.lower() for f in resolved)

        # 验证保留的合法元素
        self.assertIn("extreme close-up", res_text)
        self.assertIn("black lace blindfold covering eyes", res_text)
        self.assertIn("school swimsuit (sukumizu)", res_text)
        self.assertIn("hands clasped behind back", res_text)
        self.assertIn("ilford hp5 plus", res_text)
        self.assertIn("dramatic chiaroscuro lighting", res_text)

        # 验证全部冲突被 100% 清除
        self.assertNotIn("thigh-high stockings", res_text)
        self.assertNotIn("stiletto heels", res_text)
        self.assertNotIn("direct eye contact", res_text)
        self.assertNotIn("playful wink", res_text)
        self.assertNotIn("skirt lifted", res_text)
        self.assertNotIn("unbuttoned dress shirt", res_text)
        self.assertNotIn("holding smartphone", res_text)
        self.assertNotIn("holding wine glass", res_text)
        self.assertNotIn("vibrant neon cyan and magenta", res_text)


if __name__ == "__main__":
    unittest.main()
