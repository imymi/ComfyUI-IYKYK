"""
test_props_and_extensions_reachability.py — 道具与服装扩展数据端到端可达性与确定性测试
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random

from lib.errors import DataSelectionError
from lib.sampler import DataSampler
from nodes import IYKYKPromptGenerator


DATA_DIR = Path(__file__).parent.parent / "data"


class TestPropsAndExtensionsReachability(unittest.TestCase):
    """测试嵌套道具两级采样与服装扩展梯度的端到端可达性与节点全链路验证"""

    @classmethod
    def setUpClass(cls):
        cls.sampler = DataSampler(DATA_DIR)
        cls.generator = IYKYKPromptGenerator()

        clothing_doc = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        cls.exp_tags = {t.lower(): item["id"] for item in clothing_doc.get("sfw_exposure_tiers", []) for t in item.get("tags", [])}
        cls.trans_tags = {t.lower(): item["id"] for item in clothing_doc.get("cloth_transparency_tiers", []) for t in item.get("tags", [])}
        cls.wardrobe_tags = {t.lower(): item["id"] for item in clothing_doc.get("lingerie_wardrobe", []) for t in item.get("tags", [])}
        cls.all_extension_tags = set(cls.exp_tags.keys()) | set(cls.trans_tags.keys()) | set(cls.wardrobe_tags.keys())

        cls.expected_exposure_ids = {item["id"] for item in clothing_doc.get("sfw_exposure_tiers", [])}
        cls.expected_transparency_ids = {item["id"] for item in clothing_doc.get("cloth_transparency_tiers", [])}
        cls.expected_wardrobe_ids = {item["id"] for item in clothing_doc.get("lingerie_wardrobe", [])}
        cls.expected_all_24_tier_ids = cls.expected_exposure_ids | cls.expected_transparency_ids | cls.expected_wardrobe_ids
        assert len(cls.expected_all_24_tier_ids) == 24, f"Expected 24 distinct tier IDs, got {len(cls.expected_all_24_tier_ids)}"

    def test_all_15_props_return_non_empty_tags(self):
        """测试全部 15 个道具分类（包含 4 个二级 items 嵌套分类）单项选择均输出非空结果"""
        prop_styles = self.sampler.list_prop_styles()
        self.assertGreaterEqual(len(prop_styles), 15, "Expected at least 15 prop styles")

        for prop in prop_styles:
            tags = self.sampler.sample_prop(prop, Random(42))
            self.assertTrue(
                len(tags) > 0,
                f"Prop category [{prop}] returned empty tags when sampled!"
            )

    def test_invalid_prop_returns_empty_safely(self):
        """测试无道具选项时安全返回空列表，未知显式道具遵循 Fail-Fast 抛出 DataSelectionError"""
        for none_name in ["", "无 (None)", "None"]:
            res = self.sampler.sample_prop(none_name, Random(42))
            self.assertEqual(res, [], f"Expected empty list for '{none_name}', got {res}")
        for invalid_name in ["not-a-real-prop", "unknown_123"]:
            with self.assertRaises(DataSelectionError):
                self.sampler.sample_prop(invalid_name, Random(42))


    def test_prop_sampling_deterministic_per_seed(self):
        """测试同一 seed 下道具采样结果 100% 确定性复现"""
        for prop in self.sampler.list_prop_styles():
            res1 = self.sampler.sample_prop(prop, Random(12345))
            res2 = self.sampler.sample_prop(prop, Random(12345))
            self.assertEqual(res1, res2, f"Prop [{prop}] produced non-deterministic results for same seed")

    def test_nested_items_mutual_exclusivity(self):
        """测试包含多个子条目的分类（如团扇与油纸伞），单次采样绝不将冲突子项混拼"""
        nested_prop = "🪭 古风刺绣团扇/油纸伞/折扇"
        for seed in range(50):
            tags = self.sampler.sample_prop(nested_prop, Random(seed))
            tag_str = " ".join(tags).lower()
            has_fan = "fan" in tag_str
            has_umbrella = "umbrella" in tag_str
            self.assertFalse(
                has_fan and has_umbrella,
                f"Seed {seed} mixed both fan and umbrella in single sampling: {tags}"
            )

    def test_sfw_exposure_tiers_reachability(self):
        """测试 9 档 SFW 镂空露肤梯度均可达且非空"""
        tiers = self.sampler.list_sfw_exposure_tiers()
        self.assertEqual(len(tiers), 9, f"Expected 9 SFW exposure tiers, got {len(tiers)}")
        for t in tiers:
            tags = self.sampler.sample_sfw_exposure(t, Random(42))
            self.assertTrue(len(tags) > 0, f"Exposure tier [{t}] returned empty tags")

    def test_cloth_transparency_tiers_reachability(self):
        """测试 5 档面料透肉度梯度均可达且非空"""
        tiers = self.sampler.list_cloth_transparency_tiers()
        self.assertEqual(len(tiers), 5, f"Expected 5 cloth transparency tiers, got {len(tiers)}")
        for t in tiers:
            tags = self.sampler.sample_cloth_transparency(t, Random(42))
            self.assertTrue(len(tags) > 0, f"Transparency tier [{t}] returned empty tags")

    def test_lingerie_wardrobe_reachability(self):
        """测试 10 大类情趣内衣衣柜分类均可达且非空"""
        wardrobe = self.sampler.list_lingerie_wardrobe()
        self.assertEqual(len(wardrobe), 10, f"Expected 10 lingerie wardrobe items, got {len(wardrobe)}")
        for w in wardrobe:
            tags = self.sampler.sample_lingerie_wardrobe(w, Random(42))
            self.assertTrue(len(tags) > 0, f"Lingerie wardrobe category [{w}] returned empty tags")

    def test_default_autolink_nudity_samples_clothing_extensions(self):
        """默认 '自动联动裸露等级' 下，L2/L3/L4 必须在端到端生成中稳定接入全部 24 个扩展 tier ID"""
        l2_hits = 0
        l3_hits = 0
        l4_hits = 0
        hit_tier_ids = set()

        all_tag_to_id = {**self.exp_tags, **self.trans_tags, **self.wardrobe_tags}

        for seed in range(1000):
            # L2 默认自动联动
            p_l2, _, _ = self.generator.generate(
                预设模板="无 (None)",
                风格配方="无 (None)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="自动 (Auto)",
                拍摄视角="自动 (Auto)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="无 (None)",
                妆容细节="无 (None)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="无 (None)",
                液体效果="无 (None)",
                纹身标记="无 (None)",
                道具物件="无 (None)",
                角色设定="无 (None)",
                真实微瑕="无 (None)",
                画质等级="高清写真 (High)",
                prompt_seed=seed,
            )
            p_l2_low = p_l2.lower()
            for t, tid in all_tag_to_id.items():
                if t in p_l2_low:
                    l2_hits += 1
                    hit_tier_ids.add(tid)

            # L3 默认自动联动
            p_l3, _, _ = self.generator.generate(
                预设模板="无 (None)",
                风格配方="无 (None)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="自动 (Auto)",
                拍摄视角="自动 (Auto)",
                裸露等级="L3 半裸诱惑 (Half Nude)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="无 (None)",
                妆容细节="无 (None)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="无 (None)",
                液体效果="无 (None)",
                纹身标记="无 (None)",
                道具物件="无 (None)",
                角色设定="无 (None)",
                真实微瑕="无 (None)",
                画质等级="高清写真 (High)",
                prompt_seed=seed,
            )
            p_l3_low = p_l3.lower()
            for t, tid in all_tag_to_id.items():
                if t in p_l3_low:
                    l3_hits += 1
                    hit_tier_ids.add(tid)

            # L4 默认自动联动
            p_l4, _, _ = self.generator.generate(
                预设模板="无 (None)",
                风格配方="无 (None)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="自动 (Auto)",
                拍摄视角="自动 (Auto)",
                裸露等级="L4 重点暴露 (Topless / Bottomless)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="无 (None)",
                妆容细节="无 (None)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="无 (None)",
                液体效果="无 (None)",
                纹身标记="无 (None)",
                道具物件="无 (None)",
                角色设定="无 (None)",
                真实微瑕="无 (None)",
                画质等级="高清写真 (High)",
                prompt_seed=seed,
            )
            p_l4_low = p_l4.lower()
            for t, tid in all_tag_to_id.items():
                if t in p_l4_low:
                    l4_hits += 1
                    hit_tier_ids.add(tid)

        # 断言默认 Auto Link Nudity 下扩展命中率必须 > 70% (700/1000)
        self.assertGreater(l2_hits, 700, f"L2 default autolink extension hits too low: {l2_hits}/1000")
        self.assertGreater(l3_hits, 700, f"L3 default autolink extension hits too low: {l3_hits}/1000")
        self.assertGreater(l4_hits, 700, f"L4 default autolink extension hits too low: {l4_hits}/1000")

        # 核心断言：24 个扩展 tier ID 在自动链路中全部被实际命中 (24/24 逐 ID 100% 可达)
        missing_ids = self.expected_all_24_tier_ids - hit_tier_ids
        self.assertEqual(
            hit_tier_ids,
            self.expected_all_24_tier_ids,
            f"Missing reachable extension tier IDs in automatic link: {missing_ids}"
        )

    def test_l1_l5_l6_zero_extension_hits(self):
        """L1、L5、L6 在各自 1000 次随机生成中绝不命中任何服装扩展标签（1000 seeds 0 污染）"""
        for lvl_name in [
            "L1 包裹暗示 (Fully Clothed / Suggestive)",
            "L5 极致全裸 (Full Nude)",
            "L6 特写全见 (Explicit Genital Close-up)",
        ]:
            for seed in range(1000):
                p, _, _ = self.generator.generate(
                    预设模板="无 (None)",
                    风格配方="无 (None)",
                    场景大类="随机 (Random)",
                    剧情主题="随机 (Random)",
                    景别构图="自动 (Auto)",
                    拍摄视角="自动 (Auto)",
                    裸露等级=lvl_name,
                    服装款式="随机 (Random)",
                    服装状态="自动联动裸露等级 (Auto Link Nudity)",
                    发型发色="随机 (Random)",
                    饰品头饰="无 (None)",
                    妆容细节="无 (None)",
                    姿势动作="随机 (Random)",
                    情绪表情="随机 (Random)",
                    光影预设="自动 (Auto)",
                    胶片风格="无 (None)",
                    液体效果="无 (None)",
                    纹身标记="无 (None)",
                    道具物件="无 (None)",
                    角色设定="无 (None)",
                    真实微瑕="无 (None)",
                    画质等级="高清写真 (High)",
                    prompt_seed=seed,
                )
                p_low = p.lower()
                for ext_tag in self.all_extension_tags:
                    self.assertNotIn(
                        ext_tag,
                        p_low,
                        f"Level [{lvl_name}] contaminated by clothing extension tag [{ext_tag}] at seed {seed}!"
                    )


if __name__ == "__main__":
    unittest.main()
