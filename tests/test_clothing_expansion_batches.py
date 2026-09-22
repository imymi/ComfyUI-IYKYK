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
            self.assertIn(len(res_id.base_tags), (1, 2))
            for tag in res_id.base_tags:
                self.assertEqual(tag.provenance.item_id, cid)
                self.assertEqual(tag.provenance.kind, "base_clothing")
                self.assertIn("top", tag.facts.garment_topologies)

            # 2. 按中文名称采样
            res_name = self.sampler.sample_clothing_result(name_zh, "无 (None)", "L1", rng)
            self.assertEqual(res_name.style_id, cid)
            self.assertIn(len(res_name.base_tags), (1, 2))

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

    def test_07_four_state_modes_mutual_exclusion_and_variant_reachability(self):
        """
        覆盖四种状态模式 (NONE, AUTO, RANDOM, EXPLICIT) 的受控独立采样契约：
        1. 规范等级代码 (L2-L4) 覆盖 AUTO, RANDOM, EXPLICIT 的真实分支，同时保留 NONE；
        2. 基础服装防空保留：在所有应保留基础服装的模式下，len(res.base_tags) >= 1；
        3. 版型与属性约束：恰有 1 个主款/变体 (core_base/variant)，至多 1 个可组合属性 (combinable_attribute)；
        4. 同组互斥严格为 1：res.base_tags 中绝不允许存在共享相同非空 mutex_group 的两个标签；
        5. 全变体可达性 (Reachability)：对 16 款逐款多种子抽样，词库定义的每一个变体与属性均被真实采到，全批 51 个叶子变体 100% 可达。
        """
        with open(DATA_DIR / "clothing.json", "r", encoding="utf-8") as f:
            cdata = json.load(f)
        catalog_tags_by_id = {c["id"]: {t["text"] for t in c.get("tags", [])} for c in cdata.get("categories", [])}

        modes = [
            ("NONE", "无 (None)", "L1"),
            ("AUTO_L2", "自动联动裸露等级 (Auto Link Nudity)", "L2"),
            ("AUTO_L3", "自动联动裸露等级 (Auto Link Nudity)", "L3"),
            ("AUTO_L4", "自动联动裸露等级 (Auto Link Nudity)", "L4"),
            ("RANDOM_L2", "随机 (Random)", "L2"),
            ("RANDOM_L3", "随机 (Random)", "L3"),
            ("RANDOM_L4", "随机 (Random)", "L4"),
            ("EXPLICIT_L2", "正常穿着 (Normal)", "L2"),
            ("EXPLICIT_L3", "正常穿着 (Normal)", "L3"),
            ("EXPLICIT_L4", "正常穿着 (Normal)", "L4"),
        ]

        all_seen_tags = set()
        for cid, _, _, _ in BATCH_1_ITEMS:
            expected_tags = catalog_tags_by_id[cid]
            seen_tags = set()

            for mode_name, state_opt, nudity_code in modes:
                for seed in range(25):
                    rng = Random(seed * 100 + 7)
                    res = self.sampler.sample_clothing_result(cid, state_opt, nudity_code, rng)

                    # 1. 基础服装防空保留：必须存在基础款式标签
                    self.assertGreaterEqual(
                        len(res.base_tags),
                        1,
                        f"Category {cid} produced empty base_tags under mode {mode_name} with seed {seed}"
                    )

                    # 2. 恰有 1 个版型主款/变体 (role in core_base, variant)
                    silhouettes = [t for t in res.base_tags if getattr(t, "role", None) in ("core_base", "variant")]
                    self.assertEqual(
                        len(silhouettes),
                        1,
                        f"Category {cid} expected exactly 1 silhouette variant, got {[t.text for t in silhouettes]} under {mode_name} seed {seed}"
                    )

                    # 3. 至多 1 个可组合属性 (role == combinable_attribute)
                    attributes = [t for t in res.base_tags if getattr(t, "role", None) == "combinable_attribute"]
                    self.assertLessEqual(
                        len(attributes),
                        1,
                        f"Category {cid} expected at most 1 combinable attribute, got {[t.text for t in attributes]} under {mode_name} seed {seed}"
                    )

                    # 4. 强互斥不共存：同一互斥组至多 1 个
                    used_mg_counts: dict[str, int] = {}
                    for tag in res.base_tags:
                        seen_tags.add(tag.text)
                        all_seen_tags.add(tag.text)
                        for mg in tag.facts.mutex_groups:
                            used_mg_counts[mg] = used_mg_counts.get(mg, 0) + 1
                    for mg, count in used_mg_counts.items():
                        self.assertEqual(
                            count,
                            1,
                            f"Category {cid} co-sampled {count} tags in same mutex_group '{mg}': {[t.text for t in res.base_tags]} under {mode_name} seed {seed}"
                        )

            # 5. 款内可达性验证：该款词库声明的每一个标签在不同种子下均能被实际采到
            unreached = expected_tags - seen_tags
            self.assertEqual(
                len(unreached),
                0,
                f"Category {cid} has unreachable tags in controlled sampling: {unreached}"
            )

        # 6. 全批可达性验证：Batch 1 声明的全部 51 个叶子标签必须全部被采到
        self.assertEqual(
            len(all_seen_tags),
            51,
            f"Expected all 51 leaf tags across Batch 1 to be reached, got {len(all_seen_tags)}"
        )

    def test_08_legacy_31_styles_compatibility_and_rng_order(self):
        """
        验证存量 31 款向后兼容与随机数调用顺序严格保真：
        1. 存量款式在 SelectionMode.NONE 下保持原分支，返回全部基础标签且不消耗 RNG 调用；
        2. 在 L5/L6 等原本不输出基础服装的路径继续输出空 base_tags，保持原行为；
        3. 对比 596f43e 基线的固定多场景/多种子全量输出 (930 组用例) 与 RNG 状态序列，严格断言哈希一致。
        """
        import hashlib
        from tests.test_clothing_catalog_integrity import BASE_31_IDS

        test_scenarios = [
            ("无 (None)", "L1"),
            ("无 (None)", "L2"),
            ("无 (None)", "L5"),
            ("自动联动裸露等级 (Auto Link Nudity)", "L1"),
            ("自动联动裸露等级 (Auto Link Nudity)", "L2"),
            ("自动联动裸露等级 (Auto Link Nudity)", "L3"),
            ("随机 (Random)", "L1"),
            ("随机 (Random)", "L2"),
            ("正常穿着 (Normal)", "L1"),
            ("正常穿着 (Normal)", "L2"),
        ]

        records = []
        for cid in sorted(BASE_31_IDS):
            # 1. NONE 模式：存量款式无元数据，必须返回全部基础标签且 0 次 RNG 消费
            rng_a = Random(42)
            state_before = rng_a.getstate()
            res_none = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng_a)
            state_after = rng_a.getstate()
            # 严格断言：存量款式在 NONE 模式下完全不改变 RNG 内部状态 (0 次 RNG 消费)
            self.assertEqual(
                state_before,
                state_after,
                f"Legacy style {cid} consumed RNG calls in SelectionMode.NONE!"
            )
            self.assertGreaterEqual(len(res_none.base_tags), 1)

            # 2. L5/L6 模式：基础服装必须为空
            res_l5 = self.sampler.sample_clothing_result(cid, "正常穿着 (Normal)", "L5", Random(42))
            self.assertEqual(len(res_l5.base_tags), 0, f"Legacy style {cid} under L5 must have empty base_tags")
            res_l6 = self.sampler.sample_clothing_result(cid, "正常穿着 (Normal)", "L6", Random(42))
            self.assertEqual(len(res_l6.base_tags), 0, f"Legacy style {cid} under L6 must have empty base_tags")

            # 3. 收集 930 组输出与 post-call RNG 状态以核验 596f43e 基线保真度
            for state_opt, nudity_code in test_scenarios:
                for seed in (42, 100, 2024):
                    rng = Random(seed)
                    res = self.sampler.sample_clothing_result(cid, state_opt, nudity_code, rng)
                    records.append({
                        "cid": cid,
                        "state_opt": state_opt,
                        "nudity": nudity_code,
                        "seed": seed,
                        "base": [t.text for t in res.base_tags],
                        "state": [t.text for t in res.state_tags],
                        "ext": [t.text for t in res.extension_tags],
                        "state_id": res.state_id,
                        "rng_next": rng.random(),
                    })

        self.assertEqual(len(records), 930)
        serialized = json.dumps(records, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        golden_digest_596f43e = "2d00175f39f7c8fd2492f212e8867efd0feb5c1a697d0591829fdf5c1c3da4c4"
        self.assertEqual(
            digest,
            golden_digest_596f43e,
            f"Legacy 31 styles sampling output or RNG sequence drifted from 596f43e baseline! Got {digest}"
        )


if __name__ == "__main__":
    unittest.main()

