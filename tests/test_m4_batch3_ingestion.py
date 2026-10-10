#!/usr/bin/env python3
"""
tests/test_m4_batch3_ingestion.py — M4 Batch 3 自动化验收测试套件

测试覆盖范围：
1. 来源实体与目标映射双向对账核验 (202 来源实体 / 202 条目标映射，1 隔离，201 候选：18 REUSE, 56 VARIANT, 127 NEW, 0 COMBO)；
2. 目标数据文件 (shot_types.json & film_stocks.json) 100% 满足 JSON Schema 契约；
3. 硬件不兼容隔离项物理隔离防泄露断言 (SRC_CAM_06691 / MAP_00352 零侵入生产库)；
4. 编目索引 ExactCatalogIndex 在全部容器上 0 键冲突；
5. 全量 201 条可入库候选映射逐条采样可达性核验 (0 不可达)；
6. 器材套机兼容性与胶卷 ISO/EI 真实测定保真度核验；
7. 隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)。
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
from lib.models import CameraHardwareFacts, PromptAtom, SemanticFacts, SpanType, TagProvenance
from lib.sampler import DataSampler, ExactCatalogIndex, SampledTag
from nodes import _make_slot_fragments
from scratch.apply_m4_batch3_ingestion import (
    CAMERA_ANGLE_ITEM_IDS,
    execute_batch3_ingestion,
    get_sha256,
    load_batch3_ledger,
)

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
SNAPSHOT_DIR = REPO_DIR / "scratch/m4_snapshots/batch_3_pre_ingest"


class TestM4Batch3Ingestion(unittest.TestCase):
    """M4 Batch 3 自动化验收测试套件。"""

    def setUp(self):
        self.b3_sources, self.b3_mappings = load_batch3_ledger()
        self.sampler = DataSampler(data_dir=DATA_DIR)
        self.resolver = ConflictResolver(data_dir=DATA_DIR)
        self.assembler = PromptAssembler(data_dir=DATA_DIR)

    def test_01_ledger_mapping_and_source_reconciliation(self):
        """核对 Batch 3 来源实体与目标映射双向对账矩阵。"""
        # 202 个来源实体, 202 条目标映射，严格 1:1
        self.assertEqual(len(self.b3_sources), 202)
        self.assertEqual(len(self.b3_mappings), 202)
        unique_eids = set(s["entity_id"] for s in self.b3_sources)
        self.assertEqual(len(unique_eids), 202)

        # 隔离项核验
        quarantine_sources = [s for s in self.b3_sources if s["entity_id"] == "SRC_CAM_06691"]
        self.assertEqual(len(quarantine_sources), 1)
        self.assertEqual(quarantine_sources[0]["decision"], "DEFERRED_ISSUE")

        quarantine_mappings = [m for m in self.b3_mappings if m["mapping_id"] == "MAP_00352"]
        self.assertEqual(len(quarantine_mappings), 1)
        self.assertEqual(quarantine_mappings[0]["target_role"], "quarantine")

        # 候选映射决策构成核验 (排除 1 条隔离项后共 201 条)
        valid_mappings = [m for m in self.b3_mappings if m["mapping_id"] != "MAP_00352"]
        source_map = {s["entity_id"]: s for s in self.b3_sources}

        counts_by_decision = {}
        for m in valid_mappings:
            d = source_map[m["source_entity_id"]]["decision"]
            counts_by_decision[d] = counts_by_decision.get(d, 0) + 1

        self.assertEqual(counts_by_decision.get("REUSE_EXISTING"), 18)
        self.assertEqual(counts_by_decision.get("STYLE_VARIANT"), 56)
        self.assertEqual(counts_by_decision.get("NEW_STYLE"), 127)
        self.assertEqual(counts_by_decision.get("ENSEMBLE_COMBO", 0), 0)
        self.assertEqual(sum(counts_by_decision.values()), 201)

        # 目标文件分布核验
        st_maps = [m for m in valid_mappings if m["target_catalog_file"] == "shot_types.json"]
        fs_maps = [m for m in valid_mappings if m["target_catalog_file"] == "film_stocks.json"]
        self.assertEqual(len(st_maps), 72)
        self.assertEqual(len(fs_maps), 129)

    def test_02_production_files_schema_conformance(self):
        """校验 shot_types.json 与 film_stocks.json 100% 满足其 JSON Schema 契约。"""
        files_to_check = [
            ("shot_types.json", "shot_types.schema.json"),
            ("film_stocks.json", "film_stocks.schema.json"),
        ]

        for data_name, schema_name in files_to_check:
            data_file = DATA_DIR / data_name
            schema_file = SCHEMAS_DIR / schema_name
            self.assertTrue(data_file.exists(), f"Missing data file: {data_file}")
            self.assertTrue(schema_file.exists(), f"Missing schema file: {schema_file}")

            data = json.loads(data_file.read_text(encoding="utf-8"))
            schema = json.loads(schema_file.read_text(encoding="utf-8"))

            resolver = jsonschema.RefResolver.from_schema(schema)
            validator = jsonschema.Draft7Validator(schema, resolver=resolver)
            errors = list(validator.iter_errors(data))
            err_msgs = [f"{e.message} at {list(e.path)}" for e in errors]
            self.assertEqual(len(errors), 0, f"Schema validation failed for {data_name}: {err_msgs}")

    def test_03_quarantine_isolation_enforcement(self):
        """硬件不兼容隔离项物理隔离防泄露断言 (SRC_CAM_06691 / MAP_00352 零侵入生产库)。"""
        fs_text = (DATA_DIR / "film_stocks.json").read_text(encoding="utf-8")
        st_text = (DATA_DIR / "shot_types.json").read_text(encoding="utf-8")

        # 1. 隔离 ID 严禁出现
        self.assertNotIn("SRC_CAM_06691", fs_text)
        self.assertNotIn("SRC_CAM_06691", st_text)
        self.assertNotIn("MAP_00352", fs_text)
        self.assertNotIn("MAP_00352", st_text)

        # 2. 隔离标签 ID 与标签文本严禁出现
        self.assertNotIn("cam_hw__sigma_sd_quattro_h_with_sigma_24_70mm_f_2_8_dg", fs_text)
        self.assertNotIn("cam_hw__sigma_sd_quattro_h_with_sigma_24_70mm_f_2_8_dg", st_text)
        self.assertNotIn("Sigma sd Quattro H with Sigma 24-70mm f/2.8 DG DN Art", fs_text)
        self.assertNotIn("Sigma sd Quattro H with Sigma 24-70mm f/2.8 DG DN Art", st_text)
        self.assertNotIn("sd Quattro H", fs_text)
        self.assertNotIn("sd Quattro H", st_text)

        # 3. 适马合法条目正常保留且仅有原生合规套机
        fs_data = json.loads(fs_text)
        sigma_items = [it for it in fs_data.get("cinema_lenses", []) if it.get("id") == "camera_hardware_sigma"]
        self.assertEqual(len(sigma_items), 1)
        sigma_tags = sigma_items[0].get("tags", [])
        self.assertEqual(len(sigma_tags), 1)
        self.assertEqual(sigma_tags[0]["id"], "cam_hw__sigma_fp_with_sigma_45mm_f_2_8_dg_dn")
        self.assertIn("Sigma fp with Sigma 45mm", sigma_tags[0]["text"])

    def test_04_exact_catalog_indexing_zero_collisions(self):
        """校验 ExactCatalogIndex 在全部目标文件容器上 0 键冲突。"""
        # shot_types.json 容器
        st_data = self.sampler._load("shot_types")
        for container_key in ("shot_types", "camera_angles"):
            items = st_data.get(container_key, [])
            idx = ExactCatalogIndex(f"shot_types_{container_key}")
            for it in items:
                idx.register_item(it)
            self.assertGreater(len(items), 0)

        # film_stocks.json 容器
        fs_data = self.sampler._load("film_stocks")
        for container_key in ("film_stocks", "cinema_lenses", "weather_moods", "photography_styles"):
            items = fs_data.get(container_key, [])
            idx = ExactCatalogIndex(f"film_stocks_{container_key}")
            for it in items:
                idx.register_item(it)
            self.assertGreater(len(items), 0)

    def test_05_all_201_mappings_reachable(self):
        """全量 201 条可入库目标映射逐条采样可达性核验。"""
        seeds_to_test = [0, 1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 42]
        unreachable = []

        valid_mappings = [m for m in self.b3_mappings if m["mapping_id"] != "MAP_00352"]

        for m in valid_mappings:
            mid = m["mapping_id"]
            target_file = m["target_catalog_file"]
            item_id = m["target_item_id"]
            tag_id = m["target_tag_id"]
            tag_text = m["target_tag_text"]

            is_reached = False

            if target_file == "shot_types.json":
                is_angle = item_id in CAMERA_ANGLE_ITEM_IDS
                if is_angle:
                    # 1. 尝试以 tag_id 直接采样
                    try:
                        res = self.sampler.sample_camera_angle_result(tag_id, random.Random(42))
                        if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                            is_reached = True
                    except Exception:
                        pass
                    # 2. 若未命中，通过 item_id 多种子遍历
                    if not is_reached:
                        for s in seeds_to_test:
                            res = self.sampler.sample_camera_angle_result(item_id, random.Random(s))
                            if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                                is_reached = True
                                break
                else:
                    # 1. 尝试以 tag_id 直接采样
                    try:
                        res = self.sampler.sample_shot_type_result(tag_id, random.Random(42))
                        if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                            is_reached = True
                    except Exception:
                        pass
                    # 2. 若未命中，通过 item_id 多种子遍历
                    if not is_reached:
                        for s in seeds_to_test:
                            res = self.sampler.sample_shot_type_result(item_id, random.Random(s))
                            if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                                is_reached = True
                                break
            elif target_file == "film_stocks.json":
                # 1. 尝试以 tag_id 直接采样
                try:
                    res = self.sampler.sample_film_result(tag_id, random.Random(42))
                    if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                        is_reached = True
                except Exception:
                    pass
                # 2. 若未命中，通过 item_id 多种子遍历
                if not is_reached:
                    for s in seeds_to_test:
                        res = self.sampler.sample_film_result(item_id, random.Random(s))
                        if res and any(t.id == tag_id or tag_text.casefold() in t.text.casefold() for t in res.sampled_tags):
                            is_reached = True
                            break

            if not is_reached:
                unreachable.append((mid, target_file, item_id, tag_id, tag_text))

        self.assertEqual(len(unreachable), 0, f"Unreachable mappings found ({len(unreachable)}): {unreachable[:5]}")

    def test_06_camera_hardware_and_film_stock_fidelity(self):
        """核验专业相机套机原生兼容性与胶卷测定 ISO / 推荐 EI 分离事实。"""
        fs_data = self.sampler._load("film_stocks")

        # 1. 胶卷感光度与测定 ISO 区分核验 (Ilford Delta 3200)
        films_by_id = {f["id"]: f for f in fs_data.get("film_stocks", [])}
        self.assertIn("ilford_delta_3200", films_by_id)
        delta3200_tags = films_by_id["ilford_delta_3200"]["tags"]
        for t in delta3200_tags:
            cf = t.get("facts", {}).get("camera_facts", {})
            if cf.get("nominal_name_rating") == 3200:
                self.assertEqual(cf.get("measured_iso"), 1000)
                self.assertEqual(cf.get("recommended_ei"), 3200)

        # 2. 暗房相纸标记核验
        self.assertIn("darkroom_photographic_paper", films_by_id)
        paper_item = films_by_id["darkroom_photographic_paper"]
        self.assertEqual(paper_item["series"], "🔵 冷调/特殊系")
        for t in paper_item["tags"]:
            cf = t.get("facts", {}).get("camera_facts", {})
            self.assertFalse(cf.get("is_camera_film", True))

        # 3. 14 款相机器材套机原生卡口与规格核验
        lenses_by_id = {lens["id"]: lens for lens in fs_data.get("cinema_lenses", [])}
        expected_brands = [
            "canon", "dji", "fujifilm", "gopro", "hasselblad", "kodak",
            "leica", "nikon", "olympus", "panasonic", "pentax", "ricoh",
            "sigma", "sony"
        ]
        for b in expected_brands:
            hw_id = f"camera_hardware_{b}"
            self.assertIn(hw_id, lenses_by_id, f"Missing hardware item: {hw_id}")
            hw_item = lenses_by_id[hw_id]
            self.assertTrue(len(hw_item.get("tags", [])) > 0)
            for t in hw_item["tags"]:
                facts = t.get("facts", {})
                gov = facts.get("governance_metadata", {})
                cf = facts.get("camera_facts", {})
                self.assertTrue(gov.get("hardware_kit_compatible", False))
                self.assertEqual(gov.get("kit_compatibility_status"), "NATIVE_COMPATIBLE")
                self.assertEqual(cf.get("device_category"), "camera_hardware_kit")

    def test_07_ingestion_idempotency_verification(self):
        """隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验 (遵守只读审核约束)。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_data_dir = Path(tmpdir) / "data"
            temp_data_dir.mkdir(parents=True, exist_ok=True)
            temp_snapshot_dir = Path(tmpdir) / "snapshots"
            temp_ledger_path = Path(tmpdir) / "ledger.json"

            # 拷贝基线数据至隔离目录 (从入库前快照加载基线)
            for fname in ("shot_types.json", "film_stocks.json"):
                shutil.copy2(SNAPSHOT_DIR / fname, temp_data_dir / fname)

            # 第一遍真实写入
            res1 = execute_batch3_ingestion(
                data_dir=temp_data_dir,
                dry_run=False,
                backup_snapshot=True,
                snapshot_dir=temp_snapshot_dir,
                ledger_path=temp_ledger_path,
            )
            stats1 = res1["stats"]
            hashes1 = res1["file_hashes"]

            self.assertEqual(stats1["quarantined_sources"], 1)
            self.assertEqual(stats1["quarantined_mappings"], 1)
            self.assertEqual(stats1["candidate_mappings"], 201)
            self.assertEqual(stats1["added_items"], 45)
            self.assertEqual(stats1["added_tags"], 182)

            # 快照建立核验 (写保护验证)
            for fname in ("shot_types.json", "film_stocks.json"):
                snap_file = temp_snapshot_dir / fname
                self.assertTrue(snap_file.exists(), f"Snapshot file not created: {snap_file}")

            # 第二遍真实写入 (幂等性核验)
            res2 = execute_batch3_ingestion(
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
            self.assertEqual(stats2["idempotent_skipped_tags"], 183)

            # 双遍落盘文件 SHA256 哈希必须绝对一致
            self.assertEqual(hashes1["shot_types.json"], hashes2["shot_types.json"])
            self.assertEqual(hashes1["film_stocks.json"], hashes2["film_stocks.json"])

    def test_08_sampling_to_formal_resolution_end_to_end(self):
        """核验 14 款相机器材套机与 17 款胶卷/相纸集合从采样到生产节点流水线正式消解全链路畅通 (0 契约报错)。"""
        # 1. 14 款相机器材套机生产全链路消解验证
        camera_hardware_items = [
            "camera_hardware_canon", "camera_hardware_dji", "camera_hardware_fujifilm",
            "camera_hardware_gopro", "camera_hardware_hasselblad", "camera_hardware_kodak",
            "camera_hardware_leica", "camera_hardware_nikon", "camera_hardware_olympus",
            "camera_hardware_panasonic", "camera_hardware_pentax", "camera_hardware_ricoh",
            "camera_hardware_sigma", "camera_hardware_sony"
        ]
        for hw_id in camera_hardware_items:
            res = self.sampler.sample_film_result(hw_id, random.Random(42))
            self.assertIsNotNone(res, f"Sampling failed for {hw_id}")
            frags = _make_slot_fragments(res, "film", hw_id, "generator")
            self.assertGreater(len(frags), 0, f"No fragments produced for {hw_id}")
            slots = {"film": frags}
            try:
                assembled = self.assembler.assemble_slots(slots, rng=random.Random(42))
            except RuleConfigurationError as e:
                self.fail(f"RuleConfigurationError raised for camera hardware {hw_id}: {e}")
            self.assertGreater(len(assembled.accepted_atoms), 0)
            self.assertTrue(assembled.prompt)

        # 2. 17 款胶卷/相纸集合生产全链路消解验证
        film_collection_items = [
            "film_stock_adox", "film_stock_agfa", "film_stock_agfaphoto", "film_stock_bergger",
            "film_stock_foma", "film_stock_fujifilm", "film_stock_ilford", "darkroom_photographic_paper",
            "film_stock_kentmere", "film_stock_kodak", "film_stock_konica_minolta", "film_stock_lomography",
            "film_stock_lucky", "film_stock_orwo", "film_stock_polaroid", "film_stock_revue",
            "film_stock_rollei"
        ]
        for film_id in film_collection_items:
            res = self.sampler.sample_film_result(film_id, random.Random(42))
            self.assertIsNotNone(res, f"Sampling failed for {film_id}")
            frags = _make_slot_fragments(res, "film", film_id, "generator")
            self.assertGreater(len(frags), 0, f"No fragments produced for {film_id}")
            slots = {"film": frags}
            try:
                assembled = self.assembler.assemble_slots(slots, rng=random.Random(42))
            except RuleConfigurationError as e:
                self.fail(f"RuleConfigurationError raised for film collection {film_id}: {e}")
            self.assertGreater(len(assembled.accepted_atoms), 0)
            self.assertTrue(assembled.prompt)

        # 3. 用户探针复现用例断言 (probe_0 携带器材 facts，送入消解器严禁报错 missing required color_modes)
        facts = SemanticFacts(
            camera_facts=CameraHardwareFacts(
                device_category="camera_hardware_kit",
                camera_brand="Canon",
                camera_model="EOS 5D Mark IV",
                lens_spec="Canon EF 24-70mm f/2.8L II USM",
                lens_mount="Canon EF",
                is_camera_film=False,
            ),
            explicit_fields=("camera_facts",),
        )
        probe = PromptAtom(
            atom_id="probe_0",
            text="Canon EOS 5D Mark IV with Canon EF 24-70mm f/2.8L II",
            span_type=SpanType.PLAIN,
            source_slot="film",
            facts=facts,
            provenance=TagProvenance(item_id="camera_hardware_canon", kind="film"),
        )
        try:
            resolved = self.resolver.resolve_atoms([probe], rng=random.Random(42))
        except RuleConfigurationError as e:
            self.fail(f"User probe reproduction atom failed with RuleConfigurationError: {e}")
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].text, "Canon EOS 5D Mark IV with Canon EF 24-70mm f/2.8L II")

        # 4. 相机器材与黑白胶片共存测试 (器材严禁被黑白胶片互斥规则误删)
        cam_res = self.sampler.sample_film_result("camera_hardware_canon", random.Random(42))
        bw_res = self.sampler.sample_film_result("kodak_tmax_400", random.Random(42))
        cam_frags = _make_slot_fragments(cam_res, "film", "camera_hardware_canon", "generator")
        bw_frags = _make_slot_fragments(bw_res, "film", "kodak_tmax_400", "generator")
        slots = {"film": cam_frags + bw_frags}
        assembled = self.assembler.assemble_slots(slots, rng=random.Random(42))
        self.assertTrue(any("Canon" in a.text for a in assembled.accepted_atoms), "Camera hardware atom was mistakenly removed!")

        # 5. 暗房相纸色彩规则参与度断言 (严禁误豁免相纸：相纸必须正常触发/参与黑白色彩消解)
        # 反例 1: 黑白胶片 (kodak_tmax_400) + 彩色相纸 (Kodak Professional Metallic) -> 彩色相纸必须被消解剔除
        color_paper_res = self.sampler.sample_film_result("film__kodak_professional_metallic", random.Random(42))
        color_paper_frags = _make_slot_fragments(color_paper_res, "film", "film__kodak_professional_metallic", "generator")
        slots_bw_color = {"film": bw_frags + color_paper_frags}
        assembled_bw_color = self.assembler.assemble_slots(slots_bw_color, rng=random.Random(42))
        self.assertIn("monochrome_film_chroma_coherence", assembled_bw_color.rules_applied)
        self.assertFalse(
            any("Kodak Professional Metallic" in a.text for a in assembled_bw_color.accepted_atoms),
            "Chroma darkroom paper should be dropped by monochrome film!"
        )

        # 反例 2: 彩色胶片 (kodak_portra_400) + 黑白相纸 (Ilford Multigrade IV RC) -> 彩色胶片属性必须被消解剔除
        color_film_res = self.sampler.sample_film_result("kodak_portra_400", random.Random(42))
        bw_paper_res = self.sampler.sample_film_result("film__ilford_multigrade_iv_rc", random.Random(42))
        color_film_frags = _make_slot_fragments(color_film_res, "film", "kodak_portra_400", "generator")
        bw_paper_frags = _make_slot_fragments(bw_paper_res, "film", "film__ilford_multigrade_iv_rc", "generator")
        slots_color_bw = {"film": color_film_frags + bw_paper_frags}
        assembled_color_bw = self.assembler.assemble_slots(slots_color_bw, rng=random.Random(42))
        self.assertIn("monochrome_film_chroma_coherence", assembled_color_bw.rules_applied)
        self.assertTrue(
            any("Ilford Multigrade IV RC" in a.text for a in assembled_color_bw.accepted_atoms),
            "B&W darkroom paper should trigger monochrome and be preserved!"
        )
        self.assertFalse(
            any("warm skin tones" in a.text or "golden" in a.text for a in assembled_color_bw.accepted_atoms),
            "Chroma film tags should be dropped by monochrome darkroom paper!"
        )

    def test_09_brand_and_hardware_collections_strict_single_choice(self):
        """核验全部 31 个品牌集合与器材套机集合在多随机种子下严格单选 1 个产品标签 (杜绝并发冲突)"""
        all_collection_ids = [
            # 14 款相机器材套机
            "camera_hardware_canon", "camera_hardware_dji", "camera_hardware_fujifilm",
            "camera_hardware_gopro", "camera_hardware_hasselblad", "camera_hardware_kodak",
            "camera_hardware_leica", "camera_hardware_nikon", "camera_hardware_olympus",
            "camera_hardware_panasonic", "camera_hardware_pentax", "camera_hardware_ricoh",
            "camera_hardware_sigma", "camera_hardware_sony",
            # 16 款胶卷扩展集合 + 1 款暗房相纸集合
            "film_stock_adox", "film_stock_agfa", "film_stock_agfaphoto", "film_stock_bergger",
            "film_stock_foma", "film_stock_fujifilm", "film_stock_ilford", "darkroom_photographic_paper",
            "film_stock_kentmere", "film_stock_kodak", "film_stock_konica_minolta", "film_stock_lomography",
            "film_stock_lucky", "film_stock_orwo", "film_stock_polaroid", "film_stock_revue",
            "film_stock_rollei"
        ]
        self.assertEqual(len(all_collection_ids), 31)

        # 1. 针对用户明确提出的固定 seed=0 反例逐项硬性核验
        # (a) film_stock_kodak: 严禁同时输出 Kodak Vision2 200T 与 Kodak Aerochrome
        k_res = self.sampler.sample_film_result("film_stock_kodak", random.Random(0))
        self.assertEqual(len(k_res.tags), 1, f"Expected 1 tag for film_stock_kodak seed=0, got {k_res.tags}")
        self.assertEqual(len(k_res.sampled_tags), 1)

        # (b) camera_hardware_canon: 严禁同时输出 EOS R 与 5D Mark IV
        c_res = self.sampler.sample_film_result("camera_hardware_canon", random.Random(0))
        self.assertEqual(len(c_res.tags), 1, f"Expected 1 tag for camera_hardware_canon seed=0, got {c_res.tags}")
        self.assertEqual(len(c_res.sampled_tags), 1)

        # (c) darkroom_photographic_paper: 严禁同时输出彩色 Metallic 与黑白 Multigrade
        p_res = self.sampler.sample_film_result("darkroom_photographic_paper", random.Random(0))
        self.assertEqual(len(p_res.tags), 1, f"Expected 1 tag for darkroom_photographic_paper seed=0, got {p_res.tags}")
        self.assertEqual(len(p_res.sampled_tags), 1)

        # 2. 遍历全部 31 个集合，100 个随机种子全量覆盖断言严格单选
        for cid in all_collection_ids:
            for s in range(100):
                res = self.sampler.sample_film_result(cid, random.Random(s))
                self.assertIsNotNone(res)
                self.assertEqual(
                    len(res.tags), 1,
                    f"Collection {cid} at seed={s} produced {len(res.tags)} tags: {res.tags}"
                )
                self.assertEqual(len(res.sampled_tags), 1)

        # 3. 对照验证：非集合单款胶卷（如 kodak_portra_400）仍然支持 1~2 个风格修饰标签
        portra_tag_lens = set()
        for s in range(50):
            res = self.sampler.sample_film_result("kodak_portra_400", random.Random(s))
            self.assertIsNotNone(res)
            self.assertIn(len(res.tags), (1, 2))
            portra_tag_lens.add(len(res.tags))
        self.assertEqual(portra_tag_lens, {1, 2}, "Single-stock film should still support 1~2 tags sampling")


if __name__ == "__main__":
    unittest.main()
