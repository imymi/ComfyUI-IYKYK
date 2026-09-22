"""
test_clothing_expansion_batches.py — 生产服装词库分批迁移行为、采样可达性与消解冲突门禁测试

覆盖验证：
1. Batch 1 (16 款上装) 与 Batch 2 (22 款下装与内衣) 逐款采样可达性：按 ID、按中文显示名、按别名精确命中；
2. 随机采样全可达性：在随机模式下，万种子空间内 38 款新增款式均可被真实抽样命中；
3. 离散叶子标签独立性：多变体款式返回离散原子，绝无暴力拼接；
4. 形制契约正反例：
   - 纽扣能力：正例 (7 款) 验证具体动作词条留在 positive 且零 drop；反例 (31 款) 验证动作词条不漏出且 drop 标记 state_lacks_carrier；
   - 裙装能力：正例 (16 款半身裙) 验证掀裙动作留在 positive 且零 drop；反例 (22 款上装/裤装/内衣) 验证动作词条不漏出且裤装标记 pants_state_conflict、其他标记 state_lacks_carrier；
5. 真实节点端到端生成与 Provenance DAG 完整性；
6. 四状态模式受控采样与叶子标签 100% 全覆盖 (Batch 1: 51 标签, Batch 2: 67 标签, 共 118 标签)；
7. 存量 31 款兼容性与 596f43e 基线 930 用例全量黄金哈希保真。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.conflict_resolver import (
    ConflictResolver,
    GarmentCarrierEntity,
    is_garment_compatible_with_state,
)
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

BATCH_2_ITEMS = [
    ("armored_skirt", "披甲战裙 (Armored Skirt)", ["tassets", "fauld"], False, True, ["bottom_skirt"]),
    ("bikini_classic", "经典比基尼 (Classic Bikini)", ["two_piece_swimsuit"], False, False, ["underwear"]),
    ("bikini_creative", "创意奶牛比基尼 (Creative Cow Bikini)", ["cow_print_bikini"], False, False, ["underwear"]),
    ("bikini_strappy", "绑带系绳比基尼 (Strappy String Bikini)", ["string_bikini"], False, False, ["underwear"]),
    ("black_leggings", "黑色紧身裤 (Black Leggings)", ["leggings", "tights"], False, False, ["bottom_pants"]),
    ("denim_shorts", "牛仔短裤 (Denim Shorts)", ["jean_shorts", "cutoffs"], True, False, ["bottom_pants"]),
    ("hot_pants", "热裤 (Hot Pants)", ["booty_shorts"], False, False, ["bottom_pants"]),
    ("layered_skirt", "多层蛋糕裙 (Layered Skirt)", ["tiered_skirt"], False, True, ["bottom_skirt"]),
    ("leather_skirt", "皮裙 (Leather Skirt)", ["leather_miniskirt"], True, True, ["bottom_skirt"]),
    ("long_skirt", "长裙 (Long Skirt)", ["maxi_skirt"], False, True, ["bottom_skirt"]),
    ("microskirt", "微型超短裙 (Microskirt)", ["micro_skirt"], False, True, ["bottom_skirt"]),
    ("miniskirt", "迷你超短裙 (Miniskirt)", ["mini_skirt", "short_skirt"], False, True, ["bottom_skirt"]),
    ("pencil_skirt", "铅笔包臀裙 (Pencil Skirt)", ["tight_skirt"], False, True, ["bottom_skirt"]),
    ("pettiskirt", "蓬蓬衬裙 (Pettiskirt)", ["crinoline"], False, True, ["bottom_skirt"]),
    ("plaid_skirt", "格子百褶裙 (Plaid Skirt)", ["tartan_skirt"], False, True, ["bottom_skirt"]),
    ("pleated_skirt", "经典百褶裙 (Pleated Skirt)", ["tennis_skirt", "school_skirt"], False, True, ["bottom_skirt"]),
    ("pumpkin_skirt", "南瓜裙 (Pumpkin Skirt)", ["balloon_skirt"], False, True, ["bottom_skirt"]),
    ("rain_skirt", "分体雨裙 (Rain Skirt)", ["rainskirt"], False, True, ["bottom_skirt"]),
    ("skirts_general", "通用半身裙 (Skirts)", ["casual_skirt"], False, True, ["bottom_skirt"]),
    ("suspender_skirt", "吊带裙 (Suspender Skirt)", ["pinafore_skirt"], False, True, ["bottom_skirt"]),
    ("tutu_skirt", "芭蕾舞短裙 (Tutu Skirt)", ["ballet_tutu"], False, True, ["bottom_skirt"]),
    ("waist_apron", "半身腰围裙 (Waist Apron)", ["half_apron"], False, True, ["bottom_skirt"]),
]

BATCH_3_ITEMS = [
    ("berserker_armor", "狂战士铠甲 (Berserker Armor)", ["berserker_gear", "savage_armor"], False, False, ["outerwear"]),
    ("business_suit", "职业西装 (Business Suit)", ["formal_suit", "office_suit"], True, False, ["outerwear"]),
    ("denim_jacket", "牛仔夹克 (Denim Jacket)", ["jean_jacket", "denim_coat"], True, False, ["outerwear"]),
    ("down_jacket", "羽绒服 (Down Jacket)", ["puffer_jacket", "down_coat"], True, False, ["outerwear"]),
    ("duffel_coat", "粗呢大衣 (Duffel Coat)", ["toggle_coat", "duffle_coat"], True, False, ["outerwear"]),
    ("firefighter_gear", "消防防护服 (Firefighter Gear)", ["turnout_gear", "bunker_gear"], True, False, ["outerwear"]),
    ("knight_armor", "骑士重铠甲 (Knight Armor)", ["plate_armor", "full_plate"], False, False, ["outerwear"]),
    ("lab_coat", "实验白大褂 (Lab Coat)", ["doctor_coat", "scientist_coat"], True, False, ["outerwear"]),
    ("leather_jacket", "机车皮衣 (Leather Jacket)", ["biker_jacket", "moto_jacket"], True, False, ["outerwear"]),
    ("mecha_exoskeleton", "外骨骼机甲 (Mecha Exoskeleton)", ["exoskeleton_suit", "mech_frame"], False, False, ["outerwear"]),
    ("mecha_power_armor", "动力装甲 (Power Armor)", ["powered_exosuit", "heavy_mech_armor"], False, False, ["outerwear"]),
    ("military_overcoat", "军大衣 (Military Overcoat)", ["greatcoat", "army_greatcoat"], True, False, ["outerwear"]),
    ("outerwear_coat", "通用风衣外套 (Outerwear Coat)", ["casual_coat", "mid_length_coat"], True, False, ["outerwear"]),
    ("outerwear_jacket", "夹克外套 (Outerwear Jacket)", ["casual_jacket", "zip_jacket"], True, False, ["outerwear"]),
    ("outerwear_overcoat", "长款毛呢大衣 (Outerwear Overcoat)", ["winter_overcoat", "wool_overcoat"], True, False, ["outerwear"]),
    ("rainwear_coat", "防雨风衣 (Rainwear Coat)", ["waterproof_raincoat", "slicker"], True, False, ["outerwear"]),
    ("safari_jacket", "探险猎装夹克 (Safari Jacket)", ["bush_jacket", "field_jacket"], True, False, ["outerwear"]),
    ("soft_shell_jacket", "户外软壳外套 (Soft Shell Jacket)", ["windproof_softshell", "trekking_jacket"], True, False, ["outerwear"]),
    ("tactical_vest", "战术防弹背心 (Tactical Vest)", ["body_armor_vest", "plate_carrier"], True, False, ["outerwear"]),
    ("tailcoat", "宫廷燕尾服 (Tailcoat)", ["evening_tailcoat", "dress_coat"], True, False, ["outerwear"]),
    ("trench_coat", "战壕风衣 (Trench Coat)", ["belted_trench", "duster_coat"], True, False, ["outerwear"]),
    ("windbreaker", "运动防风衣 (Windbreaker)", ["lightweight_windbreaker", "running_jacket"], True, False, ["outerwear"]),
    ("winter_parka", "防寒派克大衣 (Winter Parka)", ["down_parka", "arctic_parka"], True, False, ["outerwear"]),
]


class TestClothingExpansionBatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sampler = DataSampler(DATA_DIR)
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.generator = nodes.IYKYKPromptGenerator()

    def test_01_batch_1_2_3_sampling_accessibility(self):
        """逐款验证 Batch 1 (16 款上装), Batch 2 (22 款下装与内衣), Batch 3 (23 款外套机甲) 按规范 ID、中文显示名和别名均可精确采样。"""
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

        for cid, name_zh, aliases, _, _, topos in BATCH_2_ITEMS:
            # 1. 按规范 ID 采样
            res_id = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng)
            self.assertEqual(res_id.style_id, cid)
            self.assertIn(len(res_id.base_tags), (1, 2))
            for tag in res_id.base_tags:
                self.assertEqual(tag.provenance.item_id, cid)
                self.assertEqual(tag.provenance.kind, "base_clothing")
                self.assertTrue(
                    any(t in tag.facts.garment_topologies for t in topos),
                    f"{cid} tag {tag.text} topologies {tag.facts.garment_topologies} disjoint from {topos}"
                )

            # 2. 按中文名称采样
            res_name = self.sampler.sample_clothing_result(name_zh, "无 (None)", "L1", rng)
            self.assertEqual(res_name.style_id, cid)
            self.assertIn(len(res_name.base_tags), (1, 2))

            # 3. 按别名采样
            for alias in aliases:
                res_alias = self.sampler.sample_clothing_result(alias, "无 (None)", "L1", rng)
                self.assertEqual(res_alias.style_id, cid)

        for cid, name_zh, aliases, _, _, topos in BATCH_3_ITEMS:
            # 1. 按规范 ID 采样
            res_id = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng)
            self.assertEqual(res_id.style_id, cid)
            self.assertIn(len(res_id.base_tags), (1, 2))
            for tag in res_id.base_tags:
                self.assertEqual(tag.provenance.item_id, cid)
                self.assertEqual(tag.provenance.kind, "base_clothing")
                self.assertTrue(
                    any(t in tag.facts.garment_topologies for t in topos),
                    f"{cid} tag {tag.text} topologies {tag.facts.garment_topologies} disjoint from {topos}"
                )

            # 2. 按中文名称采样
            res_name = self.sampler.sample_clothing_result(name_zh, "无 (None)", "L1", rng)
            self.assertEqual(res_name.style_id, cid)
            self.assertIn(len(res_name.base_tags), (1, 2))

            # 3. 按别名采样
            for alias in aliases:
                res_alias = self.sampler.sample_clothing_result(alias, "无 (None)", "L1", rng)
                self.assertEqual(res_alias.style_id, cid)

    def test_02_batches_random_reachability(self):
        """验证 Batch 1 (16 款), Batch 2 (22 款), Batch 3 (23 款) 共 61 款新增款式在随机模式 (随机 (Random)) 下均可真实被抽中。"""
        sampled_styles = set()
        rng = Random(2026)
        # 92 款均匀抽取，抽样 3000 次，所有 61 款新增款式必须 100% 被覆盖
        for _ in range(3000):
            res = self.sampler.sample_clothing_result("随机 (Random)", "无 (None)", "L1", rng)
            if res.style_id:
                sampled_styles.add(res.style_id)

        target_ids = {item[0] for item in BATCH_1_ITEMS} | {item[0] for item in BATCH_2_ITEMS} | {item[0] for item in BATCH_3_ITEMS}
        unreached = target_ids - sampled_styles
        self.assertEqual(len(unreached), 0, f"Styles never reached in random sampling: {unreached}")

    def test_03_discrete_tag_syntax_and_word_count(self):
        """验证 Batch 1, 2, 3 共 61 款款式返回离散叶子标签，零语法破坏且词数处于合理受控范围。"""
        rng = Random(1234)
        all_items = [(i[0], i[1]) for i in BATCH_1_ITEMS] + [(i[0], i[1]) for i in BATCH_2_ITEMS] + [(i[0], i[1]) for i in BATCH_3_ITEMS]
        for cid, name_zh in all_items:
            res = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng)
            for tag in res.base_tags:
                validate_prompt_syntax(tag.text)
                words = tag.text.split()
                self.assertLessEqual(len(words), 15, f"Tag in {cid} too long: '{tag.text}'")

    def test_04_button_capability_positive_and_negative_matrix(self):
        """形制能力契约核验 (解扣状态 unbuttoned):
        严格验证具体动作词条 (按服装形制拓扑区分动作) 真实生成或精准剔除：
        - 正例：具备纽扣能力的款式 (Batch 1: 5 款上装, Batch 2: denim_shorts, leather_skirt 2 款, Batch 3: 19 款外套 共 26 款)：
          * 上装正例：buttons undone revealing cleavage / shirt open at chest 保留在 positive prompt 中；
          * 裤装正例 (denim_shorts)：pants button undone / jeans unbuttoned 保留在 positive prompt 中，胸前/衬衫/外套动作严禁存在；
          * 裙装正例 (leather_skirt)：skirt button undone / skirt unbuttoned 保留在 positive prompt 中，胸前/衬衫/外套动作严禁存在；
          * 外套正例 (Batch 3 19 款)：unbuttoned coat / jacket open 保留在 positive prompt 中，衬衫/裤装/裙装动作严禁存在；
          * 正例动作无相应 Drop 决策。
        - 反例：无纽扣能力的款式 (Batch 1: 11 款, Batch 2: 20 款, Batch 3: 4 款机甲重铠 共 35 款)：
          动作词条绝对不出现在 positive prompt 中，且决策报告中必须明确记录 action == "drop" 且 reason_code == "state_lacks_carrier"。
        """
        all_items = (
            [(i[0], i[3], ["top"]) for i in BATCH_1_ITEMS]
            + [(i[0], i[3], i[5]) for i in BATCH_2_ITEMS]
            + [(i[0], i[3], i[5]) for i in BATCH_3_ITEMS]
        )

        ALL_BUTTON_ACTIONS = (
            "blouse unbuttoned", "buttons undone revealing cleavage", "shirt open at chest",
            "pants button undone", "jeans unbuttoned",
            "skirt button undone", "skirt unbuttoned",
            "jacket unbuttoned", "coat open", "jacket open", "unbuttoned coat",
        )

        for cid, button_allowed, topos in all_items:
            gen_res = self.generator.generate_structured(
                场景预设="日常街道",
                服装款式=cid,
                服装状态="解开纽扣 (Unbuttoned)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                prompt_seed=42,
            )
            report = gen_res.resolution_report
            self.assertIsNotNone(report)

            if "bottom_pants" in topos:
                expected_target_actions = ("pants button undone", "jeans unbuttoned")
                forbidden_cross_actions = ("buttons undone revealing cleavage", "shirt open at chest", "skirt button undone", "skirt unbuttoned", "unbuttoned coat", "jacket open")
            elif "bottom_skirt" in topos:
                expected_target_actions = ("skirt button undone", "skirt unbuttoned")
                forbidden_cross_actions = ("buttons undone revealing cleavage", "shirt open at chest", "pants button undone", "jeans unbuttoned", "unbuttoned coat", "jacket open")
            elif "outerwear" in topos:
                expected_target_actions = ("unbuttoned coat", "jacket open")
                forbidden_cross_actions = ("buttons undone revealing cleavage", "shirt open at chest", "blouse unbuttoned", "pants button undone", "jeans unbuttoned", "skirt button undone", "skirt unbuttoned")
            else:
                expected_target_actions = ("buttons undone revealing cleavage", "shirt open at chest")
                forbidden_cross_actions = ("pants button undone", "jeans unbuttoned", "skirt button undone", "skirt unbuttoned", "unbuttoned coat", "jacket open")

            dropped_btn_decisions = {
                d.before_text: d
                for d in report.decisions
                if d.action == "drop" and d.before_text in expected_target_actions
            }

            if button_allowed:
                for bt in expected_target_actions:
                    self.assertIn(
                        bt,
                        gen_res.positive,
                        f"Style {cid} has button capability, but action '{bt}' is missing from positive prompt!",
                    )
                    self.assertNotIn(
                        bt,
                        dropped_btn_decisions,
                        f"Style {cid} has button capability, but action '{bt}' was unexpectedly dropped!",
                    )
                for f_bt in forbidden_cross_actions:
                    self.assertNotIn(
                        f_bt,
                        gen_res.positive,
                        f"Style {cid} must NOT carry incompatible cross-body action '{f_bt}'!",
                    )
            else:
                for bt in ALL_BUTTON_ACTIONS:
                    self.assertNotIn(
                        bt,
                        gen_res.positive,
                        f"Style {cid} lacks button capability, but action '{bt}' leaked into positive prompt!",
                    )
                dropped_all_btn = {
                    d.before_text: d
                    for d in report.decisions
                    if d.action == "drop" and d.before_text in ALL_BUTTON_ACTIONS
                }
                self.assertGreater(
                    len(dropped_all_btn),
                    0,
                    f"Style {cid} lacks button capability, but no button action was dropped!",
                )
                for bt, d in dropped_all_btn.items():
                    self.assertEqual(
                        d.reason_code,
                        "state_lacks_carrier",
                        f"Style {cid} dropped '{bt}' with unexpected reason {d.reason_code}",
                    )

    def test_05_skirt_capability_positive_and_negative_matrix(self):
        """形制能力契约核验 (掀裙状态 lifted_up):
        严格验证具体动作词条 (skirt pulled up revealing panties / skirt hiked up to waist) 真实生成或精准剔除：
        - 正例：具备裙装能力的款式 (Batch 2 的 16 款半身裙)：
          * 半身裙动作 (skirt pulled up revealing panties, skirt hiked up to waist) 100% 保留在 positive prompt 中；
          * 连衣裙动作 (dress hitched up) 绝对禁止借用/泄漏到半身裙中；
          * 决策报告中无裙装动作的 Drop；
        - 反例：无裙装能力的款式 (Batch 1 的 16 款上装、Batch 2 的 3 款裤装及 3 款比基尼内衣、Batch 3 的 23 款外套机甲 共 45 款)：
          动作词条绝对不出现在 positive prompt 中，且决策报告中必须明确记录 action == "drop"；
          其中裤装冲突原因严格为 pants_state_conflict，上装/内衣/外套冲突原因严格为 state_lacks_carrier。
        """
        all_items = (
            [(i[0], False, ["top"]) for i in BATCH_1_ITEMS]
            + [(i[0], i[4], i[5]) for i in BATCH_2_ITEMS]
            + [(i[0], i[4], i[5]) for i in BATCH_3_ITEMS]
        )

        for cid, skirt_allowed, topos in all_items:
            gen_res = self.generator.generate_structured(
                场景预设="日常街道",
                服装款式=cid,
                服装状态="裙摆掀起 (Skirt Lifted Up)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                prompt_seed=42,
            )
            report = gen_res.resolution_report
            self.assertIsNotNone(report)

            if skirt_allowed:
                expected_skirt_actions = ("skirt pulled up revealing panties", "skirt hiked up to waist")
                dropped_skirt_decisions = {
                    d.before_text: d
                    for d in report.decisions
                    if d.action == "drop" and d.before_text in expected_skirt_actions
                }
                # 具备裙装能力：采到的裙装动作必须保留在 positive prompt 中
                has_skirt_action = any(st in gen_res.positive for st in expected_skirt_actions)
                self.assertTrue(
                    has_skirt_action,
                    f"Skirt style {cid} has skirt capability, but no skirt action was retained in positive prompt!",
                )
                self.assertEqual(
                    len(dropped_skirt_decisions),
                    0,
                    f"Skirt style {cid} has skirt capability, but skirt action was unexpectedly dropped: {list(dropped_skirt_decisions.keys())}!",
                )
                # 核心验收契约：半身裙绝对不能冒用连衣裙词条 dress hitched up
                self.assertNotIn(
                    "dress hitched up",
                    gen_res.positive,
                    f"Half-skirt style {cid} must NOT carry dress action 'dress hitched up'!",
                )
                # 若采到了 dress hitched up，其必须以 state_lacks_carrier 记录 drop
                dropped_dress_decisions = {
                    d.before_text: d
                    for d in report.decisions
                    if d.action == "drop" and d.before_text == "dress hitched up"
                }
                for dt, d in dropped_dress_decisions.items():
                    self.assertEqual(
                        d.reason_code,
                        "state_lacks_carrier",
                        f"Half-skirt style {cid} dropped '{dt}' with unexpected reason {d.reason_code}",
                    )
            else:
                expected_reason = (
                    "pants_state_conflict" if "bottom_pants" in topos else "state_lacks_carrier"
                )
                test_terms = ("skirt pulled up revealing panties", "skirt hiked up to waist", "dress hitched up")
                dropped_skirt_decisions = {
                    d.before_text: d
                    for d in report.decisions
                    if d.action == "drop" and d.before_text in test_terms
                }
                for st in test_terms:
                    self.assertNotIn(
                        st,
                        gen_res.positive,
                        f"Non-skirt style {cid} lacks skirt capability, but action '{st}' leaked into positive prompt!",
                    )
                self.assertTrue(
                    len(dropped_skirt_decisions) >= 1,
                    f"Non-skirt style {cid} expected dropped skirt actions, but none found",
                )
                for st, dec in dropped_skirt_decisions.items():
                    self.assertEqual(
                        dec.reason_code,
                        expected_reason,
                        f"Non-skirt style {cid} dropped '{st}' with unexpected reason {dec.reason_code}, expected {expected_reason}",
                    )

    def test_05b_carrier_action_type_binding_isolation(self):
        """专项核验验收阻断项：下装禁止承载上装/连衣裙动作，且外来错误动作必须以 state_lacks_carrier 被 drop。

        测试场景 (固定 prompt_seed=42)：
        1. denim_shorts + 解扣：保留短裤解扣 (jeans unbuttoned / pants button undone)，
           绝无 buttons undone revealing cleavage / shirt open at chest；
           若将 buttons undone 注入消解器，必须以 state_lacks_carrier 被精准剔除；
        2. leather_skirt + 解扣：保留裙装解扣 (skirt button undone / skirt unbuttoned)，
           绝无 buttons undone revealing cleavage / shirt open at chest；
           若将 buttons undone 注入消解器，必须以 state_lacks_carrier 被精准剔除；
        3. miniskirt + 掀裙：保留半身裙掀起动作 (skirt pulled up revealing panties / skirt hiked up to waist)，
           绝无 dress hitched up；
           若将 dress hitched up 注入消解器，必须以 state_lacks_carrier 被精准剔除。
        """
        from tests.fixtures.conflict_rule_fixtures import make_test_atom

        # 1. denim_shorts + 解扣
        res_denim = self.generator.generate_structured(
            场景预设="日常街道",
            服装款式="denim_shorts",
            服装状态="解开纽扣 (Unbuttoned)",
            裸露等级="L2 差分微露 (Partially Exposed)",
            prompt_seed=42,
        )
        self.assertIn("pants button undone", res_denim.positive)
        self.assertIn("jeans unbuttoned", res_denim.positive)
        self.assertNotIn("buttons undone revealing cleavage", res_denim.positive)
        self.assertNotIn("shirt open at chest", res_denim.positive)

        # 独立消解器核验：上装开扣动作与 denim_shorts 强行组合时必须以 state_lacks_carrier drop
        atoms_denim = [
            make_test_atom("denim shorts", source_slot="clothing", item_id="denim_shorts", garment_topologies=["bottom_pants"], tag_order=0),
            make_test_atom("buttons undone revealing cleavage", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece"], garment_states=["opened"], tag_order=1),
            make_test_atom("shirt open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece"], garment_states=["opened"], tag_order=2),
        ]
        _, _, report_denim = self.resolver.resolve_atoms_with_full_report(atoms_denim)
        dropped_denim = {d.before_text: d.reason_code for d in report_denim.decisions if d.action == "drop"}
        self.assertEqual(dropped_denim.get("buttons undone revealing cleavage"), "state_lacks_carrier")
        self.assertEqual(dropped_denim.get("shirt open at chest"), "state_lacks_carrier")

        # 2. leather_skirt + 解扣
        res_leather = self.generator.generate_structured(
            场景预设="日常街道",
            服装款式="leather_skirt",
            服装状态="解开纽扣 (Unbuttoned)",
            裸露等级="L2 差分微露 (Partially Exposed)",
            prompt_seed=42,
        )
        self.assertIn("skirt button undone", res_leather.positive)
        self.assertIn("skirt unbuttoned", res_leather.positive)
        self.assertNotIn("buttons undone revealing cleavage", res_leather.positive)
        self.assertNotIn("shirt open at chest", res_leather.positive)

        # 独立消解器核验：上装开扣动作与 leather_skirt 强行组合时必须以 state_lacks_carrier drop
        atoms_leather = [
            make_test_atom("leather skirt", source_slot="clothing", item_id="leather_skirt", garment_topologies=["bottom_skirt"], tag_order=0),
            make_test_atom("buttons undone revealing cleavage", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece"], garment_states=["opened"], tag_order=1),
            make_test_atom("shirt open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece"], garment_states=["opened"], tag_order=2),
        ]
        _, _, report_leather = self.resolver.resolve_atoms_with_full_report(atoms_leather)
        dropped_leather = {d.before_text: d.reason_code for d in report_leather.decisions if d.action == "drop"}
        self.assertEqual(dropped_leather.get("buttons undone revealing cleavage"), "state_lacks_carrier")
        self.assertEqual(dropped_leather.get("shirt open at chest"), "state_lacks_carrier")

        # 3. miniskirt + 掀裙
        res_mini = self.generator.generate_structured(
            场景预设="日常街道",
            服装款式="miniskirt",
            服装状态="裙摆掀起 (Skirt Lifted Up)",
            裸露等级="L2 差分微露 (Partially Exposed)",
            prompt_seed=42,
        )
        self.assertIn("skirt pulled up revealing panties", res_mini.positive)
        self.assertNotIn("dress hitched up", res_mini.positive)
        dropped_mini_decisions = {
            d.before_text: d.reason_code
            for d in res_mini.resolution_report.decisions
            if d.action == "drop"
        }
        self.assertEqual(
            dropped_mini_decisions.get("dress hitched up"),
            "state_lacks_carrier",
            "miniskirt must drop sampled 'dress hitched up' with state_lacks_carrier",
        )

        # 独立消解器核验：连衣裙掀起动作与 miniskirt 强行组合时必须以 state_lacks_carrier drop
        atoms_mini = [
            make_test_atom("miniskirt", source_slot="clothing", item_id="miniskirt", garment_topologies=["bottom_skirt"], tag_order=0),
            make_test_atom("dress hitched up", source_slot="clothing_state", item_id="lifted_up", garment_topologies=["one_piece"], garment_states=["lifted"], tag_order=1),
        ]
        _, _, report_mini = self.resolver.resolve_atoms_with_full_report(atoms_mini)
        dropped_mini = {d.before_text: d.reason_code for d in report_mini.decisions if d.action == "drop"}
        self.assertEqual(dropped_mini.get("dress hitched up"), "state_lacks_carrier")

    def test_05c_outerwear_action_binding_isolation(self):
        """专项核验 Batch 3 外套解扣动作适配与语义承载物隔离 (解决原有 top/one_piece 拓扑无法承载外套解扣问题)：

        测试场景：
        1. lab_coat + 解扣：保留外套专属解扣动作 (unbuttoned coat / jacket open)，
           绝无 shirt open at chest / blouse unbuttoned；
        2. 独立消解器核验：当向 lab_coat (仅 outerwear 拓扑) 强行注入 shirt open at chest / blouse unbuttoned 时，
           必须精准被标记为 action == 'drop' 且 reason_code == 'state_lacks_carrier'，绝不冒用；
        3. 独立消解器核验：当向 lab_coat 强行注入 dress hitched up 时，必须以 state_lacks_carrier 被 drop；
        4. 显式目标绑定测试：当显式将衬衫动作绑定到纯外套时，判定为不兼容，不发生错误承载。
        """
        from tests.fixtures.conflict_rule_fixtures import make_test_atom

        # 1. lab_coat 端到端生成
        res_lab = self.generator.generate_structured(
            场景预设="日常街道",
            服装款式="lab_coat",
            服装状态="解开纽扣 (Unbuttoned)",
            裸露等级="L2 差分微露 (Partially Exposed)",
            prompt_seed=42,
        )
        self.assertIn("unbuttoned coat", res_lab.positive)
        self.assertIn("jacket open", res_lab.positive)
        self.assertNotIn("shirt open at chest", res_lab.positive)
        self.assertNotIn("blouse unbuttoned", res_lab.positive)
        self.assertNotIn("buttons undone revealing cleavage", res_lab.positive)

        # 2. 独立消解器核验：衬衫动作与 lab_coat 组合时必须以 state_lacks_carrier drop
        atoms_lab = [
            make_test_atom("lab coat", source_slot="clothing", item_id="lab_coat", garment_topologies=["outerwear"], tag_order=0),
            make_test_atom("jacket unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["outerwear"], garment_states=["opened"], tag_order=1),
            make_test_atom("shirt open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece"], garment_states=["opened"], tag_order=2),
            make_test_atom("blouse unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece"], garment_states=["opened"], tag_order=3),
            make_test_atom("dress hitched up", source_slot="clothing_state", item_id="lifted_up", garment_topologies=["one_piece"], garment_states=["lifted"], tag_order=4),
        ]
        _, _, report_lab = self.resolver.resolve_atoms_with_full_report(atoms_lab)
        dropped_lab = {d.before_text: d.reason_code for d in report_lab.decisions if d.action == "drop"}
        self.assertNotIn("jacket unbuttoned", dropped_lab)
        self.assertEqual(dropped_lab.get("shirt open at chest"), "state_lacks_carrier")
        self.assertEqual(dropped_lab.get("blouse unbuttoned"), "state_lacks_carrier")
        self.assertEqual(dropped_lab.get("dress hitched up"), "state_lacks_carrier")

        # 3. 显式目标绑定测试：当显式将衬衫动作指定给外套时，不兼容
        carrier_outer = GarmentCarrierEntity(
            entity_id="carrier_outer",
            selector="clothing",
            selected_id="lab_coat",
            member_atoms=[atoms_lab[0]],
        )
        compat = is_garment_compatible_with_state(carrier_outer, "unbuttoned", atoms_lab[2])
        self.assertFalse(compat, "lab_coat carrier must NOT be compatible with shirt open at chest atom")

        compat_outer_action = is_garment_compatible_with_state(carrier_outer, "unbuttoned", atoms_lab[1])
        self.assertTrue(compat_outer_action, "lab_coat carrier must be compatible with jacket unbuttoned atom")

    def test_06_end_to_end_generation_and_dag_provenance(self):
        """端到端验证 Batch 1 (16 款), Batch 2 (22 款), Batch 3 (23 款) 共 61 款新增款式在真实节点生成下的 Provenance DAG 完整性与零未消解冲突。"""
        from tests.test_rc8_quality_gate import _verify_provenance_dag

        all_items = [(i[0], i[1]) for i in BATCH_1_ITEMS] + [(i[0], i[1]) for i in BATCH_2_ITEMS] + [(i[0], i[1]) for i in BATCH_3_ITEMS]
        for cid, name_zh in all_items:
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
        5. 全变体可达性 (Reachability)：对 Batch 1 (16 款), Batch 2 (22 款), Batch 3 (23 款) 逐款多种子抽样，
           词库定义的每一个变体与属性均被真实采到，Batch 1 51 个叶子标签、Batch 2 67 个叶子标签、Batch 3 75 个叶子标签 (共 193 个) 100% 可达。
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

        def run_batch_checks(batch_items, expected_batch_tag_count, batch_name):
            all_seen_tags = set()
            for item in batch_items:
                cid = item[0]
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
                    f"Category {cid} in {batch_name} has unreachable tags in controlled sampling: {unreached}"
                )

            # 6. 全批可达性验证：该批声明的全部叶子标签必须全部被采到
            self.assertEqual(
                len(all_seen_tags),
                expected_batch_tag_count,
                f"Expected all {expected_batch_tag_count} leaf tags across {batch_name} to be reached, got {len(all_seen_tags)}"
            )
            return all_seen_tags

        b1_seen = run_batch_checks(BATCH_1_ITEMS, 51, "Batch 1")
        b2_seen = run_batch_checks(BATCH_2_ITEMS, 67, "Batch 2")
        b3_seen = run_batch_checks(BATCH_3_ITEMS, 75, "Batch 3")
        self.assertEqual(len(b1_seen | b2_seen | b3_seen), 193, "Combined Batch 1 + 2 + 3 reached leaf tags must be exactly 193")


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

