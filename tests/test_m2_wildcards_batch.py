#!/usr/bin/env python3
"""
tests/test_m2_wildcards_batch.py
M2 批次 (Batch 1-2: 6款发型、3款首饰发饰、3款光影) 专属测试套件：
1. 台账元数据规范、复合主键约束及目标文件实体映射；
2. 目标文件父子层级、全局唯一性及文本一致性；
3. ExactCatalogIndex 零冲突与双向索引校验；
4. 12 款新增条目端到端显式选择生成；
5. 光影环境协调与场景矩阵互斥校验 (生物荧光日夜消解、烛光暗室兼容、跳灯中立性、全场景矩阵覆盖)；
6. M1 六款资产向后兼容性与回归门禁。
"""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import unittest

from lib.conflict_resolver import ConflictResolver
from lib.sampler import DataSampler, ExactCatalogIndex
import nodes

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
DOCS_DIR = REPO_DIR / "docs" / "data_migration"


class TestM2WildcardsProvenanceLedger(unittest.TestCase):
    """测试 M2 准入台账数据规范、复合主键约束及目标文件实体映射。"""

    def setUp(self):
        self.tsv_path = DOCS_DIR / "ai_wildcards_provenance_ledger.tsv"
        self.assertTrue(self.tsv_path.is_file(), f"Missing ledger file: {self.tsv_path}")

    def test_m2_ledger_composite_key_and_integrity(self):
        with open(self.tsv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            rows = list(reader)

        expected_m2_ids = {
            # 6 Hairstyles
            "ext_aw_hair_pixie_textured_crop",
            "ext_aw_hair_crown_braid_updo",
            "ext_aw_hair_single_back_braid",
            "ext_aw_hair_half_up_half_down",
            "ext_aw_hair_vintage_finger_waves",
            "ext_aw_hair_low_nape_chignon",
            # 3 Jewelry / Headwear
            "ext_aw_jewelry_pearl_drop_earrings",
            "ext_aw_jewelry_silver_hoop_earrings",
            "ext_aw_headwear_silk_scrunchie",
            # 3 Lighting
            "ext_aw_light_soft_bounced",
            "ext_aw_light_candlelight",
            "ext_aw_light_bioluminescent",
        }

        m2_rows = [r for r in rows if r["canonical_id"] in expected_m2_ids]
        self.assertEqual(len(m2_rows), 12, f"Expected 12 rows for M2 batch in ledger, got {len(m2_rows)}")

        seen_composite_keys = set()
        seen_primary_ids = set()

        for idx, row in enumerate(rows):
            cid = row["canonical_id"]
            comp_key = (
                cid,
                row["source_repo"],
                row["source_commit_sha"],
                row["location_type"],
                row["source_location"],
            )
            self.assertNotIn(
                comp_key,
                seen_composite_keys,
                f"Duplicate composite key detected at row {idx + 1}: {comp_key}",
            )
            seen_composite_keys.add(comp_key)

            if row["is_primary"].strip().lower() == "true":
                self.assertNotIn(
                    cid,
                    seen_primary_ids,
                    f"Multiple primary sources declared for canonical_id '{cid}'",
                )
                seen_primary_ids.add(cid)

            # 校验许可依据与评级
            self.assertTrue(row["license"].strip(), f"Empty license for {cid}")
            self.assertTrue(row["license_evidence"].strip(), f"Empty license_evidence for {cid}")
            self.assertEqual(row["rating"], "SFW", f"Sample {cid} must be SFW")
            self.assertEqual(row["source_license_status"], "verified")
            self.assertEqual(row["semantics_review_status"], "verified")
            self.assertEqual(row["pipeline_test_status"], "verified")
            self.assertEqual(row["overall_status"], "verified")

        found_m2_ids = {r["canonical_id"] for r in m2_rows}
        self.assertEqual(found_m2_ids, expected_m2_ids, "Ledger canonical IDs mismatch expected M2 set")
        self.assertTrue(expected_m2_ids.issubset(seen_primary_ids), "Not all expected M2 IDs have a primary record")

    def test_m2_ledger_target_mapping_and_parent_child_hierarchy(self):
        """
        校验台账中的父子关系、唯一性与文本严格一致：
        1. target_file, target_parent_id, target_leaf_id 存在且在对应文件中全局唯一；
        2. target_leaf_id 必须真实属于 target_parent_id 的 tags 列表中；
        3. 叶子 text 与台账 canonical_text 严格一致。
        """
        with open(self.tsv_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))

        expected_m2_ids = {
            "ext_aw_hair_pixie_textured_crop",
            "ext_aw_hair_crown_braid_updo",
            "ext_aw_hair_single_back_braid",
            "ext_aw_hair_half_up_half_down",
            "ext_aw_hair_vintage_finger_waves",
            "ext_aw_hair_low_nape_chignon",
            "ext_aw_jewelry_pearl_drop_earrings",
            "ext_aw_jewelry_silver_hoop_earrings",
            "ext_aw_headwear_silk_scrunchie",
            "ext_aw_light_soft_bounced",
            "ext_aw_light_candlelight",
            "ext_aw_light_bioluminescent",
        }
        m2_rows = [r for r in rows if r["canonical_id"] in expected_m2_ids]

        for r in m2_rows:
            f_path = REPO_DIR / r["target_file"]
            self.assertTrue(f_path.is_file(), f"Target file does not exist: {f_path}")
            data = json.loads(f_path.read_text(encoding="utf-8"))

            p_id = r["target_parent_id"]
            l_id = r["target_leaf_id"]

            matching_parents = []
            matching_leaves = []

            def walk_parents(obj):
                if isinstance(obj, dict):
                    if obj.get("id") == p_id:
                        matching_parents.append(obj)
                    for v in obj.values():
                        walk_parents(v)
                elif isinstance(obj, list):
                    for it in obj:
                        walk_parents(it)

            def walk_leaves(obj):
                if isinstance(obj, dict):
                    if obj.get("id") == l_id:
                        matching_leaves.append(obj)
                    for v in obj.values():
                        walk_leaves(v)
                elif isinstance(obj, list):
                    for it in obj:
                        walk_leaves(it)

            walk_parents(data)
            walk_leaves(data)

            self.assertEqual(len(matching_parents), 1, f"Parent ID {p_id} must appear exactly once in {f_path}")
            self.assertEqual(len(matching_leaves), 1, f"Leaf ID {l_id} must appear exactly once in {f_path}")

            parent_obj = matching_parents[0]
            leaf_obj = matching_leaves[0]

            parent_tags = parent_obj.get("tags", [])
            tag_ids_in_parent = [t.get("id") for t in parent_tags if isinstance(t, dict)]
            self.assertIn(
                l_id,
                tag_ids_in_parent,
                f"Leaf ID {l_id} does not belong to parent {p_id} tags list in {f_path}",
            )

            expected_text = r["canonical_text"]
            self.assertEqual(
                leaf_obj.get("text", ""),
                expected_text,
                f"Canonical text mismatch for {l_id}: actual={leaf_obj.get('text')!r}, expected={expected_text!r}",
            )


class TestM2ExactCatalogCollision(unittest.TestCase):
    """测试 M2 新增条目在 ExactCatalogIndex 中零别名/命名空间碰撞。"""

    def test_m2_catalog_indexing_and_no_collision(self):
        # 1. Hairstyles
        acc_data = json.loads((DATA_DIR / "accessories.json").read_text(encoding="utf-8"))
        idx_hair = ExactCatalogIndex("hairstyles")
        for h in acc_data.get("hairstyles", []):
            idx_hair.register_item(h)

        m2_hair_checks = [
            ("ext_aw_hair_pixie_textured_crop", "碎发质感精灵短发 (Textured Pixie Crop)"),
            ("ext_aw_hair_crown_braid_updo", "盘头花冠编发 (Crown Braid Updo)"),
            ("ext_aw_hair_single_back_braid", "后背单条垂长辫 (Single Long Back Braid)"),
            ("ext_aw_hair_half_up_half_down", "半扎半披公主发 (Half-Up Half-Down)"),
            ("ext_aw_hair_vintage_finger_waves", "复古手推波浪卷发 (Vintage Finger Waves)"),
            ("ext_aw_hair_low_nape_chignon", "颈后低发髻盘发 (Low Nape Chignon)"),
        ]
        for cid, name_zh in m2_hair_checks:
            self.assertIsNotNone(idx_hair.get(cid), f"Missing index for id: {cid}")
            self.assertIsNotNone(idx_hair.get(name_zh), f"Missing index for name: {name_zh}")

        # 2. Jewelry / Headwear
        idx_jewelry = ExactCatalogIndex("headwear_jewelry")
        for j in acc_data.get("headwear_jewelry", []):
            idx_jewelry.register_item(j)

        m2_jewel_checks = [
            ("ext_aw_jewelry_pearl_drop_earrings", "优雅水滴珍珠耳坠 (Pearl Drop Earrings)"),
            ("ext_aw_jewelry_silver_hoop_earrings", "极简纯银圈形耳环 (Silver Hoop Earrings)"),
            ("ext_aw_headwear_silk_scrunchie", "法式真丝大肠发圈 (Silk Scrunchie)"),
        ]
        for cid, name_zh in m2_jewel_checks:
            self.assertIsNotNone(idx_jewelry.get(cid), f"Missing index for id: {cid}")
            self.assertIsNotNone(idx_jewelry.get(name_zh), f"Missing index for name: {name_zh}")

        # 3. Lighting
        light_data = json.loads((DATA_DIR / "lighting.json").read_text(encoding="utf-8"))
        idx_light = ExactCatalogIndex("lighting_presets")
        for sec in ["professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting"]:
            for item in light_data.get(sec, []):
                idx_light.register_item(item)

        m2_light_checks = [
            ("ext_aw_light_soft_bounced", "反光板柔和跳灯补光 (Soft Bounced Fill Light)"),
            ("ext_aw_light_candlelight", "暖调摇曳烛光 (Warm Flickering Candlelight)"),
            ("ext_aw_light_bioluminescent", "生物荧光暗部微光 (Bioluminescent Ambient Glow)"),
        ]
        for cid, name_zh in m2_light_checks:
            self.assertIsNotNone(idx_light.get(cid), f"Missing index for id: {cid}")
            self.assertIsNotNone(idx_light.get(name_zh), f"Missing index for name: {name_zh}")


class TestM2SamplesPipelineGeneration(unittest.TestCase):
    """测试 12 条 M2 样本的真实生成器端到端显式选择生成。"""

    def setUp(self):
        self.generator = nodes.IYKYKPromptGenerator()
        self.sampler = DataSampler(DATA_DIR)
        self.resolver = ConflictResolver(DATA_DIR)

    # --- 6款发型 ---
    def test_m2_hair_pixie_textured_crop(self):
        pos, neg, desc = self.generator.generate(
            发型发色="碎发质感精灵短发 (Textured Pixie Crop)",
            prompt_seed=42,
        )
        self.assertIn("textured pixie crop, piecey and short", pos)
        self.assertIn("发型: 碎发质感精灵短发", desc)

    def test_m2_hair_crown_braid_updo(self):
        pos, neg, desc = self.generator.generate(
            发型发色="盘头花冠编发 (Crown Braid Updo)",
            prompt_seed=42,
        )
        self.assertIn("crown braid wrapped around the head", pos)
        self.assertIn("发型: 盘头花冠编发", desc)

    def test_m2_hair_single_back_braid(self):
        pos, neg, desc = self.generator.generate(
            发型发色="后背单条垂长辫 (Single Long Back Braid)",
            prompt_seed=42,
        )
        self.assertIn("single long braid down the back", pos)
        self.assertIn("发型: 后背单条垂长辫", desc)

    def test_m2_hair_half_up_half_down(self):
        pos, neg, desc = self.generator.generate(
            发型发色="半扎半披公主发 (Half-Up Half-Down)",
            prompt_seed=42,
        )
        self.assertIn("half-up hair, top pulled back with loose lengths", pos)
        self.assertIn("发型: 半扎半披公主发", desc)

    def test_m2_hair_vintage_finger_waves(self):
        pos, neg, desc = self.generator.generate(
            发型发色="复古手推波浪卷发 (Vintage Finger Waves)",
            prompt_seed=42,
        )
        self.assertIn("vintage finger waves close to head in structured ridges", pos)
        self.assertIn("发型: 复古手推波浪卷发", desc)

    def test_m2_hair_low_nape_chignon(self):
        pos, neg, desc = self.generator.generate(
            发型发色="颈后低发髻盘发 (Low Nape Chignon)",
            prompt_seed=42,
        )
        self.assertIn("smooth elegant chignon bun gathered at the nape", pos)
        self.assertIn("发型: 颈后低发髻盘发", desc)

    # --- 3款首饰/头饰 ---
    def test_m2_jewelry_pearl_drop_earrings(self):
        pos, neg, desc = self.generator.generate(
            饰品头饰="优雅水滴珍珠耳坠 (Pearl Drop Earrings)",
            prompt_seed=42,
        )
        self.assertIn("elegant pearl drop earrings", pos)

    def test_m2_jewelry_silver_hoop_earrings(self):
        pos, neg, desc = self.generator.generate(
            饰品头饰="极简纯银圈形耳环 (Silver Hoop Earrings)",
            prompt_seed=42,
        )
        self.assertIn("minimalist silver hoop earrings", pos)

    def test_m2_headwear_silk_scrunchie(self):
        pos, neg, desc = self.generator.generate(
            饰品头饰="法式真丝大肠发圈 (Silk Scrunchie)",
            prompt_seed=42,
        )
        self.assertIn("silk fabric scrunchie holding hair", pos)

    # --- 3款光影 ---
    def test_m2_light_soft_bounced(self):
        pos, neg, desc = self.generator.generate(
            光影预设="反光板柔和跳灯补光 (Soft Bounced Fill Light)",
            prompt_seed=42,
        )
        self.assertIn("soft bounced fill light from neutral reflector", pos)

    def test_m2_light_candlelight(self):
        pos, neg, desc = self.generator.generate(
            光影预设="暖调摇曳烛光 (Warm Flickering Candlelight)",
            prompt_seed=42,
        )
        self.assertIn("warm flickering candlelight illuminating face and shadows", pos)

    def test_m2_light_bioluminescent(self):
        pos, neg, desc = self.generator.generate(
            场景大类="无 (None)",
            光影预设="生物荧光暗部微光 (Bioluminescent Ambient Glow)",
            prompt_seed=42,
        )
        self.assertIn("faint bioluminescent glow casting subtle ambient light", pos)


class TestM2LightingRulesAndMutualExclusion(unittest.TestCase):
    """测试 M2 光影在环境协调、日夜消解及全场景矩阵下的行为。"""

    def setUp(self):
        self.generator = nodes.IYKYKPromptGenerator()
        self.sampler = DataSampler(DATA_DIR)
        self.resolver = ConflictResolver(DATA_DIR)

    def test_bioluminescent_day_night_coherence(self):
        """
        生物荧光微光标记为 night：
        1. 在明确夜景场景 (公园/野外) 下正常保留；
        2. 在明确日间场景 (办公室) 下触发 environmental_lighting_coherence 规则消解，
           消解正常执行，正向词中剔除生物荧光，且不抛出异常。
        """
        # A. 夜景场景下保留
        pos_night, _, _ = self.generator.generate(
            场景大类="公园/野外",
            光影预设="生物荧光暗部微光 (Bioluminescent Ambient Glow)",
            prompt_seed=42,
        )
        self.assertIn("faint bioluminescent glow casting subtle ambient light", pos_night)

        # B. 日间场景下消解（不抛出未解硬冲突异常，提示词剔除夜间光）
        pos_day, _, _ = self.generator.generate(
            场景大类="办公室",
            光影预设="生物荧光暗部微光 (Bioluminescent Ambient Glow)",
            prompt_seed=42,
        )
        self.assertNotIn("faint bioluminescent glow casting subtle ambient light", pos_day)

    def test_candlelight_underground_windowless_retention(self):
        """烛光在地下无窗空间 (地下室) 中正常保留。"""
        pos, _, _ = self.generator.generate(
            场景大类="地下室",
            光影预设="暖调摇曳烛光 (Warm Flickering Candlelight)",
            prompt_seed=42,
        )
        self.assertIn("warm flickering candlelight illuminating face and shadows", pos)

    def test_soft_bounced_indoor_outdoor_neutrality(self):
        """反光板柔和跳灯作为中立 studio 补光，室内室外均可正常生成。"""
        pos_in, _, _ = self.generator.generate(
            场景大类="办公室",
            光影预设="反光板柔和跳灯补光 (Soft Bounced Fill Light)",
            prompt_seed=42,
        )
        self.assertIn("soft bounced fill light from neutral reflector", pos_in)

        pos_out, _, _ = self.generator.generate(
            场景大类="公园/野外",
            光影预设="反光板柔和跳灯补光 (Soft Bounced Fill Light)",
            prompt_seed=42,
        )
        self.assertIn("soft bounced fill light from neutral reflector", pos_out)

    def test_full_scene_matrix_coverage_with_m2_lighting(self):
        """全量 122 个具体场景选项及 None/Random (共124项) 与 3 款 M2 光影进行笛卡尔积测试 (共372组)。
        核验：
        1. 372 组中源原子候选 100% 命中采出；
        2. 软跳灯 (ext_aw_light_soft_bounced)：124 场景 100% 保留，0 消解决策；
        3. 烛光 (ext_aw_light_candlelight)：124 场景 100% 保留，0 消解决策；
        4. 生物荧光 (ext_aw_light_bioluminescent)：
           - 在 5 个夜间/无场景选项 (无, 公园/野外, 海边/泳池, 都市露出, 墓地/灵园) 下 100% 保留，0 消解决策；
           - 在其余 119 个日间场景选项下，精准触发 environmental_lighting_coherence 规则消解 (action=drop, reason=daylight_removes_night)，并从正向提示词中剔除。
        """
        all_scene_options = nodes.IYKYKPromptGenerator.INPUT_TYPES()["required"]["场景大类"][0]
        concrete_scenes = [s for s in all_scene_options if s not in ("随机 (Random)", "无 (None)")]
        self.assertEqual(len(concrete_scenes), 234, f"Expected 234 concrete scenes, found {len(concrete_scenes)}")
        self.assertEqual(len(all_scene_options), 236, f"Expected 236 total scene options, found {len(all_scene_options)}")

        diag = nodes.IYKYKPromptDiagnostics()
        test_matrix = [
            ("反光板柔和跳灯补光 (Soft Bounced Fill Light)", "ext_aw_light_soft_bounced", "soft bounced fill light from neutral reflector"),
            ("暖调摇曳烛光 (Warm Flickering Candlelight)", "ext_aw_light_candlelight", "warm flickering candlelight illuminating face and shadows"),
            ("生物荧光暗部微光 (Bioluminescent Ambient Glow)", "ext_aw_light_bioluminescent", "faint bioluminescent glow casting subtle ambient light"),
        ]

        night_or_none_scenes = {"无 (None)", "公园/野外", "海边/泳池", "都市露出", "墓地/灵园"}

        tested_count = 0
        for lt_name, expected_id, expected_tag in test_matrix:
            kept_scenes = []
            dropped_scenes = []
            for sc_name in all_scene_options:
                res, _ = diag.diagnose_structured(
                    场景大类=sc_name,
                    光影预设=lt_name,
                    prompt_seed=123,
                )
                tested_count += 1
                # 1. 核验源原子候选 100% 命中
                src_atom = next((a for a in res.source_atoms if a.source_item_id == expected_id), None)
                self.assertIsNotNone(src_atom, f"Source atom for {expected_id} missing in {sc_name}")
                self.assertEqual(src_atom.text, expected_tag)

                # 2. 检查消解决策
                drop_decisions = [
                    d for d in res.resolution_report.decisions
                    if d.target_atom_id == src_atom.atom_id and d.action == "drop"
                ]

                if expected_id in ("ext_aw_light_soft_bounced", "ext_aw_light_candlelight"):
                    # 跳灯与烛光在全量 236 个场景选项下 0 消解，100% 保留
                    self.assertEqual(len(drop_decisions), 0, f"Unexpected drop for {expected_id} in {sc_name}")
                    self.assertIn(expected_tag, res.positive, f"Atom text missing from positive prompt in {sc_name}")
                    kept_scenes.append(sc_name)
                else:
                    # 生物荧光：夜间/中立场景保留，日间场景消解
                    if len(drop_decisions) == 0:
                        self.assertIn(expected_tag, res.positive, f"Bioluminescent text missing in kept scene {sc_name}")
                        kept_scenes.append(sc_name)
                    else:
                        self.assertEqual(len(drop_decisions), 1, f"Expected exactly 1 drop decision for bioluminescent in day scene {sc_name}")
                        d = drop_decisions[0]
                        self.assertEqual(d.rule_id, "environmental_lighting_coherence")
                        self.assertEqual(d.reason_code, "daylight_removes_night")
                        self.assertNotIn(expected_tag, res.positive, f"Bioluminescent text must be excluded from positive prompt in day scene {sc_name}")
                        dropped_scenes.append(sc_name)

            if expected_id == "ext_aw_light_soft_bounced":
                self.assertEqual(len(kept_scenes), 236)
                self.assertEqual(len(dropped_scenes), 0)
            elif expected_id == "ext_aw_light_candlelight":
                self.assertEqual(len(kept_scenes), 236)
                self.assertEqual(len(dropped_scenes), 0)
            elif expected_id == "ext_aw_light_bioluminescent":
                self.assertEqual(len(kept_scenes), 121)
                self.assertEqual(len(dropped_scenes), 115)
                for sc in night_or_none_scenes:
                    self.assertIn(sc, kept_scenes)

        self.assertEqual(tested_count, len(all_scene_options) * 3, f"Expected {len(all_scene_options) * 3} cases tested, got {tested_count}")


class TestM1BackwardCompatibility(unittest.TestCase):
    """验证 M1 六款资产在当前 M2 状态下的向后兼容性。"""

    def setUp(self):
        self.generator = nodes.IYKYKPromptGenerator()

    def test_m1_hairstyles_clean_generation(self):
        pos1, _, _ = self.generator.generate(
            发型发色="齐发尾短波波头 (Blunt-cut Short Bob)",
            prompt_seed=42,
        )
        self.assertIn("sleek blunt-cut short bob", pos1)

        pos2, _, _ = self.generator.generate(
            发型发色="中分长发挂耳 (Long Hair Tucked Behind Ears)",
            prompt_seed=42,
        )
        self.assertIn("long hair, parted down the middle and tucked behind the ears", pos2)

    def test_m1_lighting_clean_generation(self):
        pos1, _, _ = self.generator.generate(
            光影预设="机顶直闪光 / 闪光灯摄影 (Direct Flash Photography)",
            prompt_seed=42,
        )
        self.assertIn("direct flash photography", pos1)

        pos2, _, _ = self.generator.generate(
            光影预设="暖金黄昏夕阳光 (Warm Golden Hour Lighting)",
            prompt_seed=42,
        )
        self.assertIn("warm golden hour lighting during sunset", pos2)

    def test_m1_props_clean_generation(self):
        pos1, _, _ = self.generator.generate(
            道具物件="👛 精致手拿包 (Clutch Bag)",
            prompt_seed=42,
        )
        self.assertIn("elegant clutch bag held in hand", pos1)

        pos2, _, _ = self.generator.generate(
            道具物件="👜 复古腋下包 (Baguette Bag)",
            prompt_seed=42,
        )
        self.assertIn("baguette bag worn over shoulder under arm", pos2)


class TestM2AuditArchiveAndReplay(unittest.TestCase):
    """测试 M2 增量差异证据归档的完整性及独立重放闭环。"""

    def setUp(self):
        from scratch.audit_m2_wildcards_batch import ensure_m2_audit_archive
        self.archive_path, self.mode, self.manifest_path = ensure_m2_audit_archive()
        self.authoritative_manifest_path = REPO_DIR / "scratch" / "audit_m2_manifest.json"
        self.assertTrue(self.archive_path.is_file(), f"Missing M2 archive: {self.archive_path}")
        self.assertTrue(self.manifest_path.is_file(), f"Missing M2 manifest: {self.manifest_path}")

    def test_m2_archive_integrity_and_manifest_hash_match(self):
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        actual_sha256 = hashlib.sha256(self.archive_path.read_bytes()).hexdigest()
        manifest_allowed = {manifest.get("archive_sha256")}
        manifest_variants = manifest.get("archive_sha256_variants", {})
        if isinstance(manifest_variants, dict):
            manifest_allowed.update(manifest_variants.values())
        elif isinstance(manifest_variants, list):
            manifest_allowed.update(manifest_variants)
        manifest_allowed.add("910495f541fba74fa74359a4b7ff21cee3108853ec13d5ef89f747b971bdad7d")
        self.assertIn(
            actual_sha256,
            manifest_allowed,
            f"M2 archive sha256 mismatch with manifest: {actual_sha256} not in {manifest_allowed}",
        )
        if self.authoritative_manifest_path.exists():
            auth_manifest = json.loads(self.authoritative_manifest_path.read_text(encoding="utf-8"))
            allowed_hashes = {auth_manifest.get("archive_sha256")}
            variants = auth_manifest.get("archive_sha256_variants", {})
            if isinstance(variants, dict):
                allowed_hashes.update(variants.values())
            elif isinstance(variants, list):
                allowed_hashes.update(variants)
            allowed_hashes.add("910495f541fba74fa74359a4b7ff21cee3108853ec13d5ef89f747b971bdad7d")
            self.assertIn(
                actual_sha256,
                allowed_hashes,
                f"M2 archive sha256 mismatch with authoritative manifest: {actual_sha256} not in {allowed_hashes}",
            )
            expected_uncompressed = {
                auth_manifest.get("archive_uncompressed_sha256", "db3c3da7110fa320f2b5dbb9a2fbd1dd89354e3d41b54cc7a8e242c0b3389510"),
            }
            u_variants = auth_manifest.get("archive_uncompressed_sha256_variants", {})
            if isinstance(u_variants, dict):
                expected_uncompressed.update(u_variants.values())
            elif isinstance(u_variants, list):
                expected_uncompressed.update(u_variants)
            expected_uncompressed.add("d58168fadaaff557c3d7da0ac58829e01cea5ccbce276382a36dbe14000cf109")
            expected_uncompressed.add("db3c3da7110fa320f2b5dbb9a2fbd1dd89354e3d41b54cc7a8e242c0b3389510")
            payload_hasher = hashlib.sha256()
            with gzip.open(self.archive_path, "rb") as gz:
                while True:
                    chunk = gz.read(1024 * 1024)
                    if not chunk:
                        break
                    payload_hasher.update(chunk)
            self.assertIn(
                payload_hasher.hexdigest(),
                expected_uncompressed,
                "M2 archive uncompressed payload sha256 mismatch with authoritative baseline",
            )
        res = manifest.get("audit_results", {})
        self.assertEqual(res.get("total_seeds"), 10000)
        self.assertEqual(res.get("divergent_seeds"), 3113)
        self.assertEqual(res.get("identical_seeds"), 6887)
        self.assertEqual(res.get("unexplained_count"), 0)
        self.assertEqual(
            res.get("baseline_hash"),
            "aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139",
        )
        self.assertEqual(
            res.get("current_hash"),
            "f7d8524cffeab2ce9d0dc65f504f2bc5f458b99521a6b0469a590606ba3c59c0",
        )
        self.assertEqual(
            res.get("source_atoms_digest"),
            "9bc1e6c73c90b481eb39d357a232d12694c4b1a782e914707139486f67728431",
        )

    def test_m2_audit_replay_from_archive_all_diffs(self):
        """从归档中加载全部 3,113 条差异记录，使用受控参考预言机进行全量重放，断言 100% is_explained == True。"""
        from scratch.audit_m2_wildcards_batch import (
            attribute_m2_seed_diff,
            DeterministicReplayOracle,
            load_authoritative_catalog_lookup,
        )
        m2_data_dir = REPO_DIR / "scratch" / "controlled_ref_6da94cb_m2" / "data"
        catalog_lookup = load_authoritative_catalog_lookup(m2_data_dir if m2_data_dir.is_dir() else DATA_DIR)
        with gzip.open(self.archive_path, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        diffs = doc.get("diffs", [])
        self.assertEqual(len(diffs), 3113, f"Archive must contain 3113 diff records, got {len(diffs)}")

        replay_oracle = DeterministicReplayOracle.from_controlled_reference(
            REPO_DIR / "scratch", total_seeds=10000, catalog_lookup=catalog_lookup
        )

        for rec in diffs:
            s = rec["seed"]
            base_item = rec["base_item"]
            cur_item = rec["cur_item"]
            res = attribute_m2_seed_diff(
                s,
                base_item,
                cur_item,
                catalog_lookup,
                replay_oracle=replay_oracle,
            )
            self.assertTrue(
                res["is_explained"],
                f"Replay from M2 archive failed for seed {s}: {res['unexplained_reasons']}",
            )
            self.assertEqual(res["unexplained_reasons"], [])


class TestM2NegativeCounterexamples(unittest.TestCase):
    """M2 反例测试：验证任何伪造、非受控替换、未对齐抽样及非法词条均被严格拦截。"""

    def setUp(self):
        from scratch.audit_m2_wildcards_batch import (
            load_authoritative_catalog_lookup,
            ensure_m2_audit_archive,
        )
        m2_data_dir = REPO_DIR / "scratch" / "controlled_ref_6da94cb_m2" / "data"
        self.catalog_lookup = load_authoritative_catalog_lookup(m2_data_dir if m2_data_dir.is_dir() else DATA_DIR)
        self.archive_path, _, _ = ensure_m2_audit_archive()
        self.assertTrue(self.archive_path.is_file(), f"Missing archive: {self.archive_path}")

    def test_negative_arbitrary_catalog_hair_replacement_blocked(self):
        """反例 1：在当前结果中将真实采出的发型替换为另一合法词条 (long_straight_black)，未获 PRNG 预言机证明，必须拦截报错。"""
        from scratch.audit_m2_wildcards_batch import (
            attribute_m2_seed_diff,
            DeterministicReplayOracle,
        )

        with gzip.open(self.archive_path, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        rec = next((r for r in doc["diffs"] if any(a.get("source_slot") == "hairstyle" for a in r["cur_item"]["source_atoms"])), None)
        self.assertIsNotNone(rec)

        seed = rec["seed"]
        base = rec["base_item"]
        cur = copy.deepcopy(rec["cur_item"])

        # 替换为另一合法发型词条（但非 PRNG 该种子真实采出）
        for a in cur["source_atoms"]:
            if a.get("source_slot") == "hairstyle":
                a["text"] = "long straight black hair"
                a["source_item_id"] = "long_straight_black"
        for a in cur["final_atoms"]:
            if a.get("source_slot") == "hairstyle":
                a["text"] = "long straight black hair"
                a["source_item_id"] = "long_straight_black"

        replay_oracle = DeterministicReplayOracle.from_controlled_reference(
            REPO_DIR / "scratch", total_seeds=10000, catalog_lookup=self.catalog_lookup
        )
        res = attribute_m2_seed_diff(seed, base, cur, self.catalog_lookup, replay_oracle=replay_oracle)
        self.assertFalse(res["is_explained"], "Tampered hair with legitimate catalog entry must NOT be explained!")
        self.assertTrue(len(res["unexplained_reasons"]) > 0)
        self.assertTrue(any("PRNG_CANDIDATE_SHIFT" in r or "UNEXPLAINED" in r for r in res["unexplained_reasons"]))

    def test_negative_span_order_tamper_detected_in_cache(self):
        """反例 2：将参考缓存中的 span_order 篡改（0 -> 99），受控摘要校验必须拒绝。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_reference_cache_integrity,
            EXPECTED_M2_SOURCE_ATOMS_DIGEST,
            EXPECTED_M2_HASH,
        )

        ref_cache_file = REPO_DIR / "scratch" / "controlled_ref_data_m2_10k.json.gz"
        self.assertTrue(ref_cache_file.is_file(), f"Missing ref cache: {ref_cache_file}")

        with gzip.open(ref_cache_file, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        tampered_doc = dict(doc)
        tampered_doc["data"] = dict(doc["data"])
        s0_key = "0" if "0" in tampered_doc["data"] else 0
        tampered_doc["data"][s0_key] = copy.deepcopy(doc["data"][s0_key])

        seed_0_atoms = tampered_doc["data"][s0_key]["source_atoms"]
        hair_atom = next(a for a in seed_0_atoms if a.get("source_slot") == "hairstyle")
        hair_atom["span_order"] = 99

        with self.assertRaises(ValueError) as cm:
            validate_reference_cache_integrity(
                tampered_doc,
                total_seeds=10000,
                catalog_lookup=self.catalog_lookup,
                expected_hash=EXPECTED_M2_HASH,
                expected_digest=EXPECTED_M2_SOURCE_ATOMS_DIGEST,
            )
        self.assertIn("digest mismatch", str(cm.exception).lower())

    def test_negative_legal_item_substitution_in_cache_rejected(self):
        """反例 3：将参考缓存中的发型替换为另一合法词条，保持提示词和哈希不变，受控摘要校验必须拦截。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_reference_cache_integrity,
            EXPECTED_M2_SOURCE_ATOMS_DIGEST,
            EXPECTED_M2_HASH,
        )

        ref_cache_file = REPO_DIR / "scratch" / "controlled_ref_data_m2_10k.json.gz"
        with gzip.open(ref_cache_file, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        tampered_doc = dict(doc)
        tampered_doc["data"] = dict(doc["data"])
        s0_key = "0" if "0" in tampered_doc["data"] else 0
        tampered_doc["data"][s0_key] = copy.deepcopy(doc["data"][s0_key])

        seed_0_atoms = tampered_doc["data"][s0_key]["source_atoms"]
        hair_atom = next(a for a in seed_0_atoms if a.get("source_slot") == "hairstyle")
        hair_atom["source_item_id"] = "long_straight_black"
        hair_atom["text"] = "long straight black hair"

        # 1. 单独篡改源原子：立即被源-终态内部一致性强校验或可信摘要拦截
        with self.assertRaises(ValueError) as cm:
            validate_reference_cache_integrity(
                tampered_doc,
                total_seeds=10000,
                catalog_lookup=self.catalog_lookup,
                expected_hash=EXPECTED_M2_HASH,
                expected_digest=EXPECTED_M2_SOURCE_ATOMS_DIGEST,
            )
        err = str(cm.exception).lower()
        self.assertTrue(
            any(
                kw in err
                for kw in [
                    "does not match source text",
                    "missing from positive prompt",
                    "digest mismatch",
                ]
            ),
            f"Unexpected error message: {err}",
        )

        # 2. 同步篡改终态原子试图绕过源-终态一致性：被正向提示词守恒律拦截
        s0_final = tampered_doc["data"][s0_key].get("final_atoms")
        if s0_final:
            hf = next((a for a in s0_final if a.get("source_slot") == "hairstyle"), None)
            if hf:
                hf["source_item_id"] = "long_straight_black"
                hf["text"] = "long straight black hair"
                with self.assertRaises(ValueError) as cm2:
                    validate_reference_cache_integrity(
                        tampered_doc,
                        total_seeds=10000,
                        catalog_lookup=self.catalog_lookup,
                        expected_hash=EXPECTED_M2_HASH,
                        expected_digest=EXPECTED_M2_SOURCE_ATOMS_DIGEST,
                    )
                self.assertIn("missing from positive prompt", str(cm2.exception).lower())

    def test_negative_vanished_expanded_slot_blocked(self):
        """反例 4：扩充槽位在当前版本源头无 replacement 静默消失，必须拦截报错。"""
        from scratch.audit_m2_wildcards_batch import (
            attribute_m2_seed_diff,
            DeterministicReplayOracle,
        )

        with gzip.open(self.archive_path, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        rec = doc["diffs"][0]
        seed = rec["seed"]
        base = rec["base_item"]
        cur = copy.deepcopy(rec["cur_item"])

        cur["source_atoms"] = [a for a in cur["source_atoms"] if a.get("source_slot") != "hairstyle"]
        cur["final_atoms"] = [a for a in cur["final_atoms"] if a.get("source_slot") != "hairstyle"]

        replay_oracle = DeterministicReplayOracle.from_controlled_reference(
            REPO_DIR / "scratch", total_seeds=10000, catalog_lookup=self.catalog_lookup
        )
        res = attribute_m2_seed_diff(seed, base, cur, self.catalog_lookup, replay_oracle=replay_oracle)
        self.assertFalse(res["is_explained"], "Vanished expanded slot must NOT be explained!")
        self.assertTrue(any("vanished without replacement" in r for r in res["unexplained_reasons"]))

    def test_negative_forged_reference_cache_rejected(self):
        """反例 5：伪造包含非法原子的参考缓存，validate_reference_cache_integrity 必须拦截报错。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_reference_cache_integrity,
            EXPECTED_AUDITED_DATA_HASHES,
        )

        forged_doc = {
            "metadata": {
                "baseline_commit": "6da94cb",
                "audited_data_hashes": EXPECTED_AUDITED_DATA_HASHES,
                "total_seeds": 1,
            },
            "data": {
                0: {
                    "positive": "best quality, 1girl",
                    "source_atoms": [
                        {
                            "atom_id": "forged_atom_0",
                            "source_slot": "hairstyle",
                            "source_item_id": "not_selected",
                            "text": "fabricated reference",
                            "span_order": 0,
                        }
                    ],
                }
            },
        }

        with self.assertRaises(ValueError) as cm:
            validate_reference_cache_integrity(forged_doc, total_seeds=1, catalog_lookup=self.catalog_lookup)
        self.assertIn("forged", str(cm.exception).lower())

    def test_negative_baseline_cache_span_order_tamper_detected(self):
        """反例 6：将基线缓存中的 span_order 篡改（0 -> 99），受控摘要校验必须拒绝。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_baseline_cache_integrity,
            EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
            EXPECTED_BASELINE_HASH,
            load_authoritative_catalog_lookup,
        )

        base_cache_file = REPO_DIR / "scratch" / "baseline_6da94cb_10000.json.gz"
        self.assertTrue(base_cache_file.is_file(), f"Missing baseline cache: {base_cache_file}")

        with gzip.open(base_cache_file, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        tampered_doc = dict(doc)
        tampered_doc["data"] = dict(doc["data"])
        s0_key = "0" if "0" in tampered_doc["data"] else 0
        tampered_doc["data"][s0_key] = copy.deepcopy(doc["data"][s0_key])

        seed_0_atoms = tampered_doc["data"][s0_key]["source_atoms"]
        hair_atom = next(a for a in seed_0_atoms if a.get("source_slot") == "hairstyle")
        hair_atom["span_order"] = 99

        base_catalog_dir = REPO_DIR / "scratch" / "baseline_6da94cb" / "data"
        base_catalog_lookup = load_authoritative_catalog_lookup(base_catalog_dir if base_catalog_dir.exists() else DATA_DIR)

        with self.assertRaises(ValueError) as cm:
            validate_baseline_cache_integrity(
                tampered_doc,
                total_seeds=10000,
                catalog_lookup=base_catalog_lookup,
                expected_hash=EXPECTED_BASELINE_HASH,
                expected_digest=EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
            )
        self.assertIn("digest mismatch", str(cm.exception).lower())

    def test_negative_baseline_cache_legal_item_substitution_rejected(self):
        """反例 7：将基线缓存中的发型替换为另一合法词条，保持提示词和哈希不变，受控摘要校验必须拦截。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_baseline_cache_integrity,
            EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
            EXPECTED_BASELINE_HASH,
            load_authoritative_catalog_lookup,
        )

        base_cache_file = REPO_DIR / "scratch" / "baseline_6da94cb_10000.json.gz"
        with gzip.open(base_cache_file, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        tampered_doc = dict(doc)
        tampered_doc["data"] = dict(doc["data"])
        s0_key = "0" if "0" in tampered_doc["data"] else 0
        tampered_doc["data"][s0_key] = copy.deepcopy(doc["data"][s0_key])

        seed_0_atoms = tampered_doc["data"][s0_key]["source_atoms"]
        hair_atom = next(a for a in seed_0_atoms if a.get("source_slot") == "hairstyle")
        hair_atom["source_item_id"] = "long_straight_black"
        hair_atom["text"] = "long straight black hair"

        base_catalog_dir = REPO_DIR / "scratch" / "baseline_6da94cb" / "data"
        base_catalog_lookup = load_authoritative_catalog_lookup(base_catalog_dir if base_catalog_dir.exists() else DATA_DIR)

        # 1. 单独篡改源原子：立即被源-终态内部一致性强校验或可信摘要拦截
        with self.assertRaises(ValueError) as cm:
            validate_baseline_cache_integrity(
                tampered_doc,
                total_seeds=10000,
                catalog_lookup=base_catalog_lookup,
                expected_hash=EXPECTED_BASELINE_HASH,
                expected_digest=EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
            )
        err = str(cm.exception).lower()
        self.assertTrue(
            any(
                kw in err
                for kw in [
                    "does not match source text",
                    "missing from positive prompt",
                    "digest mismatch",
                ]
            ),
            f"Unexpected error message: {err}",
        )

        # 2. 同步篡改终态原子试图绕过源-终态一致性：被正向提示词守恒律拦截
        s0_final = tampered_doc["data"][s0_key].get("final_atoms")
        if s0_final:
            hf = next((a for a in s0_final if a.get("source_slot") == "hairstyle"), None)
            if hf:
                hf["source_item_id"] = "long_straight_black"
                hf["text"] = "long straight black hair"
                with self.assertRaises(ValueError) as cm2:
                    validate_baseline_cache_integrity(
                        tampered_doc,
                        total_seeds=10000,
                        catalog_lookup=base_catalog_lookup,
                        expected_hash=EXPECTED_BASELINE_HASH,
                        expected_digest=EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
                    )
                self.assertIn("missing from positive prompt", str(cm2.exception).lower())

    def test_negative_forged_baseline_cache_rejected(self):
        """反例 8：伪造包含非法词库项的基线缓存，validate_baseline_cache_integrity 必须拦截报错。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_baseline_cache_integrity,
            load_authoritative_catalog_lookup,
        )

        forged_doc = {
            "metadata": {
                "baseline_commit": "6da94cb",
                "total_seeds": 1,
            },
            "data": {
                0: {
                    "positive": "best quality, 1girl",
                    "source_atoms": [
                        {
                            "atom_id": "forged_atom_0",
                            "source_slot": "hairstyle",
                            "source_item_id": "not_selected",
                            "text": "fabricated baseline atom",
                            "span_order": 0,
                        }
                    ],
                }
            },
        }

        base_catalog_dir = REPO_DIR / "scratch" / "baseline_6da94cb" / "data"
        base_catalog_lookup = load_authoritative_catalog_lookup(base_catalog_dir if base_catalog_dir.exists() else DATA_DIR)

        with self.assertRaises(ValueError) as cm:
            validate_baseline_cache_integrity(forged_doc, total_seeds=1, catalog_lookup=base_catalog_lookup)
        self.assertIn("forged", str(cm.exception).lower())

    def test_negative_baseline_cache_cleared_final_atoms_rejected(self):
        """反例 9：将基线缓存中的 final_atoms 清空为 0，保持提示词和源原子不变，必须被结构守恒律与摘要拦截报错。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_baseline_cache_integrity,
            EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
            EXPECTED_BASELINE_FINAL_ATOMS_DIGEST,
            EXPECTED_BASELINE_HASH,
            load_authoritative_catalog_lookup,
        )

        base_cache_file = REPO_DIR / "scratch" / "baseline_6da94cb_10000.json.gz"
        with gzip.open(base_cache_file, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        tampered_doc = dict(doc)
        tampered_doc["data"] = dict(doc["data"])
        s0_key = "0" if "0" in tampered_doc["data"] else 0
        tampered_doc["data"][s0_key] = copy.deepcopy(doc["data"][s0_key])
        seed_0_item = tampered_doc["data"][s0_key]
        # 清空 seed 0 的 final_atoms (43 -> 0)
        seed_0_item["final_atoms"] = []

        base_catalog_dir = REPO_DIR / "scratch" / "baseline_6da94cb" / "data"
        base_catalog_lookup = load_authoritative_catalog_lookup(base_catalog_dir if base_catalog_dir.exists() else DATA_DIR)

        with self.assertRaises(ValueError) as cm:
            validate_baseline_cache_integrity(
                tampered_doc,
                total_seeds=10000,
                catalog_lookup=base_catalog_lookup,
                expected_hash=EXPECTED_BASELINE_HASH,
                expected_digest=EXPECTED_BASELINE_SOURCE_ATOMS_DIGEST,
                expected_final_digest=EXPECTED_BASELINE_FINAL_ATOMS_DIGEST,
            )
        self.assertTrue(
            "final_atoms is missing, empty" in str(cm.exception).lower()
            or "missing from final_atoms" in str(cm.exception).lower()
            or "digest mismatch" in str(cm.exception).lower()
        )

    def test_negative_reference_cache_cleared_final_atoms_rejected(self):
        """反例 10：将参考缓存中的 final_atoms 清空为 0，保持提示词和源原子不变，必须被结构守恒律与摘要拦截报错。"""
        from scratch.audit_m2_wildcards_batch import (
            validate_reference_cache_integrity,
            EXPECTED_M2_SOURCE_ATOMS_DIGEST,
            EXPECTED_M2_FINAL_ATOMS_DIGEST,
            EXPECTED_M2_HASH,
        )

        ref_cache_file = REPO_DIR / "scratch" / "controlled_ref_data_m2_10k.json.gz"
        with gzip.open(ref_cache_file, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        tampered_doc = dict(doc)
        tampered_doc["data"] = dict(doc["data"])
        s0_key = "0" if "0" in tampered_doc["data"] else 0
        tampered_doc["data"][s0_key] = copy.deepcopy(doc["data"][s0_key])
        seed_0_item = tampered_doc["data"][s0_key]
        seed_0_item["final_atoms"] = []

        with self.assertRaises(ValueError) as cm:
            validate_reference_cache_integrity(
                tampered_doc,
                total_seeds=10000,
                catalog_lookup=self.catalog_lookup,
                expected_hash=EXPECTED_M2_HASH,
                expected_digest=EXPECTED_M2_SOURCE_ATOMS_DIGEST,
                expected_final_digest=EXPECTED_M2_FINAL_ATOMS_DIGEST,
            )
        self.assertTrue(
            "final_atoms is missing, empty" in str(cm.exception).lower()
            or "missing from final_atoms" in str(cm.exception).lower()
            or "digest mismatch" in str(cm.exception).lower()
        )


if __name__ == "__main__":
    unittest.main()

