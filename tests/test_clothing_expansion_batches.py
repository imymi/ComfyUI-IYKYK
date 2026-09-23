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
    BindingStatus,
    ConflictResolver,
    GarmentCarrierEntity,
    find_bound_carrier,
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

BATCH_4_ITEMS = [
    ("anime_cosplay", "不知火舞 (Mai Shiranui Cosplay)", ["mai_shiranui", "shiranui_mai_costume"], False, False, ["one_piece"]),
    ("apron_dress", "全身围裙 (Apron Dress)", ["pinafore_dress", "full_apron"], False, True, ["one_piece"]),
    ("armored_dress", "铠装连衣裙 (Armored Dress)", ["battle_dress_armor", "armored_gown"], False, True, ["one_piece"]),
    ("bathrobe", "浴袍 (Bathrobe)", ["spa_robe", "terrycloth_robe"], True, True, ["one_piece"]),
    ("battle_robe", "战袍 (Battle Robe)", ["combat_robe", "warrior_robe"], False, True, ["one_piece"]),
    ("clerical_nun", "修女服 (Nun Habit)", ["nun_habit", "convent_habit"], False, True, ["one_piece"]),
    ("clerical_priest", "神父修生黑袍 (Priest Cassock)", ["cassock", "priest_robe"], True, True, ["one_piece"]),
    ("festive_costume", "节日圣诞装 (Festive Santa Costume)", ["santa_costume", "christmas_outfit"], True, True, ["one_piece"]),
    ("frock_smock", "工装罩衫 (Frock / Smock)", ["artist_smock", "work_frock"], True, True, ["one_piece"]),
    ("greek_toga", "古希腊托加/佩普洛斯 (Greek Toga / Peplos)", ["peplos", "chiton"], False, True, ["one_piece"]),
    ("hospital_gown", "病号服 (Hospital Gown)", ["patient_gown", "medical_gown"], True, True, ["one_piece"]),
    ("leotard_bodysuit", "连体紧身衣 (Leotard Bodysuit)", ["dance_leotard", "gymnastics_bodysuit"], False, False, ["one_piece"]),
    ("racing_suit", "赛车连体服 (Racing Suit)", ["motorsport_suit", "driver_overalls"], True, False, ["one_piece"]),
    ("robe_general", "休闲长袍 (Casual Robe)", ["lounge_robe", "housecoat"], True, True, ["one_piece"]),
    ("shinto_miko", "神道巫女服 (Shinto Miko Attire)", ["miko_attire", "shrine_maiden_costume"], False, True, ["one_piece"]),
    ("slime_dress", "史莱姆凝胶装 (Slime Dress)", ["gel_dress", "translucent_slime_outfit"], False, True, ["one_piece"]),
    ("swimsuit_classic", "经典连体泳装 (Classic One-Piece Swimsuit)", ["classic_one_piece", "maillot"], False, False, ["one_piece"]),
    ("swimsuit_competition", "竞赛专业泳衣 (Competition Swimsuit)", ["racing_swimsuit", "fastskin_suit"], False, False, ["one_piece"]),
    ("swimsuit_creative", "中式改良死库水 (Chinese Style Sukumizu)", ["chinese_sukumizu", "qipao_swimsuit"], False, False, ["one_piece"]),
    ("taoist_robe", "传统道袍 (Taoist Robe / Daopao)", ["daopao", "taoist_vestment"], False, True, ["one_piece"]),
    ("witch_robe", "魔女法袍 (Witch Robe / Dress)", ["sorceress_robe", "witch_gown"], False, True, ["one_piece"]),
    ("wizard_robe", "巫师贤者法袍 (Wizard Archmage Robe)", ["mage_robe", "archmage_vestment"], False, True, ["one_piece"]),
    ("zentai_suit", "全身紧身连体衣 (Zentai Suit)", ["full_body_tights", "catsuit_zentai"], False, False, ["one_piece"]),
]


class TestClothingExpansionBatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sampler = DataSampler(DATA_DIR)
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.generator = nodes.IYKYKPromptGenerator()

    def test_01_batch_1_2_3_4_sampling_accessibility(self):
        """逐款验证 Batch 1 (16 款上装), Batch 2 (22 款下装与内衣), Batch 3 (23 款外套机甲), Batch 4 (23 款连衣裙/连体衣/制服) 按规范 ID、中文显示名和别名均可精确采样。"""
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

        for cid, name_zh, aliases, _, _, topos in BATCH_4_ITEMS:
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
        """验证 Batch 1 (16 款), Batch 2 (22 款), Batch 3 (23 款), Batch 4 (23 款) 共 84 款新增款式在随机模式 (随机 (Random)) 下均可真实被抽中。"""
        sampled_styles = set()
        rng = Random(2026)
        # 115 款均匀抽取，抽样 4000 次，所有 84 款新增款式必须 100% 被覆盖
        for _ in range(4000):
            res = self.sampler.sample_clothing_result("随机 (Random)", "无 (None)", "L1", rng)
            if res.style_id:
                sampled_styles.add(res.style_id)

        target_ids = {item[0] for item in BATCH_1_ITEMS} | {item[0] for item in BATCH_2_ITEMS} | {item[0] for item in BATCH_3_ITEMS} | {item[0] for item in BATCH_4_ITEMS}
        unreached = target_ids - sampled_styles
        self.assertEqual(len(unreached), 0, f"Styles never reached in random sampling: {unreached}")

    def test_03_discrete_tag_syntax_and_word_count(self):
        """验证 Batch 1, 2, 3, 4 共 84 款款式返回离散叶子标签，零语法破坏且词数处于合理受控范围。"""
        rng = Random(1234)
        all_items = (
            [(i[0], i[1]) for i in BATCH_1_ITEMS]
            + [(i[0], i[1]) for i in BATCH_2_ITEMS]
            + [(i[0], i[1]) for i in BATCH_3_ITEMS]
            + [(i[0], i[1]) for i in BATCH_4_ITEMS]
        )
        for cid, name_zh in all_items:
            res = self.sampler.sample_clothing_result(cid, "无 (None)", "L1", rng)
            for tag in res.base_tags:
                validate_prompt_syntax(tag.text)
                words = tag.text.split()
                self.assertLessEqual(len(words), 15, f"Tag in {cid} too long: '{tag.text}'")

    def test_04_button_capability_positive_and_negative_matrix(self):
        """形制能力契约核验 (解扣状态 unbuttoned):
        严格验证具体动作词条 (按服装形制拓扑区分动作) 真实生成或精准剔除：
        - 正例：具备纽扣能力的款式 (Batch 1: 5 款上装, Batch 2: denim_shorts, leather_skirt 2 款, Batch 3: 19 款外套, Batch 4: 7 款连体/长袍/套装 共 33 款)：
          * 上装正例：collar unbuttoned / front unbuttoned 保留在 positive prompt 中；
          * 裤装正例 (denim_shorts)：pants button undone / jeans unbuttoned 保留在 positive prompt 中，胸前/衬衫/外套/长袍动作严禁存在；
          * 裙装正例 (leather_skirt)：skirt button undone / skirt unbuttoned 保留在 positive prompt 中，胸前/衬衫/外套/长袍动作严禁存在；
          * 外套正例 (Batch 3 19 款)：unbuttoned coat / collar unbuttoned 保留在 positive prompt 中，衬衫/裤装/裙装/长袍动作严禁存在；
          * 连体正例 (Batch 4 7 款)：racing_suit 保留 suit unbuttoned at chest 与 collar unbuttoned；bathrobe 等长袍/工装保留 collar unbuttoned 且 suit 动作以 state_lacks_carrier drop；
          * 正例动作无相应 Drop 决策。
        - 反例：无纽扣能力的款式 (Batch 1: 11 款, Batch 2: 20 款, Batch 3: 4 款机甲重铠, Batch 4: 16 款非纽扣连体衣 共 51 款)：
          动作词条绝对不出现在 positive prompt 中，且决策报告中必须明确记录 action == "drop"。
        """
        all_items = (
            [(i[0], i[3], ["top"]) for i in BATCH_1_ITEMS]
            + [(i[0], i[3], i[5]) for i in BATCH_2_ITEMS]
            + [(i[0], i[3], i[5]) for i in BATCH_3_ITEMS]
            + [(i[0], i[3], i[5]) for i in BATCH_4_ITEMS]
        )

        ALL_BUTTON_ACTIONS = (
            "blouse unbuttoned", "buttons undone revealing cleavage", "shirt open at chest",
            "pants button undone", "jeans unbuttoned",
            "skirt button undone", "skirt unbuttoned",
            "jacket unbuttoned", "coat open", "jacket open", "unbuttoned coat",
            "robe open at chest", "robe unfastened", "suit unbuttoned at chest",
            "collar unbuttoned", "front unbuttoned",
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
                forbidden_cross_actions = (
                    "buttons undone revealing cleavage", "shirt open at chest", "blouse unbuttoned",
                    "skirt button undone", "skirt unbuttoned", "unbuttoned coat", "jacket open",
                    "jacket unbuttoned", "robe open at chest", "suit unbuttoned at chest",
                    "collar unbuttoned", "front unbuttoned"
                )
            elif "bottom_skirt" in topos:
                expected_target_actions = ("skirt button undone", "skirt unbuttoned")
                forbidden_cross_actions = (
                    "buttons undone revealing cleavage", "shirt open at chest", "blouse unbuttoned",
                    "pants button undone", "jeans unbuttoned", "unbuttoned coat", "jacket open",
                    "jacket unbuttoned", "robe open at chest", "suit unbuttoned at chest",
                    "collar unbuttoned", "front unbuttoned"
                )
            elif "outerwear" in topos:
                expected_target_actions = ("unbuttoned coat", "collar unbuttoned")
                forbidden_cross_actions = (
                    "buttons undone revealing cleavage", "shirt open at chest", "blouse unbuttoned",
                    "pants button undone", "jeans unbuttoned", "skirt button undone", "skirt unbuttoned",
                    "robe open at chest", "suit unbuttoned at chest"
                )
            elif "one_piece" in topos:
                expected_target_actions = ("collar unbuttoned",)
                if cid == "racing_suit":
                    forbidden_cross_actions = (
                        "blouse unbuttoned", "shirt open at chest", "pants button undone",
                        "jeans unbuttoned", "skirt button undone", "skirt unbuttoned",
                        "unbuttoned coat", "jacket open"
                    )
                else:
                    expected_target_actions = ("collar unbuttoned",)
                    forbidden_cross_actions = (
                        "blouse unbuttoned", "shirt open at chest", "suit unbuttoned at chest",
                        "pants button undone", "jeans unbuttoned", "skirt button undone",
                        "skirt unbuttoned", "unbuttoned coat", "jacket open"
                    )
            else:
                expected_target_actions = ("collar unbuttoned", "front unbuttoned")
                forbidden_cross_actions = (
                    "pants button undone", "jeans unbuttoned", "skirt button undone",
                    "skirt unbuttoned", "unbuttoned coat", "jacket open", "jacket unbuttoned",
                    "robe open at chest", "suit unbuttoned at chest"
                )

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
                if "one_piece" in topos and cid != "racing_suit":
                    # 独立消解器核验：若非赛车服连体衣被强行注入赛车服专属解扣动作，消解器必须以 state_lacks_carrier 剔除
                    from tests.fixtures.conflict_rule_fixtures import make_test_atom
                    atoms_suit_test = [
                        make_test_atom(cid, source_slot="clothing", item_id=cid, garment_topologies=["one_piece"], tag_order=0),
                        make_test_atom("suit unbuttoned at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["one_piece"], garment_states=["opened"], tag_order=1),
                    ]
                    _, _, rep_suit = self.resolver.resolve_atoms_with_full_report(atoms_suit_test)
                    drop_suit = {d.before_text: d.reason_code for d in rep_suit.decisions if d.action == "drop"}
                    self.assertEqual(
                        drop_suit.get("suit unbuttoned at chest"),
                        "state_lacks_carrier",
                        f"Style {cid} expected 'suit unbuttoned at chest' to be dropped by resolver with state_lacks_carrier",
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
                expected_drop_reason = (
                    "one_piece_state_conflict" if "one_piece" in topos else "state_lacks_carrier"
                )
                for bt, d in dropped_all_btn.items():
                    self.assertEqual(
                        d.reason_code,
                        expected_drop_reason,
                        f"Style {cid} dropped '{bt}' with unexpected reason {d.reason_code}, expected {expected_drop_reason}",
                    )

    def test_04b_button_candidate_resolution_consistency_and_seed_4_regression(self):
        """核验 P2 阻断项修复：采样候选形制兼容性与消解器完全一致，Random(4) 严禁丢光解扣动作。

        验收要求：
        1. 采样候选应使用与消解器一致的具体动作兼容判定，不能仅比较拓扑；
        2. 对七款授权解扣款式 (festive_costume, frock_smock, racing_suit, bathrobe, clerical_priest, hospital_gown, robe_general)，
           在没有镜头等其他排他因素时，保证至少能抽中并保留一个合法解扣动作，且不得出现“只有错误动作、被消解器全数剔除导致正向提示词缺少解扣表达”；
        3. 严禁款式冒用不匹配的细化动作（如 festive_costume / frock_smock 抽到 robe 动作，或普通长袍抽到 suit 动作）；
        4. 覆盖 Random(4) 精确复现种子与 0..20 种子多轮扫描。
        """
        target_styles = [
            ("festive_costume", "节日圣诞装 (Festive Santa Costume)"),
            ("frock_smock", "工装罩衫 (Frock / Smock)"),
            ("racing_suit", "赛车连体服 (Racing Suit)"),
            ("bathrobe", "浴袍 (Bathrobe)"),
            ("clerical_priest", "神父修生黑袍 (Priest Cassock)"),
            ("hospital_gown", "病号服 (Hospital Gown)"),
            ("robe_general", "休闲长袍 (Casual Robe)"),
        ]

        # 1. 直接核验采样器候选与绑定结果 (精准复现 Random(4) 场景)
        for cid, name_zh in target_styles:
            rng = Random(4)
            sample_res = self.sampler.sample_clothing_result(
                style=cid,
                state="解开纽扣 (Unbuttoned)",
                nudity_level_code="L2",
                rng=rng,
            )
            state_texts = [t.text for t in sample_res.state_tags]
            self.assertGreater(
                len(state_texts),
                0,
                f"Style {cid} must sample at least 1 state tag under Random(4)",
            )
            if cid in ("festive_costume", "frock_smock", "racing_suit"):
                for t in state_texts:
                    self.assertNotIn(
                        "robe",
                        t.lower(),
                        f"Style {cid} must NOT sample robe-specific action '{t}' under Random(4)!",
                    )
            if cid != "racing_suit":
                for t in state_texts:
                    self.assertNotIn(
                        "suit unbuttoned",
                        t.lower(),
                        f"Style {cid} must NOT sample suit-specific action '{t}' under Random(4)!",
                    )

        # 2. 端到端生成节点核验：Random(4) 保证正向提示词具备解扣动作
        for cid, name_zh in target_styles:
            gen_res = self.generator.generate_structured(
                场景预设="日常街道",
                剧情主题="随机 (Random)",
                服装款式=name_zh,
                服装状态="解开纽扣 (Unbuttoned)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                画质等级="高清写真 (High)",
                prompt_seed=4,
            )
            pos = gen_res.positive
            has_btn = any(w in pos.lower() for w in ("unbutton", "open at chest", "unfastened"))
            self.assertTrue(
                has_btn,
                f"Style {cid} must retain at least one unbuttoned action under seed=4! Positive prompt: {pos}",
            )
            if cid in ("festive_costume", "frock_smock", "racing_suit"):
                self.assertNotIn(
                    "robe",
                    pos.lower(),
                    f"Style {cid} must NOT output robe action under seed=4! Positive: {pos}",
                )
            if cid != "racing_suit":
                self.assertNotIn(
                    "suit unbuttoned",
                    pos.lower(),
                    f"Style {cid} must NOT output suit action under seed=4! Positive: {pos}",
                )

        # 3. 0..20 多种子扫描：保证 100% 具备解扣动作且无不兼容细化动作泄漏
        for s in range(21):
            for cid, name_zh in target_styles:
                gen_res = self.generator.generate_structured(
                    场景预设="日常街道",
                    剧情主题="随机 (Random)",
                    服装款式=name_zh,
                    服装状态="解开纽扣 (Unbuttoned)",
                    裸露等级="L2 差分微露 (Partially Exposed)",
                    画质等级="高清写真 (High)",
                    prompt_seed=s,
                )
                pos = gen_res.positive
                has_btn = any(w in pos.lower() for w in ("unbutton", "open at chest", "unfastened"))
                self.assertTrue(
                    has_btn,
                    f"Style {cid} lost button action under seed={s}! Positive: {pos}",
                )

    def test_05_skirt_capability_positive_and_negative_matrix(self):
        """形制能力契约核验 (掀裙状态 lifted_up):
        严格验证具体动作词条 (skirt pulled up revealing panties / skirt hiked up to waist) 真实生成或精准剔除：
        - 正例：具备裙装能力的款式 (Batch 2 的 16 款半身裙, Batch 4 的 16 款裙装/长袍 共 32 款)：
          * 半身裙动作 (skirt pulled up revealing panties, skirt hiked up to waist) 100% 保留在 positive prompt 中；
          * 半身裙绝对禁止冒用/泄漏连衣裙动作 (dress hitched up)；
          * 连衣裙/长袍正例具备完整裙装掀起能力；
          * 决策报告中无合法裙装动作的 Drop；
        - 反例：无裙装能力的款式 (Batch 1 的 16 款上装、Batch 2 的 3 款裤装及 3 款比基尼内衣、Batch 3 的 23 款外套机甲、Batch 4 的 7 款非裙装连体衣 共 52 款)：
          动作词条绝对不出现在 positive prompt 中，且决策报告中必须明确记录 action == "drop"；
          其中裤装冲突原因严格为 pants_state_conflict，非裙装连体衣冲突原因严格为 one_piece_state_conflict，其他标记 state_lacks_carrier。
        """
        all_items = (
            [(i[0], False, ["top"]) for i in BATCH_1_ITEMS]
            + [(i[0], i[4], i[5]) for i in BATCH_2_ITEMS]
            + [(i[0], i[4], i[5]) for i in BATCH_3_ITEMS]
            + [(i[0], i[4], i[5]) for i in BATCH_4_ITEMS]
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
                if "bottom_skirt" in topos:
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
                    "pants_state_conflict" if "bottom_pants" in topos else (
                        "one_piece_state_conflict" if "one_piece" in topos else "state_lacks_carrier"
                    )
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
        1. lab_coat + 解扣：保留外套专属解扣动作 (unbuttoned coat / collar unbuttoned)，
           绝无 shirt open at chest / blouse unbuttoned；
        2. 独立消解器核验：当向 lab_coat (仅 outerwear 拓扑) 强行注入 shirt open at chest / blouse unbuttoned 时，
           必须精准被标记为 action == 'drop' 且 reason_code == 'state_lacks_carrier'，绝不冒用；
        3. 独立消解器核验：当向 lab_coat 强行注入 dress hitched up 时，必须以 state_lacks_carrier 被 drop；
        4. 显式目标与多承载物隔离长期回归测试：
           当同时存在外套 (lab_coat) 和合法衬衫 (shirts_blouses) 时，
           将衬衫解扣动作 (shirt open at chest) 的 target_id 显式指向外套 (lab_coat)，
           find_bound_carrier 必须返回 UNBOUND_INCOMPATIBLE，绝不回退改绑到衬衫；
           且在全量消解器中，该动作必须以 state_lacks_carrier 记录 drop。
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
        self.assertIn("collar unbuttoned", res_lab.positive)
        self.assertNotIn("shirt open at chest", res_lab.positive)
        self.assertNotIn("blouse unbuttoned", res_lab.positive)
        self.assertNotIn("buttons undone revealing cleavage", res_lab.positive)

        # 2. 独立消解器核验：衬衫动作与 lab_coat 组合时必须以 state_lacks_carrier drop
        atoms_lab = [
            make_test_atom("lab coat", source_slot="clothing", item_id="lab_coat", garment_topologies=["outerwear"], tag_order=0),
            make_test_atom("jacket unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["outerwear"], garment_states=["opened"], tag_order=1),
            make_test_atom("shirt open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top"], garment_states=["opened"], tag_order=2),
            make_test_atom("blouse unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top"], garment_states=["opened"], tag_order=3),
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

        # 4. 多承载物长期回归：同时存在外套与合法衬衫，显式指定外套为目标绝不回退改绑
        atom_shirt_carrier = make_test_atom("white collared shirt", source_slot="clothing", item_id="shirts_blouses", garment_topologies=["top"], tag_order=5)
        carrier_shirt = GarmentCarrierEntity(
            entity_id="carrier_shirt",
            selector="clothing",
            selected_id="shirts_blouses",
            member_atoms=[atom_shirt_carrier],
        )
        atom_shirt_action_targeted_to_outer = make_test_atom(
            "shirt open at chest",
            source_slot="clothing_state",
            item_id="unbuttoned",
            garment_topologies=["top"],
            garment_states=["opened"],
            target_id="lab_coat",
            tag_order=6,
        )
        binding_multi = find_bound_carrier(atom_shirt_action_targeted_to_outer, [carrier_outer, carrier_shirt])
        self.assertEqual(
            binding_multi.status,
            BindingStatus.UNBOUND_INCOMPATIBLE,
            "Targeting shirt action to outerwear must return UNBOUND_INCOMPATIBLE even when a compatible shirt carrier exists",
        )
        self.assertNotEqual(
            binding_multi.target_entity,
            carrier_shirt,
            "Must NOT fallback rebind to compatible shirt when explicit target_id was specified",
        )

        multi_atoms = [atoms_lab[0], atom_shirt_carrier, atom_shirt_action_targeted_to_outer]
        _, _, report_multi = self.resolver.resolve_atoms_with_full_report(multi_atoms)
        dropped_multi = {d.before_text: d.reason_code for d in report_multi.decisions if d.action == "drop"}
        self.assertEqual(
            dropped_multi.get("shirt open at chest"),
            "state_lacks_carrier",
            "Explicitly mis-targeted shirt action must be dropped with state_lacks_carrier without fallback",
        )

    def test_05d_one_piece_action_binding_isolation(self):
        """专项核验 Batch 4 连体衣/长袍/制服动作语义约束与隔离 (one_piece 不能自动等同于衬衫)：

        测试场景：
        1. 浴袍 (bathrobe)：正例承载 collar unbuttoned / robe open at chest，
           绝不承载 shirt open at chest / blouse unbuttoned / suit unbuttoned at chest；
        2. 赛车连体服 (racing_suit)：正例承载 suit unbuttoned at chest / collar unbuttoned，
           绝不承载 shirt open at chest / robe open at chest；
        3. 显式目标与多承载物隔离长期回归测试：
           当同时存在连体长袍 (bathrobe) 和合法衬衫 (shirts_blouses) 时，
           将衬衫解扣动作 (shirt open at chest) 的 target_id 显式指向 bathrobe，
           find_bound_carrier 必须返回 UNBOUND_INCOMPATIBLE，绝不回退改绑到衬衫；
           且在全量消解器中，该动作必须以 state_lacks_carrier 记录 drop。
        """
        from tests.fixtures.conflict_rule_fixtures import make_test_atom

        # 1. bathrobe 独立消解器核验
        atom_bathrobe = make_test_atom("bathrobe", source_slot="clothing", item_id="bathrobe", garment_topologies=["one_piece"], tag_order=0)
        atoms_bathrobe = [
            atom_bathrobe,
            make_test_atom("robe open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["one_piece"], garment_states=["opened"], tag_order=1),
            make_test_atom("collar unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece", "outerwear"], garment_states=["opened"], tag_order=2),
            make_test_atom("shirt open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top"], garment_states=["opened"], tag_order=3),
            make_test_atom("blouse unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top"], garment_states=["opened"], tag_order=4),
            make_test_atom("suit unbuttoned at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["one_piece"], garment_states=["opened"], tag_order=5),
        ]
        _, _, rep_bathrobe = self.resolver.resolve_atoms_with_full_report(atoms_bathrobe)
        dropped_bathrobe = {d.before_text: d.reason_code for d in rep_bathrobe.decisions if d.action == "drop"}
        self.assertNotIn("robe open at chest", dropped_bathrobe)
        self.assertNotIn("collar unbuttoned", dropped_bathrobe)
        self.assertEqual(dropped_bathrobe.get("shirt open at chest"), "state_lacks_carrier")
        self.assertEqual(dropped_bathrobe.get("blouse unbuttoned"), "state_lacks_carrier")
        self.assertEqual(dropped_bathrobe.get("suit unbuttoned at chest"), "state_lacks_carrier")

        # 2. racing_suit 独立消解器核验
        atom_racing = make_test_atom("racing suit", source_slot="clothing", item_id="racing_suit", garment_topologies=["one_piece"], tag_order=0)
        atoms_racing = [
            atom_racing,
            make_test_atom("suit unbuttoned at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["one_piece"], garment_states=["opened"], tag_order=1),
            make_test_atom("collar unbuttoned", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top", "one_piece", "outerwear"], garment_states=["opened"], tag_order=2),
            make_test_atom("shirt open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["top"], garment_states=["opened"], tag_order=3),
            make_test_atom("robe open at chest", source_slot="clothing_state", item_id="unbuttoned", garment_topologies=["one_piece"], garment_states=["opened"], tag_order=4),
        ]
        _, _, rep_racing = self.resolver.resolve_atoms_with_full_report(atoms_racing)
        dropped_racing = {d.before_text: d.reason_code for d in rep_racing.decisions if d.action == "drop"}
        self.assertNotIn("suit unbuttoned at chest", dropped_racing)
        self.assertNotIn("collar unbuttoned", dropped_racing)
        self.assertEqual(dropped_racing.get("shirt open at chest"), "state_lacks_carrier")
        self.assertEqual(dropped_racing.get("robe open at chest"), "state_lacks_carrier")

        # 3. 多承载物长期回归：同时存在浴袍与合法衬衫，显式指定浴袍为目标绝不回退改绑
        carrier_bathrobe = GarmentCarrierEntity(
            entity_id="carrier_bathrobe",
            selector="clothing",
            selected_id="bathrobe",
            member_atoms=[atom_bathrobe],
        )
        atom_shirt_carrier2 = make_test_atom("white collared shirt", source_slot="clothing", item_id="shirts_blouses", garment_topologies=["top"], tag_order=5)
        carrier_shirt2 = GarmentCarrierEntity(
            entity_id="carrier_shirt",
            selector="clothing",
            selected_id="shirts_blouses",
            member_atoms=[atom_shirt_carrier2],
        )
        atom_shirt_action_targeted_to_robe = make_test_atom(
            "shirt open at chest",
            source_slot="clothing_state",
            item_id="unbuttoned",
            garment_topologies=["top"],
            garment_states=["opened"],
            target_id="bathrobe",
            tag_order=6,
        )
        binding_robe_multi = find_bound_carrier(atom_shirt_action_targeted_to_robe, [carrier_bathrobe, carrier_shirt2])
        self.assertEqual(
            binding_robe_multi.status,
            BindingStatus.UNBOUND_INCOMPATIBLE,
            "Targeting shirt action to bathrobe must return UNBOUND_INCOMPATIBLE even when a compatible shirt carrier exists",
        )
        self.assertNotEqual(
            binding_robe_multi.target_entity,
            carrier_shirt2,
            "Must NOT fallback rebind to compatible shirt when explicit target_id was specified",
        )

        multi_atoms_robe = [atom_bathrobe, atom_shirt_carrier2, atom_shirt_action_targeted_to_robe]
        _, _, report_robe_multi = self.resolver.resolve_atoms_with_full_report(multi_atoms_robe)
        dropped_robe_multi = {d.before_text: d.reason_code for d in report_robe_multi.decisions if d.action == "drop"}
        self.assertEqual(
            dropped_robe_multi.get("shirt open at chest"),
            "state_lacks_carrier",
            "Explicitly mis-targeted shirt action must be dropped with state_lacks_carrier without fallback",
        )

    def test_06_end_to_end_generation_and_dag_provenance(self):
        """端到端验证 Batch 1 (16 款), Batch 2 (22 款), Batch 3 (23 款), Batch 4 (23 款) 共 84 款新增款式在真实节点生成下的 Provenance DAG 完整性与零未消解冲突。"""
        from tests.test_rc8_quality_gate import _verify_provenance_dag

        all_items = (
            [(i[0], i[1]) for i in BATCH_1_ITEMS]
            + [(i[0], i[1]) for i in BATCH_2_ITEMS]
            + [(i[0], i[1]) for i in BATCH_3_ITEMS]
            + [(i[0], i[1]) for i in BATCH_4_ITEMS]
        )
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
        5. 全变体可达性 (Reachability)：对 Batch 1 (16 款), Batch 2 (22 款), Batch 3 (23 款), Batch 4 (23 款) 逐款多种子抽样，
           词库定义的每一个变体与属性均被真实采到，四个新增批次累计269个叶子标签 100% 可达。
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
        b4_seen = run_batch_checks(BATCH_4_ITEMS, 76, "Batch 4")
        self.assertEqual(
            len(b1_seen | b2_seen | b3_seen | b4_seen),
            269,
            "四个新增批次累计269个叶子标签 must be exactly 269",
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

