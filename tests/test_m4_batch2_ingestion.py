#!/usr/bin/env python3
"""
tests/test_m4_batch2_ingestion.py — M4 Batch 2 自动化验收测试套件

测试覆盖范围：
1. 来源实体与目标映射双向对账核验 (145 来源实体 / 145 条目标映射，10 REUSE, 75 VARIANT, 59 NEW, 1 COMBO)；
2. 目标数据文件 (accessories.json) 100% 满足 JSON Schema 契约；
3. 编目索引 ExactCatalogIndex 0 键冲突；
4. 复合发型组合宏 (hair_ensemble_composite) 槽位展开、容器描述防泄露与互斥规则检验；
5. 代表性发型大类 (光头、爆炸头、编发、脏辫、呆毛、发饰等) 覆盖率与变体多样性；
6. 正交属性色板库 (49款发色、5级发长、20种发质) 完整性；
7. 全量 145 条目标映射逐条采样可达性核验 (0 不可达)；
8. 隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)；
9. 02_hair 发型/发饰/发长词池保真度核验 (与冻结来源 100% 对齐)。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import random
import tempfile
import unittest

import jsonschema

from lib.conflict_resolver import ConflictResolver
from lib.lexer import validate_prompt_syntax
from lib.pool_resolver import (
    BATCH1_POOL_REGISTRY,
    PoolResolutionError,
    is_container_description,
    resolve_dynamic_slots,
    resolve_pool_reference,
)
from lib.sampler import DataSampler, ExactCatalogIndex, SampledTag
from scratch.apply_m4_batch2_ingestion import (
    execute_batch2_ingestion,
    get_sha256,
    load_batch2_ledger,
)

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
SNAPSHOT_DIR = REPO_DIR / "scratch/m4_snapshots/batch_2_pre_ingest"


class TestM4Batch2Ingestion(unittest.TestCase):
    """M4 Batch 2 自动化验收测试套件。"""

    def setUp(self):
        self.b2_sources, self.b2_mappings = load_batch2_ledger()
        self.sampler = DataSampler(data_dir=DATA_DIR)
        self.resolver = ConflictResolver(data_dir=DATA_DIR)

    def test_01_ledger_mapping_and_source_reconciliation(self):
        """核对 Batch 2 来源实体与目标映射双向对账矩阵。"""
        # 145 个来源实体, 145 条目标映射，严格 1:1
        self.assertEqual(len(self.b2_sources), 145)
        self.assertEqual(len(self.b2_mappings), 145)
        unique_eids = set(s["entity_id"] for s in self.b2_sources)
        self.assertEqual(len(unique_eids), 145)

        # 决策构成精确核验
        counts_by_decision = {}
        for s in self.b2_sources:
            d = s["decision"]
            counts_by_decision[d] = counts_by_decision.get(d, 0) + 1

        self.assertEqual(counts_by_decision.get("REUSE_EXISTING"), 10)
        self.assertEqual(counts_by_decision.get("STYLE_VARIANT"), 75)
        self.assertEqual(counts_by_decision.get("NEW_STYLE"), 59)
        self.assertEqual(counts_by_decision.get("ENSEMBLE_COMBO"), 1)
        self.assertEqual(sum(counts_by_decision.values()), 145)

        # 全量映射均指向 accessories.json
        for m in self.b2_mappings:
            self.assertEqual(m["target_catalog_file"], "accessories.json")

    def test_02_production_files_schema_conformance(self):
        """校验 accessories.json 100% 满足其 JSON Schema 契约。"""
        data_file = DATA_DIR / "accessories.json"
        schema_file = SCHEMAS_DIR / "accessories.schema.json"
        self.assertTrue(data_file.exists(), f"Missing data file: {data_file}")
        self.assertTrue(schema_file.exists(), f"Missing schema file: {schema_file}")

        data = json.loads(data_file.read_text(encoding="utf-8"))
        schema = json.loads(schema_file.read_text(encoding="utf-8"))

        resolver = jsonschema.RefResolver.from_schema(schema)
        validator = jsonschema.Draft7Validator(schema, resolver=resolver)
        errors = list(validator.iter_errors(data))
        err_msgs = [f"{e.message} at {list(e.path)}" for e in errors]
        self.assertEqual(len(errors), 0, f"Schema validation failed for accessories.json: {err_msgs}")

    def test_03_exact_catalog_indexing_zero_collisions(self):
        """校验 ExactCatalogIndex 在 accessories.json 上 0 键冲突。"""
        data = self.sampler._load("accessories")
        for container_key in ("hairstyles", "headwear_jewelry"):
            items = data.get(container_key, [])
            idx = ExactCatalogIndex(f"accessories_{container_key}")
            for it in items:
                idx.register_item(it)
            self.assertGreater(len(items), 0)

    def test_04_ensemble_composite_hairstyle_expansion(self):
        """校验复合发型组合宏 (hair_ensemble_composite) 槽位展开、容器描述防泄露与互斥规则。"""
        rng = random.Random(2026)

        for _ in range(50):
            res = self.sampler.sample_hairstyle_result("hair_ensemble_composite", rng)
            self.assertIsNotNone(res)
            tags = res.tags
            self.assertGreaterEqual(len(tags), 2, f"Composite hairstyle output too short: {tags}")

            # 1. 容器描述文本严禁泄露
            self.assertFalse(
                any("complete composite hairstyle" in t.lower() for t in tags),
                f"Container description leaked into tags: {tags}",
            )

            # 2. 严禁未解析花括号或裸通配符标记泄露
            for t in tags:
                self.assertNotIn("__by_source", t)
                self.assertNotIn("{", t)
                self.assertNotIn("}", t)
                validate_prompt_syntax(t)

            # 3. 必须包含发色槽位，且按规范包装为 ({color} hair)
            has_color = any(t.startswith("(") and t.endswith(" hair)") for t in tags)
            self.assertTrue(has_color, f"Missing color wrapper in composite hairstyle tags: {tags}")

            # 4. 发型基础廓形互斥：straight, curly, wavy 间至多抽取 1 个
            silhouettes = [s for s in tags if s in ("straight hair", "curly hair", "wavy hair")]
            self.assertLessEqual(
                len(silhouettes),
                1,
                f"Multiple conflicting base silhouettes in composite output: {silhouettes}",
            )

            # 5. 扎发结构完整互斥：high ponytail, ponytail, twintails 至多抽取 1 个
            tied_styles = [p for p in tags if p in ("high ponytail", "ponytail", "twintails")]
            self.assertLessEqual(
                len(tied_styles),
                1,
                f"Multiple conflicting tied hair styles in composite output: {tied_styles}",
            )

            # 6. 刘海互斥：bangs, blunt bangs 至多抽取 1 个
            bangs = [b for b in tags if b in ("bangs", "blunt bangs")]
            self.assertLessEqual(
                len(bangs),
                1,
                f"Multiple conflicting bangs in composite output: {bangs}",
            )

            # 7. 发长级联约束：短发/极短发绝对禁止出现扎发 (高马尾、单马尾、双马尾)
            is_short = any(sk in tags for sk in ("short hair", "very short hair"))
            if is_short:
                self.assertEqual(
                    len(tied_styles),
                    0,
                    f"Short hair cascade violation! Tied hair styles found with short length: {tags}",
                )

    def test_05_hair_category_diversity_and_variants(self):
        """核验新增代表性发型大类与发饰变体的丰富度与抽样结果。"""
        rng = random.Random(101)

        # 1. 光头/剃光发大类与变体
        bald_res = self.sampler.sample_hairstyle_result("hair_bald", rng)
        self.assertIsNotNone(bald_res)
        self.assertTrue(any("bald" in t.lower() for t in bald_res.tags))

        # 2. 经典圆蓬爆炸头
        afro_res = self.sampler.sample_hairstyle_result("hair_afro", rng)
        self.assertIsNotNone(afro_res)
        self.assertTrue(any("afro" in t.lower() for t in afro_res.tags))

        # 3. 地垄辫
        corn_res = self.sampler.sample_hairstyle_result("hair_cornrows", rng)
        self.assertIsNotNone(corn_res)
        self.assertTrue(any("cornrows" in t.lower() for t in corn_res.tags))

        # 4. 盒状脏辫
        box_res = self.sampler.sample_hairstyle_result("hair_box_braids", rng)
        self.assertIsNotNone(box_res)
        self.assertTrue(any("box braids" in t.lower() for t in box_res.tags))

        # 5. 雷鬼脏辫
        locs_res = self.sampler.sample_hairstyle_result("hair_locs", rng)
        self.assertIsNotNone(locs_res)
        self.assertTrue(any("locs" in t.lower() for t in locs_res.tags))

        # 6. 二次元呆毛
        ahoge_res = self.sampler.sample_hairstyle_result("hair_feature_ahoge", rng)
        self.assertIsNotNone(ahoge_res)
        self.assertTrue(any("ahoge" in t.lower() for t in ahoge_res.tags))

        # 7. 泛用发饰多变体抽样 (13 种造型变体)
        seen_ornaments = set()
        for s in range(30):
            orn_res = self.sampler.sample_jewelry_result("acc_hair_ornament", random.Random(s))
            if orn_res:
                seen_ornaments.update(orn_res.tags)
        self.assertGreaterEqual(
            len(seen_ornaments),
            5,
            f"acc_hair_ornament lacks variant diversity across 30 seeds: {seen_ornaments}",
        )

    def test_06_hair_color_and_length_palettes(self):
        """核验正交发色、发长、发质调色板库条目数量与约束。"""
        data = self.sampler._load("accessories")
        hairstyles = {h["id"]: h for h in data.get("hairstyles", [])}

        # 1. 发色库：包含 49 款发色变体
        self.assertIn("hair_color_palette", hairstyles)
        color_tags = hairstyles["hair_color_palette"]["tags"]
        self.assertEqual(len(color_tags), 49)

        # 2. 发长库：包含 5 级发长标定
        self.assertIn("hair_length_palette", hairstyles)
        length_tags = hairstyles["hair_length_palette"]["tags"]
        self.assertEqual(len(length_tags), 5)
        length_texts = {t["text"] for t in length_tags}
        expected_lengths = {"long hair", "medium hair", "short hair", "very long hair", "very short hair"}
        self.assertEqual(length_texts, expected_lengths)

        # 3. 发质库：包含 20 种微观发质质感
        self.assertIn("hair_texture_palette", hairstyles)
        texture_tags = hairstyles["hair_texture_palette"]["tags"]
        self.assertEqual(len(texture_tags), 20)

    def test_07_all_145_mappings_reachable(self):
        """全量 145 条目标映射逐条采样可达性核验。"""
        seeds_to_test = [0, 1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 42]
        unreachable = []

        for m in self.b2_mappings:
            mid = m["mapping_id"]
            item_id = m["target_item_id"]
            tag_id = m["target_tag_id"]
            tag_text = m["target_tag_text"]
            role = m["target_role"]

            is_reached = False
            is_headwear = item_id.startswith("acc_")

            if is_headwear:
                try:
                    res = self.sampler.sample_jewelry_result(tag_id, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass
                if not is_reached:
                    for s in seeds_to_test:
                        res = self.sampler.sample_jewelry_result(item_id, random.Random(s))
                        if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                            is_reached = True
                            break
            else:
                try:
                    res = self.sampler.sample_hairstyle_result(tag_id, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass
                if not is_reached:
                    for s in seeds_to_test:
                        res = self.sampler.sample_hairstyle_result(item_id, random.Random(s))
                        if res:
                            if role == "ensemble_combo" and item_id == "hair_ensemble_composite":
                                if len(res.tags) >= 2:
                                    is_reached = True
                                    break
                            elif any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                                is_reached = True
                                break

            if not is_reached:
                unreachable.append(f"{mid} ({item_id}/{tag_id}: '{tag_text}')")

        self.assertEqual(len(unreachable), 0, f"Unreachable Batch 2 mappings found: {unreachable}")

    def test_08_ingestion_idempotency_verification(self):
        """在隔离临时副本中验证两次真实写入及哈希一致性，遵守只读审核约束不触碰生产目录及主账本。"""
        import shutil

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            tmp_data_dir = tmp_dir / "data"
            tmp_data_dir.mkdir(parents=True, exist_ok=True)
            tmp_snapshot_dir = tmp_dir / "scratch/m4_snapshots/batch_2_pre_ingest"
            tmp_snapshot_dir.mkdir(parents=True, exist_ok=True)
            tmp_ledger_path = tmp_dir / "scratch/m4_batch2_execution_ledger.json"

            # 1. 复制生产 accessories.json 及入库前快照至临时副本
            shutil.copy2(DATA_DIR / "accessories.json", tmp_data_dir / "accessories.json")
            shutil.copy2(SNAPSHOT_DIR / "accessories.json", tmp_snapshot_dir / "accessories.json")

            hashes_before = get_sha256(tmp_data_dir / "accessories.json")
            snapshot_hash_before = get_sha256(tmp_snapshot_dir / "accessories.json")

            # 2. 第一次在临时副本中真实执行入库 (dry_run=False)
            res1 = execute_batch2_ingestion(
                tmp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=tmp_snapshot_dir,
                ledger_path=tmp_ledger_path,
            )
            st1 = res1["stats"]
            self.assertEqual(st1["added_items"], 0, f"Expected 0 added items on run 1, got {st1['added_items']}")
            self.assertEqual(st1["added_tags"], 0, f"Expected 0 added tags on run 1, got {st1['added_tags']}")
            hash_after_run1 = get_sha256(tmp_data_dir / "accessories.json")
            self.assertEqual(hashes_before, hash_after_run1, "accessories.json changed during first idempotent write")

            # 3. 第二次在临时副本中真实执行入库 (dry_run=False)
            res2 = execute_batch2_ingestion(
                tmp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=tmp_snapshot_dir,
                ledger_path=tmp_ledger_path,
            )
            st2 = res2["stats"]
            self.assertEqual(st2["added_items"], 0, f"Expected 0 added items on run 2, got {st2['added_items']}")
            self.assertEqual(st2["added_tags"], 0, f"Expected 0 added tags on run 2, got {st2['added_tags']}")
            hash_after_run2 = get_sha256(tmp_data_dir / "accessories.json")
            self.assertEqual(hash_after_run1, hash_after_run2, "accessories.json changed during second idempotent write")

            # 4. 快照写保护断言
            snapshot_hash_after = get_sha256(tmp_snapshot_dir / "accessories.json")
            self.assertEqual(snapshot_hash_before, snapshot_hash_after, "Snapshot was illegally overwritten")

            # 5. 账本记录断言
            self.assertTrue(tmp_ledger_path.exists())
            ledger_data = json.loads(tmp_ledger_path.read_text(encoding="utf-8"))
            self.assertIn("runs", ledger_data)
            self.assertEqual(len(ledger_data["runs"]), 2)

    def test_09_pool_registry_hair_fidelity(self):
        """核验 02_hair 词池保真度：发长(5项)、发型款式(9项)、发饰(15项)与冻结来源 100% 对齐。"""
        wildcards_base = REPO_DIR.parent / "ai-image-wildcards" / "wildcards" / "by_source" / "skyyysi" / "02_hair"

        # 1. 发长词池
        lengths_file = wildcards_base / "hair_lengths.txt"
        self.assertTrue(lengths_file.exists())
        src_lengths = [l.strip().lower() for l in lengths_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
        reg_lengths = BATCH1_POOL_REGISTRY["02_hair/hair_lengths"]
        self.assertEqual(len(src_lengths), 5)
        self.assertEqual(len(reg_lengths), 5)
        self.assertEqual(reg_lengths, src_lengths)
        self.assertEqual(set(reg_lengths), set(src_lengths))

        # 2. 发型款式词池
        styles_file = wildcards_base / "hair_styles.txt"
        self.assertTrue(styles_file.exists())
        src_styles = [l.strip().lower() for l in styles_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
        reg_styles = BATCH1_POOL_REGISTRY["02_hair/hair_styles"]
        self.assertEqual(len(src_styles), 9)
        self.assertEqual(len(reg_styles), 9)
        self.assertEqual(reg_styles, src_styles)
        self.assertEqual(set(reg_styles), set(src_styles))

        # 3. 发饰词池
        orn_file = wildcards_base / "hair_ornaments.txt"
        self.assertTrue(orn_file.exists())
        src_orns = [l.strip().lower() for l in orn_file.read_text(encoding="utf-8").splitlines() if l.strip() and not l.strip().startswith("#")]
        reg_orns = BATCH1_POOL_REGISTRY["02_hair/hair_ornaments"]
        self.assertEqual(len(src_orns), 15)
        self.assertEqual(len(reg_orns), 15)
        self.assertEqual(reg_orns, src_orns)
        self.assertEqual(set(reg_orns), set(src_orns))

    def test_10_fixed_seed_counterexamples_and_palette_single_selection(self):
        """针对评审指出的固定种子反例与属性调色板单选约束进行专项回归验证。"""
        # 1. [P1 反例复核] hair_length_palette 在 seed=5 下必须且仅产出 1 个发长值，杜绝 'short hair, long hair'
        res_len_s5 = self.sampler.sample_hairstyle_result("hair_length_palette", random.Random(5))
        self.assertIsNotNone(res_len_s5)
        self.assertEqual(len(res_len_s5.tags), 1, f"hair_length_palette seed=5 must be single selection: {res_len_s5.tags}")
        self.assertIn(res_len_s5.tags[0], {"long hair", "medium hair", "short hair", "very long hair", "very short hair"})

        # 2. [P1 反例复核] hair_color_palette 在 seed=0 下必须且仅产出 1 个发色值，杜绝 'ultraviolet purple hair, medium ash brown hair'
        res_col_s0 = self.sampler.sample_hairstyle_result("hair_color_palette", random.Random(0))
        self.assertIsNotNone(res_col_s0)
        self.assertEqual(len(res_col_s0.tags), 1, f"hair_color_palette seed=0 must be single selection: {res_col_s0.tags}")
        self.assertTrue(res_col_s0.tags[0].endswith(" hair"))

        # 3. 跨 50 个种子全面核验三大属性调色板严格单选 (max_selections=1)
        for s in range(50):
            r_seed = random.Random(s)
            l_res = self.sampler.sample_hairstyle_result("hair_length_palette", r_seed)
            self.assertEqual(len(l_res.tags), 1, f"hair_length_palette seed={s} produced multiple tags: {l_res.tags}")

            c_res = self.sampler.sample_hairstyle_result("hair_color_palette", r_seed)
            self.assertEqual(len(c_res.tags), 1, f"hair_color_palette seed={s} produced multiple tags: {c_res.tags}")

            t_res = self.sampler.sample_hairstyle_result("hair_texture_palette", r_seed)
            self.assertEqual(len(t_res.tags), 1, f"hair_texture_palette seed={s} produced multiple tags: {t_res.tags}")

        # 4. [P1 反例复核] hair_ensemble_composite 在 seed=3 下绝对禁止出现多扎发冲突或短发扎发
        res_comp_s3 = self.sampler.sample_hairstyle_result("hair_ensemble_composite", random.Random(3))
        self.assertIsNotNone(res_comp_s3)
        tags_s3 = res_comp_s3.tags

        # 绝对杜绝 high ponytail 与 twintails 共存
        has_hp = "high ponytail" in tags_s3
        has_tt = "twintails" in tags_s3
        self.assertFalse(has_hp and has_tt, f"seed=3 leaked contradictory high ponytail + twintails: {tags_s3}")

        # 绝对杜绝 short/very short 发长搭配扎发
        is_short_s3 = any(sk in tags_s3 for sk in ("short hair", "very short hair"))
        has_tied_s3 = any(t in tags_s3 for t in ("high ponytail", "ponytail", "twintails"))
        self.assertFalse(is_short_s3 and has_tied_s3, f"seed=3 leaked short length with tied hair: {tags_s3}")

        # 5. 跨 200 个种子断言复合发型 100% 满足发长级联与扎发互斥
        TIED_SET = {"high ponytail", "ponytail", "twintails"}
        SIL_SET = {"straight hair", "curly hair", "wavy hair"}
        BANGS_SET = {"bangs", "blunt bangs"}

        for s in range(200):
            res_comp = self.sampler.sample_hairstyle_result("hair_ensemble_composite", random.Random(s))
            tags = res_comp.tags

            # 扎发互斥：至多 1 个
            tied_cnt = sum(1 for t in tags if t in TIED_SET)
            self.assertLessEqual(tied_cnt, 1, f"Seed {s} has multiple tied hair styles: {tags}")

            # 基础廓形互斥：至多 1 个
            sil_cnt = sum(1 for t in tags if t in SIL_SET)
            self.assertLessEqual(sil_cnt, 1, f"Seed {s} has multiple silhouette styles: {tags}")

            # 刘海互斥：至多 1 个
            bangs_cnt = sum(1 for t in tags if t in BANGS_SET)
            self.assertLessEqual(bangs_cnt, 1, f"Seed {s} has multiple bangs: {tags}")

            # 发长级联：短发严禁扎发
            is_short = any(sk in tags for sk in ("short hair", "very short hair"))
            if is_short:
                self.assertEqual(tied_cnt, 0, f"Seed {s} short hair cascade violation: {tags}")


if __name__ == "__main__":
    unittest.main()
