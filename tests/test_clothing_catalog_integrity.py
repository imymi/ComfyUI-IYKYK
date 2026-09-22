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
LEDGER_PATH = REPO_DIR / "docs" / "data_migration" / "clothing_lexicon_migration_ledger.md"
RAW_TSV_PATH = REPO_DIR / "docs" / "data_migration" / "raw_clothing_input.tsv"

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

# Batch 2: 下装与内衣 22 款 ID 集合
BATCH_2_IDS = frozenset({
    "black_leggings",
    "denim_shorts",
    "hot_pants",
    "armored_skirt",
    "layered_skirt",
    "leather_skirt",
    "long_skirt",
    "microskirt",
    "miniskirt",
    "pencil_skirt",
    "pettiskirt",
    "plaid_skirt",
    "pleated_skirt",
    "pumpkin_skirt",
    "rain_skirt",
    "skirts_general",
    "suspender_skirt",
    "tutu_skirt",
    "waist_apron",
    "bikini_classic",
    "bikini_creative",
    "bikini_strappy",
})

# Batch 2 规格与能力 SSOT 期望定义
BATCH_2_SPEC = {
    "black_leggings": {"topologies": ["bottom_pants"], "button": False, "skirt": False, "raw_lines": [228]},
    "denim_shorts": {"topologies": ["bottom_pants"], "button": True, "skirt": False, "raw_lines": [223]},
    "hot_pants": {"topologies": ["bottom_pants"], "button": False, "skirt": False, "raw_lines": [225]},
    "armored_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [239]},
    "layered_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [147, 148]},
    "leather_skirt": {"topologies": ["bottom_skirt"], "button": True, "skirt": True, "raw_lines": [227]},
    "long_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [162]},
    "microskirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [170]},
    "miniskirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [143, 156]},
    "pencil_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [155, 226]},
    "pettiskirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [151]},
    "plaid_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [153, 238]},
    "pleated_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [142, 171, 224]},
    "pumpkin_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [126]},
    "rain_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [163]},
    "skirts_general": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [141]},
    "suspender_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [172]},
    "tutu_skirt": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [152]},
    "waist_apron": {"topologies": ["bottom_skirt"], "button": False, "skirt": True, "raw_lines": [150]},
    "bikini_classic": {"topologies": ["underwear"], "button": False, "skirt": False, "raw_lines": [1]},
    "bikini_creative": {"topologies": ["underwear"], "button": False, "skirt": False, "raw_lines": [68]},
    "bikini_strappy": {"topologies": ["underwear"], "button": False, "skirt": False, "raw_lines": [2, 3, 4]},
}

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

# Batch 3: 外套、西服套装与机甲装备 23 款 ID 集合
BATCH_3_IDS = frozenset({
    "berserker_armor",
    "business_suit",
    "denim_jacket",
    "down_jacket",
    "duffel_coat",
    "firefighter_gear",
    "knight_armor",
    "lab_coat",
    "leather_jacket",
    "mecha_exoskeleton",
    "mecha_power_armor",
    "military_overcoat",
    "outerwear_coat",
    "outerwear_jacket",
    "outerwear_overcoat",
    "rainwear_coat",
    "safari_jacket",
    "soft_shell_jacket",
    "tactical_vest",
    "tailcoat",
    "trench_coat",
    "windbreaker",
    "winter_parka",
})

# Batch 3 规格与能力 SSOT 期望定义
BATCH_3_SPEC = {
    "berserker_armor": {"topologies": ["outerwear"], "button": False, "skirt": False, "raw_lines": [242]},
    "business_suit": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [47, 53, 201, 202]},
    "denim_jacket": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [187]},
    "down_jacket": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [192]},
    "duffel_coat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [196]},
    "firefighter_gear": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [189]},
    "knight_armor": {"topologies": ["outerwear"], "button": False, "skirt": False, "raw_lines": [109, 240, 241]},
    "lab_coat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [44, 76, 191]},
    "leather_jacket": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [93, 184]},
    "mecha_exoskeleton": {"topologies": ["outerwear"], "button": False, "skirt": False, "raw_lines": [130, 136, 139, 140, 221]},
    "mecha_power_armor": {"topologies": ["outerwear"], "button": False, "skirt": False, "raw_lines": [114]},
    "military_overcoat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [133]},
    "outerwear_coat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [110]},
    "outerwear_jacket": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [183, 188]},
    "outerwear_overcoat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [83, 181, 195]},
    "rainwear_coat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [103, 220]},
    "safari_jacket": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [185, 234]},
    "soft_shell_jacket": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [128]},
    "tactical_vest": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [94, 193, 194]},
    "tailcoat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [197]},
    "trench_coat": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [67, 86, 190]},
    "windbreaker": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [66, 115]},
    "winter_parka": {"topologies": ["outerwear"], "button": True, "skirt": False, "raw_lines": [88]},
}


class TestClothingCatalogIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CLOTHING_JSON_PATH, "r", encoding="utf-8") as f:
            cls.data = json.load(f)
        cls.categories = cls.data.get("categories", [])
        cls.cat_by_id = {c["id"]: c for c in cls.categories}

    def test_01_id_set_exact_equivalence(self):
        """严格断言生产 ID 集合精确等于基线 31 款 ∪ Batch 1 16 款 ∪ Batch 2 22 款 ∪ Batch 3 23 款，共 92 款，多一漏一均直接报错。"""
        current_ids = set(self.cat_by_id.keys())
        expected_ids = set(BASE_31_IDS | BATCH_1_IDS | BATCH_2_IDS | BATCH_3_IDS)

        missing = expected_ids - current_ids
        unexpected = current_ids - expected_ids

        self.assertEqual(len(missing), 0, f"Missing category IDs in catalog: {missing}")
        self.assertEqual(len(unexpected), 0, f"Unexpected category IDs in catalog: {unexpected}")
        self.assertEqual(len(current_ids), 92, f"Expected exactly 92 categories, got {len(current_ids)}")

    def test_02_capability_matrix(self):
        """逐款核验 Batch 1, Batch 2, Batch 3 共 61 款形制能力与规格表 100% 吻合 (解扣、掀裙、拉链能力白名单)。"""
        tag_id_pattern = re.compile(r"^[a-z][a-z0-9_]{2,95}$")

        combined_specs = {**BATCH_1_SPEC, **BATCH_2_SPEC, **BATCH_3_SPEC}
        for cid, spec in combined_specs.items():
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

            # 4. 掀裙能力断言
            if spec.get("skirt", False):
                self.assertNotIn(cid, NON_SKIRT_ONE_PIECE, f"{cid} must not be in NON_SKIRT_ONE_PIECE")
            else:
                if cid in ("mecha_power_armor", "mecha_exoskeleton"):
                    self.assertIn(cid, NON_SKIRT_ONE_PIECE, f"{cid} must be in NON_SKIRT_ONE_PIECE")
                else:
                    self.assertNotIn(cid, NON_SKIRT_ONE_PIECE, f"{cid} must not be in NON_SKIRT_ONE_PIECE")

            # 5. 拉链能力断言 (Batch 1, 2, 3 均未授权拉链开襟动作)
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

        # 针对 miniskirt: 原始行 143(miniskirt), 156(miniskirt)
        mini_cat = self.cat_by_id["miniskirt"]
        mini_texts = {t["text"] for t in mini_cat["tags"]}
        self.assertIn("miniskirt", mini_texts)
        self.assertIn("tight fitted miniskirt", mini_texts)
        self.assertIn("flared miniskirt", mini_texts)
        self.assertIn("high-waisted miniskirt", mini_texts)
        # 验证 tight 与 flared 处于同一互斥组
        t_tag = next(t for t in mini_cat["tags"] if t["text"] == "tight fitted miniskirt")
        f_tag = next(t for t in mini_cat["tags"] if t["text"] == "flared miniskirt")
        self.assertEqual(t_tag["mutex_group"], f_tag["mutex_group"], "tight and flared miniskirt must share the same silhouette mutex_group")

        # 针对 Batch 3: business_suit 与 tailcoat 严格保持 outerwear 拓扑
        for b3_cid in ("business_suit", "tailcoat"):
            b3_cat = self.cat_by_id[b3_cid]
            for tag in b3_cat["tags"]:
                self.assertEqual(tag["facts"]["garment_topologies"], ["outerwear"], f"{b3_cid} tag {tag['id']} must have topology ['outerwear']")

    def test_04_leaf_tag_count_and_metadata_integrity(self):
        """严格核验 Batch 1 (51), Batch 2 (67), Batch 3 (75) 共 193 个叶子标签及其元数据完整性 (role, mutex_group, raw_lines, derivation)。"""
        b1_tags = [t for cid in BATCH_1_IDS for t in self.cat_by_id[cid].get("tags", [])]
        b2_tags = [t for cid in BATCH_2_IDS for t in self.cat_by_id[cid].get("tags", [])]
        b3_tags = [t for cid in BATCH_3_IDS for t in self.cat_by_id[cid].get("tags", [])]
        self.assertEqual(len(b1_tags), 51, f"Expected exactly 51 leaf tags in Batch 1, got {len(b1_tags)}")
        self.assertEqual(len(b2_tags), 67, f"Expected exactly 67 leaf tags in Batch 2, got {len(b2_tags)}")
        self.assertEqual(len(b3_tags), 75, f"Expected exactly 75 leaf tags in Batch 3, got {len(b3_tags)}")

        mg_regex = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
        valid_roles = {"core_base", "variant", "combinable_attribute"}
        valid_derivations = {"verbatim", "derived", "product_extension"}

        for cid in sorted(BATCH_1_IDS | BATCH_2_IDS | BATCH_3_IDS):
            cat = self.cat_by_id[cid]
            tags = cat.get("tags", [])
            has_core = False
            for tag in tags:
                tid = tag.get("id", "")
                role = tag.get("role")
                mg = tag.get("mutex_group")
                raw_lines = tag.get("raw_lines")
                derivation = tag.get("derivation")
                derivation_note = tag.get("derivation_note")

                # 1. 角色合法性
                self.assertIn(role, valid_roles, f"Tag {tid} in {cid} has invalid role: {role}")
                if role == "core_base":
                    has_core = True

                # 2. 互斥组合法性且与 facts.mutex_groups 严格对齐
                self.assertIsInstance(mg, str, f"Tag {tid} in {cid} missing string mutex_group")
                self.assertTrue(bool(mg_regex.match(mg)), f"Tag {tid} in {cid} mutex_group '{mg}' invalid")
                facts_mgs = tag.get("facts", {}).get("mutex_groups", [])
                self.assertIn(mg, facts_mgs, f"Tag {tid} in {cid} mutex_group '{mg}' missing from facts.mutex_groups {facts_mgs}")

                # 3. 原始行号合法性
                self.assertIsInstance(raw_lines, list, f"Tag {tid} in {cid} raw_lines must be a list")
                self.assertTrue(len(raw_lines) > 0, f"Tag {tid} in {cid} raw_lines must not be empty")
                for r in raw_lines:
                    self.assertIsInstance(r, int, f"Tag {tid} in {cid} raw_lines item {r} must be int")
                    self.assertTrue(1 <= r <= 246, f"Tag {tid} in {cid} raw_lines item {r} out of range [1, 246]")

                # 4. 派生类型与扩写依据
                self.assertIn(derivation, valid_derivations, f"Tag {tid} in {cid} invalid derivation: {derivation}")
                if derivation in ("derived", "product_extension"):
                    self.assertTrue(
                        bool(derivation_note),
                        f"Tag {tid} in {cid} has derivation '{derivation}' but missing derivation_note"
                    )

            self.assertTrue(has_core, f"Category {cid} must have at least one 'core_base' tag")

    def test_05_bidirectional_raw_lines_ledger_mapping_and_merge_rationales(self):
        """双向核验原始 TSV 行与词库映射：
        1. 真实读取 TSV 原文，校验行号连续性与 100% 存在；
        2. 校验 verbatim 标签在 TSV 规范化原词中严格有据可查，杜绝凭空新增修饰；
        3. 台账迁移行 100% 覆盖 (Batch 1: 27 行 + Batch 2: 30 行 + Batch 3: 45 行 = 102 行)，标签引用行真实存在且归口精确，多对一归并依据充分。
        """
        self.assertTrue(LEDGER_PATH.exists(), f"Ledger file missing at {LEDGER_PATH}")
        self.assertTrue(RAW_TSV_PATH.exists(), f"TSV file missing at {RAW_TSV_PATH}")

        # 1. 真实读取并解析原始 TSV 输入文件
        tsv_lines = RAW_TSV_PATH.read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(tsv_lines), 1, "TSV file is empty")
        tsv_raw_by_line: dict[int, dict[str, str]] = {}
        for line in tsv_lines[1:]:
            parts = line.split("\t")
            if len(parts) >= 4 and parts[0].isdigit():
                lno = int(parts[0])
                tsv_raw_by_line[lno] = {
                    "cat": parts[1].strip(),
                    "zh": parts[2].strip(),
                    "en": parts[3].strip(),
                }
        self.assertEqual(len(tsv_raw_by_line), 246, f"Expected 246 TSV rows, got {len(tsv_raw_by_line)}")

        def get_allowed_verbatim_forms(raw_text: str) -> set[str]:
            forms = set()
            base = raw_text.strip().lower().replace("_", " ").rstrip(",;").strip()
            forms.add(base)
            for chunk in re.split(r"[,;]+", raw_text):
                c = chunk.strip().lower().replace("_", " ").rstrip(",;").strip()
                if c:
                    forms.add(c)
                    if c.endswith("s") and not c.endswith("ss"):
                        forms.add(c[:-1])
                    if "frillded" in c:
                        forms.add(c.replace("frillded", "frilled"))
            return forms

        # 2. 从迁移台账提取所有分配给 Batch 1, Batch 2, Batch 3 的原始行
        target_ids_scope = set(BATCH_1_IDS | BATCH_2_IDS | BATCH_3_IDS)
        ledger_text = LEDGER_PATH.read_text(encoding="utf-8")
        active_ledger_rows: dict[int, dict[str, str]] = {}
        for line in ledger_text.splitlines():
            line = line.strip()
            if not line.startswith("|") or line.startswith("| 行号") or line.startswith("|:--"):
                continue
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) < 13 or not cols[0].isdigit():
                continue
            row_no = int(cols[0])
            target_id = cols[8].strip("`")
            if target_id in target_ids_scope:
                active_ledger_rows[row_no] = {
                    "row_no": row_no,
                    "zh": cols[2],
                    "norm": cols[4].strip("`"),
                    "status": cols[5],
                    "target_id": target_id,
                    "reason": cols[12],
                }

        self.assertEqual(len(active_ledger_rows), 102, f"Expected 102 ledger rows targeting Batch 1, 2 & 3, got {len(active_ledger_rows)}")

        # 3. 方向一 (Ledger -> Tags): 台账中分配给 Batch 1 & 2 的每一行在词库中均有对应标签与来源行号标注
        catalog_rows_by_cid: dict[str, set[int]] = {cid: set() for cid in target_ids_scope}
        for cid in target_ids_scope:
            for tag in self.cat_by_id[cid].get("tags", []):
                for r in tag.get("raw_lines", []):
                    catalog_rows_by_cid[cid].add(r)

        for row_no, rdata in active_ledger_rows.items():
            cid = rdata["target_id"]
            self.assertIn(
                row_no,
                catalog_rows_by_cid[cid],
                f"Ledger row {row_no} ('{rdata['zh']}' -> {cid}) is NOT accounted for in catalog tags of {cid}!"
            )
            if rdata["status"] == "合并":
                self.assertTrue(
                    len(rdata["reason"]) >= 5,
                    f"Merged ledger row {row_no} missing substantial merge rationale: '{rdata['reason']}'"
                )

        # 4. 方向二 (Tags -> Ledger & TSV): 词库标签引用的每一个 raw_lines 行号真实存在且在台账中属于该款式
        for cid in target_ids_scope:
            cat = self.cat_by_id[cid]
            for tag in cat.get("tags", []):
                tid = tag["id"]
                tag_text = tag["text"].strip().lower()
                deriv = tag.get("derivation")
                raw_lines = tag.get("raw_lines", [])

                for r in raw_lines:
                    self.assertIn(r, tsv_raw_by_line, f"Tag {tid} in {cid} references nonexistent TSV row {r}!")
                    self.assertIn(
                        r,
                        active_ledger_rows,
                        f"Tag {tid} in {cid} references row {r}, which is not assigned to Batch 1 or 2 in the ledger!"
                    )
                    expected_cid = active_ledger_rows[r]["target_id"]
                    self.assertEqual(
                        cid,
                        expected_cid,
                        f"Tag {tid} in {cid} references row {r}, but that row belongs to {expected_cid} in the ledger!"
                    )

                # 5. 原文真伪校验：若标注为 verbatim，必须在引用行的规范化原文中严格匹配
                if deriv == "verbatim":
                    allowed_forms = set()
                    for r in raw_lines:
                        allowed_forms.update(get_allowed_verbatim_forms(tsv_raw_by_line[r]["en"]))
                    self.assertIn(
                        tag_text,
                        allowed_forms,
                        f"Tag {tid} ('{tag['text']}') in {cid} is marked verbatim but not found in allowed forms "
                        f"{allowed_forms} of raw lines {raw_lines}! If it includes derived details, mark as 'derived' or 'product_extension'."
                    )
                else:
                    self.assertTrue(
                        bool(tag.get("derivation_note")),
                        f"Tag {tid} in {cid} is '{deriv}' but missing derivation_note"
                    )


if __name__ == "__main__":
    unittest.main()

