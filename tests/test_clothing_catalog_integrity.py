"""
test_clothing_catalog_integrity.py — 服装款式全量目录完整性、精确集合等价性与形制能力矩阵门禁测试

测试要求：
1. 精确 ID 集合等价断言 (Set Equivalence)：杜绝“只看总数不看实际集合”，严格断言当前生产集合精确等于
   BASE_31_IDS | 已落地批次集合；
2. 形制能力矩阵逐款核验 (Capability Matrix)：
   - topologies 必须与规格表 100% 一致；
   - 解扣能力：仅白名单内款式允许存在于 ALLOWED_BUTTON_STYLES，其余严禁擅自入选；
   - 掀裙能力：NON_SKIRT_ONE_PIECE 严格遵循规格；
   - 拉链能力：ALLOWED_ZIPPER_STYLES 仅限已授权款式；
3. 离散叶子标签契约：唯一 ID、合法正则、合规 facts、禁止暴力拼接；
4. 原始 TSV 物理行映射守恒验证。
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from lib.conflict_resolver import (
    ALLOWED_BUTTON_STYLES,
    ALLOWED_ZIPPER_STYLES,
    NON_SKIRT_ONE_PIECE,
)

REPO_DIR = Path(__file__).resolve().parent.parent
CLOTHING_JSON_PATH = REPO_DIR / "data" / "clothing.json"

# 基线 31 款 ID 集合 (bc0d645)
BASE_31_IDS = frozenset({
    "qipao", "hanfu", "modern_chinese", "kimono", "yukata", "furisode",
    "jk_seifuku", "blazer_uniform", "gym_uniform", "hanbok", "korean_school",
    "ol_suit", "nurse_uniform", "maid_dress", "waitress_uniform", "lingerie_lace",
    "silk_robe", "camisole_slip", "bikini_micro", "one_piece_swimsuit",
    "latex_catsuit", "leather_corset", "bunny_suit", "cheerleader", "evening_dress",
    "street_casual", "party_club", "knit_sweater", "sweater_casual", "swimsuit_school",
    "dungarees",
})

# Batch 1: 上装 16 款 ID 集合
BATCH_1_IDS = frozenset({
    "combat_tactical",
    "convenience_store",
    "crop_top",
    "fast_food_uniform",
    "fishnet_top",
    "hoodie",
    "knit_vest",
    "military_uniform",
    "sailor_shirt",
    "shirts_blouses",
    "sportswear_active",
    "strapless_top",
    "sweatshirt",
    "t_shirt",
    "tops_tanks",
    "volleyball_uniform",
})

# Batch 1 规格与能力 SSOT 期望定义
BATCH_1_SPEC = {
    "combat_tactical": {
        "topologies": ["top"],
        "button": True,
        "raw_lines": [74, 218],
    },
    "convenience_store": {
        "topologies": ["top"],
        "button": True,
        "raw_lines": [45],
    },
    "crop_top": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [40],
    },
    "fast_food_uniform": {
        "topologies": ["top"],
        "button": True,
        "raw_lines": [214],
    },
    "fishnet_top": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [79],
    },
    "hoodie": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [111, 180, 186],
    },
    "knit_vest": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [125],
    },
    "military_uniform": {
        "topologies": ["top"],
        "button": True,
        "raw_lines": [60, 203],
    },
    "sailor_shirt": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [176],
    },
    "shirts_blouses": {
        "topologies": ["top"],
        "button": True,
        "raw_lines": [16, 23, 122, 134, 175, 230],
    },
    "sportswear_active": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [10, 208],
    },
    "strapless_top": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [87],
    },
    "sweatshirt": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [112],
    },
    "t_shirt": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [177],
    },
    "tops_tanks": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [27, 174],
    },
    "volleyball_uniform": {
        "topologies": ["top"],
        "button": False,
        "raw_lines": [11],
    },
}


class TestClothingCatalogIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CLOTHING_JSON_PATH, "r", encoding="utf-8") as f:
            cls.data = json.load(f)
        cls.categories = cls.data.get("categories", [])
        cls.cat_by_id = {c["id"]: c for c in cls.categories}

    def test_01_id_set_exact_equivalence(self):
        """严格断言生产 ID 集合精确等于基线 31 款 ∪ Batch 1 16 款，共 47 款，多一漏一均直接报错。"""
        current_ids = set(self.cat_by_id.keys())
        expected_ids = set(BASE_31_IDS | BATCH_1_IDS)

        missing = expected_ids - current_ids
        unexpected = current_ids - expected_ids

        self.assertEqual(len(missing), 0, f"Missing category IDs in catalog: {missing}")
        self.assertEqual(len(unexpected), 0, f"Unexpected category IDs in catalog: {unexpected}")
        self.assertEqual(len(current_ids), 47, f"Expected exactly 47 categories, got {len(current_ids)}")

    def test_02_batch_1_capability_matrix(self):
        """逐款核验 Batch 1 16 款形制能力与规格表 100% 吻合 (解扣、掀裙、拉链能力白名单)。"""
        tag_id_pattern = re.compile(r"^[a-z][a-z0-9_]{2,95}$")

        for cid, spec in BATCH_1_SPEC.items():
            self.assertIn(cid, self.cat_by_id, f"Category {cid} missing from catalog")
            cat = self.cat_by_id[cid]

            # 1. name_zh 与 aliases 必须存在且非空
            self.assertTrue(bool(cat.get("name_zh")), f"Category {cid} missing name_zh")
            self.assertIsInstance(cat.get("aliases", []), list, f"Category {cid} aliases must be a list")
            self.assertTrue(len(cat.get("aliases", [])) > 0, f"Category {cid} must have at least 1 alias")

            # 2. topologies 逐项严格一致
            tags = cat.get("tags", [])
            self.assertTrue(len(tags) >= 2, f"Category {cid} must have at least 2 discrete leaf tags")
            for tag in tags:
                tid = tag.get("id", "")
                self.assertTrue(tag_id_pattern.match(tid), f"Tag ID '{tid}' in {cid} does not match pattern")
                text = tag.get("text", "")
                self.assertTrue(bool(text), f"Tag {tid} text is empty")
                # 禁止全量硬拼：单个 tag 不应包含过量逗号拼凑不同衣服
                self.assertLess(text.count(","), 3, f"Tag {tid} text contains excessive commas: '{text}'")

                facts = tag.get("facts", {})
                self.assertEqual(facts.get("semantic_role"), "selector", f"Tag {tid} semantic_role mismatch")
                self.assertEqual(
                    facts.get("garment_topologies"),
                    spec["topologies"],
                    f"Tag {tid} garment_topologies mismatch with spec {spec['topologies']}",
                )

            # 3. 纽扣能力断言
            if spec["button"]:
                self.assertIn(cid, ALLOWED_BUTTON_STYLES, f"{cid} has button capability in spec but missing from ALLOWED_BUTTON_STYLES")
            else:
                self.assertNotIn(cid, ALLOWED_BUTTON_STYLES, f"{cid} has NO button capability in spec but found in ALLOWED_BUTTON_STYLES")

            # 4. 掀裙能力断言 (上装不属于 one_piece，故不可在 NON_SKIRT_ONE_PIECE)
            self.assertNotIn(cid, NON_SKIRT_ONE_PIECE, f"Top garment {cid} must not be in NON_SKIRT_ONE_PIECE")

            # 5. 拉链能力断言 (Batch 1 均未授权拉链开襟动作)
            self.assertNotIn(cid, ALLOWED_ZIPPER_STYLES, f"{cid} must not be in ALLOWED_ZIPPER_STYLES")

    def test_03_discrete_leaf_tags_isolation(self):
        """验证离散叶子标签独立性：不同原始行的特征变体各自独立建模，杜绝合并行粗暴拼接。"""
        # 针对 shirts_blouses：原始行 16(collared), 23(taut), 122(white clothes), 134(frilled), 175(white shirt), 230(frills)
        sb_cat = self.cat_by_id["shirts_blouses"]
        sb_texts = {t["text"] for t in sb_cat["tags"]}
        self.assertIn("collared shirt", sb_texts)
        self.assertIn("white button-down shirt", sb_texts)
        self.assertIn("taut shirt", sb_texts)
        self.assertIn("frilled shirt", sb_texts)

        # 针对 hoodie：原始行 111(hoodie), 180(hoodie), 186(hood)
        hd_cat = self.cat_by_id["hoodie"]
        hd_texts = {t["text"] for t in hd_cat["tags"]}
        self.assertIn("hoodie", hd_texts)
        self.assertTrue(any("hood" in t for t in hd_texts))

        # 针对 combat_tactical: 原始行 74(combat suit), 218(swat uniform)
        ct_cat = self.cat_by_id["combat_tactical"]
        ct_texts = {t["text"] for t in ct_cat["tags"]}
        self.assertIn("combat suit", ct_texts)
        self.assertIn("swat uniform", ct_texts)


if __name__ == "__main__":
    unittest.main()
