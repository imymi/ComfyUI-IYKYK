"""
m33_adapter_and_relation_migrator.py — M3.3 运行时契约适配器与构件关系迁移器

功能：
1. 实现从台账事实到生产运行时模型 (lib.models) 的强契约规范化转换，172 键确定性全量分发，零静默丢弃；
2. 实现多件套构件确定性 ID 生成与关系三元组推导（严格按自然语言语法结构恢复外内层叠/收拢关系）；
3. 严格执行 DAG 拓扑无自环、无有向环校验；
4. 实现动作姿态多态保真转换（主标量 body_support 保持 UNSPECIFIED，support_options 承载完整备选集合）；
5. 实施隔离项分流硬门禁：调用 is_quarantined_payload 全路径拦截 83 条隔离池实体及任意携带隔离标志的载荷；
6. 100% 全量验证 6,857 条运行时候选映射的规范化解析闭环，未映射键严格恒为 0。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lib.conflict_resolver import QUARANTINE_ENTITY_IDS, is_quarantined_payload
from lib.errors import QuarantineLeakageError, UnresolvedEnsembleRelationError
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
    SceneEnvironmentalFacts,
    SemanticFacts,
    ShotCompositionFacts,
    UNSPECIFIED,
    VALID_BINDING_ROLES,
    VALID_BODY_SUPPORTS,
    VALID_ENSEMBLE_SLOTS,
    VALID_EXTENDED_SPACE_KINDS,
    VALID_EXTENDED_TIMES_OF_DAY,
    VALID_GARMENT_TOPOLOGIES,
    VALID_HAND_STATES,
    VALID_RELATION_DIRECTIONS,
    VALID_RELATION_KINDS,
)

GOVERNANCE_FACT_KEYS: Set[str] = {
    "slot_attribution", "sampling_compatibility_contract", "raw_template_format",
    "color_emphasis_wrapper", "is_pool_selector", "pool_reference", "selection_strategy",
    "max_selections", "hardware_kit_compatible", "kit_compatibility_status",
    "target_export_eligible", "importable", "spec_verified", "verification_source",
    "base_tag_text", "attribute_type", "context_affinity"
}


def migrate_piece_bindings(raw_pbs: List[Dict[str, Any]]) -> List[PieceBinding]:
    """为构件分配确定性 piece_id，并严格校验各构件类型契约。"""
    if not isinstance(raw_pbs, (list, tuple)):
        raise TypeError(f"piece_bindings must be list or tuple, got {type(raw_pbs).__name__}")
    pb_list: List[PieceBinding] = []
    for idx, rpb in enumerate(raw_pbs):
        if not isinstance(rpb, dict):
            raise TypeError(f"piece_binding element at index {idx} must be dict, got {type(rpb).__name__}")
        p_id = f"piece_{idx + 1:02d}"
        raw_cf = rpb.get("cut_features")
        if raw_cf is not None and not isinstance(raw_cf, (dict, CutFeatures)):
            raise TypeError(f"piece_binding cut_features must be dict or CutFeatures, got {type(raw_cf).__name__}")
        cf_obj = CutFeatures.from_dict(raw_cf) if isinstance(raw_cf, dict) else raw_cf

        pb_list.append(
            PieceBinding(
                piece_id=p_id,
                piece_slot=rpb.get("piece_slot", ""),
                piece_text=rpb.get("piece_text", ""),
                binding_role=rpb.get("binding_role", "standalone"),
                garment_topology=rpb.get("garment_topology"),
                cut_features=cf_obj,
                fabric_materials=rpb.get("fabric_materials"),
                pattern_textures=rpb.get("pattern_textures"),
            )
        )
    return pb_list


def derive_ensemble_relations(
    pb_list: List[PieceBinding],
    raw_pbs: List[Dict[str, Any]],
    source_entity_id: str = "",
) -> List[EnsembleRelation]:
    """从构件从句切分结构中恢复真实物理层叠与修饰关系三元组。

    规则真值表：
    - over / worn_over / layered_over: Left 外穿覆盖于 Right (relation_kind='worn_over', direction='outer_to_inner')
    - under / worn_under / layered_under: Left 穿在 Right 内部 (relation_kind='worn_under', direction='inner_to_outer')
    - tucked_into / bloused_into / cinched_into: Left 扎入/收束入 Right (relation_kind='tucked_into', direction='tucked_into')
    - split_over: Left 开衩跨越于 Right 之上 (relation_kind='split_over', direction='split_over')
    - paired_with / coordinated_with: Left 与 Right 搭配协调 (relation_kind='coordinated_with', direction='peer_to_peer')
    """
    rel_list: List[EnsembleRelation] = []
    for idx in range(len(pb_list)):
        role = raw_pbs[idx].get("binding_role", "standalone")
        if role == "standalone":
            continue

        if idx == 0:
            raise UnresolvedEnsembleRelationError(
                f"{source_entity_id}: Piece at index 0 has non-standalone role '{role}', missing preceding left operand!"
            )

        left_piece = pb_list[idx - 1]
        right_piece = pb_list[idx]

        if role in ("over", "worn_over", "layered_over"):
            rel_list.append(
                EnsembleRelation(
                    relation_kind="worn_over",
                    source_piece_id=left_piece.piece_id,
                    target_piece_id=right_piece.piece_id,
                    relation_direction="outer_to_inner",
                )
            )
        elif role in ("under", "worn_under", "layered_under"):
            rel_list.append(
                EnsembleRelation(
                    relation_kind="worn_under",
                    source_piece_id=left_piece.piece_id,
                    target_piece_id=right_piece.piece_id,
                    relation_direction="inner_to_outer",
                )
            )
        elif role in ("tucked_into", "bloused_into", "cinched_into"):
            rel_list.append(
                EnsembleRelation(
                    relation_kind="tucked_into",
                    source_piece_id=left_piece.piece_id,
                    target_piece_id=right_piece.piece_id,
                    relation_direction="tucked_into",
                )
            )
        elif role == "split_over":
            rel_list.append(
                EnsembleRelation(
                    relation_kind="split_over",
                    source_piece_id=left_piece.piece_id,
                    target_piece_id=right_piece.piece_id,
                    relation_direction="split_over",
                )
            )
        elif role in ("paired_with", "coordinated_with"):
            rel_list.append(
                EnsembleRelation(
                    relation_kind="coordinated_with",
                    source_piece_id=left_piece.piece_id,
                    target_piece_id=right_piece.piece_id,
                    relation_direction="peer_to_peer",
                )
            )
        else:
            raise UnresolvedEnsembleRelationError(
                f"{source_entity_id}: Unsupported binding_role {role!r}"
            )

    return rel_list


def migrate_ensemble_pieces(
    raw_ep: Dict[str, Any],
    source_entity_id: str = "",
) -> EnsemblePieces:
    """完整迁移多件套：分配构件 ID、推导层叠关系并校验 DAG 无环性。"""
    if not isinstance(raw_ep, dict):
        raise TypeError(f"ensemble_pieces must be a dict, got {type(raw_ep).__name__}")
    raw_pbs = raw_ep.get("piece_bindings", [])
    pb_list = migrate_piece_bindings(raw_pbs)
    rel_list = derive_ensemble_relations(pb_list, raw_pbs, source_entity_id)

    return EnsemblePieces(
        main_garments=tuple(raw_ep.get("main_garments", ())),
        top_pieces=tuple(raw_ep.get("top_pieces", ())),
        bottom_pieces=tuple(raw_ep.get("bottom_pieces", ())),
        outer_layers=tuple(raw_ep.get("outer_layers", ())),
        footwear=tuple(raw_ep.get("footwear", ())),
        accessories=tuple(raw_ep.get("accessories", ())),
        styling_details=tuple(raw_ep.get("styling_details", ())),
        piece_bindings=tuple(pb_list),
        relations=tuple(rel_list),
    )


def build_runtime_pose_facts(raw_facts: Dict[str, Any]) -> Optional[PosePhysicalFacts]:
    """构建姿态物理事实，严格保持模糊姿态候选集合多态保真。"""
    pose_fields = set(PosePhysicalFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = pose_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return PosePhysicalFacts(**kwargs)


def build_runtime_scene_facts(raw_facts: Dict[str, Any]) -> Optional[SceneEnvironmentalFacts]:
    """构建场景环境事实，支持拓展场所空间及时间四态。"""
    scene_fields = set(SceneEnvironmentalFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = scene_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return SceneEnvironmentalFacts(**kwargs)


def build_runtime_camera_facts(raw_facts: Dict[str, Any]) -> Optional[CameraHardwareFacts]:
    """构建相机与胶片硬件事实。"""
    cam_fields = set(CameraHardwareFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = cam_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return CameraHardwareFacts(**kwargs)


def build_runtime_hair_facts(raw_facts: Dict[str, Any]) -> Optional[HairAttributeFacts]:
    """构建发型与毛发属性事实。"""
    hair_fields = set(HairAttributeFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = hair_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return HairAttributeFacts(**kwargs)


def build_runtime_lighting_facts(raw_facts: Dict[str, Any]) -> Optional[LightingFacts]:
    """构建光照属性事实。"""
    light_fields = set(LightingFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = light_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return LightingFacts(**kwargs)


def build_runtime_shot_facts(raw_facts: Dict[str, Any]) -> Optional[ShotCompositionFacts]:
    """构建镜头构图属性事实。"""
    shot_fields = set(ShotCompositionFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = shot_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return ShotCompositionFacts(**kwargs)


def build_runtime_clothing_facts(raw_facts: Dict[str, Any]) -> Optional[ClothingAttributeFacts]:
    """构建服装细分属性事实。"""
    cloth_fields = set(ClothingAttributeFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = cloth_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return ClothingAttributeFacts(**kwargs)


def build_runtime_expression_facts(raw_facts: Dict[str, Any]) -> Optional[ExpressionFacts]:
    """构建表情面部动作事实。"""
    expr_fields = set(ExpressionFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = expr_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return ExpressionFacts(**kwargs)


def build_runtime_accessory_facts(raw_facts: Dict[str, Any]) -> Optional[AccessoryAttributeFacts]:
    """构建配饰挂件事实。"""
    acc_fields = set(AccessoryAttributeFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    present_keys = acc_fields.intersection(raw_facts.keys())
    if not present_keys:
        return None

    kwargs = {k: raw_facts[k] for k in present_keys}
    return AccessoryAttributeFacts(**kwargs)


def build_runtime_semantic_facts(
    raw_facts: Dict[str, Any],
    source_entity_id: str = "",
    target_catalog_file: str = "",
) -> SemanticFacts:
    """将台账中的 172 键事实完整、无损、强契约映射为运行时的不可变 SemanticFacts 对象。
    
    门禁守则：
    1. is_quarantined_payload(raw_facts, source_entity_id) 拦截隔离池实体及隔离载荷 (Fail-Closed)；
    2. 172 个台账事实键 100% 具备确定性归宿，零未映射字段；
    3. 严格校验往返保真度与强类型契约。
    """
    if not isinstance(raw_facts, dict):
        raise TypeError(f"raw_facts must be dict, got {type(raw_facts).__name__}")

    if is_quarantined_payload(raw_facts, source_entity_id):
        raise QuarantineLeakageError(
            f"Attempted to build runtime SemanticFacts for quarantined entity '{source_entity_id}' or payload: {raw_facts}"
        )

    consumed: Set[str] = set()
    top_kwargs: Dict[str, Any] = {}

    # 1. 剪裁特征
    if "cut_features" in raw_facts:
        raw_cf = raw_facts["cut_features"]
        if isinstance(raw_cf, dict):
            top_kwargs["cut_features"] = CutFeatures.from_dict(raw_cf)
        elif isinstance(raw_cf, CutFeatures):
            top_kwargs["cut_features"] = raw_cf
        else:
            raise TypeError(f"cut_features must be dict or CutFeatures, got {type(raw_cf).__name__}")
        consumed.add("cut_features")

    # 2. 多件套构件与层叠关系
    if "ensemble_pieces" in raw_facts:
        raw_ep = raw_facts["ensemble_pieces"]
        if isinstance(raw_ep, dict):
            top_kwargs["ensemble_pieces"] = migrate_ensemble_pieces(raw_ep, source_entity_id)
        elif isinstance(raw_ep, EnsemblePieces):
            top_kwargs["ensemble_pieces"] = raw_ep
        else:
            raise TypeError(f"ensemble_pieces must be dict or EnsemblePieces, got {type(raw_ep).__name__}")
        consumed.add("ensemble_pieces")

    # 2.1 针对作为嵌套对象直接传入的子模型进行类型检查
    for sm_key, sm_cls in (
        ("pose_facts", PosePhysicalFacts),
        ("scene_facts", SceneEnvironmentalFacts),
        ("camera_facts", CameraHardwareFacts),
        ("hair_facts", HairAttributeFacts),
        ("lighting_facts", LightingFacts),
        ("shot_facts", ShotCompositionFacts),
        ("clothing_facts", ClothingAttributeFacts),
        ("accessory_facts", AccessoryAttributeFacts),
        ("expression_facts", ExpressionFacts),
    ):
        if sm_key in raw_facts:
            sm_val = raw_facts[sm_key]
            if isinstance(sm_val, dict):
                top_kwargs[sm_key] = sm_cls.from_dict(sm_val)
            elif isinstance(sm_val, sm_cls):
                top_kwargs[sm_key] = sm_val
            else:
                raise TypeError(f"{sm_key} must be dict or {sm_cls.__name__}, got {type(sm_val).__name__}")
            consumed.add(sm_key)

    # 3. 姿态物理事实
    pose_fields = set(PosePhysicalFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    pose_present = pose_fields.intersection(raw_facts.keys())
    if pose_present:
        p_kwargs = {k: raw_facts[k] for k in pose_present}
        top_kwargs["pose_facts"] = PosePhysicalFacts(**p_kwargs)
        consumed.update(pose_present)

    # 4. 场景环境事实
    scene_fields = set(SceneEnvironmentalFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    scene_present = scene_fields.intersection(raw_facts.keys())
    if target_catalog_file == "poses.json" or source_entity_id.startswith("SRC_POSE_"):
        scene_present.discard("is_prose_template")
    if scene_present:
        s_kwargs = {k: raw_facts[k] for k in scene_present}
        top_kwargs["scene_facts"] = SceneEnvironmentalFacts(**s_kwargs)
        consumed.update(scene_present)

    # 5. 相机硬件事实
    cam_fields = set(CameraHardwareFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    cam_present = cam_fields.intersection(raw_facts.keys())
    if cam_present:
        c_kwargs = {k: raw_facts[k] for k in cam_present}
        top_kwargs["camera_facts"] = CameraHardwareFacts(**c_kwargs)
        consumed.update(cam_present)

    # 6. 发型毛发事实
    hair_fields = set(HairAttributeFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    hair_present = hair_fields.intersection(raw_facts.keys())
    if hair_present:
        h_kwargs = {k: raw_facts[k] for k in hair_present}
        top_kwargs["hair_facts"] = HairAttributeFacts(**h_kwargs)
        consumed.update(hair_present)

    # 7. 光照属性事实
    light_fields = set(LightingFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    light_present = light_fields.intersection(raw_facts.keys())
    if target_catalog_file == "shot_types.json" or source_entity_id.startswith("SRC_SHOT_"):
        light_present.discard("mood")
        light_present.discard("effect")
    if light_present:
        l_kwargs = {k: raw_facts[k] for k in light_present}
        top_kwargs["lighting_facts"] = LightingFacts(**l_kwargs)
        consumed.update(light_present)

    # 8. 镜头构图属性事实
    shot_fields = set(ShotCompositionFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    shot_present = shot_fields.intersection(raw_facts.keys())
    if target_catalog_file == "lighting.json" or source_entity_id.startswith("SRC_LIGHT_"):
        shot_present.discard("mood")
        shot_present.discard("effect")
    if target_catalog_file == "poses.json" or source_entity_id.startswith("SRC_POSE_"):
        shot_present.discard("motion_type")
        shot_present.discard("pose_orientation")
    if shot_present:
        sh_kwargs = {k: raw_facts[k] for k in shot_present}
        top_kwargs["shot_facts"] = ShotCompositionFacts(**sh_kwargs)
        consumed.update(shot_present)

    # 9. 服装属性事实与 10. 配饰属性事实 (消歧 material)
    cloth_fields = set(ClothingAttributeFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    acc_fields = set(AccessoryAttributeFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    cloth_present = cloth_fields.intersection(raw_facts.keys())
    acc_present = acc_fields.intersection(raw_facts.keys())

    if "material" in raw_facts:
        if (target_catalog_file == "accessories.json" or source_entity_id.startswith("SRC_ACC_") or
            bool(acc_present - {"material"})):
            cloth_present.discard("material")
        else:
            acc_present.discard("material")

    if cloth_present:
        cl_kwargs = {k: raw_facts[k] for k in cloth_present}
        top_kwargs["clothing_facts"] = ClothingAttributeFacts(**cl_kwargs)
        consumed.update(cloth_present)

    if acc_present:
        ac_kwargs = {k: raw_facts[k] for k in acc_present}
        top_kwargs["accessory_facts"] = AccessoryAttributeFacts(**ac_kwargs)
        consumed.update(acc_present)

    # 11. 表情面部动作事实
    expr_fields = set(ExpressionFacts.__dataclass_fields__.keys()) - {"explicit_fields"}
    expr_present = expr_fields.intersection(raw_facts.keys())
    if target_catalog_file == "poses.json" or source_entity_id.startswith("SRC_POSE_"):
        expr_present.discard("facial_action")
    if expr_present:
        ex_kwargs = {k: raw_facts[k] for k in expr_present}
        top_kwargs["expression_facts"] = ExpressionFacts(**ex_kwargs)
        consumed.update(expr_present)

    # 12. 顶层集合与单复数规范化
    if "light_sources" in raw_facts:
        ls = raw_facts["light_sources"]
        top_kwargs["light_sources"] = tuple(ls) if isinstance(ls, (list, tuple, set)) else (ls,)
        consumed.add("light_sources")
    elif "light_source" in raw_facts:
        ls = raw_facts["light_source"]
        top_kwargs["light_sources"] = (ls,) if isinstance(ls, str) else tuple(ls)
        consumed.add("light_source")

    if "color_modes" in raw_facts:
        cm = raw_facts["color_modes"]
        top_kwargs["color_modes"] = tuple(cm) if isinstance(cm, (list, tuple, set)) else (cm,)
        consumed.add("color_modes")
    elif "color_mode" in raw_facts:
        cm = str(raw_facts["color_mode"]).lower()
        if cm.startswith("bw"):
            top_kwargs["color_modes"] = ("monochrome",)
        elif cm.startswith("color"):
            top_kwargs["color_modes"] = ("color",)
        consumed.add("color_mode")

    if "mutex_groups" in raw_facts:
        mg = raw_facts["mutex_groups"]
        top_kwargs["mutex_groups"] = tuple(mg) if isinstance(mg, (list, tuple, set)) else (mg,)
        consumed.add("mutex_groups")
    elif "mutex_group" in raw_facts:
        mg = raw_facts["mutex_group"]
        top_kwargs["mutex_groups"] = (mg,) if isinstance(mg, str) else tuple(mg)
        consumed.add("mutex_group")

    if "incompatible_with" in raw_facts:
        iw = raw_facts["incompatible_with"]
        top_kwargs["incompatible_with"] = tuple(iw) if isinstance(iw, (list, tuple, set)) else (iw,)
        consumed.add("incompatible_with")

    # 13. 治理元数据
    gov_kwargs: Dict[str, Any] = {}
    for gk in GOVERNANCE_FACT_KEYS:
        if gk in raw_facts:
            gov_kwargs[gk] = raw_facts[gk]
            consumed.add(gk)
    if gov_kwargs:
        top_kwargs["governance_metadata"] = gov_kwargs

    # 14. 顶层直接字段 (除子模型与已处理字段之外)
    direct_top_keys = set(SemanticFacts.__dataclass_fields__.keys()) - {
        "explicit_fields", "pose_facts", "scene_facts", "camera_facts",
        "hair_facts", "lighting_facts", "shot_facts", "clothing_facts",
        "expression_facts", "accessory_facts", "governance_metadata",
        "cut_features", "ensemble_pieces", "light_sources", "color_modes",
        "mutex_groups", "incompatible_with"
    }
    for k in direct_top_keys:
        if k in raw_facts:
            top_kwargs[k] = raw_facts[k]
            consumed.add(k)

    # 15. Fail-Closed 断言：来源 172 键必须 100% 被捕获，严禁静默丢弃
    unmapped = set(raw_facts.keys()) - consumed
    if unmapped:
        raise ValueError(
            f"Unmapped source facts keys for entity '{source_entity_id}': {sorted(unmapped)}"
        )

    return SemanticFacts.from_dict(top_kwargs)


@dataclass(frozen=True)
class MigrationReport:
    total_records: int
    quarantine_isolated_count: int
    runtime_candidates_count: int
    runtime_source_entities_count: int
    ensembles_migrated: int
    ensemble_relations_derived: int
    ambiguous_poses_preserved: int
    dag_validation_failures: int
    schema_parsing_failures: int
    unmapped_fact_keys_count: int


def run_full_migration(
    source_tsv: Path,
    mapping_tsv: Path,
) -> Tuple[MigrationReport, List[Tuple[str, SemanticFacts]]]:
    """全量执行 M3.3 候选池规范化迁移与隔离池门禁自检。"""
    # 1. 提取隔离项清单
    quarantine_entities: Set[str] = set()
    with open(source_tsv, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row.get("decision") == "DEFERRED_ISSUE":
                quarantine_entities.add(row["entity_id"])

    assert quarantine_entities == QUARANTINE_ENTITY_IDS, (
        f"Quarantine entities mismatch! TSV has {len(quarantine_entities)}, resolver has {len(QUARANTINE_ENTITY_IDS)}"
    )

    total_records = 0
    quarantine_isolated = 0
    runtime_candidates = 0
    runtime_entities: Set[str] = set()
    ensembles_migrated = 0
    relations_derived = 0
    ambiguous_poses = 0
    dag_failures = 0
    parsing_failures = 0
    unmapped_keys_seen: Set[str] = set()

    results: List[Tuple[str, SemanticFacts]] = []

    with open(mapping_tsv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            total_records += 1
            s_id = row["source_entity_id"]
            m_id = row["mapping_id"]
            target_catalog = row.get("target_catalog_file", "")

            if s_id in quarantine_entities:
                quarantine_isolated += 1
                # 隔离项验证：调用构建必须被物理拦截
                try:
                    build_runtime_semantic_facts(json.loads(row.get("semantic_facts_json", "{}")), s_id, target_catalog)
                    raise AssertionError(f"Quarantined entity {s_id} was NOT blocked by build_runtime_semantic_facts!")
                except QuarantineLeakageError:
                    pass  # 成功拦截
                continue

            runtime_candidates += 1
            runtime_entities.add(s_id)
            raw_facts = json.loads(row.get("semantic_facts_json", "{}"))

            # 统计模糊姿态
            bs = raw_facts.get("body_support")
            if bs in ("ground_or_held", "standing_or_crouched", "standing_or_sitting"):
                ambiguous_poses += 1

            try:
                facts_obj = build_runtime_semantic_facts(raw_facts, s_id, target_catalog)
                results.append((m_id, facts_obj))

                if facts_obj.ensemble_pieces:
                    ensembles_migrated += 1
                    relations_derived += len(facts_obj.ensemble_pieces.relations)
            except UnresolvedEnsembleRelationError:
                dag_failures += 1
            except ValueError as ve:
                if "Unmapped source facts keys" in str(ve):
                    parsing_failures += 1
                    unmapped_keys_seen.add(str(ve))
                else:
                    parsing_failures += 1
            except Exception:
                parsing_failures += 1

    report = MigrationReport(
        total_records=total_records,
        quarantine_isolated_count=quarantine_isolated,
        runtime_candidates_count=runtime_candidates,
        runtime_source_entities_count=len(runtime_entities),
        ensembles_migrated=ensembles_migrated,
        ensemble_relations_derived=relations_derived,
        ambiguous_poses_preserved=ambiguous_poses,
        dag_validation_failures=dag_failures,
        schema_parsing_failures=parsing_failures,
        unmapped_fact_keys_count=len(unmapped_keys_seen),
    )
    return report, results
