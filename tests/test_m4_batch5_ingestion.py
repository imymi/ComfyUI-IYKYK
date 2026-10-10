#!/usr/bin/env python3
"""
tests/test_m4_batch5_ingestion.py — M4 Batch 5 场景环境库自动化验收测试套件

测试覆盖范围：
1. 来源实体与目标映射双向对账核验 (2,335 来源实体 / 2,335 条目标映射，71 隔离，2,264 候选：37 REUSE, 7 VARIANT, 2143/2144 NEW, 76 COMBO)；
2. 目标数据文件 scenes.json 100% 满足 JSON Schema 契约，标签 ID 与条目 ID 100% 满足 ^[a-z][a-z0-9_]{2,95}$ 规范；
3. 71 项非空间/纯剧情/无场地隔离项物理隔离防泄露断言 (零侵入生产库 scenes.json)；
4. 编目索引 ExactCatalogIndex 在全部场景分类上 0 键冲突；
5. 全量 2,264 条可入库目标映射逐条采样可达性核验 (0 不可达)；
6. 隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)；
7. 场景时间解耦、真实空间分类、长句槽位归属等原则 1/2/3 语义门禁保真度核验；
8. 场景从采样到装配器全链路端到端消解验证 (0 契约报错)；
9. 自然语言整景叙述模板 prose_scene_templates 严格单选 1 条验证 (杜绝拼接冲突长句)；
10. 通用大池单选场所相容性断言 (杜绝随机串接两个独立场所)。
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
from lib.models import PromptAtom, SemanticFacts, SpanType, TagProvenance
from lib.sampler import DataSampler, ExactCatalogIndex, SampledTag
from nodes import _make_slot_fragments
from scratch.apply_m4_batch5_ingestion import (
    BATCH5_CATEGORY_ROUTING,
    BATCH5_QUARANTINE_ENTITY_IDS,
    execute_batch5_ingestion,
    get_sha256,
    load_batch5_ledger,
    sanitize_id,
)

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
SNAPSHOT_DIR = REPO_DIR / "scratch/m4_snapshots/batch_5_pre_ingest"


class TestM4Batch5Ingestion(unittest.TestCase):
    """M4 Batch 5 自动化验收测试套件。"""

    def setUp(self):
        self.b5_sources, self.b5_mappings = load_batch5_ledger()
        self.sampler = DataSampler(data_dir=DATA_DIR)
        self.assembler = PromptAssembler(data_dir=DATA_DIR)

    def test_01_ledger_mapping_and_source_reconciliation(self):
        """核对 Batch 5 来源实体与目标映射双向对账矩阵。"""
        self.assertEqual(len(self.b5_sources), 2335, "Batch 5 来源实体总数必须为 2335")
        self.assertEqual(len(self.b5_mappings), 2335, "Batch 5 目标映射总数必须为 2335")

        # 验证 71 条隔离项绝对排除
        quarantined_sources = [s for s in self.b5_sources if s["entity_id"] in BATCH5_QUARANTINE_ENTITY_IDS]
        self.assertEqual(len(quarantined_sources), 71, "隔离实体数量必须严格为 71")

        quarantined_mappings = [
            m for m in self.b5_mappings
            if m["source_entity_id"] in BATCH5_QUARANTINE_ENTITY_IDS
            or m.get("target_role") == "quarantined"
            or m.get("target_item_id") == "quarantine"
        ]
        self.assertEqual(len(quarantined_mappings), 71, "隔离映射数量必须严格为 71")

        # 候选映射数必须严格对齐 2,264 条
        candidate_mappings = [
            m for m in self.b5_mappings
            if m["source_entity_id"] not in BATCH5_QUARANTINE_ENTITY_IDS
            and m.get("target_item_id") != "quarantine"
        ]
        self.assertEqual(len(candidate_mappings), 2264, "可入库候选映射数必须严格为 2264")

        # 对账决定分布
        decisions = {}
        for s in self.b5_sources:
            d = s.get("decision")
            decisions[d] = decisions.get(d, 0) + 1

        self.assertEqual(decisions["DEFERRED_ISSUE"], 71)
        self.assertEqual(decisions["REUSE_EXISTING"], 37)
        self.assertEqual(decisions["STYLE_VARIANT"], 7)
        self.assertEqual(decisions["NEW_STYLE"], 2144)
        self.assertEqual(decisions["ENSEMBLE_COMBO"], 76)

    def test_02_scenes_json_schema_compliance_and_id_regex(self):
        """核验 scenes.json 100% 遵循 Draft-7 JSON Schema 及命名正则约束。"""
        scenes_path = DATA_DIR / "scenes.json"
        schema_path = SCHEMAS_DIR / "scenes.schema.json"

        scenes_data = json.loads(scenes_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        # 模式校验
        resolver = jsonschema.RefResolver.from_schema(schema)
        validator = jsonschema.Draft7Validator(schema, resolver=resolver)
        errors = list(validator.iter_errors(scenes_data))
        self.assertEqual(len(errors), 0, f"Schema validation failed: {[e.message for e in errors[:5]]}")

        # ID 规范与全局上下文检查
        import re
        id_pattern = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
        from scripts.validate_data import VALID_CONTEXT_ENUMS

        seen_item_ids = set()
        seen_leaf_ids = set()

        for g in scenes_data.get("scenes", []):
            cat_name = g.get("category", "")
            self.assertTrue(cat_name, "Category name cannot be empty")
            for it in g.get("items", []):
                iid = it.get("id", "")
                self.assertTrue(id_pattern.match(iid), f"Item ID {iid!r} violates regex contract")
                self.assertNotIn(iid, seen_item_ids, f"Duplicate item ID {iid!r}")
                seen_item_ids.add(iid)

                for c in it.get("context_ids", []):
                    self.assertIn(c, VALID_CONTEXT_ENUMS, f"Invalid context_id {c!r} in item {iid}")

                anchors = it.get("anchor_tags", [])
                details = it.get("detail_tags", [])
                self.assertGreater(len(anchors), 0, f"Item {iid} has 0 anchor tags")

                a_texts = set()
                for a in anchors:
                    aid = a["id"]
                    self.assertTrue(id_pattern.match(aid), f"Anchor tag ID {aid!r} violates regex")
                    self.assertNotIn(aid, seen_leaf_ids, f"Duplicate leaf ID {aid!r}")
                    seen_leaf_ids.add(aid)
                    a_texts.add(a["text"].strip().lower())

                d_texts = set()
                for d in details:
                    did = d["id"]
                    self.assertTrue(id_pattern.match(did), f"Detail tag ID {did!r} violates regex")
                    self.assertNotIn(did, seen_leaf_ids, f"Duplicate leaf ID {did!r}")
                    seen_leaf_ids.add(did)
                    d_texts.add(d["text"].strip().lower())

                # anchor 与 detail 文本无交集
                overlap = a_texts & d_texts
                self.assertEqual(len(overlap), 0, f"Item {iid} has overlapping anchor/detail texts: {overlap}")

    def test_03_quarantine_isolation_and_anti_leakage(self):
        """核验 71 项隔离实体物理隔离防泄露 (零侵入生产库 scenes.json)。"""
        scenes_text = (DATA_DIR / "scenes.json").read_text(encoding="utf-8")
        scenes_data = json.loads(scenes_text)

        # 1. 任何隔离实体 ID 严禁出现在 scenes.json
        for q_eid in BATCH5_QUARANTINE_ENTITY_IDS:
            self.assertNotIn(q_eid, scenes_text, f"Quarantined entity {q_eid} leaked into scenes.json text!")

        # 2. 任何 quarantine 标识严禁作为 item_id
        for g in scenes_data.get("scenes", []):
            for it in g.get("items", []):
                self.assertNotEqual(it.get("id"), "quarantine", "Found 'quarantine' item in scenes.json!")
                for t in it.get("anchor_tags", []) + it.get("detail_tags", []):
                    self.assertFalse(t["id"].startswith("quarantine__"), f"Found quarantined tag {t['id']}")
                    self.assertNotEqual(t.get("facts", {}).get("semantic_role"), "quarantined")

        # 3. 典型无物理场地活动文本（如滑冰求婚、公路旅行接吻等）绝对防泄露
        self.assertNotIn("a surprise proposal while ice skating", scenes_text)
        self.assertNotIn("a surprise proposal while stargazing", scenes_text)
        self.assertNotIn("a romantic kiss on a scenic bike ride", scenes_text)
        self.assertNotIn("a romantic kiss on a scenic road trip", scenes_text)
        self.assertNotIn("a surprise proposal on a scenic drive", scenes_text)
        self.assertNotIn("a group of survivors in a zombie outbreak", scenes_text)
        self.assertNotIn("a quest for a legendary sword", scenes_text)

    def test_04_exact_catalog_index_collision_free(self):
        """编目索引 ExactCatalogIndex 在场景数据上 0 键冲突核验。"""
        scenes_data = self.sampler._load("scenes")
        idx = ExactCatalogIndex("scenes")

        for g in scenes_data.get("scenes", []):
            for it in g.get("items", []):
                idx.register_item(it)

        # 验证索引覆盖全部场景 items
        all_item_count = sum(len(g.get("items", [])) for g in scenes_data.get("scenes", []))
        self.assertGreaterEqual(all_item_count, 122 + 112)

    def test_05_all_2264_mappings_reachable(self):
        """全量 2,264 条可入库目标映射逐条采样可达性核验 (0 不可达)。"""
        valid_mappings = [
            m for m in self.b5_mappings
            if m["source_entity_id"] not in BATCH5_QUARANTINE_ENTITY_IDS
            and m.get("target_item_id") != "quarantine"
        ]
        self.assertEqual(len(valid_mappings), 2264)

        unreachable = []
        for m in valid_mappings:
            mid = m["mapping_id"]
            tag_id = sanitize_id(m["target_tag_id"])
            tag_text = m["target_tag_text"].strip()

            is_reached = False
            # 1. 尝试以 tag_id 直接采样 (通过 sampler._match_scene_tag 穿透匹配)
            try:
                res = self.sampler.sample_scene_result(tag_id, random.Random(42))
                if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                    is_reached = True
            except Exception:
                pass

            # 2. 尝试以 tag_text 采样
            if not is_reached:
                try:
                    res = self.sampler.sample_scene_result(tag_text, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass

            if not is_reached:
                unreachable.append((mid, tag_id, tag_text))

        self.assertEqual(len(unreachable), 0, f"Unreachable mappings found ({len(unreachable)}): {unreachable[:5]}")

    def test_06_ingestion_idempotency_verification(self):
        """隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_data_dir = Path(tmpdir) / "data"
            temp_data_dir.mkdir(parents=True, exist_ok=True)
            temp_snapshot_dir = Path(tmpdir) / "snapshots"
            temp_ledger_path = Path(tmpdir) / "ledger.json"

            # 从入库前快照加载基线
            shutil.copy2(SNAPSHOT_DIR / "scenes.json", temp_data_dir / "scenes.json")

            # 第一遍真实写入
            res1 = execute_batch5_ingestion(
                data_dir=temp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=temp_snapshot_dir,
                ledger_path=temp_ledger_path,
            )
            stats1 = res1["stats"]
            hashes1 = res1["file_hashes"]

            self.assertEqual(stats1["quarantined_sources"], 71)
            self.assertEqual(stats1["quarantined_mappings"], 71)
            self.assertEqual(stats1["candidate_mappings"], 2264)
            self.assertEqual(stats1["added_items"], 112)
            self.assertEqual(stats1["added_tags"], 2218)
            self.assertEqual(stats1["reuse_existing"], 37)
            self.assertEqual(stats1["idempotent_skipped_tags"], 9)

            # 第二遍真实写入 (验证 0 新增与哈希不变)
            res2 = execute_batch5_ingestion(
                data_dir=temp_data_dir,
                dry_run=False,
                backup_snapshot=False,
                snapshot_dir=temp_snapshot_dir,
                ledger_path=temp_ledger_path,
            )
            stats2 = res2["stats"]
            hashes2 = res2["file_hashes"]

            self.assertEqual(stats2["added_items"], 0, "第二遍写入新增条目必须为 0")
            self.assertEqual(stats2["added_tags"], 0, "第二遍写入新增标签必须为 0")
            self.assertEqual(hashes1["scenes.json"], hashes2["scenes.json"], "两遍写入后的 scenes.json SHA256 必须严格一致")

    def test_07_scene_semantic_fidelity_and_domain_rules(self):
        """核验 M3.2/M4 场景环境库原则 1/2/3 语义门禁保真度。"""
        scenes_data = self.sampler._load("scenes")
        all_tags = [
            t for g in scenes_data.get("scenes", [])
            for it in g.get("items", [])
            for t in it.get("anchor_tags", []) + it.get("detail_tags", [])
        ]
        tag_by_id = {t["id"]: t for t in all_tags}
        tag_by_text = {t["text"].strip().lower(): t for t in all_tags}

        # [原则 1] 来源未明说时间一律记录 unspecified，绝不硬编码常见情境
        new_venues = [
            ("nature_river__tag_000", "river"),
            ("nature_lake__tag_000", "lake"),
            ("venue_casino__tag_000", "casino"),
            ("venue_zoo__tag_000", "zoo"),
            ("commercial_retail__supermarket", "supermarket"),
        ]
        for vid, vtext in new_venues:
            self.assertIn(vid, tag_by_id, f"Tag {vid} must exist in scenes")
            facts = tag_by_id[vid].get("facts", {})
            self.assertEqual(facts.get("time_of_day"), "unspecified", f"{vid} time_of_day must be unspecified")

        # [原则 1] library (SRC_SCENE_06095) 核心事实 venue_ids 包含 library 且 time_of_day 为 unspecified，保留 baseline_default_time_of_day
        map_lib = next(m for m in self.b5_mappings if m["source_entity_id"] == "SRC_SCENE_06095")
        map_facts = json.loads(map_lib["semantic_facts_json"])
        self.assertEqual(map_facts.get("time_of_day"), "unspecified")
        self.assertEqual(map_facts.get("venue_ids"), ["library"])
        self.assertEqual(map_facts.get("baseline_default_time_of_day"), "day")

        # [原则 1] 验证 library 审核事实真实落盘入生产库 scenes.json (杜绝停留在台账未入库)
        lib_item = next(it for g in scenes_data.get("scenes", []) for it in g.get("items", []) if it["id"] == "scene_library")
        lib_anchor = next(t for t in lib_item["anchor_tags"] if t["text"].strip().lower() == "library")
        self.assertEqual(lib_anchor["facts"].get("time_of_day"), "unspecified", "生产库 library anchor tag time_of_day 必须为 unspecified")
        self.assertEqual(lib_anchor["facts"].get("venue_ids"), ["library"], "生产库 library anchor tag venue_ids 必须为 ['library']")
        self.assertEqual(lib_anchor["facts"].get("governance_metadata", {}).get("context_affinity"), ["school"])

        # [原则 2] abandoned pier 严格归入 waterfront_pier，保留 condition: abandoned
        self.assertIn("abandoned pier", tag_by_text)
        pier_tag = tag_by_text["abandoned pier"]
        self.assertEqual(pier_tag["id"], "waterfront_pier__abandoned")
        pier_facts = pier_tag.get("facts", {})
        self.assertEqual(pier_facts.get("scene_facts", {}).get("condition"), "abandoned")

        # [原则 2] supermarket 标定为 indoor (commercial_retail)，峡谷热气球标定为 outdoor
        self.assertIn("supermarket", tag_by_text)
        sm_tag = tag_by_text["supermarket"]
        self.assertEqual(sm_tag["facts"].get("space_kind"), "indoor")

        # [原则 3] 叙事长句模板提取氛围光、色调与道具陈设
        prose_item = next(it for g in scenes_data.get("scenes", []) for it in g.get("items", []) if it["id"] == "prose_scene_templates")
        green_room = next(t for t in prose_item["anchor_tags"] if t["id"] == "prose_scene__a_backstage_green_room")
        sf = green_room["facts"].get("scene_facts", {})
        self.assertEqual(sf.get("embedded_lighting"), "soft vanity light")
        self.assertEqual(sf.get("embedded_palette"), "warm off-white and amber palette")
        self.assertTrue(len(sf.get("embedded_props", [])) > 0)
        self.assertIn("slot_attribution", green_room["facts"].get("governance_metadata", {}))

    def test_08_scene_pipeline_end_to_end_resolution_and_assembly(self):
        """场景从采样到装配器全链路端到端消解与组装验证 (0 契约报错)。"""
        # 1. 遍历 11 个新增大类采样并组装
        for r_key, r_info in BATCH5_CATEGORY_ROUTING.items():
            cat_name = r_info["category"]
            res = self.sampler.sample_scene_result(cat_name, random.Random(42))
            self.assertIsNotNone(res, f"Sampling failed for scene category {cat_name}")
            frags = _make_slot_fragments(res, "scene_theme", cat_name, "generator", selector="scene")
            self.assertGreater(len(frags), 0)
            slots = {"scene_theme": frags}
            try:
                assembled = self.assembler.assemble_slots(slots, rng=random.Random(42))
            except RuleConfigurationError as e:
                self.fail(f"RuleConfigurationError raised for category {cat_name}: {e}")
            self.assertGreater(len(assembled.accepted_atoms), 0)
            self.assertTrue(assembled.prompt)

        # 2. prose_scene_templates 采样并组装验证
        p_res = self.sampler.sample_scene_result("prose_scene_templates", random.Random(10))
        p_frags = _make_slot_fragments(p_res, "scene_theme", "prose_scene_templates", "generator", selector="scene")
        p_assembled = self.assembler.assemble_slots({"scene_theme": p_frags}, rng=random.Random(10))
        self.assertTrue(p_assembled.prompt)
        self.assertGreater(len(p_assembled.accepted_atoms), 0)

    def test_09_prose_scene_templates_strict_single_choice(self):
        """核验自然语言整景叙述模板严格单选 1 条 (杜绝拼接冲突长句)。"""
        for seed in range(500):
            res = self.sampler.sample_scene_result("prose_scene_templates", random.Random(seed))
            self.assertIsNotNone(res)
            self.assertEqual(
                len(res.tags), 1,
                f"prose_scene_templates at seed={seed} produced {len(res.tags)} tags: {res.tags}"
            )
            self.assertEqual(len(res.sampled_tags), 1)

    def test_10_standalone_venues_no_conflicting_second_locations(self):
        """核验通用大池独立场所单选相容性 (杜绝随机串接两个独立场所)。"""
        for item_id in ["venue_general", "nature_landscape", "scifi_futuristic", "historical_architecture"]:
            for seed in range(50):
                res = self.sampler.sample_scene_result(item_id, random.Random(seed))
                self.assertIsNotNone(res)
                # 独立场所大池采样必须为单选 1 条场所
                self.assertEqual(
                    len(res.tags), 1,
                    f"Item {item_id} at seed={seed} produced multiple locations: {res.tags}"
                )

    def test_11_runtime_selection_semantics_and_reuse_writeback_consistency(self):
        """核验 river/lake 与 library 等条目在文本直达、标签 ID 与条目选择三种方式下的场所语义一致性，以及 37 项复用事实生产库落盘。"""
        scenes_data = self.sampler._load("scenes")
        item_map = {it["id"]: it for g in scenes_data.get("scenes", []) for it in g.get("items", [])}

        # 1. [P1 门禁] 验证 river / lake 杜绝命中 scene_bathtub，三种选择方式场所语义 100% 对齐
        bathtub = item_map.get("scene_bathtub")
        self.assertIsNotNone(bathtub)
        bathtub_all_tags = [
            (t.get("text") if isinstance(t, dict) else str(t)).strip().lower()
            for t in bathtub.get("anchor_tags", []) + bathtub.get("detail_tags", []) + bathtub.get("tags", [])
        ]
        self.assertNotIn("river", bathtub_all_tags, "scene_bathtub 必须剥离错配子标签 river")
        self.assertNotIn("lake", bathtub_all_tags, "scene_bathtub 必须剥离错配子标签 lake")

        for concept, item_id, tag_id in [
            ("river", "nature_river", "nature_river__tag_000"),
            ("lake", "nature_lake", "nature_lake__tag_000"),
        ]:
            res_text = self.sampler.sample_scene_result(concept, random.Random(42))
            res_tag = self.sampler.sample_scene_result(tag_id, random.Random(42))
            res_item = self.sampler.sample_scene_result(item_id, random.Random(42))

            for mode_name, res in [("文本直达", res_text), ("标签ID直达", res_tag), ("条目选择", res_item)]:
                self.assertIsNotNone(res, f"{concept} 在 {mode_name} 模式下采样失败")
                self.assertEqual(res.item_id, item_id, f"{concept} 在 {mode_name} 模式下必须命中专属条目 {item_id}，绝非 scene_bathtub")
                self.assertNotEqual(res.item_id, "scene_bathtub")
                facts = res.sampled_tags[0].facts
                self.assertEqual(facts.space_kind, "outdoor", f"{concept} 在 {mode_name} 模式下 space_kind 必须为 outdoor")
                self.assertEqual(str(facts.time_of_day), "unspecified", f"{concept} 在 {mode_name} 模式下 time_of_day 必须为 unspecified")
                self.assertEqual(facts.venue_ids, ("natural_water",), f"{concept} 在 {mode_name} 模式下 venue_ids 必须为 ('natural_water',)")

        # 2. [P1 门禁] 验证 library (SRC_SCENE_06095) 文本直达与标签直达运行时事实为 venue_ids=('library',), time_of_day='unspecified'
        res_lib_text = self.sampler.sample_scene_result("library", random.Random(42))
        res_lib_tag = self.sampler.sample_scene_result("scene_library__anchor_000", random.Random(42))
        for mode_name, res in [("文本直达", res_lib_text), ("标签直达", res_lib_tag)]:
            self.assertIsNotNone(res)
            self.assertEqual(res.item_id, "scene_library")
            facts = res.sampled_tags[0].facts
            self.assertEqual(facts.venue_ids, ("library",), f"library 在 {mode_name} 下 venue_ids 必须为 ('library',)，绝非旧值 ('school',)")
            self.assertEqual(str(facts.time_of_day), "unspecified", f"library 在 {mode_name} 下 time_of_day 必须为 unspecified，绝非旧值 'day'")

        # 2.1 [P1 门禁] 针对 scene_library 进行条目选择、多种子、包含细节标签的深度组合验证
        # 严格复现检查 seed=0 与 seed=1 及连续 100 个随机种子下的主标签与细节事实
        for seed in range(100):
            res_item = self.sampler.sample_scene_result("scene_library", random.Random(seed))
            self.assertIsNotNone(res_item)
            self.assertEqual(res_item.item_id, "scene_library")
            self.assertGreater(len(res_item.tags), 0)
            self.assertGreater(len(res_item.sampled_tags), 0)

            # 遍历该种子下抽出的所有标签 (包含主 anchor 与细节 details)
            for st in res_item.sampled_tags:
                txt_low = st.text.strip().lower()
                # 无论主标签还是细节标签，绝不能硬编码白天
                self.assertEqual(
                    str(st.facts.time_of_day), "unspecified",
                    f"seed={seed} 下标签 '{st.text}' time_of_day={st.facts.time_of_day} 错误携带了未经来源明确的白天！"
                )
                if "school" in txt_low:
                    # 明确包含 school library 的标签保留学校语义
                    self.assertIn(
                        "school", st.facts.venue_ids,
                        f"seed={seed} 下学校图书馆标签 '{st.text}' venue_ids 必须包含 'school'！"
                    )
                else:
                    # 普通 library 设施标签 (如 library stacks, reading room, study room 等) 绝不能默认等同学校
                    self.assertEqual(
                        st.facts.venue_ids, ("library",),
                        f"seed={seed} 下普通设施标签 '{st.text}' venue_ids={st.facts.venue_ids} 不应默认等同学校！必须为 ('library',)"
                    )

        # 3. [P1 门禁] 生产库 scenes.json 中全部 37 项 REUSE_EXISTING 映射事实落盘 100% 验证 (0 遗漏)
        source_map = {s["entity_id"]: s for s in self.b5_sources}
        reuse_mappings = [
            m for m in self.b5_mappings
            if source_map.get(m["source_entity_id"], {}).get("decision", m.get("decision")) == "REUSE_EXISTING"
        ]
        self.assertEqual(len(reuse_mappings), 37, "REUSE_EXISTING 映射数必须为 37")
        for m in reuse_mappings:
            iid = m["target_item_id"]
            ttext = m["target_tag_text"].strip().lower()
            raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
            item = item_map.get(iid)
            self.assertIsNotNone(item, f"Item {iid} 在生产库中未找到")
            matching_tags = [
                t for grp in ("anchor_tags", "detail_tags", "tags")
                for t in item.get(grp, [])
                if isinstance(t, dict) and t.get("text", "").strip().lower() == ttext
            ]
            self.assertGreater(len(matching_tags), 0, f"条目 {iid} 中未找到文本 '{ttext}' 对应的复用标签")
            for t in matching_tags:
                cur_facts = t.get("facts", {})
                for k in ("space_kind", "venue_ids", "time_of_day"):
                    if k in raw_facts:
                        self.assertEqual(
                            cur_facts.get(k), raw_facts[k],
                            f"复用映射 {m['mapping_id']} ({m['source_entity_id']}) 字段 {k} 未落盘: 生产库为 {cur_facts.get(k)}, 台账为 {raw_facts[k]}"
                        )

        # 4. [原则 1 门禁] 验证其它基线严重错配标签文本直达均命中专属独立条目
        misplaced_checks = [
            ("zoo", "venue_zoo", "scene_aquarium"),
            ("casino", "venue_casino", "scene_pachinko_parlor"),
            ("swimming pool", "venue_swimming_pool", "scene_bowling_alley"),
            ("warehouse", "industrial_warehouse", "scene_locked_room"),
            ("bus stop", "transit_bus_stop", "scene_highway_bus"),
        ]
        for query_text, expected_item, bad_item in misplaced_checks:
            res = self.sampler.sample_scene_result(query_text, random.Random(42))
            self.assertIsNotNone(res, f"Query '{query_text}' 采样失败")
            self.assertEqual(res.item_id, expected_item, f"Query '{query_text}' 必须命中 {expected_item}，绝不能误中 {bad_item}")
            self.assertNotEqual(res.item_id, bad_item)


if __name__ == "__main__":
    unittest.main()
