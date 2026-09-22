"""
test_clothing_expansion_batches.py — 生产服装词库分批迁移行为、采样可达性与消解冲突门禁测试

覆盖验证：
1. Batch 1 (16 款上装) 逐款采样可达性：按 ID、按中文显示名、按别名精确命中；
2. 随机采样全可达性：在随机模式下，万种子空间内 16 款新增款式均可被真实抽样命中；
3. 离散叶子标签独立性：多变体款式返回离散原子，绝无暴力拼接；
4. 形制契约正反例：
   - 正例：具备纽扣能力的款式 (shirts_blouses, combat_tactical 等) + unbuttoned 零 state_lacks_carrier 剔除；
   - 反例：无纽扣能力的款式 (t_shirt, hoodie 等) + unbuttoned 被精准剔除并标记 state_lacks_carrier；
   - 反例：上装 + lifted_up (掀裙) 均被精准剔除并标记 state_lacks_carrier；
5. 真实节点端到端生成与 Provenance DAG 完整性。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.conflict_resolver import ConflictResolver
from lib.lexer import validate_prompt_syntax
from lib.sampler import DataSampler
import nodes

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"

BATCH_1_ITEMS = [
    ("combat_tactical", "战斗服 (Combat Tactical Suit)", ["combat_suit", "swat_uniform"], True),
    ("convenience_store", "便利店工作服 (Convenience Store Uniform)", ["convenience_store_uniform"], True),
    ("crop_top", "小可爱露腹短上衣 (Crop Top)", ["crop_top", "belly_top"], False),
    ("fast_food_uniform", "快餐制服 (Fast Food Uniform)", ["fast_food_worker"], True),
    ("fishnet_top", "网纹衣 (Fishnet Top)", ["fishnet_shirt", "mesh_top"], False),
    ("hoodie", "连帽衫 (Hoodie)", ["hooded_sweatshirt", "oversized_hoodie"], False),
    ("knit_vest", "V领针织背心 (Knit Vest)", ["sweater_vest", "v_neck_vest"], False),
    ("military_uniform", "军装 (Military Uniform)", ["army_uniform", "military_jacket"], True),
    ("sailor_shirt", "水手衬衫 (Sailor Shirt)", ["sailor_collar_shirt"], False),
    ("shirts_blouses", "高领衬衫 (Shirts & Blouses)", ["collared_shirt", "white_shirt"], True),
    ("sportswear_active", "运动服 (Active Sportswear)", ["activewear", "athletic_wear"], False),
    ("strapless_top", "抹胸 (Strapless Top)", ["tube_top", "strapless_tank_top"], False),
    ("sweatshirt", "圆领卫衣 (Sweatshirt)", ["crewneck_sweatshirt"], False),
    ("t_shirt", "T恤 (T-Shirt)", ["tee", "casual_t_shirt"], False),
    ("tops_tanks", "背心 (Tanks & Camisoles)", ["tank_top", "camisole"], False),
    ("volleyball_uniform", "排球服 (Volleyball Uniform)", ["volleyball_jersey"], False),
]


class TestClothingExpansionBatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sampler = DataSampler(DATA_DIR)
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.generator = nodes.IYKYKPromptGenerator()

    def test_01_batch_1_sampling_accessibility(self):
        """逐款验证 Batch 1 16 款上装按规范 ID、中文显示名和别名均可精确采样。"""
        rng = Random(42)
        for cid, name_zh, aliases, _ in BATCH_1_ITEMS:
            # 1. 按规范 ID 采样
            res_id = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng)
            self.assertEqual(res_id.style_id, cid)
            self.assertTrue(len(res_id.base_tags) >= 2)
            for tag in res_id.base_tags:
                self.assertEqual(tag.provenance.item_id, cid)
                self.assertEqual(tag.provenance.kind, "base_clothing")
                self.assertIn("top", tag.facts.garment_topologies)

            # 2. 按中文名称采样
            res_name = self.sampler.sample_clothing_result(name_zh, "无 (None)", "L1", rng)
            self.assertEqual(res_name.style_id, cid)
            self.assertTrue(len(res_name.base_tags) >= 2)

            # 3. 按别名采样
            for alias in aliases:
                res_alias = self.sampler.sample_clothing_result(alias, "无 (None)", "L1", rng)
                self.assertEqual(res_alias.style_id, cid)

    def test_02_batch_1_random_reachability(self):
        """验证 Batch 1 16 款上装在随机模式 (随机 (Random)) 下均可真实被抽中。"""
        sampled_styles = set()
        rng = Random(2026)
        # 47 款均匀抽取，抽样 2000 次，期望每款约 42 次，所有 16 款必须 100% 被覆盖
        for _ in range(2000):
            res = self.sampler.sample_clothing_result("随机 (Random)", "无 (None)", "L1", rng)
            if res.style_id:
                sampled_styles.add(res.style_id)

        target_b1_ids = {item[0] for item in BATCH_1_ITEMS}
        unreached = target_b1_ids - sampled_styles
        self.assertEqual(len(unreached), 0, f"Batch 1 styles never reached in random sampling: {unreached}")

    def test_03_discrete_tag_syntax_and_word_count(self):
        """验证 Batch 1 款式返回离散叶子标签，零语法破坏且词数处于合理受控范围。"""
        rng = Random(1234)
        for cid, name_zh, _, _ in BATCH_1_ITEMS:
            res = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng)
            for tag in res.base_tags:
                validate_prompt_syntax(tag.text)
                words = tag.text.split()
                self.assertLessEqual(len(words), 15, f"Tag in {cid} too long: '{tag.text}'")

    def test_04_button_capability_positive_and_negative_matrix(self):
        """
        形制能力契约核验 (解扣状态 unbuttoned):
        - 允许解扣的 5 款 (combat_tactical, convenience_store, fast_food_uniform, military_uniform, shirts_blouses): 零 state_lacks_carrier 剔除
        - 禁止解扣的 11 款 (crop_top, fishnet_top, hoodie, knit_vest, sailor_shirt, sportswear_active, strapless_top, sweatshirt, t_shirt, tops_tanks, volleyball_uniform): unbuttoned 100% 被 Drop 并记录 state_lacks_carrier
        """
        for cid, name_zh, _, button_allowed in BATCH_1_ITEMS:
            gen_res = self.generator.generate_structured(
                场景预设="日常街道",
                服装款式=cid,
                服装状态="解开纽扣 (Unbuttoned)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                prompt_seed=42,
            )
            report = gen_res.resolution_report
            self.assertIsNotNone(report)

            dropped_unbuttoned = any(
                d.action == "drop"
                and d.reason_code == "state_lacks_carrier"
                for d in report.decisions
            )

            if button_allowed:
                self.assertFalse(
                    dropped_unbuttoned,
                    f"Style {cid} has button capability, but unbuttoned was unexpectedly dropped with state_lacks_carrier!",
                )
            else:
                self.assertTrue(
                    dropped_unbuttoned,
                    f"Style {cid} lacks button capability, but unbuttoned was NOT dropped with state_lacks_carrier!",
                )

    def test_05_skirt_capability_negative_all_tops_drop_lifted_skirt(self):
        """形制能力契约核验 (掀裙状态 lifted_up): Batch 1 全部 16 款均为上装 (top)，与掀裙均互斥，lifted_up 必须 100% 被 Drop。"""
        for cid, name_zh, _, _ in BATCH_1_ITEMS:
            gen_res = self.generator.generate_structured(
                场景预设="日常街道",
                服装款式=cid,
                服装状态="裙摆掀起 (Skirt Lifted Up)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                prompt_seed=42,
            )
            report = gen_res.resolution_report
            self.assertIsNotNone(report)

            dropped_lifted = any(
                d.action == "drop"
                and d.reason_code == "state_lacks_carrier"
                for d in report.decisions
            )
            self.assertTrue(
                dropped_lifted,
                f"Top garment {cid} has no skirt, but lifted_up was not dropped with state_lacks_carrier!",
            )

    def test_06_end_to_end_generation_and_dag_provenance(self):
        """端到端验证 Batch 1 16 款上装在真实节点生成下的 Provenance DAG 完整性与零未消解冲突。"""
        from tests.test_rc8_quality_gate import _verify_provenance_dag

        for cid, name_zh, _, _ in BATCH_1_ITEMS:
            res = self.generator.generate_structured(
                场景预设="日常街道",
                剧情主题="随机 (Random)",
                服装款式=name_zh,
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                画质等级="高清写真 (High)",
                prompt_seed=100,
            )
            self.assertTrue(res.positive)
            self.assertEqual(res.resolution_report.unresolved_conflicts, ())
            self.assertTrue(_verify_provenance_dag(res))
            validate_prompt_syntax(res.positive)


if __name__ == "__main__":
    unittest.main()
