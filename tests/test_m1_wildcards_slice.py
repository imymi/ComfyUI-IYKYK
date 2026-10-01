"""
tests/test_m1_wildcards_slice.py — M1 垂直切片 6 条样本全链路自动化验收测试

覆盖内容：
1. 台账 TSV 复合键唯一性与字段规范校验
2. 6 条样本显式选中（无冲突正向生成）测试
3. 样本 3：黑白胶片下直闪光不被误删测试（负向回归测试）
4. 样本 4：规则单测（合成 fixture）与管线集成测试（夜间场景消解黄昏光）
5. 样本 5：双手占用姿态下仅触发 pose_hand_occupation 移除手拿包
6. 样本 5 补充：双手可用下输入 2 个手持物触发 handheld_props_single_holder
7. 样本 6 对照：双手占用姿态下腋下包（worn, hands_required=0）不误删
8. ExactCatalogIndex 碰撞与命名隔离测试
"""
from __future__ import annotations

import csv
import json
import random
import unittest
from pathlib import Path

from lib.assembler import PromptAssembler
from lib.conflict_resolver import ConflictResolver, OneTimeIndex, DecisionLedger
from lib.models import (
    GenerationResult,
    PromptAtom,
    PromptFragment,
    PromptSpan,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)
from lib.rule_contract import FROZEN_DAG_METADATA
from lib.sampler import DataSampler, ExactCatalogIndex
import nodes

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
DOCS_DIR = REPO_DIR / "docs" / "data_migration"


class TestM1WildcardsProvenanceLedger(unittest.TestCase):
    """测试 M1 准入台账数据规范与复合主键约束。"""

    def setUp(self):
        self.tsv_path = DOCS_DIR / "ai_wildcards_provenance_ledger.tsv"
        self.assertTrue(self.tsv_path.is_file(), f"Missing ledger file: {self.tsv_path}")

    def test_ledger_composite_key_and_integrity(self):
        with open(self.tsv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            rows = list(reader)

        self.assertEqual(len(rows), 6, f"Expected 6 rows in M1 slice ledger, got {len(rows)}")

        seen_composite_keys = set()
        seen_primary_ids = set()

        expected_ids = {
            "ext_aw_hair_bob_blunt_cut",
            "ext_aw_hair_long_parted_behind_ears",
            "ext_aw_light_direct_flash",
            "ext_aw_light_warm_golden_hour",
            "ext_aw_prop_clutch_bag",
            "ext_aw_prop_baguette_bag",
        }

        found_ids = set()

        for idx, row in enumerate(rows):
            cid = row["canonical_id"]
            found_ids.add(cid)
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

            # 校验许可依据非空
            self.assertTrue(row["license"].strip(), f"Empty license for {cid}")
            self.assertTrue(row["license_evidence"].strip(), f"Empty license_evidence for {cid}")
            self.assertEqual(row["rating"], "SFW", f"M1 sample {cid} must be SFW")

        self.assertEqual(found_ids, expected_ids, "Ledger canonical IDs mismatch expected M1 set")
        self.assertEqual(seen_primary_ids, expected_ids, "Not all canonical IDs have a primary record")


class TestM1ExactCatalogCollision(unittest.TestCase):
    """测试新增条目在 ExactCatalogIndex 中零别名/命名空间碰撞。"""

    def test_no_exact_catalog_collisions(self):
        # 1. Accessories
        acc_data = json.loads((DATA_DIR / "accessories.json").read_text(encoding="utf-8"))
        idx_hair = ExactCatalogIndex("hairstyles")
        for h in acc_data.get("hairstyles", []):
            idx_hair.register_item(h)
        self.assertIsNotNone(idx_hair.get("ext_aw_hair_bob_blunt_cut"))
        self.assertIsNotNone(idx_hair.get("齐发尾短波波头 (Blunt-cut Short Bob)"))
        self.assertIsNotNone(idx_hair.get("ext_aw_hair_long_parted_behind_ears"))

        # 2. Lighting
        light_data = json.loads((DATA_DIR / "lighting.json").read_text(encoding="utf-8"))
        idx_light = ExactCatalogIndex("lighting_presets")
        for sec in ["professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting"]:
            for item in light_data.get(sec, []):
                idx_light.register_item(item)
        self.assertIsNotNone(idx_light.get("ext_aw_light_direct_flash"))
        self.assertIsNotNone(idx_light.get("机顶直闪光 / 闪光灯摄影 (Direct Flash Photography)"))
        self.assertIsNotNone(idx_light.get("ext_aw_light_warm_golden_hour"))
        self.assertIsNotNone(idx_light.get("暖金黄昏夕阳光 (Warm Golden Hour Lighting)"))

        # 3. Props
        prop_data = json.loads((DATA_DIR / "props.json").read_text(encoding="utf-8"))
        idx_props = ExactCatalogIndex("props")
        for p in prop_data.get("categories", []):
            idx_props.register_item(p)
        self.assertIsNotNone(idx_props.get("fashion_clutch_bag"))
        self.assertIsNotNone(idx_props.get("👛 精致手拿包 (Clutch Bag)"))
        self.assertIsNotNone(idx_props.get("fashion_baguette_bag"))
        self.assertIsNotNone(idx_props.get("👜 复古腋下包 (Baguette Bag)"))


class TestM1SamplesPipelineAndRules(unittest.TestCase):
    """测试 6 条样本的无冲突生成、单测及三态规则消解。"""

    def setUp(self):
        self.generator = nodes.IYKYKPromptGenerator()
        self.resolver = ConflictResolver(DATA_DIR)

    def test_sample_1_blunt_cut_bob_clean_generation(self):
        """样本 1：齐发尾短波波头显式选择，正常输出且不触发冲突。"""
        pos, neg, desc = self.generator.generate(
            发型发色="齐发尾短波波头 (Blunt-cut Short Bob)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            prompt_seed=42,
        )
        self.assertIn("sleek blunt-cut short bob", pos)
        self.assertIn("发型: 齐发尾短波波头", desc)

    def test_sample_2_long_hair_tucked_ears_clean_generation(self):
        """样本 2：中分长发挂耳显式选择，正常输出。"""
        pos, neg, desc = self.generator.generate(
            发型发色="中分长发挂耳 (Long Hair Tucked Behind Ears)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            prompt_seed=42,
        )
        self.assertIn("long hair, parted down the middle and tucked behind the ears", pos)

    def test_sample_3_direct_flash_clean_and_monochrome_negative_test(self):
        """样本 3：直闪光在常规下输出，且在黑白胶片下不被 monochrome 规则误删。"""
        # A. 常规正向输出
        pos, neg, desc = self.generator.generate(
            光影预设="机顶直闪光 / 闪光灯摄影 (Direct Flash Photography)",
            prompt_seed=42,
        )
        self.assertIn("direct flash photography", pos)

        # B. 黑白负向回归：选择胶片风格包含 monochrome，验证直闪光不被 drop
        # 在 IYKYK 中胶片风格有黑白选项
        sampler = DataSampler(DATA_DIR)
        film_stocks = sampler.list_film_stocks()
        mono_stock = next((f for f in film_stocks if "黑白" in f or "Monochrome" in f or "Tri-X" in f), None)
        self.assertIsNotNone(mono_stock, "Could not find a monochrome film stock in data/film_stocks.json")

        pos_mono, _, _ = self.generator.generate(
            光影预设="机顶直闪光 / 闪光灯摄影 (Direct Flash Photography)",
            胶片风格=mono_stock,
            prompt_seed=42,
        )
        # 直闪光无 color 属性，必须完整保留！
        self.assertIn("direct flash photography", pos_mono)

    def test_sample_4_warm_golden_hour_clean_and_night_resolution(self):
        """样本 4：黄昏夕阳光正向输出，且在夜间场景下被 environmental_lighting_coherence 消解。"""
        # A. 正向输出（显式搭配户外日间场景 山林/露营，验证自洽输出）
        pos, _, _ = self.generator.generate(
            场景大类="山林/露营",
            光影预设="暖金黄昏夕阳光 (Warm Golden Hour Lighting)",
            prompt_seed=42,
        )
        self.assertIn("warm golden hour lighting during sunset outdoors", pos)

        # B. 规则单测：使用合成夜间 Anchor Fixture，验证精准触发 environmental_lighting_coherence
        origin_scene = SelectionOrigin(entry_point="generator", mode="explicit", selector="scene")
        origin_light = SelectionOrigin(entry_point="generator", mode="explicit", selector="lighting")
        anchor_atom = PromptAtom(
            atom_id="fixture_anchor_night",
            text="park at night",
            source_slot="scene_theme",
            span_type=SpanType.PLAIN,
            origin=origin_scene,
            facts=SemanticFacts(
                semantic_role="scene_anchor",
                time_of_day="night",
                space_kind="outdoor",
            ),
        )
        golden_hour_atom = PromptAtom(
            atom_id="test_golden_hour",
            text="warm golden hour lighting during sunset outdoors",
            source_slot="lighting",
            span_type=SpanType.PLAIN,
            origin=origin_light,
            facts=SemanticFacts(
                semantic_role="selector",
                time_of_day="dusk",
                light_sources=("daylight",),
                color_modes=("color",),
                space_kind="outdoor",
            ),
        )

        res_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report(
            [anchor_atom, golden_hour_atom],
            rng=random.Random(42),
        )
        remaining_ids = [a.atom_id for a in res_atoms]
        self.assertNotIn("test_golden_hour", remaining_ids, "Golden hour light should be dropped in night scene")
        self.assertIn("environmental_lighting_coherence", rules_applied)
        self.assertTrue(
            any(
                d.rule_id == "environmental_lighting_coherence" and d.target_atom_id == "test_golden_hour"
                for d in report.decisions
            ),
            "Expected environmental_lighting_coherence drop record for golden hour light",
        )

        # C. 管线集成测试：使用真实夜间场景 scene_rooftop_at_night (对应子类 都市露出)
        pos_night, _, _ = self.generator.generate(
            场景大类="都市露出",
            剧情主题="无 (None)",
            光影预设="暖金黄昏夕阳光 (Warm Golden Hour Lighting)",
            prompt_seed=42,
        )
        self.assertNotIn("warm golden hour lighting during sunset outdoors", pos_night)

    def test_sample_5_clutch_bag_hand_occupation(self):
        """样本 5：双手占用时，手拿包被 pose_hand_occupation 移除。"""
        # A. 常规单手可用，正常输出
        pos_free, _, _ = self.generator.generate(
            道具物件="👛 精致手拿包 (Clutch Bag)",
            姿势动作="🧍 站姿系列",
            prompt_seed=42,
        )
        self.assertIn("elegant clutch bag held in hand", pos_free)

        # B. 规则单测：双手忙姿态（both_busy）触发 pose_hand_occupation
        origin_pose = SelectionOrigin(entry_point="generator", mode="explicit", selector="pose")
        origin_prop = SelectionOrigin(entry_point="generator", mode="explicit", selector="props")
        busy_pose_atom = PromptAtom(
            atom_id="test_busy_pose",
            text="hands clasped behind head",
            source_slot="poses",
            span_type=SpanType.PLAIN,
            origin=origin_pose,
            facts=SemanticFacts(
                semantic_role="selector",
                hand_state="both_busy",
            ),
        )
        clutch_atom = PromptAtom(
            atom_id="test_clutch_bag",
            text="elegant clutch bag held in hand",
            source_slot="props",
            span_type=SpanType.PLAIN,
            origin=origin_prop,
            facts=SemanticFacts(
                semantic_role="selector",
                prop_usage="handheld",
                hands_required=1,
            ),
        )

        res_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report(
            [busy_pose_atom, clutch_atom],
            rng=random.Random(42),
        )
        remaining_ids = [a.atom_id for a in res_atoms]
        self.assertNotIn("test_clutch_bag", remaining_ids)
        self.assertIn("pose_hand_occupation", rules_applied)
        self.assertTrue(
            any(
                d.rule_id == "pose_hand_occupation" and d.target_atom_id == "test_clutch_bag"
                for d in report.decisions
            ),
            "Expected pose_hand_occupation record for clutch bag",
        )

    def test_sample_5_supplement_multiple_holders_rule(self):
        """样本 5 补充：双手可用时，2 个手持物触发 handheld_props_single_holder 胜者保留。"""
        origin_pose = SelectionOrigin(entry_point="generator", mode="explicit", selector="pose")
        origin_prop = SelectionOrigin(entry_point="generator", mode="explicit", selector="props")
        free_pose_atom = PromptAtom(
            atom_id="test_free_pose",
            text="standing naturally",
            source_slot="poses",
            span_type=SpanType.PLAIN,
            origin=origin_pose,
            facts=SemanticFacts(
                semantic_role="selector",
                hand_state="free",
            ),
        )
        clutch_atom_1 = PromptAtom(
            atom_id="test_clutch_bag_1",
            text="elegant clutch bag held in hand",
            source_slot="props",
            span_type=SpanType.PLAIN,
            tag_order=1,
            span_order=0,
            origin=origin_prop,
            facts=SemanticFacts(
                semantic_role="selector",
                prop_usage="handheld",
                hands_required=1,
            ),
        )
        fan_atom_2 = PromptAtom(
            atom_id="test_folding_fan_2",
            text="silk folding fan held in hand",
            source_slot="props",
            span_type=SpanType.PLAIN,
            tag_order=2,
            span_order=0,
            origin=origin_prop,
            facts=SemanticFacts(
                semantic_role="selector",
                prop_usage="handheld",
                hands_required=1,
            ),
        )

        res_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report(
            [free_pose_atom, clutch_atom_1, fan_atom_2],
            rng=random.Random(42),
        )
        remaining_ids = [a.atom_id for a in res_atoms]
        self.assertIn("test_clutch_bag_1", remaining_ids, "Winner handheld prop should remain")
        self.assertNotIn("test_folding_fan_2", remaining_ids, "Loser handheld prop should be dropped")
        self.assertIn("handheld_props_single_holder", rules_applied)
        self.assertTrue(
            any(
                d.rule_id == "handheld_props_single_holder" and d.target_atom_id == "test_folding_fan_2"
                for d in report.decisions
            ),
            "Expected handheld_props_single_holder record for second handheld prop",
        )

    def test_sample_6_baguette_bag_worn_not_dropped_in_busy_pose(self):
        """样本 6 对照组：双手忙姿势下，腋下包因 prop_usage=worn 且 hands_required=0 保持保留。"""
        origin_pose = SelectionOrigin(entry_point="generator", mode="explicit", selector="pose")
        origin_prop = SelectionOrigin(entry_point="generator", mode="explicit", selector="props")
        busy_pose_atom = PromptAtom(
            atom_id="test_busy_pose",
            text="hands clasped behind head",
            source_slot="poses",
            span_type=SpanType.PLAIN,
            origin=origin_pose,
            facts=SemanticFacts(
                semantic_role="selector",
                hand_state="both_busy",
            ),
        )
        baguette_atom = PromptAtom(
            atom_id="test_baguette_bag",
            text="baguette bag worn over shoulder under arm",
            source_slot="props",
            span_type=SpanType.PLAIN,
            origin=origin_prop,
            facts=SemanticFacts(
                semantic_role="selector",
                prop_usage="worn",
                hands_required=0,
            ),
        )

        res_atoms, _, _ = self.resolver.resolve_atoms_with_full_report(
            [busy_pose_atom, baguette_atom],
            rng=random.Random(42),
        )
        remaining_ids = [a.atom_id for a in res_atoms]
        self.assertIn(
            "test_baguette_bag",
            remaining_ids,
            "Baguette bag (worn, hands_required=0) must NOT be dropped by pose_hand_occupation",
        )


if __name__ == "__main__":
    unittest.main()
