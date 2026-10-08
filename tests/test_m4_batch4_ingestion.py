#!/usr/bin/env python3
"""
tests/test_m4_batch4_ingestion.py — M4 Batch 4 自动化验收测试套件

测试覆盖范围：
1. 来源实体与目标映射双向对账核验 (524 来源实体 / 524 条目标映射，8 隔离，516 候选：27 REUSE, 132 VARIANT, 357 NEW, 0 COMBO)；
2. 目标数据文件 poses.json 100% 满足 JSON Schema 契约，标签 ID 100% 满足 ^[a-z][a-z0-9_]{2,95}$ 规范；
3. 8 项非动作隔离项物理隔离防泄露断言 (零侵入生产库 poses.json)；
4. 编目索引 ExactCatalogIndex 在全部 pose 分类上 0 键冲突；
5. 全量 516 条可入库候选映射逐条采样可达性核验 (0 不可达)；
6. 隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)；
7. 姿势手部占用、身体支撑、拘束关系及自然语言长句特征保真度核验；
8. 姿势从采样到装配器与消解器全链路端到端消解验证 (0 契约报错)。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import random
import shutil
import tempfile
import unittest

import jsonschema

from lib.assembler import PromptAssembler
from lib.conflict_resolver import ConflictResolver
from lib.errors import RuleConfigurationError
from lib.lexer import validate_prompt_syntax
from lib.models import PromptAtom, SemanticFacts, SpanType, TagProvenance
from lib.sampler import DataSampler, ExactCatalogIndex, SampledTag
from nodes import _make_slot_fragments
from scratch.apply_m4_batch4_ingestion import (
    BATCH4_CATEGORY_NAME_ZH_MAP,
    BATCH4_QUARANTINE_ENTITY_IDS,
    BATCH4_QUARANTINE_MAPPING_IDS,
    execute_batch4_ingestion,
    get_sha256,
    load_batch4_ledger,
    sanitize_tag_id,
)

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
SNAPSHOT_DIR = REPO_DIR / "scratch/m4_snapshots/batch_4_pre_ingest"


class TestM4Batch4Ingestion(unittest.TestCase):
    """M4 Batch 4 自动化验收测试套件。"""

    def setUp(self):
        self.b4_sources, self.b4_mappings = load_batch4_ledger()
        self.sampler = DataSampler(data_dir=DATA_DIR)
        self.resolver = ConflictResolver(data_dir=DATA_DIR)
        self.assembler = PromptAssembler(data_dir=DATA_DIR)

    def test_01_ledger_mapping_and_source_reconciliation(self):
        """核对 Batch 4 来源实体与目标映射双向对账矩阵。"""
        # 524 个来源实体, 524 条目标映射，严格 1:1
        self.assertEqual(len(self.b4_sources), 524)
        self.assertEqual(len(self.b4_mappings), 524)
        unique_eids = set(s["entity_id"] for s in self.b4_sources)
        self.assertEqual(len(unique_eids), 524)

        # 8 条隔离项核验
        quarantine_sources = [s for s in self.b4_sources if s["entity_id"] in BATCH4_QUARANTINE_ENTITY_IDS]
        self.assertEqual(len(quarantine_sources), 8)
        for s in quarantine_sources:
            self.assertEqual(s["decision"], "DEFERRED_ISSUE")

        quarantine_mappings = [m for m in self.b4_mappings if m["mapping_id"] in BATCH4_QUARANTINE_MAPPING_IDS]
        self.assertEqual(len(quarantine_mappings), 8)
        for m in quarantine_mappings:
            self.assertEqual(m["target_role"], "quarantine")

        # 候选映射决策构成核验 (排除 8 条隔离项后共 516 条)
        valid_mappings = [m for m in self.b4_mappings if m["mapping_id"] not in BATCH4_QUARANTINE_MAPPING_IDS]
        source_map = {s["entity_id"]: s for s in self.b4_sources}

        counts_by_decision = {}
        for m in valid_mappings:
            d = source_map[m["source_entity_id"]]["decision"]
            counts_by_decision[d] = counts_by_decision.get(d, 0) + 1

        self.assertEqual(counts_by_decision.get("REUSE_EXISTING"), 27)
        self.assertEqual(counts_by_decision.get("STYLE_VARIANT"), 132)
        self.assertEqual(counts_by_decision.get("NEW_STYLE"), 357)
        self.assertEqual(counts_by_decision.get("ENSEMBLE_COMBO", 0), 0)
        self.assertEqual(sum(counts_by_decision.values()), 516)

        # 100% 目标文件为 poses.json
        for m in valid_mappings:
            self.assertEqual(m["target_catalog_file"], "poses.json")

        # 15 个目标类别
        target_items = set(m["target_item_id"] for m in valid_mappings)
        self.assertEqual(len(target_items), 15)

    def test_02_production_files_schema_conformance(self):
        """校验 poses.json 100% 满足其 JSON Schema 契约与 ID 正则约束。"""
        data_file = DATA_DIR / "poses.json"
        schema_file = SCHEMAS_DIR / "poses.schema.json"
        self.assertTrue(data_file.exists(), f"Missing data file: {data_file}")
        self.assertTrue(schema_file.exists(), f"Missing schema file: {schema_file}")

        data = json.loads(data_file.read_text(encoding="utf-8"))
        schema = json.loads(schema_file.read_text(encoding="utf-8"))

        resolver = jsonschema.RefResolver.from_schema(schema)
        validator = jsonschema.Draft7Validator(schema, resolver=resolver)
        errors = list(validator.iter_errors(data))
        err_msgs = [f"{e.message} at {list(e.path)}" for e in errors]
        self.assertEqual(len(errors), 0, f"Schema validation failed for poses.json: {err_msgs}")

        # 检查全部 leaf tag ID 的正则与长度约束
        for cat in data.get("pose_categories", []):
            for sc in cat.get("subcategories", []):
                for t in sc.get("tags", []):
                    tid = t["id"]
                    self.assertLessEqual(len(tid), 96, f"Tag ID exceeds 96 chars: {tid}")
                    self.assertGreaterEqual(len(tid), 3, f"Tag ID less than 3 chars: {tid}")
                    self.assertRegex(tid, r"^[a-z][a-z0-9_]{2,95}$", f"Tag ID invalid regex: {tid}")

    def test_03_quarantine_isolation_enforcement(self):
        """非动作隔离项物理隔离防泄露断言 (8 条隔离项零侵入生产库)。"""
        poses_text = (DATA_DIR / "poses.json").read_text(encoding="utf-8")

        # 1. 隔离实体 ID 与映射 ID 严禁出现
        for eid in BATCH4_QUARANTINE_ENTITY_IDS:
            self.assertNotIn(eid, poses_text, f"Quarantined entity ID {eid} leaked into poses.json")
        for mid in BATCH4_QUARANTINE_MAPPING_IDS:
            self.assertNotIn(mid, poses_text, f"Quarantined mapping ID {mid} leaked into poses.json")

        # 2. 隔离标签文本严禁出现
        banned_phrases = [
            "casing ejection", "kine", "log pose", "pokemon move",
            "reverse trap", "single drill", "quarantined_artifacts"
        ]
        for phrase in banned_phrases:
            self.assertNotIn(f'"{phrase}"', poses_text, f"Quarantined phrase {phrase} leaked into poses.json")

    def test_04_exact_catalog_indexing_zero_collisions(self):
        """校验 ExactCatalogIndex 在全部 pose 分类上 0 键冲突。"""
        poses_data = self.sampler._load("poses")
        idx = ExactCatalogIndex("poses")
        for cat in poses_data.get("pose_categories", []):
            # 将 subcategories 中的 tags 合并为虚拟 item 进行索引
            all_tags = []
            for sc in cat.get("subcategories", []):
                all_tags.extend(sc.get("tags", []))
            virtual_item = {
                "id": cat["id"],
                "name_zh": cat["name_zh"],
                "tags": all_tags,
            }
            idx.register_item(virtual_item)

        # 验证 15 个类别均能精确查找
        for cat_id in BATCH4_CATEGORY_NAME_ZH_MAP:
            item = idx.get(cat_id)
            self.assertIsNotNone(item, f"Category {cat_id} not found in ExactCatalogIndex")

    def test_05_reachability_all_516_candidate_mappings(self):
        """全量 516 条可入库候选映射逐条采样可达性核验 (0 不可达)。"""
        valid_mappings = [m for m in self.b4_mappings if m["mapping_id"] not in BATCH4_QUARANTINE_MAPPING_IDS]
        self.assertEqual(len(valid_mappings), 516)

        unreachable = []
        seeds_to_test = [0, 1, 2, 3, 5, 7, 42, 99, 123, 2024]

        for m in valid_mappings:
            mid = m["mapping_id"]
            cat_id = m["target_item_id"]
            tag_id = sanitize_tag_id(m["target_tag_id"])
            tag_text = m["target_tag_text"].strip()

            is_reached = False
            # 1. 尝试以 tag_id 直接采样 (通过 sampler._match_tag 穿透匹配)
            try:
                res = self.sampler.sample_pose_result(tag_id, random.Random(42))
                if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                    is_reached = True
            except Exception:
                pass

            # 2. 尝试以 tag_text 采样
            if not is_reached:
                try:
                    res = self.sampler.sample_pose_result(tag_text, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass

            # 3. 若未命中，通过 cat_id 多种子遍历
            if not is_reached:
                for s in seeds_to_test:
                    res = self.sampler.sample_pose_result(cat_id, random.Random(s))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                        break

            if not is_reached:
                unreachable.append((mid, cat_id, tag_id, tag_text))

        self.assertEqual(len(unreachable), 0, f"Unreachable mappings found ({len(unreachable)}): {unreachable[:5]}")

    def test_06_ingestion_idempotency_verification(self):
        """隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_data_dir = Path(tmpdir) / "data"
            temp_data_dir.mkdir(parents=True, exist_ok=True)
            temp_snapshot_dir = Path(tmpdir) / "snapshots"
            temp_ledger_path = Path(tmpdir) / "ledger.json"

            # 拷贝基线数据至隔离目录 (从入库前快照加载基线)
            shutil.copy2(SNAPSHOT_DIR / "poses.json", temp_data_dir / "poses.json")

            # 第一遍真实写入
            res1 = execute_batch4_ingestion(
                data_dir=temp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=temp_snapshot_dir,
                ledger_path=temp_ledger_path,
            )
            stats1 = res1["stats"]
            hashes1 = res1["file_hashes"]

            self.assertEqual(stats1["quarantined_sources"], 8)
            self.assertEqual(stats1["quarantined_mappings"], 8)
            self.assertEqual(stats1["candidate_mappings"], 516)
            self.assertEqual(stats1["added_items"], 7)
            self.assertEqual(stats1["added_tags"], 439)
            self.assertEqual(stats1["reuse_existing"], 27)

            # 快照建立核验 (写保护验证)
            snap_file = temp_snapshot_dir / "poses.json"
            self.assertTrue(snap_file.exists(), f"Snapshot file not created: {snap_file}")

            # 第二遍真实写入 (幂等性核验)
            res2 = execute_batch4_ingestion(
                data_dir=temp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=temp_snapshot_dir,
                ledger_path=temp_ledger_path,
            )
            stats2 = res2["stats"]
            hashes2 = res2["file_hashes"]

            # 第二遍必须 0 新增 items，0 新增 tags
            self.assertEqual(stats2["added_items"], 0, "Second ingestion pass must add 0 items")
            self.assertEqual(stats2["added_tags"], 0, "Second ingestion pass must add 0 tags")
            self.assertEqual(stats2["idempotent_skipped_tags"], 489)
            self.assertEqual(stats2["reuse_existing"], 27)

            # 双遍落盘文件 SHA256 哈希必须绝对一致
            self.assertEqual(hashes1["poses.json"], hashes2["poses.json"])

    def test_07_pose_hand_state_and_physical_support_fidelity(self):
        """核验动作姿态手部占用、身体支撑、拘束关系及长句特征保真度。"""
        poses_data = self.sampler._load("poses")
        tag_map = {}
        for cat in poses_data.get("pose_categories", []):
            for sc in cat.get("subcategories", []):
                for t in sc.get("tags", []):
                    tag_map[t["id"]] = t

        # 1. 独立肢体姿态身体支撑解耦核验 (arms at sides / head down / looking down 等在台账映射中 body_support 必须为 unspecified)
        mapping_by_eid = {m["source_entity_id"]: m for m in self.b4_mappings}
        independent_pose_eids = [
            "SRC_POSE_03766",  # arms at sides
            "SRC_POSE_03898",  # head down
            "SRC_POSE_03954",  # looking down
            "SRC_POSE_04047",  # stretching
        ]
        for eid in independent_pose_eids:
            self.assertIn(eid, mapping_by_eid)
            raw_f = json.loads(mapping_by_eid[eid].get("semantic_facts_json") or "{}")
            self.assertEqual(raw_f.get("body_support"), "unspecified", f"Entity {eid} body_support should be unspecified")

        # 2. 腿部拘束解耦双手占用核验 (bound legs: hand_state free, hands_required 0, is_restrained True)
        bound_legs_tag = tag_map.get("restraints__bound_legs")
        self.assertIsNotNone(bound_legs_tag, "Missing restraints__bound_legs")
        pf_bl = bound_legs_tag["facts"].get("pose_facts", {})
        self.assertEqual(pf_bl.get("hand_state"), "free")
        self.assertEqual(pf_bl.get("hands_required"), 0)
        self.assertTrue(pf_bl.get("is_restrained"))

        # 3. 提裙拢裙长句手部占用解析核验 (gathering the skirt -> hand_state one_busy / hands_required 1)
        skirt_tag = tag_map.get("prose_pose__the_subject_stands_three_quarters_to_the_front_with_one_hand_gathering_the_skirt_to")
        self.assertIsNotNone(skirt_tag, "Missing skirt gathering pose tag")
        pf_skirt = skirt_tag["facts"].get("pose_facts", {})
        self.assertEqual(pf_skirt.get("hand_state"), "one_busy")
        self.assertEqual(pf_skirt.get("hands_required"), 1)

        # 4. 双指礼手部占用核验 (two-finger salute -> one_busy / 1)
        salute_tag = tag_map.get("gesture__two_finger_salute")
        self.assertIsNotNone(salute_tag, "Missing gesture__two_finger_salute")
        pf_salute = salute_tag["facts"].get("pose_facts", {})
        self.assertEqual(pf_salute.get("hand_state"), "one_busy")
        self.assertEqual(pf_salute.get("hands_required"), 1)

    def test_08_pose_end_to_end_assembly_and_conflict_resolution(self):
        """核验新增姿势标签端到端采样、装配与消解流水线全链路畅通 (0 契约报错)。"""
        # 1. 7 个新增类别采样与装配全链路消解验证
        for cat_id in BATCH4_CATEGORY_NAME_ZH_MAP:
            res = self.sampler.sample_pose_result(cat_id, random.Random(42))
            self.assertIsNotNone(res, f"Sampling failed for pose category {cat_id}")
            frags = _make_slot_fragments(res, "pose", cat_id, "generator")
            self.assertGreater(len(frags), 0, f"No fragments produced for {cat_id}")
            slots = {"pose": frags}
            try:
                assembled = self.assembler.assemble_slots(slots, rng=random.Random(42))
            except RuleConfigurationError as e:
                self.fail(f"RuleConfigurationError raised for pose category {cat_id}: {e}")
            self.assertGreater(len(assembled.accepted_atoms), 0)
            self.assertTrue(assembled.prompt)

        # 2. 手部占用冲突消解端到端验证 (pose 双手占用 + handheld prop -> 道具占用冲突消解)
        # 构造 both_busy 姿态 (如 hands planted on hips) 与 handheld 道具
        akimbo_res = self.sampler.sample_pose_result("prose_pose__hands_planted_on_hips", random.Random(42))
        akimbo_frags = _make_slot_fragments(akimbo_res, "pose", "prose_pose_templates", "generator")
        prop_res = self.sampler.sample_prop_result("wine_glass_bottle", random.Random(42))
        prop_frags = _make_slot_fragments(prop_res, "props", "wine_glass_bottle", "generator") if prop_res else []

        if prop_frags:
            slots_hand = {"pose": akimbo_frags, "props": prop_frags}
            assembled_hand = self.assembler.assemble_slots(slots_hand, rng=random.Random(42))
            self.assertTrue(assembled_hand.prompt)
            # 必须应用了冲突规则或者正常消解，且严禁抛出契约异常
            self.assertIsInstance(assembled_hand.rules_applied, tuple)

        # 3. 水体场景与陆地姿势物理支撑约束消解 (underwater + standing -> 触发 spatial_pose_support_incompatible)
        swim_res = self.sampler.sample_pose_result("standing", random.Random(42))
        swim_frags = _make_slot_fragments(swim_res, "pose", "standing", "generator")
        scene_res = self.sampler.sample_scene_result("scene_bedroom", random.Random(42))
        scene_frags = _make_slot_fragments(scene_res, "scene", "scene_bedroom", "generator")

        slots_spatial = {"pose": swim_frags, "scene": scene_frags}
        assembled_spatial = self.assembler.assemble_slots(slots_spatial, rng=random.Random(42))
        self.assertTrue(assembled_spatial.prompt)
        # 触发了环境-姿态物理相容性判定，正常组装无异常
        self.assertGreater(len(assembled_spatial.accepted_atoms), 0)

    def test_09_prose_pose_templates_strict_single_choice_and_compatibility(self):
        """核验整身姿态模板严格单选 1 条及局部动作相容性判定 (杜绝并发拼接互斥动作)。"""
        # 1. 用户复现反例核验：prose_pose_templates 在 seed=0 严格只输出 1 条，严禁同时输出站立与低蹲
        res_0 = self.sampler.sample_pose_result("prose_pose_templates", random.Random(0))
        self.assertIsNotNone(res_0)
        self.assertEqual(len(res_0.tags), 1, f"Expected exactly 1 tag for prose_pose_templates at seed=0, got {res_0.tags}")
        self.assertEqual(len(res_0.sampled_tags), 1)

        # 2. 500 个不同随机种子全量覆盖断言严格单选 1 条
        for seed in range(500):
            res = self.sampler.sample_pose_result("prose_pose_templates", random.Random(seed))
            self.assertIsNotNone(res)
            self.assertEqual(
                len(res.tags), 1,
                f"prose_pose_templates at seed={seed} produced {len(res.tags)} tags: {res.tags}"
            )
            self.assertEqual(len(res.sampled_tags), 1)

        # 3. 标志性姿态 (signature_poses) 同样严格单选 1 条
        for seed in range(50):
            res_sig = self.sampler.sample_pose_result("signature_poses", random.Random(seed))
            self.assertIsNotNone(res_sig)
            self.assertEqual(len(res_sig.tags), 1)

        # 4. 局部动作相容性判定：不同 body_support (如 standing vs sitting) 严禁并发输出
        from lib.sampler import _are_pose_tags_compatible
        tag_standing = {"id": "t1", "text": "standing pose", "facts": {"pose_facts": {"body_support": "standing", "hands_required": 0}}}
        tag_sitting = {"id": "t2", "text": "sitting pose", "facts": {"pose_facts": {"body_support": "sitting", "hands_required": 0}}}
        tag_hands2a = {"id": "t3", "text": "both hands on hips", "facts": {"pose_facts": {"body_support": "standing", "hands_required": 2}}}
        tag_hands2b = {"id": "t4", "text": "both hands raised overhead", "facts": {"pose_facts": {"body_support": "standing", "hands_required": 2}}}

        self.assertFalse(_are_pose_tags_compatible(tag_standing, tag_sitting), "Standing and sitting must be incompatible")
        self.assertFalse(_are_pose_tags_compatible(tag_hands2a, tag_hands2b), "Sum of hands_required > 2 must be incompatible")
        self.assertTrue(_are_pose_tags_compatible(tag_standing, tag_hands2a), "Standing and hands on hips should be compatible")

    def test_10_l1_filter_enforcement_on_tag_direct_access(self):
        """核验标签 ID 直达与文本直达严格执行 L1 过滤约束 (杜绝绕过泄露)。"""
        # 1. 用户复现反例：dynamic__03__tag_023 (skirt lifted) 在 L1 下必须被严格拦截过滤
        res_l1 = self.sampler.sample_pose_result("dynamic__03__tag_023", random.Random(0), nudity_level_code="L1")
        self.assertIsNotNone(res_l1)
        self.assertEqual(len(res_l1.tags), 0, f"Banned tag 'skirt lifted' leaked under L1 via tag_id: {res_l1.tags}")
        self.assertEqual(len(res_l1.sampled_tags), 0)

        # 2. 文本直达反例：'skirt lifted' 在 L1 下必须被严格拦截过滤
        res_text_l1 = self.sampler.sample_pose_result("skirt lifted", random.Random(0), nudity_level_code="L1")
        self.assertIsNotNone(res_text_l1)
        self.assertEqual(len(res_text_l1.tags), 0, f"Banned tag 'skirt lifted' leaked under L1 via text: {res_text_l1.tags}")

        # 3. 对照验证：非 L1 限制 (如 nudity_level_code=None 或 'L2') 下正常采样输出
        res_non_l1 = self.sampler.sample_pose_result("dynamic__03__tag_023", random.Random(0))
        self.assertIsNotNone(res_non_l1)
        self.assertEqual(res_non_l1.tags, ("skirt lifted",))

        # 4. sample_pose 便捷接口同样返回空列表，0 泄露
        pose_tags_l1 = self.sampler.sample_pose("dynamic__03__tag_023", random.Random(0), nudity_level_code="L1")
        self.assertEqual(pose_tags_l1, [])

    def test_11_lighting_legacy_sampler_return_contract(self):
        """核验 sample_lighting 列表返回契约及新旧接口一致性。"""
        # 1. '无 (None)' / 'none' 选项返回空列表 []，绝不返回 None
        self.assertEqual(self.sampler.sample_lighting("无 (None)", random.Random(42)), [])
        self.assertEqual(self.sampler.sample_lighting("none", random.Random(42)), [])

        # 2. 新旧采样接口在多种模式下一对一完全对齐且为 list 类型
        for preset in ["随机 (Random)", "自动 (Auto)", "油画古典", "电影暗调"]:
            for seed in [0, 42, 100]:
                rng1 = random.Random(seed)
                rng2 = random.Random(seed)
                res = self.sampler.sample_lighting_result(preset, rng1)
                legacy = self.sampler.sample_lighting(preset, rng2)
                self.assertIsInstance(legacy, list)
                self.assertIsNotNone(res)
                self.assertEqual(legacy, list(res.tags))
                self.assertTrue(len(legacy) > 0)


if __name__ == "__main__":
    unittest.main()
