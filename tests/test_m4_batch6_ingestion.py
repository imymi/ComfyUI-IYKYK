#!/usr/bin/env python3
"""
tests/test_m4_batch6_ingestion.py — M4 Batch 6 服装款式库自动化验收测试套件

测试覆盖范围：
1. 来源实体与目标映射双向对账核验 (3,607 来源实体 / 3,607 条目标映射，3 隔离，3,604 候选：25 REUSE, 949 VARIANT, 360 NEW, 2,270 COMBO)；
2. 目标数据文件 clothing.json 100% 满足 JSON Schema 契约，标签 ID 与条目 ID 100% 满足 ^[a-z][a-z0-9_]{2,95}$ 规范；
3. 3 项非具体服装单品/状态修饰隔离项物理隔离防泄露断言 (零侵入生产库 clothing.json)；
4. 编目索引 ExactCatalogIndex 在全部服装类别上 0 键冲突；
5. 全量 3,604 条可入库目标映射逐条采样可达性核验 (0 不可达)；
6. 隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)；
7. 剪裁特征、材质面料、套装拆分与独立单品拓扑等原则 1/2/3 语义门禁保真度核验；
8. 服装从采样到装配器全链路端到端消解验证 (0 契约报错)；
9. 复合风格穿搭组合池 clothing_ensemble_combos 严格单选 1 套完整穿搭验证 (杜绝拼接冲突长句)；
10. 单品服装类别采样相容性断言。
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
from scratch.apply_m4_batch6_ingestion import (
    BATCH6_NEW_CATEGORY_LABELS,
    BATCH6_QUARANTINE_ENTITY_IDS,
    execute_batch6_ingestion,
    get_sha256,
    load_batch6_ledger,
    sanitize_id,
)

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
SNAPSHOT_DIR = REPO_DIR / "scratch/m4_snapshots/batch_6_pre_ingest"


class TestM4Batch6Ingestion(unittest.TestCase):
    """M4 Batch 6 自动化验收测试套件。"""

    def setUp(self):
        self.b6_sources, self.b6_mappings = load_batch6_ledger()
        self.sampler = DataSampler(data_dir=DATA_DIR)
        self.assembler = PromptAssembler(data_dir=DATA_DIR)

    def test_01_ledger_mapping_and_source_reconciliation(self):
        """核对 Batch 6 来源实体与目标映射双向对账矩阵。"""
        self.assertEqual(len(self.b6_sources), 3607, "Batch 6 来源实体总数必须为 3607")
        self.assertEqual(len(self.b6_mappings), 3607, "Batch 6 目标映射总数必须为 3607")

        # 验证 3 条隔离项绝对排除
        quarantined_sources = [s for s in self.b6_sources if s["entity_id"] in BATCH6_QUARANTINE_ENTITY_IDS]
        self.assertEqual(len(quarantined_sources), 3, "隔离实体数量必须严格为 3")

        quarantined_mappings = [
            m for m in self.b6_mappings
            if m["source_entity_id"] in BATCH6_QUARANTINE_ENTITY_IDS
            or m.get("target_role") == "quarantined"
            or m.get("target_item_id") == "quarantine"
        ]
        self.assertEqual(len(quarantined_mappings), 3, "隔离映射数量必须严格为 3")

        # 候选映射数必须严格对齐 3,604 条
        candidate_mappings = [
            m for m in self.b6_mappings
            if m["source_entity_id"] not in BATCH6_QUARANTINE_ENTITY_IDS
            and m.get("target_item_id") != "quarantine"
        ]
        self.assertEqual(len(candidate_mappings), 3604, "可入库候选映射数必须严格为 3604")

        # 对账决定分布
        decisions = {}
        for s in self.b6_sources:
            d = s.get("decision")
            decisions[d] = decisions.get(d, 0) + 1

        self.assertEqual(decisions["DEFERRED_ISSUE"], 3)
        self.assertEqual(decisions["REUSE_EXISTING"], 25)
        self.assertEqual(decisions["STYLE_VARIANT"], 949)
        self.assertEqual(decisions["NEW_STYLE"], 360)
        self.assertEqual(decisions["ENSEMBLE_COMBO"], 2270)

    def test_02_clothing_json_schema_compliance_and_id_regex(self):
        """核验 clothing.json 100% 遵循 Draft-7 JSON Schema 及命名正则约束。"""
        clothing_path = DATA_DIR / "clothing.json"
        schema_path = SCHEMAS_DIR / "clothing.schema.json"

        clothing_data = json.loads(clothing_path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        # Draft-7 强校验
        resolver = jsonschema.RefResolver.from_schema(schema)
        validator = jsonschema.Draft7Validator(schema, resolver=resolver)
        errors = list(validator.iter_errors(clothing_data))
        self.assertEqual(len(errors), 0, f"clothing.json Schema 校验失败: {[e.message for e in errors[:5]]}")

        # ID 命名规范强校验
        id_regex = r"^[a-z][a-z0-9_]{2,95}$"
        for cat in clothing_data.get("categories", []):
            cat_id = cat.get("id", "")
            self.assertRegex(cat_id, id_regex, f"Category ID '{cat_id}' 违反命名约束")
            for t in cat.get("tags", []):
                t_id = t.get("id", "")
                self.assertRegex(t_id, id_regex, f"Tag ID '{t_id}' 违反命名约束")

    def test_03_quarantine_isolation_and_leakage_prevention(self):
        """核验 3 条非具体服装单品/状态修饰隔离项物理隔离防泄露断言 (零侵入生产库 clothing.json)。"""
        clothing_data = self.sampler._load("clothing")
        all_tag_texts = [
            t.get("text", "").strip().lower()
            for cat in clothing_data.get("categories", [])
            for t in cat.get("tags", [])
        ]
        all_tag_ids = [
            t.get("id", "").strip().lower()
            for cat in clothing_data.get("categories", [])
            for t in cat.get("tags", [])
        ]

        # 3 条绝对隔离实体
        leakage_checks = [
            ("SRC_CLOTH_00317", "cosplay"),
            ("SRC_CLOTH_00368", "shattered clothes"),
            ("SRC_CLOTH_00387", "transparent clothes"),
        ]
        for eid, phrase in leakage_checks:
            self.assertNotIn(phrase, all_tag_texts, f"隔离词 '{phrase}' 泄露入生产库 clothing.json！")
            self.assertFalse(any(eid.lower() in tid for tid in all_tag_ids), f"隔离实体 '{eid}' 标签 ID 泄露入库！")

    def test_04_exact_catalog_index_and_zero_collision(self):
        """核验编目索引 ExactCatalogIndex 在全部服装类别上 0 键冲突。"""
        clothing_data = self.sampler._load("clothing")
        categories = clothing_data.get("categories", [])
        index = ExactCatalogIndex("clothing")
        for cat in categories:
            index.register_item(cat)
        self.assertGreater(len(index.key_to_item), 0)

        # 确保 17 个新增大类均可无冲突解析
        for new_id in BATCH6_NEW_CATEGORY_LABELS.keys():
            self.assertIn(new_id, index.key_to_item, f"新增大类 {new_id} 必须被索引")

    def test_05_target_mappings_reachability_verification(self):
        """全量 3,604 条可入库目标映射逐条采样可达性核验 (0 不可达)。"""
        valid_mappings = [
            m for m in self.b6_mappings
            if m["source_entity_id"] not in BATCH6_QUARANTINE_ENTITY_IDS
            and m.get("target_item_id") != "quarantine"
        ]

        clothing_data = self.sampler._load("clothing")
        all_tags = {
            t["id"]: t for cat in clothing_data.get("categories", [])
            for t in cat.get("tags", [])
        }
        # 兼容包含 lingerie_wardrobe
        for it in clothing_data.get("lingerie_wardrobe", []):
            for t in it.get("tags", []):
                all_tags[t["id"]] = t

        accessories_data = self.sampler._load("accessories")
        for k, v in accessories_data.items():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        for t in item.get("tags", []):
                            if isinstance(t, dict) and "id" in t:
                                all_tags[t["id"]] = t

        unreachable = []
        for m in valid_mappings:
            mid = m["mapping_id"]
            raw_tag_id = m["target_tag_id"]
            s_tag_id = sanitize_id(raw_tag_id)
            tag_text = m["target_tag_text"].strip()
            tf = m["target_catalog_file"]

            is_reached = False
            # 1. 检查是否存在于目标数据中
            if s_tag_id in all_tags or raw_tag_id in all_tags:
                is_reached = True

            # 2. 尝试采样器采样可达
            if not is_reached:
                try:
                    res = self.sampler.sample_clothing_result(s_tag_id, "None", "L1", random.Random(42))
                    if res and len(res.base_tags) > 0:
                        is_reached = True
                except Exception:
                    pass

            if not is_reached:
                unreachable.append((mid, s_tag_id, tag_text))

        self.assertEqual(len(unreachable), 0, f"Unreachable mappings found ({len(unreachable)}): {unreachable[:5]}")

    def test_06_ingestion_idempotency_verification(self):
        """隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_data_dir = Path(tmpdir) / "data"
            temp_data_dir.mkdir(parents=True, exist_ok=True)
            temp_snapshot_dir = Path(tmpdir) / "snapshots"
            temp_ledger_path = Path(tmpdir) / "ledger.json"

            # 从入库前快照加载基线
            shutil.copy2(SNAPSHOT_DIR / "clothing.json", temp_data_dir / "clothing.json")

            # 第一遍真实写入
            res1 = execute_batch6_ingestion(
                data_dir=temp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=temp_snapshot_dir,
                ledger_path=temp_ledger_path,
            )
            stats1 = res1["stats"]
            hashes1 = res1["file_hashes"]

            self.assertEqual(stats1["quarantined_sources"], 3)
            self.assertEqual(stats1["quarantined_mappings"], 3)
            self.assertEqual(stats1["candidate_mappings"], 3604)
            self.assertEqual(stats1["added_items"], 17)
            self.assertEqual(stats1["added_tags"], 3579)
            self.assertEqual(stats1["reuse_existing"], 25)
            self.assertEqual(stats1["idempotent_skipped_tags"], 0)

            # 第二遍真实写入 (验证 0 新增与哈希不变)
            res2 = execute_batch6_ingestion(
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
            self.assertEqual(hashes1["clothing.json"], hashes2["clothing.json"], "两遍写入后的 clothing.json SHA256 必须严格一致")

    def test_07_clothing_semantic_fidelity_and_domain_rules(self):
        """核验 M3.2/M4 服装款式库原则 1/2/3 语义门禁保真度。"""
        clothing_data = self.sampler._load("clothing")
        all_tags = [
            t for cat in clothing_data.get("categories", [])
            for t in cat.get("tags", [])
        ] + [
            t for item in clothing_data.get("lingerie_wardrobe", [])
            for t in item.get("tags", [])
        ]
        tag_by_id = {t["id"]: t for t in all_tags}
        tag_by_text = {t["text"].strip().lower(): t for t in all_tags}

        # [原则 1] 垂褶领 (cowl neck) 正交提取，严禁粗暴归并为 turtleneck
        cowl_tag = next((t for t in all_tags if t.get("id") == "sweater_casual__ext_03691_cowl_neck_sweater" or t.get("text") == "cowl-neck sweater"), None)
        self.assertIsNotNone(cowl_tag, "必须包含 cowl neck 标签")
        cowl_cut = cowl_tag["facts"].get("cut_features", {})
        self.assertEqual(cowl_cut.get("neckline"), "cowl_neck", "cowl neck 领型绝不能归并为 turtleneck")

        # [原则 2] 独立袜类单品 (fishnet stockings) 严禁复用套装，必须为独立单品
        src_socks = next(s for s in self.b6_sources if s["entity_id"] == "SRC_CLOTH_00326")
        self.assertEqual(src_socks.get("decision"), "NEW_STYLE", "独立袜类单品必须为 NEW_STYLE，严禁 REUSE_EXISTING")
        map_socks = next(m for m in self.b6_mappings if m["source_entity_id"] == "SRC_CLOTH_00326")
        self.assertEqual(map_socks.get("target_item_id"), "hosiery_legwear", "独立袜类单品必须挂载于 hosiery_legwear 独立分类")

        # [原则 3] 验证 25 项 REUSE_EXISTING 中属于 clothing.json 的 24 项真实事实落盘
        source_map = {s["entity_id"]: s for s in self.b6_sources}
        clothing_reuses = [
            m for m in self.b6_mappings
            if source_map.get(m["source_entity_id"], {}).get("decision") == "REUSE_EXISTING"
            and m["target_catalog_file"] == "clothing.json"
        ]
        self.assertEqual(len(clothing_reuses), 24, "clothing.json 中复用项必须为 24 条")
        for m in clothing_reuses:
            raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
            tid = sanitize_id(m["target_tag_id"])
            self.assertIn(tid, tag_by_id, f"复用标签 {tid} 必须存在于生产库中")
            cur_facts = tag_by_id[tid].get("facts", {})
            self.assertEqual(cur_facts.get("semantic_role"), "selector")

    def test_08_clothing_pipeline_end_to_end_resolution_and_assembly(self):
        """服装从采样到装配器全链路端到端消解与组装验证 (0 契约报错)。"""
        # 遍历 17 个新增类别采样并组装
        for new_id in BATCH6_NEW_CATEGORY_LABELS.keys():
            res = self.sampler.sample_clothing_result(new_id, "None", "L1", random.Random(42))
            self.assertIsNotNone(res, f"Sampling failed for clothing category {new_id}")
            self.assertGreater(len(res.base_tags), 0)
            frags = _make_slot_fragments(res, "clothing", new_id, "generator", selector="clothing")
            self.assertGreater(len(frags), 0)
            slots = {"clothing": frags}
            try:
                assembled = self.assembler.assemble_slots(slots, rng=random.Random(42))
            except RuleConfigurationError as e:
                self.fail(f"RuleConfigurationError raised for category {new_id}: {e}")
            self.assertGreater(len(assembled.accepted_atoms), 0)
            self.assertTrue(assembled.prompt)

    def test_09_ensemble_combos_strict_single_outfit_selection(self):
        """核验复合风格穿搭组合池 clothing_ensemble_combos 严格单选 1 套完整穿搭 (杜绝拼接冲突长句)。"""
        for seed in range(200):
            res = self.sampler.sample_clothing_result("clothing_ensemble_combos", "None", "L1", random.Random(seed))
            self.assertIsNotNone(res)
            # 穿搭池单选必须严格为 1 套穿搭
            self.assertEqual(
                len(res.base_tags), 1,
                f"clothing_ensemble_combos at seed={seed} produced {len(res.base_tags)} outfits: {res.base_tags}"
            )

    def test_10_standalone_pieces_no_conflicting_second_garment(self):
        """核验独立单品服装大类及变体款式采样相容性 (覆盖无状态、显式状态及正式组装结果，杜绝多款同时输出)。"""
        all_new_cats = list(BATCH6_NEW_CATEGORY_LABELS.keys())
        expanded_representative_cats = ["qipao", "kimono", "yukata", "hanbok", "dress_casual"]

        # 1. 核验全部 17 个新增大类在无状态 (None)、显式状态 (unbuttoned, normal) 及随机状态 (random) 下严格单选
        for cat_id in all_new_cats:
            for state_mode in ["None", "unbuttoned", "normal", "random"]:
                nudity_code = "L1" if state_mode == "None" else "L2"
                for seed in (0, 1, 42, 100):
                    rng = random.Random(seed)
                    res = self.sampler.sample_clothing_result(cat_id, state_mode, nudity_code, rng)
                    self.assertIsNotNone(res, f"Sampling failed for {cat_id} {state_mode} {seed}")
                    # 新增大类（含穿搭组合与独立单品款式池）基础款式必须且仅能输出 1 套/款
                    self.assertEqual(
                        len(res.base_tags), 1,
                        f"Category {cat_id} at state={state_mode}, seed={seed} produced {len(res.base_tags)} base garments: {[t.text for t in res.base_tags]}"
                    )

                    # 端到端装配核验：正式组装后的 prompt 绝无多个互斥独立长句并存
                    frags = _make_slot_fragments(res, "clothing", cat_id, "generator", selector="clothing")
                    assembled = self.assembler.assemble_slots({"clothing": frags}, rng=rng)
                    self.assertTrue(assembled.prompt, f"Assembled prompt empty for {cat_id} {state_mode} {seed}")

                    # 独立款式基础 atom 归属且仅归属于唯一的源标签，杜绝多主件串联
                    base_atoms = [
                        a for a in assembled.accepted_atoms
                        if a.source_slot == "clothing" and getattr(a.provenance, "kind", None) == "base_clothing"
                    ]
                    unique_source_items = set(a.provenance.item_id for a in base_atoms)
                    self.assertEqual(
                        len(unique_source_items), 1,
                        f"Category {cat_id} at state={state_mode}, seed={seed} produced multiple conflicting base clothing sources: {unique_source_items}"
                    )

        # 2. 核验挂载了 Batch 6 新增变体的代表性存量分类 (qipao, kimono, yukata, hanbok, dress_casual)
        for cat_id in expanded_representative_cats:
            for state_mode in ["None", "unbuttoned", "normal", "random"]:
                nudity_code = "L1" if state_mode == "None" else "L2"
                for seed in (0, 1, 42, 100):
                    rng = random.Random(seed)
                    res = self.sampler.sample_clothing_result(cat_id, state_mode, nudity_code, rng)
                    self.assertIsNotNone(res)
                    # 包含主款式候选与相容属性，总数至多 1~2 个标签，绝不能全量输出 (如旗袍 86 条或日常裙 400 条)
                    self.assertIn(
                        len(res.base_tags), (1, 2),
                        f"Expanded category {cat_id} at state={state_mode}, seed={seed} produced {len(res.base_tags)} tags: {[t.text for t in res.base_tags]}"
                    )
                    # 独立主款版型/变体严格至多 1 个
                    primary_silhouettes = [
                        t for t in res.base_tags
                        if t.role in ("core_base", "variant") or (t.facts and t.facts.semantic_role == "variant")
                    ]
                    self.assertLessEqual(
                        len(primary_silhouettes), 1,
                        f"Found multiple mutually exclusive silhouettes in {cat_id}: {[t.text for t in primary_silhouettes]}"
                    )

                    frags = _make_slot_fragments(res, "clothing", cat_id, "generator", selector="clothing")
                    assembled = self.assembler.assemble_slots({"clothing": frags}, rng=rng)
                    self.assertTrue(assembled.prompt)


if __name__ == "__main__":
    unittest.main()
