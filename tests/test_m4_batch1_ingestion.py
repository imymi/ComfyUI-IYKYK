"""
tests/test_m4_batch1_ingestion.py — M4 Batch 1 生产数据入库与运行时契约自动化验收测试

验收范围：
1. 四个目标数据文件 (expressions.json, lighting.json, accessories.json, clothing.json) 的完整性与准确性；
2. 126 来源实体 / 127 目标映射双向台账对账闭环；
3. 全量目标文件 jsonschema-draft7 校验 0 错误；
4. ExactCatalogIndex 零碰撞索引自洽性；
5. 双必选构件组合 (combo_necklace_choker) 共同展开与独立单品隔离；
6. 组合描述容器 ('legwear with optional footwear', 'necklace and choker combo') 严格过滤拦截；
7. 加权空分支 (2::|) 统计学分布置信区间验证；
8. 全量产物零 '__by_source' 裸标记与零 '{...}' 通配符泄露；
9. 增量去重与双遍执行幂等性核验 (基于全量数据 SHA256 哈希比对与快照保护)；
10. 全量 127 条映射逐条采样可达性 (覆盖直接查询与蒙特卡洛种子多样性)；
11. 词池保真度 (保留全部已审核项与动态颜色规则，Fail-Closed 异常契约)。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import random
import unittest

import jsonschema

from lib.conflict_resolver import ConflictResolver
from lib.lexer import validate_prompt_syntax
from lib.models import SemanticFacts
from lib.pool_resolver import (
    BATCH1_POOL_REGISTRY,
    PoolResolutionError,
    resolve_pool_reference,
)
from lib.sampler import DataSampler, ExactCatalogIndex
from scratch.apply_m4_batch1_ingestion import (
    execute_batch1_ingestion,
    get_sha256,
    load_batch1_ledger,
)

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
SNAPSHOT_DIR = REPO_DIR / "scratch/m4_snapshots/batch_1_pre_ingest"


class TestM4Batch1Ingestion(unittest.TestCase):
    """M4 Batch 1 自动化验收测试套件。"""

    def setUp(self):
        self.b1_sources, self.b1_mappings = load_batch1_ledger()
        self.sampler = DataSampler(data_dir=DATA_DIR)
        self.resolver = ConflictResolver(data_dir=DATA_DIR)

    def test_01_ledger_mapping_and_source_reconciliation(self):
        """核对 Batch 1 来源实体与目标映射双向对账矩阵。"""
        # 126 个来源实体, 127 条目标映射
        self.assertEqual(len(self.b1_sources), 127)
        unique_eids = set(s["entity_id"] for s in self.b1_sources)
        self.assertEqual(len(unique_eids), 126)

        # 唯一 1 拆 2 来源 SRC_ACC_06897
        combo_1to2_mappings = [m for m in self.b1_mappings if m["source_entity_id"] == "SRC_ACC_06897"]
        self.assertEqual(len(combo_1to2_mappings), 2)
        mids = {m["mapping_id"] for m in combo_1to2_mappings}
        self.assertEqual(mids, {"MAP_00084", "MAP_00085"})

        # 四个目标文件映射精确分布
        counts_by_file = {}
        for m in self.b1_mappings:
            tf = m["target_catalog_file"]
            counts_by_file[tf] = counts_by_file.get(tf, 0) + 1

        self.assertEqual(counts_by_file.get("expressions.json"), 35)
        self.assertEqual(counts_by_file.get("lighting.json"), 46)
        self.assertEqual(counts_by_file.get("accessories.json"), 30)
        self.assertEqual(counts_by_file.get("clothing.json"), 16)
        self.assertEqual(sum(counts_by_file.values()), 127)

    def test_02_production_files_schema_conformance(self):
        """校验 4 个数据文件 100% 满足其 JSON Schema 契约。"""
        for fname in ("expressions.json", "lighting.json", "accessories.json", "clothing.json"):
            data_file = DATA_DIR / fname
            schema_file = SCHEMAS_DIR / fname.replace(".json", ".schema.json")
            self.assertTrue(data_file.exists(), f"Missing data file: {data_file}")
            self.assertTrue(schema_file.exists(), f"Missing schema file: {schema_file}")

            data = json.loads(data_file.read_text(encoding="utf-8"))
            schema = json.loads(schema_file.read_text(encoding="utf-8"))

            resolver = jsonschema.RefResolver.from_schema(schema)
            validator = jsonschema.Draft7Validator(schema, resolver=resolver)
            errors = list(validator.iter_errors(data))
            err_msgs = [f"{e.message} at {list(e.path)}" for e in errors]
            self.assertEqual(len(errors), 0, f"Schema validation failed for {fname}: {err_msgs}")

    def test_03_exact_catalog_indexing_zero_collisions(self):
        """校验 ExactCatalogIndex 在四个数据文件上 0 键冲突。"""
        checks = [
            ("expressions", "expressions", "emotions"),
            ("lighting", "lighting", "professional_lighting"),
            ("accessories", "accessories", "headwear_jewelry"),
            ("clothing", "clothing", "categories"),
        ]
        for cat_name, file_key, list_key in checks:
            data = self.sampler._load(file_key)
            items = data.get(list_key, [])
            idx = ExactCatalogIndex(cat_name)
            for it in items:
                idx.register_item(it)
            self.assertGreater(len(items), 0)

    def test_04_combo_necklace_choker_mandatory_components_cooccurrence(self):
        """校验双必选构件组合 (combo_necklace_choker) 同时共存与独立单品隔离。"""
        rng = random.Random(1001)

        # 1. 显式选中组合项时，项链与项圈必须同时被采样输出
        for _ in range(10):
            res_combo = self.sampler.sample_jewelry_result("combo_necklace_choker", rng)
            self.assertIsNotNone(res_combo)
            tags = res_combo.tags
            self.assertGreaterEqual(len(tags), 2)
            has_necklace = any("necklace" in t.lower() for t in tags)
            has_choker = any(any(k in t.lower() for k in ("choker", "collar", "ribbon", "bell")) for t in tags)
            self.assertTrue(has_necklace, f"Combo output missing necklace component: {tags}")
            self.assertTrue(has_choker, f"Combo output missing choker component: {tags}")
            # 组合描述容器严禁泄露
            self.assertFalse(any("combo" in t for t in tags), f"Combo container description leaked: {tags}")

        # 2. 独立选中 leather_choker 时，绝不触发项链
        for _ in range(20):
            res_choker = self.sampler.sample_jewelry_result("leather_choker", rng)
            self.assertIsNotNone(res_choker)
            self.assertFalse(any("necklace" in t for t in res_choker.tags), f"Choker leaked necklace: {res_choker.tags}")

        # 3. 独立选中 acc_necklace_general 时，绝不触发项圈
        for _ in range(20):
            res_neck = self.sampler.sample_jewelry_result("acc_necklace_general", rng)
            self.assertIsNotNone(res_neck)
            self.assertFalse(any("choker" in t for t in res_neck.tags), f"Necklace leaked choker: {res_neck.tags}")

    def test_05_clothing_legwear_combo_container_description_suppressed(self):
        """校验 clothing_legwear_combo 的组合描述文本严禁泄露至最终提示词。"""
        rng = random.Random(2002)
        for _ in range(30):
            c_res = self.sampler.sample_clothing_result("clothing_legwear_combo", "none", "l1", rng)
            tags = [t.text for t in c_res.base_tags]
            # 严禁 'legwear with optional footwear' 泄露
            self.assertFalse(
                any("legwear with optional footwear" in t.lower() for t in tags),
                f"Container description leaked to prompt: {tags}",
            )
            # 必须产出有效腿饰构件
            self.assertGreaterEqual(len(tags), 1)

    def test_06_denim_shorts_weighted_empty_branch_distribution(self):
        """验证短裤下装次级腿饰 {2::|} 加权空分支统计学分布 (期望 66.7%)。"""
        rng = random.Random(3003)
        empty_legwear_count = 0
        N = 500
        for _ in range(N):
            c_res = self.sampler.sample_clothing_result("denim_shorts", "none", "l1", rng)
            tags = [t.text for t in c_res.base_tags]
            has_legwear = any(any(k in t for k in ("fishnet", "pantyhose", "thighhigh")) for t in tags)
            if not has_legwear:
                empty_legwear_count += 1

        ratio = empty_legwear_count / N
        # 允许 58% ~ 75% 容差区间
        self.assertGreaterEqual(ratio, 0.58, f"Empty branch ratio too low: {ratio}")
        self.assertLessEqual(ratio, 0.75, f"Empty branch ratio too high: {ratio}")

    def test_07_zero_raw_wildcards_and_braces_in_sampled_tags(self):
        """验证所有批次相关采样的标签中，绝对无 '__by_source' 与 '{...}' 裸语法标记。"""
        rng = random.Random(4004)
        targets_to_test = [
            ("jewelry", "combo_necklace_choker"),
            ("jewelry", "acc_bowtie"),
            ("jewelry", "acc_necktie"),
            ("clothing", "clothing_legwear_combo"),
            ("clothing", "bottom_jeans_casual"),
            ("clothing", "bottom_pantyhose_pants"),
            ("clothing", "denim_shorts"),
            ("clothing", "bottom_shorts_casual"),
        ]

        for kind, item_id in targets_to_test:
            for _ in range(10):
                if kind == "jewelry":
                    res = self.sampler.sample_jewelry_result(item_id, rng)
                    tags = res.tags if res else ()
                else:
                    c_res = self.sampler.sample_clothing_result(item_id, "none", "l1", rng)
                    tags = tuple(t.text for t in c_res.base_tags)

                for t in tags:
                    self.assertNotIn("__by_source", t, f"Raw wildcard token leaked in {item_id}: {t}")
                    self.assertNotIn("{", t, f"Unparsed opening brace leaked in {item_id}: {t}")
                    self.assertNotIn("}", t, f"Unparsed closing brace leaked in {item_id}: {t}")
                    validate_prompt_syntax(t)

    def test_08_ingestion_idempotency_verification(self):
        """在隔离临时副本中验证两次真实写入及哈希一致性，遵守只读审核约束不触碰生产目录及账本。"""
        import shutil
        import tempfile

        target_fnames = ["expressions.json", "lighting.json", "accessories.json", "clothing.json"]

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            tmp_data_dir = tmp_dir / "data"
            tmp_data_dir.mkdir(parents=True, exist_ok=True)
            tmp_snapshot_dir = tmp_dir / "scratch/m4_snapshots/batch_1_pre_ingest"
            tmp_snapshot_dir.mkdir(parents=True, exist_ok=True)
            tmp_ledger_path = tmp_dir / "scratch/m4_batch1_execution_ledger.json"

            # 1. 将生产数据与入库前基线快照复制到临时副本
            for fname in target_fnames:
                shutil.copy2(DATA_DIR / fname, tmp_data_dir / fname)
                shutil.copy2(SNAPSHOT_DIR / fname, tmp_snapshot_dir / fname)

            # 记录临时目录真实执行前的哈希
            hashes_before = {fname: get_sha256(tmp_data_dir / fname) for fname in target_fnames}
            snapshot_hashes_before = {fname: get_sha256(tmp_snapshot_dir / fname) for fname in target_fnames}

            # 2. 第一次在临时副本中真实执行入库（dry_run=False）
            res1 = execute_batch1_ingestion(
                tmp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=tmp_snapshot_dir,
                ledger_path=tmp_ledger_path,
            )
            st1 = res1["stats"]
            self.assertEqual(st1["added_items"], 0, f"Expected 0 added items on run 1, got {st1['added_items']}")
            self.assertEqual(st1["added_tags"], 0, f"Expected 0 added tags on run 1, got {st1['added_tags']}")
            hashes_after_run1 = {fname: get_sha256(tmp_data_dir / fname) for fname in target_fnames}
            self.assertEqual(hashes_before, hashes_after_run1, "File content changed during first idempotent write")

            # 3. 第二次在临时副本中真实执行入库（dry_run=False）
            res2 = execute_batch1_ingestion(
                tmp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=tmp_snapshot_dir,
                ledger_path=tmp_ledger_path,
            )
            st2 = res2["stats"]
            self.assertEqual(st2["added_items"], 0, f"Expected 0 added items on run 2, got {st2['added_items']}")
            self.assertEqual(st2["added_tags"], 0, f"Expected 0 added tags on run 2, got {st2['added_tags']}")
            hashes_after_run2 = {fname: get_sha256(tmp_data_dir / fname) for fname in target_fnames}
            self.assertEqual(hashes_after_run1, hashes_after_run2, "File content changed during second idempotent write")

            # 4. 严格断言初始快照在两次真实写入中均未被修改或覆盖
            snapshot_hashes_after = {fname: get_sha256(tmp_snapshot_dir / fname) for fname in target_fnames}
            self.assertEqual(snapshot_hashes_before, snapshot_hashes_after, "Snapshot was illegally overwritten")

            # 5. 断言独立账本已在临时目录成功写入且包含 runs 记录
            self.assertTrue(tmp_ledger_path.exists())
            ledger_data = json.loads(tmp_ledger_path.read_text(encoding="utf-8"))
            self.assertIn("runs", ledger_data)
            self.assertEqual(len(ledger_data["runs"]), 2)

    def test_09_all_127_mappings_reachable(self):
        """全量 127 条目标映射逐条采样可达性核验与变体多样性断言。"""
        seeds_to_test = [0, 1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 42, 43, 47]
        unreachable = []

        for m in self.b1_mappings:
            mid = m["mapping_id"]
            target_file = m["target_catalog_file"]
            item_id = m["target_item_id"]
            tag_id = m["target_tag_id"]
            tag_text = m["target_tag_text"]
            role = m["target_role"]

            is_reached = False

            if target_file == "expressions.json":
                try:
                    res = self.sampler.sample_expression_result(tag_id, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass
                if not is_reached:
                    for s in seeds_to_test:
                        res = self.sampler.sample_expression_result(item_id, random.Random(s))
                        if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                            is_reached = True
                            break

            elif target_file == "lighting.json":
                try:
                    res = self.sampler.sample_lighting_result(tag_id, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass
                if not is_reached:
                    for s in seeds_to_test:
                        res = self.sampler.sample_lighting_result(item_id, random.Random(s))
                        if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                            is_reached = True
                            break

            elif target_file == "accessories.json":
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

            elif target_file == "clothing.json":
                try:
                    res = self.sampler.sample_clothing_result(tag_id, "None", "L2", random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.base_tags):
                        is_reached = True
                except Exception:
                    pass
                if not is_reached:
                    for s in seeds_to_test:
                        res = self.sampler.sample_clothing_result(item_id, "None", "L2", random.Random(s))
                        if res:
                            if role == "ensemble_combo" and item_id == "clothing_legwear_combo":
                                if len(res.base_tags) > 0 and all(t.provenance.item_id == item_id for t in res.base_tags):
                                    is_reached = True
                                    break
                            elif any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.base_tags):
                                is_reached = True
                                break

            if not is_reached:
                unreachable.append(f"{mid} ({item_id}/{tag_id}: '{tag_text}')")

        self.assertEqual(len(unreachable), 0, f"Unreachable mappings found: {unreachable}")

        # 专项断言：leather_choker 连续 30 个种子抽样必须产出丰富的变体多样性 (覆盖 5+ 种独立变体)
        choker_variants_seen = set()
        for seed in range(30):
            res = self.sampler.sample_jewelry_result("leather_choker", random.Random(seed))
            choker_variants_seen.update(res.tags)

        self.assertGreaterEqual(
            len(choker_variants_seen),
            5,
            f"leather_choker lacks variant diversity across 30 seeds: {choker_variants_seen}",
        )

    def test_10_pool_registry_fidelity_and_recursion_error_handling(self):
        """词池保真度核验：来源候选完整性、动态颜色递归展开与 Fail-Closed 异常契约。"""
        necklaces_pool = BATCH1_POOL_REGISTRY["10_accessories/necklaces"]
        expected_necklace_items = [
            "key necklace",
            "lock necklace",
            "magatama necklace",
            "ring necklace",
            "star necklace",
            "tooth necklace",
            "cross necklace",
            "heart necklace",
            "pearl necklace",
        ]
        for exp in expected_necklace_items:
            self.assertIn(exp, necklaces_pool, f"Missing audited candidate in necklaces pool: {exp}")

        # 验证项圈词池包含动态颜色语法，且原模板独立的 4::black 分支全部保留
        chokers_pool = BATCH1_POOL_REGISTRY["10_accessories/chokers"]
        self.assertTrue(any("{__by_source" in c for c in chokers_pool), "Chokers pool lost dynamic color syntax")
        self.assertTrue(all("4::black" in c for c in chokers_pool), "Chokers pool lost 4::black weighted branch")

        # 验证颜色依赖池完整来源于冻结源 09_style/colors.txt (312 来源候选 100% 一致，无遗漏无额外加入)
        colors_file = REPO_DIR.parent / "ai-image-wildcards" / "wildcards" / "09_style" / "colors.txt"
        self.assertTrue(colors_file.exists(), f"Source colors file missing: {colors_file}")
        source_colors = [
            line.strip().lower()
            for line in colors_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        registry_colors = BATCH1_POOL_REGISTRY["09_style/colors"]
        self.assertEqual(len(source_colors), 312, f"Expected 312 source colors, got {len(source_colors)}")
        self.assertEqual(len(registry_colors), 312, f"Expected 312 registry colors, got {len(registry_colors)}")
        self.assertEqual(registry_colors, source_colors, "Registry colors list order/content does not match colors.txt")
        self.assertEqual(set(registry_colors), set(source_colors), "Registry colors set does not match colors.txt")

        # 验证解析后的文本不包含任何花括号或裸引用，且抽取的颜色必须严格属于 312 项来源集合
        rng = random.Random(777)
        for _ in range(50):
            sample_choker = resolve_pool_reference("10_accessories/chokers", rng)
            self.assertNotIn("{", sample_choker)
            self.assertNotIn("}", sample_choker)
            self.assertNotIn("__by_source", sample_choker)
            # 抽样文本中的颜色必须属于 312 来源集合或独立 4::black 分支
            allowed_colors = set(source_colors).union({"black"})
            matched_color = any(
                c in sample_choker for c in allowed_colors
            )
            self.assertTrue(matched_color, f"Color not recognized in sample choker: {sample_choker}")

        # 验证 Fail-Closed 异常处理：缺失词池抛出 PoolResolutionError
        with self.assertRaises(PoolResolutionError):
            resolve_pool_reference("non_existent_pool_reference", rng)

        # 验证循环引用抛出 PoolResolutionError
        try:
            BATCH1_POOL_REGISTRY["_mock_cycle_a"] = ["__by_source/_mock_cycle_b__"]
            BATCH1_POOL_REGISTRY["_mock_cycle_b"] = ["__by_source/_mock_cycle_a__"]
            with self.assertRaises(PoolResolutionError):
                resolve_pool_reference("_mock_cycle_a", rng)
        finally:
            BATCH1_POOL_REGISTRY.pop("_mock_cycle_a", None)
            BATCH1_POOL_REGISTRY.pop("_mock_cycle_b", None)


if __name__ == "__main__":
    unittest.main()
