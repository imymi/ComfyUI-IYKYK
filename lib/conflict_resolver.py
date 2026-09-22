"""
conflict_resolver.py — 冲突消解引擎权威执行器 (Python SSOT)

严格遵循 DAG 拓扑执行顺序 (17 条规则)，不可变规则契约 (lib/rule_contract.py)，
多信号情境亲和度接入 (lib/context_affinity.py) 与全链路零旁路账本闭环。
"""
from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from dataclasses import dataclass, replace
import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

if __package__:
    from .errors import RuleConfigurationError, UnresolvedConflictError
    from .models import (
        CANONICAL_SELECTORS_BY_ENTRY_POINT,
        FORMAL_ORIGIN_MODES,
        ContextProfile,
        PromptAtom,
        PromptFragment,
        ResolutionDecision,
        ResolutionReport,
        SelectionOrigin,
        SemanticFacts,
        SpanType,
        TagProvenance,
    )
    from .rng import derive_substream_rng, recover_effective_seed
    from .rule_contract import (
        AngleGazeMappingSpec,
        BannedComboSpec,
        DeviceConstraintSpec,
        EmotionGazeConflictSpec,
        PatternSpec,
        RuleDocument,
        RuleItem,
        TriggerBanConflictSpec,
        parse_rule_document,
    )
    from .slot_contract import SLOT_ALIASES, normalize_slot_name
else:
    from lib.errors import RuleConfigurationError, UnresolvedConflictError
    from lib.models import (
        CANONICAL_SELECTORS_BY_ENTRY_POINT,
        FORMAL_ORIGIN_MODES,
        ContextProfile,
        PromptAtom,
        PromptFragment,
        ResolutionDecision,
        ResolutionReport,
        SelectionOrigin,
        SemanticFacts,
        SpanType,
        TagProvenance,
    )
    from lib.rng import derive_substream_rng, recover_effective_seed
    from lib.rule_contract import (
        AngleGazeMappingSpec,
        BannedComboSpec,
        DeviceConstraintSpec,
        EmotionGazeConflictSpec,
        PatternSpec,
        RuleDocument,
        RuleItem,
        TriggerBanConflictSpec,
        parse_rule_document,
    )
    from lib.slot_contract import SLOT_ALIASES, normalize_slot_name


# ═══════════════════════════════════════════════════════════════════════════
# 服装实体聚合与绑定判定 SSOT 生产级实现
# ═══════════════════════════════════════════════════════════════════════════

class BindingStatus(str, Enum):
    BOUND = "bound"
    UNBOUND_NO_CANDIDATE = "unbound_no_candidate"
    UNBOUND_TARGET_NOT_FOUND = "unbound_target_not_found"
    UNBOUND_INCOMPATIBLE = "unbound_incompatible"
    AMBIGUOUS_MULTIPLE_CANDIDATES = "ambiguous_multiple_candidates"


@dataclass
class GarmentCarrierEntity:
    entity_id: str
    selector: str
    selected_id: str
    member_atoms: List[PromptAtom]
    is_worn: bool = True
    is_ambient: bool = False
    discarded_by: Optional[PromptAtom] = None


@dataclass
class BindingResult:
    status: BindingStatus
    target_entity: Optional[GarmentCarrierEntity] = None
    reason: str = ""


ALLOWED_BUTTON_STYLES: Set[str] = {
    "business_suit", "shirts_blouses", "trench_coat", "lab_coat", "dungarees",
    "dirndl_dress", "qipao", "fast_food_uniform", "denim_jacket", "down_jacket",
    "safari_jacket", "duffel_coat", "tailcoat", "firefighter_gear", "blazer_uniform",
    "clerical_priest", "military_uniform", "military_overcoat", "outerwear_coat",
    "outerwear_overcoat", "outerwear_jacket", "soft_shell_jacket", "rainwear_coat",
    "bathrobe", "combat_tactical", "convenience_store", "frock_smock", "hospital_gown",
    "denim_shorts", "leather_skirt",
}

NON_SKIRT_ONE_PIECE: Set[str] = {
    "latex_catsuit", "zentai_suit", "leotard_bodysuit", "mecha_power_armor",
    "mecha_exoskeleton", "racing_suit", "swimsuit_classic", "swimsuit_school",
    "swimsuit_competition", "swimsuit_creative", "one_piece_swimsuit", "dungarees",
    "qipao",
}

# 特定款式的产品业务规则：仅授权数据中明确具备拉链开襟联动事实的款式
ALLOWED_ZIPPER_STYLES: Set[str] = {
    "nurse_uniform",      # 护士服拉链开领 (nurse uniform unzipped to waist)
    "latex_catsuit",      # 乳胶紧身衣拉链 (latex bodysuit unzipped to navel / zipper pulled halfway down)
    "modern_chinese",     # 新中式改良旗袍拉链 (modern qipao unzipped)
}


def is_garment_compatible_with_state(entity: GarmentCarrierEntity, state_id: str) -> bool:
    """判定服装实体是否具备承载特定状态原子的物理形制能力（按特定款式的产品业务规则判定）。"""
    if state_id in ("lifted_up", "lifted", "lifted_skirt"):
        return any(
            a.facts and (
                "bottom_skirt" in a.facts.garment_topologies or
                ("one_piece" in a.facts.garment_topologies and entity.selected_id not in NON_SKIRT_ONE_PIECE)
            )
            for a in entity.member_atoms
        )
    elif state_id in ("unbuttoned", "opened"):
        return entity.selected_id in ALLOWED_BUTTON_STYLES
    elif state_id == "unzipped":
        return entity.selected_id in ALLOWED_ZIPPER_STYLES
    elif state_id == "pulled_down":
        return any(
            a.facts and any(t in a.facts.garment_topologies for t in ("bottom_pants", "bottom_skirt", "underwear", "top", "one_piece"))
            for a in entity.member_atoms
        )
    return True


CANONICAL_MODIFIER_STATES: Set[str] = {
    "unbuttoned", "unzipped", "lifted_up", "pulled_down", "disheveled", "slipping_off",
    "wet_clinging", "wet_pure", "sweat_soaked",
    "heart_cutout", "back_cutout", "underboob_cutout", "torn_shredded",
    "off_shoulder_cut", "bare_shoulders", "taut_tight",
}

CANONICAL_ABSENCE_STATES: Set[str] = {"braless", "underwearless"}

CANONICAL_LAYERING_STATES: Set[str] = {"skirt_under_kimono", "undergarment_leotard"}

BODY_EXPOSURE_TERMS: Set[str] = {
    "topless", "bottomless", "bare breasts", "bare chest", "bare skin",
    "bare back", "bare body", "completely naked", "completely nude",
    "fully bare body", "fully bare skin", "fully nude", "bare pussy",
    "bare pussy visible", "bare pussy displayed", "spread legs",
    "cleavage", "cleavage exposed", "exposed nipples", "bare upper body",
    "open chest", "fully bare glowing body", "fully bare glowing skin",
    "fully bare sun-kissed skin", "bare skin glowing", "nude", "naked",
    "bare breasts exposed", "bare breasts completely exposed",
    "breasts bare waist covered", "bare breasts over glossy latex pants",
    "bare breasts bursting from glossy rubber",
}

RE_ZIPPER_ACTION = re.compile(
    r"\b(?:unzipped|zipper\s+pulled(?:\s+halfway)?\s+down|zipper\s+down\s+to)\b",
    re.IGNORECASE,
)
RE_BUTTON_ACTION = re.compile(
    r"\b(?:unbuttoned|buttons?\s+undone|buttons?\s+unbuttoned)\b",
    re.IGNORECASE,
)
RE_PULL_DOWN_ACTION = re.compile(r"\b(?:pulled\s+down|pulled_down)\b", re.IGNORECASE)
RE_LIFT_ACTION = re.compile(r"\b(?:lifted\s+up|lifted_up|hiked\s+up|lifted\s+skirt|pulled\s+up)\b", re.IGNORECASE)
RE_SLIPPING_ACTION = re.compile(r"\b(?:slipping\s+off|sliding\s+off)\b", re.IGNORECASE)
RE_TORN_ACTION = re.compile(r"\b(?:torn|shredded|ripped)\b", re.IGNORECASE)
RE_DISHEVELED_ACTION = re.compile(r"\b(?:disheveled|untucked|rumpled)\b", re.IGNORECASE)
RE_PURE_BODY_EXPOSURE = re.compile(
    r"\b(?:topless|bare\s+breasts|bare\s+skin|completely\s+naked|completely\s+nude|fully\s+bare|bare\s+pussy)\b",
    re.IGNORECASE,
)


def get_canonical_state_id(atom: PromptAtom) -> str:
    """提取原子的规范状态 ID，优先结构化事实与规范 ID，集中处理历史文本短语兼容（单源真实源 SSOT）。"""
    # 1. 优先结构化事实
    if atom.facts and atom.facts.garment_states:
        for gs in atom.facts.garment_states:
            if gs in ("unbuttoned", "opened"):
                return "unbuttoned"
            elif gs == "unzipped":
                return "unzipped"
            elif gs in ("lifted", "lifted_up", "lifted_skirt"):
                return "lifted_up"
            elif gs == "pulled_down":
                return "pulled_down"
            elif gs in CANONICAL_MODIFIER_STATES or gs in CANONICAL_ABSENCE_STATES or gs in CANONICAL_LAYERING_STATES or gs == "discarded":
                return gs

    # 2. 规范 item_id / origin.selected_id
    raw_id = atom.source_item_id or (atom.origin.selected_id if atom.origin else "")
    if raw_id in CANONICAL_MODIFIER_STATES or raw_id in CANONICAL_ABSENCE_STATES or raw_id in CANONICAL_LAYERING_STATES or raw_id == "discarded":
        return raw_id

    # 3. 集中文本动作短语正则回退 (历史文本兼容，杜绝单字误判)
    clean_text = atom.text.lower().strip()
    if RE_ZIPPER_ACTION.search(clean_text):
        return "unzipped"
    elif RE_BUTTON_ACTION.search(clean_text):
        return "unbuttoned"
    elif RE_PULL_DOWN_ACTION.search(clean_text):
        return "pulled_down"
    elif RE_LIFT_ACTION.search(clean_text):
        return "lifted_up"
    elif RE_SLIPPING_ACTION.search(clean_text):
        return "slipping_off"
    elif RE_TORN_ACTION.search(clean_text):
        return "torn_shredded"
    elif RE_DISHEVELED_ACTION.search(clean_text):
        return "disheveled"

    return raw_id or clean_text


def is_body_exposure_atom(a: PromptAtom) -> bool:
    """判定原子是否表达纯人体生理裸露/暴露事实（身体事实绝对免于服装承载物检查）。"""
    # 1. 显式指定承载目标、或具有服装事实/修饰动作的原子绝非纯身体事实
    if getattr(a, "target_id", None) is not None:
        return False
    if a.facts and (a.facts.garment_states or a.facts.garment_topologies):
        return False
    state_id = get_canonical_state_id(a)
    if state_id in CANONICAL_MODIFIER_STATES:
        return False

    clean_text = a.text.lower().strip()
    # 2. 纯身体事实准入
    if a.source_slot == "nudity" or (a.origin and a.origin.selector == "nudity"):
        return True
    if a.facts:
        nudity_level = getattr(a.facts, "nudity_level", None)
        if nudity_level and nudity_level not in ("L1", "sfw"):
            return True
        if a.facts.visible_regions and any(r in a.facts.visible_regions for r in ("breasts", "genitals", "buttocks", "pubic")):
            if not a.facts.garment_states and not a.facts.garment_topologies:
                return True
    if clean_text in BODY_EXPOSURE_TERMS:
        return True
    if RE_PURE_BODY_EXPOSURE.search(clean_text):
        return True
    return False


def is_garment_modifier_atom(a: PromptAtom) -> bool:
    """判定原子是否属于需要服装主体承载的衣物动作、湿润、切口或质感修饰。"""
    if is_body_exposure_atom(a):
        return False

    slot = (a.source_slot or (a.origin.selector if a.origin else "") or "").lower()
    kind = ((a.provenance.kind or "") if a.provenance else "").lower()
    non_garment_domains = {
        "hairstyle", "hair", "makeup", "accessory", "accessories",
        "pose", "emotion", "lighting", "shot", "camera", "context",
        "scene_category", "scene_theme", "theme", "shot_framing", "camera_angle",
        "film_grain", "liquid", "tattoo", "props", "character", "imperfections",
        "quality", "preset", "recipe", "nudity", "nudity_level",
    }
    if slot in non_garment_domains or kind in non_garment_domains:
        return False

    state_id = get_canonical_state_id(a)
    if state_id in CANONICAL_ABSENCE_STATES or state_id in CANONICAL_LAYERING_STATES or state_id == "discarded":
        return False
    if state_id in CANONICAL_MODIFIER_STATES:
        return True
    if a.facts and a.facts.garment_states:
        modifier_states = {
            "unbuttoned", "unzipped", "opened", "lifted", "lifted_up", "pulled_down",
            "disheveled", "slipping_off", "wet_clinging", "wet_pure",
            "sweat_soaked", "torn_shredded", "heart_cutout", "back_cutout",
            "underboob_cutout", "off_shoulder_cut", "taut_tight",
        }
        if any(s in modifier_states for s in a.facts.garment_states):
            return True

    # 若显式指定 target_id 且非主服装实体，判定为修饰
    if getattr(a, "target_id", None) is not None:
        if not (a.facts and any(t in a.facts.garment_topologies for t in ("top", "bottom_skirt", "bottom_pants", "one_piece", "outerwear", "suit"))):
            return True

    return False


def build_garment_entity_key(atom: PromptAtom) -> Optional[str]:
    """区分来源 (Provenance) 与实体身份 (Garment Entity Identity)。"""
    if (atom.origin and atom.origin.selector == "clothing_state") or (atom.provenance and atom.provenance.kind == "clothing_state"):
        return None

    slot = atom.origin.selector if atom.origin else (atom.source_slot or "")
    prov_kind = atom.provenance.kind if atom.provenance else ""
    item_id = atom.source_item_id or (atom.origin.selected_id if atom.origin else "") or atom.id

    is_carrier = False
    if slot in ("clothing", "base_clothing") or prov_kind in ("clothing", "base_clothing"):
        is_carrier = True
    elif prov_kind == "clothing_extension":
        if atom.facts and any(t in atom.facts.garment_topologies for t in ("one_piece", "top", "bottom_skirt", "bottom_pants", "outerwear", "underwear", "suit")):
            is_carrier = True
    elif slot == "lingerie" or prov_kind in ("lingerie", "lingerie_wardrobe"):
        is_carrier = True
    elif slot == "jewelry" or prov_kind in ("jewelry", "headwear_jewelry"):
        if item_id in ("cloak", "hooded_cloak", "poncho", "fur_shawl"):
            is_carrier = True

    if not is_carrier:
        return None

    mode = atom.origin.mode if atom.origin else "explicit"
    if mode in ("preset", "recipe") and atom.origin and atom.origin.parent_ids:
        parent_id = atom.origin.parent_ids[0]
        return f"{mode}:{parent_id}#{slot}#{item_id}"

    return f"garment:{slot}:{item_id}"


def extract_garment_entities(active_atoms: Sequence[PromptAtom]) -> Dict[str, GarmentCarrierEntity]:
    """全域扫描 PromptAtom 序列，按稳定的服装实体键聚合物理服装实体。"""
    entities_map: Dict[str, GarmentCarrierEntity] = {}
    for a in active_atoms:
        ekey = build_garment_entity_key(a)
        if not ekey:
            continue
        slot = a.origin.selector if a.origin else (a.source_slot or "")
        item_id = a.source_item_id or (a.origin.selected_id if a.origin else "") or a.id
        if ekey not in entities_map:
            entities_map[ekey] = GarmentCarrierEntity(
                entity_id=ekey,
                selector=slot,
                selected_id=item_id,
                member_atoms=[a],
                is_worn=True,
                is_ambient=False,
            )
        else:
            entities_map[ekey].member_atoms.append(a)
    return entities_map



def find_bound_carrier(
    state_atom: PromptAtom,
    entities: Sequence[GarmentCarrierEntity],
    target_id: Optional[str] = None,
) -> BindingResult:
    """四阶确定性绑定决策阶梯：显式目标精确匹配快速失败，无目标形制能力筛选，拒绝盲选 entities[0]。"""
    active_entities = [e for e in entities if e.is_worn and not e.is_ambient]
    if not active_entities:
        return BindingResult(status=BindingStatus.UNBOUND_NO_CANDIDATE, reason="no_active_garments")

    state_id = get_canonical_state_id(state_atom)
    effective_target_id = target_id if target_id is not None else getattr(state_atom, "target_id", None)

    # 阶梯 1：显式目标匹配（规范 ID 精确匹配，快速失败，绝不回退改绑其他衣物）
    if effective_target_id is not None:
        matched = [
            e for e in active_entities
            if e.selected_id == effective_target_id or e.entity_id == effective_target_id
        ]
        if not matched:
            return BindingResult(
                status=BindingStatus.UNBOUND_TARGET_NOT_FOUND,
                reason=f"explicit_target_not_found: {effective_target_id}",
            )
        if len(matched) == 1:
            target_entity = matched[0]
            if not is_garment_compatible_with_state(target_entity, state_id):
                return BindingResult(
                    status=BindingStatus.UNBOUND_INCOMPATIBLE,
                    target_entity=target_entity,
                    reason=f"target_incompatible: entity '{target_entity.selected_id}' cannot accept state '{state_id}'",
                )
            return BindingResult(
                status=BindingStatus.BOUND,
                target_entity=target_entity,
                reason="explicit_target_match",
            )
        else:
            compatible_matched = [e for e in matched if is_garment_compatible_with_state(e, state_id)]
            if len(compatible_matched) == 1:
                return BindingResult(
                    status=BindingStatus.BOUND,
                    target_entity=compatible_matched[0],
                    reason="explicit_target_match",
                )
            elif not compatible_matched:
                return BindingResult(
                    status=BindingStatus.UNBOUND_INCOMPATIBLE,
                    reason=f"explicit_target_incompatible: {effective_target_id}",
                )
            else:
                return BindingResult(
                    status=BindingStatus.AMBIGUOUS_MULTIPLE_CANDIDATES,
                    reason=f"multiple_explicit_targets_ambiguous: {effective_target_id}",
                )

    # 阶梯 2：无显式目标时，按形制能力筛选兼容候选集合
    compatible_candidates = [
        e for e in active_entities
        if is_garment_compatible_with_state(e, state_id)
    ]

    # 阶梯 3：零候选或单候选确定性裁决（区分“零兼容候选”和“单兼容候选”）
    if len(compatible_candidates) == 0:
        return BindingResult(
            status=BindingStatus.UNBOUND_NO_CANDIDATE,
            reason=f"no_compatible_candidate_for_state: {state_id}",
        )
    elif len(compatible_candidates) == 1:
        reason = "single_compatible_candidate_match"
        if state_id == "lifted_up":
            reason = "skirt_capability_unique_match"
        elif state_id == "unbuttoned":
            reason = "button_capability_unique_match"
        elif state_id == "pulled_down":
            reason = "pulled_capability_unique_match"
        return BindingResult(
            status=BindingStatus.BOUND,
            target_entity=compatible_candidates[0],
            reason=reason,
        )

    # 阶梯 4：多候选歧义拒绝与保全原则（严禁 entities[0] 盲选！）
    return BindingResult(
        status=BindingStatus.AMBIGUOUS_MULTIPLE_CANDIDATES,
        reason=f"ambiguous_carrier_binding: {len(compatible_candidates)} candidates",
    )


# 预编译常量匹配模式 (R2-N09: 零运行时 re.compile 调用)
LVL_REGEX_MAP = {
    "L6": r"\b(?:l6|extreme nude|explicitly naked|explicit nude|full naked)\b",
    "L5": r"\b(?:l5|completely naked|full nude|bare body|unclothed|stripped bare)\b",
    "L4": r"\b(?:l4|micro bikini|sling bikini|minimal covering|pasties|tape)\b",
    "L3": r"\b(?:l3|lingerie|underwear|bra|panties|bikini|swimsuit)\b",
    "L2": r"\b(?:l2|cleavage|deep v-neck|plunging neckline|see-through|translucent|sheer)\b",
    "L1": r"\b(?:l1|fully clothed|neatly worn|casual wear|suit|coat|jacket)\b",
}
LVL_PATTERN_SPECS: Dict[str, PatternSpec] = {
    lvl: PatternSpec(pattern=LVL_REGEX_MAP[lvl], match_mode="regex")
    for lvl in ("L6", "L5", "L4", "L3", "L2", "L1")
}
for ps in LVL_PATTERN_SPECS.values():
    ps.compile()


def is_formal_atom(a: PromptAtom) -> bool:
    """判断是否为正式目录/预设/配方产出的 Atom。仅信任封闭来源模式。"""
    if a.origin is not None and a.origin.mode in FORMAL_ORIGIN_MODES:
        return True
    src_mode = getattr(a.provenance, "source_mode", None)
    if src_mode in FORMAL_ORIGIN_MODES:
        return True
    return False


def make_replaced_atom(
    target: PromptAtom,
    new_text: str,
    rule_id: str,
    new_facts: Optional[SemanticFacts] = None,
) -> PromptAtom:
    """每次替换生成新的、确定性的 Atom ID，保留 target、produced 和 parent 闭环 (R2-P1-003, R3-P1-005)。"""
    new_atom_id = f"{target.atom_id}__r_{rule_id}"
    new_parents = (target.atom_id,) + tuple(p for p in target.provenance.parent_ids if p != target.atom_id)
    orig_source_mode = target.provenance.source_mode
    if not orig_source_mode:
        if target.origin and target.origin.mode in FORMAL_ORIGIN_MODES:
            orig_source_mode = target.origin.mode
        elif target.origin and target.origin.mode in ("custom", "none"):
            orig_source_mode = target.origin.mode
        elif target.provenance and target.provenance.kind in ("custom", "user_input"):
            orig_source_mode = target.provenance.kind
    new_prov = replace(target.provenance, parent_ids=new_parents, rule_id=rule_id, source_mode=orig_source_mode)
    tgt_selector = target.origin.selector if target.origin else target.source_slot
    if tgt_selector not in CANONICAL_SELECTORS_BY_ENTRY_POINT.get("generator", ()):
        tgt_selector = "custom"
    new_origin = SelectionOrigin(
        entry_point=target.origin.entry_point if target.origin else "generator",
        mode="resolver",
        selector=tgt_selector,
        selected_id=f"rule:{rule_id}",
        raw_value=target.origin.raw_value if target.origin else target.text,
        parent_ids=(target.atom_id,),
    )
    return replace(
        target,
        atom_id=new_atom_id,
        text=new_text,
        provenance=new_prov,
        origin=new_origin,
        facts=new_facts if new_facts is not None else target.facts,
    )


def compute_atoms_hash(atoms: Sequence[PromptAtom]) -> str:
    """计算原子序列的确定性 SHA-256 哈希。"""
    h = hashlib.sha256()
    for a in atoms:
        h.update(f"{a.atom_id}:{a.text}:{a.source_slot}:{a.tag_order}:{a.span_order}".encode("utf-8"))
    return h.hexdigest()


class OneTimeIndex:
    """单次扫描构建的快速索引容器，支持规则共享高效查询与 tombstone 跟踪 (R2-P2-001, R2R2-P3-001)。"""
    def __init__(self, atoms: Sequence[PromptAtom]):
        self._all_atoms: List[PromptAtom] = list(atoms)
        self._tombstones: Set[str] = set()
        self._indexed_atom_ids: Set[str] = set()
        self.by_slot: Dict[str, List[PromptAtom]] = {}
        self.by_item_id: Dict[str, List[PromptAtom]] = {}
        self.by_origin_mode: Dict[str, List[PromptAtom]] = {}
        self.by_mutex_group: Dict[str, List[PromptAtom]] = {}
        self.by_parent_source_id: Dict[str, List[PromptAtom]] = {}
        self.by_fact: Dict[Tuple[str, Any], List[PromptAtom]] = {}
        self.ordered_detectable_atoms: List[PromptAtom] = []

        for a in atoms:
            self._index_atom(a)

    def _index_atom(self, a: PromptAtom) -> None:
        if a.atom_id in self._indexed_atom_ids:
            return
        self._indexed_atom_ids.add(a.atom_id)
        norm_slot = normalize_slot_name(a.source_slot)
        slots_to_index = {a.source_slot, norm_slot}
        if a.source_slot == "scene_theme" or norm_slot == "scene_theme":
            slots_to_index.update(("scene", "theme"))
        for alias, canonical in SLOT_ALIASES.items():
            if canonical == a.source_slot or canonical == norm_slot:
                slots_to_index.add(alias)
        for s in slots_to_index:
            self.by_slot.setdefault(s, []).append(a)
        if a.source_item_id:
            self.by_item_id.setdefault(a.source_item_id, []).append(a)
        if a.origin and a.origin.mode:
            self.by_origin_mode.setdefault(a.origin.mode, []).append(a)
        if a.exclusive_group:
            self.by_mutex_group.setdefault(a.exclusive_group, []).append(a)
        if a.facts:
            if a.facts.mutex_groups:
                for mg in a.facts.mutex_groups:
                    self.by_mutex_group.setdefault(mg, []).append(a)
            for k in ("space_kind", "hand_state", "prop_usage", "emotion", "gaze", "occlusion",
                      "time_of_day", "capture_device", "quality_class", "makeup_base", "liquid_kind", "liquid_amount"):
                v = getattr(a.facts, k, None)
                if v:
                    self.by_fact.setdefault((k, v), []).append(a)
            for k in ("venue_ids", "visible_regions", "garment_topologies", "garment_states",
                      "light_sources", "color_modes", "makeup_effects", "liquid_locations"):
                v_list = getattr(a.facts, k, ())
                for v in v_list:
                    self.by_fact.setdefault((k, v), []).append(a)
        if a.provenance:
            if a.provenance.item_id:
                self.by_item_id.setdefault(a.provenance.item_id, []).append(a)
            for pid in a.provenance.parent_ids:
                self.by_parent_source_id.setdefault(pid, []).append(a)
        if a.can_detect:
            self.ordered_detectable_atoms.append(a)

    def is_active(self, a: Any) -> bool:
        atom_id = a if isinstance(a, str) else getattr(a, "atom_id", str(a))
        return atom_id not in self._tombstones

    def drop(self, atom_id: str) -> None:
        self._tombstones.add(atom_id)

    def tombstone(self, atom_id: str) -> None:
        self._tombstones.add(atom_id)

    def _update_active_atom(self, updated: PromptAtom) -> None:
        """更新活跃原子的实例对象并同步索引容器。"""
        aid = updated.atom_id
        norm_slot = normalize_slot_name(updated.source_slot)
        slots = {updated.source_slot, norm_slot}
        if updated.source_slot == "scene_theme" or norm_slot == "scene_theme":
            slots.update(("scene", "theme"))
        for s in slots:
            if s in self.by_slot:
                for j, at in enumerate(self.by_slot[s]):
                    if at.atom_id == aid:
                        self.by_slot[s][j] = updated
        for j, at in enumerate(self.ordered_detectable_atoms):
            if at.atom_id == aid:
                self.ordered_detectable_atoms[j] = updated
        if updated.origin and updated.origin.mode:
            self.by_origin_mode.setdefault(updated.origin.mode, []).append(updated)

    def replace(self, old_atom_id: str, new_atom: PromptAtom) -> None:
        self._tombstones.add(old_atom_id)
        self._all_atoms.append(new_atom)
        self._index_atom(new_atom)
        # 同步同一 Tag (tag_order) 的所有存活 sibling spans 的 Tag 级元数据 (origin, facts, id)
        for i, a in enumerate(self._all_atoms):
            if a.atom_id not in self._tombstones and a.tag_order == new_atom.tag_order and a.atom_id != new_atom.atom_id:
                updated_sibling = replace(
                    a,
                    origin=new_atom.origin,
                    facts=new_atom.facts,
                    id=new_atom.id,
                )
                self._all_atoms[i] = updated_sibling
                self._update_active_atom(updated_sibling)

    def inject(self, new_atom: PromptAtom) -> None:
        self._all_atoms.append(new_atom)
        self._index_atom(new_atom)

    def get_active_by_slot(self, slot: str) -> List[PromptAtom]:
        return [a for a in self.by_slot.get(slot, []) if a.atom_id not in self._tombstones]

    def get_active_by_slots(self, *slots: str) -> List[PromptAtom]:
        seen = set()
        res = []
        for s in slots:
            for a in self.by_slot.get(s, []):
                if a.atom_id not in self._tombstones and a.atom_id not in seen:
                    seen.add(a.atom_id)
                    res.append(a)
        return res

    def get_active_by_fact(self, key_or_pair: Any, val: Any = None) -> List[PromptAtom]:
        """按语义事实键值查询活跃原子，支持 (key, val) 单一元组或双参数调用 (R2R2-P3-001)。"""
        if isinstance(key_or_pair, tuple) and val is None:
            k, v = key_or_pair
        else:
            k, v = key_or_pair, val
        seen = set()
        res = []
        for a in self.by_fact.get((k, v), []):
            if a.atom_id not in self._tombstones and a.atom_id not in seen:
                seen.add(a.atom_id)
                res.append(a)
        return res

    def get_active_by_item_id(self, item_id: str) -> List[PromptAtom]:
        """按 item ID 查询活跃原子 (R2R2-P3-001)。"""
        seen = set()
        res = []
        for a in self.by_item_id.get(item_id, []):
            if a.atom_id not in self._tombstones and a.atom_id not in seen:
                seen.add(a.atom_id)
                res.append(a)
        return res

    def get_active_by_mutex_group(self, group: str) -> List[PromptAtom]:
        """按互斥组查询活跃原子 (R2R2-P3-001)。"""
        seen = set()
        res = []
        for a in self.by_mutex_group.get(group, []):
            if a.atom_id not in self._tombstones and a.atom_id not in seen:
                seen.add(a.atom_id)
                res.append(a)
        return res

    def get_active_detectable(self) -> List[PromptAtom]:
        return [a for a in self.ordered_detectable_atoms if a.atom_id not in self._tombstones]

    def get_all_active_ordered(self) -> List[PromptAtom]:
        return sorted([a for a in self._all_atoms if a.atom_id not in self._tombstones], key=lambda x: (x.tag_order, x.span_order))


class DecisionLedger:
    """原子级消解决策审计账本。"""
    def __init__(self, rule_registry: Optional[RuleRegistry] = None):
        self.decisions: List[ResolutionDecision] = []
        self.produced_atoms: List[PromptAtom] = []
        self._seq = 0
        self._registry = rule_registry

    def _validate_reason_code(self, rule_id: str, reason_code: str) -> None:
        if self._registry:
            rule = self._registry.get_rule(rule_id)
            if reason_code not in rule.reason_codes:
                raise RuleConfigurationError(
                    f"Reason code '{reason_code}' is not declared in rule '{rule_id}' reason_codes: {rule.reason_codes}"
                )

    def record_drop(
        self,
        rule_id: str,
        phase: str,
        reason_code: str,
        winner_atom_ids: Sequence[str],
        target_atom: PromptAtom,
        parent_source_ids: Sequence[str] = (),
    ) -> ResolutionDecision:
        self._validate_reason_code(rule_id, reason_code)
        seq = self._seq
        self._seq += 1
        p_ids = tuple(parent_source_ids or target_atom.provenance.parent_ids)
        dec = ResolutionDecision(
            decision_id=f"dec_{seq:04d}_{rule_id}_drop",
            sequence=seq,
            rule_id=rule_id,
            phase=phase,
            action="drop",
            reason_code=reason_code,
            winner_atom_ids=tuple(winner_atom_ids),
            target_atom_id=target_atom.atom_id,
            produced_atom_ids=(),
            before_text=target_atom.text,
            after_text=None,
            parent_source_ids=p_ids,
        )
        self.decisions.append(dec)
        return dec

    def record_replace(
        self,
        rule_id: str,
        phase: str,
        reason_code: str,
        winner_atom_ids: Sequence[str],
        target_atom: PromptAtom,
        produced_atoms: Sequence[PromptAtom],
        parent_source_ids: Sequence[str] = (),
    ) -> ResolutionDecision:
        self._validate_reason_code(rule_id, reason_code)
        seq = self._seq
        self._seq += 1
        self.produced_atoms.extend(produced_atoms)
        produced_ids = tuple(a.atom_id for a in produced_atoms)
        after_text = ", ".join(a.text for a in produced_atoms)
        p_ids = tuple(parent_source_ids) if parent_source_ids else ((target_atom.atom_id,) + tuple(p for p in target_atom.provenance.parent_ids if p != target_atom.atom_id))
        dec = ResolutionDecision(
            decision_id=f"dec_{seq:04d}_{rule_id}_replace",
            sequence=seq,
            rule_id=rule_id,
            phase=phase,
            action="replace",
            reason_code=reason_code,
            winner_atom_ids=tuple(winner_atom_ids),
            target_atom_id=target_atom.atom_id,
            produced_atom_ids=produced_ids,
            before_text=target_atom.text,
            after_text=after_text,
            parent_source_ids=p_ids,
        )
        self.decisions.append(dec)
        return dec

    def record_inject(
        self,
        rule_id: str,
        phase: str,
        reason_code: str,
        winner_atom_ids: Sequence[str],
        produced_atoms: Sequence[PromptAtom],
        parent_source_ids: Sequence[str] = (),
    ) -> ResolutionDecision:
        self._validate_reason_code(rule_id, reason_code)
        seq = self._seq
        self._seq += 1
        self.produced_atoms.extend(produced_atoms)
        produced_ids = tuple(a.atom_id for a in produced_atoms)
        after_text = ", ".join(a.text for a in produced_atoms)
        dec = ResolutionDecision(
            decision_id=f"dec_{seq:04d}_{rule_id}_inject",
            sequence=seq,
            rule_id=rule_id,
            phase=phase,
            action="inject",
            reason_code=reason_code,
            winner_atom_ids=tuple(winner_atom_ids),
            target_atom_id=None,
            produced_atom_ids=produced_ids,
            before_text=None,
            after_text=after_text,
            parent_source_ids=tuple(parent_source_ids),
        )
        self.decisions.append(dec)
        return dec


def verify_provenance_closure(
    source_atoms: Sequence[PromptAtom],
    final_atoms: Sequence[PromptAtom],
    decisions: Sequence[ResolutionDecision],
) -> bool:
    """递归验证 source/decision/produced/final 的完整溯源闭环 (R2R-P1-003)。"""
    valid_ids: Set[str] = {a.atom_id for a in source_atoms}
    source_parent_ids: Set[str] = set()
    for a in source_atoms:
        if a.provenance:
            source_parent_ids.update(a.provenance.parent_ids)

    for d in decisions:
        if d.target_atom_id and d.target_atom_id not in valid_ids:
            return False
        for pid in d.parent_source_ids:
            if pid not in valid_ids and pid not in source_parent_ids:
                return False
        for prod_id in d.produced_atom_ids:
            valid_ids.add(prod_id)

    for a in final_atoms:
        if not a.provenance or not a.provenance.parent_ids:
            return False
        for pid in a.provenance.parent_ids:
            if pid not in valid_ids and pid not in source_parent_ids:
                return False

    return True


def match_pattern(pattern: str, text: str, mode: str = "phrase") -> bool:
    """向后兼容接口：直接代理至 PatternSpec 模式匹配引擎。"""
    if not pattern or not text:
        return False
    ps = PatternSpec(pattern=pattern, match_mode=mode)  # type: ignore[arg-type]
    return ps.matches(text)



def build_canonical_catalog_facts(data_dir: Path) -> Dict[Tuple[str, str], SemanticFacts]:
    """单次加载构建 (slot, stable_leaf_id) -> canonical SemanticFacts 权威映射表 (R2R2-P1-001)。"""
    data_dir = Path(data_dir)
    FILE_SLOT_MAP = {
        "accessories.json": ["jewelry", "accessories"],
        "characters.json": ["character"],
        "clothing.json": ["clothing", "clothing_state", "clothing_extension", "underwear"],
        "expressions.json": ["expression", "expressions"],
        "film_stocks.json": ["film", "film_stock"],
        "imperfections.json": ["imperfections"],
        "lighting.json": ["lighting", "lighting_palette"],
        "makeup.json": ["makeup"],
        "nudity_levels.json": ["nudity", "liquids", "liquid"],
        "poses.json": ["pose", "poses"],
        "props.json": ["props", "prop"],
        "scenes.json": ["scene_theme", "scene", "theme"],
        "shot_types.json": ["shot_type", "shot", "camera_angle", "camera", "angle", "view"],
        "style_recipes.json": ["style_recipe", "recipe"],
        "tattoos.json": ["tattoo", "tattoos"],
        "themes.json": ["scene_theme", "scene", "theme"],
    }
    catalog_facts: Dict[Tuple[str, str], SemanticFacts] = {}
    for fname, slots in FILE_SLOT_MAP.items():
        p = data_dir / fname
        if not p.exists():
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        def walk(obj, current_slot=None):
            if isinstance(obj, dict):
                slot_override = obj.get("slot", current_slot)
                if "id" in obj and "facts" in obj and isinstance(obj["id"], str):
                    lid = obj["id"]
                    try:
                        facts = SemanticFacts.from_dict(obj["facts"])
                        target_slots = [slot_override] if slot_override else slots
                        for s in target_slots:
                            norm_s = normalize_slot_name(s)
                            catalog_facts[(norm_s, lid)] = facts
                            catalog_facts[(s, lid)] = facts
                    except Exception:
                        pass
                if fname == "clothing.json" and "id" in obj and isinstance(obj["id"], str) and "tags" in obj and isinstance(obj["tags"], list):
                    lid = obj["id"]
                    combined: Dict[str, Any] = {}
                    for t in obj["tags"]:
                        if isinstance(t, dict) and "facts" in t:
                            for k, v in t["facts"].items():
                                if isinstance(v, list):
                                    if k not in combined:
                                        combined[k] = list(v)
                                    else:
                                        for x in v:
                                            if x not in combined[k]:
                                                combined[k].append(x)
                                else:
                                    if k not in combined:
                                        combined[k] = v
                                    elif combined[k] != v:
                                        combined[k] = None
                    combined = {k: v for k, v in combined.items() if v is not None}
                    if combined:
                        try:
                            facts = SemanticFacts.from_dict(combined)
                            target_slots = [slot_override] if slot_override else slots
                            for s in target_slots:
                                norm_s = normalize_slot_name(s)
                                if (norm_s, lid) not in catalog_facts:
                                    catalog_facts[(norm_s, lid)] = facts
                                    catalog_facts[(s, lid)] = facts
                        except Exception:
                            pass
                for k, v in obj.items():
                    walk(v, slot_override)
            elif isinstance(obj, list):
                for itm in obj:
                    walk(itm, current_slot)
        walk(data)

    presets_file = data_dir / "presets.json"
    if presets_file.exists():
        p_data = json.loads(presets_file.read_text(encoding="utf-8"))
        for pr in p_data.get("presets", []):
            for fr in pr.get("fragments", []):
                fid = fr.get("id")
                fslot = fr.get("slot")
                if fid and fslot and "facts" in fr:
                    try:
                        facts = SemanticFacts.from_dict(fr["facts"])
                        norm_s = normalize_slot_name(fslot)
                        if (norm_s, fid) not in catalog_facts:
                            catalog_facts[(norm_s, fid)] = facts
                            catalog_facts[(fslot, fid)] = facts
                    except Exception:
                        pass
    return catalog_facts


def select_fallback_patterns(
    tf_patterns: Sequence[PatternSpec],
    role: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Tuple[PatternSpec, ...]:
    """严格根据 RuleItem.spec.text_fallback.patterns 单一来源获取运行时可用模式 (R2R5-P1-001)。
    - 禁止接收或求交 banned_words、catalog_*、custom_* 等平行集合；
    - 所有关键词只能来自当前 RuleItem 的 text_fallback 声明 (tf_patterns)；
    - 若 tf_patterns 为空，直接返回空元组 () (Fail-Closed)；
    - 按 role 与 group_id 过滤。若指定了 role，必须匹配；若指定了 group_id，必须匹配；
    - 向后兼容：若 tf_patterns 中的所有模式均未指定 role (或 group_id)，降级跳过该维度过滤；
    - 若指定了但无任何匹配项，Fail-Closed 返回 ()。
    """
    if not tf_patterns:
        return ()
    effective_role = role
    if role is not None and all(getattr(p, "role", None) is None for p in tf_patterns):
        effective_role = None

    effective_group_id = group_id
    if group_id is not None and all(getattr(p, "group_id", None) is None for p in tf_patterns):
        effective_group_id = None

    res = []
    for p in tf_patterns:
        if effective_role is not None and getattr(p, "role", None) != effective_role:
            continue
        if effective_group_id is not None and getattr(p, "group_id", None) != effective_group_id:
            continue
        res.append(p)
    return tuple(res)


def get_fallback_group_ids(
    tf_patterns: Sequence[PatternSpec],
    role: Optional[str] = None,
) -> Tuple[str, ...]:
    """获取指定 role 下存在的所有 group_id (保持出现顺序且去重) (R2R5-P1-001)。"""
    seen = set()
    order = []
    for p in tf_patterns:
        if role is not None and getattr(p, "role", None) != role:
            continue
        gid = getattr(p, "group_id", None)
        if gid and gid not in seen:
            seen.add(gid)
            order.append(gid)
    return tuple(order)


def filter_active_patterns(
    patterns: Sequence[PatternSpec],
    tf_patterns: Sequence[PatternSpec],
    role: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Tuple[PatternSpec, ...]:
    """向后兼容别名：仅代理调用 select_fallback_patterns，禁止消费平行集合 patterns (R2R5-P1-001)。"""
    return select_fallback_patterns(tf_patterns, role=role, group_id=group_id)


class RuleRegistry:
    """冲突规则注册表：从 JSON 数据文件加载并按契约解析构建 17 个规则规格对象。"""
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.rules_file = self.data_dir / "conflict_rules.json"
        self.doc: RuleDocument = self._load_rules()
        self._rules_by_id: Dict[str, Any] = {r.id: r.spec for r in self.doc.rules}
        self._rule_items_by_id: Dict[str, RuleItem] = {r.id: r for r in self.doc.rules}
        (
            self.total_precompiled_patterns,
            self.precompiled_fallback_count,
            self.uncompiled_patterns_count,
        ) = self._precompile_all_patterns()
        self.precompiled_count: int = self.precompiled_fallback_count

    def _precompile_all_patterns(self) -> Tuple[int, int, int]:
        """深度遍历 RuleDocument，支持 collections.abc.Mapping、dataclass 字段和 visited 集合。
        遍历全部 1,078 个 PatternSpec 并完成预编译，确保未编译数为 0 (P1)。
        返回 (total_patterns, fallback_patterns, uncompiled_count)。
        """
        visited = set()
        all_patterns: List[PatternSpec] = []

        def _walk(obj: Any) -> None:
            if obj is None:
                return
            oid = id(obj)
            if oid in visited:
                return
            visited.add(oid)

            if isinstance(obj, PatternSpec):
                all_patterns.append(obj)
                return

            if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
                for f in dataclasses.fields(obj):
                    _walk(getattr(obj, f.name))
                return

            if isinstance(obj, Mapping):
                for v in obj.values():
                    _walk(v)
                return

            if isinstance(obj, (list, tuple, set, frozenset)):
                for item in obj:
                    _walk(item)
                return

        _walk(self.doc)

        fallback_count = 0
        for r in self.doc.rules:
            rtf = getattr(r.spec, "text_fallback", None)
            if rtf and rtf.patterns:
                fallback_count += len(rtf.patterns)

        for p in all_patterns:
            p.compile()

        uncompiled_count = sum(1 for p in all_patterns if getattr(p, "_compiled", None) is None)
        return len(all_patterns), fallback_count, uncompiled_count

    def _load_rules(self) -> RuleDocument:
        if not self.rules_file.exists():
            raise RuleConfigurationError(f"conflict_rules.json not found in {self.data_dir}")

        import json
        try:
            raw_data = json.loads(self.rules_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuleConfigurationError(f"Failed to parse {self.rules_file}: {e}") from e

        return parse_rule_document(raw_data)

    def get_rule(self, rule_id: str) -> Any:
        if rule_id not in self._rules_by_id:
            raise RuleConfigurationError(f"Rule {rule_id!r} not found in registry")
        return self._rules_by_id[rule_id]

    def get_rule_item(self, rule_id: str) -> RuleItem:
        if rule_id not in self._rule_items_by_id:
            raise RuleConfigurationError(f"RuleItem {rule_id!r} not found in registry")
        return self._rule_items_by_id[rule_id]


class ConflictResolver:
    """ComfyUI-IYKYK v1.1.0-rc8 冲突消解引擎权威执行器。"""
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.registry = RuleRegistry(data_dir)
        self.canonical_catalog_facts: Dict[Tuple[str, str], SemanticFacts] = build_canonical_catalog_facts(data_dir)
        self.text_fallback_hits: int = 0
        self.last_report: Optional[ResolutionReport] = None

    def _validate_formal_atom(self, a: PromptAtom, rule_id: str) -> None:
        """验证正式目录/预设/配方原子是否携带当前规则所需的基础语义事实。若事实缺失则 Fail-Closed (R2R2-P1-001)。"""
        if not is_formal_atom(a):
            return

        if a.origin and a.origin.mode == "resolver":
            return

        rule_item = self.registry.get_rule_item(rule_id)
        sc = getattr(rule_item.spec, "semantic_constraints", None)
        if sc:
            rule_target_slots = set(sc.target_slots)
            norm_target_slots = {normalize_slot_name(s) for s in rule_target_slots} | rule_target_slots
            rule_fact_fields = set(sc.fact_fields)

            norm_slot = normalize_slot_name(a.source_slot)
            is_relevant = (norm_slot in norm_target_slots) or (a.source_slot in norm_target_slots)
            if not is_relevant and a.facts:
                is_relevant = any(bool(getattr(a.facts, ff, None)) for ff in rule_fact_fields)

            if not is_relevant:
                return

        candidate_ids = []
        if a.id:
            candidate_ids.append(a.id)
        if a.provenance and a.provenance.item_id and a.provenance.item_id not in candidate_ids:
            candidate_ids.append(a.provenance.item_id)
        if a.source_item_id and a.source_item_id not in candidate_ids:
            candidate_ids.append(a.source_item_id)
        if a.origin and a.origin.selected_id and a.origin.selected_id not in candidate_ids:
            if not a.origin.selected_id.startswith("rule:"):
                candidate_ids.append(a.origin.selected_id)

        norm_slot = normalize_slot_name(a.source_slot)

        # 1. 权威事实匹配与未知 ID Fail-Closed
        canon: Optional[SemanticFacts] = None
        matched_id: Optional[str] = None
        for cid in candidate_ids:
            found = self.canonical_catalog_facts.get((norm_slot, cid)) or self.canonical_catalog_facts.get((a.source_slot, cid))
            if not found:
                for alias in (a.source_slot, norm_slot):
                    found = self.canonical_catalog_facts.get((alias, cid))
                    if found:
                        break
            if found:
                canon = found
                matched_id = cid
                break

        # 未知正式 ID + 缺失或空 facts 必须 Fail-Closed
        if candidate_ids and not canon:
            if a.facts is None or not a.facts.explicit_fields:
                raise RuleConfigurationError(
                    f"Formal atom '{a.atom_id}' has unrecognized item ID {candidate_ids[0]!r} in slot '{a.source_slot}' with empty/insufficient facts for rule '{rule_id}'"
                )

        # 2. 事实冲突检测与事实补全
        leaf_id = matched_id or (candidate_ids[0] if candidate_ids else "")
        if canon and a.facts:
            scalar_fields = (
                "space_kind", "hand_state", "prop_usage", "emotion", "gaze", "occlusion",
                "time_of_day", "capture_device", "quality_class", "makeup_base", "liquid_kind", "liquid_amount"
            )
            for sf in scalar_fields:
                av = getattr(a.facts, sf, None)
                cv = getattr(canon, sf, None)
                if av is not None and cv is not None and av != cv:
                    raise RuleConfigurationError(
                        f"Formal atom '{a.atom_id}' ({leaf_id}) fact '{sf}' conflicts with catalog: atom={av!r} vs catalog={cv!r}"
                    )
            if a.facts.hands_required != 0 and canon.hands_required != 0 and a.facts.hands_required != canon.hands_required:
                raise RuleConfigurationError(
                    f"Formal atom '{a.atom_id}' ({leaf_id}) hands_required conflicts with catalog: atom={a.facts.hands_required} vs catalog={canon.hands_required}"
                )
            object.__setattr__(a, "facts", canon.merge(a.facts))
        elif canon and a.facts is None:
            object.__setattr__(a, "facts", canon)

        if a.facts is None:
            raise RuleConfigurationError(
                f"Formal atom '{a.atom_id}' (slot='{a.source_slot}', mode='{a.origin.mode}') is missing SemanticFacts for rule '{rule_id}'"
            )

        slot = a.source_slot
        facts = a.facts

        # 3. 严格按规则契约校验必要事实字段 (Fail-Closed)
        if rule_id == "spatial_environmental_mutual_exclusion" and slot == "scene":
            if not facts.space_kind:
                raise RuleConfigurationError(
                    f"Formal scene atom '{a.atom_id}' missing required 'space_kind' fact for rule '{rule_id}'"
                )
        elif rule_id == "nudity_clothing_conflicts":
            if slot == "nudity":
                if not (facts.visible_regions or facts.garment_topologies or canon):
                    raise RuleConfigurationError(
                        f"Formal nudity atom '{a.atom_id}' missing required facts for rule '{rule_id}'"
                    )
            elif slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
                if not (facts.garment_topologies or facts.garment_states or facts.visible_regions or canon):
                    raise RuleConfigurationError(
                        f"Formal clothing atom '{a.atom_id}' missing required facts for rule '{rule_id}'"
                    )
        elif rule_id == "framing_lower_body_coherence":
            if slot in ("shot_type", "shot"):
                item_id = leaf_id.lower() if leaf_id else ""
                if item_id in ("close_up", "extreme_close_up", "macro") or facts.visible_regions:
                    if not (facts.visible_regions or canon):
                        raise RuleConfigurationError(
                            f"Formal shot atom '{a.atom_id}' missing required facts for rule '{rule_id}'"
                        )
            elif slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
                if not (facts.visible_regions or facts.garment_topologies or canon):
                    raise RuleConfigurationError(
                        f"Formal clothing atom '{a.atom_id}' missing required visible_regions for rule '{rule_id}'"
                    )
        elif rule_id == "pose_hand_occupation":
            if slot == "pose":
                if not facts.hand_state:
                    raise RuleConfigurationError(
                        f"Formal pose atom '{a.atom_id}' missing required 'hand_state' fact for rule '{rule_id}'"
                    )
            elif slot == "props":
                if facts.hands_required == 0 and not facts.prop_usage:
                    raise RuleConfigurationError(
                        f"Formal prop atom '{a.atom_id}' missing required prop facts for rule '{rule_id}'"
                    )
        elif rule_id == "handheld_props_single_holder":
            if slot == "props":
                if facts.hands_required == 0 and not facts.prop_usage:
                    raise RuleConfigurationError(
                        f"Formal prop atom '{a.atom_id}' missing required prop facts for rule '{rule_id}'"
                    )
        elif rule_id == "clothing_style_state_coherence":
            if slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
                if not (facts.garment_topologies or facts.garment_states or facts.visible_regions or canon):
                    raise RuleConfigurationError(
                        f"Formal clothing atom '{a.atom_id}' missing required garment facts for rule '{rule_id}'"
                    )
        elif rule_id == "material_penetration":
            if slot in ("clothing", "clothing_state"):
                if not (facts.garment_states or facts.garment_topologies or facts.visible_regions or canon):
                    raise RuleConfigurationError(
                        f"Formal clothing atom '{a.atom_id}' missing required garment_states for rule '{rule_id}'"
                    )
        elif rule_id == "device_quality_compatibility":
            if slot in ("shot_type", "shot"):
                item_id = leaf_id.lower() if leaf_id else ""
                if item_id in ("cctv", "vhs", "polaroid", "webcam") or facts.capture_device:
                    if not facts.capture_device:
                        raise RuleConfigurationError(
                            f"Formal shot atom '{a.atom_id}' missing required 'capture_device' fact for rule '{rule_id}'"
                        )
            elif slot == "quality":
                if not facts.quality_class:
                    raise RuleConfigurationError(
                        f"Formal quality atom '{a.atom_id}' missing required 'quality_class' fact for rule '{rule_id}'"
                    )
        elif rule_id == "environmental_lighting_coherence":
            if slot in ("scene", "scene_theme", "theme"):
                if not (facts.time_of_day or facts.space_kind or facts.venue_ids or canon):
                    raise RuleConfigurationError(
                        f"Formal scene atom '{a.atom_id}' missing required 'time_of_day' fact for rule '{rule_id}'"
                    )
            elif slot in ("lighting", "lighting_palette"):
                if (a.origin and a.origin.mode == "recipe") or getattr(a.provenance, "source_mode", None) == "recipe":
                    pass
                else:
                    item_id = leaf_id.lower() if leaf_id else ""
                    if any(k in item_id for k in ("sunlight", "daylight", "morning_sun", "golden_hour")) or facts.light_sources:
                        if not (facts.light_sources or facts.time_of_day or canon):
                            raise RuleConfigurationError(
                                f"Formal lighting atom '{a.atom_id}' missing required 'light_sources' fact for rule '{rule_id}'"
                            )
        elif rule_id == "monochrome_film_chroma_coherence":
            if slot in ("film", "film_stock"):
                if not facts.color_modes:
                    raise RuleConfigurationError(
                        f"Formal film atom '{a.atom_id}' missing required color_modes for rule '{rule_id}'"
                    )
        elif rule_id == "makeup_details_coherence":
            if slot == "makeup":
                if not (facts.makeup_base or facts.makeup_effects):
                    raise RuleConfigurationError(
                        f"Formal makeup atom '{a.atom_id}' missing required makeup facts for rule '{rule_id}'"
                    )
        elif rule_id == "gaze_angle_geometry":
            if slot in ("camera", "camera_angle", "angle", "shot", "view"):
                item_id = leaf_id.lower() if leaf_id else ""
                if item_id in ("overhead", "top_down", "birds_eye", "low_angle", "eye_level") or facts.gaze:
                    if not facts.gaze:
                        raise RuleConfigurationError(
                            f"Formal camera atom '{a.atom_id}' missing required camera angle facts for rule '{rule_id}'"
                        )
            elif slot in ("expression", "expressions"):
                item_id = leaf_id.lower() if leaf_id else ""
                if "gaze" in item_id or facts.gaze or any(k in item_id for k in ("eye", "look")):
                    if not facts.gaze:
                        raise RuleConfigurationError(
                            f"Formal expression atom '{a.atom_id}' missing required gaze facts for rule '{rule_id}'"
                        )
        elif rule_id == "accessory_occlusion_gaze_coherence":
            if slot in ("jewelry", "accessories"):
                item_id = leaf_id.lower() if leaf_id else ""
                if item_id in ("blindfold", "sunglasses", "eyepatch", "mask") or facts.occlusion:
                    if not facts.occlusion:
                        raise RuleConfigurationError(
                            f"Formal jewelry atom '{a.atom_id}' missing required facts for rule '{rule_id}'"
                        )
            elif slot in ("expression", "expressions"):
                item_id = leaf_id.lower() if leaf_id else ""
                if "gaze" in item_id or facts.gaze or any(k in item_id for k in ("eye", "look")):
                    if not facts.gaze:
                        raise RuleConfigurationError(
                            f"Formal expression atom '{a.atom_id}' missing required gaze facts for rule '{rule_id}'"
                        )
        elif rule_id == "emotion_gaze_affinity":
            if slot in ("expression", "expressions"):
                if not (facts.emotion or facts.gaze):
                    raise RuleConfigurationError(
                        f"Formal expression atom '{a.atom_id}' missing required emotion facts for rule '{rule_id}'"
                    )
        elif rule_id == "gaze_mutual_exclusion":
            if slot in ("expression", "expressions"):
                item_id = leaf_id.lower() if leaf_id else ""
                if "gaze" in item_id or facts.gaze or any(k in item_id for k in ("eye", "look")):
                    if not facts.gaze:
                        raise RuleConfigurationError(
                            f"Formal expression atom '{a.atom_id}' missing required gaze facts for rule '{rule_id}'"
                        )
        elif rule_id == "liquid_restrictions":
            if slot in ("liquids", "liquid"):
                if not (facts.liquid_kind or facts.liquid_locations or facts.liquid_amount):
                    raise RuleConfigurationError(
                        f"Formal liquid atom '{a.atom_id}' missing required liquid facts for rule '{rule_id}'"
                    )

    def resolve_atoms_with_full_report(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None,
        context_profile: Optional[ContextProfile] = None,
        effective_seed: Optional[int] = None,
    ) -> Tuple[List[PromptAtom], Tuple[str, ...], ResolutionReport]:
        """按 DAG 冻结顺序执行 17 条冲突消解规则并产出完备审计报告与零硬冲突闭环。"""
        if rng is None:
            rng = Random(42)

        self.text_fallback_hits = 0

        # R2-P1-002: 提取或恢复 effective seed
        if effective_seed is None:
            effective_seed = getattr(rng, "_effective_seed", None)
        if effective_seed is None:
            effective_seed = recover_effective_seed(rng)
        if effective_seed is None:
            state = rng.getstate()
            if isinstance(state, tuple) and len(state) >= 2 and isinstance(state[1], tuple) and len(state[1]) >= 2:
                effective_seed = state[1][1]
            else:
                effective_seed = 42

        input_hash = compute_atoms_hash(atoms)
        current_atoms = list(atoms)
        ledger = DecisionLedger(rule_registry=self.registry)
        rules_applied: List[str] = []

        # 1. 针对输入原子构建单次扫描不可变快速索引
        index = OneTimeIndex(atoms)

        # 2. 依次按 execution_order (DAG 拓扑顺序) 执行 17 条规则，使用独立派生 RNG 子流
        for rule_id in self.registry.doc.execution_order:
            rule_item = self.registry.get_rule_item(rule_id)
            resolver_fn = getattr(self, f"_resolve_{rule_id}_atoms", None)
            if resolver_fn is None:
                continue

            rule_rng = derive_substream_rng(effective_seed, f"rule:{rule_item.id}")
            dec_count_before = len(ledger.decisions)
            current_atoms = resolver_fn(current_atoms, rule_item, ledger, rule_rng, index, context_profile)
            if len(ledger.decisions) > dec_count_before:
                if rule_id not in rules_applied:
                    rules_applied.append(rule_id)

        # 3. 计算产出哈希与构建决策报告
        current_atoms = index.get_all_active_ordered()
        output_hash = compute_atoms_hash(current_atoms)
        actual_dropped = sum(1 for d in ledger.decisions if d.action == "drop")
        actual_replaced = sum(1 for d in ledger.decisions if d.action == "replace")
        actual_injected = sum(1 for d in ledger.decisions if d.action == "inject")

        report = ResolutionReport(
            schema_version="1.0",
            input_atom_hash=input_hash,
            output_atom_hash=output_hash,
            input_count=len(atoms),
            output_count=len(current_atoms),
            dropped_count=actual_dropped,
            replaced_count=actual_replaced,
            injected_count=actual_injected,
            rules_applied=tuple(rules_applied),
            decisions=tuple(ledger.decisions),
            unresolved_conflicts=(),
            produced_atoms=tuple(ledger.produced_atoms),
        )

        # 4. 只读最终零硬冲突检测 (detect_hard_conflicts)
        residual_conflicts, has_protected = self.detect_hard_conflicts(current_atoms)
        if residual_conflicts:
            reason = "protected_syntax_conflict" if has_protected else "unresolved_hard_conflict"
            unres_report = replace(report, unresolved_conflicts=tuple(residual_conflicts))
            self.last_report = unres_report
            raise UnresolvedConflictError(
                reason=reason,
                unresolved_conflicts=tuple(residual_conflicts),
                report=unres_report,
            )

        self.last_report = report
        return current_atoms, tuple(rules_applied), report

    def resolve_atoms_with_report(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None,
        context_profile: Optional[ContextProfile] = None,
    ) -> Tuple[List[PromptAtom], Tuple[str, ...]]:
        """向后兼容接口：执行消解并返回 (resolved_atoms, rules_applied)。"""
        resolved, rules_applied, _ = self.resolve_atoms_with_full_report(atoms, rng, context_profile)
        return resolved, rules_applied

    def resolve_atoms(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None,
        context_profile: Optional[ContextProfile] = None,
    ) -> List[PromptAtom]:
        """向后兼容接口：执行消解并返回 resolved_atoms 列表。"""
        resolved, _ = self.resolve_atoms_with_report(atoms, rng, context_profile)
        return resolved

    def resolve_fragments(
        self,
        fragments: Sequence[PromptFragment],
        rng: Optional[Random] = None,
    ) -> List[PromptFragment]:
        """向后兼容接口：消解 PromptFragment 列表。"""
        if not fragments:
            return []
        if rng is None:
            rng = Random(42)

        from .atomizer import atoms_to_fragments, fragments_to_atoms
        _, atoms = fragments_to_atoms(fragments)
        resolved_atoms = self.resolve_atoms(atoms, rng)
        return atoms_to_fragments(resolved_atoms)

    def resolve(
        self,
        slots: Dict[str, Sequence[str]],
        rng: Optional[Random] = None,
    ) -> Dict[str, List[str]]:
        """向后兼容字典接口。"""
        if rng is None:
            rng = Random(42)

        if not isinstance(slots, dict):
            raise TypeError(f"slots must be a dict, got {type(slots).__name__}")

        fragments: List[PromptFragment] = []
        for slot_name, tags in slots.items():
            if not isinstance(slot_name, str):
                raise TypeError(f"slot name must be str, got {type(slot_name).__name__}")
            if not isinstance(tags, (list, tuple)):
                raise TypeError(f"tags for slot {slot_name!r} must be list or tuple, got {type(tags).__name__}")
            for t in tags:
                if not isinstance(t, str):
                    raise TypeError(f"tag in slot {slot_name!r} must be str, got {type(t).__name__}")
                if not t:
                    continue
                fragments.append(
                    PromptFragment(
                        text=t,
                        source_slot=slot_name,
                        order=len(fragments),
                    )
                )

        resolved_frags = self.resolve_fragments(fragments, rng)

        new_slots: Dict[str, List[str]] = {}
        for f in resolved_frags:
            new_slots.setdefault(f.source_slot, []).append(f.text)

        return new_slots

    # ─── 规则 1: spatial_environmental_mutual_exclusion (anchors, 100) ───

    def _resolve_spatial_environmental_mutual_exclusion_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        rule = rule_item.spec
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        venue_cluster_names = tuple(rule.get("venue_clusters", {}).keys()) or get_fallback_group_ids(tf_patterns)
        venue_clusters: Dict[str, Tuple[PatternSpec, ...]] = {
            vname: select_fallback_patterns(tf_patterns, role="venue", group_id=vname)
            for vname in venue_cluster_names
        }
        outdoor_exclusive: Tuple[PatternSpec, ...] = select_fallback_patterns(tf_patterns, role="outdoor", group_id="outdoor")
        indoor_exclusive: Tuple[PatternSpec, ...] = select_fallback_patterns(tf_patterns, role="indoor", group_id="indoor")
        deprecated_tags = [
            d for d in rule.get("deprecated_tags", ())
            if (d.banned.pattern, d.banned.match_mode) in {(p.pattern, p.match_mode) for p in tf_patterns}
        ]
        _fact_indoors = index.get_active_by_fact("space_kind", "indoor")
        _fact_outdoors = index.get_active_by_fact("space_kind", "outdoor")

        # 1. 废弃词无缝迁移 (仅 custom fallback)
        if tf.enabled and deprecated_tags:
            for a in index.get_all_active_ordered():
                if not is_formal_atom(a) and a.can_modify_internal:
                    txt = a.text
                    replaced_any = False
                    for rep in deprecated_tags:
                        if rep.banned.matches(txt):
                            txt = rep.banned.substitute(txt, rep.replacement)
                            replaced_any = True
                    if replaced_any and txt != a.text:
                        new_a = make_replaced_atom(a, txt, rule_item.id)
                        self.text_fallback_hits += 1
                        ledger.record_replace(
                            rule_item.id,
                            rule_item.phase,
                            "deprecated_tag_replaced",
                            (),
                            a,
                            (new_a,),
                        )
                        index.replace(a.atom_id, new_a)

        # 2. 场所集群互斥 (首个出现的 cluster 胜出)
        active_venues: List[Tuple[int, int, str, PromptAtom]] = []
        for vname, vtags in venue_clusters.items():
            for a in index.get_active_by_slot("scene"):
                if a.can_detect and index.is_active(a):
                    self._validate_formal_atom(a, rule_item.id)
                    matched = False
                    if is_formal_atom(a):
                        if a.facts and a.facts.venue_ids:
                            matched = any(vname == vid or vname in vid for vid in a.facts.venue_ids)
                    elif tf.enabled:
                        matched = any(vt.matches(a.text) for vt in vtags)
                    if matched:
                        active_venues.append((a.tag_order, a.span_order, vname, a))
                        break

        active_cluster_names = list(dict.fromkeys(v[2] for v in active_venues))
        if len(active_cluster_names) > 1:
            active_venues.sort(key=lambda x: (x[0], x[1]))
            dominant_venue = active_venues[0][2]
            dominant_atom = active_venues[0][3]
            banned_venues = [v for v in active_cluster_names if v != dominant_venue]
            banned_tags_all: List[PatternSpec] = []
            for bv in banned_venues:
                banned_tags_all.extend(venue_clusters[bv])

            for a in index.get_active_by_slot("scene"):
                if not a.can_detect or not index.is_active(a):
                    continue
                if a.atom_id == dominant_atom.atom_id:
                    # A multi-venue anchor may mention both the dominant and a
                    # banned cluster. The selected winner itself must remain
                    # active for this and subsequent decisions.
                    continue
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and a.facts.venue_ids:
                        if any(bv in a.facts.venue_ids for bv in banned_venues):
                            is_loser = True
                elif tf.enabled:
                    if any(bt.matches(a.text) for bt in banned_tags_all):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "venue_cluster_mutex",
                        (dominant_atom.atom_id,),
                        a,
                    )

        # 3. 室内 / 室外二元互斥 (实际消费事实索引 R2R3-P3-001)
        indoor_atoms = [a for a in index.get_active_by_fact("space_kind", "indoor") if a.can_detect and index.is_active(a)]
        outdoor_atoms = [a for a in index.get_active_by_fact("space_kind", "outdoor") if a.can_detect and index.is_active(a)]
        for a in indoor_atoms + outdoor_atoms:
            self._validate_formal_atom(a, rule_item.id)

        seen_scene_ids = {a.atom_id for a in indoor_atoms + outdoor_atoms}
        for a in index.get_active_by_slot("scene"):
            if a.atom_id in seen_scene_ids or not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and a.facts.space_kind == "indoor":
                    indoor_atoms.append(a)
                elif a.facts and a.facts.space_kind == "outdoor":
                    outdoor_atoms.append(a)
            elif tf.enabled:
                if any(w.matches(a.text) for w in outdoor_exclusive):
                    outdoor_atoms.append(a)
                elif any(w.matches(a.text) for w in indoor_exclusive) or any(any(vt.matches(a.text) for vt in vtags) for vtags in venue_clusters.values()):
                    indoor_atoms.append(a)

        if indoor_atoms and outdoor_atoms:
            first_in = min(indoor_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_out = min(outdoor_atoms, key=lambda x: (x.tag_order, x.span_order))

            if (first_in.tag_order, first_in.span_order) <= (first_out.tag_order, first_out.span_order):
                winner = first_in
                losers = outdoor_atoms
                loser_exclusive_tags = outdoor_exclusive
            else:
                winner = first_out
                losers = indoor_atoms
                loser_exclusive_tags = indoor_exclusive

            for a in index.get_active_by_slot("scene"):
                if not a.can_detect or not index.is_active(a):
                    continue
                is_loser = False
                is_fallback = False
                if a in losers:
                    is_loser = True
                    is_fallback = not is_formal_atom(a)
                elif (
                    not is_formal_atom(a)
                    and tf.enabled
                    and any(w.matches(a.text) for w in loser_exclusive_tags)
                ):
                    is_loser = True
                    is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "indoor_outdoor_mutex",
                        (winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 2: nudity_clothing_conflicts (anchors, 110) ───

    def _resolve_nudity_clothing_conflicts_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        rule = rule_item.spec
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        level_rules = {
            lvl: replace(lr, banned_patterns=select_fallback_patterns(tf_patterns, role="banned", group_id=lvl))
            for lvl, lr in rule.level_rules.items()
        }
        conflicts = [
            TriggerBanConflictSpec(
                trigger=select_fallback_patterns(tf_patterns, role="trigger", group_id=gid),
                ban=select_fallback_patterns(tf_patterns, role="banned", group_id=gid),
            )
            for gid in get_fallback_group_ids(tf_patterns, role="trigger")
        ]
        active_lvl_specs = {
            lvl: ps for lvl, ps in LVL_PATTERN_SPECS.items()
            if (ps.pattern, ps.match_mode) in {(p.pattern, p.match_mode) for p in tf_patterns}
        }
        _nude_fact_atoms = index.get_active_by_fact("coverage_level", "L6")

        # 1. 裸露等级决定允许的最大服装穿着
        nudity_atoms = index.get_active_by_slot("nudity")
        detected_nudity: List[Tuple[str, PromptAtom]] = []

        for a in nudity_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            matched_lvl = None
            if is_formal_atom(a):
                raw_id = (((a.provenance.item_id if a.provenance else "") or "") + " " + (a.source_item_id or "") + " " + ((a.origin.selected_id if a.origin else "") or "")).upper()
                for lvl in ("L6", "L5", "L4", "L3", "L2", "L1"):
                    if lvl in raw_id:
                        matched_lvl = lvl
                        break
                if not matched_lvl and a.facts:
                    if "full_body" in a.facts.visible_regions:
                        matched_lvl = "L5"
                    elif any(r in a.facts.visible_regions for r in ("crotch", "buttocks")):
                        matched_lvl = "L5"
                    elif any(r in a.facts.visible_regions for r in ("breasts", "topless")):
                        matched_lvl = "L4"
                    elif any(r in a.facts.visible_regions for r in ("cleavage", "midriff")):
                        matched_lvl = "L2"
            elif tf.enabled:
                for lvl, ps in active_lvl_specs.items():
                    if ps.matches(a.text):
                        matched_lvl = lvl
                        break
            if matched_lvl:
                detected_nudity.append((matched_lvl, a))

        if detected_nudity:
            detected_nudity.sort(key=lambda x: (x[1].tag_order, x[1].span_order))
            dominant_lvl, nudity_winner = detected_nudity[0]

            candidates = [
                a for a in index.get_all_active_ordered()
                if a.source_slot != "nudity" and a.atom_id != nudity_winner.atom_id
            ]

            for a in candidates:
                if not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                should_drop = False
                is_fallback = False

                if dominant_lvl in ("L6", "L5"):
                    # 全裸与极致全裸：禁止一切常规穿着
                    if is_formal_atom(a):
                        if a.facts and (a.facts.garment_topologies or a.facts.garment_states):
                            should_drop = True
                        elif a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
                            should_drop = True
                    elif tf.enabled:
                        if any(b.matches(a.text) for b in level_rules["L5"].banned_patterns):
                            should_drop = True
                            is_fallback = True
                elif dominant_lvl == "L4":
                    # 微型覆盖/比基尼：禁止外穿大衣/整套长裙，但不删轻量内衣
                    if is_formal_atom(a):
                        if a.facts and any(t in a.facts.garment_topologies for t in ("suit", "coat", "jacket", "hoodie", "one_piece", "pants", "blazer", "sweater")):
                            should_drop = True
                    elif tf.enabled:
                        if any(b.matches(a.text) for b in level_rules["L4"].banned_patterns):
                            should_drop = True
                            is_fallback = True
                elif dominant_lvl == "L3":
                    if is_formal_atom(a):
                        if a.facts and any(r in a.facts.visible_regions for r in ("crotch", "buttocks", "intimate_lower_body")):
                            should_drop = True
                    elif tf.enabled:
                        if any(b.matches(a.text) for b in level_rules["L3"].banned_patterns):
                            should_drop = True
                            is_fallback = True
                elif dominant_lvl in ("L1", "L2"):
                    if is_formal_atom(a):
                        item_id = ((a.source_item_id or "") + " " + ((a.provenance.item_id or "") if a.provenance else "") + " " + ((a.origin.selected_id or "") if a.origin else "")).lower()
                        if a.facts and any(r in a.facts.visible_regions for r in ("intimate_lower_body",)):
                            should_drop = True
                        elif dominant_lvl == "L1" and (
                            "erotic_close_up" in item_id
                            or (a.facts and any(s in a.facts.garment_states for s in ("lifted", "opened", "removed", "lifted_skirt")))
                            or (a.facts and any(r in a.facts.visible_regions for r in ("cleavage", "breasts", "crotch", "buttocks", "underboob", "sideboob")))
                        ):
                            should_drop = True
                    elif tf.enabled:
                        lvl_rule = level_rules.get(dominant_lvl)
                        if lvl_rule and any(b.matches(a.text) for b in lvl_rule.banned_patterns):
                            should_drop = True
                            is_fallback = True

                if should_drop:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    is_underwear = (
                        "underwear" in a.source_slot
                        or (a.facts and any(t in a.facts.garment_topologies for t in ("underwear", "panties", "bra", "bikini", "lingerie")))
                        or any(u in (a.source_item_id or "").lower() for u in ("underwear", "bikini", "panties", "bra", "lingerie"))
                        or any(u in ((a.provenance.item_id if a.provenance else "") or "").lower() for u in ("underwear", "bikini", "panties", "bra", "lingerie"))
                    )
                    rc = "nudity_removes_underwear" if is_underwear else "nudity_removes_clothing"
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        rc,
                        (nudity_winner.atom_id,),
                        a,
                    )

        # 2. Trigger-Ban 细粒度互斥 (R2-N07: 不直接裸露抛错，保护结构留给最终检测器)
        if conflicts:
            for c in conflicts:
                triggers = c.trigger
                bans = c.ban
                trig_atoms = []
                for a in index.get_all_active_ordered():
                    if not a.can_detect or not index.is_active(a):
                        continue
                    if is_formal_atom(a):
                        if a.facts and any(r in a.facts.visible_regions for r in ("crotch", "breasts", "buttocks", "pubic", "full_body")):
                            trig_atoms.append(a)
                    elif tf.enabled:
                        if any(t.matches(a.text) for t in triggers):
                            trig_atoms.append(a)

                if trig_atoms:
                    first_trig = min(trig_atoms, key=lambda x: (x.tag_order, x.span_order))
                    for a in index.get_all_active_ordered():
                        if a in trig_atoms or not a.can_detect or not index.is_active(a):
                            continue
                        is_banned = False
                        is_fallback = False
                        if is_formal_atom(a):
                            if a.facts and any(t in a.facts.garment_topologies for t in ("panties", "bra", "underwear", "suit", "blouse", "dress")):
                                is_banned = True
                        elif tf.enabled:
                            if any(b.matches(a.text) for b in bans):
                                is_banned = True
                                is_fallback = True

                        if is_banned:
                            if not a.can_delete_atom:
                                continue
                            if is_fallback:
                                self.text_fallback_hits += 1
                            index.drop(a.atom_id)
                            ledger.record_drop(
                                rule_item.id,
                                rule_item.phase,
                                "nudity_removes_underwear",
                                (first_trig.atom_id,),
                                a,
                            )

        return index.get_all_active_ordered()

    # ─── 规则 3: framing_lower_body_coherence (anchors, 120) ───

    def _resolve_framing_lower_body_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        all_cu_triggers = select_fallback_patterns(tf_patterns, role="trigger", group_id="framing")
        all_lb_patterns = select_fallback_patterns(tf_patterns, role="banned", group_id="framing")
        cu_atoms = [
            a for a in (index.get_active_by_item_id("extreme_close_up") + index.get_active_by_item_id("close_up"))
            if a.can_detect and index.is_active(a)
        ]
        cu_ids = {a.atom_id for a in cu_atoms}
        for a in (index.get_active_by_slot("shot_type") + index.get_active_by_slot("shot")):
            if a.atom_id in cu_ids or not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                item_id = (a.provenance.item_id or a.source_item_id or "") if a.provenance else (a.source_item_id or "")
                if item_id in ("extreme_close_up", "close_up"):
                    cu_atoms.append(a)
                    cu_ids.add(a.atom_id)
                elif item_id in ("medium_close_up", "medium_shot", "cowboy_shot", "full_body", "wide_shot", "extreme_wide"):
                    pass
                elif a.facts and "face" in a.facts.visible_regions and not any(r in a.facts.visible_regions for r in ("upper_body", "lower_body", "legs", "feet")):
                    cu_atoms.append(a)
                    cu_ids.add(a.atom_id)
            elif tf.enabled:
                if any(t.matches(a.text) for t in all_cu_triggers):
                    cu_atoms.append(a)
                    cu_ids.add(a.atom_id)

        if cu_atoms:
            cu_winner = min(cu_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in index.get_active_by_slot("clothing"):
                if a in cu_atoms or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and any(r in a.facts.visible_regions for r in ("feet", "legs", "lower_body", "shoes")):
                        is_loser = True
                elif tf.enabled:
                    if any(p.matches(a.text) for p in all_lb_patterns):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "close_up_removes_lower_body",
                        (cu_winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 4: pose_hand_occupation (physical, 200) ───

    def _resolve_pose_hand_occupation_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        all_busy_pose = select_fallback_patterns(tf_patterns, role="trigger", group_id="pose_hand")
        all_handheld = select_fallback_patterns(tf_patterns, role="handheld", group_id="pose_hand")
        busy_fact_atoms = (
            index.get_active_by_fact("hand_state", "both_busy")
            + index.get_active_by_fact("hand_state", "one_busy")
            + index.get_active_by_mutex_group("busy_hands")
        )

        busy_atoms = []
        seen_busy_ids = set()
        for a in busy_fact_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_busy_ids.add(a.atom_id)
            busy_atoms.append(a)

        for a in index.get_active_by_slot("pose"):
            if a.atom_id in seen_busy_ids:
                continue
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and a.facts.hand_state == "both_busy":
                    busy_atoms.append(a)
            elif tf.enabled:
                if any(t.matches(a.text) for t in all_busy_pose):
                    busy_atoms.append(a)

        if busy_atoms:
            pose_winner = min(busy_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in index.get_active_by_slot("props"):
                if a in busy_atoms or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"):
                        is_loser = True
                elif tf.enabled:
                    if any(p.matches(a.text) for p in all_handheld):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "busy_hands_remove_props",
                        (pose_winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 5: handheld_props_single_holder (physical, 210) ───

    def _resolve_handheld_props_single_holder_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns if (tf and tf.enabled) else ()
        handheld_patterns = select_fallback_patterns(tf_patterns, role="handheld", group_id="handheld")

        # 实际消费事实索引 (R2R3-P3-001)
        hh_fact_atoms = index.get_active_by_fact("prop_usage", "handheld")
        detected_hh: List[Tuple[PromptAtom, bool]] = []
        seen_ids = set()

        for a in hh_fact_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_ids.add(a.atom_id)
            detected_hh.append((a, False))

        for a in index.get_active_by_slot("props"):
            if a.atom_id in seen_ids or not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"):
                    seen_ids.add(a.atom_id)
                    detected_hh.append((a, False))
            elif tf.enabled:
                if any(hp.matches(a.text) for hp in handheld_patterns):
                    seen_ids.add(a.atom_id)
                    detected_hh.append((a, True))

        if len(detected_hh) > 1:
            detected_hh.sort(key=lambda x: (x[0].tag_order, x[0].span_order))
            winner_atom, _ = detected_hh[0]
            for loser_atom, is_fallback in detected_hh[1:]:
                if not loser_atom.can_delete_atom or not index.is_active(loser_atom):
                    continue
                if is_fallback:
                    self.text_fallback_hits += 1
                index.drop(loser_atom.atom_id)
                ledger.record_drop(
                    rule_item.id,
                    rule_item.phase,
                    "single_handheld_prop_limit",
                    (winner_atom.atom_id,),
                    loser_atom,
                )

        return index.get_all_active_ordered()

    # ─── 规则 6: clothing_style_state_coherence (physical, 220) ───

    def _resolve_clothing_style_state_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        one_piece_triggers = select_fallback_patterns(tf_patterns, role="trigger", group_id="one_piece")
        one_piece_banned_states = select_fallback_patterns(tf_patterns, role="banned", group_id="one_piece")
        pants_triggers = select_fallback_patterns(tf_patterns, role="trigger", group_id="pants")
        pants_banned_states = select_fallback_patterns(tf_patterns, role="banned", group_id="pants")

        active_atoms = index.get_all_active_ordered()
        entities_map = extract_garment_entities(active_atoms)

        # 0. 优先处理 discarded 状态（精准锁定唯一目标实体并注销其在穿承载资格）
        discarded_atoms = [
            a for a in active_atoms
            if index.is_active(a) and (
                (a.origin and a.origin.selected_id == "discarded")
                or (a.facts and "discarded" in a.facts.garment_states)
                or a.source_item_id == "discarded"
            )
        ]
        for da in discarded_atoms:
            binding = find_bound_carrier(da, list(entities_map.values()))
            if binding.status == BindingStatus.BOUND and binding.target_entity:
                target_entity = binding.target_entity
                target_entity.is_worn = False
                target_entity.is_ambient = True
                target_entity.discarded_by = da

        # 获取在穿且非环境物实体
        worn_entities = [e for e in entities_map.values() if e.is_worn and not e.is_ambient]
        clothing_atoms = [a for a in active_atoms if a.source_slot in ("clothing", "clothing_state") and index.is_active(a)]

        # 1. 连体衣与解扣/掀裙等结构状态互斥判定
        op_entities = [
            e for e in worn_entities
            if any(ma.facts and "one_piece" in ma.facts.garment_topologies for ma in e.member_atoms)
        ]
        op_tf_atoms = []
        if tf.enabled:
            for a in clothing_atoms:
                if not is_formal_atom(a) and any(opt.matches(a.text) for opt in one_piece_triggers):
                    op_tf_atoms.append(a)

        if op_entities or op_tf_atoms:
            all_op_atoms = [a for e in op_entities for a in e.member_atoms] + op_tf_atoms
            if all_op_atoms:
                op_winner = min(all_op_atoms, key=lambda x: (x.tag_order, x.span_order))
                for a in clothing_atoms:
                    if a in all_op_atoms or not a.can_detect or not index.is_active(a):
                        continue
                    is_loser = False
                    is_fallback = False
                    if is_formal_atom(a):
                        state_id = get_canonical_state_id(a)
                        if state_id == "unbuttoned":
                            # 使用四阶绑定阶梯检查解扣目标
                            binding = find_bound_carrier(a, worn_entities)
                            if binding.status == BindingStatus.BOUND and binding.target_entity:
                                # 若绑定的目标实体自身不支持纽扣能力，则判定冲突
                                if binding.target_entity.selected_id not in ALLOWED_BUTTON_STYLES:
                                    is_loser = True
                            elif binding.status in (BindingStatus.UNBOUND_NO_CANDIDATE, BindingStatus.UNBOUND_INCOMPATIBLE, BindingStatus.UNBOUND_TARGET_NOT_FOUND):
                                if any(oe.selected_id not in ALLOWED_BUTTON_STYLES for oe in op_entities):
                                    is_loser = True
                        elif state_id == "unzipped":
                            # 使用四阶绑定阶梯检查拉链目标
                            binding = find_bound_carrier(a, worn_entities)
                            if binding.status == BindingStatus.BOUND and binding.target_entity:
                                if binding.target_entity.selected_id not in ALLOWED_ZIPPER_STYLES:
                                    is_loser = True
                            elif binding.status in (BindingStatus.UNBOUND_NO_CANDIDATE, BindingStatus.UNBOUND_INCOMPATIBLE, BindingStatus.UNBOUND_TARGET_NOT_FOUND):
                                if any(oe.selected_id not in ALLOWED_ZIPPER_STYLES for oe in op_entities):
                                    is_loser = True
                        elif state_id == "lifted_up" or (a.facts and any(s in a.facts.garment_states for s in ("lifted", "lifted_up", "lifted_skirt"))):
                            binding = find_bound_carrier(a, worn_entities)
                            if binding.status == BindingStatus.BOUND and binding.target_entity:
                                if binding.target_entity.selected_id in NON_SKIRT_ONE_PIECE:
                                    is_loser = True
                            elif binding.status in (BindingStatus.UNBOUND_NO_CANDIDATE, BindingStatus.UNBOUND_INCOMPATIBLE, BindingStatus.UNBOUND_TARGET_NOT_FOUND):
                                if any(oe.selected_id in NON_SKIRT_ONE_PIECE for oe in op_entities):
                                    is_loser = True
                        elif a.facts and "removed" in a.facts.garment_states:
                            is_loser = True
                    elif tf.enabled:
                        if any(bs.matches(a.text) for bs in one_piece_banned_states):
                            is_loser = True
                            is_fallback = True

                    if is_loser:
                        if not a.can_delete_atom:
                            continue
                        if is_fallback:
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "one_piece_state_conflict",
                            (op_winner.atom_id,),
                            a,
                        )

        # 2. 裤装与掀裙状态互斥判定
        pants_entities = [
            e for e in worn_entities
            if any(ma.facts and any(t in ma.facts.garment_topologies for t in ("bottom_pants", "pants", "jeans", "shorts", "trousers")) for ma in e.member_atoms)
        ]
        pants_tf_atoms = []
        if tf.enabled:
            for a in clothing_atoms:
                if not is_formal_atom(a) and any(pt.matches(a.text) for pt in pants_triggers):
                    pants_tf_atoms.append(a)

        if pants_entities or pants_tf_atoms:
            all_pants_atoms = [a for e in pants_entities for a in e.member_atoms] + pants_tf_atoms
            if all_pants_atoms:
                pants_winner = min(all_pants_atoms, key=lambda x: (x.tag_order, x.span_order))
                has_skirt_entity = any(
                    any(ma.facts and "bottom_skirt" in ma.facts.garment_topologies for ma in e.member_atoms)
                    for e in worn_entities
                )
                for a in clothing_atoms:
                    if a in all_pants_atoms or not a.can_detect or not index.is_active(a):
                        continue
                    is_loser = False
                    is_fallback = False
                    if is_formal_atom(a):
                        state_id = get_canonical_state_id(a)
                        if (
                            state_id in ("lifted_up", "lifted")
                            or (a.facts and any(s in a.facts.garment_states for s in ("lifted", "lifted_up", "skirt_slit", "pleated_skirt", "skirt_floating", "slit", "lifted_skirt")))
                        ):
                            if not has_skirt_entity:
                                is_loser = True
                    elif tf.enabled:
                        if any(bs.matches(a.text) for bs in pants_banned_states):
                            if not has_skirt_entity:
                                is_loser = True
                                is_fallback = True

                    if is_loser:
                        if not a.can_delete_atom:
                            continue
                        if is_fallback:
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "pants_state_conflict",
                            (pants_winner.atom_id,),
                            a,
                        )

        # 3. 叠穿状态检查 (skirt_under_kimono, undergarment_leotard)
        layering_atoms = [
            a for a in active_atoms
            if index.is_active(a) and is_formal_atom(a) and (
                a.source_item_id in CANONICAL_LAYERING_STATES
                or (a.origin and a.origin.selected_id in CANONICAL_LAYERING_STATES)
            )
        ]
        for la in layering_atoms:
            if not index.is_active(la) or not la.can_delete_atom:
                continue
            l_id = la.source_item_id or (la.origin.selected_id if la.origin else "")
            if l_id == "skirt_under_kimono":
                kimono_entities = [e for e in worn_entities if e.selected_id in ("kimono", "yukata", "furisode", "robe_general", "taoist_robe", "battle_robe")]
                skirt_entities = [
                    e for e in worn_entities
                    if any(ma.facts and "bottom_skirt" in ma.facts.garment_topologies for ma in e.member_atoms)
                ]
                has_distinct = any(k.entity_id != s.entity_id for k in kimono_entities for s in skirt_entities)
                if not has_distinct:
                    index.drop(la.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "layering_mismatch",
                        (),
                        la,
                    )
            elif l_id == "undergarment_leotard":
                leotard_entities = [e for e in worn_entities if e.selected_id in ("leotard_bodysuit", "latex_catsuit", "zentai_suit")]
                outer_entities = [e for e in worn_entities if e.selected_id not in ("leotard_bodysuit", "latex_catsuit", "zentai_suit")]
                has_distinct = any(leo.entity_id != out.entity_id for leo in leotard_entities for out in outer_entities)
                if not has_distinct:
                    index.drop(la.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "layering_mismatch",
                        (),
                        la,
                    )

        # 4. 缺席状态检查 (braless, underwearless)
        absence_atoms = [
            a for a in active_atoms
            if index.is_active(a) and is_formal_atom(a) and (
                a.source_item_id in CANONICAL_ABSENCE_STATES
                or (a.origin and a.origin.selected_id in CANONICAL_ABSENCE_STATES)
            )
        ]
        for aa in absence_atoms:
            if not index.is_active(aa) or not aa.can_delete_atom:
                continue
            ab_id = aa.source_item_id or (aa.origin.selected_id if aa.origin else "")
            if ab_id == "braless":
                bra_entities = [
                    e for e in worn_entities
                    if e.selected_id in ("lingerie_lace", "bikini_classic", "bikini_strappy", "bikini_micro")
                    or any(ma.facts and "underwear" in ma.facts.garment_topologies and "top" in ma.facts.garment_topologies for ma in e.member_atoms)
                ]
                if bra_entities:
                    winner_ids = (bra_entities[0].member_atoms[0].atom_id,)
                    index.drop(aa.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "absence_state_conflict",
                        winner_ids,
                        aa,
                    )
            elif ab_id == "underwearless":
                panties_entities = [
                    e for e in worn_entities
                    if e.selected_id in ("basic_underwear", "crotchless_panties", "bikini_classic", "bikini_strappy", "bikini_micro")
                    or any(ma.facts and "underwear" in ma.facts.garment_topologies and ("bottom" in ma.facts.garment_topologies or "bottom_pants" in ma.facts.garment_topologies) for ma in e.member_atoms)
                ]
                if panties_entities:
                    winner_ids = (panties_entities[0].member_atoms[0].atom_id,)
                    index.drop(aa.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "absence_state_conflict",
                        winner_ids,
                        aa,
                    )

        # 5. 承载主体检查 (基于四阶绑定阶梯检查在穿承载物)
        for a in active_atoms:
            if not index.is_active(a) or not a.can_detect or not a.can_delete_atom:
                continue

            if not is_formal_atom(a):
                continue

            if not is_garment_modifier_atom(a):
                continue

            binding = find_bound_carrier(a, worn_entities)
            if binding.status in (
                BindingStatus.UNBOUND_NO_CANDIDATE,
                BindingStatus.UNBOUND_TARGET_NOT_FOUND,
                BindingStatus.UNBOUND_INCOMPATIBLE,
            ):
                index.drop(a.atom_id)
                winner_ids = ()
                if binding.target_entity:
                    winner_ids = tuple(m.atom_id for m in binding.target_entity.member_atoms)
                ledger.record_drop(
                    rule_item.id,
                    rule_item.phase,
                    "state_lacks_carrier",
                    winner_ids,
                    a,
                )

        return index.get_all_active_ordered()

    # ─── 规则 7: material_penetration (physical, 230) ───

    def _resolve_material_penetration_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        rule = rule_item.spec
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        banned_words = select_fallback_patterns(tf_patterns, role="banned", group_id="material")
        replacements = rule.replacements
        _sheer_fact_atoms = index.get_active_by_fact("garment_states", "sheer")

        clothing_atoms = index.get_active_by_slot("clothing") + index.get_active_by_slot("clothing_state")
        for a in clothing_atoms:
            if not index.is_active(a) or not a.can_modify_internal or not a.can_detect:
                continue
            if (
                a.source_slot == "clothing_extension"
                or (a.provenance and a.provenance.kind == "clothing_extension")
            ):
                continue
            if a.provenance and a.provenance.rule_id == rule_item.id:
                continue

            self._validate_formal_atom(a, rule_item.id)
            matched = False
            is_fallback = False
            if is_formal_atom(a):
                if a.facts and any(s in a.facts.garment_states for s in ("sheer", "see_through", "transparent", "translucent", "wet_clinging")):
                    matched = True
                elif a.source_item_id in ("sheer_chiffon", "sheer_mesh", "semi_translucent"):
                    matched = True
            elif tf.enabled:
                if any(bw.matches(a.text) for bw in banned_words):
                    matched = True
                    is_fallback = True

            if matched and replacements:
                rep_text = rng.choice(replacements)
                new_facts = None
                if a.facts:
                    clean_states = tuple(s for s in a.facts.garment_states if s not in ("sheer", "see_through", "transparent", "translucent"))
                    new_facts = replace(a.facts, garment_states=clean_states)
                new_a = make_replaced_atom(a, rep_text, rule_item.id, new_facts=new_facts)
                if is_fallback:
                    self.text_fallback_hits += 1
                index.replace(a.atom_id, new_a)
                ledger.record_replace(
                    rule_item.id,
                    rule_item.phase,
                    "material_penetration_replaced",
                    (),
                    a,
                    (new_a,),
                )

        return index.get_all_active_ordered()

    # ─── 规则 8: device_quality_compatibility (physical, 240) ───

    def _resolve_device_quality_compatibility_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        constraints: Tuple[DeviceConstraintSpec, ...] = tuple(
            DeviceConstraintSpec(
                devices=select_fallback_patterns(tf_patterns, role="device", group_id=gid),
                banned_tags=select_fallback_patterns(tf_patterns, role="banned", group_id=gid),
            )
            for gid in get_fallback_group_ids(tf_patterns, role="device")
        )
        cctv_fact_atoms = index.get_active_by_fact("capture_device", "cctv")

        dev_atoms = []
        seen_dev_ids = set()
        for a in cctv_fact_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_dev_ids.add(a.atom_id)
            dev_atoms.append(a)

        for a in (index.get_active_by_slot("shot_type") + index.get_active_by_slot("shot")):
            if a.atom_id in seen_dev_ids:
                continue
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            matched = False
            if is_formal_atom(a):
                if a.facts and a.facts.capture_device in ("cctv", "vhs", "polaroid", "webcam"):
                    matched = True
                elif a.source_item_id in ("cctv", "vhs", "polaroid", "webcam"):
                    matched = True
            elif tf.enabled:
                for c in constraints:
                    if any(dp.matches(a.text) for dp in c.devices):
                        matched = True
                        break
            if matched:
                dev_atoms.append(a)

        if dev_atoms:
            dev_winner = min(dev_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in index.get_active_by_slot("quality"):
                if a in dev_atoms or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and a.facts.quality_class in ("masterpiece", "ultra_detailed", "high_res", "best_quality"):
                        is_loser = True
                elif tf.enabled:
                    for c in constraints:
                        if any(bt.matches(a.text) for bt in c.banned_tags):
                            is_loser = True
                            is_fallback = True
                            break

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "device_removes_conflicting_quality",
                        (dev_winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 9: environmental_lighting_coherence (physical, 250) ───

    def _resolve_environmental_lighting_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        all_daylight = select_fallback_patterns(tf_patterns, role="trigger", group_id="daylight")
        all_night = select_fallback_patterns(tf_patterns, role="banned", group_id="daylight")
        _night_atoms = index.get_active_by_fact("time_of_day", "night")
        _day_atoms = index.get_active_by_fact("time_of_day", "day")

        night_scene_atoms = []
        for a in index.get_active_by_slots("scene", "scene_theme", "theme"):
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and a.facts.time_of_day in ("night", "midnight", "late_night"):
                    night_scene_atoms.append(a)
            elif tf.enabled:
                if any(nt.matches(a.text) for nt in all_night):
                    night_scene_atoms.append(a)

        if night_scene_atoms:
            night_winner = min(night_scene_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in index.get_active_by_slots("lighting", "lighting_palette"):
                if a in night_scene_atoms or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and (a.facts.time_of_day in ("morning", "noon", "afternoon", "day") or any(ls in ("sunlight", "daylight", "direct_sun") for ls in a.facts.light_sources)):
                        is_loser = True
                elif tf.enabled:
                    if any(dt.matches(a.text) for dt in all_daylight):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "night_scene_removes_daylight",
                        (night_winner.atom_id,),
                        a,
                    )
        else:
            daylight_atoms = []
            for a in index.get_active_by_slots("scene", "scene_theme", "theme", "lighting", "lighting_palette"):
                if not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                if is_formal_atom(a):
                    if a.facts and (a.facts.time_of_day in ("morning", "noon", "afternoon", "day") or any(ls in ("sunlight", "daylight", "direct_sun") for ls in a.facts.light_sources)):
                        daylight_atoms.append(a)
                elif tf.enabled:
                    if any(dt.matches(a.text) for dt in all_daylight):
                        daylight_atoms.append(a)

            if daylight_atoms:
                daylight_winner = min(daylight_atoms, key=lambda x: (x.tag_order, x.span_order))
                for a in index.get_active_by_slots("lighting", "lighting_palette"):
                    if a in daylight_atoms or not a.can_detect or not index.is_active(a):
                        continue
                    self._validate_formal_atom(a, rule_item.id)
                    is_loser = False
                    is_fallback = False
                    if is_formal_atom(a):
                        if a.facts and a.facts.time_of_day in ("night", "midnight", "late_night"):
                            is_loser = True
                    elif tf.enabled:
                        if any(nt.matches(a.text) for nt in all_night):
                            is_loser = True
                            is_fallback = True

                    if is_loser:
                        if not a.can_delete_atom:
                            continue
                        if is_fallback:
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "daylight_removes_night",
                            (daylight_winner.atom_id,),
                            a,
                        )

        return index.get_all_active_ordered()

    # ─── 规则 10: monochrome_film_chroma_coherence (physical, 260) ───

    def _resolve_monochrome_film_chroma_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        monochrome_triggers = select_fallback_patterns(tf_patterns, role="trigger", group_id="monochrome")
        banned_chroma = select_fallback_patterns(tf_patterns, role="banned", group_id="monochrome")
        mono_fact_atoms = index.get_active_by_fact("color_modes", "monochrome")

        mono_atoms = []
        seen_mono_ids = set()
        for a in mono_fact_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_mono_ids.add(a.atom_id)
            mono_atoms.append(a)

        for a in index.get_active_by_slots("film", "film_stock"):
            if a.atom_id in seen_mono_ids or not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and "monochrome" in a.facts.color_modes:
                    seen_mono_ids.add(a.atom_id)
                    mono_atoms.append(a)
            elif tf.enabled:
                if any(t.matches(a.text) for t in monochrome_triggers):
                    seen_mono_ids.add(a.atom_id)
                    mono_atoms.append(a)

        if mono_atoms:
            mono_winner = min(mono_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in index.get_active_by_slots("lighting", "lighting_palette", "film", "film_stock"):
                if a in mono_atoms or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and any(c in a.facts.color_modes for c in ("color", "high_saturation", "neon")):
                        is_loser = True
                elif tf.enabled:
                    if any(b.matches(a.text) for b in banned_chroma):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "monochrome_film_removes_chroma",
                        (mono_winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 11: makeup_details_coherence (physical, 270) ───

    def _resolve_makeup_details_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        no_makeup_triggers = select_fallback_patterns(tf_patterns, role="trigger", group_id="no_makeup")
        banned_makeup_smudge = select_fallback_patterns(tf_patterns, role="banned", group_id="no_makeup")
        bare_fact_atoms = index.get_active_by_fact("makeup_base", "bare")

        clean_atoms = []
        seen_clean_ids = set()
        for a in bare_fact_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_clean_ids.add(a.atom_id)
            clean_atoms.append(a)

        makeup_atoms = index.get_active_by_slot("makeup")
        for a in makeup_atoms:
            if a.atom_id in seen_clean_ids:
                continue
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and a.facts.makeup_base in ("bare", "clean", "natural"):
                    clean_atoms.append(a)
            elif tf.enabled:
                if any(t.matches(a.text) for t in no_makeup_triggers):
                    clean_atoms.append(a)

        if clean_atoms:
            clean_winner = min(clean_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in makeup_atoms:
                if a.atom_id == clean_winner.atom_id or not a.can_detect or not index.is_active(a):
                    continue
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and any(e in a.facts.makeup_effects for e in ("heavy", "smudged", "runny", "smeared")):
                        is_loser = True
                elif tf.enabled:
                    if any(b.matches(a.text) for b in banned_makeup_smudge):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "clean_base_removes_heavy_makeup",
                        (clean_winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 12: gaze_angle_geometry (semantic, 300) ───

    def _resolve_gaze_angle_geometry_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        mappings: Tuple[AngleGazeMappingSpec, ...] = tuple(
            AngleGazeMappingSpec(
                angles=select_fallback_patterns(tf_patterns, role="angle", group_id=gid),
                banned_gaze=select_fallback_patterns(tf_patterns, role="banned", group_id=gid),
            )
            for gid in get_fallback_group_ids(tf_patterns, role="angle")
        )
        _angle_down_atoms = index.get_active_by_fact("gaze", "down")

        camera_atoms = (
            index.get_active_by_slot("camera_angle")
            + index.get_active_by_slot("camera")
            + index.get_active_by_slot("shot_type")
        )
        expression_atoms = index.get_active_by_slot("expression")

        for mapping in mappings:
            ang_atoms = []
            for a in camera_atoms:
                if not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                if is_formal_atom(a):
                    item_id = (a.provenance.item_id or a.source_item_id or "") if a.provenance else (a.source_item_id or "")
                    if item_id in ("overhead", "top_down", "birds_eye") or (a.facts and a.facts.gaze in ("down", "looking_down_from_above")):
                        ang_atoms.append(a)
                elif tf.enabled:
                    if any(ag.matches(a.text) for ag in mapping.angles):
                        ang_atoms.append(a)

            if ang_atoms:
                ang_winner = min(ang_atoms, key=lambda x: (x.tag_order, x.span_order))
                for a in expression_atoms:
                    if a in ang_atoms or not a.can_detect or not index.is_active(a):
                        continue
                    self._validate_formal_atom(a, rule_item.id)
                    is_loser = False
                    is_fallback = False
                    if is_formal_atom(a):
                        if a.facts and a.facts.gaze in ("down", "looking_down_from_above"):
                            is_loser = True
                    elif tf.enabled:
                        if any(bg.matches(a.text) for bg in mapping.banned_gaze):
                            is_loser = True
                            is_fallback = True

                    if is_loser:
                        if not a.can_delete_atom:
                            continue
                        if is_fallback:
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "camera_angle_removes_impossible_gaze",
                            (ang_winner.atom_id,),
                            a,
                        )

        return index.get_all_active_ordered()

    # ─── 规则 13: accessory_occlusion_gaze_coherence (semantic, 310) ───

    def _resolve_accessory_occlusion_gaze_coherence_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        occlusion_triggers = select_fallback_patterns(tf_patterns, role="trigger", group_id="occlusion")
        banned_gaze_actions = select_fallback_patterns(tf_patterns, role="banned", group_id="occlusion")
        _occ_eyes_atoms = index.get_active_by_fact("occlusion", "eyes")

        occ_atoms = []
        jewelry_atoms = index.get_active_by_slots("jewelry", "accessories")
        expression_atoms = index.get_active_by_slots("expression", "expressions")

        for a in jewelry_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a):
                if a.facts and a.facts.occlusion in ("eyes", "face"):
                    occ_atoms.append(a)
            elif tf.enabled:
                if any(t.matches(a.text) for t in occlusion_triggers):
                    occ_atoms.append(a)

        if occ_atoms:
            occ_winner = min(occ_atoms, key=lambda x: (x.tag_order, x.span_order))
            for a in expression_atoms:
                if a in occ_atoms or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if is_formal_atom(a):
                    if a.facts and a.facts.gaze in ("camera", "viewer", "direct"):
                        is_loser = True
                elif tf.enabled:
                    if any(b.matches(a.text) for b in banned_gaze_actions):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "eye_occlusion_removes_gaze",
                        (occ_winner.atom_id,),
                        a,
                    )

        return index.get_all_active_ordered()

    # ─── 规则 14: emotion_gaze_affinity (semantic, 320) ───

    def _resolve_emotion_gaze_affinity_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        conflicts: Tuple[EmotionGazeConflictSpec, ...] = tuple(
            EmotionGazeConflictSpec(
                catalog_emotion_triggers=(),
                custom_emotion_triggers=select_fallback_patterns(tf_patterns, role="emotion", group_id=gid),
                catalog_banned_gaze=(),
                custom_banned_gaze=select_fallback_patterns(tf_patterns, role="banned", group_id=gid),
            )
            for gid in get_fallback_group_ids(tf_patterns, role="emotion")
        )
        shy_fact_atoms = [a for a in index.get_active_by_fact("emotion", "shy") if a.can_detect and index.is_active(a)]
        all_active = index.get_all_active_ordered()

        for c in conflicts:
            emo_atoms = list(shy_fact_atoms)
            seen_emo_ids = {a.atom_id for a in emo_atoms}
            for a in all_active:
                if a.atom_id in seen_emo_ids or not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                if is_formal_atom(a):
                    if a.facts and a.facts.emotion == "shy":
                        seen_emo_ids.add(a.atom_id)
                        emo_atoms.append(a)
                elif tf.enabled:
                    if any(t.matches(a.text) for t in c.emotion_triggers):
                        seen_emo_ids.add(a.atom_id)
                        emo_atoms.append(a)

            if emo_atoms:
                emo_winner = min(emo_atoms, key=lambda x: (x.tag_order, x.span_order))
                for a in all_active:
                    if a in emo_atoms or not a.can_detect or not index.is_active(a):
                        continue
                    self._validate_formal_atom(a, rule_item.id)
                    is_loser = False
                    is_fallback = False
                    if is_formal_atom(a):
                        if a.facts and a.facts.emotion in ("seductive", "dominant"):
                            is_loser = True
                    elif tf.enabled:
                        if any(b.matches(a.text) for b in c.banned_gaze):
                            is_loser = True
                            is_fallback = True

                    if is_loser:
                        if not a.can_delete_atom:
                            continue
                        if is_fallback:
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "emotion_removes_conflicting_gaze",
                            (emo_winner.atom_id,),
                            a,
                        )

        return index.get_all_active_ordered()

    # ─── 规则 15: gaze_mutual_exclusion (semantic, 330) ───

    def _resolve_gaze_mutual_exclusion_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        pairs = [
            (p1[0], p2[0])
            for gid in get_fallback_group_ids(tf_patterns, role="exclusive_a")
            for p1 in [select_fallback_patterns(tf_patterns, role="exclusive_a", group_id=gid)]
            for p2 in [select_fallback_patterns(tf_patterns, role="exclusive_b", group_id=gid)]
            if p1 and p2
        ]
        _gaze_slot_atoms = index.get_active_by_slot("expression")
        expression_atoms = index.get_active_by_slot("expression")

        for a in expression_atoms:
            self._validate_formal_atom(a, rule_item.id)

        # 结构化事实消解：同一 slot 出现相互矛盾的 gaze 方向，保留首个
        formal_gazes = [a for a in expression_atoms if a.can_detect and index.is_active(a) and is_formal_atom(a) and a.facts and a.facts.gaze]
        if len(formal_gazes) > 1:
            first_gaze = formal_gazes[0]
            for later_gaze in formal_gazes[1:]:
                if later_gaze.facts.gaze != first_gaze.facts.gaze:
                    if not later_gaze.can_delete_atom or not index.is_active(later_gaze):
                        continue
                    index.drop(later_gaze.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "gaze_mutual_exclusion_preserved_first",
                        (first_gaze.atom_id,),
                        later_gaze,
                    )

        # 自由文本消解 (仅 custom fallback)
        if tf.enabled:
            for p1, p2 in pairs:
                m1 = [a for a in expression_atoms if a.can_detect and index.is_active(a) and not is_formal_atom(a) and p1.matches(a.text)]
                m2 = [a for a in expression_atoms if a.can_detect and index.is_active(a) and not is_formal_atom(a) and p2.matches(a.text)]
                if m1 and m2:
                    m1.sort(key=lambda x: (x.tag_order, x.span_order))
                    m2.sort(key=lambda x: (x.tag_order, x.span_order))
                    if (m1[0].tag_order, m1[0].span_order) <= (m2[0].tag_order, m2[0].span_order):
                        winner = m1[0]
                        losers = m2
                    else:
                        winner = m2[0]
                        losers = m1

                    for loser in losers:
                        if not loser.can_delete_atom or not index.is_active(loser):
                            continue
                        self.text_fallback_hits += 1
                        index.drop(loser.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "gaze_mutual_exclusion_preserved_first",
                            (winner.atom_id,),
                            loser,
                        )

        return index.get_all_active_ordered()

    # ─── 规则 16: liquid_restrictions (effects, 400) ───

    def _resolve_liquid_restrictions_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        rule = rule_item.spec
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        combo_replacements = {
            "cum_eyes": "cum on cheek",
            "opaque_paint": "translucent slightly viscous fluid",
            "pussy_juice": "clear glistening moisture trail",
        }
        banned_combos: Tuple[BannedComboSpec, ...] = tuple(
            BannedComboSpec(
                triggers=select_fallback_patterns(tf_patterns, role="trigger", group_id=gid),
                replace=combo_replacements.get(gid, "clear fluid"),
            )
            for gid in get_fallback_group_ids(tf_patterns, role="trigger")
        )
        modifiers: Tuple[str, ...] = rule.modifiers
        liquid_words: Tuple[PatternSpec, ...] = select_fallback_patterns(tf_patterns, role="liquid", group_id="liquid_words")
        _sexual_fluid_atoms = index.get_active_by_fact("liquid_kind", "sexual_fluid")

        liquid_atoms = index.get_active_by_slot("liquids") + index.get_active_by_slot("liquid")
        for a in liquid_atoms:
            if not index.is_active(a) or not a.can_modify_internal:
                continue
            self._validate_formal_atom(a, rule_item.id)

            # 1. 替换高危组合
            if is_formal_atom(a):
                if a.facts and a.facts.liquid_kind in ("sexual_fluid", "cum", "semen") and any(loc in a.facts.liquid_locations for loc in ("eyes", "closed_eyes", "face")):
                    new_text = "few drops of semen on stomach"
                    new_facts = replace(a.facts, liquid_locations=("torso",))
                    new_a = make_replaced_atom(a, new_text, rule_item.id, new_facts=new_facts)
                    index.replace(a.atom_id, new_a)
                    ledger.record_replace(
                        rule_item.id,
                        rule_item.phase,
                        "liquid_combo_replaced",
                        (),
                        a,
                        (new_a,),
                    )
            elif tf.enabled:
                txt = a.text
                did_replace = False
                for bc in banned_combos:
                    for trig in bc.triggers:
                        if trig.matches(txt):
                            txt = trig.substitute(txt, bc.replace)
                            did_replace = True
                if did_replace and txt != a.text:
                    new_a = make_replaced_atom(a, txt, rule_item.id)
                    self.text_fallback_hits += 1
                    index.replace(a.atom_id, new_a)
                    ledger.record_replace(
                        rule_item.id,
                        rule_item.phase,
                        "liquid_combo_replaced",
                        (),
                        a,
                        (new_a,),
                    )

        # 2. 对微量液体应用量词修饰
        for a in (index.get_active_by_slot("liquids") + index.get_active_by_slot("liquid")):
            if not index.is_active(a) or not a.can_modify_internal:
                continue
            if is_formal_atom(a):
                if a.facts and not a.facts.liquid_amount:
                    mod = rng.choice(modifiers)
                    mod_text = f"{mod} {a.text}"
                    new_facts = replace(a.facts, liquid_amount="trace")
                    new_a = make_replaced_atom(a, mod_text, rule_item.id, new_facts=new_facts)
                    index.replace(a.atom_id, new_a)
                    ledger.record_replace(
                        rule_item.id,
                        rule_item.phase,
                        "liquid_quantifier_added",
                        (),
                        a,
                        (new_a,),
                    )
            elif tf.enabled:
                txt = a.text
                has_mod = any(m in txt.lower() for m in modifiers)
                if not has_mod and any(lw.matches(txt) for lw in liquid_words):
                    mod = rng.choice(modifiers)
                    mod_text = f"{mod} {txt}"
                    new_a = make_replaced_atom(a, mod_text, rule_item.id)
                    self.text_fallback_hits += 1
                    index.replace(a.atom_id, new_a)
                    ledger.record_replace(
                        rule_item.id,
                        rule_item.phase,
                        "liquid_quantifier_added",
                        (),
                        a,
                        (new_a,),
                    )

        return index.get_all_active_ordered()

    # ─── 规则 17: tattoo_dermal_fusion (effects, 410) ───

    def _resolve_tattoo_dermal_fusion_atoms(
        self,
        atoms: List[PromptAtom],
        rule_item: RuleItem,
        ledger: DecisionLedger,
        rng: Random,
        index: OneTimeIndex,
        context_profile: Optional[ContextProfile],
    ) -> List[PromptAtom]:
        rule = rule_item.spec
        tf = rule_item.spec.text_fallback
        tf_patterns = tf.patterns
        tattoo_indicators = select_fallback_patterns(tf_patterns, role="tattoo", group_id="tattoo")
        fusion_tags = rule.fusion_tags
        _tattoo_slot_atoms = index.get_active_by_slot("tattoo")

        tattoo_source_atoms = index.get_active_by_slot("tattoo")
        has_tattoo = len(tattoo_source_atoms) > 0
        if not has_tattoo and tf.enabled:
            for a in index.get_all_active_ordered():
                if a.can_detect and index.is_active(a):
                    if not is_formal_atom(a) and any(ti.matches(a.text) for ti in tattoo_indicators):
                        has_tattoo = True
                        tattoo_source_atoms.append(a)
        already_injected = any(
            (a.provenance and a.provenance.rule_id == rule_item.id) or (a.text in fusion_tags)
            for a in tattoo_source_atoms
        )
        if has_tattoo and not already_injected:
            fusion_text = rng.choice(fusion_tags)
            ref_atom = min(tattoo_source_atoms, key=lambda x: (x.tag_order, x.span_order))
            next_order = max((a.tag_order for a in index.get_all_active_ordered()), default=0) + 1
            injected_atom = PromptAtom(
                atom_id=f"atom_injected_{ref_atom.atom_id}_{rule_item.id}",
                text=fusion_text,
                span_type=SpanType.PLAIN,
                source_slot="tattoo",
                source_item_id=f"{rule_item.id}_fusion",
                tag_order=next_order,
                span_order=0,
                provenance=TagProvenance(
                    item_id=f"{rule_item.id}_fusion",
                    parent_ids=(ref_atom.atom_id,),
                    rule_id=rule_item.id,
                    kind="resolver_generated",
                ),
                origin=SelectionOrigin(
                    entry_point=ref_atom.origin.entry_point if (ref_atom.origin and ref_atom.origin.entry_point) else "generator",
                    mode="resolver",
                    selector="tattoo",
                    raw_value=ref_atom.origin.raw_value if ref_atom.origin else None,
                    parent_ids=(ref_atom.atom_id,),
                ),
                facts=SemanticFacts(),
            )
            if any(not is_formal_atom(a) and a.source_slot != "tattoo" for a in tattoo_source_atoms):
                self.text_fallback_hits += 1
            index.inject(injected_atom)
            ledger.record_inject(
                rule_item.id,
                rule_item.phase,
                "tattoo_dermal_fusion_injected",
                (ref_atom.atom_id,),
                (injected_atom,),
                parent_source_ids=(ref_atom.atom_id,),
            )

        return index.get_all_active_ordered()


    # ─── 只读最终硬冲突检测器 (detect_hard_conflicts) ───

    def detect_hard_conflicts(self, atoms: Sequence[PromptAtom]) -> Tuple[Tuple[Tuple[str, ...], ...], bool]:
        """只读检测输出原子列表中是否存在任何残留硬冲突，返回 (conflicts, has_protected)。
        覆盖全部 17 大规则硬不变量。
        仅当实际 loser 受保护（不可删除或含黑盒）时，has_protected 为 True。
        纯 ANGLE 与 QUOTED 黑盒原子完全不透明，绝不被内部文本命中。
        严格消费各规则声明式 text_fallback.patterns，杜绝第二套模式事实源 (R2R3-P1-001)。"""
        rule_tf_map: Dict[str, Tuple[PatternSpec, ...]] = {}
        for ritem in self.registry.doc.rules:
            rid = ritem.id
            rtf = ritem.spec.text_fallback
            rule_tf_map[rid] = tuple(rtf.patterns) if rtf else ()

        conflicts: List[Tuple[str, ...]] = []
        detectable = [a for a in atoms if a.can_detect]
        protected_losers: List[PromptAtom] = []

        def _is_loser_protected(loser: PromptAtom) -> bool:
            return not loser.can_delete_atom or loser.contains_blackbox or not loser.can_modify_internal


        # 1. 空间环境：室内 vs 室外
        r1_tf = rule_tf_map["spatial_environmental_mutual_exclusion"]
        r1_in_pats = select_fallback_patterns(r1_tf, role="indoor", group_id="indoor")
        r1_out_pats = select_fallback_patterns(r1_tf, role="outdoor", group_id="outdoor")
        r1_venue_pats = select_fallback_patterns(r1_tf, role="venue")
        outdoor_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and a.facts.space_kind == "outdoor")
            or (not is_formal_atom(a) and any(w.matches(a.text) for w in r1_out_pats))
        ]
        indoor_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and a.facts.space_kind == "indoor")
            or (not is_formal_atom(a) and not any(w.matches(a.text) for w in r1_out_pats) and (any(w.matches(a.text) for w in r1_in_pats) or any(w.matches(a.text) for w in r1_venue_pats)))
        ]
        if indoor_atoms and outdoor_atoms:
            first_in = min(indoor_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_out = min(outdoor_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_in.atom_id != first_out.atom_id:
                if (first_in.tag_order, first_in.span_order) <= (first_out.tag_order, first_out.span_order):
                    winner, loser = first_in, first_out
                else:
                    winner, loser = first_out, first_in
                conflicts.append(("residual_indoor_outdoor_conflict", "spatial_environmental_mutual_exclusion", "indoor_outdoor_mutex", winner.atom_id, loser.atom_id))
                if _is_loser_protected(loser):
                    protected_losers.append(loser)

        # 2. 裸露 vs 服装
        r2_tf = rule_tf_map["nudity_clothing_conflicts"]
        r2_l5_banned = select_fallback_patterns(r2_tf, role="banned", group_id="L5")
        nude_atoms = [a for a in detectable if a.source_slot == "nudity"]
        if nude_atoms:
            first_nude = min(nude_atoms, key=lambda x: (x.tag_order, x.span_order))
            is_full_nude = False
            raw_id = ((first_nude.source_item_id or "") + " " + ((first_nude.provenance.item_id or "") if first_nude.provenance else "") + " " + ((first_nude.origin.selected_id or "") if first_nude.origin else "")).upper()
            if is_formal_atom(first_nude):
                is_full_nude = any(lvl in raw_id for lvl in ("L5", "L6"))
            else:
                if any(lvl in raw_id for lvl in ("L5", "L6")) or any(s in first_nude.text.lower() for s in ("completely naked", "full nude", "bare body", "no clothes")):
                    is_full_nude = True
            if is_full_nude:
                clothing_atoms = [
                    a for a in detectable
                    if a.source_slot in ("clothing", "clothing_state", "clothing_extension")
                    or (not is_formal_atom(a) and any(b.matches(a.text) for b in r2_l5_banned))
                ]
                clothing_atoms = [a for a in clothing_atoms if a.atom_id != first_nude.atom_id]
                if clothing_atoms:
                    first_cloth = min(clothing_atoms, key=lambda x: (x.tag_order, x.span_order))
                    conflicts.append(("residual_nudity_clothing_conflict", "nudity_clothing_conflicts", "nudity_removes_clothing", first_nude.atom_id, first_cloth.atom_id))
                    if _is_loser_protected(first_cloth):
                        protected_losers.append(first_cloth)

        for gid in get_fallback_group_ids(r2_tf, role="trigger"):
            c_trig = select_fallback_patterns(r2_tf, role="trigger", group_id=gid)
            c_ban = select_fallback_patterns(r2_tf, role="banned", group_id=gid)
            trig_atoms = [
                a for a in detectable
                if (is_formal_atom(a) and a.facts and any(r in a.facts.visible_regions for r in ("crotch", "breasts", "buttocks", "pubic", "full_body")))
                or (not is_formal_atom(a) and any(t.matches(a.text) for t in c_trig))
            ]
            ban_atoms = [
                a for a in detectable
                if (is_formal_atom(a) and a.facts and any(t in a.facts.garment_topologies for t in ("panties", "bra", "underwear", "suit", "blouse", "dress")))
                or (not is_formal_atom(a) and any(b.matches(a.text) for b in c_ban))
            ]
            if trig_atoms and ban_atoms:
                first_trig = min(trig_atoms, key=lambda x: (x.tag_order, x.span_order))
                first_ban = min(ban_atoms, key=lambda x: (x.tag_order, x.span_order))
                if first_trig.atom_id != first_ban.atom_id:
                    conflicts.append(("residual_nudity_underwear_conflict", "nudity_clothing_conflicts", "nudity_removes_underwear", first_trig.atom_id, first_ban.atom_id))
                    if _is_loser_protected(first_ban):
                        protected_losers.append(first_ban)
                    break

        # 3. 景别 vs 下半身
        r3_tf = rule_tf_map["framing_lower_body_coherence"]
        r3_cu_pats = select_fallback_patterns(r3_tf, role="trigger", group_id="framing")
        r3_lb_pats = select_fallback_patterns(r3_tf, role="banned", group_id="framing")
        cu_atoms = []
        for a in detectable:
            if is_formal_atom(a):
                if a.source_slot in ("shot_type", "shot"):
                    item_id = (a.provenance.item_id or a.source_item_id or "") if a.provenance else (a.source_item_id or "")
                    if item_id in ("extreme_close_up", "close_up"):
                        cu_atoms.append(a)
                    elif item_id in ("medium_close_up", "medium_shot", "cowboy_shot", "full_body", "wide_shot", "extreme_wide"):
                        pass
                    elif a.facts and "face" in a.facts.visible_regions and not any(r in a.facts.visible_regions for r in ("upper_body", "lower_body", "legs", "feet")):
                        cu_atoms.append(a)
            else:
                if any(t.matches(a.text) for t in r3_cu_pats):
                    cu_atoms.append(a)
        lb_atoms = [
            a for a in detectable
            if (
                is_formal_atom(a)
                and a.source_slot == "clothing"
                and a.facts
                and any(r in a.facts.visible_regions for r in ("feet", "legs", "lower_body", "shoes"))
            )
            or (not is_formal_atom(a) and any(b.matches(a.text) for b in r3_lb_pats))
        ]
        if cu_atoms and lb_atoms:
            first_cu = min(cu_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_lb = min(lb_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_cu.atom_id != first_lb.atom_id:
                conflicts.append(("residual_framing_lower_body_conflict", "framing_lower_body_coherence", "close_up_removes_lower_body", first_cu.atom_id, first_lb.atom_id))
                if _is_loser_protected(first_lb):
                    protected_losers.append(first_lb)

        # 4. 姿态双手占用 vs 手持道具
        r4_tf = rule_tf_map["pose_hand_occupation"]
        r4_busy_pats = select_fallback_patterns(r4_tf, role="trigger", group_id="pose_hand")
        r4_hh_pats = select_fallback_patterns(r4_tf, role="handheld", group_id="pose_hand")
        busy_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and a.facts.hand_state == "both_busy")
            or (not is_formal_atom(a) and any(t.matches(a.text) for t in r4_busy_pats))
        ]
        prop_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.source_slot == "props" and a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"))
            or (not is_formal_atom(a) and any(p.matches(a.text) for p in r4_hh_pats))
        ]
        if busy_atoms and prop_atoms:
            first_busy = min(busy_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_prop = min(prop_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_busy.atom_id != first_prop.atom_id:
                conflicts.append(("residual_hand_occupation_conflict", "pose_hand_occupation", "busy_hands_remove_props", first_busy.atom_id, first_prop.atom_id))
                if _is_loser_protected(first_prop):
                    protected_losers.append(first_prop)

        # 5. 单持道具上限
        r5_tf = rule_tf_map["handheld_props_single_holder"]
        r5_pats = select_fallback_patterns(r5_tf, role="handheld", group_id="handheld")
        hh_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.source_slot == "props" and a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"))
            or (not is_formal_atom(a) and any(hp.matches(a.text) for hp in r5_pats))
        ]
        if len(hh_atoms) > 1:
            hh_atoms.sort(key=lambda x: (x.tag_order, x.span_order))
            first_hh = hh_atoms[0]
            second_hh = hh_atoms[1]
            conflicts.append(("residual_single_handheld_prop_conflict", "handheld_props_single_holder", "single_handheld_prop_limit", first_hh.atom_id, second_hh.atom_id))
            if _is_loser_protected(second_hh):
                protected_losers.append(second_hh)

        # 6. 连体衣结构状态
        r6_tf = rule_tf_map["clothing_style_state_coherence"]
        r6_op_trig = select_fallback_patterns(r6_tf, role="trigger", group_id="one_piece")
        r6_op_ban = select_fallback_patterns(r6_tf, role="banned", group_id="one_piece")
        op_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and "one_piece" in a.facts.garment_topologies)
            or (not is_formal_atom(a) and any(opt.matches(a.text) for opt in r6_op_trig))
        ]
        if op_atoms:
            has_button_carrier = any(
                (oa.source_item_id or (oa.provenance.item_id if oa.provenance else "") or (oa.origin.selected_id if oa.origin else "")) in ALLOWED_BUTTON_STYLES
                for oa in op_atoms
            ) or any(
                is_formal_atom(a) and (a.source_item_id or (a.provenance.item_id if a.provenance else "") or (a.origin.selected_id if a.origin else "")) in ALLOWED_BUTTON_STYLES
                for a in detectable
            )
            has_skirt_entity = any(
                is_formal_atom(a) and a.facts and "bottom_skirt" in a.facts.garment_topologies
                for a in detectable
            )
            for a in detectable:
                if a in op_atoms or a.atom_id in {oa.atom_id for oa in op_atoms}:
                    continue
                is_op_conflict = False
                if is_formal_atom(a):
                    state_id = a.source_item_id or (a.origin.selected_id if a.origin else "") or a.text
                    is_unbuttoned = state_id == "unbuttoned" or (a.facts and any(s in a.facts.garment_states for s in ("unbuttoned", "opened")))
                    is_lifted = state_id in ("lifted_up", "lifted") or (a.facts and any(s in a.facts.garment_states for s in ("lifted", "lifted_up", "lifted_skirt")))
                    is_removed = a.facts and "removed" in a.facts.garment_states

                    if is_unbuttoned:
                        if not has_button_carrier:
                            is_op_conflict = True
                    elif is_lifted:
                        is_non_skirt = any(
                            (oa.source_item_id or (oa.provenance.item_id if oa.provenance else "") or (oa.origin.selected_id if oa.origin else "")) in NON_SKIRT_ONE_PIECE
                            for oa in op_atoms
                        )
                        if is_non_skirt and not has_skirt_entity:
                            is_op_conflict = True
                    elif is_removed:
                        is_op_conflict = True
                elif any(bs.matches(a.text) for bs in r6_op_ban):
                    is_op_conflict = True

                if is_op_conflict:
                    first_op = min(op_atoms, key=lambda x: (x.tag_order, x.span_order))
                    conflicts.append(("residual_one_piece_state_conflict", "clothing_style_state_coherence", "one_piece_state_conflict", first_op.atom_id, a.atom_id))
                    if _is_loser_protected(a):
                        protected_losers.append(a)

        r6_pants_trig = select_fallback_patterns(r6_tf, role="trigger", group_id="pants")
        r6_pants_ban = select_fallback_patterns(r6_tf, role="banned", group_id="pants")
        if r6_pants_trig and r6_pants_ban:
            pants_atoms = [
                a for a in detectable
                if (is_formal_atom(a) and a.facts and any(t in a.facts.garment_topologies for t in ("pants", "jeans", "shorts", "trousers", "bottom_pants")))
                or (not is_formal_atom(a) and any(pt.matches(a.text) for pt in r6_pants_trig))
            ]
            has_skirt_entity = any(
                is_formal_atom(a) and a.facts and "bottom_skirt" in a.facts.garment_topologies
                for a in detectable
            )
            if pants_atoms and not has_skirt_entity:
                for a in detectable:
                    if a in pants_atoms or a.atom_id in {pa.atom_id for pa in pants_atoms}:
                        continue
                    is_pants_conflict = False
                    if is_formal_atom(a):
                        state_id = a.source_item_id or (a.origin.selected_id if a.origin else "") or a.text
                        if (
                            state_id in ("lifted_up", "lifted")
                            or (a.facts and any(s in a.facts.garment_states for s in ("skirt_slit", "pleated_skirt", "skirt_floating", "slit", "lifted_skirt", "lifted", "lifted_up")))
                        ):
                            is_pants_conflict = True
                    elif any(bs.matches(a.text) for bs in r6_pants_ban):
                        is_pants_conflict = True

                    if is_pants_conflict:
                        first_pants = min(pants_atoms, key=lambda x: (x.tag_order, x.span_order))
                        conflicts.append(("residual_pants_state_conflict", "clothing_style_state_coherence", "pants_state_conflict", first_pants.atom_id, a.atom_id))
                        if _is_loser_protected(a):
                            protected_losers.append(a)

        # 7. 材质穿透
        r7 = self.registry.get_rule("material_penetration")
        r7_tf = rule_tf_map["material_penetration"]
        r7_banned = select_fallback_patterns(r7_tf, role="banned", group_id="material")
        sheer_atoms = [
            a for a in detectable
            if not (a.source_slot == "clothing_extension" or (a.provenance and a.provenance.kind == "clothing_extension"))
            and (a.source_slot in getattr(r7, "target_slots", ()) or a.source_slot in ("clothing", "clothing_state"))
            and not (a.provenance and a.provenance.rule_id == "material_penetration")
            and (
                (is_formal_atom(a) and a.facts and any(s in a.facts.garment_states for s in ("sheer", "see_through", "transparent", "translucent", "wet_clinging")))
                or (is_formal_atom(a) and a.source_item_id in ("sheer_chiffon", "sheer_mesh", "semi_translucent"))
                or (not is_formal_atom(a) and any(bw.matches(a.text) for bw in r7_banned))
            )
        ]
        if sheer_atoms:
            first_sheer = min(sheer_atoms, key=lambda x: (x.tag_order, x.span_order))
            conflicts.append(("residual_material_penetration_conflict", "material_penetration", "material_penetration_removed", first_sheer.atom_id, first_sheer.atom_id))
            if _is_loser_protected(first_sheer):
                protected_losers.append(first_sheer)

        # 8. 拍摄设备与画质词冲突
        r8_tf = rule_tf_map["device_quality_compatibility"]
        dev_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and a.facts.capture_device in ("cctv", "vhs", "polaroid", "webcam"))
            or (not is_formal_atom(a) and any(dp.matches(a.text) for gid in get_fallback_group_ids(r8_tf, role="device") for dp in select_fallback_patterns(r8_tf, role="device", group_id=gid)))
        ]
        quality_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and a.facts.quality_class in ("masterpiece", "ultra_detailed", "high_res", "best_quality"))
            or (not is_formal_atom(a) and any(bt.matches(a.text) for gid in get_fallback_group_ids(r8_tf, role="banned") for bt in select_fallback_patterns(r8_tf, role="banned", group_id=gid)))
        ]
        if dev_atoms and quality_atoms:
            first_dev = min(dev_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_qual = min(quality_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_dev.atom_id != first_qual.atom_id:
                conflicts.append(("residual_device_quality_conflict", "device_quality_compatibility", "device_removes_conflicting_quality", first_dev.atom_id, first_qual.atom_id))
                if _is_loser_protected(first_qual):
                    protected_losers.append(first_qual)

        # 9. 昼夜环境与光影
        r9_tf = rule_tf_map["environmental_lighting_coherence"]
        r9_night_pats = select_fallback_patterns(r9_tf, role="banned", group_id="daylight")
        r9_day_pats = select_fallback_patterns(r9_tf, role="trigger", group_id="daylight")
        night_scene_atoms = [
            a for a in detectable
            if a.source_slot in ("scene", "scene_theme", "theme") and (
                (is_formal_atom(a) and a.facts and a.facts.time_of_day in ("night", "midnight", "late_night"))
                or (not is_formal_atom(a) and any(nt.matches(a.text) for nt in r9_night_pats))
            )
        ]
        daylight_lighting_atoms = [
            a for a in detectable
            if a.source_slot in ("lighting", "lighting_palette") and (
                (is_formal_atom(a) and a.facts and (a.facts.time_of_day in ("morning", "noon", "afternoon", "day") or any(ls in ("sunlight", "daylight", "direct_sun") for ls in a.facts.light_sources)))
                or (not is_formal_atom(a) and any(dt.matches(a.text) for dt in r9_day_pats))
            )
        ]
        if night_scene_atoms and daylight_lighting_atoms:
            first_night = min(night_scene_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_day = min(daylight_lighting_atoms, key=lambda x: (x.tag_order, x.span_order))
            conflicts.append(("residual_lighting_night_day_conflict", "environmental_lighting_coherence", "night_scene_removes_daylight", first_night.atom_id, first_day.atom_id))
            if _is_loser_protected(first_day):
                protected_losers.append(first_day)
        else:
            daylight_scene_atoms = [
                a for a in detectable
                if a.source_slot in ("scene", "scene_theme", "theme") and (
                    (is_formal_atom(a) and a.facts and a.facts.time_of_day in ("morning", "noon", "afternoon", "day"))
                    or (not is_formal_atom(a) and any(dt.matches(a.text) for dt in r9_day_pats))
                )
            ]
            night_lighting_atoms = [
                a for a in detectable
                if a.source_slot in ("lighting", "lighting_palette") and (
                    (is_formal_atom(a) and a.facts and a.facts.time_of_day in ("night", "midnight", "late_night"))
                    or (not is_formal_atom(a) and any(nt.matches(a.text) for nt in r9_night_pats))
                )
            ]
            if daylight_scene_atoms and night_lighting_atoms:
                first_day = min(daylight_scene_atoms, key=lambda x: (x.tag_order, x.span_order))
                first_night = min(night_lighting_atoms, key=lambda x: (x.tag_order, x.span_order))
                conflicts.append(("residual_lighting_night_day_conflict", "environmental_lighting_coherence", "daylight_removes_night", first_day.atom_id, first_night.atom_id))
                if _is_loser_protected(first_night):
                    protected_losers.append(first_night)

        # 10. 黑白胶片与色彩
        r10_tf = rule_tf_map["monochrome_film_chroma_coherence"]
        r10_mono_pats = select_fallback_patterns(r10_tf, role="trigger", group_id="monochrome")
        r10_chroma_pats = select_fallback_patterns(r10_tf, role="banned", group_id="monochrome")
        mono_atoms = [
            a for a in detectable
            if a.source_slot in ("film", "film_stock") and (
                (is_formal_atom(a) and a.facts and "monochrome" in a.facts.color_modes)
                or (not is_formal_atom(a) and any(t.matches(a.text) for t in r10_mono_pats))
            )
        ]
        chroma_atoms = [
            a for a in detectable
            if a.source_slot in ("lighting", "lighting_palette", "film", "film_stock") and (
                (is_formal_atom(a) and a.facts and any(c in a.facts.color_modes for c in ("color", "high_saturation", "neon")))
                or (not is_formal_atom(a) and any(b.matches(a.text) for b in r10_chroma_pats))
            )
        ]
        if mono_atoms and chroma_atoms:
            first_mono = min(mono_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_chroma = min(chroma_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_mono.atom_id != first_chroma.atom_id:
                conflicts.append(("residual_monochrome_chroma_conflict", "monochrome_film_chroma_coherence", "monochrome_film_removes_chroma", first_mono.atom_id, first_chroma.atom_id))
                if _is_loser_protected(first_chroma):
                    protected_losers.append(first_chroma)

        # 11. 妆容细节
        r11_tf = rule_tf_map["makeup_details_coherence"]
        r11_bare_pats = select_fallback_patterns(r11_tf, role="trigger", group_id="no_makeup")
        r11_heavy_pats = select_fallback_patterns(r11_tf, role="banned", group_id="no_makeup")
        bare_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and a.facts.makeup_base in ("bare", "clean", "natural"))
            or (not is_formal_atom(a) and any(t.matches(a.text) for t in r11_bare_pats))
        ]
        heavy_atoms = [
            a for a in detectable
            if (is_formal_atom(a) and a.facts and any(e in a.facts.makeup_effects for e in ("heavy", "smudged", "runny", "smeared")))
            or (not is_formal_atom(a) and any(b.matches(a.text) for b in r11_heavy_pats))
        ]
        if bare_atoms and heavy_atoms:
            first_bare = min(bare_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_heavy = min(heavy_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_bare.atom_id != first_heavy.atom_id:
                conflicts.append(("residual_makeup_details_conflict", "makeup_details_coherence", "clean_base_removes_heavy_makeup", first_bare.atom_id, first_heavy.atom_id))
                if _is_loser_protected(first_heavy):
                    protected_losers.append(first_heavy)

        # 12. 视线几何
        r12_tf = rule_tf_map["gaze_angle_geometry"]
        for gid in get_fallback_group_ids(r12_tf, role="angle"):
            map_angles = select_fallback_patterns(r12_tf, role="angle", group_id=gid)
            map_banned = select_fallback_patterns(r12_tf, role="banned", group_id=gid)
            ang_atoms = [
                a for a in detectable
                if a.source_slot in ("camera_angle", "camera", "shot_type")
                and (
                    (is_formal_atom(a) and ((a.source_item_id in ("overhead", "top_down", "birds_eye")) or (a.provenance and a.provenance.item_id in ("overhead", "top_down", "birds_eye")) or (a.facts and a.facts.gaze in ("down", "looking_down_from_above"))))
                    or (not is_formal_atom(a) and any(ag.matches(a.text) for ag in map_angles))
                )
            ]
            gaze_atoms = [
                a for a in detectable
                if a.source_slot == "expression"
                and (
                    (is_formal_atom(a) and a.facts and a.facts.gaze in ("down", "looking_down_from_above"))
                    or (not is_formal_atom(a) and any(bg.matches(a.text) for bg in map_banned))
                )
            ]
            if ang_atoms and gaze_atoms:
                first_ang = min(ang_atoms, key=lambda x: (x.tag_order, x.span_order))
                first_gaze = min(gaze_atoms, key=lambda x: (x.tag_order, x.span_order))
                if first_ang.atom_id != first_gaze.atom_id:
                    conflicts.append(("residual_gaze_angle_conflict", "gaze_angle_geometry", "camera_angle_removes_impossible_gaze", first_ang.atom_id, first_gaze.atom_id))
                    if _is_loser_protected(first_gaze):
                        protected_losers.append(first_gaze)
                    break

        # 13. 饰品遮挡视线
        r13_tf = rule_tf_map["accessory_occlusion_gaze_coherence"]
        r13_occ_pats = select_fallback_patterns(r13_tf, role="trigger", group_id="occlusion")
        r13_gaze_pats = select_fallback_patterns(r13_tf, role="banned", group_id="occlusion")
        occ_atoms = [
            a for a in detectable
            if a.source_slot in ("jewelry", "accessories") and (
                (is_formal_atom(a) and a.facts and a.facts.occlusion in ("eyes", "face"))
                or (not is_formal_atom(a) and any(t.matches(a.text) for t in r13_occ_pats))
            )
        ]
        gaze_atoms = [
            a for a in detectable
            if a.source_slot in ("expression", "expressions") and (
                (is_formal_atom(a) and a.facts and a.facts.gaze in ("camera", "viewer", "direct"))
                or (not is_formal_atom(a) and any(b.matches(a.text) for b in r13_gaze_pats))
            )
        ]
        if occ_atoms and gaze_atoms:
            first_occ = min(occ_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_gaze = min(gaze_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_occ.atom_id != first_gaze.atom_id:
                conflicts.append(("residual_accessory_occlusion_conflict", "accessory_occlusion_gaze_coherence", "eye_occlusion_removes_gaze", first_occ.atom_id, first_gaze.atom_id))
                if _is_loser_protected(first_gaze):
                    protected_losers.append(first_gaze)

        # 14. 情绪与视线
        r14_tf = rule_tf_map["emotion_gaze_affinity"]
        for gid in get_fallback_group_ids(r14_tf, role="emotion"):
            c_emo_pats = select_fallback_patterns(r14_tf, role="emotion", group_id=gid)
            c_ban_pats = select_fallback_patterns(r14_tf, role="banned", group_id=gid)
            emo_atoms = [
                a for a in detectable
                if (is_formal_atom(a) and a.facts and a.facts.emotion == "shy")
                or (not is_formal_atom(a) and any(t.matches(a.text) for t in c_emo_pats))
            ]
            banned_gaze = [
                a for a in detectable
                if (is_formal_atom(a) and a.facts and a.facts.emotion in ("seductive", "dominant"))
                or (not is_formal_atom(a) and any(b.matches(a.text) for b in c_ban_pats))
            ]
            if emo_atoms and banned_gaze:
                first_emo = min(emo_atoms, key=lambda x: (x.tag_order, x.span_order))
                first_bg = min(banned_gaze, key=lambda x: (x.tag_order, x.span_order))
                if first_emo.atom_id != first_bg.atom_id:
                    conflicts.append(("residual_emotion_gaze_conflict", "emotion_gaze_affinity", "emotion_removes_conflicting_gaze", first_emo.atom_id, first_bg.atom_id))
                    if _is_loser_protected(first_bg):
                        protected_losers.append(first_bg)
                    break

        # 15. 视线互斥
        r15_tf = rule_tf_map["gaze_mutual_exclusion"]
        formal_gazes = [a for a in detectable if is_formal_atom(a) and a.facts and a.facts.gaze and a.source_slot == "expression"]
        if len(formal_gazes) > 1:
            first_g = formal_gazes[0]
            for later_g in formal_gazes[1:]:
                if later_g.facts.gaze != first_g.facts.gaze:
                    conflicts.append(("residual_gaze_mutual_exclusion_conflict", "gaze_mutual_exclusion", "gaze_mutual_exclusion_preserved_first", first_g.atom_id, later_g.atom_id))
                    if _is_loser_protected(later_g):
                        protected_losers.append(later_g)
        active_exclusive_pairs = [
            (p1[0], p2[0])
            for gid in get_fallback_group_ids(r15_tf, role="exclusive_a")
            for p1 in [select_fallback_patterns(r15_tf, role="exclusive_a", group_id=gid)]
            for p2 in [select_fallback_patterns(r15_tf, role="exclusive_b", group_id=gid)]
            if p1 and p2
        ]
        for p1, p2 in active_exclusive_pairs:
            m1 = [a for a in detectable if not is_formal_atom(a) and p1.matches(a.text)]
            m2 = [a for a in detectable if not is_formal_atom(a) and p2.matches(a.text)]
            if m1 and m2:
                m1.sort(key=lambda x: (x.tag_order, x.span_order))
                m2.sort(key=lambda x: (x.tag_order, x.span_order))
                if (m1[0].tag_order, m1[0].span_order) <= (m2[0].tag_order, m2[0].span_order):
                    winner, loser = m1[0], m2[0]
                else:
                    winner, loser = m2[0], m1[0]
                conflicts.append(("residual_gaze_mutual_exclusion_conflict", "gaze_mutual_exclusion", "gaze_mutual_exclusion_preserved_first", winner.atom_id, loser.atom_id))
                if _is_loser_protected(loser):
                    protected_losers.append(loser)

        # 16. 液体高危组合残留
        r16_tf = rule_tf_map["liquid_restrictions"]
        for gid in get_fallback_group_ids(r16_tf, role="trigger"):
            combo_triggers = select_fallback_patterns(r16_tf, role="trigger", group_id=gid)
            combo_atoms = [
                a for a in detectable
                if (is_formal_atom(a) and a.facts and a.facts.liquid_kind in ("sexual_fluid", "cum", "semen") and any(loc in a.facts.liquid_locations for loc in ("eyes", "closed_eyes", "face")))
                or (not is_formal_atom(a) and any(trig.matches(a.text) for trig in combo_triggers))
            ]
            if combo_atoms:
                first_cb = min(combo_atoms, key=lambda x: (x.tag_order, x.span_order))
                conflicts.append(("residual_liquid_combo_conflict", "liquid_restrictions", "liquid_combo_replaced", first_cb.atom_id, first_cb.atom_id))
                if _is_loser_protected(first_cb):
                    protected_losers.append(first_cb)

        return tuple(conflicts), len(protected_losers) > 0
