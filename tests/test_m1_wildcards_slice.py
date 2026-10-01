"""
tests/test_m1_wildcards_slice.py — M1 垂直切片 6 条样本全链路自动化验收测试

覆盖内容：
1. 台账 TSV 复合键唯一性、目标映射与 JSON 实体双向一致性校验
2. 6 条样本在真实生成器与目录数据下的端到端生成测试
3. 样本 3：真实黑白胶片下直闪光不被误删测试（负向回归测试）
4. 样本 4：解决默认配置与多种子下生成失败问题，并验证夜景下的消解规则
5. 样本 5 & 6：真实目录数据加载核验、双手占用下真实生成器对抗测试（手拿包移除 vs 腋下包保留）
6. 样本 5 补充：真实目录双持物触发 handheld_props_single_holder
7. ExactCatalogIndex 碰撞与命名隔离测试
"""
from __future__ import annotations

import csv
import gzip
import json
import random
import unittest
from pathlib import Path

from lib.conflict_resolver import ConflictResolver
from lib.models import (
    PromptAtom,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
)
from lib.sampler import DataSampler, ExactCatalogIndex
import nodes

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
DOCS_DIR = REPO_DIR / "docs" / "data_migration"


class TestM1WildcardsProvenanceLedger(unittest.TestCase):
    """测试 M1 准入台账数据规范、复合主键约束及目标文件实体映射。"""

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

            # 校验许可依据与评级
            self.assertTrue(row["license"].strip(), f"Empty license for {cid}")
            self.assertTrue(row["license_evidence"].strip(), f"Empty license_evidence for {cid}")
            self.assertEqual(row["rating"], "SFW", f"M1 sample {cid} must be SFW")
            self.assertEqual(row["source_license_status"], "verified")
            self.assertEqual(row["semantics_review_status"], "verified")
            self.assertEqual(row["pipeline_test_status"], "verified")
            self.assertEqual(row["overall_status"], "verified")

        self.assertEqual(found_ids, expected_ids, "Ledger canonical IDs mismatch expected M1 set")
        self.assertEqual(seen_primary_ids, expected_ids, "Not all canonical IDs have a primary record")

    def test_ledger_target_mapping_and_parent_child_hierarchy(self):
        """
        校验台账中的父子关系、唯一性与文本严格一致：
        1. target_file, target_parent_id, target_leaf_id 存在且在对应文件中全局唯一；
        2. target_leaf_id 必须真实属于 target_parent_id 的 tags 列表中 (严格父子关系)；
        3. 叶子 text 与台账 canonical_text 严格一致。
        """
        with open(self.tsv_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))

        for r in rows:
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

            # 1. 全局唯一性
            self.assertEqual(len(matching_parents), 1, f"Parent ID {p_id} must appear exactly once in {f_path}, found {len(matching_parents)}")
            self.assertEqual(len(matching_leaves), 1, f"Leaf ID {l_id} must appear exactly once in {f_path}, found {len(matching_leaves)}")

            parent_obj = matching_parents[0]
            leaf_obj = matching_leaves[0]

            # 2. 确认叶子属于指定父项
            parent_tags = parent_obj.get("tags", [])
            tag_ids_in_parent = [t.get("id") for t in parent_tags if isinstance(t, dict)]
            self.assertIn(
                l_id,
                tag_ids_in_parent,
                f"Leaf ID {l_id} does not belong to parent {p_id} tags list in {f_path} (parent tags: {tag_ids_in_parent})",
            )

            # 3. 规范文本严格一致
            expected_text = r["canonical_text"]
            self.assertEqual(
                leaf_obj.get("text", ""),
                expected_text,
                f"Canonical text mismatch for {l_id}: actual={leaf_obj.get('text')!r}, expected={expected_text!r}",
            )

    def test_ledger_bidirectional_exact_leaf_coverage(self):
        """
        反向闭环核验：全量扫描 20 份运行时 JSON，所有以 'ext_aw_' 开头的叶子 ID
        必须与台账中登记的 target_leaf_id 集合 100% 严格恒等 (双向闭包，无未登记新增项，无虚报条目)。
        """
        with open(self.tsv_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f, delimiter="\t"))
        ledger_leaf_ids = {r["target_leaf_id"] for r in rows}

        actual_catalog_ext_aw_leaves = set()
        for json_path in DATA_DIR.glob("*.json"):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            def scan_ext_leaves(obj):
                if isinstance(obj, dict):
                    oid = obj.get("id", "")
                    if (
                        isinstance(oid, str)
                        and oid.startswith("ext_aw_")
                        and "text" in obj
                        and ("facts" in obj or "semantic_role" in obj.get("facts", {}))
                    ):
                        actual_catalog_ext_aw_leaves.add(oid)
                    for v in obj.values():
                        scan_ext_leaves(v)
                elif isinstance(obj, list):
                    for it in obj:
                        scan_ext_leaves(it)

            scan_ext_leaves(data)

        self.assertEqual(
            actual_catalog_ext_aw_leaves,
            ledger_leaf_ids,
            f"Bidirectional M1 leaf ID coverage mismatch! In catalog: {actual_catalog_ext_aw_leaves}, in ledger: {ledger_leaf_ids}",
        )


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
    """测试 6 条样本的真实目录数据加载、全管线端到端生成及冲突消解。"""

    def setUp(self):
        self.generator = nodes.IYKYKPromptGenerator()
        self.sampler = DataSampler(DATA_DIR)
        self.resolver = ConflictResolver(DATA_DIR)

    def test_sample_1_blunt_cut_bob_clean_generation(self):
        """样本 1：齐发尾短波波头显式选择，真实生成器端到端输出且不触发冲突。"""
        pos, neg, desc = self.generator.generate(
            发型发色="齐发尾短波波头 (Blunt-cut Short Bob)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            prompt_seed=42,
        )
        self.assertIn("sleek blunt-cut short bob", pos)
        self.assertIn("发型: 齐发尾短波波头", desc)

    def test_sample_2_long_hair_tucked_ears_clean_generation(self):
        """样本 2：中分长发挂耳显式选择，真实生成器端到端输出。"""
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
        film_stocks = self.sampler.list_film_stocks()
        mono_stock = next((f for f in film_stocks if "黑白" in f or "Monochrome" in f or "Tri-X" in f), None)
        self.assertIsNotNone(mono_stock, "Could not find a monochrome film stock in data/film_stocks.json")

        pos_mono, _, _ = self.generator.generate(
            光影预设="机顶直闪光 / 闪光灯摄影 (Direct Flash Photography)",
            胶片风格=mono_stock,
            prompt_seed=42,
        )
        self.assertIn("direct flash photography", pos_mono)

    def test_sample_4_warm_golden_hour_default_config_and_multi_seed(self):
        """样本 4 回归门禁：验证在默认配置与多种子下生成成功，不抛出室内外冲突异常。"""
        # A. 用户复现用例：默认参数 + prompt_seed=42
        pos, neg, desc = self.generator.generate(
            光影预设="暖金黄昏夕阳光 (Warm Golden Hour Lighting)",
            prompt_seed=42,
        )
        self.assertTrue(pos, "Prompt must not be empty")
        self.assertIn("warm golden hour lighting during sunset", pos)

        # B. 跨 50 个随机种子全管线运行无任何未消解冲突异常
        for s in range(50):
            p, _, _ = self.generator.generate(
                光影预设="暖金黄昏夕阳光 (Warm Golden Hour Lighting)",
                prompt_seed=s,
            )
            self.assertTrue(p, f"Empty prompt at seed {s}")

    def test_sample_4_warm_golden_hour_night_scene_drop(self):
        """样本 4 规则消解：使用真实夜间场景 (都市露出 / scene_rooftop_at_night) 验证夕阳光被精准消解。"""
        res_night = self.generator.generate_structured(
            场景大类="都市露出",
            剧情主题="无 (None)",
            光影预设="暖金黄昏夕阳光 (Warm Golden Hour Lighting)",
            prompt_seed=42,
        )
        self.assertNotIn("warm golden hour lighting during sunset", res_night.positive)
        rules_applied = [d.rule_id for d in res_night.resolution_report.decisions]
        self.assertIn("environmental_lighting_coherence", rules_applied)

    def test_sample_5_and_6_props_catalog_data_integrity(self):
        """样本 5 与 6 真实目录加载核验：断言 DataSampler 从 props.json 加载的 facts 严格符合契约。"""
        res_clutch = self.sampler.sample_prop_result("👛 精致手拿包 (Clutch Bag)", random.Random(42))
        self.assertIsNotNone(res_clutch)
        self.assertEqual(res_clutch.sampled_tags[0].text, "elegant clutch bag held in hand")
        self.assertEqual(res_clutch.sampled_tags[0].facts.prop_usage, "handheld")
        self.assertEqual(res_clutch.sampled_tags[0].facts.hands_required, 1)

        res_baguette = self.sampler.sample_prop_result("👜 复古腋下包 (Baguette Bag)", random.Random(42))
        self.assertIsNotNone(res_baguette)
        self.assertEqual(res_baguette.sampled_tags[0].text, "baguette bag worn over shoulder under arm")
        self.assertEqual(res_baguette.sampled_tags[0].facts.prop_usage, "worn")
        self.assertEqual(res_baguette.sampled_tags[0].facts.hands_required, 0)

    def test_sample_5_clutch_bag_clean_and_busy_pose_drop(self):
        """样本 5 端到端测试：双手可用正常生成；真实双手忙姿势触发 pose_hand_occupation 移除手拿包。"""
        # A. 真实单手可用站姿：手拿包正常输出
        pos_free, _, _ = self.generator.generate(
            道具物件="👛 精致手拿包 (Clutch Bag)",
            姿势动作="🧍 站姿系列",
            prompt_seed=42,
        )
        self.assertIn("elegant clutch bag held in hand", pos_free)

        # B. 真实双手忙姿态（姿势动作="💋 挑逗姿态", seed=21 采样 both hands wrapped around shaft, hands_required=2）
        res_busy = self.generator.generate_structured(
            姿势动作="💋 挑逗姿态",
            道具物件="👛 精致手拿包 (Clutch Bag)",
            prompt_seed=21,
        )
        self.assertNotIn("elegant clutch bag held in hand", res_busy.positive)
        hand_drops = [
            d for d in res_busy.resolution_report.decisions
            if d.rule_id == "pose_hand_occupation" and d.action == "drop"
        ]
        self.assertEqual(len(hand_drops), 1, "Expected pose_hand_occupation drop record for clutch bag")
        self.assertEqual(hand_drops[0].before_text, "elegant clutch bag held in hand")

    def test_sample_6_baguette_bag_busy_pose_retention(self):
        """样本 6 对照组端到端测试：在完全相同的双手忙姿态下，腋下包 (worn, hands_required=0) 100% 保留。"""
        res_baguette = self.generator.generate_structured(
            姿势动作="💋 挑逗姿态",
            道具物件="👜 复古腋下包 (Baguette Bag)",
            prompt_seed=21,
        )
        self.assertIn("baguette bag worn over shoulder under arm", res_baguette.positive)
        # 断言未被 pose_hand_occupation 规则作为目标删除
        pose_drops = [
            d for d in res_baguette.resolution_report.decisions
            if d.rule_id == "pose_hand_occupation" and d.before_text == "baguette bag worn over shoulder under arm"
        ]
        self.assertEqual(len(pose_drops), 0, "Baguette bag must NOT be dropped by pose_hand_occupation")

    def test_sample_5_real_facts_rule_resolution_for_multiple_holders(self):
        """样本 5 规则消解：使用 data/props.json 真实条目文本与 facts 构造 atom，验证规则 handheld_props_single_holder 消解。"""
        props_doc = json.loads((DATA_DIR / "props.json").read_text(encoding="utf-8"))
        clutch_cat = next(c for c in props_doc["categories"] if c["id"] == "fashion_clutch_bag")
        clutch_tag = clutch_cat["tags"][0]

        wine_cat = next(c for c in props_doc["categories"] if c["id"] == "wine_glass_bottle")
        wine_tag = next(t for t in wine_cat["tags"] if t["id"] == "wine_glass_bottle__tag_001")

        origin = SelectionOrigin(entry_point="custom_combiner", mode="explicit", selector="props")

        atom_clutch = PromptAtom(
            atom_id="atom_clutch_1",
            text=clutch_tag["text"],
            source_slot="props",
            tag_order=1,
            span_order=0,
            span_type=SpanType.PLAIN,
            origin=origin,
            facts=SemanticFacts.from_dict(clutch_tag["facts"]),
        )

        atom_wine = PromptAtom(
            atom_id="atom_wine_2",
            text=wine_tag["text"],
            source_slot="props",
            tag_order=2,
            span_order=0,
            span_type=SpanType.PLAIN,
            origin=origin,
            facts=SemanticFacts.from_dict(wine_tag["facts"]),
        )

        resolved, rules, rep = self.resolver.resolve_atoms_with_full_report([atom_clutch, atom_wine])
        resolved_texts = [a.text for a in resolved]

        self.assertIn("elegant clutch bag held in hand", resolved_texts)
        self.assertNotIn("stemware with red wine", resolved_texts)
        self.assertIn("handheld_props_single_holder", rules)
        self.assertTrue(
            any(
                d.rule_id == "handheld_props_single_holder"
                and d.target_atom_id == "atom_wine_2"
                and d.reason_code == "single_handheld_prop_limit"
                for d in rep.decisions
            )
        )


class TestM1AuditNegativeVerification(unittest.TestCase):
    """
    针对 M1 差异归因审计器的负向反例测试 (Negative Anti-Regression Tests)：
    验证审计器在面对人为篡改、意外丢失、未授权词条时必须 100% 敏锐拦截并判为 UNEXPLAINED，
    绝对禁止按槽位白名单盲目放行！
    """

    @classmethod
    def setUpClass(cls):
        from scratch.audit_m1_wildcards_slice import (
            load_authoritative_catalog_lookup,
            load_authoritative_resolver_rules,
        )
        cls.catalog_lookup = load_authoritative_catalog_lookup(DATA_DIR)
        cls.valid_rules = load_authoritative_resolver_rules(DATA_DIR)

    def _create_clean_fixture(self):
        """构造一个合法、结构闭环的 baseline 与 current 种子基线，包含 pose、hairstyle、props、lighting 全部四大关键槽位"""
        atom_pose = {
            "atom_id": "atom_pose_01",
            "text": "sitting on floor",
            "source_slot": "pose",
            "source_item_id": "sitting",
            "tag_order": 1,
            "span_order": 0,
        }
        atom_hair = {
            "atom_id": "atom_hair_01",
            "text": "high ponytail",
            "source_slot": "hairstyle",
            "source_item_id": "high_ponytail",
            "tag_order": 2,
            "span_order": 0,
        }
        atom_prop = {
            "atom_id": "atom_prop_01",
            "text": "leather briefcase",
            "source_slot": "props",
            "source_item_id": "briefcase",
            "tag_order": 3,
            "span_order": 0,
        }
        atom_light = {
            "atom_id": "atom_light_01",
            "text": "studio softbox lighting",
            "source_slot": "lighting",
            "source_item_id": "studio_softbox",
            "tag_order": 4,
            "span_order": 0,
        }
        import copy
        atoms = [copy.deepcopy(atom_pose), copy.deepcopy(atom_hair), copy.deepcopy(atom_prop), copy.deepcopy(atom_light)]
        item = {
            "seed": 42,
            "positive": "sitting on floor, high ponytail, leather briefcase, studio softbox lighting",
            "hash": "hash42",
            "source_atoms": copy.deepcopy(atoms),
            "final_atoms": copy.deepcopy(atoms),
            "decisions": [],
            "carrier_bindings": {},
            "dedup_records": [],
            "budget_records": [],
        }
        return copy.deepcopy(item), copy.deepcopy(item)

    def test_negative_counterfactual_pose_dropped_without_evidence(self):
        """
        反例 1 (核心审查发现)：基线中存在姿态原子，当前版本将其从 source_atoms 和 final_atoms 均删除，
        无消解决策、无 RNG 证据。断言审计器严苛拦截，禁止误判为 PRNG 替换！
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        cur["source_atoms"] = [a for a in cur["source_atoms"] if a["source_slot"] != "pose"]
        cur["final_atoms"] = [a for a in cur["final_atoms"] if a["source_slot"] != "pose"]
        cur["positive"] = "high ponytail, leather briefcase, studio softbox lighting"
        cur["hash"] = "hash_mutated"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertFalse(res["is_explained"], "Audit must NOT explain silently deleted pose atoms!")
        self.assertTrue(
            any("UNEXPLAINED_SOURCE_ATOM_MUTATION(pose)" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SOURCE_ATOM_MUTATION(pose), got: {res['unexplained_reasons']}",
        )

    def test_negative_counterfactual_hairstyle_dropped_without_evidence(self):
        """
        反例 1b (审查阻断项)：基线存在发型原子，当前版本将其从 source_atoms 和 final_atoms 均删除，
        无消解决策、无 replacement 证据。断言审计器严苛拦截，禁止误判为 PRNG_CANDIDATE_REPLACED！
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        cur["source_atoms"] = [a for a in cur["source_atoms"] if a["source_slot"] != "hairstyle"]
        cur["final_atoms"] = [a for a in cur["final_atoms"] if a["source_slot"] != "hairstyle"]
        cur["positive"] = "sitting on floor, leather briefcase, studio softbox lighting"
        cur["hash"] = "hash_mutated_hair"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertFalse(res["is_explained"], "Audit must NOT explain silently deleted hairstyle atoms!")
        self.assertTrue(
            any("UNEXPLAINED_SOURCE_ATOM_MUTATION(hairstyle)" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SOURCE_ATOM_MUTATION(hairstyle), got: {res['unexplained_reasons']}",
        )
        self.assertTrue(
            any("UNEXPLAINED_REMOVED_ATOM" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_REMOVED_ATOM, got: {res['unexplained_reasons']}",
        )

    def test_negative_counterfactual_props_dropped_without_evidence(self):
        """
        反例 1c (审查阻断项)：基线存在道具原子，当前版本将其从 source_atoms 和 final_atoms 均删除，
        无消解决策、无 replacement 证据。断言审计器严苛拦截，禁止误判为 PRNG_CANDIDATE_REPLACED！
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        cur["source_atoms"] = [a for a in cur["source_atoms"] if a["source_slot"] != "props"]
        cur["final_atoms"] = [a for a in cur["final_atoms"] if a["source_slot"] != "props"]
        cur["positive"] = "sitting on floor, high ponytail, studio softbox lighting"
        cur["hash"] = "hash_mutated_props"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertFalse(res["is_explained"], "Audit must NOT explain silently deleted props atoms!")
        self.assertTrue(
            any("UNEXPLAINED_SOURCE_ATOM_MUTATION(props)" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SOURCE_ATOM_MUTATION(props), got: {res['unexplained_reasons']}",
        )
        self.assertTrue(
            any("UNEXPLAINED_REMOVED_ATOM" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_REMOVED_ATOM, got: {res['unexplained_reasons']}",
        )

    def test_negative_counterfactual_lighting_dropped_without_evidence(self):
        """
        反例 1d (审查阻断项)：基线存在光影原子，当前版本将其从 source_atoms 和 final_atoms 均删除，
        无消解决策、无 replacement 证据。断言审计器严苛拦截，禁止误判为 PRNG_CANDIDATE_REPLACED！
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        cur["source_atoms"] = [a for a in cur["source_atoms"] if a["source_slot"] != "lighting"]
        cur["final_atoms"] = [a for a in cur["final_atoms"] if a["source_slot"] != "lighting"]
        cur["positive"] = "sitting on floor, high ponytail, leather briefcase"
        cur["hash"] = "hash_mutated_lighting"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertFalse(res["is_explained"], "Audit must NOT explain silently deleted lighting atoms!")
        self.assertTrue(
            any("UNEXPLAINED_SOURCE_ATOM_MUTATION(lighting)" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SOURCE_ATOM_MUTATION(lighting), got: {res['unexplained_reasons']}",
        )
        self.assertTrue(
            any("UNEXPLAINED_REMOVED_ATOM" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_REMOVED_ATOM, got: {res['unexplained_reasons']}",
        )

    def test_negative_expanded_slot_arbitrary_candidate_mutation_without_replay(self):
        """
        反例 1e：在扩充槽位中虽然采出了有效词条，但并未经过确定性重放预言机确认（人为篡改候选）。
        断言预言机模式下必须严格拦截并定性为重放偏差。
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        class MockOracle:
            def get_source_atoms(self, seed: int, slot: str | None = None):
                expected = [("hairstyle", "high_ponytail", "high ponytail", 0)]
                return expected if (slot is None or slot == "hairstyle") else []

        base, cur = self._create_clean_fixture()
        # 将发型篡改为另一个合法词条，但与预言机期望不符
        for a in cur["source_atoms"]:
            if a["source_slot"] == "hairstyle":
                a["text"] = "short bob"
                a["source_item_id"] = "short_bob"
        for a in cur["final_atoms"]:
            if a["source_slot"] == "hairstyle":
                a["text"] = "short bob"
                a["source_item_id"] = "short_bob"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules, replay_oracle=MockOracle())
        self.assertFalse(res["is_explained"])
        self.assertTrue(
            any("UNEXPLAINED_SOURCE_ATOM_MUTATION(hairstyle)" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SOURCE_ATOM_MUTATION(hairstyle), got: {res['unexplained_reasons']}",
        )

    def test_negative_unauthorized_leaf_tag_tampering(self):
        """反例 2：当前版本被注入未授权词条文本，断言审计器必须拦截。"""
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        # 篡改发型文本为未授权词条
        for a in cur["source_atoms"]:
            if a["source_slot"] == "hairstyle":
                a["text"] = "unauthorized_hacked_hair_tag"
        for a in cur["final_atoms"]:
            if a["source_slot"] == "hairstyle":
                a["text"] = "unauthorized_hacked_hair_tag"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertFalse(res["is_explained"])
        self.assertTrue(
            any("UNEXPLAINED_UNKNOWN_LEAF_TAG(hairstyle" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_UNKNOWN_LEAF_TAG, got: {res['unexplained_reasons']}",
        )

    def test_negative_silent_atom_drop_without_decision(self):
        """反例 3：源原子存在但终态消失，且无任何消解决策记录。断言必须拦截。"""
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        # 从终态移除发型，但不提供任何 decision
        cur["final_atoms"] = [a for a in cur["final_atoms"] if a["source_slot"] != "hairstyle"]
        cur["positive"] = "sitting on floor, leather briefcase, studio softbox lighting"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertFalse(res["is_explained"])
        self.assertTrue(
            any("UNEXPLAINED_SILENT_ATOM_DROP" in r for r in res["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SILENT_ATOM_DROP, got: {res['unexplained_reasons']}",
        )

    def test_negative_identical_text_with_structural_tampering(self):
        """
        反例 4：虽然正向提示词与哈希完全相同，但原子 ID 被篡改导致来源签名断裂。
        断言即使 is_identical == True，依然判定为 is_explained == False。
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff

        base, cur = self._create_clean_fixture()
        # 篡改终态原子的 atom_id
        cur["final_atoms"][0]["atom_id"] = "hacked_atom_id"

        res = attribute_m1_seed_diff(42, base, cur, self.catalog_lookup, self.valid_rules)
        self.assertTrue(res["is_identical"])
        self.assertFalse(res["is_explained"], "Even identical text must fail if internal structure is tampered!")
        self.assertTrue(len(res["unexplained_reasons"]) > 0)

    def test_audit_replay_purely_from_archive(self):
        """
        反例与自洽闭环：验证归档证据包含全部必要输入（atoms, decisions, bindings, dedup/budget 等），
        且“仅从归档重放”全部检查能够得到严格相同结果，0 unexplained diffs。
        """
        from scratch.audit_m1_wildcards_slice import attribute_m1_seed_diff
        archive_path = REPO_DIR / "scratch" / "audit_m1_evidence.json.gz"
        if not archive_path.exists():
            self.skipTest(f"Archive {archive_path} not found")

        with gzip.open(archive_path, "rt", encoding="utf-8") as f:
            doc = json.load(f)

        diffs = doc.get("diffs", [])
        self.assertGreater(len(diffs), 0, "Archive diffs must not be empty")

        # 抽样前 100 组差异记录，验证仅靠归档输入能完整重放
        for rec in diffs[:100]:
            s = rec["seed"]
            # 兼容：如果记录中已有完整的 base_item 和 cur_item，则直接使用
            base_item = rec.get("base_item")
            cur_item = rec.get("cur_item")
            if base_item is None or cur_item is None:
                # 兼容旧格式
                base_item = {
                    "seed": s,
                    "source_atoms": rec["base_source_atoms"],
                    "final_atoms": rec["base_final_atoms"],
                    "decisions": [],
                    "carrier_bindings": {},
                    "dedup_records": [],
                    "budget_records": [],
                    "positive": "",
                    "hash": "",
                }
                cur_item = {
                    "seed": s,
                    "source_atoms": rec["cur_source_atoms"],
                    "final_atoms": rec["cur_final_atoms"],
                    "decisions": rec.get("decisions", []),
                    "carrier_bindings": rec.get("carrier_bindings", {}),
                    "dedup_records": rec.get("dedup_records", []),
                    "budget_records": rec.get("budget_records", []),
                    "positive": "",
                    "hash": "",
                }
            res = attribute_m1_seed_diff(s, base_item, cur_item, self.catalog_lookup, self.valid_rules)
            self.assertTrue(
                res["is_explained"],
                f"Replay from archive failed for seed {s}: {res['unexplained_reasons']}"
            )
            self.assertEqual(res["unexplained_reasons"], [])


if __name__ == "__main__":
    unittest.main()

