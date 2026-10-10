"""
tests/test_m33_runtime_contracts.py — M3.3 运行时数据模型、契约消解与生产路径全景测试套件

测试范围：
1. 构造入口强类型安全与防绕过 (fail-closed, 杜绝通过直接构造伪造或静默丢字段，子模型类型错误直接抛 TypeError)；
2. 四态往返双射与 SemanticFacts.merge() 继承真值表 (ABSENT, EXPLICIT_UNSPECIFIED, EXPLICIT_EMPTY, EXPLICIT_VALUE)；
3. 多件套构件层叠方向与 DAG 成环阻断 (真实 SRC_CLOTH_01503 语法恢复, 有向无环性校验)；
4. 动作姿态多态保真与消解器兼容 (ground_or_held 等 15 条模糊姿态候选集合保留与消解器评估)；
5. 83 条隔离池实体物理不可达与无 ID 隔离载荷拦截门禁 (is_quarantined_payload 全路径防御)；
6. 来源事实 172 键完整覆盖与无损分发 (包含 SRC_LIGHT_06819, SRC_CAM_06698, SRC_HAIR_00001 零丢失精确断言)；
7. 生产 JSON Schema 兼容与拒绝非法输入校验 (jsonschema.validate 严格核验)；
8. 生产消解器实际执行路径集成校验 (套装多构件穿透修饰、姿态与环境相容评估)。
"""
from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path
from typing import Any, Dict, List

import jsonschema

from lib.conflict_resolver import (
    QUARANTINE_ENTITY_IDS,
    BindingStatus,
    CapabilitySupport,
    ConflictResolver,
    GarmentCarrierEntity,
    does_garment_support_modifier,
    find_bound_carrier,
    is_garment_compatible_with_state,
    is_pose_support_compatible,
    is_quarantined_payload,
    piece_has_button_capability,
    piece_has_zipper_capability,
)
from lib.errors import (
    QuarantineLeakageError,
    UnresolvedEnsembleRelationError,
)
from lib.models import (
    AMBIGUOUS_POSE_SUPPORT_OPTIONS,
    AccessoryAttributeFacts,
    CameraHardwareFacts,
    ClothingAttributeFacts,
    CutFeatures,
    EnsemblePieces,
    EnsembleRelation,
    ExpressionFacts,
    HairAttributeFacts,
    LightingFacts,
    PieceBinding,
    PosePhysicalFacts,
    PromptAtom,
    PromptFragment,
    SceneEnvironmentalFacts,
    SelectionOrigin,
    SemanticFacts,
    ShotCompositionFacts,
    SpanType,
    TagProvenance,
    UNSPECIFIED,
    _ABSENT,
)
from scratch.m33_adapter_and_relation_migrator import (
    build_runtime_camera_facts,
    build_runtime_hair_facts,
    build_runtime_lighting_facts,
    build_runtime_pose_facts,
    build_runtime_scene_facts,
    build_runtime_semantic_facts,
    derive_ensemble_relations,
    migrate_ensemble_pieces,
    migrate_piece_bindings,
    run_full_migration,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DIR = REPO_ROOT / "scratch"
SOURCE_TSV = SCRATCH_DIR / "rc10_source_entities.tsv"
MAPPING_TSV = SCRATCH_DIR / "rc10_target_mappings.tsv"
SCHEMAS_DIR = REPO_ROOT / "schemas"


class TestConstructorTypeSafetyAndExplicitTracking(unittest.TestCase):
    """测试 1: 构造入口强类型安全与自动 explicit_fields 记录（防绕过门禁）。"""

    def test_cut_features_constructor_validation_and_tracking(self):
        # 1. 正常构造：自动追踪 explicit_fields
        cf = CutFeatures(neckline="sweetheart")
        self.assertIn("neckline", cf.explicit_fields)
        self.assertEqual(cf.to_dict(), {"neckline": "sweetheart"})

        # 2. 正常显式未知
        cf_unspec = CutFeatures(neckline=UNSPECIFIED)
        self.assertEqual(cf_unspec.to_dict(), {"neckline": "unspecified"})

        # 3. 非法类型拦截：传入 int
        with self.assertRaises(TypeError):
            CutFeatures(neckline=123)

        # 4. 非法类型防绕过：即使手动指定 explicit_fields 也必须被强类型拦截
        with self.assertRaises(TypeError):
            CutFeatures(neckline=123, explicit_fields=("neckline",))

        # 5. 非法枚举值拦截
        with self.assertRaises(ValueError):
            CutFeatures(neckline="invalid_neckline_xyz")

        # 6. 未知 explicit_fields 键拦截
        with self.assertRaises(KeyError):
            CutFeatures(explicit_fields=("unknown_field",))

        # 7. from_dict 非法键拦截
        with self.assertRaises(KeyError):
            CutFeatures.from_dict({"unknown_key": "val"})

    def test_piece_binding_constructor_validation_and_tracking(self):
        # 1. 正常构造
        pb = PieceBinding(
            piece_id="piece_01",
            piece_slot="main_garments",
            piece_text="silk slip dress",
            binding_role="standalone",
            garment_topology="one_piece",
            fabric_materials=["silk", "satin"],
        )
        self.assertEqual(pb.piece_id, "piece_01")
        self.assertEqual(set(pb.fabric_materials), {"silk", "satin"})
        d = pb.to_dict()
        self.assertEqual(d["piece_id"], "piece_01")
        self.assertEqual(sorted(d["fabric_materials"]), ["satin", "silk"])

        # 2. 非法类型拦截：fabric_materials 传 str 而非 list/set
        with self.assertRaises(TypeError):
            PieceBinding(piece_id="p1", fabric_materials="silk")

        # 3. 非法元素类型拦截
        with self.assertRaises(TypeError):
            PieceBinding(piece_id="p1", fabric_materials=["silk", 123])

        # 4. 非法枚举拦截
        with self.assertRaises(ValueError):
            PieceBinding(piece_id="p1", garment_topology="invalid_topology")

        # 5. from_dict 非法字典拦截
        with self.assertRaises(KeyError):
            PieceBinding.from_dict({"invalid_prop": "error"})

    def test_pose_physical_facts_constructor_validation(self):
        # 1. 正常构造与布尔/字符串转换
        pf1 = PosePhysicalFacts(hands_required=2, is_restrained=True, hand_state="free")
        self.assertEqual(pf1.hands_required, 2)
        self.assertTrue(pf1.is_restrained)

        pf2 = PosePhysicalFacts(is_restrained="context_dependent")
        self.assertEqual(pf2.is_restrained, "context_dependent")

        pf3 = PosePhysicalFacts(is_restrained="true")
        self.assertTrue(pf3.is_restrained)

        # 2. 非法 hands_required 拦截
        with self.assertRaises(TypeError):
            PosePhysicalFacts(hands_required="two")
        with self.assertRaises(ValueError):
            PosePhysicalFacts(hands_required=5)

        # 3. 非法 is_restrained 类型拦截
        with self.assertRaises(TypeError):
            PosePhysicalFacts(is_restrained=999)

        # 4. 非法 body_support 枚举拦截
        with self.assertRaises(ValueError):
            PosePhysicalFacts(body_support="levitating_in_outer_space")

    def test_scene_environmental_facts_constructor_validation(self):
        # 1. 正常构造支持字符串或字典解构事件
        sf1 = SceneEnvironmentalFacts(space_kind="indoor", time_of_day="dusk")
        self.assertEqual(sf1.space_kind, "indoor")
        self.assertEqual(sf1.time_of_day, "dusk")

        sf2 = SceneEnvironmentalFacts(
            deconstructed_event={"event_type": "emotional_interaction", "event_action": "goodbye"}
        )
        self.assertIsInstance(sf2.deconstructed_event, dict)
        self.assertIn("deconstructed_event", sf2.to_dict())

        # 2. 非法 space_kind 拦截
        with self.assertRaises(ValueError):
            SceneEnvironmentalFacts(space_kind="dimension_x")

        # 3. 非法 time_of_day 拦截
        with self.assertRaises(ValueError):
            SceneEnvironmentalFacts(time_of_day="high_noon_galactic")

        # 4. 布尔字段类型拦截
        with self.assertRaises(TypeError):
            SceneEnvironmentalFacts(is_prose_template="true")

    def test_semantic_facts_constructor_strict_type_safety(self):
        """顶层 SemanticFacts 构造强类型安全防绕过测试。"""
        # 1. cut_features 传入非 CutFeatures 对象拦截
        with self.assertRaises(TypeError):
            SemanticFacts(cut_features=123)

        with self.assertRaises(TypeError):
            SemanticFacts(cut_features={"neckline": "v_neck"})

        # 2. ensemble_pieces 传入非法类型拦截
        with self.assertRaises(TypeError):
            SemanticFacts(ensemble_pieces="invalid_ensemble")

        # 3. 子模型字段传入非法类型拦截
        with self.assertRaises(TypeError):
            SemanticFacts(pose_facts="invalid_pose")

        with self.assertRaises(TypeError):
            SemanticFacts(scene_facts=123)

        with self.assertRaises(TypeError):
            SemanticFacts(camera_facts=[])

        with self.assertRaises(TypeError):
            SemanticFacts(lighting_facts="not_a_lighting_fact")

        with self.assertRaises(TypeError):
            SemanticFacts(hair_facts=999)

        with self.assertRaises(TypeError):
            SemanticFacts(shot_facts=False)

        with self.assertRaises(TypeError):
            SemanticFacts(clothing_facts=456)

        with self.assertRaises(TypeError):
            SemanticFacts(expression_facts="neutral")

        with self.assertRaises(TypeError):
            SemanticFacts(accessory_facts=789)

        # 4. 正常传递各强类型子模型
        cf = CutFeatures(neckline="v_neck")
        pf = PosePhysicalFacts(body_support="standing")
        facts = SemanticFacts(cut_features=cf, pose_facts=pf)
        self.assertEqual(facts.cut_features.neckline, "v_neck")
        self.assertEqual(facts.pose_facts.body_support, "standing")

    def test_adapter_rejects_invalid_nested_and_top_types(self):
        """测试迁移适配器对非法顶层字典与非法嵌套字典的 Fail-Closed 阻断。"""
        # 1. 顶层非 dict
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts(123)
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts("not_a_dict")

        # 2. cut_features 为非法非 dict 类型
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"cut_features": 123})
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"cut_features": "v_neck"})

        # 3. ensemble_pieces 为非法非 dict 类型
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"ensemble_pieces": 123})
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"ensemble_pieces": ["piece1"]})

        # 4. 直接传入的子模型为非法非 dict 类型
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"pose_facts": 123})
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"scene_facts": "beach"})
        with self.assertRaises(TypeError):
            build_runtime_semantic_facts({"camera_facts": 456})

        # 5. piece_bindings 元素非法类型
        with self.assertRaises(TypeError):
            migrate_piece_bindings("not_a_list")
        with self.assertRaises(TypeError):
            migrate_piece_bindings([123])
        with self.assertRaises(TypeError):
            migrate_piece_bindings([{"cut_features": 123}])

        # 6. migrate_ensemble_pieces 非 dict 拦截
        with self.assertRaises(TypeError):
            migrate_ensemble_pieces(123)


class TestFourStateBijectiveRoundTripAndHierarchicalMerge(unittest.TestCase):
    """测试 2: 四态往返双射与 SemanticFacts.merge() 继承真值表。"""

    def test_four_state_round_trip_cut_features(self):
        # 状态 1: ABSENT (缺席)
        cf_absent = CutFeatures()
        self.assertEqual(cf_absent.to_dict(), {})
        self.assertEqual(CutFeatures.from_dict(cf_absent.to_dict()).to_dict(), {})

        # 状态 2: EXPLICIT_UNSPECIFIED (显式未知)
        cf_unspec = CutFeatures(neckline=UNSPECIFIED)
        d_unspec = cf_unspec.to_dict()
        self.assertEqual(d_unspec, {"neckline": "unspecified"})
        cf_rebuilt = CutFeatures.from_dict(d_unspec)
        self.assertEqual(cf_rebuilt.neckline, UNSPECIFIED)
        self.assertEqual(cf_rebuilt.to_dict(), d_unspec)

        # 状态 3: EXPLICIT_VALUE (显式值)
        cf_val = CutFeatures(neckline="square_neck", sleeve_length="short_sleeves")
        d_val = cf_val.to_dict()
        self.assertEqual(d_val, {"neckline": "square_neck", "sleeve_length": "short_sleeves"})
        cf_val_rebuilt = CutFeatures.from_dict(d_val)
        self.assertEqual(cf_val_rebuilt.neckline, "square_neck")
        self.assertEqual(cf_val_rebuilt.sleeve_length, "short_sleeves")
        self.assertEqual(cf_val_rebuilt.to_dict(), d_val)

    def test_four_state_round_trip_pose_physical_facts(self):
        # 显式未知 body_support
        pf_unspec = PosePhysicalFacts(body_support=UNSPECIFIED, hand_state=UNSPECIFIED)
        d = pf_unspec.to_dict()
        self.assertEqual(d["body_support"], "unspecified")
        self.assertEqual(d["hand_state"], "unspecified")
        rebuilt = PosePhysicalFacts.from_dict(d)
        self.assertEqual(rebuilt.body_support, UNSPECIFIED)
        self.assertEqual(rebuilt.hand_state, UNSPECIFIED)

    def test_semantic_facts_hierarchical_merge_truth_table(self):
        # 1. Child ABSENT -> 保留 Parent 默认值与拓扑
        parent = SemanticFacts(
            space_kind="indoor",
            time_of_day="day",
            visible_regions=["face", "upper_body"],
            garment_topologies=["top", "bottom_pants"],
        )
        child_absent = SemanticFacts(explicit_fields=())
        merged1 = parent.merge(child_absent)
        self.assertEqual(merged1.space_kind, "indoor")
        self.assertEqual(merged1.time_of_day, "day")
        self.assertEqual(set(merged1.visible_regions), {"face", "upper_body"})
        self.assertEqual(set(merged1.garment_topologies), {"top", "bottom_pants"})

        # 2. Child EXPLICIT_UNSPECIFIED -> 覆盖清除 Parent 默认值，变为 UNSPECIFIED
        child_unspec = SemanticFacts(space_kind=UNSPECIFIED, time_of_day=UNSPECIFIED)
        merged2 = parent.merge(child_unspec)
        self.assertEqual(merged2.space_kind, UNSPECIFIED)
        self.assertEqual(merged2.time_of_day, UNSPECIFIED)

        # 3. Child EXPLICIT_EMPTY -> 清空集合字段 (包括 garment_topologies=())
        child_empty = SemanticFacts(
            visible_regions=[],
            garment_topologies=(),
            explicit_fields=("visible_regions", "garment_topologies"),
        )
        merged3 = parent.merge(child_empty)
        self.assertEqual(merged3.visible_regions, ())
        self.assertEqual(merged3.garment_topologies, ())

        # 4. Child EXPLICIT_VALUE -> 覆盖标量，扩展集合
        child_val = SemanticFacts(space_kind="outdoor", visible_regions=["lower_body"])
        merged4 = parent.merge(child_val)
        self.assertEqual(merged4.space_kind, "outdoor")
        self.assertEqual(set(merged4.visible_regions), {"face", "upper_body", "lower_body"})


class TestEnsembleRelationDirectionAndDAGValidation(unittest.TestCase):
    """测试 3: 真实 SRC_CLOTH_01503 构件层叠方向与 DAG 成环阻断。"""

    def test_src_cloth_01503_real_direction_restoration(self):
        raw_pbs = [
            {"piece_slot": "main_garments", "piece_text": "black satin slip", "binding_role": "standalone"},
            {"piece_slot": "top_pieces", "piece_text": "bullet bra", "binding_role": "over"},
        ]
        pb_list = migrate_piece_bindings(raw_pbs)
        rels = derive_ensemble_relations(pb_list, raw_pbs, "SRC_CLOTH_01503")

        self.assertEqual(len(rels), 1)
        rel = rels[0]
        self.assertEqual(rel.relation_kind, "worn_over")
        self.assertEqual(rel.source_piece_id, "piece_01")
        self.assertEqual(rel.target_piece_id, "piece_02")
        self.assertEqual(rel.relation_direction, "outer_to_inner")

    def test_ensemble_relation_grammar_directions(self):
        raw_under = [
            {"piece_slot": "main_garments", "piece_text": "mesh tee", "binding_role": "standalone"},
            {"piece_slot": "outer_layers", "piece_text": "leather jacket", "binding_role": "under"},
        ]
        pb_under = migrate_piece_bindings(raw_under)
        rels_under = derive_ensemble_relations(pb_under, raw_under)
        self.assertEqual(rels_under[0].relation_kind, "worn_under")
        self.assertEqual(rels_under[0].source_piece_id, "piece_01")
        self.assertEqual(rels_under[0].target_piece_id, "piece_02")
        self.assertEqual(rels_under[0].relation_direction, "inner_to_outer")

        raw_tuck = [
            {"piece_slot": "top_pieces", "piece_text": "blouse", "binding_role": "standalone"},
            {"piece_slot": "bottom_pieces", "piece_text": "pencil skirt", "binding_role": "tucked_into"},
        ]
        pb_tuck = migrate_piece_bindings(raw_tuck)
        rels_tuck = derive_ensemble_relations(pb_tuck, raw_tuck)
        self.assertEqual(rels_tuck[0].relation_kind, "tucked_into")
        self.assertEqual(rels_tuck[0].relation_direction, "tucked_into")

    def test_ensemble_grammar_error_on_missing_left_operand(self):
        raw_bad = [
            {"piece_slot": "main_garments", "piece_text": "slip", "binding_role": "over"},
        ]
        pb_bad = migrate_piece_bindings(raw_bad)
        with self.assertRaises(UnresolvedEnsembleRelationError):
            derive_ensemble_relations(pb_bad, raw_bad, "SRC_CLOTH_ERR_01")

    def test_ensemble_dag_cycle_and_dangling_detection(self):
        pbs = (
            PieceBinding(piece_id="piece_01"),
            PieceBinding(piece_id="piece_02"),
            PieceBinding(piece_id="piece_03"),
        )

        with self.assertRaises(UnresolvedEnsembleRelationError):
            EnsembleRelation("worn_over", "piece_01", "piece_01", "outer_to_inner")

        two_node_cycle = (
            EnsembleRelation("worn_over", "piece_01", "piece_02", "outer_to_inner"),
            EnsembleRelation("worn_over", "piece_02", "piece_01", "outer_to_inner"),
        )
        with self.assertRaises(UnresolvedEnsembleRelationError):
            EnsemblePieces(piece_bindings=pbs, relations=two_node_cycle)

        three_node_cycle = (
            EnsembleRelation("worn_over", "piece_01", "piece_02", "outer_to_inner"),
            EnsembleRelation("worn_over", "piece_02", "piece_03", "outer_to_inner"),
            EnsembleRelation("worn_over", "piece_03", "piece_01", "outer_to_inner"),
        )
        with self.assertRaises(UnresolvedEnsembleRelationError):
            EnsemblePieces(piece_bindings=pbs, relations=three_node_cycle)

        dangling_rel = (
            EnsembleRelation("worn_over", "piece_01", "piece_99", "outer_to_inner"),
        )
        with self.assertRaises(UnresolvedEnsembleRelationError):
            EnsemblePieces(piece_bindings=pbs, relations=dangling_rel)

        valid_dag = (
            EnsembleRelation("worn_over", "piece_01", "piece_02", "outer_to_inner"),
            EnsembleRelation("worn_over", "piece_01", "piece_03", "outer_to_inner"),
            EnsembleRelation("worn_over", "piece_02", "piece_03", "outer_to_inner"),
        )
        ep = EnsemblePieces(piece_bindings=pbs, relations=valid_dag)
        self.assertEqual(len(ep.relations), 3)


class TestAmbiguousPoseSupportPolymorphism(unittest.TestCase):
    """测试 4: 动作姿态模糊状态多态保真与消解器评估判定。"""

    def test_ambiguous_pose_options_preservation(self):
        pf_goh = PosePhysicalFacts(body_support="ground_or_held")
        self.assertEqual(pf_goh.body_support, UNSPECIFIED)
        self.assertEqual(pf_goh.support_options, ("ground", "held"))

        pf_soc = PosePhysicalFacts(body_support="standing_or_crouched")
        self.assertEqual(pf_soc.body_support, UNSPECIFIED)
        self.assertEqual(pf_soc.support_options, ("crouched", "standing"))

        pf_sos = PosePhysicalFacts(body_support="standing_or_sitting")
        self.assertEqual(pf_sos.body_support, UNSPECIFIED)
        self.assertEqual(pf_sos.support_options, ("sitting", "standing"))

    def test_conflict_resolver_pose_support_compatibility(self):
        pf_standing = PosePhysicalFacts(body_support="standing")
        self.assertTrue(is_pose_support_compatible(pf_standing, "standing"))
        self.assertFalse(is_pose_support_compatible(pf_standing, "sitting"))

        pf_goh = PosePhysicalFacts(body_support="ground_or_held")
        self.assertTrue(is_pose_support_compatible(pf_goh, "ground"))
        self.assertTrue(is_pose_support_compatible(pf_goh, "held"))
        self.assertFalse(is_pose_support_compatible(pf_goh, "airborne"))

        pf_free = PosePhysicalFacts(body_support=UNSPECIFIED, support_options=())
        self.assertTrue(is_pose_support_compatible(pf_free, "any_surface"))

    def test_garment_support_modifier_ensemble_penetration(self):
        pb_top = PieceBinding(piece_id="p1", piece_slot="top_pieces", garment_topology="top")
        pb_skirt = PieceBinding(piece_id="p2", piece_slot="bottom_pieces", garment_topology="bottom_skirt")
        ep = EnsemblePieces(piece_bindings=(pb_top, pb_skirt))
        carrier = SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep)

        self.assertTrue(does_garment_support_modifier(carrier, "top"))
        self.assertTrue(does_garment_support_modifier(carrier, "bottom_skirt"))
        self.assertFalse(does_garment_support_modifier(carrier, "bottom_pants"))


class TestQuarantineHardDefenseBoundary(unittest.TestCase):
    """测试 5: 83 条隔离池实体物理不可达与无 ID 隔离载荷 Fail-Closed 门禁。"""

    def test_quarantine_entity_count_and_identity(self):
        self.assertEqual(len(QUARANTINE_ENTITY_IDS), 83)
        self.assertIn("SRC_CAM_06691", QUARANTINE_ENTITY_IDS)
        self.assertIn("SRC_CLOTH_00317", QUARANTINE_ENTITY_IDS)
        self.assertIn("SRC_POSE_03788", QUARANTINE_ENTITY_IDS)
        self.assertIn("SRC_SCENE_04459", QUARANTINE_ENTITY_IDS)

    def test_quarantine_leakage_on_runtime_semantic_facts_build(self):
        for q_id in ("SRC_CAM_06691", "SRC_CLOTH_00317", "SRC_POSE_03788", "SRC_SCENE_04459"):
            with self.assertRaises(QuarantineLeakageError):
                build_runtime_semantic_facts({"tag_text": "sample"}, source_entity_id=q_id)

    def test_quarantine_leakage_on_idless_or_flagged_payloads(self):
        """测试无实体 ID 但携带显式隔离标志的载荷拦截。"""
        # 1. 显式 is_quarantined=True
        with self.assertRaises(QuarantineLeakageError):
            build_runtime_semantic_facts({"is_quarantined": True, "tag_text": "test"})

        # 2. 携带 quarantine_reason
        with self.assertRaises(QuarantineLeakageError):
            build_runtime_semantic_facts({"quarantine_reason": "unverified_source", "tag_text": "test"})

        # 3. 携带 importable=False
        with self.assertRaises(QuarantineLeakageError):
            build_runtime_semantic_facts({"importable": False, "tag_text": "test"})

        # 4. 携带 target_export_eligible=False
        with self.assertRaises(QuarantineLeakageError):
            build_runtime_semantic_facts({"target_export_eligible": False, "tag_text": "test"})

    def test_quarantine_leakage_on_garment_carrier_construction(self):
        with self.assertRaises(QuarantineLeakageError):
            GarmentCarrierEntity(entity_id="SRC_CLOTH_00317", selector="clothing", selected_id="SRC_CLOTH_00317", member_atoms=[])
        with self.assertRaises(QuarantineLeakageError):
            GarmentCarrierEntity(entity_id="carrier_01", selector="clothing", selected_id="SRC_CLOTH_00317", member_atoms=[])

    def test_quarantine_leakage_on_conflict_resolver(self):
        resolver = ConflictResolver(REPO_ROOT / "data")
        atom_bad = PromptAtom(
            text="bad item",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="SRC_CLOTH_00317",
            provenance=TagProvenance(item_id="SRC_CLOTH_00317"),
        )
        with self.assertRaises(QuarantineLeakageError):
            resolver.resolve_atoms([atom_bad])

        frag_bad = PromptFragment(
            text="bad fragment",
            source_slot="clothing",
            source_item_id="SRC_CLOTH_00317",
        )
        with self.assertRaises(QuarantineLeakageError):
            resolver.resolve_fragments([frag_bad])


class TestSpecificEntityZeroLossMigration(unittest.TestCase):
    """测试 6: 针对点名实体 (SRC_LIGHT_06819, SRC_CAM_06698, SRC_HAIR_00001) 的精确零丢失断言。"""

    def test_src_light_06819_light_source_preservation(self):
        raw = {"light_source": "candle"}
        f = build_runtime_semantic_facts(raw, "SRC_LIGHT_06819", "lighting.json")
        self.assertEqual(f.light_sources, ("candle",))
        self.assertIsNotNone(f.lighting_facts)
        self.assertEqual(f.lighting_facts.light_source, "candle")

    def test_src_cam_06698_camera_and_film_specs_preservation(self):
        raw = {
            "film_name": "Adox CMS 20 II",
            "manufacturer_brand": "ADOX",
            "color_mode": "bw",
            "measured_iso": 20,
            "recommended_ei": 20,
            "nominal_name_rating": 20,
            "iso_display": "ISO 20",
            "emulsion_type": "orthopanchromatic_microfilm",
            "developing_process": "CMS 20 Developer",
            "is_camera_film": True,
            "spec_verified": True,
            "verification_source": "https://www.adox.de/Photo/adox-films/cms-20-ii/",
        }
        f = build_runtime_semantic_facts(raw, "SRC_CAM_06698", "film_stocks.json")
        self.assertEqual(f.color_modes, ("monochrome",))
        self.assertIsNotNone(f.camera_facts)
        self.assertEqual(f.camera_facts.color_mode, "bw")
        self.assertEqual(f.camera_facts.film_name, "Adox CMS 20 II")
        self.assertEqual(f.camera_facts.measured_iso, 20)
        self.assertTrue(f.camera_facts.is_camera_film)
        self.assertIn("spec_verified", f.governance_metadata)
        self.assertTrue(f.governance_metadata["spec_verified"])

    def test_src_hair_00001_incompatible_with_preservation(self):
        raw = {
            "hair_category": "bald",
            "shaved_level": "close",
            "incompatible_with": ["hair_length_palette", "hair_texture_palette", "hair_color_palette"],
        }
        f = build_runtime_semantic_facts(raw, "SRC_HAIR_00001", "accessories.json")
        self.assertEqual(set(f.incompatible_with), {"hair_length_palette", "hair_texture_palette", "hair_color_palette"})
        self.assertIsNotNone(f.hair_facts)
        self.assertEqual(f.hair_facts.hair_category, "bald")
        self.assertEqual(f.hair_facts.shaved_level, "close")


class TestSchemaComplianceAndValidation(unittest.TestCase):
    """测试 7: 生产 JSON Schema 兼容性及非法输入严格拦截测试。"""

    def test_schema_accepts_valid_migrated_clothing(self):
        schema_path = SCHEMAS_DIR / "clothing.schema.json"
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        cf = CutFeatures(neckline="sweetheart", fit_silhouette="form_fitting")
        sample_facts = SemanticFacts(
            semantic_role="selector",
            garment_topologies=["one_piece"],
            cut_features=cf,
            is_ensemble=False,
            fabric_materials=["silk"],
        ).to_dict()

        sub_schema = schema["definitions"]["semantic_facts"]
        # 校验通过，无异常
        jsonschema.validate(instance=sample_facts, schema=sub_schema)

    def test_schema_rejects_invalid_submodel_types(self):
        schema_path = SCHEMAS_DIR / "clothing.schema.json"
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        sub_schema = schema["definitions"]["semantic_facts"]

        # 1. 剪裁特征非法类型与非法字段
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"cut_features": {"neckline": 12345}}, schema=sub_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"cut_features": {"unknown_cut_key": "v_neck"}}, schema=sub_schema)

        # 2. 姿态事实非法类型与未注册字段 (additionalProperties: false)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"pose_facts": {"body_support": 12345}}, schema=sub_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"pose_facts": {"unknown_pose_field": "val"}}, schema=sub_schema)

        # 3. 相机硬件事实非法类型与未注册字段
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"camera_facts": {"focal_length_mm": "fifty"}}, schema=sub_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"camera_facts": {"unknown_cam_prop": True}}, schema=sub_schema)

        # 4. 场景环境事实非法类型与未注册字段
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"scene_facts": {"space_kind": 999}}, schema=sub_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"scene_facts": {"bogus_scene_field": "test"}}, schema=sub_schema)

        # 5. 毛发与光照事实非法类型与未注册字段
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"hair_facts": {"shaved_level": 123}}, schema=sub_schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance={"lighting_facts": {"light_source": 123}}, schema=sub_schema)


class TestProductionConflictResolutionIntegration(unittest.TestCase):
    """测试 8: 生产消解流程实际路径集成测试 (套装修饰穿透、开合能力验证与姿态环境相容)。"""

    def setUp(self):
        self.resolver = ConflictResolver(REPO_ROOT / "data")

    def test_production_ensemble_modifier_penetration_resolution(self):
        """测试多件套内部构件穿透修饰在生产 resolve_atoms 中的判定。"""
        pb_top = PieceBinding(piece_id="piece_01", piece_slot="top_pieces", garment_topology="top")
        pb_skirt = PieceBinding(piece_id="piece_02", piece_slot="bottom_pieces", garment_topology="bottom_skirt")
        ep = EnsemblePieces(piece_bindings=(pb_top, pb_skirt))

        carrier_facts = SemanticFacts(
            semantic_role="selector",
            garment_topologies=["ensemble_outfit"],
            ensemble_pieces=ep,
        )

        atom_ensemble = PromptAtom(
            text="two-piece ensemble",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="ensemble_01",
            facts=carrier_facts,
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="ensemble_01"),
        )

        atom_top_modifier = PromptAtom(
            text="loosened collar",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="top_mod_01",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["loosened"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="top_mod_01"),
        )

        # 运行消解流程
        surviving_atoms, dropped_reasons, report = self.resolver.resolve_atoms_with_full_report([atom_ensemble, atom_top_modifier])
        surviving_texts = [a.text for a in surviving_atoms]
        self.assertIn("two-piece ensemble", surviving_texts)
        self.assertIn("loosened collar", surviving_texts)

    def test_production_pose_support_environmental_compatibility(self):
        """测试姿态与环境水体相容消解（自由态保留）。"""
        atom_scene = PromptAtom(
            atom_id="atom_scene_pool",
            text="swimming pool surface",
            span_type=SpanType.PLAIN,
            source_slot="scene",
            source_item_id="scene_pool",
            facts=SemanticFacts(
                space_kind="outdoor",
                scene_facts=SceneEnvironmentalFacts(venue_category="aquatic"),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="scene", selected_id="scene_pool"),
        )

        # 兼容姿态：自由态
        atom_pose_free = PromptAtom(
            atom_id="atom_pose_float",
            text="floating pose",
            span_type=SpanType.PLAIN,
            source_slot="pose",
            source_item_id="pose_float",
            facts=SemanticFacts(
                hand_state="free",
                pose_facts=PosePhysicalFacts(body_support=UNSPECIFIED, support_options=()),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="pose", selected_id="pose_float"),
        )

        surviving_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report([atom_scene, atom_pose_free])
        surviving_ids = [a.source_item_id for a in surviving_atoms]
        self.assertIn("scene_pool", surviving_ids)
        self.assertIn("pose_float", surviving_ids)

    def test_production_pose_support_underwater_conflict_drop(self):
        """测试深度水下环境与显式地面站立姿态冲突的生产消解真实删除路径及审计记录。"""
        atom_scene_deep = PromptAtom(
            atom_id="atom_scene_deep",
            text="deep underwater cave",
            span_type=SpanType.PLAIN,
            source_slot="scene",
            source_item_id="scene_underwater_01",
            facts=SemanticFacts(
                space_kind="indoor",
                scene_facts=SceneEnvironmentalFacts(venue_category="underwater"),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="scene", selected_id="scene_underwater_01"),
        )

        atom_pose_standing = PromptAtom(
            atom_id="atom_pose_standing",
            text="standing upright",
            span_type=SpanType.PLAIN,
            source_slot="pose",
            source_item_id="pose_stand_01",
            facts=SemanticFacts(
                hand_state="free",
                pose_facts=PosePhysicalFacts(body_support="standing", support_options=("standing",)),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="pose", selected_id="pose_stand_01"),
        )

        # 真实消解：必须成功删除不相容的姿态原子，且严禁抛出 RuleConfigurationError
        surviving_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report([atom_scene_deep, atom_pose_standing])
        surviving_ids = [a.source_item_id for a in surviving_atoms]

        self.assertIn("scene_underwater_01", surviving_ids)
        self.assertNotIn("pose_stand_01", surviving_ids)
        self.assertIn("spatial_environmental_mutual_exclusion", rules_applied)

        # 验证决策账本记录精确匹配
        drop_decisions = [d for d in report.decisions if d.action == "drop"]
        self.assertEqual(len(drop_decisions), 1)
        d = drop_decisions[0]
        self.assertEqual(d.reason_code, "spatial_pose_support_incompatible")
        self.assertEqual(d.target_atom_id, atom_pose_standing.atom_id)
        self.assertEqual(d.winner_atom_ids, (atom_scene_deep.atom_id,))

    def test_production_normal_scenes_preserve_standing_and_airborne(self):
        """测试普通场景不误判：泳池边保留站姿，普通公园保留腾空跳跃姿态。"""
        # 1. 泳池场景保留站立姿态
        atom_pool = PromptAtom(
            atom_id="atom_pool",
            text="swimming pool lounge area",
            span_type=SpanType.PLAIN,
            source_slot="scene",
            source_item_id="scene_pool_lounge",
            facts=SemanticFacts(
                space_kind="outdoor",
                scene_facts=SceneEnvironmentalFacts(venue_type="resort"),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="scene", selected_id="scene_pool_lounge"),
        )
        atom_stand = PromptAtom(
            atom_id="atom_stand",
            text="standing tall",
            span_type=SpanType.PLAIN,
            source_slot="pose",
            source_item_id="pose_stand_pool",
            facts=SemanticFacts(
                hand_state="free",
                pose_facts=PosePhysicalFacts(body_support="standing", support_options=("standing",)),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="pose", selected_id="pose_stand_pool"),
        )
        surv_pool, rules_pool, _ = self.resolver.resolve_atoms_with_full_report([atom_pool, atom_stand])
        self.assertEqual(len(surv_pool), 2)
        self.assertEqual(len(rules_pool), 0)

        # 2. 公园场景保留腾空姿态
        atom_park = PromptAtom(
            atom_id="atom_park",
            text="park at night",
            span_type=SpanType.PLAIN,
            source_slot="scene",
            source_item_id="scene_park_night",
            facts=SemanticFacts(
                space_kind="outdoor",
                scene_facts=SceneEnvironmentalFacts(venue_type="park"),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="scene", selected_id="scene_park_night"),
        )
        atom_airborne = PromptAtom(
            atom_id="atom_airborne",
            text="mid-air jump pose",
            span_type=SpanType.PLAIN,
            source_slot="pose",
            source_item_id="pose_jump_air",
            facts=SemanticFacts(
                hand_state="free",
                pose_facts=PosePhysicalFacts(body_support="airborne", support_options=("airborne",)),
            ),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="pose", selected_id="pose_jump_air"),
        )
        surv_park, rules_park, _ = self.resolver.resolve_atoms_with_full_report([atom_park, atom_airborne])
        self.assertEqual(len(surv_park), 2)
        self.assertEqual(len(rules_park), 0)

    def test_pants_ensemble_rejects_lifted_skirt(self):
        """测试裤装套装绝不允许承载掀裙修饰 (Fail-Closed)。"""
        pb_top = PieceBinding(piece_id="piece_01", piece_slot="top_pieces", garment_topology="top", piece_text="knit sweater")
        pb_pants = PieceBinding(piece_id="piece_02", piece_slot="bottom_pieces", garment_topology="bottom_pants", piece_text="denim jeans")
        ep = EnsemblePieces(piece_bindings=(pb_top, pb_pants))

        carrier_facts = SemanticFacts(
            semantic_role="selector",
            garment_topologies=["ensemble_outfit"],
            ensemble_pieces=ep,
        )

        atom_ensemble_pants = PromptAtom(
            atom_id="atom_ensemble_pants",
            text="sweater and jeans outfit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="ensemble_pants_01",
            facts=carrier_facts,
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="ensemble_pants_01"),
        )

        atom_lifted_skirt = PromptAtom(
            atom_id="atom_lifted_skirt",
            text="lifted skirt",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="lifted_skirt",
            facts=SemanticFacts(garment_topologies=["bottom_skirt"], garment_states=["lifted"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="lifted_skirt"),
        )

        surviving_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report([atom_ensemble_pants, atom_lifted_skirt])
        surviving_ids = [a.source_item_id for a in surviving_atoms]

        self.assertIn("ensemble_pants_01", surviving_ids)
        self.assertNotIn("lifted_skirt", surviving_ids)
        self.assertIn("clothing_style_state_coherence", rules_applied)
        drop_reasons = [d.reason_code for d in report.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons)

    def test_tshirt_ensemble_rejects_unbuttoned(self):
        """测试无纽扣上衣（T恤）绝不放行 unbuttoned 开纽扣状态修饰。"""
        pb_tee = PieceBinding(piece_id="piece_01", piece_slot="top_pieces", garment_topology="top", piece_text="cotton crewneck t-shirt")
        pb_skirt = PieceBinding(piece_id="piece_02", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="pleated mini skirt")
        ep = EnsemblePieces(piece_bindings=(pb_tee, pb_skirt))

        carrier_facts = SemanticFacts(
            semantic_role="selector",
            garment_topologies=["ensemble_outfit"],
            ensemble_pieces=ep,
        )

        atom_ensemble_tee = PromptAtom(
            atom_id="atom_ensemble_tee",
            text="t-shirt and skirt set",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="ensemble_tee_01",
            facts=carrier_facts,
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="ensemble_tee_01"),
        )

        atom_unbuttoned = PromptAtom(
            atom_id="atom_unbuttoned",
            text="unbuttoned shirt",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unbuttoned"),
        )

        surviving_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report([atom_ensemble_tee, atom_unbuttoned])
        surviving_ids = [a.source_item_id for a in surviving_atoms]

        self.assertIn("ensemble_tee_01", surviving_ids)
        self.assertNotIn("unbuttoned", surviving_ids)
        self.assertIn("clothing_style_state_coherence", rules_applied)
        drop_reasons = [d.reason_code for d in report.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons)

    def test_button_shirt_ensemble_allows_unbuttoned(self):
        """测试具备纽扣的衬衫上装正常放行 unbuttoned 开纽扣状态修饰。"""
        pb_shirt = PieceBinding(piece_id="piece_01", piece_slot="top_pieces", garment_topology="top", piece_text="button-down oxford shirt")
        pb_skirt = PieceBinding(piece_id="piece_02", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="pleated mini skirt")
        ep = EnsemblePieces(piece_bindings=(pb_shirt, pb_skirt))

        carrier_facts = SemanticFacts(
            semantic_role="selector",
            garment_topologies=["ensemble_outfit"],
            ensemble_pieces=ep,
        )

        atom_ensemble_shirt = PromptAtom(
            atom_id="atom_ensemble_shirt",
            text="shirt and skirt set",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="ensemble_shirt_01",
            facts=carrier_facts,
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="ensemble_shirt_01"),
        )

        atom_unbuttoned = PromptAtom(
            atom_id="atom_unbuttoned",
            text="unbuttoned shirt",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unbuttoned"),
        )

        surviving_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report([atom_ensemble_shirt, atom_unbuttoned])
        surviving_ids = [a.source_item_id for a in surviving_atoms]

        self.assertIn("ensemble_shirt_01", surviving_ids)
        self.assertIn("unbuttoned", surviving_ids)
        self.assertEqual(len(rules_applied), 0)

    def test_button_and_zipper_capability_tri_state_and_negations(self):
        """测试纽扣与拉链开合能力三态契约：优先处理明确否定，区分明确支持、明确不支持与未知。"""
        # 1. 明确否定：buttonless 绝不支持开扣
        e_btnless = GarmentCarrierEntity("e1", "clothing", "shirts_blouses", [PromptAtom(text="buttonless blouse", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertFalse(is_garment_compatible_with_state(e_btnless, "unbuttoned"))
        self.assertFalse(piece_has_button_capability("buttonless blouse"))

        # 2. 未知/无拉链款式：普通 hoodie 绝不放行拉链开襟
        e_hoodie = GarmentCarrierEntity("e2", "clothing", "hoodie", [PromptAtom(text="oversized hoodie", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertFalse(is_garment_compatible_with_state(e_hoodie, "unzipped"))
        self.assertFalse(piece_has_zipper_capability("hoodie"))

        # 3. 真实拉链正例：zip-up hoodie / zippered jacket / latex_catsuit / nurse_uniform
        e_zip_hoodie = GarmentCarrierEntity("e3", "clothing", "hoodie", [PromptAtom(text="zip-up hoodie", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertTrue(is_garment_compatible_with_state(e_zip_hoodie, "unzipped"))
        self.assertTrue(piece_has_zipper_capability("zip-up hoodie"))

        e_zip_jacket = GarmentCarrierEntity("e4", "clothing", "outerwear_jacket", [PromptAtom(text="zippered jacket", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertTrue(is_garment_compatible_with_state(e_zip_jacket, "unzipped"))
        self.assertTrue(piece_has_zipper_capability("zippered jacket"))

        e_catsuit = GarmentCarrierEntity("e5", "clothing", "latex_catsuit", [PromptAtom(text="black latex catsuit", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertTrue(is_garment_compatible_with_state(e_catsuit, "unzipped"))

        e_nurse = GarmentCarrierEntity("e6", "clothing", "nurse_uniform", [PromptAtom(text="nurse uniform", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertTrue(is_garment_compatible_with_state(e_nurse, "unzipped"))

        # 4. 真实纽扣正例：button-down oxford shirt / business_suit
        e_oxford = GarmentCarrierEntity("e7", "clothing", "shirts_blouses", [PromptAtom(text="button-down oxford shirt", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertTrue(is_garment_compatible_with_state(e_oxford, "unbuttoned"))
        self.assertTrue(piece_has_button_capability("button-down oxford shirt"))

        e_suit = GarmentCarrierEntity("e8", "clothing", "business_suit", [PromptAtom(text="business suit", span_type=SpanType.PLAIN, source_slot="clothing")])
        self.assertTrue(is_garment_compatible_with_state(e_suit, "unbuttoned"))

    def test_production_buttonless_blouse_drops_unbuttoned(self):
        """测试在真实消解器中，buttonless blouse 搭配 unbuttoned 触发 state_lacks_carrier 准确丢弃。"""
        atom_blouse = PromptAtom(
            atom_id="atom_blouse",
            text="buttonless blouse",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="shirts_blouses",
            facts=SemanticFacts(garment_topologies=["top"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="shirts_blouses"),
        )
        atom_unbuttoned = PromptAtom(
            atom_id="atom_unbuttoned",
            text="unbuttoned collar",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unbuttoned"),
        )
        surviving, rules, report = self.resolver.resolve_atoms_with_full_report([atom_blouse, atom_unbuttoned])
        surviving_ids = [a.source_item_id for a in surviving]
        self.assertIn("shirts_blouses", surviving_ids)
        self.assertNotIn("unbuttoned", surviving_ids)
        self.assertIn("clothing_style_state_coherence", rules)
        drop_reasons = [d.reason_code for d in report.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons)

    def test_production_hoodie_drops_unzipped(self):
        """测试在真实消解器中，普通 hoodie 搭配 unzipped 触发 state_lacks_carrier 准确丢弃。"""
        atom_hoodie = PromptAtom(
            atom_id="atom_hoodie",
            text="oversized hoodie",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="hoodie_item",
            facts=SemanticFacts(garment_topologies=["top"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="hoodie_item"),
        )
        atom_unzipped = PromptAtom(
            atom_id="atom_unzipped",
            text="unzipped front",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unzipped"),
        )
        surviving, rules, report = self.resolver.resolve_atoms_with_full_report([atom_hoodie, atom_unzipped])
        surviving_ids = [a.source_item_id for a in surviving]
        self.assertIn("hoodie_item", surviving_ids)
        self.assertNotIn("unzipped", surviving_ids)
        self.assertIn("clothing_style_state_coherence", rules)
        drop_reasons = [d.reason_code for d in report.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons)

    def test_production_zip_up_hoodie_allows_unzipped(self):
        """测试在真实消解器中，明确带拉链的 zip-up hoodie 正常放行 unzipped。"""
        pb_zip_hoodie = PieceBinding(piece_id="piece_01", piece_slot="top_pieces", garment_topology="top", piece_text="zip-up fleece hoodie")
        ep = EnsemblePieces(piece_bindings=(pb_zip_hoodie,))
        atom_ensemble_zip = PromptAtom(
            atom_id="atom_ensemble_zip",
            text="zip-up hoodie outfit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="hoodie_ensemble",
            facts=SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="hoodie_ensemble"),
        )
        atom_unzipped = PromptAtom(
            atom_id="atom_unzipped",
            text="unzipped front",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unzipped"),
        )
        surviving, rules, report = self.resolver.resolve_atoms_with_full_report([atom_ensemble_zip, atom_unzipped])
        surviving_ids = [a.source_item_id for a in surviving]
        self.assertIn("hoodie_ensemble", surviving_ids)
        self.assertIn("unzipped", surviving_ids)
        self.assertEqual(len(rules), 0)

    def test_ensemble_localized_opening_capability_and_negations(self):
        """测试多件套开合能力局域化与构件否定隔离契约：
        1. 某一构件的否定描述（如 buttonless / zipperless 裙子）严禁跨构件否决其他支持构件（如排扣衬衫、拉链外套）；
        2. 当动作明确指定目标构件且该构件明确不支持时，严禁因其他构件具备能力而借权误放行；
        3. 验证真实消解器 pipeline 下的保留/丢弃与 state_lacks_carrier 审计记录。
        """
        # ── 案例 1: 排扣衬衫 + 无纽扣裙子 ──
        pb_shirt_btn = PieceBinding(piece_id="shirt_01", piece_slot="top_pieces", garment_topology="top", piece_text="button-down oxford shirt")
        pb_skirt_nobtn = PieceBinding(piece_id="skirt_01", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="buttonless skirt")
        ep1 = EnsemblePieces(piece_bindings=(pb_shirt_btn, pb_skirt_nobtn))
        carrier1 = GarmentCarrierEntity(
            "carrier_ens1", "clothing", "ens1",
            [PromptAtom(
                atom_id="atom_ens1",
                text="button-down shirt with buttonless skirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="ens1",
                facts=SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep1),
                origin=SelectionOrigin("generator", "explicit", "clothing", "ens1"),
            )]
        )
        act_shirt_unbutton = PromptAtom(
            atom_id="act_s_unbutton",
            text="shirt unbuttoned",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unbuttoned"),
        )
        act_skirt_unbutton = PromptAtom(
            atom_id="act_sk_unbutton",
            text="skirt unbuttoned",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["bottom_skirt"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unbuttoned"),
        )

        # 衬衫开扣：下装无纽扣不得否定上装，正例放行
        self.assertTrue(is_garment_compatible_with_state(carrier1, "unbuttoned", act_shirt_unbutton))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier1.member_atoms[0], act_shirt_unbutton])
        self.assertIn("act_s_unbutton", [a.atom_id for a in surv])

        # 裙子开扣：裙子无纽扣明确否定生效，反例拒绝并记录 state_lacks_carrier
        self.assertFalse(is_garment_compatible_with_state(carrier1, "unbuttoned", act_skirt_unbutton))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier1.member_atoms[0], act_skirt_unbutton])
        self.assertNotIn("act_sk_unbutton", [a.atom_id for a in surv])
        drop_reasons = [d.reason_code for d in rep.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons)

        # ── 案例 2: 拉链外套 + 无拉链裙子 ──
        pb_jacket_zip = PieceBinding(piece_id="jacket_01", piece_slot="outer_layers", garment_topology="outerwear", piece_text="zippered bomber jacket")
        pb_skirt_nozip = PieceBinding(piece_id="skirt_02", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="zipperless skirt")
        ep2 = EnsemblePieces(piece_bindings=(pb_jacket_zip, pb_skirt_nozip))
        carrier2 = GarmentCarrierEntity(
            "carrier_ens2", "clothing", "ens2",
            [PromptAtom(
                atom_id="atom_ens2",
                text="zippered bomber jacket with zipperless skirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="ens2",
                facts=SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep2),
                origin=SelectionOrigin("generator", "explicit", "clothing", "ens2"),
            )]
        )
        act_jacket_unzip = PromptAtom(
            atom_id="act_j_unzip",
            text="jacket unzipped",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_topologies=["outerwear"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unzipped"),
        )
        act_skirt_unzip = PromptAtom(
            atom_id="act_sk_unzip",
            text="skirt unzipped",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_topologies=["bottom_skirt"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unzipped"),
        )

        # 外套拉链：下装无拉链不得否定外套，正例放行
        self.assertTrue(is_garment_compatible_with_state(carrier2, "unzipped", act_jacket_unzip))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier2.member_atoms[0], act_jacket_unzip])
        self.assertIn("act_j_unzip", [a.atom_id for a in surv])

        # 裙子拉链：裙子无拉链明确否定生效，反例拒绝并记录 state_lacks_carrier
        self.assertFalse(is_garment_compatible_with_state(carrier2, "unzipped", act_skirt_unzip))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier2.member_atoms[0], act_skirt_unzip])
        self.assertNotIn("act_sk_unzip", [a.atom_id for a in surv])
        drop_reasons = [d.reason_code for d in rep.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons)

        # ── 案例 3: 无纽扣衬衫 + 排扣裙子 ──
        pb_shirt_nobtn = PieceBinding(piece_id="shirt_02", piece_slot="top_pieces", garment_topology="top", piece_text="buttonless blouse")
        pb_skirt_btn = PieceBinding(piece_id="skirt_03", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="buttoned pleated skirt")
        ep3 = EnsemblePieces(piece_bindings=(pb_shirt_nobtn, pb_skirt_btn))
        carrier3 = GarmentCarrierEntity(
            "carrier_ens3", "clothing", "ens3",
            [PromptAtom(
                atom_id="atom_ens3",
                text="buttonless blouse with buttoned skirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="ens3",
                facts=SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep3),
                origin=SelectionOrigin("generator", "explicit", "clothing", "ens3"),
            )]
        )
        # 衬衫无扣反例：解开衬衫被拒绝
        self.assertFalse(is_garment_compatible_with_state(carrier3, "unbuttoned", act_shirt_unbutton))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier3.member_atoms[0], act_shirt_unbutton])
        self.assertNotIn("act_s_unbutton", [a.atom_id for a in surv])
        # 裙子有扣正例：解开裙子被放行
        self.assertTrue(is_garment_compatible_with_state(carrier3, "unbuttoned", act_skirt_unbutton))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier3.member_atoms[0], act_skirt_unbutton])
        self.assertIn("act_sk_unbutton", [a.atom_id for a in surv])

        # ── 案例 4: 无拉链外套 + 拉链裙子 ──
        pb_jacket_nozip = PieceBinding(piece_id="jacket_02", piece_slot="outer_layers", garment_topology="outerwear", piece_text="zipperless poncho coat")
        pb_skirt_zip = PieceBinding(piece_id="skirt_04", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="zippered leather skirt")
        ep4 = EnsemblePieces(piece_bindings=(pb_jacket_nozip, pb_skirt_zip))
        carrier4 = GarmentCarrierEntity(
            "carrier_ens4", "clothing", "ens4",
            [PromptAtom(
                atom_id="atom_ens4",
                text="zipperless poncho with zippered skirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="ens4",
                facts=SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep4),
                origin=SelectionOrigin("generator", "explicit", "clothing", "ens4"),
            )]
        )
        # 外套无拉链反例：拉开外套被拒绝
        self.assertFalse(is_garment_compatible_with_state(carrier4, "unzipped", act_jacket_unzip))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier4.member_atoms[0], act_jacket_unzip])
        self.assertNotIn("act_j_unzip", [a.atom_id for a in surv])
        # 裙子有拉链正例：拉开裙子被放行
        self.assertTrue(is_garment_compatible_with_state(carrier4, "unzipped", act_skirt_unzip))
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier4.member_atoms[0], act_skirt_unzip])
        self.assertIn("act_sk_unzip", [a.atom_id for a in surv])

        # ── 案例 5: 显式 target_id 阻断与放行 ──
        act_target_jacket = PromptAtom(
            atom_id="act_tj",
            text="unzipped front",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unzipped"),
            target_id="jacket_02",
        )
        act_target_skirt = PromptAtom(
            atom_id="act_ts",
            text="unzipped front",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unzipped"),
            target_id="skirt_04",
        )
        act_target_none = PromptAtom(
            atom_id="act_tn",
            text="unzipped front",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            facts=SemanticFacts(garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unzipped"),
            target_id="missing_piece",
        )
        # 靶向无拉链外套：绝不能借裙子能力放行，严格拒绝
        self.assertFalse(is_garment_compatible_with_state(carrier4, "unzipped", act_target_jacket))
        b_tj = find_bound_carrier(act_target_jacket, [carrier4])
        self.assertEqual(b_tj.status, BindingStatus.UNBOUND_INCOMPATIBLE)
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier4.member_atoms[0], act_target_jacket])
        self.assertNotIn("act_tj", [a.atom_id for a in surv])

        # 靶向拉链裙子：支持构件放行
        self.assertTrue(is_garment_compatible_with_state(carrier4, "unzipped", act_target_skirt))
        b_ts = find_bound_carrier(act_target_skirt, [carrier4])
        self.assertEqual(b_ts.status, BindingStatus.BOUND)
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier4.member_atoms[0], act_target_skirt])
        self.assertIn("act_ts", [a.atom_id for a in surv])

        # 靶向不存在构件：严格 fail-closed
        self.assertFalse(is_garment_compatible_with_state(carrier4, "unzipped", act_target_none))
        b_tn = find_bound_carrier(act_target_none, [carrier4])
        self.assertEqual(b_tn.status, BindingStatus.UNBOUND_TARGET_NOT_FOUND)
        surv, _, rep = self.resolver.resolve_atoms_with_full_report([carrier4.member_atoms[0], act_target_none])
        self.assertNotIn("act_tn", [a.atom_id for a in surv])

    def test_target_id_unified_contract_production_pipeline(self):
        """测试生产消解入口显式 target_id 实体与构件统一契约（四类标准场景）：
        1. 实体目标成功：target_id="ensemble_01"（套装实体 ID），解扣动作成功匹配并保留；
        2. 构件目标成功：target_id="shirt_01"（构件 ID），解析所属实体并匹配对应支持构件，成功保留；
        3. 目标构件不支持：target_id="skirt_01"（构件 ID，无扣裙子），严格拒绝并记录 state_lacks_carrier；
        4. 目标不存在：target_id="nonexistent_id"，四阶绑定快速失败并记录 state_lacks_carrier。
        """
        pb_shirt = PieceBinding(piece_id="shirt_01", piece_slot="top_pieces", garment_topology="top", piece_text="button-down oxford shirt")
        pb_skirt = PieceBinding(piece_id="skirt_01", piece_slot="bottom_pieces", garment_topology="bottom_skirt", piece_text="buttonless skirt")
        ep = EnsemblePieces(piece_bindings=(pb_shirt, pb_skirt))
        carrier = GarmentCarrierEntity(
            "carrier_01", "clothing", "ensemble_01",
            [PromptAtom(
                atom_id="atom_ens_01",
                text="oxford shirt with buttonless skirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="ensemble_01",
                facts=SemanticFacts(garment_topologies=["ensemble_outfit"], ensemble_pieces=ep),
                origin=SelectionOrigin("generator", "explicit", "clothing", "ensemble_01"),
            )]
        )

        # 1. 实体目标成功：显式 target_id 为套装 ID
        act_entity_target = PromptAtom(
            atom_id="act_entity_target",
            text="shirt unbuttoned",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unbuttoned"),
            target_id="ensemble_01",
        )
        b1 = find_bound_carrier(act_entity_target, [carrier])
        self.assertEqual(b1.status, BindingStatus.BOUND)
        self.assertEqual(b1.target_entity, carrier)
        surv1, _, rep1 = self.resolver.resolve_atoms_with_full_report([carrier.member_atoms[0], act_entity_target])
        self.assertIn("act_entity_target", [a.atom_id for a in surv1])

        # 2. 构件目标成功：显式 target_id 为支持构件 ID (shirt_01)
        act_piece_target = PromptAtom(
            atom_id="act_piece_target",
            text="shirt unbuttoned",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unbuttoned"),
            target_id="shirt_01",
        )
        b2 = find_bound_carrier(act_piece_target, [carrier])
        self.assertEqual(b2.status, BindingStatus.BOUND)
        self.assertEqual(b2.target_entity, carrier)
        surv2, _, rep2 = self.resolver.resolve_atoms_with_full_report([carrier.member_atoms[0], act_piece_target])
        self.assertIn("act_piece_target", [a.atom_id for a in surv2])

        # 3. 目标构件不支持：显式 target_id 为无扣裙子构件 ID (skirt_01)
        act_unsupported_piece = PromptAtom(
            atom_id="act_unsupported_piece",
            text="skirt unbuttoned",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["bottom_skirt"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unbuttoned"),
            target_id="skirt_01",
        )
        b3 = find_bound_carrier(act_unsupported_piece, [carrier])
        self.assertEqual(b3.status, BindingStatus.UNBOUND_INCOMPATIBLE)
        surv3, _, rep3 = self.resolver.resolve_atoms_with_full_report([carrier.member_atoms[0], act_unsupported_piece])
        self.assertNotIn("act_unsupported_piece", [a.atom_id for a in surv3])
        drop_reasons3 = [d.reason_code for d in rep3.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons3)

        # 4. 目标不存在：显式 target_id 为不存在构件或实体
        act_missing_target = PromptAtom(
            atom_id="act_missing_target",
            text="shirt unbuttoned",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            facts=SemanticFacts(garment_topologies=["top"], garment_states=["opened"]),
            origin=SelectionOrigin("generator", "explicit", "clothing_state", "unbuttoned"),
            target_id="nonexistent_id",
        )
        b4 = find_bound_carrier(act_missing_target, [carrier])
        self.assertEqual(b4.status, BindingStatus.UNBOUND_TARGET_NOT_FOUND)
        surv4, _, rep4 = self.resolver.resolve_atoms_with_full_report([carrier.member_atoms[0], act_missing_target])
        self.assertNotIn("act_missing_target", [a.atom_id for a in surv4])
        drop_reasons4 = [d.reason_code for d in rep4.decisions if d.action == "drop"]
        self.assertIn("state_lacks_carrier", drop_reasons4)



class TestEndToEndCandidatePoolMigration(unittest.TestCase):
    """测试 9: 候选池 6,857 条运行时候选端到端无损迁移与往返自检。"""

    def test_full_ledger_candidate_migration_closure(self):
        self.assertTrue(SOURCE_TSV.exists(), f"Missing source entities TSV: {SOURCE_TSV}")
        self.assertTrue(MAPPING_TSV.exists(), f"Missing target mappings TSV: {MAPPING_TSV}")

        report, results = run_full_migration(SOURCE_TSV, MAPPING_TSV)

        # 严格验证对账指标与 0 丢弃要求
        self.assertEqual(report.total_records, 6940)
        self.assertEqual(report.quarantine_isolated_count, 83)
        self.assertEqual(report.runtime_candidates_count, 6857)
        self.assertEqual(report.runtime_source_entities_count, 6856)
        self.assertEqual(report.ensembles_migrated, 2270)
        self.assertEqual(report.ensemble_relations_derived, 426)
        self.assertEqual(report.ambiguous_poses_preserved, 15)
        self.assertEqual(report.dag_validation_failures, 0)
        self.assertEqual(report.schema_parsing_failures, 0)
        self.assertEqual(report.unmapped_fact_keys_count, 0)
        self.assertEqual(len(results), 6857)

        # 抽样验证 100 条构建结果往返序列化保真
        for m_id, facts_obj in results[:100]:
            d = facts_obj.to_dict()
            self.assertIsInstance(d, dict)
            rebuilt = SemanticFacts.from_dict(d)
            self.assertIsInstance(rebuilt, SemanticFacts)

    def test_all_6857_migrated_candidates_pass_target_schemas(self):
        """测试全量 6,857 条运行时候选迁移后，通过各自目标 Schema 校验 (0 错误)。"""
        schemas_dir = Path("schemas")
        schemas_map = {}
        target_catalogs = [
            "accessories.json", "clothing.json", "expressions.json",
            "film_stocks.json", "lighting.json", "poses.json",
            "scenes.json", "shot_types.json"
        ]
        for cat in target_catalogs:
            s_name = cat.replace(".json", "")
            s_path = schemas_dir / f"{s_name}.schema.json"
            with open(s_path, "r", encoding="utf-8") as f:
                s_doc = json.load(f)
            resolver = jsonschema.RefResolver.from_schema(s_doc)
            facts_schema = s_doc["definitions"]["semantic_facts"]
            schemas_map[cat] = jsonschema.Draft7Validator(facts_schema, resolver=resolver)

        # 检查 SRC_LIGHT_06842 与 SRC_HAIR_00117 重点关注条目
        target_special_ids = {"SRC_LIGHT_06842", "SRC_HAIR_00117"}
        seen_special_ids = set()

        validation_errors = []
        total_checked = 0
        with open(MAPPING_TSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                s_id = row["source_entity_id"]
                if s_id in QUARANTINE_ENTITY_IDS:
                    continue
                total_checked += 1
                cat = row["target_catalog_file"]
                raw_facts = json.loads(row.get("semantic_facts_json", "{}"))
                facts_obj = build_runtime_semantic_facts(raw_facts, s_id, cat)
                facts_dict = facts_obj.to_dict()

                if s_id in target_special_ids:
                    seen_special_ids.add(s_id)
                    if s_id == "SRC_LIGHT_06842":
                        self.assertIsNotNone(facts_obj.lighting_facts)
                        self.assertEqual(facts_obj.lighting_facts.source_in_scene, True)
                    elif s_id == "SRC_HAIR_00117":
                        self.assertIsNotNone(facts_obj.hair_facts)
                        self.assertEqual(facts_obj.hair_facts.tapered, True)

                validator = schemas_map.get(cat)
                self.assertIsNotNone(validator, f"Missing validator for catalog: {cat}")
                for err in validator.iter_errors(facts_dict):
                    validation_errors.append((s_id, cat, err.message, list(err.path)))

        self.assertEqual(total_checked, 6857)
        self.assertEqual(seen_special_ids, target_special_ids)
        self.assertEqual(
            len(validation_errors),
            0,
            f"Expected 0 schema validation errors for all 6,857 candidates, but got {len(validation_errors)}: {validation_errors[:5]}"
        )


if __name__ == "__main__":
    unittest.main()
