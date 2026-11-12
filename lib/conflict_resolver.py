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
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

if __package__:
    from .errors import PromptValidationError, QuarantineLeakageError, RuleConfigurationError, UnresolvedConflictError
    from .models import (
        CANONICAL_SELECTORS_BY_ENTRY_POINT,
        FORMAL_ORIGIN_MODES,
        ContextProfile,
        NudityConstraint,
        PieceBinding,
        PosePhysicalFacts,
        PromptAtom,
        PromptFragment,
        ResolutionDecision,
        ResolutionReport,
        SelectionOrigin,
        SemanticFacts,
        SpanType,
        TagProvenance,
        UNSPECIFIED,
        VALID_NUDITY_LEVELS,
        is_external_atom,
        normalize_nudity_level_code,
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
    from lib.errors import PromptValidationError, QuarantineLeakageError, RuleConfigurationError, UnresolvedConflictError
    from lib.models import (
        CANONICAL_SELECTORS_BY_ENTRY_POINT,
        FORMAL_ORIGIN_MODES,
        ContextProfile,
        NudityConstraint,
        PieceBinding,
        PosePhysicalFacts,
        PromptAtom,
        PromptFragment,
        ResolutionDecision,
        ResolutionReport,
        SelectionOrigin,
        SemanticFacts,
        SpanType,
        TagProvenance,
        UNSPECIFIED,
        VALID_NUDITY_LEVELS,
        is_external_atom,
        normalize_nudity_level_code,
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
# 隔离池阻断与服装实体聚合 SSOT 生产级实现
# ═══════════════════════════════════════════════════════════════════════════

QUARANTINE_ENTITY_IDS: FrozenSet[str] = frozenset({
    'SRC_CAM_06691', 'SRC_CLOTH_00317', 'SRC_CLOTH_00368', 'SRC_CLOTH_00387',
    'SRC_POSE_03788', 'SRC_POSE_03931', 'SRC_POSE_03952', 'SRC_POSE_03992',
    'SRC_POSE_04004', 'SRC_POSE_04018', 'SRC_POSE_04266', 'SRC_POSE_04270',
    'SRC_SCENE_04459', 'SRC_SCENE_04460', 'SRC_SCENE_04461', 'SRC_SCENE_04462',
    'SRC_SCENE_04463', 'SRC_SCENE_04464', 'SRC_SCENE_04465', 'SRC_SCENE_04466',
    'SRC_SCENE_04519', 'SRC_SCENE_04521', 'SRC_SCENE_04525', 'SRC_SCENE_04684',
    'SRC_SCENE_04686', 'SRC_SCENE_04687', 'SRC_SCENE_04689', 'SRC_SCENE_04696',
    'SRC_SCENE_04697', 'SRC_SCENE_04698', 'SRC_SCENE_04699', 'SRC_SCENE_04763',
    'SRC_SCENE_04764', 'SRC_SCENE_04765', 'SRC_SCENE_04766', 'SRC_SCENE_04767',
    'SRC_SCENE_04816', 'SRC_SCENE_04817', 'SRC_SCENE_04982', 'SRC_SCENE_04983',
    'SRC_SCENE_04984', 'SRC_SCENE_04985', 'SRC_SCENE_04986', 'SRC_SCENE_05004',
    'SRC_SCENE_05057', 'SRC_SCENE_05059', 'SRC_SCENE_05114', 'SRC_SCENE_05116',
    'SRC_SCENE_05117', 'SRC_SCENE_05118', 'SRC_SCENE_05132', 'SRC_SCENE_05133',
    'SRC_SCENE_05134', 'SRC_SCENE_05135', 'SRC_SCENE_05136', 'SRC_SCENE_05137',
    'SRC_SCENE_05138', 'SRC_SCENE_05139', 'SRC_SCENE_05140', 'SRC_SCENE_05141',
    'SRC_SCENE_05142', 'SRC_SCENE_05143', 'SRC_SCENE_05181', 'SRC_SCENE_05182',
    'SRC_SCENE_05183', 'SRC_SCENE_05184', 'SRC_SCENE_05185', 'SRC_SCENE_05186',
    'SRC_SCENE_05187', 'SRC_SCENE_05188', 'SRC_SCENE_05240', 'SRC_SCENE_05273',
    'SRC_SCENE_05281', 'SRC_SCENE_05282', 'SRC_SCENE_05303', 'SRC_SCENE_05304',
    'SRC_SCENE_05310', 'SRC_SCENE_05311', 'SRC_SCENE_05312', 'SRC_SCENE_05313',
    'SRC_SCENE_05314', 'SRC_SCENE_05361', 'SRC_SCENE_05362'
})


def is_pose_support_compatible(pose_input: Any, required_support: str) -> bool:
    """评估姿态物理支撑与场景/约束要求是否兼容。
    若 body_support 为单一确定值，直接比对；
    若 body_support 为 UNSPECIFIED，但存在 support_options 候选集合，只要 required_support 在候选集合中即兼容；
    若 support_options 为空（全自由态），恒兼容。
    """
    if hasattr(pose_input, "pose_facts") and getattr(pose_input, "pose_facts", None) is not None:
        return is_pose_support_compatible(getattr(pose_input, "pose_facts"), required_support)
    pose_facts: Optional[PosePhysicalFacts] = pose_input
    if not pose_facts or not getattr(pose_facts, "support_options", None):
        return True
    if pose_facts.body_support and pose_facts.body_support is not UNSPECIFIED and pose_facts.body_support != "unspecified":
        return pose_facts.body_support == required_support
    return required_support in pose_facts.support_options


def is_quarantined_payload(
    obj: Any,
    entity_id: Optional[str] = None,
) -> bool:
    """判定任意对象、字典、PromptAtom 或 ID 是否触发物理隔离防护门禁 (Fail-Closed)。

    拦截规则：
    1. entity_id 在 QUARANTINE_ENTITY_IDS 中；
    2. 显式隔离标志: is_quarantined is True / 'true';
    3. 隔离原因/策略: 含有非空 quarantine_reason 或 quarantine_policy;
    4. 禁止入库/导出标志: importable is False (或 'false') 或 target_export_eligible is False.
    """
    if entity_id and entity_id in QUARANTINE_ENTITY_IDS:
        return True

    if isinstance(obj, str):
        return obj in QUARANTINE_ENTITY_IDS

    if isinstance(obj, dict):
        for id_k in ("source_entity_id", "entity_id", "id", "target_item_id", "selected_id", "source_item_id"):
            val = obj.get(id_k)
            if isinstance(val, str) and val in QUARANTINE_ENTITY_IDS:
                return True
        if obj.get("is_quarantined") is True or str(obj.get("is_quarantined", "")).lower() == "true":
            return True
        if bool(obj.get("quarantine_reason")) or bool(obj.get("quarantine_policy")):
            return True
        if obj.get("importable") is False or str(obj.get("importable", "")).lower() == "false":
            return True
        if obj.get("target_export_eligible") is False or str(obj.get("target_export_eligible", "")).lower() == "false":
            return True

    elif hasattr(obj, "facts") or hasattr(obj, "origin") or hasattr(obj, "id"):
        # PromptAtom
        atom_id = getattr(obj, "id", None)
        source_id = getattr(obj, "source_item_id", None)
        if (atom_id and atom_id in QUARANTINE_ENTITY_IDS) or (source_id and source_id in QUARANTINE_ENTITY_IDS):
            return True
        origin = getattr(obj, "origin", None)
        if origin:
            sel_id = getattr(origin, "selected_id", None)
            if sel_id and sel_id in QUARANTINE_ENTITY_IDS:
                return True
        facts = getattr(obj, "facts", None)
        if facts is not None:
            gov = getattr(facts, "governance_metadata", None)
            if isinstance(gov, dict) and is_quarantined_payload(gov):
                return True
        meta = getattr(obj, "metadata", None)
        if isinstance(meta, dict) and is_quarantined_payload(meta):
            return True

    return False


def does_garment_support_modifier(carrier_facts: Optional[SemanticFacts], modifier_topology: str) -> bool:
    """检查在穿服装是否支持特定部位修饰（支持多件套构件穿透）。"""
    if not carrier_facts:
        return False
    if carrier_facts.garment_topologies:
        if modifier_topology in carrier_facts.garment_topologies:
            return True
        if "ensemble_outfit" in carrier_facts.garment_topologies and carrier_facts.ensemble_pieces:
            for pb in carrier_facts.ensemble_pieces.piece_bindings:
                if pb.garment_topology:
                    if pb.garment_topology == modifier_topology:
                        return True
                    continue
                # 当构件未显式指定拓扑时的严格槽位与文本判定：严禁裤装/裙装混淆
                p_txt = (pb.piece_text or pb.piece_id or "").lower()
                if modifier_topology == "top" and pb.piece_slot in ("top_pieces", "main_garments"):
                    return True
                if modifier_topology == "bottom_skirt" and pb.piece_slot in ("bottom_pieces", "main_garments"):
                    if any(k in p_txt for k in ("skirt", "dress", "pleated", "tutu", "petticoat", "kilt", "sarong")) and not any(k in p_txt for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings")):
                        return True
                    continue
                if modifier_topology == "bottom_pants" and pb.piece_slot in ("bottom_pieces", "main_garments"):
                    if any(k in p_txt for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings")) and not any(k in p_txt for k in ("skirt", "dress")):
                        return True
                    continue
                if modifier_topology == "outerwear" and pb.piece_slot == "outer_layers":
                    return True
                if modifier_topology == "underwear" and pb.piece_slot in ("underwear", "styling_details"):
                    return True
            if modifier_topology == "top" and (carrier_facts.ensemble_pieces.top_pieces or carrier_facts.ensemble_pieces.main_garments):
                return True
            if modifier_topology == "bottom_skirt" and carrier_facts.ensemble_pieces.bottom_pieces:
                for bp in carrier_facts.ensemble_pieces.bottom_pieces:
                    bp_lower = bp.lower()
                    if any(k in bp_lower for k in ("skirt", "dress", "pleated", "tutu", "petticoat", "kilt")) and not any(k in bp_lower for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings")):
                        return True
            if modifier_topology == "bottom_pants" and carrier_facts.ensemble_pieces.bottom_pieces:
                for bp in carrier_facts.ensemble_pieces.bottom_pieces:
                    bp_lower = bp.lower()
                    if any(k in bp_lower for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings")) and not any(k in bp_lower for k in ("skirt", "dress")):
                        return True
            if modifier_topology == "outerwear" and carrier_facts.ensemble_pieces.outer_layers:
                return True
    return False


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

    def __post_init__(self) -> None:
        if is_quarantined_payload(self.entity_id) or is_quarantined_payload(self.selected_id):
            raise QuarantineLeakageError(
                f"Quarantined entity '{self.selected_id or self.entity_id}' cannot form a GarmentCarrierEntity!"
            )
        for a in self.member_atoms:
            if is_quarantined_payload(a):
                raise QuarantineLeakageError(
                    f"Quarantined atom '{getattr(a, 'id', 'unknown')}' cannot be part of GarmentCarrierEntity!"
                )


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
    "leather_jacket", "tactical_vest", "windbreaker", "winter_parka",
    "festive_costume", "racing_suit", "robe_general",
    "lolita_fashion", "lolita_gothic", "lolita_sweet",
}

NON_SKIRT_ONE_PIECE: Set[str] = {
    "latex_catsuit", "zentai_suit", "leotard_bodysuit", "mecha_power_armor",
    "mecha_exoskeleton", "racing_suit", "swimsuit_classic", "swimsuit_school",
    "swimsuit_competition", "swimsuit_creative", "one_piece_swimsuit", "dungarees",
    "qipao", "anime_cosplay", "gym_uniform", "cyberpunk_tactical_bodysuit",
    "tactical_jumpsuit", "cyberpunk_plugsuit", "leather_corset", "bunny_suit", "ao_dai",
}

# 特定款式的产品业务规则：仅授权数据中明确具备拉链开襟联动事实的款式
ALLOWED_ZIPPER_STYLES: Set[str] = {
    "nurse_uniform",      # 护士服拉链开领 (nurse uniform unzipped to waist)
    "latex_catsuit",      # 乳胶紧身衣拉链 (latex bodysuit unzipped to navel / zipper pulled halfway down)
    "modern_chinese",     # 新中式改良旗袍拉链 (modern qipao unzipped)
}

RE_DRESS_ACTION = re.compile(r"\b(?:dress)\b", re.IGNORECASE)
RE_SHIRT_ACTION = re.compile(r"\b(?:shirt|blouse)\b", re.IGNORECASE)
RE_ROBE_ACTION = re.compile(r"\b(?:robe|cassock|gown)\b", re.IGNORECASE)
RE_SUIT_ACTION = re.compile(r"\b(?:suit unbuttoned|racing suit)\b", re.IGNORECASE)
RE_OUTERWEAR_ACTION = re.compile(r"\b(?:jacket|coat|vest|parka|overcoat)\b", re.IGNORECASE)
RE_CHEST_ACTION = re.compile(r"\b(?:cleavage|chest|collar|breasts?)\b", re.IGNORECASE)
RE_UPPER_BODY_ACTION = re.compile(r"\b(?:shirt|blouse|cleavage|chest|collar|breasts?|jacket|coat|vest|parka|overcoat)\b", re.IGNORECASE)
RE_PANTS_ACTION = re.compile(r"\b(?:pants|jeans|shorts)\b", re.IGNORECASE)
RE_SKIRT_ACTION = re.compile(r"\b(?:skirt)\b", re.IGNORECASE)


def _is_accessory_or_detail(text: Optional[str]) -> bool:
    """判定构件文本是否纯属附属配件/饰品/细节（如腰带、袖子、刺绣、领口等），杜绝其被误判为承载动作的完整服装。"""
    if not text:
        return False
    t_low = text.lower()
    garment_nouns = (
        "dress", "skirt", "robe", "gown", "kimono", "yukata", "cheongsam",
        "qipao", "hanfu", "hanbok", "nightgown", "bathrobe", "nightie",
        "sundress", "kilt", "sarong", "lehenga", "chima", "shirt", "blouse",
        "suit", "jacket", "coat", "corset", "vest", "top", "pants", "shorts",
        "jeans", "swimsuit", "bikini", "uniform", "changshan", "daopao",
        "uchikake", "sari", "dirndl", "furisode", "tunic", "slip", "caftan",
        "abaya", "overalls", "dungarees"
    )
    if any(w in t_low for w in garment_nouns):
        return False
    return bool(re.search(
        r"\b(?:untied belt|belts?|sash|obi|sleeves?|floral embroidery|embroidery|embroidered ribbons?|ribbons?|collars?|scarf|scarves|bow tie|necktie|ties?|cuffs?|headbands?|pompoms?|badges?|brooches?|harness|studs|push-up cups|tulle fabric|delicate gauze)\b",
        t_low,
        re.IGNORECASE,
    ))


# ─── 单源真实源 (SSOT): 提示词级服装载体前提核验 ───
_SLIT_INCAPABLE_ONE_PIECE_IDS: Set[str] = NON_SKIRT_ONE_PIECE - {"qipao", "ao_dai", "anime_cosplay"}
_SKIRT_ENSEMBLE_IDS: Set[str] = {"jk_seifuku", "korean_school", "hanbok", "waitress_uniform"}
_EXTENSION_MODIFIER_IDS: Set[str] = {
    "auto_linkage", "linkage_general", "high_slit", "collar_cutout",
    "off_shoulder", "subtle_cleavage", "waist_cutouts", "crop_midriff",
    "multipoint_cutout", "plunging_backless", "open_sides",
}
_SKIRT_OR_SLIT_DRESS_TEXT_RE = re.compile(
    r"\b(?:skirt|miniskirt|microskirt|skort|dress|sundress|minidress|gown|nightgown|nightdress|nightie|qipao|cheongsam|kimono|yukata|furisode|uchikake|hanfu|hanbok|chima|sari|dirndl|petticoat|tutu|kilt|sarong|lehenga|toga|cassock|frock|abaya|caftan|lolita|mahou\s+shoujo|miko\s+clothing|ao\s+dai|apron\s+dress|short\s+apron|changshan|daygown|thin\s+strap\s+silk\s+slip|satin\s+slip)\b",
    re.IGNORECASE,
)
_STRICT_DRESS_TEXT_RE = re.compile(
    r"\b(?:dress|sundress|minidress|gown|nightgown|nightdress|nightie|qipao|cheongsam|kimono|yukata|furisode|uchikake|hanfu|hanbok|dirndl|toga|cassock|frock|abaya|caftan|daygown)\b",
    re.IGNORECASE,
)
_NON_DRESS_ONE_PIECE_TEXT_RE = re.compile(
    r"\b(?:leotard|bodysuit|catsuit|jumpsuit|plugsuit|skinsuit|techsuit|zentai|swimsuit|sukumizu|overalls|dungarees|romper|racing\s+suit|bunny\s+suit|bloomers|tuxedo)\b",
    re.IGNORECASE,
)
_PANTS_TEXT_ONLY_RE = re.compile(
    r"\b(?:pants|jeans|trousers|shorts|slacks|leggings|capris|culottes|joggers|sweatpants|cargo\s+pants|hot\s+pants|denim\s+shorts|bloomers|overalls|dungarees)\b",
    re.IGNORECASE,
)
_BRA_GARMENT_TEXT_RE = re.compile(
    r"\b(?:bra|bralette|bustier|bikini\s+top|lingerie\s+set|sports\s+bra)\b",
    re.IGNORECASE,
)
_UPPER_GARMENT_TEXT_RE = re.compile(
    r"\b(?:shirt|blouse|top|t-shirt|tee|sweater|hoodie|cardigan|jacket|coat|vest|blazer|tunic|dress|sundress|gown|kimono|yukata|hanfu|qipao|cheongsam|leotard|bodysuit|catsuit|jumpsuit|plugsuit|swimsuit|sukumizu|uniform|robe|corset|bustier|camisole|tank\s+top|tube\s+top|bra|bralette)\b",
    re.IGNORECASE,
)
_BUTTONLESS_UPPER_TEXT_RE = re.compile(
    r"\b(?:t-shirt|tee|tshirt|tank\s*top|crop\s*top|tube\s*top|sports\s+bra|camisole|bralette|pullover|sweater|sweatshirt|hoodie|swimsuit|bikini|monokini|leotard|bodysuit|catsuit|plugsuit|zentai|sukumizu|bunny\s+suit|jumpsuit|racing\s+suit)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_SKIRT_SLIT_RE = re.compile(
    r"\b(?:skirt\s+slit|high\s+slit|high\s+side\s+slit|slit\s+revealing\s+thigh)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_TOP_AND_SKIRT_RE = re.compile(
    r"\btop\s+and\s+skirt\b",
    re.IGNORECASE,
)
_SEPARATE_TOP_TEXT_RE = re.compile(
    r"\b(?:top|shirt|blouse|t-shirt|tee|sweater|hoodie|cardigan|jacket|coat|vest|blazer|camisole|tank\s+top|tube\s+top|jeogori|bikini\s+top|crop\s+top)\b",
    re.IGNORECASE,
)
_SKIRT_BOTTOM_TEXT_RE = re.compile(
    r"\b(?:skirt|miniskirt|microskirt|skort|petticoat|tutu|kilt|chima|sarong|lehenga)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_SKIRT_ACTION_RE = re.compile(
    r"\bskirt\b",
    re.IGNORECASE,
)
_CARRIER_REQ_SKIRT_VERBS_RE = re.compile(
    r"\b(?:hiked|pulled|hitched|button|unbuttoned|remaining|still\s+on|under|lifted|floating|blown|slit)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_DRESS_RE = re.compile(
    r"\b(?:dress\s+(?:pulled|bunched|hitched|sliding)|pulled\s+down\s+dress)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_PANTS_RE = re.compile(
    r"\b(?:pants\s+button\s+undone|jeans\s+unbuttoned|bare\s+chest\s+with\s+pants\s+on)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_BRA_RE = re.compile(
    r"\b(?:bra\s+pulled\s+down|bra\s+pushed\s+up)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_BUTTONED_UPPER_RE = re.compile(
    r"\b(?:one\s+button\s+undone|blouse\s+unbuttoned|buttons?\s+undone|shirt\s+open\s+at\s+chest|shirt\s+open\s+showing|collar\s+unbuttoned|suit\s+unbuttoned)\b",
    re.IGNORECASE,
)
_CARRIER_REQ_UPPER_TOP_RE = re.compile(
    r"\b(?:tank\s+top\s+raised|shirt\s+pulled\s+down|untucked\s+shirt|sweater\s+falling\s+off|sweat-soaked\s+shirt)\b",
    re.IGNORECASE,
)
_UPPER_EXTENSION_CARRIER_IDS: Set[str] = {
    "collar_cutout", "off_shoulder", "subtle_cleavage",
    "plunging_backless", "crop_midriff", "waist_cutouts", "open_sides",
}


def _unpack_carrier_subject(obj: Any, style_id: str = "") -> Dict[str, Any]:
    """统一解包 PromptAtom / SampledTag / JSON dict / plain str 为标准化载体检查字典。"""
    if isinstance(obj, str):
        return {
            "text_low": obj.lower().strip(),
            "facts": None,
            "facts_dict": {},
            "source_slot": "",
            "prov_kind": "",
            "origin_sel": "",
            "sid": (style_id or "").lower(),
            "raw_item_id": "",
            "role": "",
            "topos": (),
            "covered": (),
            "garment_states": (),
            "sem_role": "",
            "ensemble_pieces": None,
            "is_modifier_entry": False,
        }
    if isinstance(obj, dict):
        facts = obj.get("facts")
        facts_dict = facts.to_dict() if isinstance(facts, SemanticFacts) else (facts if isinstance(facts, dict) else {})
        text_low = str(obj.get("text") or "").lower().strip()
        source_slot = str(obj.get("source_slot") or "").lower()
        prov = obj.get("provenance")
        prov_kind = (prov.get("kind") if isinstance(prov, dict) else getattr(prov, "kind", "")) or ""
        raw_id = str(obj.get("id") or obj.get("source_item_id") or "")
        if "__tag_" in raw_id or "__ext_" in raw_id:
            raw_item_id = raw_id.split("__", 1)[0].lower()
        else:
            raw_item_id = raw_id.lower()
        sid = (style_id or raw_item_id).lower()
        origin_sel = ""
    else:
        facts = getattr(obj, "facts", None)
        if isinstance(facts, SemanticFacts):
            facts_dict = {
                "piece_role": facts.piece_role,
                "garment_topologies": facts.garment_topologies,
                "covered_regions": facts.covered_regions,
                "garment_states": facts.garment_states,
                "semantic_role": facts.semantic_role,
                "ensemble_pieces": facts.ensemble_pieces,
            }
        elif isinstance(facts, dict):
            facts_dict = facts
        else:
            facts_dict = {}
        text_low = str(getattr(obj, "text", "") or "").lower().strip()
        source_slot = str(getattr(obj, "source_slot", "") or "").lower()
        prov = getattr(obj, "provenance", None)
        prov_kind = str(getattr(prov, "kind", "") or "").lower()
        origin = getattr(obj, "origin", None)
        origin_sel = str(getattr(origin, "selector", "") or "").lower()
        raw_candidates = [
            getattr(obj, "source_item_id", None) or "",
            getattr(prov, "item_id", None) or "" if prov else "",
            getattr(origin, "selected_id", None) or "" if origin else "",
        ]
        raw_item_id = ""
        for cand in raw_candidates:
            c_low = str(cand).strip().lower()
            if c_low and c_low not in ("custom", "candidate", "none", "random", "auto") and not c_low.startswith("rule:"):
                raw_item_id = c_low.split("__", 1)[0]
                break
        sid = (style_id or raw_item_id).lower()

    role = str(facts_dict.get("piece_role") or "").lower()
    topos = tuple(str(t) for t in (facts_dict.get("garment_topologies") or ()) if t)
    covered = tuple(str(r) for r in (facts_dict.get("covered_regions") or ()) if r)
    garment_states = tuple(str(s) for s in (facts_dict.get("garment_states") or ()) if s)
    sem_role = str(facts_dict.get("semantic_role") or "").lower()
    ensemble_pieces = facts_dict.get("ensemble_pieces")
    obj_sem_ids = getattr(obj, "semantic_ids", None) or ()
    for sm in obj_sem_ids:
        if sm in ("override:linkage", "linkage:general"):
            origin_sel = origin_sel or "clothing_state"
    is_modifier_entry = bool(
        source_slot in ("nudity", "clothing_state", "clothing_extension")
        or prov_kind in ("nudity", "clothing_state", "clothing_extension")
        or origin_sel in ("nudity", "clothing_state", "clothing_extension")
        or raw_item_id in _EXTENSION_MODIFIER_IDS
    )
    return {
        "text_low": text_low,
        "facts": facts,
        "facts_dict": facts_dict,
        "source_slot": source_slot,
        "prov_kind": prov_kind,
        "origin_sel": origin_sel,
        "sid": sid,
        "raw_item_id": raw_item_id,
        "role": role,
        "topos": topos,
        "covered": covered,
        "garment_states": garment_states,
        "sem_role": sem_role,
        "ensemble_pieces": ensemble_pieces,
        "is_modifier_entry": is_modifier_entry,
    }


def _is_worn_garment_carrier(obj: Any, style_id: str = "") -> bool:
    """判定对象是否为在穿主衣物或贴身衣物实体，排除 nudity / 状态联动 / 配件 / 已脱弃物。"""
    info = _unpack_carrier_subject(obj, style_id=style_id)
    if info["is_modifier_entry"]:
        return False
    if "discarded" in info["garment_states"]:
        return False
    if info["role"] in ("none", "accessory", "legwear", "footwear"):
        return False
    if info["source_slot"] and info["source_slot"] not in ("clothing", "underwear"):
        return False
    if _is_accessory_or_detail(info["text_low"]):
        return False
    if not info["text_low"] and not info["topos"] and not info["role"]:
        return False
    return True


def _iter_ensemble_piece_texts(ep: Any) -> Tuple[List[Tuple[str, Optional[str], str]], List[str]]:
    """统一读取 EnsemblePieces 或 raw dict 中的构件拓扑与文本列表。"""
    bindings: List[Tuple[str, Optional[str], str]] = []
    extra_bottoms: List[str] = []
    if isinstance(ep, dict):
        for pb in ep.get("piece_bindings") or ():
            if isinstance(pb, dict):
                bindings.append((
                    str(pb.get("piece_slot") or ""),
                    pb.get("garment_topology"),
                    str(pb.get("piece_text") or pb.get("piece_id") or "").lower(),
                ))
        extra_bottoms.extend(str(x).lower() for x in (ep.get("bottom_pieces") or ()))
        extra_bottoms.extend(str(x).lower() for x in (ep.get("main_garments") or ()))
    else:
        for pb in getattr(ep, "piece_bindings", ()) or ():
            bindings.append((
                str(getattr(pb, "piece_slot", "") or ""),
                getattr(pb, "garment_topology", None),
                str(getattr(pb, "piece_text", None) or getattr(pb, "piece_id", "") or "").lower(),
            ))
        extra_bottoms.extend(str(x).lower() for x in (getattr(ep, "bottom_pieces", ()) or ()))
        extra_bottoms.extend(str(x).lower() for x in (getattr(ep, "main_garments", ()) or ()))
    return bindings, extra_bottoms


def is_item_skirt_or_slit_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否真正具备裙装或可开衩长裙/袍服载体（严禁 leotard/jumpsuit/pantsuit 冒充）。"""
    if not _is_worn_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    text_low = info["text_low"]
    topos = info["topos"]
    role = info["role"]
    sid = info["sid"]
    ep = info["ensemble_pieces"]

    if ep is not None:
        bindings, extra_pieces = _iter_ensemble_piece_texts(ep)
        for slot_name, pb_topo, p_txt in bindings:
            if pb_topo == "bottom_skirt":
                return True
            if slot_name in ("bottom_pieces", "main_garments") and not pb_topo:
                if _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(p_txt) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(p_txt):
                    return True
        return any(
            _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(pt) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(pt)
            for pt in extra_pieces
        )

    if "bottom_skirt" in topos:
        return True
    if sid in _SLIT_INCAPABLE_ONE_PIECE_IDS:
        return bool(_SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low))
    if "bottom_pants" in topos and "one_piece" not in topos:
        return bool(_SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low))
    if "one_piece" in topos:
        if _NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low) and not _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low):
            return False
        return True
    if role == "ensemble" or "ensemble_outfit" in topos:
        if _NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low) and not _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low):
            return False
        if _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low):
            return True
        return bool(sid in _SKIRT_ENSEMBLE_IDS and not _PANTS_TEXT_ONLY_RE.search(text_low))
    return bool(
        _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low)
        and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low)
    )


def is_item_dress_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否为连衣裙/连体袍服类载物（半身裙、裤装、连体紧身衣不在此列）。"""
    if not _is_worn_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    text_low = info["text_low"]
    topos = info["topos"]
    role = info["role"]
    sid = info["sid"]
    ep = info["ensemble_pieces"]

    if ep is not None:
        bindings, extra_pieces = _iter_ensemble_piece_texts(ep)
        for slot_name, _, p_txt in bindings:
            if slot_name == "main_garments" and _STRICT_DRESS_TEXT_RE.search(p_txt) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(p_txt) and "dress shirt" not in p_txt:
                return True
        return any(
            _STRICT_DRESS_TEXT_RE.search(pt) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(pt) and "dress shirt" not in pt
            for pt in extra_pieces
        )
    if sid in _SLIT_INCAPABLE_ONE_PIECE_IDS:
        return False
    if "bottom_skirt" in topos and "one_piece" not in topos:
        return False
    if "one_piece" in topos:
        return not (_NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low) and not RE_DRESS_ACTION.search(text_low))
    if role == "ensemble" or "ensemble_outfit" in topos:
        return bool(_STRICT_DRESS_TEXT_RE.search(text_low) and "dress shirt" not in text_low)
    return bool(_STRICT_DRESS_TEXT_RE.search(text_low) and not _NON_DRESS_ONE_PIECE_TEXT_RE.search(text_low) and "dress shirt" not in text_low)


def is_item_pants_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否具备裤装载体（jeans / pants / shorts / trousers / leggings）。"""
    if not _is_worn_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    text_low = info["text_low"]
    topos = info["topos"]
    ep = info["ensemble_pieces"]
    if ep is not None:
        bindings, extra_pieces = _iter_ensemble_piece_texts(ep)
        for _, pb_topo, p_txt in bindings:
            if pb_topo == "bottom_pants" or (_PANTS_TEXT_ONLY_RE.search(p_txt) and not _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(p_txt)):
                return True
        return any(_PANTS_TEXT_ONLY_RE.search(pt) and not _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(pt) for pt in extra_pieces)
    if "bottom_pants" in topos:
        return True
    return bool(_PANTS_TEXT_ONLY_RE.search(text_low) and not _SKIRT_OR_SLIT_DRESS_TEXT_RE.search(text_low))


def is_item_bra_carrier(obj: Any) -> bool:
    """判定衣物项是否为文胸/胸衣实体载体（不含 no bra / bra edge 描述）。"""
    if not _is_worn_garment_carrier(obj):
        return False
    info = _unpack_carrier_subject(obj)
    tl = info["text_low"]
    if any(neg in tl for neg in ("no bra", "braless", "bra removed", "bra edge", "showing lace bra")):
        return False
    if "intimate_upper" in info["covered"]:
        return True
    return bool(
        (info["role"] == "underwear" or info["source_slot"] in ("clothing", "underwear") or "underwear" in info["topos"] or "bikini" in info["topos"])
        and _BRA_GARMENT_TEXT_RE.search(tl)
    )


def is_item_upper_garment_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否具备上身衣物载体（top / outerwear / one_piece / ensemble）。"""
    if not _is_worn_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    topos = info["topos"]
    role = info["role"]
    if any(t in topos for t in ("top", "outerwear", "one_piece", "ensemble_outfit")):
        return True
    if role in ("top", "outerwear", "one_piece", "ensemble"):
        return True
    if any(t in topos for t in ("bottom_pants", "bottom_skirt")) and not any(t in topos for t in ("top", "outerwear", "one_piece", "ensemble_outfit")):
        return False
    return bool(_UPPER_GARMENT_TEXT_RE.search(info["text_low"]))


def is_item_button_or_collar_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否具备纽扣开襟/衬衫领口载体（排除纯裤装、纯裙装、t-shirt/leotard 等无扣无领款）。"""
    if not is_item_upper_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    tl = info["text_low"]
    sid = info["sid"]
    if RE_BUTTON_NEGATION.search(tl):
        return False
    if sid in ALLOWED_BUTTON_STYLES or sid in ("jk_seifuku", "korean_school", "blazer_uniform", "ol_suit", "waitress_uniform", "maid_dress", "nurse_uniform"):
        return True
    if RE_BUTTON_AFFIRMATION.search(tl) or RE_BUTTON_STYLE_KEYWORDS.search(tl):
        return True
    if _BUTTONLESS_UPPER_TEXT_RE.search(tl):
        return False
    if "bottom_pants" in info["topos"] and not any(t in info["topos"] for t in ("top", "outerwear", "one_piece", "ensemble_outfit")):
        return False
    return False


def _is_item_separate_top_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否具备独立上衣/上身外搭载体（严禁纯连衣裙 one_piece 或纯下装冒充）。"""
    if not _is_worn_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    topos = info["topos"]
    role = info["role"]
    tl = info["text_low"]
    ep = info["ensemble_pieces"]
    if ep is not None:
        bindings, extra_pieces = _iter_ensemble_piece_texts(ep)
        for slot_name, pb_topo, p_txt in bindings:
            if slot_name == "top_pieces" or pb_topo in ("top", "outerwear"):
                return True
            if pb_topo != "bottom_skirt" and pb_topo != "bottom_pants" and _SEPARATE_TOP_TEXT_RE.search(p_txt):
                return True
        if any(_SEPARATE_TOP_TEXT_RE.search(pt) and not _STRICT_DRESS_TEXT_RE.search(pt) for pt in extra_pieces):
            return True
    if "top" in topos or role == "top" or "outerwear" in topos or role == "outerwear":
        return True
    if ("one_piece" in topos or role == "one_piece") and "top" not in topos:
        return False
    if any(t in topos for t in ("bottom_pants", "bottom_skirt")) or role == "bottom":
        return False
    return bool(
        _SEPARATE_TOP_TEXT_RE.search(tl)
        and "without top" not in tl
        and "no top" not in tl
        and "topless" not in tl
    )


def _is_item_skirt_bottom_carrier(obj: Any, style_id: str = "") -> bool:
    """判定衣物项是否具备半身裙载体（严禁连衣裙 one_piece 或裤装冒充分体裙）。"""
    if not _is_worn_garment_carrier(obj, style_id=style_id):
        return False
    info = _unpack_carrier_subject(obj, style_id=style_id)
    topos = info["topos"]
    role = info["role"]
    tl = info["text_low"]
    ep = info["ensemble_pieces"]
    if ep is not None:
        bindings, extra_pieces = _iter_ensemble_piece_texts(ep)
        for slot_name, pb_topo, p_txt in bindings:
            if pb_topo == "bottom_skirt":
                return True
            if slot_name == "bottom_pieces" and pb_topo != "bottom_pants" and _SKIRT_BOTTOM_TEXT_RE.search(p_txt):
                return True
        if any(_SKIRT_BOTTOM_TEXT_RE.search(pt) for pt in extra_pieces):
            return True
    if "bottom_skirt" in topos:
        return True
    if ("one_piece" in topos or role == "one_piece") and "bottom_skirt" not in topos:
        return False
    if "bottom_pants" in topos and "bottom_skirt" not in topos:
        return False
    return bool(_SKIRT_BOTTOM_TEXT_RE.search(tl) and "no skirt" not in tl)


def get_required_carrier_kind(obj: Any, default_slot: str = "") -> Optional[str]:
    """返回非基础衣物修饰/裸露提示词所依赖的物理载体类别，无载体硬前提或为丢弃态时返回 None。"""
    if isinstance(obj, PromptAtom) and obj.contains_blackbox:
        return None
    info = _unpack_carrier_subject(obj)
    slot = info["source_slot"] or default_slot.lower()
    is_mod = info["is_modifier_entry"] or slot in ("nudity", "clothing_state", "clothing_extension")
    if not is_mod:
        return None
    tl = info["text_low"]
    if "discarded" in info["garment_states"] or any(k in tl for k in ("tossed aside", "discarded on", "scattered beside", "all clothes removed")):
        return None
    sid = info["sid"] or info["raw_item_id"]
    if _CARRIER_REQ_TOP_AND_SKIRT_RE.search(tl):
        return "top_and_skirt"
    if sid == "high_slit" or _CARRIER_REQ_SKIRT_SLIT_RE.search(tl):
        return "skirt_slit"
    if _CARRIER_REQ_SKIRT_ACTION_RE.search(tl) and (
        _CARRIER_REQ_SKIRT_VERBS_RE.search(tl) or slot in ("clothing_state", "clothing_extension")
    ):
        if "no skirt" not in tl:
            return "skirt_slit"
    if _CARRIER_REQ_DRESS_RE.search(tl) and "dress shirt" not in tl:
        return "dress"
    if _CARRIER_REQ_PANTS_RE.search(tl):
        return "pants"
    if _CARRIER_REQ_BRA_RE.search(tl):
        return "bra"
    if _CARRIER_REQ_BUTTONED_UPPER_RE.search(tl):
        return "buttoned_upper"
    if _CARRIER_REQ_UPPER_TOP_RE.search(tl) or sid in _UPPER_EXTENSION_CARRIER_IDS:
        return "upper_top"
    return None


def is_carrier_satisfied_by_garments(required_kind: str, garment_objs: Sequence[Any], style_id: str = "") -> bool:
    """检查候选或在穿衣物列表是否满足指定的载体类别要求（组合载体必须同时具备全部组成载体）。"""
    if required_kind == "top_and_skirt":
        return any(_is_item_separate_top_carrier(g, style_id=style_id) for g in garment_objs) and any(
            _is_item_skirt_bottom_carrier(g, style_id=style_id) for g in garment_objs
        )
    if required_kind == "separate_top":
        return any(_is_item_separate_top_carrier(g, style_id=style_id) for g in garment_objs)
    if required_kind == "skirt_bottom":
        return any(_is_item_skirt_bottom_carrier(g, style_id=style_id) for g in garment_objs)
    if required_kind == "skirt_slit":
        return any(is_item_skirt_or_slit_carrier(g, style_id=style_id) for g in garment_objs)
    if required_kind == "dress":
        return any(is_item_dress_carrier(g, style_id=style_id) for g in garment_objs)
    if required_kind == "pants":
        return any(is_item_pants_carrier(g, style_id=style_id) for g in garment_objs)
    if required_kind == "bra":
        return any(is_item_bra_carrier(g) for g in garment_objs)
    if required_kind == "buttoned_upper":
        return any(is_item_button_or_collar_carrier(g, style_id=style_id) for g in garment_objs)
    if required_kind == "upper_top":
        return any(is_item_upper_garment_carrier(g, style_id=style_id) for g in garment_objs)
    return True


def is_carrier_satisfied_by_style_pool(
    required_kind: str,
    garment_pool: Sequence[Any],
    style_id: str = "",
    *,
    is_single_tag: bool = False,
) -> bool:
    """校验风格候选池（单件替代项列表或多件套组类别）是否允许满足指定载体前提。"""
    if required_kind != "top_and_skirt":
        return is_carrier_satisfied_by_garments(required_kind, garment_pool, style_id=style_id)
    if is_single_tag or len(garment_pool) <= 1:
        return is_carrier_satisfied_by_garments("top_and_skirt", garment_pool, style_id=style_id)
    if any(is_carrier_satisfied_by_garments("top_and_skirt", [g], style_id=style_id) for g in garment_pool):
        return True
    is_multi_piece_ensemble = any(
        "ensemble_outfit" in (((g.get("facts") or {}).get("garment_topologies")) or ())
        for g in garment_pool
        if isinstance(g, dict)
    ) or (style_id in (_SKIRT_ENSEMBLE_IDS | {"blazer_uniform", "ol_suit", "cheerleader"}))
    return bool(
        is_multi_piece_ensemble
        and is_carrier_satisfied_by_garments("top_and_skirt", garment_pool, style_id=style_id)
    )


def find_carrier_mismatch_in_atoms(
    atoms: Sequence[PromptAtom],
) -> List[Tuple[PromptAtom, str, Tuple[PromptAtom, ...]]]:
    """核验原子集合中每个需载体修饰词（含 nudity / clothing_state / clothing_extension）是否具备在穿衣物载体；实际服装为空时同样严格拦截。"""
    detectable = [a for a in atoms if getattr(a, "can_detect", True)]
    worn_garments = [a for a in detectable if _is_worn_garment_carrier(a)]
    mismatches: List[Tuple[PromptAtom, str, Tuple[PromptAtom, ...]]] = []
    for a in detectable:
        req = get_required_carrier_kind(a)
        if req is None:
            continue
        if not worn_garments or not is_carrier_satisfied_by_garments(req, worn_garments):
            mismatches.append((a, req, tuple(worn_garments)))
    return mismatches


class CapabilitySupport(Enum):
    """构件/服装物理开合能力判定三态契约：明确支持、明确不支持、未知。"""
    SUPPORTED = "supported"        # 明确支持
    UNSUPPORTED = "unsupported"    # 明确不支持
    UNKNOWN = "unknown"            # 未知


RE_BUTTON_NEGATION = re.compile(
    r"\b(?:buttonless|no\s+buttons?|without\s+buttons?|zipper\s+only)\b",
    re.IGNORECASE,
)
RE_BUTTON_EXCLUDED_TYPES = re.compile(
    r"\b(?:t-shirt|tee|tshirt|tank\s*top|crop\s*top|tube\s*top|sports\s+bra|camisole|bralette|pullover|sweater|sweatshirt|hoodie|swimsuit|bikini|monokini|leotard|bodysuit|catsuit|zentai)\b",
    re.IGNORECASE,
)
RE_BUTTON_AFFIRMATION = re.compile(
    r"\b(?:button-down|button-up|buttoned-up|buttoned|buttons?|single-breasted|double-breasted)\b",
    re.IGNORECASE,
)
RE_BUTTON_STYLE_KEYWORDS = re.compile(
    r"\b(?:shirt|blouse|cardigan|blazer|waistcoat|vest|polo\s+shirt|polo|trench\s+coat|lab\s+coat|dungarees|overcoat|duffel\s+coat|tailcoat)\b",
    re.IGNORECASE,
)

RE_ZIPPER_NEGATION = re.compile(
    r"\b(?:zipperless|no\s+(?:zipper|zippers|zip)\b|without\s+(?:zipper|zippers|zip)\b|button(?:s|-down|-up)?\s+only)\b",
    re.IGNORECASE,
)
RE_ZIPPER_EXCLUDED_TYPES = re.compile(
    r"\b(?:t-shirt|tee|tshirt|tank\s*top|crop\s*top|tube\s*top|sports\s+bra|camisole|bralette|pullover|sweater|sweatshirt|button-down\s+shirt|dress\s+shirt|swimsuit|bikini|monokini)\b",
    re.IGNORECASE,
)
RE_ZIPPER_AFFIRMATION = re.compile(
    r"\b(?:zippers?|zippered|zipped|zipping|zip-up|full-zip|half-zip|quarter-zip|front\s+zip(?:per)?)\b",
    re.IGNORECASE,
)


def _extract_capability_inspect_text(item: Any) -> str:
    """提取构件、原子或实体的全部相关标识与文本描述。"""
    parts: List[str] = []
    if isinstance(item, str):
        parts.append(item)
    elif hasattr(item, "member_atoms"):
        if getattr(item, "selected_id", None):
            parts.append(str(item.selected_id))
        if getattr(item, "entity_id", None):
            parts.append(str(item.entity_id))
        for a in item.member_atoms:
            if getattr(a, "text", None):
                parts.append(a.text)
            if getattr(a, "source_item_id", None):
                parts.append(a.source_item_id)
    elif hasattr(item, "piece_text"):
        parts.append(getattr(item, "piece_text", ""))
        parts.append(getattr(item, "piece_id", ""))
        parts.append(getattr(item, "piece_slot", ""))
    elif hasattr(item, "text"):
        parts.append(getattr(item, "text", ""))
        if getattr(item, "source_item_id", None):
            parts.append(item.source_item_id)
    return " ".join(p for p in parts if p).lower()


def get_button_capability(item: Any) -> CapabilitySupport:
    """判定构件或实体的纽扣开合能力：优先处理明确否定，区分明确支持、明确不支持、未知。"""
    s = _extract_capability_inspect_text(item)
    # 1. 优先处理明确否定
    if RE_BUTTON_NEGATION.search(s):
        return CapabilitySupport.UNSUPPORTED
    # 2. 结构上无纽扣的排他款式（未包含纽扣正面肯定词时）
    if RE_BUTTON_EXCLUDED_TYPES.search(s) and not RE_BUTTON_AFFIRMATION.search(s):
        return CapabilitySupport.UNSUPPORTED
    # 3. 显式肯定词
    if RE_BUTTON_AFFIRMATION.search(s):
        return CapabilitySupport.SUPPORTED
    # 4. 白名单款式
    sid = getattr(item, "selected_id", None) or getattr(item, "piece_id", None)
    if sid and sid in ALLOWED_BUTTON_STYLES:
        return CapabilitySupport.SUPPORTED
    if any(st in s.split() for st in ALLOWED_BUTTON_STYLES):
        return CapabilitySupport.SUPPORTED
    # 5. 未知款式（严格 Fail-Closed）
    return CapabilitySupport.UNKNOWN


def get_zipper_capability(item: Any) -> CapabilitySupport:
    """判定构件或实体的拉链开合能力：优先处理明确否定，区分明确支持、明确不支持、未知。"""
    s = _extract_capability_inspect_text(item)
    # 1. 优先处理明确否定
    if RE_ZIPPER_NEGATION.search(s):
        return CapabilitySupport.UNSUPPORTED
    # 2. 结构上无拉链的排他款式（未包含拉链正面肯定词时）
    if RE_ZIPPER_EXCLUDED_TYPES.search(s) and not RE_ZIPPER_AFFIRMATION.search(s):
        return CapabilitySupport.UNSUPPORTED
    # 3. 显式肯定词
    if RE_ZIPPER_AFFIRMATION.search(s):
        return CapabilitySupport.SUPPORTED
    # 4. 白名单款式
    sid = getattr(item, "selected_id", None) or getattr(item, "piece_id", None)
    if sid and sid in ALLOWED_ZIPPER_STYLES:
        return CapabilitySupport.SUPPORTED
    if any(st in s.split() for st in ALLOWED_ZIPPER_STYLES):
        return CapabilitySupport.SUPPORTED
    # 5. 未知款式（如普通未标拉链的 hoodie, jacket 等未指明拉链结构者，均视为未知）
    return CapabilitySupport.UNKNOWN


def piece_has_button_capability(piece: Any) -> bool:
    """判定构件是否具备纽扣开合能力：仅明确支持返回 True，未知与明确不支持均 Fail-Closed。"""
    return get_button_capability(piece) == CapabilitySupport.SUPPORTED


def piece_has_zipper_capability(piece: Any) -> bool:
    """判定构件是否具备拉链开合能力：仅明确支持返回 True，未知与明确不支持均 Fail-Closed。"""
    return get_zipper_capability(piece) == CapabilitySupport.SUPPORTED


def _infer_piece_topology(pb: Any) -> str:
    """获取或推导构件的服装拓扑。"""
    topo = getattr(pb, "garment_topology", None)
    if topo:
        return topo
    slot = getattr(pb, "piece_slot", "")
    p_text = (getattr(pb, "piece_text", "") or getattr(pb, "piece_id", "") or (pb if isinstance(pb, str) else "")).lower()
    if slot in ("top_pieces", "tops"):
        return "top"
    elif slot == "outer_layers":
        return "outerwear"
    elif slot in ("main_garments", "one_piece"):
        return "one_piece"
    elif slot == "bottom_pieces":
        if any(k in p_text for k in ("skirt", "pleated", "tutu", "petticoat", "kilt", "sarong")) and not any(k in p_text for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings")):
            return "bottom_skirt"
        elif any(k in p_text for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings")):
            return "bottom_pants"
        return "bottom"
    if any(k in p_text for k in ("jacket", "coat", "vest", "parka", "overcoat", "cardigan", "blazer", "hoodie")):
        return "outerwear"
    elif any(k in p_text for k in ("shirt", "blouse", "top", "tee", "polo")):
        return "top"
    elif any(k in p_text for k in ("skirt", "dress")):
        return "bottom_skirt"
    elif any(k in p_text for k in ("pants", "jeans", "shorts")):
        return "bottom_pants"
    return ""


def _extract_ensemble_pieces_list(entity: GarmentCarrierEntity) -> List[Any]:
    """提取实体中的全部套装构件（优先 piece_bindings，回退至文本列表）。"""
    for a in entity.member_atoms:
        if a.facts and getattr(a.facts, "ensemble_pieces", None):
            ep = a.facts.ensemble_pieces
            if getattr(ep, "piece_bindings", None) and len(ep.piece_bindings) > 0:
                return list(ep.piece_bindings)
            pieces = []
            for tp in getattr(ep, "top_pieces", ()):
                pieces.append(PieceBinding(piece_id=tp, piece_slot="top_pieces", piece_text=tp, garment_topology="top"))
            for ol in getattr(ep, "outer_layers", ()):
                pieces.append(PieceBinding(piece_id=ol, piece_slot="outer_layers", piece_text=ol, garment_topology="outerwear"))
            for mg in getattr(ep, "main_garments", ()):
                pieces.append(PieceBinding(piece_id=mg, piece_slot="main_garments", piece_text=mg, garment_topology="one_piece"))
            for bp in getattr(ep, "bottom_pieces", ()):
                bp_topo = "bottom_skirt" if "skirt" in bp.lower() else ("bottom_pants" if any(k in bp.lower() for k in ("pants", "jeans", "shorts")) else "bottom")
                pieces.append(PieceBinding(piece_id=bp, piece_slot="bottom_pieces", piece_text=bp, garment_topology=bp_topo))
            return pieces
    return []


def _resolve_target_pieces_for_ensemble(
    pieces: Sequence[Any],
    state_id: str,
    state_atom: Optional[PromptAtom],
    entity: Optional[GarmentCarrierEntity] = None,
    target_id: Optional[str] = None,
) -> Tuple[List[Any], bool]:
    """确定动作在套装中的目标构件集合，返回 (target_pieces, is_specifically_targeted)。"""
    if not pieces:
        return [], False

    effective_target_id = target_id if target_id is not None else (getattr(state_atom, "target_id", None) if state_atom else None)
    atom_text = (state_atom.text or "").lower() if state_atom else ""
    atom_topos = set(state_atom.facts.garment_topologies) if (state_atom and state_atom.facts and state_atom.facts.garment_topologies) else set()

    # 1. 显式 target_id 处理
    if effective_target_id is not None:
        target_id_lower = effective_target_id.lower()
        # 区分：若 effective_target_id 匹配套装/实体自身 ID (entity.selected_id / entity_id)，说明动作显式靶向整套服装实体，
        # 而非限定于特定子构件！已有实体目标不能被误当成构件 ID，应下沉至动作语义解析具体的承载构件。
        is_entity_level_target = False
        if entity is not None:
            if target_id_lower in (entity.selected_id.lower(), entity.entity_id.lower()):
                is_entity_level_target = True

        if not is_entity_level_target:
            matched = [
                pb for pb in pieces
                if getattr(pb, "piece_id", "").lower() == target_id_lower
                or getattr(pb, "piece_slot", "").lower() == target_id_lower
                or (getattr(pb, "piece_text", "") and target_id_lower in getattr(pb, "piece_text", "").lower())
            ]
            return matched, True

    # 2. 动作通过文本正则或形制事实明确指定目标构件形制
    if RE_SKIRT_ACTION.search(atom_text) or atom_topos == {"bottom_skirt"}:
        matched = [
            pb for pb in pieces
            if _infer_piece_topology(pb) == "bottom_skirt"
            or (getattr(pb, "piece_slot", "") in ("bottom_pieces", "main_garments") and RE_SKIRT_ACTION.search(getattr(pb, "piece_text", "") or ""))
        ]
        return matched, True

    if RE_PANTS_ACTION.search(atom_text) or atom_topos == {"bottom_pants"}:
        matched = [
            pb for pb in pieces
            if _infer_piece_topology(pb) == "bottom_pants"
            or (getattr(pb, "piece_slot", "") in ("bottom_pieces", "main_garments") and RE_PANTS_ACTION.search(getattr(pb, "piece_text", "") or ""))
        ]
        return matched, True

    if RE_OUTERWEAR_ACTION.search(atom_text) or atom_topos == {"outerwear"}:
        matched = [
            pb for pb in pieces
            if _infer_piece_topology(pb) == "outerwear"
            or getattr(pb, "piece_slot", "") == "outer_layers"
            or RE_OUTERWEAR_ACTION.search(getattr(pb, "piece_text", "") or "")
        ]
        return matched, True

    if RE_SHIRT_ACTION.search(atom_text) or (atom_topos == {"top"} and not RE_CHEST_ACTION.search(atom_text)):
        matched = [
            pb for pb in pieces
            if _infer_piece_topology(pb) == "top"
            or getattr(pb, "piece_slot", "") == "top_pieces"
            or RE_SHIRT_ACTION.search(getattr(pb, "piece_text", "") or "")
        ]
        return matched, True

    if RE_ROBE_ACTION.search(atom_text):
        matched = [
            pb for pb in pieces
            if any(k in (getattr(pb, "piece_text", "") or "").lower() for k in ("robe", "bathrobe", "cassock", "gown"))
        ]
        return matched, True

    if RE_SUIT_ACTION.search(atom_text):
        matched = [
            pb for pb in pieces
            if any(k in (getattr(pb, "piece_text", "") or "").lower() for k in ("suit", "racing"))
        ]
        return matched, True

    if RE_CHEST_ACTION.search(atom_text):
        matched = [
            pb for pb in pieces
            if _infer_piece_topology(pb) in ("top", "outerwear", "one_piece")
            or getattr(pb, "piece_slot", "") in ("top_pieces", "outer_layers", "main_garments")
        ]
        return matched, True

    # 3. 通用动作（默认靶向上身承载构件）
    upper_pieces = [
        pb for pb in pieces
        if _infer_piece_topology(pb) in ("top", "outerwear", "one_piece")
        or getattr(pb, "piece_slot", "") in ("top_pieces", "outer_layers", "main_garments")
    ]
    if upper_pieces:
        return upper_pieces, False

    return list(pieces), False


def is_garment_compatible_with_state(
    entity: GarmentCarrierEntity,
    state_id: str,
    state_atom: Optional[PromptAtom] = None,
    target_id: Optional[str] = None,
) -> bool:
    """判定服装实体是否具备承载特定状态原子的物理形制能力（按特定款式的产品业务规则判定，支持多件套构件穿透）。"""
    if state_id in ("lifted_up", "lifted", "lifted_skirt"):
        is_skirt_compatible = any(
            a.facts and (
                "bottom_skirt" in a.facts.garment_topologies or
                does_garment_support_modifier(a.facts, "bottom_skirt") or
                ("one_piece" in a.facts.garment_topologies and entity.selected_id not in NON_SKIRT_ONE_PIECE)
            )
            for a in entity.member_atoms
        )
        if not is_skirt_compatible:
            return False
    elif state_id in ("unbuttoned", "opened", "unzipped"):
        pieces = _extract_ensemble_pieces_list(entity)
        is_zip = (state_id == "unzipped")

        if pieces:
            # ── 多件套构件判定 ──
            # 1. 确定动作对应的目标构件（不能借其他构件能力，也不能被无关构件否定）
            target_pieces, is_specifically_targeted = _resolve_target_pieces_for_ensemble(
                pieces, state_id, state_atom, entity=entity, target_id=target_id
            )
            if not target_pieces:
                return False

            # 2. 检查目标构件的能力（否定优先限定在明确被该动作靶向的构件内）
            if is_specifically_targeted:
                has_negation = any(
                    (get_zipper_capability(pb) == CapabilitySupport.UNSUPPORTED if is_zip else get_button_capability(pb) == CapabilitySupport.UNSUPPORTED)
                    for pb in target_pieces
                )
                if has_negation:
                    return False
                has_support = any(
                    (piece_has_zipper_capability(pb) if is_zip else piece_has_button_capability(pb))
                    for pb in target_pieces
                )
                if not has_support:
                    return False
            else:
                supported_pieces = [
                    pb for pb in target_pieces
                    if (piece_has_zipper_capability(pb) if is_zip else piece_has_button_capability(pb))
                ]
                if not supported_pieces:
                    return False
        else:
            # ── 单品实体判定 ──
            effective_target_id = target_id if target_id is not None else (getattr(state_atom, "target_id", None) if state_atom else None)
            if effective_target_id is not None:
                eff_lower = effective_target_id.lower()
                if eff_lower != entity.selected_id.lower() and eff_lower != entity.entity_id.lower():
                    return False
            if is_zip:
                if get_zipper_capability(entity) != CapabilitySupport.SUPPORTED:
                    return False
            else:
                if get_button_capability(entity) != CapabilitySupport.SUPPORTED:
                    return False
    elif state_id == "pulled_down":
        is_pulled_compatible = any(
            a.facts and any(
                t in a.facts.garment_topologies or does_garment_support_modifier(a.facts, t)
                for t in ("bottom_pants", "bottom_skirt", "underwear", "top", "one_piece")
            )
            for a in entity.member_atoms
        )
        if not is_pulled_compatible:
            return False

    if state_atom is not None:
        entity_topologies: Set[str] = set()
        for a in entity.member_atoms:
            if a.facts and a.facts.garment_topologies:
                entity_topologies.update(a.facts.garment_topologies)

        # 1. 结构化事实匹配：若动作原子声明了所需服装拓扑，实体拓扑必须相交（或通过构件穿透支持）
        if state_atom.facts and state_atom.facts.garment_topologies:
            atom_topos = set(state_atom.facts.garment_topologies)
            has_matching_topo = any(
                a.facts and any(does_garment_support_modifier(a.facts, topo) for topo in atom_topos)
                for a in entity.member_atoms
            )
            if entity_topologies and not (entity_topologies & atom_topos) and not has_matching_topo:
                return False

        # 2. 动作短语语义正则核验 (单源真实源，覆盖跨形制细化)
        atom_text = state_atom.text.lower().strip() if state_atom.text else ""

        if state_id in ("lifted_up", "lifted", "lifted_skirt"):
            # 2.1 连衣裙动作 (如 dress hitched up / dress sliding down):
            # 必须为真实连衣裙 (one_piece 且不在 NON_SKIRT_ONE_PIECE 中)，下装半身裙 (bottom_skirt) 绝对禁止冒充承载！
            if RE_DRESS_ACTION.search(atom_text):
                is_dress = any(
                    a.facts and "one_piece" in a.facts.garment_topologies and entity.selected_id not in NON_SKIRT_ONE_PIECE
                    and not _is_accessory_or_detail(a.text)
                    for a in entity.member_atoms
                )
                if not is_dress:
                    return False

            # 2.2 裙装动作 (如 skirt pulled up / skirt hiked up):
            # 必须具备裙形制 (bottom_skirt, 或具备裙装的一体衣 one_piece)，裤装与纯上装绝对禁止承载！
            if RE_SKIRT_ACTION.search(atom_text):
                has_skirt = any(
                    a.facts and (
                        ("bottom_skirt" in a.facts.garment_topologies and not _is_accessory_or_detail(a.text)) or
                        does_garment_support_modifier(a.facts, "bottom_skirt") or
                        ("one_piece" in a.facts.garment_topologies and entity.selected_id not in NON_SKIRT_ONE_PIECE and not _is_accessory_or_detail(a.text))
                    )
                    for a in entity.member_atoms
                )
                if not has_skirt:
                    return False

        elif state_id in ("unbuttoned", "opened", "unzipped"):
            # 2.3 外套开扣/开襟动作 (如 jacket unbuttoned / coat open / jacket open / unbuttoned coat):
            # 必须具备外套拓扑 (outerwear)，纯上装衬衫与下装绝对禁止冒充承载！
            if RE_OUTERWEAR_ACTION.search(atom_text):
                has_outer = any(
                    a.facts and (
                        "outerwear" in a.facts.garment_topologies or
                        does_garment_support_modifier(a.facts, "outerwear")
                    )
                    for a in entity.member_atoms
                )
                if not has_outer:
                    return False

            # 2.4 衬衫专属开扣动作 (如 blouse unbuttoned / shirt open at chest):
            # 必须为真实衬衫/上装 (top 拓扑，或款式标签明确包含衬衫语义)；纯外套、纯下装及浴袍/长袍/连体衣绝对禁止冒用！
            if RE_SHIRT_ACTION.search(atom_text):
                has_shirt = any(
                    a.facts and (
                        "top" in a.facts.garment_topologies or
                        does_garment_support_modifier(a.facts, "top")
                    )
                    for a in entity.member_atoms
                ) or any("shirt" in (a.text or "").lower() for a in entity.member_atoms)
                if not has_shirt:
                    return False

            # 2.4b 长袍/法袍/病号服专属动作 (如 robe open at chest / robe unfastened):
            # 必须为长袍/法袍/病号服款式，纯上装衬衫与普通下装禁止冒用！
            if RE_ROBE_ACTION.search(atom_text):
                has_robe = any(
                    any(k in (a.text or "").lower() for k in ("robe", "bathrobe", "cassock", "gown"))
                    for a in entity.member_atoms
                ) or any(k in entity.selected_id for k in ("robe", "bathrobe", "cassock", "gown", "clerical"))
                if not has_robe:
                    return False

            # 2.4c 连体服/赛车服专属动作 (如 suit unbuttoned at chest):
            # 必须为连体服/赛车服款式！
            if RE_SUIT_ACTION.search(atom_text):
                has_suit = any("suit" in (a.text or "").lower() or "racing" in (a.text or "").lower() for a in entity.member_atoms) or "racing" in entity.selected_id or "suit" in entity.selected_id
                if not has_suit:
                    return False

            # 2.5 胸部开扣动作 (如 buttons undone revealing cleavage / collar unbuttoned / front unbuttoned):
            # 必须具备上身覆盖 (top, one_piece, outerwear)，纯下装绝对禁止承载！
            if RE_CHEST_ACTION.search(atom_text):
                has_chest = any(
                    a.facts and any(
                        t in a.facts.garment_topologies or does_garment_support_modifier(a.facts, t)
                        for t in ("top", "one_piece", "outerwear")
                    )
                    for a in entity.member_atoms
                )
                if not has_chest:
                    return False

            # 2.6 裤装动作 (如 pants button undone / jeans unbuttoned):
            # 必须为裤装 (bottom_pants, one_piece)，半身裙与纯上装绝对禁止承载！
            if RE_PANTS_ACTION.search(atom_text):
                has_pants = any(
                    a.facts and (
                        any(t in a.facts.garment_topologies for t in ("bottom_pants", "one_piece")) or
                        does_garment_support_modifier(a.facts, "bottom_pants")
                    )
                    for a in entity.member_atoms
                )
                if not has_pants:
                    return False

            # 2.7 裙装解扣动作 (如 skirt button undone / skirt unbuttoned):
            # 必须具备裙形制 (bottom_skirt, 或具备裙装的一体衣 one_piece)，裤装与纯上装绝对禁止承载！
            if RE_SKIRT_ACTION.search(atom_text):
                has_skirt = any(
                    a.facts and (
                        ("bottom_skirt" in a.facts.garment_topologies and not _is_accessory_or_detail(a.text)) or
                        does_garment_support_modifier(a.facts, "bottom_skirt") or
                        ("one_piece" in a.facts.garment_topologies and entity.selected_id not in NON_SKIRT_ONE_PIECE and not _is_accessory_or_detail(a.text))
                    )
                    for a in entity.member_atoms
                )
                if not has_skirt:
                    return False

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
    raw_id = atom.source_item_id or (atom.origin.selected_id if atom.origin else "")
    clean_text = atom.text.lower().strip() if atom.text else ""

    # 1. 优先结构化事实
    if atom.facts and atom.facts.garment_states:
        for gs in atom.facts.garment_states:
            if gs == "unzipped":
                return "unzipped"
            elif gs == "unbuttoned":
                return "unbuttoned"
            elif gs == "opened":
                # opened 为通用枚举，若标识明确为 unzipped 或文本匹配拉链动作，归为 unzipped，否则归为 unbuttoned
                if raw_id == "unzipped" or RE_ZIPPER_ACTION.search(clean_text):
                    return "unzipped"
                return "unbuttoned"
            elif gs in ("lifted", "lifted_up", "lifted_skirt"):
                return "lifted_up"
            elif gs == "pulled_down":
                return "pulled_down"
            elif gs in CANONICAL_MODIFIER_STATES or gs in CANONICAL_ABSENCE_STATES or gs in CANONICAL_LAYERING_STATES or gs == "discarded":
                return gs

    # 2. 规范 item_id / origin.selected_id
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
    if a.provenance and a.provenance.rule_id == "material_penetration":
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
            "underboob_cutout", "off_shoulder_cut", "bare_shoulders", "taut_tight",
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
    if atom.source_slot == "clothing_state" or (atom.origin and atom.origin.selector == "clothing_state") or (atom.provenance and atom.provenance.kind == "clothing_state"):
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

    # 阶梯 1：显式目标匹配（规范 ID 精确匹配，支持实体目标与构件目标解析，快速失败，绝不回退改绑其他衣物）
    if effective_target_id is not None:
        eff_lower = effective_target_id.lower()
        # 1.1 实体级显式目标匹配 (selected_id 或 entity_id)
        matched = [
            e for e in active_entities
            if e.selected_id == effective_target_id
            or e.entity_id == effective_target_id
            or e.selected_id.lower() == eff_lower
            or e.entity_id.lower() == eff_lower
        ]
        # 1.2 构件级目标解析所属实体 (构件 → 所属实体)
        if not matched:
            for e in active_entities:
                pieces = _extract_ensemble_pieces_list(e)
                if any(
                    getattr(pb, "piece_id", "").lower() == eff_lower
                    or getattr(pb, "piece_slot", "").lower() == eff_lower
                    or (getattr(pb, "piece_text", "") and eff_lower in getattr(pb, "piece_text", "").lower())
                    for pb in pieces
                ):
                    if e not in matched:
                        matched.append(e)

        if not matched:
            return BindingResult(
                status=BindingStatus.UNBOUND_TARGET_NOT_FOUND,
                reason=f"explicit_target_not_found: {effective_target_id}",
            )
        if len(matched) == 1:
            target_entity = matched[0]
            if not is_garment_compatible_with_state(target_entity, state_id, state_atom, target_id=effective_target_id):
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
            compatible_matched = [e for e in matched if is_garment_compatible_with_state(e, state_id, state_atom, target_id=effective_target_id)]
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
        if is_garment_compatible_with_state(e, state_id, state_atom)
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


def is_camera_hardware_atom(a: Any) -> bool:
    """判断给定原子是否属于相机硬件套机/机身/光学器材（而非感光胶片/相纸乳剂）。

    器材套机与光学镜头具有机械/光学属性，不承载感光乳剂色彩模式，不适用黑白胶片互斥规则。
    暗房相纸等非机载感光介质属于印相材料，承载真实色彩模式（黑白/彩色），必须参与色彩互斥消解。
    """
    facts = getattr(a, "facts", None)
    if facts:
        cf = getattr(facts, "camera_facts", None)
        if cf:
            # 必须凭明确的器材类别判断，严禁将 is_camera_film=False（如暗房相纸）误判为硬件
            if getattr(cf, "device_category", None) == "camera_hardware_kit":
                return True
            if any(getattr(cf, f, None) for f in ("camera_brand", "camera_model", "lens_mount", "lens_spec", "device_type")):
                return True
        if getattr(facts, "capture_device", None):
            return True

    # 检查 ID 标识 (支持 source_item_id, id, provenance, origin)
    for attr in ("source_item_id", "id"):
        val = getattr(a, attr, None) or ""
        if val.startswith("camera_hardware_") or val.startswith("cam_hw__") or val == "cinema_lenses":
            return True

    prov = getattr(a, "provenance", None)
    if prov:
        p_item = getattr(prov, "item_id", None) or ""
        if p_item.startswith("camera_hardware_") or p_item.startswith("cam_hw__") or p_item == "cinema_lenses":
            return True

    origin = getattr(a, "origin", None)
    if origin:
        o_item = getattr(origin, "selected_id", None) or ""
        if o_item.startswith("camera_hardware_") or o_item.startswith("cam_hw__") or o_item == "cinema_lenses":
            return True

    return False


_SAFE_NON_NUDE_TEXT_RE = re.compile(
    r"\b(?:sheer\s+nude\s+lip|nude\s+lip(?:\s+gloss)?|nude\s+peach|"
    r"bare\s+(?:bulb|bulbs|tree|trees|wall|walls|face|brick|wood|concrete|floor|branch|branches)|"
    r"ankle\s+bracelet)\b",
    re.I,
)
_L6_UPPER_CUTOFF_TEXT_RE = re.compile(
    r"\b(?:waist-up(?:\s+framing)?|chest-up(?:\s+shot)?|upper[- ]body(?:\s+framing)?|"
    r"half-body\s+portrait|face\s+shot\s+filling\s+frame|portrait\s+framing|"
    r"close-up\s+portrait|face\s+portrait|face\s+close-up|extreme\s+close-up|"
    r"headshot|bust\s+shot|shoulder-up|medium\s+shot|medium\s+close-up|"
    r"breast\s+and\s+cleavage\s+focus)\b",
    re.I,
)
_L6_LOWER_GENITAL_ALLOW_RE = re.compile(
    r"\b(?:crotch|genital|pussy|vulva|vagina|anus|lower\s+body|between\s+legs|spread|pelvis)\b",
    re.I,
)
_L4_PANTIES_ONLY_UPPER_RE = re.compile(
    r"\b(?:bra|cups|underwire|halter|lingerie\s+set|teddy|bikini\s+underwear|three-point)\b",
    re.I,
)
_L4_HOSIERY_ACCESSORY_UNDERWEAR_RE = re.compile(r"\b(?:bra|panties|underwear|lingerie)\b", re.I)
_L1_CROSS_SLOT_EXPOSURE_RE = re.compile(
    r"\b(?:upskirt|downblouse|panties|bra|bare\s+breasts?|exposed\s+breasts?|one\s+breast\s+exposed|"
    r"covering\s+breasts?|below\s+breasts?|cross-torso\s+body\s+jewelry|nipple\w*|areola\w*|"
    r"underbust\s+mandala|womb\s+tattoo|pubic|topless|bottomless|pussy|vagina|penis|cock|"
    r"nude|naked|undressed|crotch\s+close-up|between\s+legs\s+angle|crotch\s+level\s+view|"
    r"thong|g-string|cleavage|underboob|sideboob|raw\s+sex|raw\s+dogging|no\s+condom|"
    r"creampie|cum|semen|vibrator|dildo|sponge\s+bath|no\s+bra|no\s+panties|"
    r"skirt\s+hiked\s+up|sailor\s+shirt\s+unbuttoned|slipping\s+off\s+shoulder|bare\s+shoulder)\b",
    re.I,
)
_L2_EXPOSURE_RE = re.compile(
    r"\b(?:one\s+breast\s+exposed|bare\s+breasts?|exposed\s+breasts?|nipple\w*|areola\w*|"
    r"no\s+panties|no\s+bra|topless|bottomless|pussy|vagina|penis|cock|genitals|"
    r"raw\s+sex|raw\s+dogging|creampie|cum|semen|vibrator|dildo|public\s+nudity)\b",
    re.I,
)
_L4_FULL_GARMENT_TOPOLOGIES = frozenset({
    "top",
    "bottom_pants",
    "bottom_skirt",
    "outerwear",
    "one_piece",
    "ensemble_outfit",
    # 兼容历史/自定义非标准拓扑词
    "suit",
    "coat",
    "jacket",
    "hoodie",
    "pants",
    "blazer",
    "sweater",
})
_NON_L1_EXPOSED_REGIONS_SET = frozenset({
    "breasts",
    "genitals",
    "anus",
    "buttocks",
    "full_nude",
    "cleavage",
    "midriff",
    "underboob_sideboob",
    "underwear_panties",
    "underwear_bra",
    "thigh_leg",
    "shoulders",
    "back",
})
_NON_L2_EXPOSED_REGIONS_SET = frozenset({
    "breasts",
    "genitals",
    "anus",
    "buttocks",
    "full_nude",
})


def _get_atom_item_id(a: PromptAtom) -> str:
    return (
        a.source_item_id
        or (a.provenance.item_id if a.provenance else "")
        or (a.origin.selected_id if a.origin else "")
        or ""
    )


def _is_non_worn_discarded_narrative_atom(a: PromptAtom) -> bool:
    """判定是否为“衣物脱下散落旁侧 / 非在身赤裸”叙事原子，避免 L4/L5/L6 误删。"""
    f = a.facts
    if not f:
        return False
    if f.piece_role not in (None, "none"):
        return False
    if f.garment_topologies and any(t != "none" for t in f.garment_topologies):
        return False
    if f.covered_regions and any(r != "none" for r in f.covered_regions):
        return False
    return bool(
        "discarded" in f.garment_states
        or "removed" in f.garment_states
        or "full_nude" in f.exposed_regions
        or f.coverage_delta == "uncover_all"
    )


def _is_safe_non_nude_atom_text(a: PromptAtom) -> bool:
    tl = a.text.lower()
    if _SAFE_NON_NUDE_TEXT_RE.search(tl):
        return True
    if a.source_slot in ("makeup", "scene", "scene_theme", "lighting", "film"):
        if a.facts and a.facts.exposed_regions in ((), ("none",)) and not a.facts.allowed_nudity_levels:
            if any(w in tl for w in ("nude", "bare")) and not any(
                b in tl for b in ("breast", "nipple", "pussy", "genital", "crotch", "buttocks", "naked")
            ):
                return True
    return False


def _is_upper_body_cutoff_framing_atom(a: PromptAtom) -> bool:
    """判定 L6 下会裁切私处展示的上半身/面部景别或构图。"""
    if a.source_slot not in ("shot_type", "shot", "camera_angle", "camera"):
        return False
    iid = _get_atom_item_id(a)
    tl = a.text.lower()
    if iid in ("close_up", "medium_close_up", "medium_shot"):
        return True
    if a.facts and a.facts.visible_regions:
        vis = set(a.facts.visible_regions)
        if (vis & {"face", "upper_body"}) and not (vis & {"lower_body", "intimate_lower_body", "full_body", "legs"}):
            return True
    if a.facts and a.facts.exposed_regions:
        exp = set(a.facts.exposed_regions)
        if "breasts" in exp and "genitals" not in exp:
            return True
    if _L6_LOWER_GENITAL_ALLOW_RE.search(tl):
        return False
    return bool(_L6_UPPER_CUTOFF_TEXT_RE.search(tl))


def _align_facts_with_rewritten_text(rep_text: str, orig_facts: Optional[SemanticFacts]) -> SemanticFacts:
    """将规则改写后的文本同步映射为一致的 SemanticFacts，防止残留透视/内衣/旧动作陈旧事实。"""
    base = orig_facts or SemanticFacts()
    stale_material_states = ("sheer", "see_through", "transparent", "translucent", "wet_clinging")
    stale_action_states = ("opened", "lifted", "loosened", "pulled_aside", "removed", "partially_removed")
    clean_base_states = tuple(
        s for s in base.garment_states
        if s not in stale_material_states and s not in stale_action_states
    )
    tl = rep_text.strip().lower()
    if "dress" in tl:
        clean_exposed = tuple(
            r for r in base.exposed_regions
            if r not in ("breasts", "genitals", "anus", "underwear_panties", "underwear_bra")
        )
        return replace(
            base,
            garment_topologies=("one_piece",),
            garment_states=("wet_clinging",) if "wet" in tl or "clinging" in tl else clean_base_states,
            piece_role="one_piece",
            covered_regions=("upper_torso", "lower_torso"),
            exposed_regions=clean_exposed,
            allowed_nudity_levels=tuple(lvl_tag for lvl_tag in base.allowed_nudity_levels if lvl_tag not in ("L5", "L6")),
        )
    if "unbuttoned" in tl:
        new_states = tuple(dict.fromkeys(clean_base_states + ("opened",)))
        is_lower_only = (
            any(t in base.garment_topologies for t in ("bottom_pants", "bottom_skirt"))
            and not any(t in base.garment_topologies for t in ("top", "one_piece", "outerwear", "ensemble_outfit"))
        )
        new_exposed = ("midriff",) if is_lower_only else ("cleavage",)
        return replace(base, garment_states=new_states, exposed_regions=new_exposed)
    if "lifted up" in tl or "lifted" in tl:
        new_states = tuple(dict.fromkeys(clean_base_states + ("lifted",)))
        return replace(base, garment_states=new_states, exposed_regions=("thigh_leg",))
    if "slipping off shoulder" in tl:
        new_states = tuple(dict.fromkeys(clean_base_states + ("loosened",)))
        return replace(
            base,
            garment_states=new_states,
            exposed_regions=("shoulders",),
        )
    return replace(
        base,
        garment_states=tuple(s for s in base.garment_states if s not in stale_material_states),
    )


def _is_lower_body_unbuttoned_atom(a: PromptAtom) -> bool:
    """判定是否为纯下装腰扣/裤扣/裙扣解开动作（如 pants button undone / skirt unbuttoned），与上装领口解扣严格隔离。"""
    tl = a.text.lower().strip()
    if not ("unbuttoned" in tl or "button undone" in tl):
        return False
    if not (RE_PANTS_ACTION.search(tl) or RE_SKIRT_ACTION.search(tl)):
        return False
    if (
        RE_UPPER_BODY_ACTION.search(tl)
        or RE_DRESS_ACTION.search(tl)
        or RE_ROBE_ACTION.search(tl)
        or RE_SUIT_ACTION.search(tl)
        or "buttons undone" in tl
        or "front unbuttoned" in tl
        or "one button undone" in tl
        or "collarbone" in tl
    ):
        return False
    f = a.facts
    if f:
        if f.garment_topologies and any(t not in ("bottom_pants", "bottom_skirt") for t in f.garment_topologies):
            return False
        if f.exposed_regions and any(r not in ("none", "midriff") for r in f.exposed_regions):
            return False
    return True


def _format_nudity_constraint_trace(constraint: Optional[NudityConstraint]) -> Optional[Dict[str, Any]]:
    if constraint is None:
        return None
    return {
        "requested_level": constraint.requested_level,
        "effective_level": constraint.effective_level,
        "source": constraint.source,
        "selected_branch": constraint.selected_branch,
        "yielded_to_override": constraint.yielded_to_override,
        "yield_reason": constraint.yield_reason,
    }


def _is_atom_compatible_with_level_and_branch(
    a: PromptAtom,
    dominant_lvl: Optional[str],
    selected_branch: Optional[str] = None,
) -> bool:
    """统一校验非 nudity 原子与 (effective_level, selected_branch) 的相容性。"""
    if not dominant_lvl:
        return True
    f = a.facts
    tl = a.text.lower()
    is_safe_phrase = _is_safe_non_nude_atom_text(a)

    if dominant_lvl in ("L5", "L6"):
        if _is_non_worn_discarded_narrative_atom(a):
            return True
        if dominant_lvl == "L6" and _is_upper_body_cutoff_framing_atom(a):
            return False
        if f and (
            any(t != "none" for t in f.garment_topologies)
            or (f.garment_states and not _is_non_worn_discarded_narrative_atom(a))
            or f.piece_role in ("top", "bottom", "one_piece", "ensemble", "outerwear", "underwear", "legwear", "footwear")
        ):
            return False
        if a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
            return False
        return True

    if dominant_lvl == "L4":
        if _is_non_worn_discarded_narrative_atom(a):
            return True
        if f and (
            any(t in _L4_FULL_GARMENT_TOPOLOGIES for t in f.garment_topologies)
            or f.piece_role in ("top", "bottom", "one_piece", "ensemble", "outerwear")
        ):
            return False
        if selected_branch == "l4_hosiery_footwear_only":
            if f and ("underwear" in f.garment_topologies or f.piece_role == "underwear"):
                return False
            if a.source_slot == "underwear" or _L4_HOSIERY_ACCESSORY_UNDERWEAR_RE.search(tl):
                return False
        elif selected_branch == "l4_panties_only":
            if f and ("intimate_upper" in f.covered_regions or "breasts" in f.exposed_regions):
                return False
            if _L4_PANTIES_ONLY_UPPER_RE.search(tl) and "panties" not in tl:
                return False
            if "string halter bikini" in tl or "strappy lingerie set" in tl:
                return False
        elif selected_branch == "l4_bra_and_panties":
            if f and "breasts" in f.exposed_regions and "underwear_bra" not in f.exposed_regions:
                return False
            if any(w in tl for w in ("topless", "bare breasts", "only lace panties")):
                return False
        elif selected_branch in ("l4_towel_sheet_wrap", "l4_accessory_only"):
            if f and ("underwear" in f.garment_topologies or f.piece_role == "underwear"):
                return False
            if selected_branch == "l4_accessory_only" and f and f.piece_role in ("legwear", "footwear"):
                return False
            if a.source_slot in ("underwear", "clothing_extension") or _L4_HOSIERY_ACCESSORY_UNDERWEAR_RE.search(tl):
                return False
        return True

    if dominant_lvl == "L3":
        if f and any(r in f.visible_regions for r in ("crotch", "buttocks", "intimate_lower_body")):
            return False
        is_state_or_ext_l3 = (
            a.source_slot in ("clothing_state", "clothing_extension")
            or (a.provenance is not None and a.provenance.kind in ("clothing_state", "clothing_extension"))
            or (a.origin is not None and a.origin.selector in ("clothing_state", "clothing_extension"))
            or _get_atom_item_id(a).lower() in ("auto_linkage", "linkage_general")
        )
        _l3_bra_re = re.compile(r"\b(?:bra|bralette|bustier|bikini\s+top|nipple\s+covers|pasties)\b")
        _has_bra_garment = bool(
            (f and "intimate_upper" in f.covered_regions)
            or (
                ((f and f.piece_role == "underwear") or a.source_slot in ("clothing", "underwear"))
                and _l3_bra_re.search(tl)
                and "no bra" not in tl
                and "braless" not in tl
                and "bra removed" not in tl
                and "bra edge" not in tl
                and "showing lace bra" not in tl
            )
        )
        if selected_branch == "l3_panties_only":
            if f and any(t in _L4_FULL_GARMENT_TOPOLOGIES for t in f.garment_topologies):
                return False
            if f and f.piece_role in ("top", "bottom", "one_piece", "ensemble", "outerwear"):
                return False
            if _has_bra_garment:
                return False
            if is_state_or_ext_l3 and any(
                w in tl for w in ("shirt", "blouse", "skirt", "dress", "tank top", "off-shoulder", "collar cutout", "plunging neckline", "subtle cleavage", "high slit", "crop cut")
            ):
                return False
        elif selected_branch in ("l3_topless_skirt_kept", "l3_topless_pants_kept", "l3_bikini_top_removed"):
            if f and (f.piece_role in ("top", "outerwear", "one_piece") or any(t in ("top", "outerwear", "one_piece") for t in f.garment_topologies)):
                return False
            if selected_branch == "l3_bikini_top_removed":
                if (f and "intimate_upper" in f.covered_regions and "intimate_lower" not in f.covered_regions) or (
                    _has_bra_garment and "bikini" not in tl
                ):
                    return False
            else:
                if _has_bra_garment:
                    return False
            if selected_branch == "l3_topless_skirt_kept" and f and "bottom_pants" in f.garment_topologies:
                return False
            if selected_branch == "l3_topless_pants_kept" and f and "bottom_skirt" in f.garment_topologies:
                return False
            if is_state_or_ext_l3:
                banned_l3 = (
                    "shirt open", "dress bunched", "dress pulled", "tank top", "bra", "blouse",
                    "off-shoulder", "collar cutout", "plunging neckline", "subtle cleavage", "crop cut",
                )
                if selected_branch != "l3_topless_skirt_kept":
                    banned_l3 = banned_l3 + ("high slit",)
                if any(w in tl for w in banned_l3):
                    return False
        elif selected_branch == "l3_bra_pushed_up":
            if re.search(r"\b(?:nipple\s+covers|pasties)\b", tl):
                return False
            if (is_state_or_ext_l3 or a.source_slot in ("clothing", "underwear")) and any(
                w in tl for w in (
                    "bra removed", "braless", "no bra", "shirt open", "tank top",
                    "dress bunched", "dress pulled", "skirt hitched", "all top removed",
                    "panties only", "bikini top removed", "topless", "high slit",
                    "off-shoulder", "collar cutout", "plunging neckline", "subtle cleavage", "crop cut",
                )
            ):
                return False
        elif selected_branch == "l3_pulled_down_dress":
            if is_state_or_ext_l3 and any(
                w in tl for w in (
                    "shirt open", "tank top", "skirt hitched", "bra pushed up",
                    "off-shoulder", "collar cutout", "plunging neckline", "subtle cleavage", "crop cut",
                )
            ):
                return False
        elif selected_branch in ("l3_open_shirt_breasts", "l3_raised_top_breasts"):
            if is_state_or_ext_l3 and any(
                w in tl for w in (
                    "dress bunched", "dress pulled", "skirt hitched", "bra pushed up",
                    "off-shoulder", "plunging neckline", "high slit",
                )
            ):
                return False
            if selected_branch == "l3_raised_top_breasts" and is_state_or_ext_l3 and "shirt open" in tl:
                return False
        return True

    if dominant_lvl == "L1":
        if is_safe_phrase:
            return True
        if f:
            if f.allowed_nudity_levels and "L1" not in f.allowed_nudity_levels:
                return False
            if any(r in _NON_L1_EXPOSED_REGIONS_SET for r in f.exposed_regions):
                return False
            if any(r in ("intimate_lower_body", "cleavage", "breasts", "crotch", "buttocks", "underboob", "sideboob", "pubic") for r in f.visible_regions):
                return False
            if getattr(f, "style_genre", None) == "boudoir_lingerie":
                return False
            if f.liquid_kind in ("sexual_fluid", "cum", "semen"):
                return False
            if any(s in ("lifted", "lifted_up", "opened", "removed", "lifted_skirt", "unbuttoned") for s in f.garment_states):
                return False
        iid = _get_atom_item_id(a).lower().replace("_", " ")
        if "between legs angle" in iid or "erotic close up" in iid:
            return False
        if _L1_CROSS_SLOT_EXPOSURE_RE.search(tl):
            return False
        return True

    if dominant_lvl == "L2":
        if is_safe_phrase:
            return True
        if f:
            if f.allowed_nudity_levels and "L2" not in f.allowed_nudity_levels:
                return False
            if any(r in _NON_L2_EXPOSED_REGIONS_SET for r in f.exposed_regions):
                return False
            if any(r in ("intimate_lower_body", "crotch", "pubic") for r in f.visible_regions):
                return False
            if f.liquid_kind in ("sexual_fluid", "cum", "semen"):
                return False
            if selected_branch and f.nudity_branches:
                branch_specific = [b for b in f.nudity_branches if b != "l2_sheer_hint"]
                if branch_specific and selected_branch not in branch_specific:
                    return False
        is_state_origin = (
            a.source_slot == "clothing_state"
            or (a.provenance is not None and a.provenance.kind == "clothing_state")
            or (a.origin is not None and a.origin.selector == "clothing_state")
        )
        allow_formal_absence_state = bool(
            is_state_origin
            and f
            and "L2" in f.allowed_nudity_levels
            and "none" in f.exposed_regions
            and re.search(r"\bno\s+(?:bra|panties)\b", tl)
        )
        if not allow_formal_absence_state and _L2_EXPOSURE_RE.search(tl):
            return False
        if selected_branch:
            if (
                selected_branch == "l2_midriff_waist"
                and a.source_slot == "clothing"
                and f
                and ("one_piece" in f.garment_topologies or f.piece_role == "one_piece")
                and not (a.origin and a.origin.mode == "preset")
                and not re.search(r"\b(?:crop|midriff|cutout|two[- ]piece|bikini)\b", tl)
            ):
                return False

            _l2_positional_state_ids = {
                "auto_linkage", "linkage_general", "unbuttoned", "slipping_off", "lifted_up",
                "back_cutout", "heart_cutout", "off_shoulder_cut", "bare_shoulders",
            }
            item_id_low = _get_atom_item_id(a).lower()
            is_branch_scoped_mod = (
                a.source_slot == "clothing_extension"
                or (a.provenance is not None and a.provenance.kind == "clothing_extension")
                or (
                    is_state_origin
                    and (
                        item_id_low in _l2_positional_state_ids
                        or (a.origin is not None and (a.origin.selected_id or "").lower() in _l2_positional_state_ids)
                    )
                )
            )
            if is_branch_scoped_mod:
                _l2_allowed_exp: Dict[str, Set[str]] = {
                    "l2_collar_neckline": {"none", "cleavage", "collarbone_neck", "underwear_bra"},
                    "l2_shoulder_strap": {"none", "shoulders"},
                    "l2_leg_slit": {"none", "thigh_leg", "ankle_feet", "underwear_panties"},
                    "l2_midriff_waist": {"none", "midriff"},
                    "l2_backless": {"none", "back"},
                    "l2_sleeve_wrist": {"none", "arms_wrists"},
                }
                allowed_exp = _l2_allowed_exp.get(selected_branch)
                if allowed_exp is not None and f and f.exposed_regions:
                    if any(r not in allowed_exp for r in f.exposed_regions):
                        return False
                _l2_banned_tokens: Dict[str, Tuple[str, ...]] = {
                    "l2_collar_neckline": ("strap slipping", "off-shoulder", "off shoulder", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "backless", "rolled up", "pushed sleeve"),
                    "l2_shoulder_strap": ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "backless", "rolled up", "pushed sleeve"),
                    "l2_leg_slit": ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "midriff", "waist cutout", "open side", "backless", "rolled up", "pushed sleeve"),
                    "l2_midriff_waist": ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "high slit", "bare leg", "skirt slit", "backless", "rolled up", "pushed sleeve"),
                    "l2_backless": ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "rolled up", "pushed sleeve"),
                    "l2_sleeve_wrist": ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "backless"),
                }
                banned = _l2_banned_tokens.get(selected_branch, ())
                if banned:
                    if selected_branch == "l2_midriff_waist" and _is_lower_body_unbuttoned_atom(a):
                        effective_banned = tuple(w for w in banned if w not in ("unbuttoned", "button undone"))
                    else:
                        effective_banned = banned
                    if any(w in tl for w in effective_banned):
                        return False
        return True

    return True


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

def is_container_id(id_str: str) -> bool:
    """判断给定标识是否为预设/配方容器 ID，而非单项道具实体 ID。"""
    if not id_str or not isinstance(id_str, str):
        return False
    s = id_str.strip()
    if s in ("preset_core", "preset_custom", "style_recipe", "recipe", "props", "prop", "unknown"):
        return True
    if re.match(r"^[A-Z]\d{2}$", s):
        return True
    if s.startswith("preset_") or s.startswith("recipe_"):
        return True
    return False


def is_fragment_id(id_str: str) -> bool:
    """判断给定标识是否为数据文件内的片段行号标识，而非共享道具实体 ID。"""
    if not id_str or not isinstance(id_str, str):
        return False
    s = id_str.strip()
    if re.match(r"^[a-z]\d{2}__frag_\d+$", s, re.I):
        return True
    if s.startswith("frag_") or s.startswith("atom_") or re.match(r"^rec__frag_\d+$", s, re.I):
        return True
    return False


def extract_explicit_prop_entity_id(atom: PromptAtom) -> Optional[str]:
    """提取显式道具实体 ID。
    规范字段 (entity_id) 与旧字段兼容判断严格分离：
    1. 规范字段：若存在显式 entity_id，直接作为实体身份，不凭容器/片段前缀推翻；
    2. 旧字段兼容：针对 target_id、provenance.item_id、source_item_id 等历史字段，
       执行容器 ID (preset_*/recipe_*) 与片段行号 (frag_*) 过滤，避免将容器或片段误认作实体。
    """
    # 1. 规范字段：直接作为身份
    if getattr(atom, "entity_id", None):
        eid = str(atom.entity_id).strip()
        if eid:
            return eid

    # 2. 旧字段兼容判断（需过滤容器与片段行号）
    if atom.target_id and not is_container_id(atom.target_id) and not is_fragment_id(atom.target_id) and not atom.target_id.startswith("override_"):
        return atom.target_id.strip()
    if atom.provenance and atom.provenance.item_id:
        pid = atom.provenance.item_id.strip()
        if not is_container_id(pid) and not is_fragment_id(pid) and not pid.startswith("override_"):
            return pid
    if atom.source_item_id:
        sid = atom.source_item_id.strip()
        if not is_container_id(sid) and not is_fragment_id(sid) and not sid.startswith("override_"):
            return sid
    if atom.origin and atom.origin.selector == "props" and atom.origin.selected_id:
        sel = atom.origin.selected_id.strip()
        if not is_container_id(sel) and not is_fragment_id(sel) and not sel.startswith("override_"):
            return sel
    return None


def get_prop_entity_key(atom: PromptAtom) -> str:
    """提取道具实体聚合键。
    权威契约规则：
    1. 严格区分容器 ID、片段 ID 与道具实体 ID；
    2. 显式道具实体 ID (entity_id / item_id) 具有最高且跨来源 (preset/recipe/catalog/override) 完全一致的权威性，同一实体 ID 的多条描述绝不被方位或状态割裂；
    3. 外部覆盖独立标签 (override_props_xxx) 保持独立实体资格；
    4. 无显式实体 ID 的片段：依片段 ID (frag_id) 保持实体独立性，严禁依据类别或对象词字典猜测合并不同道具；
    5. 仅当明确共享实体 ID 或属于同一片段内部共指时，方可合并计数。
    """
    # 1. 显式道具实体 ID 优先跨来源统一生效（预设、配方、目录、外部均同等遵循）
    explicit_id = extract_explicit_prop_entity_id(atom)
    if explicit_id:
        return f"item:{explicit_id}"

    # 2. 外部覆盖独立标签 (各含 override_props_xxx)
    sid = getattr(atom, "source_item_id", "") or ""
    pid = (atom.provenance.item_id if atom.provenance else "") or ""
    if sid.startswith("override_"):
        return f"ext:{sid}"
    if pid.startswith("override_"):
        return f"ext:{pid}"

    # 3. 片段级身份（同一片段内部切分出来的多个 span/atom 共享同一实体）
    frag_id = getattr(atom, "id", "") or ""
    if frag_id:
        return f"frag:{frag_id}"

    # 4. 无显式 ID 且无片段 ID 的独立原子
    return f"atom:{atom.atom_id}"


class PropEntity:
    def __init__(self, key: str, member_atoms: List[PromptAtom]):
        self.key = key
        self.entity_id = key
        self.atoms = member_atoms
        self.is_external = any(is_external_atom(ma) for ma in member_atoms)
        self.order = min((ma.tag_order, ma.span_order) for ma in member_atoms)
        h = 1
        for ma in member_atoms:
            if ma.facts and ma.facts.hands_required > 0:
                h = max(h, ma.facts.hands_required)
            elif any(w in ma.text.lower() for w in ("controller", "two hands", "both hands", "sword", "rifle", "tray")):
                h = max(h, 2)
        self.hands_required = h


def build_prop_entities(prop_atoms: Sequence[PromptAtom]) -> List[PropEntity]:
    """按道具实体聚合 PromptAtom，区分'同一道具的多个描述'与'多个独立道具'。"""
    entities: Dict[str, List[PromptAtom]] = {}
    for a in prop_atoms:
        ekey = get_prop_entity_key(a)
        entities.setdefault(ekey, []).append(a)
    entity_list = [PropEntity(k, v) for k, v in entities.items()]
    entity_list.sort(key=lambda e: (0 if e.is_external else 1, e.order))
    return entity_list


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
        atom_key = a.atom_id if a.atom_id else f"_anon_{id(a)}"
        if atom_key in self._indexed_atom_ids:
            return
        self._indexed_atom_ids.add(atom_key)
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


ALLOWED_RUNTIME_REASON_CODES: Mapping[str, Tuple[str, ...]] = {
    "spatial_environmental_mutual_exclusion": ("spatial_pose_support_incompatible", "external_spatial_precedence"),
    "pose_hand_occupation": ("external_prop_overrides_pose", "busy_hands_remove_props"),
    "nudity_clothing_conflicts": ("clothing_removes_nudity",),
}


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
            declared_codes = set(rule.reason_codes) | set(ALLOWED_RUNTIME_REASON_CODES.get(rule_id, ()))
            if reason_code not in declared_codes:
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
        fact_trace: Optional[Dict[str, Any]] = None,
    ) -> ResolutionDecision:
        self._validate_reason_code(rule_id, reason_code)
        seq = self._seq
        self._seq += 1
        p_ids = tuple(parent_source_ids or target_atom.provenance.parent_ids)
        if fact_trace is None:
            priority = 0
            if self._registry:
                try:
                    rule_item = self._registry.get_rule_item(rule_id)
                    priority = rule_item.priority
                except Exception:
                    priority = 0
            fact_trace = {
                "rule_id": rule_id,
                "phase": phase,
                "priority": priority,
                "winner_objects": list(winner_atom_ids),
                "target_objects": [target_atom.atom_id] if target_atom else [],
            }
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
            fact_trace=fact_trace,
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
        fact_trace: Optional[Dict[str, Any]] = None,
    ) -> ResolutionDecision:
        self._validate_reason_code(rule_id, reason_code)
        seq = self._seq
        self._seq += 1
        self.produced_atoms.extend(produced_atoms)
        produced_ids = tuple(a.atom_id for a in produced_atoms)
        after_text = ", ".join(a.text for a in produced_atoms)
        p_ids = tuple(parent_source_ids) if parent_source_ids else ((target_atom.atom_id,) + tuple(p for p in target_atom.provenance.parent_ids if p != target_atom.atom_id))
        if fact_trace is None:
            priority = 0
            if self._registry:
                try:
                    rule_item = self._registry.get_rule_item(rule_id)
                    priority = rule_item.priority
                except Exception:
                    priority = 0
            fact_trace = {
                "rule_id": rule_id,
                "phase": phase,
                "priority": priority,
                "winner_objects": list(winner_atom_ids),
                "target_objects": [target_atom.atom_id] if target_atom else [],
            }
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
            fact_trace=fact_trace,
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
        fact_trace: Optional[Dict[str, Any]] = None,
    ) -> ResolutionDecision:
        self._validate_reason_code(rule_id, reason_code)
        seq = self._seq
        self._seq += 1
        self.produced_atoms.extend(produced_atoms)
        produced_ids = tuple(a.atom_id for a in produced_atoms)
        after_text = ", ".join(a.text for a in produced_atoms)
        if fact_trace is None:
            priority = 0
            if self._registry:
                try:
                    rule_item = self._registry.get_rule_item(rule_id)
                    priority = rule_item.priority
                except Exception:
                    priority = 0
            fact_trace = {
                "rule_id": rule_id,
                "phase": phase,
                "priority": priority,
                "winner_objects": list(winner_atom_ids),
                "target_objects": [],
            }
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
            fact_trace=fact_trace,
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



def _build_canonical_catalog_facts_index(
    data_dir: Path,
) -> Tuple[
    Dict[Tuple[str, str], SemanticFacts],
    Set[Tuple[str, str]],
    Dict[Tuple[str, str, str], Optional[SemanticFacts]],
]:
    """构建 (slot, id) -> canonical SemanticFacts、聚合类目键集合，以及 (slot, category_id, text) 叶子映射。"""
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
    aggregate_keys: Set[Tuple[str, str]] = set()
    category_text_leaf_facts: Dict[Tuple[str, str, str], Optional[SemanticFacts]] = {}
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
                    target_slots = [slot_override] if slot_override else slots
                    for t in obj["tags"]:
                        if isinstance(t, dict) and "facts" in t:
                            t_text_norm = str(t.get("text") or "").strip().lower()
                            if t_text_norm:
                                try:
                                    leaf_f = SemanticFacts.from_dict(t["facts"])
                                    for s in target_slots:
                                        norm_s = normalize_slot_name(s)
                                        for slot_k in {norm_s, s}:
                                            ckey = (slot_k, lid, t_text_norm)
                                            if ckey not in category_text_leaf_facts:
                                                category_text_leaf_facts[ckey] = leaf_f
                                            elif category_text_leaf_facts[ckey] != leaf_f:
                                                category_text_leaf_facts[ckey] = None
                                except Exception:
                                    pass
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
                            for s in target_slots:
                                norm_s = normalize_slot_name(s)
                                if (norm_s, lid) not in catalog_facts:
                                    catalog_facts[(norm_s, lid)] = facts
                                    catalog_facts[(s, lid)] = facts
                                    aggregate_keys.add((norm_s, lid))
                                    aggregate_keys.add((s, lid))
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
    return catalog_facts, aggregate_keys, category_text_leaf_facts


def build_canonical_catalog_facts(data_dir: Path) -> Dict[Tuple[str, str], SemanticFacts]:
    """单次加载构建 (slot, stable_leaf_id) -> canonical SemanticFacts 权威映射表 (R2R2-P1-001)。"""
    catalog_facts, _, _ = _build_canonical_catalog_facts_index(data_dir)
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
    """ComfyUI-IYKYK 冲突消解引擎权威执行器。"""
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.registry = RuleRegistry(data_dir)
        (
            self.canonical_catalog_facts,
            self._category_aggregate_fact_keys,
            self._category_text_leaf_facts,
        ) = _build_canonical_catalog_facts_index(data_dir)
        self.text_fallback_hits: int = 0
        self.last_report: Optional[ResolutionReport] = None
        self._active_nudity_constraint: Optional[NudityConstraint] = None

    def _validate_formal_atom(self, a: PromptAtom, rule_id: str, index: Optional[OneTimeIndex] = None) -> None:
        """验证正式目录/预设/配方原子是否携带当前规则所需的基础语义事实。若事实缺失则 Fail-Closed (R2R2-P1-001)。"""
        if not is_formal_atom(a):
            return

        if a.origin and a.origin.mode == "resolver":
            return

        # 相机器材/光学镜头具有机械光学属性，不承载感光乳剂色彩模式，不适用黑白胶片互斥契约校验
        if rule_id == "monochrome_film_chroma_coherence" and is_camera_hardware_atom(a):
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
        # 优先匹配精确叶子条目；其次在服装类目下按精确文本匹配叶子，最后才回退至类目聚合条目，避免多件套类目拓扑污染单件词条。
        canon: Optional[SemanticFacts] = None
        matched_id: Optional[str] = None
        is_category_aggregate = False
        a_text_norm = (a.text or "").strip().lower()

        def _lookup_fact(cid: str, *, allow_aggregate: bool) -> Tuple[Optional[SemanticFacts], bool]:
            for key in ((norm_slot, cid), (a.source_slot, cid)):
                found = self.canonical_catalog_facts.get(key)
                if found is not None:
                    is_agg = key in self._category_aggregate_fact_keys
                    if is_agg and not allow_aggregate:
                        continue
                    return found, is_agg
            return None, False

        for cid in candidate_ids:
            found, is_agg = _lookup_fact(cid, allow_aggregate=False)
            if found is not None:
                canon = found
                matched_id = cid
                is_category_aggregate = is_agg
                break

        if canon is None and a_text_norm:
            for cid in candidate_ids:
                for key in ((norm_slot, cid, a_text_norm), (a.source_slot, cid, a_text_norm)):
                    leaf_f = self._category_text_leaf_facts.get(key)
                    if leaf_f is not None:
                        canon = leaf_f
                        matched_id = cid
                        is_category_aggregate = False
                        break
                if canon is not None:
                    break

        if canon is None:
            for cid in candidate_ids:
                found, is_agg = _lookup_fact(cid, allow_aggregate=True)
                if found is not None:
                    canon = found
                    matched_id = cid
                    is_category_aggregate = is_agg
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
            if is_category_aggregate:
                atom_explicit = set(a.facts.explicit_fields or ())
                piece_specific_keys = (
                    "garment_topologies", "covered_regions", "exposed_regions", "visible_regions",
                    "garment_states", "fabric_materials", "cut_features", "piece_role", "is_ensemble",
                )
                has_piece_identity = bool(
                    a.facts.piece_role is not None
                    or a.facts.garment_topologies
                    or ("garment_topologies" in atom_explicit)
                    or ("covered_regions" in atom_explicit)
                )
                filtered_dict = {
                    k: v
                    for k, v in canon.to_dict().items()
                    if k not in atom_explicit and not (has_piece_identity and k in piece_specific_keys)
                }
                merged_facts = SemanticFacts.from_dict(filtered_dict).merge(a.facts)
            else:
                merged_facts = canon.merge(a.facts)
            object.__setattr__(a, "facts", merged_facts)
            if index is not None:
                for sib in index.get_all_active_ordered():
                    if (
                        sib.tag_order == a.tag_order
                        and sib.source_slot == a.source_slot
                        and sib.id == a.id
                        and sib.origin == a.origin
                        and sib.atom_id != a.atom_id
                    ):
                        object.__setattr__(sib, "facts", merged_facts)
        elif canon and a.facts is None:
            object.__setattr__(a, "facts", canon)
            if index is not None:
                for sib in index.get_all_active_ordered():
                    if (
                        sib.tag_order == a.tag_order
                        and sib.source_slot == a.source_slot
                        and sib.id == a.id
                        and sib.origin == a.origin
                        and sib.atom_id != a.atom_id
                    ):
                        object.__setattr__(sib, "facts", canon)

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
                if not (facts.visible_regions or facts.garment_topologies or facts.exposed_regions or canon):
                    raise RuleConfigurationError(
                        f"Formal nudity atom '{a.atom_id}' missing required facts for rule '{rule_id}'"
                    )
            elif slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
                if not (
                    facts.garment_topologies
                    or facts.garment_states
                    or facts.visible_regions
                    or facts.piece_role
                    or facts.covered_regions
                    or canon
                ):
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
                if is_camera_hardware_atom(a):
                    return
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
                item_id = leaf_id.lower() if leaf_id else ""
                ef = facts.expression_facts
                pf = facts.pose_facts
                ef_action = ef.get("facial_action") if isinstance(ef, dict) else getattr(ef, "facial_action", None)
                pf_action = pf.get("facial_action") if isinstance(pf, dict) else getattr(pf, "facial_action", None)
                has_facial_action = bool(ef_action or pf_action)
                if not (
                    item_id.startswith("mouth_")
                    or any(k in item_id for k in ("mouth", "lip", "teeth", "tongue", "frown"))
                    or has_facial_action
                ):
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
        nudity_constraint: Optional[NudityConstraint] = None,
    ) -> Tuple[List[PromptAtom], Tuple[str, ...], ResolutionReport]:
        """按 DAG 冻结顺序执行 17 条冲突消解规则并产出完备审计报告与零硬冲突闭环。"""
        if rng is None:
            rng = Random(42)

        # 隔离池第二道物理防线拦截 (QuarantinePool 零泄漏，覆盖 ID 及载荷隔离标志)
        for a in atoms:
            if is_quarantined_payload(a):
                raise QuarantineLeakageError(
                    f"Quarantined payload detected in ConflictResolver! "
                    f"id={getattr(a, 'id', None)}, source_item_id={getattr(a, 'source_item_id', None)}"
                )

        self.text_fallback_hits = 0
        self._active_nudity_constraint = nudity_constraint

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

        try:
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

            # 2b. 规则改写后目标等级与单一分支复检（防止后期规则改写重现被删衣物或语义漂移）
            eff_constraint = self._active_nudity_constraint
            if eff_constraint is not None and eff_constraint.effective_level and not eff_constraint.yielded_to_override:
                eff_lvl = eff_constraint.effective_level
                eff_branch = eff_constraint.selected_branch
                nc_trace = _format_nudity_constraint_trace(eff_constraint)
                for a in list(index.get_all_active_ordered()):
                    if not a.can_detect or not index.is_active(a) or a.source_slot == "nudity":
                        continue
                    is_produced = bool(a.provenance and a.provenance.rule_id and a.provenance.rule_id != "nudity_clothing_conflicts")
                    if not is_produced:
                        continue
                    if not _is_atom_compatible_with_level_and_branch(a, eff_lvl, eff_branch):
                        if is_external_atom(a) or not a.can_delete_atom:
                            continue
                        prod_rule_id = a.provenance.rule_id
                        if prod_rule_id == "material_penetration":
                            index.drop(a.atom_id)
                            ft = {
                                "rule_id": "material_penetration",
                                "phase": "physical",
                                "priority": 230,
                                "winner_objects": [],
                                "target_objects": [a.atom_id],
                            }
                            if nc_trace:
                                ft["nudity_constraint"] = nc_trace
                            ledger.record_drop(
                                "material_penetration",
                                "physical",
                                "material_penetration_removed",
                                (),
                                a,
                                fact_trace=ft,
                            )

            # 按决策实际首次出现顺序严格同步 rules_applied
            rules_applied = []
            for d in ledger.decisions:
                if d.rule_id not in rules_applied:
                    rules_applied.append(d.rule_id)

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
                nudity_constraint=self._active_nudity_constraint,
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
        finally:
            self._active_nudity_constraint = None

    def resolve_atoms_with_report(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None,
        context_profile: Optional[ContextProfile] = None,
        nudity_constraint: Optional[NudityConstraint] = None,
    ) -> Tuple[List[PromptAtom], Tuple[str, ...]]:
        """向后兼容接口：执行消解并返回 (resolved_atoms, rules_applied)。"""
        resolved, rules_applied, _ = self.resolve_atoms_with_full_report(
            atoms, rng, context_profile, nudity_constraint=nudity_constraint
        )
        return resolved, rules_applied

    def resolve_atoms(
        self,
        atoms: Sequence[PromptAtom],
        rng: Optional[Random] = None,
        context_profile: Optional[ContextProfile] = None,
        nudity_constraint: Optional[NudityConstraint] = None,
    ) -> List[PromptAtom]:
        """向后兼容接口：执行消解并返回 resolved_atoms 列表。"""
        resolved, _ = self.resolve_atoms_with_report(
            atoms, rng, context_profile, nudity_constraint=nudity_constraint
        )
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

        for f in fragments:
            if is_quarantined_payload(f):
                raise QuarantineLeakageError(
                    f"Quarantined fragment detected in ConflictResolver! "
                    f"id={getattr(f, 'id', None)}, source_item_id={getattr(f, 'source_item_id', None)}"
                )

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
                if not is_formal_atom(a) and a.can_modify_internal and not is_external_atom(a):
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
            ext_venues = [v for v in active_venues if is_external_atom(v[3])]
            ext_clusters = list(dict.fromkeys(v[2] for v in ext_venues))
            if len(ext_clusters) > 1:
                v1 = ext_venues[0][3]
                v2 = next(v[3] for v in ext_venues if v[2] != ext_venues[0][2])
                raise PromptValidationError(
                    f"External override conflict under rule '{rule_item.id}': slot '{v1.source_slot}' ('{v1.text}') conflicts with slot '{v2.source_slot}' ('{v2.text}')",
                    slots=(v1.source_slot, v2.source_slot),
                    texts=(v1.text, v2.text),
                    rule_id=rule_item.id,
                )
            if ext_clusters:
                dominant_venue = ext_clusters[0]
                dominant_atom = next(v[3] for v in ext_venues if v[2] == dominant_venue)
            else:
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
                if is_formal_atom(a) or is_external_atom(a):
                    if a.facts and a.facts.venue_ids:
                        if any(bv in a.facts.venue_ids for bv in banned_venues):
                            is_loser = True
                    elif tf.enabled:
                        if any(bt.matches(a.text) for bt in banned_tags_all):
                            is_loser = True
                            is_fallback = True
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
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and a.facts.space_kind == "indoor":
                    indoor_atoms.append(a)
                elif a.facts and a.facts.space_kind == "outdoor":
                    outdoor_atoms.append(a)
                elif tf.enabled:
                    if any(w.matches(a.text) for w in outdoor_exclusive):
                        outdoor_atoms.append(a)
                    elif any(w.matches(a.text) for w in indoor_exclusive) or any(any(vt.matches(a.text) for vt in vtags) for vtags in venue_clusters.values()):
                        indoor_atoms.append(a)
            elif tf.enabled:
                if any(w.matches(a.text) for w in outdoor_exclusive):
                    outdoor_atoms.append(a)
                elif any(w.matches(a.text) for w in indoor_exclusive) or any(any(vt.matches(a.text) for vt in vtags) for vtags in venue_clusters.values()):
                    indoor_atoms.append(a)

        if indoor_atoms and outdoor_atoms:
            first_in = min(indoor_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_out = min(outdoor_atoms, key=lambda x: (x.tag_order, x.span_order))
            in_is_ext = any(is_external_atom(a) for a in indoor_atoms)
            out_is_ext = any(is_external_atom(a) for a in outdoor_atoms)

            if in_is_ext and out_is_ext:
                raise PromptValidationError(
                    f"External override conflict under rule '{rule_item.id}': indoor environment ('{first_in.text}') conflicts with outdoor environment ('{first_out.text}')",
                    slots=(first_in.source_slot, first_out.source_slot),
                    texts=(first_in.text, first_out.text),
                    rule_id=rule_item.id,
                )

            if in_is_ext and not out_is_ext:
                winner = first_in
                losers = outdoor_atoms
                loser_exclusive_tags = outdoor_exclusive
            elif out_is_ext and not in_is_ext:
                winner = first_out
                losers = indoor_atoms
                loser_exclusive_tags = indoor_exclusive
            else:
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

        # 4. 空间与姿态物理支撑约束消解 (R3-POSE-ENV-001)
        active_poses = [a for a in index.get_active_by_slot("pose") if a.can_detect and index.is_active(a)]
        active_scenes = [a for a in index.get_active_by_slot("scene") if a.can_detect and index.is_active(a)]

        if active_poses and active_scenes:
            # 仅当场景显式要求水体环境或处于水下/沉浸环境时（排除泳池岸边/普通水边，仅针对真正水下/沉浸环境），
            # 且姿态显式声明了不相容的坚实地面支撑方式时，才属于冲突。
            submerged_scenes = [
                a for a in active_scenes
                if (
                    (a.facts and a.facts.scene_facts and getattr(a.facts.scene_facts, "venue_category", "") in ("underwater", "submerged", "deep_water"))
                    or (a.facts and a.facts.venue_ids and any(v in ("underwater", "submerged", "deep_sea") for v in a.facts.venue_ids))
                    or any(w in (a.text or "").lower() for w in ("underwater", "submerged", "deep underwater", "scuba diving", "bottom of the ocean", "bottom of the sea"))
                )
            ]
            if submerged_scenes:
                for p_atom in active_poses:
                    if not p_atom.can_detect or not index.is_active(p_atom):
                        continue
                    if p_atom.facts and p_atom.facts.pose_facts:
                        pf = p_atom.facts.pose_facts
                        # 如果姿态显式要求坚实地面/坐卧支撑，且不支持水体
                        if pf.body_support in ("standing", "sitting", "kneeling", "crouched") and not is_pose_support_compatible(pf, "aquatic"):
                            if p_atom.can_delete_atom:
                                index.drop(p_atom.atom_id)
                                ledger.record_drop(
                                    rule_item.id,
                                    rule_item.phase,
                                    "spatial_pose_support_incompatible",
                                    tuple(s.atom_id for s in submerged_scenes if index.is_active(s)),
                                    p_atom,
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

        # 1. 裸露等级决定允许的最大服装穿着 (优先消费内部 NudityConstraint 对象，外部覆盖始终优先)
        nudity_atoms = index.get_active_by_slot("nudity")
        detected_nudity: List[Tuple[str, PromptAtom]] = []

        for a in nudity_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            matched_lvl = None
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and len(a.facts.allowed_nudity_levels) == 1:
                    matched_lvl = a.facts.allowed_nudity_levels[0]
                if not matched_lvl and is_external_atom(a):
                    ext_code = normalize_nudity_level_code(a.text)
                    if ext_code in VALID_NUDITY_LEVELS:
                        matched_lvl = ext_code
                if not matched_lvl:
                    raw_id = (((a.provenance.item_id if a.provenance else "") or "") + " " + (a.source_item_id or "") + " " + ((a.origin.selected_id if a.origin else "") or "")).upper()
                    for lvl in ("L6", "L5", "L4", "L3", "L2", "L1"):
                        if lvl in raw_id:
                            matched_lvl = lvl
                            break
                if not matched_lvl and a.facts:
                    if "full_nude" in a.facts.exposed_regions or (
                        "full_body" in a.facts.visible_regions
                        and "none" not in a.facts.exposed_regions
                        and not any(lvl in ("L1", "L2", "L3") for lvl in a.facts.allowed_nudity_levels)
                    ):
                        matched_lvl = "L5"
                    elif any(r in a.facts.exposed_regions for r in ("genitals", "anus", "buttocks")):
                        matched_lvl = "L5"
                    elif any(r in a.facts.visible_regions for r in ("crotch", "buttocks")):
                        matched_lvl = "L5"
                    elif "breasts" in a.facts.exposed_regions or any(r in a.facts.visible_regions for r in ("breasts", "topless")):
                        matched_lvl = "L4"
                    elif any(r in a.facts.exposed_regions for r in ("cleavage", "midriff")) or any(r in a.facts.visible_regions for r in ("cleavage", "midriff")):
                        matched_lvl = "L2"
                if not matched_lvl and is_external_atom(a) and tf.enabled:
                    for lvl, ps in active_lvl_specs.items():
                        if ps.matches(a.text):
                            matched_lvl = lvl
                            break
            elif tf.enabled:
                for lvl, ps in active_lvl_specs.items():
                    if ps.matches(a.text):
                        matched_lvl = lvl
                        break
            if matched_lvl:
                detected_nudity.append((matched_lvl, a))

        constraint = self._active_nudity_constraint
        dominant_lvl: Optional[str] = None
        nudity_winner: Optional[PromptAtom] = None

        ext_detected = [(lvl, a) for (lvl, a) in detected_nudity if is_external_atom(a)]
        if ext_detected:
            ext_detected.sort(key=lambda x: (x[1].tag_order, x[1].span_order))
            dominant_lvl, nudity_winner = ext_detected[0]
            base_c = constraint or NudityConstraint(
                requested_level="unspecified",
                effective_level=None,
                source="unconstrained",
            )
            prev_differs = (
                (base_c.effective_level is not None and base_c.effective_level != dominant_lvl)
                or (base_c.requested_level in VALID_NUDITY_LEVELS and base_c.requested_level != dominant_lvl)
                or base_c.requested_level in ("none", "random")
            )
            yielded = base_c.yielded_to_override or prev_differs
            y_reason = base_c.yield_reason or ("external_nudity_override" if yielded else None)
            if (
                base_c.source != "external_override"
                or base_c.effective_level != dominant_lvl
                or yielded != base_c.yielded_to_override
                or base_c.selected_branch is not None
            ):
                self._active_nudity_constraint = replace(
                    base_c,
                    effective_level=dominant_lvl,
                    source="external_override",
                    selected_branch=None,
                    yielded_to_override=yielded,
                    yield_reason=y_reason,
                )
        elif (
            constraint is not None
            and constraint.effective_level
            and constraint.source in ("ui_explicit", "ui_random", "external_override", "derived_override")
            and (detected_nudity or constraint.source in ("ui_explicit", "external_override", "derived_override"))
        ):
            dominant_lvl = constraint.effective_level
            if detected_nudity:
                detected_nudity.sort(key=lambda x: (x[1].tag_order, x[1].span_order))
                nudity_winner = detected_nudity[0][1]
            elif constraint.source == "external_override":
                ext_nudity_atoms = [a for a in nudity_atoms if is_external_atom(a) and index.is_active(a)]
                if ext_nudity_atoms:
                    ext_nudity_atoms.sort(key=lambda x: (x.tag_order, x.span_order))
                    nudity_winner = ext_nudity_atoms[0]
        elif detected_nudity:
            detected_nudity.sort(key=lambda x: (x[1].tag_order, x[1].span_order))
            dominant_lvl, nudity_winner = detected_nudity[0]

        if dominant_lvl:
            winner_atom_id = nudity_winner.atom_id if nudity_winner is not None else None
            winner_ids = (winner_atom_id,) if winner_atom_id is not None else ()
            selected_branch = (
                self._active_nudity_constraint.selected_branch
                if self._active_nudity_constraint is not None
                else None
            )
            nc_trace = _format_nudity_constraint_trace(self._active_nudity_constraint)
            candidates = [
                a for a in index.get_all_active_ordered()
                if a.source_slot != "nudity" and a.atom_id != winner_atom_id
            ]

            for a in candidates:
                if not a.can_detect or not index.is_active(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                should_drop = False
                is_fallback = False

                if is_formal_atom(a) or is_external_atom(a):
                    if not _is_atom_compatible_with_level_and_branch(a, dominant_lvl, selected_branch):
                        should_drop = True
                    elif (
                        dominant_lvl == "L3"
                        and nudity_winner is not None
                        and "bra removed" in nudity_winner.text.lower()
                        and (
                            (a.facts and "intimate_upper" in a.facts.covered_regions)
                            or (
                                ((a.facts and a.facts.piece_role == "underwear") or a.source_slot in ("clothing", "underwear"))
                                and re.search(r"\b(?:bra|bralette|bustier)\b", a.text.lower())
                                and "no bra" not in a.text.lower()
                                and "bra removed" not in a.text.lower()
                            )
                        )
                    ):
                        should_drop = True
                elif tf.enabled:
                    if dominant_lvl in ("L5", "L6") and _is_non_worn_discarded_narrative_atom(a):
                        should_drop = False
                    elif dominant_lvl == "L6" and _is_upper_body_cutoff_framing_atom(a):
                        should_drop = True
                    elif not _is_atom_compatible_with_level_and_branch(a, dominant_lvl, selected_branch):
                        should_drop = True
                        is_fallback = True
                    elif dominant_lvl == "L4":
                        if any(b.matches(a.text) for b in level_rules["L4"].banned_patterns):
                            should_drop = True
                            is_fallback = True
                    elif dominant_lvl == "L3":
                        if any(b.matches(a.text) for b in level_rules["L3"].banned_patterns):
                            should_drop = True
                            is_fallback = True
                    elif dominant_lvl in ("L5", "L6"):
                        if any(b.matches(a.text) for b in level_rules["L5"].banned_patterns):
                            should_drop = True
                            is_fallback = True
                    else:
                        lvl_rule = level_rules.get(dominant_lvl)
                        if lvl_rule and not _is_safe_non_nude_atom_text(a) and any(b.matches(a.text) for b in lvl_rule.banned_patterns):
                            should_drop = True
                            is_fallback = True

                if should_drop:
                    if is_external_atom(nudity_winner) and is_external_atom(a):
                        raise PromptValidationError(
                            f"External override conflict under rule '{rule_item.id}': slot '{nudity_winner.source_slot}' ('{nudity_winner.text}') conflicts with slot '{a.source_slot}' ('{a.text}')",
                            slots=(nudity_winner.source_slot, a.source_slot),
                            texts=(nudity_winner.text, a.text),
                            rule_id=rule_item.id,
                            details={"nudity_constraint": nc_trace} if nc_trace else None,
                        )
                    if is_external_atom(a) and not is_external_atom(nudity_winner):
                        for n_atom in nudity_atoms:
                            if not is_external_atom(n_atom) and n_atom.can_delete_atom and index.is_active(n_atom):
                                index.drop(n_atom.atom_id)
                                ft = {
                                    "rule_id": rule_item.id,
                                    "phase": rule_item.phase,
                                    "priority": rule_item.priority,
                                    "winner_objects": [a.atom_id],
                                    "target_objects": [n_atom.atom_id],
                                }
                                if nc_trace:
                                    ft["nudity_constraint"] = nc_trace
                                ledger.record_drop(
                                    rule_item.id,
                                    rule_item.phase,
                                    "clothing_removes_nudity",
                                    (a.atom_id,),
                                    n_atom,
                                    fact_trace=ft,
                                )
                        if a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
                            self._yield_constraint_to_external_clothing(index.get_all_active_ordered())
                        else:
                            base_c = self._active_nudity_constraint or NudityConstraint(
                                requested_level="unspecified",
                                effective_level=None,
                                source="unconstrained",
                            )
                            self._active_nudity_constraint = replace(
                                base_c,
                                effective_level=None,
                                source="derived_override",
                                selected_branch=None,
                                yielded_to_override=True,
                                yield_reason=f"external_{a.source_slot}_override",
                            )
                        continue
                    if not a.can_delete_atom:
                        continue
                    if is_fallback:
                        self.text_fallback_hits += 1
                    underwear_pattern = re.compile(r"\b(underwear|bikini|panties|bra|lingerie)\b")
                    is_underwear = (
                        "underwear" in a.source_slot
                        or (a.facts and any(t in a.facts.garment_topologies for t in ("underwear", "panties", "bra", "bikini", "lingerie")))
                        or (a.facts and a.facts.piece_role == "underwear")
                        or bool(underwear_pattern.search((a.source_item_id or "").lower().replace("_", " ")))
                        or bool(underwear_pattern.search(((a.provenance.item_id if a.provenance else "") or "").lower().replace("_", " ")))
                    )
                    rc = "nudity_removes_underwear" if is_underwear else "nudity_removes_clothing"
                    index.drop(a.atom_id)
                    ft = {
                        "rule_id": rule_item.id,
                        "phase": rule_item.phase,
                        "priority": rule_item.priority,
                        "winner_objects": list(winner_ids),
                        "target_objects": [a.atom_id],
                    }
                    if nc_trace:
                        ft["nudity_constraint"] = nc_trace
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        rc,
                        winner_ids,
                        a,
                        fact_trace=ft,
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
                    if is_formal_atom(a) or is_external_atom(a):
                        if self._is_formal_section2_nudity_trigger(a):
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
                        if is_formal_atom(a) or is_external_atom(a):
                            if a.facts and any(t in a.facts.garment_topologies for t in ("panties", "bra", "underwear", "suit", "blouse", "dress", "outerwear", "bottom_pants", "bottom_skirt")):
                                is_banned = True
                        elif tf.enabled:
                            if any(b.matches(a.text) for b in bans):
                                is_banned = True
                                is_fallback = True

                        if is_banned:
                            if is_external_atom(first_trig) and is_external_atom(a):
                                raise PromptValidationError(
                                    f"External override conflict under rule '{rule_item.id}': slot '{first_trig.source_slot}' ('{first_trig.text}') conflicts with slot '{a.source_slot}' ('{a.text}')",
                                    slots=(first_trig.source_slot, a.source_slot),
                                    texts=(first_trig.text, a.text),
                                    rule_id=rule_item.id,
                                )
                            if is_external_atom(a) and not is_external_atom(first_trig):
                                if first_trig.can_delete_atom and index.is_active(first_trig):
                                    index.drop(first_trig.atom_id)
                                    ledger.record_drop(
                                        rule_item.id,
                                        rule_item.phase,
                                        "clothing_removes_nudity",
                                        (a.atom_id,),
                                        first_trig,
                                    )
                                self._yield_constraint_to_external_clothing(index.get_all_active_ordered())
                                continue
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

        # 3. 提示词级服装载体前提核验：有/无在穿衣物时均不得保留缺乏真实对应载体的裸露词条（如无裙装时剔除 skirt slit）
        active_now = index.get_all_active_ordered()
        worn_garments = [a for a in active_now if a.can_detect and index.is_active(a) and _is_worn_garment_carrier(a)]
        nc_trace_cur = _format_nudity_constraint_trace(self._active_nudity_constraint)
        ext_garments = [g for g in worn_garments if is_external_atom(g)]
        win_garment = ext_garments[0] if ext_garments else (worn_garments[0] if worn_garments else None)
        winner_ids_carrier = (win_garment.atom_id,) if win_garment is not None else ()
        for na in active_now:
            if not index.is_active(na) or not na.can_detect or na.source_slot != "nudity":
                continue
            req_carrier = get_required_carrier_kind(na)
            if req_carrier is None:
                continue
            if worn_garments and is_carrier_satisfied_by_garments(req_carrier, worn_garments):
                continue
            if is_external_atom(na):
                if ext_garments:
                    raise PromptValidationError(
                        f"External override conflict under rule '{rule_item.id}': nudity ('{na.text}') requires '{req_carrier}' carrier missing in external clothing ('{ext_garments[0].text}')",
                        slots=(na.source_slot, ext_garments[0].source_slot),
                        texts=(na.text, ext_garments[0].text),
                        rule_id=rule_item.id,
                        error_code="missing_garment_carrier",
                        details={"required_carrier": req_carrier, "nudity_constraint": nc_trace_cur} if nc_trace_cur else {"required_carrier": req_carrier},
                    )
                continue
            if not na.can_delete_atom:
                continue
            index.drop(na.atom_id)
            ft = {
                "rule_id": rule_item.id,
                "phase": rule_item.phase,
                "priority": rule_item.priority,
                "winner_objects": list(winner_ids_carrier),
                "target_objects": [na.atom_id],
                "required_carrier": req_carrier,
            }
            if nc_trace_cur:
                ft["nudity_constraint"] = nc_trace_cur
            ledger.record_drop(
                rule_item.id,
                rule_item.phase,
                "clothing_removes_nudity",
                winner_ids_carrier,
                na,
                fact_trace=ft,
            )

        return index.get_all_active_ordered()

    def _yield_constraint_to_external_clothing(self, active_atoms: Sequence[PromptAtom]) -> None:
        """外部服装覆盖迫使内部裸露约束让位：统一更新 source/effective_level/selected_branch。"""
        base_c = self._active_nudity_constraint or NudityConstraint(
            requested_level="unspecified",
            effective_level=None,
            source="unconstrained",
        )
        ext_clothing = [
            x for x in active_atoms
            if is_external_atom(x)
            and x.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear")
        ]
        derived_lvl: Optional[str] = None
        annotated_sets = [
            set(x.facts.allowed_nudity_levels)
            for x in ext_clothing
            if x.facts and x.facts.allowed_nudity_levels
        ]
        if annotated_sets and len(annotated_sets) == len(ext_clothing):
            common = set.intersection(*annotated_sets)
            if len(common) == 1:
                derived_lvl = next(iter(common))
        self._active_nudity_constraint = replace(
            base_c,
            effective_level=derived_lvl,
            source="derived_override",
            selected_branch=None,
            yielded_to_override=True,
            yield_reason="external_clothing_override",
        )

    def _is_formal_section2_nudity_trigger(self, a: PromptAtom) -> bool:
        """判断形式化原子是否触发裸露全衣禁令（仅由实际裸露状态触发，严禁仅因 allowed_nudity_levels 含 L5/L6 误触发）。"""
        if not a.facts:
            return False
        if a.facts.piece_role in ("top", "bottom", "one_piece", "ensemble", "outerwear", "legwear", "footwear", "underwear"):
            return False
        is_nude_slot = (
            a.source_slot == "nudity"
            or (a.origin and a.origin.selector == "nudity")
            or (a.provenance and a.provenance.kind == "nudity")
        )
        if not is_nude_slot and "discarded" in a.facts.garment_states:
            return False
        exp = a.facts.exposed_regions
        alvl = a.facts.allowed_nudity_levels
        if exp or alvl:
            if "none" in exp:
                return False
            if alvl and all(lvl in ("L1", "L2", "L3", "L4") for lvl in alvl):
                return False
            if not (is_nude_slot or a.facts.coverage_delta == "uncover_all"):
                return False
            return "full_nude" in exp or any(r in ("genitals", "anus") for r in exp)
        return any(r in a.facts.visible_regions for r in ("crotch", "breasts", "buttocks", "pubic")) or (
            "full_body" in a.facts.visible_regions and is_nude_slot
        )

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
                    if is_external_atom(a):
                        # 软偏好保护：外部显式输入或自定义覆盖的下半身/鞋袜标签 100% 原样保留，绝不因构图软偏好删除用户输入
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
            + index.get_active_by_fact("hand_state", "restrained")
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
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and (a.facts.hand_state in ("both_busy", "one_busy", "restrained") or a.facts.hands_required > 0 or "busy_hands" in a.facts.mutex_groups):
                    busy_atoms.append(a)
                elif any(w in a.text.lower() for w in ("bound", "tied", "behind back", "hands on head", "restrained")):
                    busy_atoms.append(a)
            elif tf.enabled:
                if any(t.matches(a.text) for t in all_busy_pose) or any(w in a.text.lower() for w in ("bound", "tied", "behind back", "hands on head", "restrained")):
                    busy_atoms.append(a)

        # 收集全部手持道具 (统一系统与外部路径)
        detected_props: List[Tuple[PromptAtom, int, bool]] = []
        seen_prop_ids = set()
        for a in index.get_active_by_slot("props"):
            if a in busy_atoms or not a.can_detect or not index.is_active(a) or a.atom_id in seen_prop_ids:
                continue
            self._validate_formal_atom(a, rule_item.id)
            is_loser = False
            is_fallback = False
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"):
                    is_loser = True
                elif any(w in a.text.lower() for w in ("holding", "in hand", "held", "carrying", "phone", "cup", "camera", "controller", "spatula")):
                    is_loser = True
            if not is_loser and tf.enabled:
                if any(p.matches(a.text) for p in all_handheld):
                    is_loser = True
                    is_fallback = True
            if is_loser:
                seen_prop_ids.add(a.atom_id)
                h_req = (a.facts.hands_required if (a.facts and a.facts.hands_required > 0) else (2 if any(w in a.text.lower() for w in ("controller", "two hands", "sword", "rifle")) else 1))
                detected_props.append((a, h_req, is_fallback))

        if busy_atoms:
            pose_winner = min(busy_atoms, key=lambda x: (x.tag_order, x.span_order))
            pose_hands = 2
            if pose_winner.facts:
                if pose_winner.facts.hand_state == "one_busy" or pose_winner.facts.hands_required == 1:
                    pose_hands = 1
                elif pose_winner.facts.hand_state in ("both_busy", "restrained") or pose_winner.facts.hands_required == 2:
                    pose_hands = 2
                elif any(w in pose_winner.text.lower() for w in ("cheek", "hip", "chin", "touching")):
                    pose_hands = 1
                else:
                    pose_hands = 2
            else:
                pose_hands = 2 if any(w in pose_winner.text.lower() for w in ("bound", "tied", "behind back", "hands on head", "restrained")) else 1

            # 统一按道具实体聚合
            detected_prop_atoms = [p[0] for p in detected_props]
            prop_entities = build_prop_entities(detected_prop_atoms)
            ext_entities = [e for e in prop_entities if e.is_external]
            total_ext_prop_hands = sum(e.hands_required for e in ext_entities)
            is_ext_pose = is_external_atom(pose_winner)

            # 1. 外部道具间超容量检测 (3 > 2 Fail-Closed)
            if total_ext_prop_hands > 2:
                p1 = ext_entities[0].atoms[0]
                p2 = ext_entities[1].atoms[0]
                raise PromptValidationError(
                    f"External override conflict under rule '{rule_item.id}': external props exceed total hand capacity of 2 ({total_ext_prop_hands} hands)",
                    slots=(p1.source_slot, p2.source_slot),
                    texts=(p1.text, p2.text),
                    rule_id=rule_item.id,
                )

            # 2. 外部姿势 + 外部道具超容量检测 (1 + 2 > 2 Fail-Closed)
            if is_ext_pose and (pose_hands + total_ext_prop_hands > 2):
                first_prop = ext_entities[0].atoms[0]
                raise PromptValidationError(
                    f"External override conflict under rule '{rule_item.id}': slot '{pose_winner.source_slot}' ('{pose_winner.text}') conflicts with slot '{first_prop.source_slot}' ('{first_prop.text}')",
                    slots=(pose_winner.source_slot, first_prop.source_slot),
                    texts=(pose_winner.text, first_prop.text),
                    rule_id=rule_item.id,
                )

            # 3. 外部道具优先于系统姿势：外部道具使双手超容时，系统姿势被外部道具裁撤
            if not is_ext_pose and ext_entities and (pose_hands + total_ext_prop_hands > 2):
                if pose_winner.can_delete_atom and index.is_active(pose_winner):
                    index.drop(pose_winner.atom_id)
                    ft = {
                        "rule_id": rule_item.id,
                        "phase": rule_item.phase,
                        "priority": rule_item.priority,
                        "winner_objects": [ext_entities[0].atoms[0].atom_id],
                        "target_objects": [pose_winner.atom_id],
                        "prop_capacity": {
                            "capacity_limit": 2,
                            "total_hands_demanded": pose_hands + total_ext_prop_hands,
                            "entity_demands": [
                                {
                                    "entity_id": str(e.entity_id),
                                    "hands_required": int(e.hands_required),
                                    "hand_state": "one_busy" if e.hands_required == 1 else ("both_busy" if e.hands_required >= 2 else "free"),
                                }
                                for e in ext_entities
                            ] + [
                                {
                                    "entity_id": str(pose_winner.atom_id),
                                    "hands_required": int(pose_hands),
                                    "hand_state": "both_busy" if pose_hands >= 2 else ("one_busy" if pose_hands == 1 else "free"),
                                }
                            ],
                        },
                    }
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "external_prop_overrides_pose",
                        (ext_entities[0].atoms[0].atom_id,),
                        pose_winner,
                        fact_trace=ft,
                    )
                    pose_hands = 0

            # 4. 系统道具容量核算 (按实体分配容量！同一实体的多个描述整体保留)
            available_hands = 2 - pose_hands - total_ext_prop_hands
            for e in prop_entities:
                if e.is_external:
                    continue
                if e.hands_required <= available_hands:
                    available_hands -= e.hands_required
                else:
                    for a in e.atoms:
                        if not a.can_delete_atom or not index.is_active(a):
                            continue
                        if tf.enabled and any(p.matches(a.text) for p in all_handheld):
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ft = {
                            "rule_id": rule_item.id,
                            "phase": rule_item.phase,
                            "priority": rule_item.priority,
                            "winner_objects": [pose_winner.atom_id],
                            "target_objects": [a.atom_id],
                            "prop_capacity": {
                                "capacity_limit": 2,
                                "total_hands_demanded": pose_hands + total_ext_prop_hands + sum(ent.hands_required for ent in prop_entities if not ent.is_external),
                                "entity_demands": [
                                    {
                                        "entity_id": str(ent.entity_id),
                                        "hands_required": int(ent.hands_required),
                                        "hand_state": "one_busy" if ent.hands_required == 1 else ("both_busy" if ent.hands_required >= 2 else "free"),
                                    }
                                    for ent in prop_entities
                                ],
                            },
                        }
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "busy_hands_remove_props",
                            (pose_winner.atom_id,),
                            a,
                            fact_trace=ft,
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

        # 1. 检查姿势占用双手情况
        busy_atoms = []
        for a in (
            index.get_active_by_fact("hand_state", "both_busy")
            + index.get_active_by_fact("hand_state", "one_busy")
            + index.get_active_by_fact("hand_state", "restrained")
            + index.get_active_by_mutex_group("busy_hands")
        ):
            if a.can_detect and index.is_active(a):
                busy_atoms.append(a)
        for a in index.get_active_by_slot("pose"):
            if a in busy_atoms or not a.can_detect or not index.is_active(a):
                continue
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and (a.facts.hand_state in ("both_busy", "one_busy", "restrained") or a.facts.hands_required > 0):
                    busy_atoms.append(a)
                elif any(w in a.text.lower() for w in ("bound", "tied", "behind back", "hands on head", "restrained")):
                    busy_atoms.append(a)

        pose_hands = 0
        is_ext_pose = False
        pose_atom = None
        if busy_atoms:
            pose_atom = min(busy_atoms, key=lambda x: (x.tag_order, x.span_order))
            is_ext_pose = is_external_atom(pose_atom)
            if pose_atom.facts:
                if pose_atom.facts.hand_state == "one_busy" or pose_atom.facts.hands_required == 1:
                    pose_hands = 1
                elif pose_atom.facts.hand_state in ("both_busy", "restrained") or pose_atom.facts.hands_required == 2:
                    pose_hands = 2
                elif any(w in pose_atom.text.lower() for w in ("cheek", "hip", "chin", "touching")):
                    pose_hands = 1
                else:
                    pose_hands = 2
            else:
                pose_hands = 2 if any(w in pose_atom.text.lower() for w in ("bound", "tied", "behind back", "hands on head", "restrained")) else 1

        # 2. 收集全部手持道具 (实际消费事实索引 R2R3-P3-001)
        hh_fact_atoms = index.get_active_by_fact("prop_usage", "handheld")
        hh_atoms: List[PromptAtom] = []
        seen_ids = set()

        for a in hh_fact_atoms:
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_ids.add(a.atom_id)
            hh_atoms.append(a)

        for a in index.get_active_by_slot("props"):
            if a.atom_id in seen_ids or not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"):
                    seen_ids.add(a.atom_id)
                    hh_atoms.append(a)
                elif any(w in a.text.lower() for w in ("holding", "in hand", "held", "carrying", "phone", "cup", "camera", "controller", "spatula")):
                    seen_ids.add(a.atom_id)
                    hh_atoms.append(a)
            elif tf.enabled:
                if any(hp.matches(a.text) for hp in handheld_patterns):
                    seen_ids.add(a.atom_id)
                    hh_atoms.append(a)

        if not hh_atoms:
            return index.get_all_active_ordered()

        # 3. 按道具实体区分“同一道具的多个描述”与“多个独立道具”
        entity_list = build_prop_entities(hh_atoms)

        # 4. 外部道具间超容量检测与外部姿势冲突检测
        ext_entities = [e for e in entity_list if e.is_external]
        total_ext_hands = sum(e.hands_required for e in ext_entities)
        if total_ext_hands > 2:
            p1 = ext_entities[0].atoms[0]
            p2 = ext_entities[1].atoms[0]
            raise PromptValidationError(
                f"External override conflict under rule '{rule_item.id}': external props exceed total hand capacity of 2 ({total_ext_hands} hands)",
                slots=(p1.source_slot, p2.source_slot),
                texts=(p1.text, p2.text),
                rule_id=rule_item.id,
            )

        if is_ext_pose and (pose_hands + total_ext_hands > 2):
            first_prop = ext_entities[0].atoms[0]
            raise PromptValidationError(
                f"External override conflict under rule '{rule_item.id}': slot '{pose_atom.source_slot}' ('{pose_atom.text}') conflicts with slot '{first_prop.source_slot}' ('{first_prop.text}')",
                slots=(pose_atom.source_slot, first_prop.source_slot),
                texts=(pose_atom.text, first_prop.text),
                rule_id=rule_item.id,
            )

        # 外部道具优先于系统姿势：外部道具使双手超容时，系统姿势退让
        if not is_ext_pose and ext_entities and (pose_hands + total_ext_hands > 2):
            if pose_atom and pose_atom.can_delete_atom and index.is_active(pose_atom):
                index.drop(pose_atom.atom_id)
                winner_id = ext_entities[0].atoms[0].atom_id
                ft_pose = {
                    "rule_id": rule_item.id,
                    "phase": rule_item.phase,
                    "priority": rule_item.priority,
                    "winner_objects": [winner_id],
                    "target_objects": [pose_atom.atom_id],
                    "prop_capacity": {
                        "capacity_limit": 2,
                        "total_hands_demanded": int(pose_hands + sum(ent.hands_required for ent in entity_list)),
                        "entity_demands": [
                            {
                                "entity_id": str(ent.entity_id),
                                "hands_required": int(ent.hands_required),
                                "hand_state": "one_busy" if ent.hands_required == 1 else ("both_busy" if ent.hands_required >= 2 else "free"),
                            }
                            for ent in entity_list
                        ],
                    },
                }
                ledger.record_drop(
                    rule_item.id,
                    rule_item.phase,
                    "external_prop_overrides_pose",
                    (winner_id,),
                    pose_atom,
                    fact_trace=ft_pose,
                )
                pose_hands = 0

        # 5. 统一容量分配（系统、外部、混合来源共用 2 手模型；双手空闲时允许两件单手道具共存）
        available_hands = max(0, 2 - pose_hands)
        winner_atom = entity_list[0].atoms[0] if entity_list else None

        for e in entity_list:
            if e.hands_required <= available_hands:
                available_hands -= e.hands_required
            else:
                for a in e.atoms:
                    if not a.can_delete_atom or not index.is_active(a):
                        continue
                    if tf and tf.enabled and any(hp.matches(a.text) for hp in handheld_patterns):
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ft_prop = {
                        "rule_id": rule_item.id,
                        "phase": rule_item.phase,
                        "priority": rule_item.priority,
                        "winner_objects": [winner_atom.atom_id] if winner_atom else [],
                        "target_objects": [a.atom_id],
                        "prop_capacity": {
                            "capacity_limit": 2,
                            "total_hands_demanded": int(pose_hands + sum(ent.hands_required for ent in entity_list)),
                            "entity_demands": [
                                {
                                    "entity_id": str(ent.entity_id),
                                    "hands_required": int(ent.hands_required),
                                    "hand_state": "one_busy" if ent.hands_required == 1 else ("both_busy" if ent.hands_required >= 2 else "free"),
                                }
                                for ent in entity_list
                            ],
                        },
                    }
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "single_handheld_prop_limit",
                        (winner_atom.atom_id,) if winner_atom else (),
                        a,
                        fact_trace=ft_prop,
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
                                if not is_garment_compatible_with_state(binding.target_entity, "unbuttoned", a):
                                    is_loser = True
                            elif binding.status in (BindingStatus.UNBOUND_NO_CANDIDATE, BindingStatus.UNBOUND_INCOMPATIBLE, BindingStatus.UNBOUND_TARGET_NOT_FOUND):
                                if any(not is_garment_compatible_with_state(oe, "unbuttoned", a) for oe in op_entities):
                                    is_loser = True
                        elif state_id == "unzipped":
                            # 使用四阶绑定阶梯检查拉链目标
                            binding = find_bound_carrier(a, worn_entities)
                            if binding.status == BindingStatus.BOUND and binding.target_entity:
                                if not is_garment_compatible_with_state(binding.target_entity, "unzipped", a):
                                    is_loser = True
                            elif binding.status in (BindingStatus.UNBOUND_NO_CANDIDATE, BindingStatus.UNBOUND_INCOMPATIBLE, BindingStatus.UNBOUND_TARGET_NOT_FOUND):
                                if any(not is_garment_compatible_with_state(oe, "unzipped", a) for oe in op_entities):
                                    is_loser = True
                        elif state_id == "lifted_up" or (a.facts and any(s in a.facts.garment_states for s in ("lifted", "lifted_up", "lifted_skirt"))):
                            binding = find_bound_carrier(a, worn_entities)
                            if binding.status == BindingStatus.BOUND and binding.target_entity:
                                if not is_garment_compatible_with_state(binding.target_entity, "lifted_up", a):
                                    is_loser = True
                            elif binding.status in (BindingStatus.UNBOUND_NO_CANDIDATE, BindingStatus.UNBOUND_INCOMPATIBLE, BindingStatus.UNBOUND_TARGET_NOT_FOUND):
                                if any(not is_garment_compatible_with_state(oe, "lifted_up", a) for oe in op_entities):
                                    is_loser = True
                        elif a.facts and "removed" in a.facts.garment_states:
                            # 缺席状态 (braless, underwearless) 属于内衣层缺席声明，由后续 step 4 缺席状态专用互斥逻辑裁决，不得在此被误判为连体外装冲突
                            if not (
                                a.source_item_id in CANONICAL_ABSENCE_STATES
                                or (a.origin and a.origin.selected_id in CANONICAL_ABSENCE_STATES)
                                or (a.facts and "underwear" in a.facts.garment_topologies)
                            ):
                                is_loser = True
                    elif tf.enabled:
                        if any(bs.matches(a.text) for bs in one_piece_banned_states):
                            is_loser = True
                            is_fallback = True

                    if is_loser:
                        if is_external_atom(op_winner) and is_external_atom(a):
                            raise PromptValidationError(
                                f"External override conflict under rule '{rule_item.id}': one-piece clothing ('{op_winner.text}') conflicts with state ('{a.text}')",
                                slots=(op_winner.source_slot, a.source_slot),
                                texts=(op_winner.text, a.text),
                                rule_id=rule_item.id,
                            )
                        if is_external_atom(a) and not is_external_atom(op_winner):
                            if op_winner.can_delete_atom and index.is_active(op_winner):
                                index.drop(op_winner.atom_id)
                                ledger.record_drop(
                                    rule_item.id,
                                    rule_item.phase,
                                    "external_state_overrides_one_piece",
                                    (a.atom_id,),
                                    op_winner,
                                )
                            continue
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
                        if is_external_atom(pants_winner) and is_external_atom(a):
                            raise PromptValidationError(
                                f"External override conflict under rule '{rule_item.id}': pants ('{pants_winner.text}') conflicts with skirt state ('{a.text}')",
                                slots=(pants_winner.source_slot, a.source_slot),
                                texts=(pants_winner.text, a.text),
                                rule_id=rule_item.id,
                            )
                        if is_external_atom(a) and not is_external_atom(pants_winner):
                            if pants_winner.can_delete_atom and index.is_active(pants_winner):
                                index.drop(pants_winner.atom_id)
                                ledger.record_drop(
                                    rule_item.id,
                                    rule_item.phase,
                                    "external_skirt_state_overrides_pants",
                                    (a.atom_id,),
                                    pants_winner,
                                )
                            continue
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
                kimono_entities = [e for e in worn_entities if e.selected_id in ("kimono", "yukata", "furisode", "robe_general", "taoist_robe", "battle_robe", "bathrobe", "witch_robe", "wizard_robe")]
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
                    if e.selected_id in ("basic_underwear", "lingerie_lace", "bikini_classic", "bikini_strappy", "bikini_micro")
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

        # 5. 承载主体检查 (提示词级载体前提核验 + 四阶绑定阶梯检查在穿承载物)
        worn_garment_atoms = [
            w for w in index.get_all_active_ordered()
            if index.is_active(w) and w.can_detect and _is_worn_garment_carrier(w)
        ]
        for a in active_atoms:
            if not index.is_active(a) or not a.can_detect:
                continue

            if a.source_slot != "nudity":
                req_carrier = get_required_carrier_kind(a)
                if req_carrier is not None:
                    has_carrier = bool(worn_garment_atoms) and is_carrier_satisfied_by_garments(req_carrier, worn_garment_atoms)
                    if not has_carrier:
                        if is_external_atom(a):
                            ext_g = [g for g in worn_garment_atoms if is_external_atom(g)]
                            if ext_g:
                                raise PromptValidationError(
                                    f"External override conflict under rule '{rule_item.id}': modifier ('{a.text}') requires '{req_carrier}' carrier missing in external clothing ('{ext_g[0].text}')",
                                    slots=(a.source_slot, ext_g[0].source_slot),
                                    texts=(a.text, ext_g[0].text),
                                    rule_id=rule_item.id,
                                    error_code="missing_garment_carrier",
                                    details={"required_carrier": req_carrier},
                                )
                            continue
                        if a.can_delete_atom:
                            index.drop(a.atom_id)
                            ledger.record_drop(
                                rule_item.id,
                                rule_item.phase,
                                "state_lacks_carrier",
                                tuple(w.atom_id for w in worn_garment_atoms[:2]),
                                a,
                            )
                        continue

            if not a.can_delete_atom or not is_formal_atom(a):
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

    def _is_material_replacement_compatible(
        self,
        orig_atom: PromptAtom,
        cand_rep: str,
        cand_atom: PromptAtom,
        active_snapshot: Sequence[PromptAtom],
        eff_lvl: Optional[str],
        eff_branch: Optional[str],
    ) -> bool:
        if not _is_atom_compatible_with_level_and_branch(cand_atom, eff_lvl, eff_branch):
            return False

        tl = cand_rep.strip().lower()
        other_active = [x for x in active_snapshot if x.atom_id != orig_atom.atom_id]
        is_state_origin = (
            orig_atom.source_slot == "clothing_state"
            or (orig_atom.provenance is not None and orig_atom.provenance.kind == "clothing_state")
            or is_garment_modifier_atom(orig_atom)
        )
        other_has_garment = any(_is_worn_garment_carrier(x) for x in other_active)

        # 状态词或已存在在穿主衣物时，严禁凭空改写为第二件“wet dress”导致形制/状态互搏
        if "dress" in tl and (is_state_origin or other_has_garment):
            return False

        worn_entities_other = [
            e for e in extract_garment_entities(other_active).values()
            if e.is_worn and not e.is_ambient
        ]
        if worn_entities_other:
            req_carrier = get_required_carrier_kind(cand_atom)
            if req_carrier is not None:
                worn_atoms = [m for e in worn_entities_other for m in e.member_atoms]
                if not is_carrier_satisfied_by_garments(req_carrier, worn_atoms):
                    return False
            if "unbuttoned" in tl:
                if not any(is_garment_compatible_with_state(e, "unbuttoned", cand_atom) for e in worn_entities_other):
                    return False
            elif "lifted" in tl:
                if not any(is_garment_compatible_with_state(e, "lifted_up", cand_atom) for e in worn_entities_other):
                    return False
            elif "slipping off shoulder" in tl:
                if not any(
                    is_item_upper_garment_carrier(m, style_id=e.selected_id)
                    for e in worn_entities_other
                    for m in e.member_atoms
                ):
                    return False

        sim_atoms = [cand_atom if x.atom_id == orig_atom.atom_id else x for x in active_snapshot]
        base_conflicts, _ = self.detect_hard_conflicts(other_active)
        conflicts_after, _ = self.detect_hard_conflicts(sim_atoms)
        if any(cand_atom.atom_id in c for c in conflicts_after) or len(conflicts_after) > len(base_conflicts):
            return False
        mismatches_after = find_carrier_mismatch_in_atoms(sim_atoms)
        if any(m[0].atom_id == cand_atom.atom_id for m in mismatches_after):
            return False
        return True

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
            if not index.is_active(a) or not a.can_detect:
                continue
            if (
                a.source_slot == "clothing_extension"
                or (a.provenance and a.provenance.kind == "clothing_extension")
            ):
                continue
            if a.provenance and a.provenance.rule_id == rule_item.id:
                continue

            self._validate_formal_atom(a, rule_item.id, index=index)
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
                if is_external_atom(a) or not a.can_modify_internal:
                    continue
                eff_constraint = getattr(self, "_active_nudity_constraint", None)
                eff_lvl = (
                    eff_constraint.effective_level
                    if (eff_constraint is not None and not eff_constraint.yielded_to_override)
                    else None
                )
                eff_branch = eff_constraint.selected_branch if eff_constraint is not None else None
                nc_trace = _format_nudity_constraint_trace(eff_constraint)

                active_snapshot = [x for x in index.get_all_active_ordered() if index.is_active(x)]
                valid_replacements: List[str] = []
                for cand_rep in replacements:
                    cand_facts = _align_facts_with_rewritten_text(cand_rep, a.facts)
                    cand_atom = make_replaced_atom(a, cand_rep, rule_item.id, new_facts=cand_facts)
                    if self._is_material_replacement_compatible(
                        a, cand_rep, cand_atom, active_snapshot, eff_lvl, eff_branch
                    ):
                        valid_replacements.append(cand_rep)

                if not valid_replacements:
                    if a.can_delete_atom:
                        if is_fallback:
                            self.text_fallback_hits += 1
                        index.drop(a.atom_id)
                        ft = {
                            "rule_id": rule_item.id,
                            "phase": rule_item.phase,
                            "priority": rule_item.priority,
                            "winner_objects": [],
                            "target_objects": [a.atom_id],
                        }
                        if nc_trace:
                            ft["nudity_constraint"] = nc_trace
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "material_penetration_removed",
                            (),
                            a,
                            fact_trace=ft,
                        )
                    continue

                rep_text = rng.choice(valid_replacements)
                new_facts = _align_facts_with_rewritten_text(rep_text, a.facts)
                new_a = make_replaced_atom(a, rep_text, rule_item.id, new_facts=new_facts)
                if is_fallback:
                    self.text_fallback_hits += 1
                index.replace(a.atom_id, new_a)
                ft = {
                    "rule_id": rule_item.id,
                    "phase": rule_item.phase,
                    "priority": rule_item.priority,
                    "winner_objects": [],
                    "target_objects": [a.atom_id],
                }
                if nc_trace:
                    ft["nudity_constraint"] = nc_trace
                ledger.record_replace(
                    rule_item.id,
                    rule_item.phase,
                    "material_penetration_replaced",
                    (),
                    a,
                    (new_a,),
                    fact_trace=ft,
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
                    if not a.can_delete_atom or is_external_atom(a):
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

        night_atoms: List[PromptAtom] = []
        for a in index.get_active_by_slots("scene", "scene_theme", "theme", "lighting", "lighting_palette"):
            if not a.can_detect or not index.is_active(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            is_night = False
            if is_formal_atom(a):
                if a.facts and (a.facts.time_of_day in ("night", "midnight", "late_night") or any(ls in ("night", "moonlight", "candlelight", "dim") for ls in a.facts.light_sources)):
                    is_night = True
            elif tf.enabled and any(nt.matches(a.text) for nt in all_night):
                is_night = True
            if is_night:
                night_atoms.append(a)

        daylight_atoms: List[PromptAtom] = []
        for a in index.get_active_by_slots("scene", "scene_theme", "theme", "lighting", "lighting_palette"):
            if not a.can_detect or not index.is_active(a) or a in night_atoms:
                continue
            self._validate_formal_atom(a, rule_item.id)
            is_day = False
            if is_formal_atom(a):
                if a.facts and (a.facts.time_of_day in ("morning", "noon", "afternoon", "day") or any(ls in ("sunlight", "daylight", "direct_sun", "natural") for ls in a.facts.light_sources)):
                    is_day = True
            elif tf.enabled and any(dt.matches(a.text) for dt in all_daylight):
                is_day = True
            if is_day:
                daylight_atoms.append(a)

        if night_atoms and daylight_atoms:
            ext_night = [a for a in night_atoms if is_external_atom(a)]
            ext_day = [a for a in daylight_atoms if is_external_atom(a)]

            if ext_night and ext_day:
                n1 = ext_night[0]
                d1 = ext_day[0]
                raise PromptValidationError(
                    f"External override conflict under rule '{rule_item.id}': night environment ('{n1.text}') conflicts with daylight environment ('{d1.text}')",
                    slots=(n1.source_slot, d1.source_slot),
                    texts=(n1.text, d1.text),
                    rule_id=rule_item.id,
                )

            if ext_day and not ext_night:
                # 外部日照优先：系统夜景退让
                day_winner = ext_day[0]
                for a in night_atoms:
                    if not a.can_delete_atom or not index.is_active(a):
                        continue
                    if tf.enabled and any(nt.matches(a.text) for nt in all_night):
                        self.text_fallback_hits += 1
                    index.drop(a.atom_id)
                    ledger.record_drop(
                        rule_item.id,
                        rule_item.phase,
                        "daylight_removes_night",
                        (day_winner.atom_id,),
                        a,
                    )
            elif ext_night and not ext_day:
                # 外部夜景优先：系统日间光影退让
                night_winner = ext_night[0]
                for a in daylight_atoms:
                    if not a.can_delete_atom or not index.is_active(a):
                        continue
                    if tf.enabled and any(dt.matches(a.text) for dt in all_daylight):
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
                # 均为系统生成：按场景决定光影；若无场景主夜景，则日间光影清除偶发夜景元素
                scene_nights = [a for a in night_atoms if a.source_slot in ("scene", "scene_theme", "theme")]
                if scene_nights:
                    night_winner = min(scene_nights, key=lambda x: (x.tag_order, x.span_order))
                    for a in daylight_atoms:
                        if a.source_slot in ("lighting", "lighting_palette"):
                            if not a.can_delete_atom or not index.is_active(a):
                                continue
                            if tf.enabled and any(dt.matches(a.text) for dt in all_daylight):
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
                    daylight_winner = min(daylight_atoms, key=lambda x: (x.tag_order, x.span_order))
                    for a in night_atoms:
                        if a.source_slot in ("lighting", "lighting_palette"):
                            if not a.can_delete_atom or not index.is_active(a):
                                continue
                            if tf.enabled and any(nt.matches(a.text) for nt in all_night):
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
            if is_camera_hardware_atom(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            seen_mono_ids.add(a.atom_id)
            mono_atoms.append(a)

        for a in index.get_active_by_slots("film", "film_stock"):
            if a.atom_id in seen_mono_ids or not a.can_detect or not index.is_active(a):
                continue
            if is_camera_hardware_atom(a):
                continue
            self._validate_formal_atom(a, rule_item.id)
            is_mono = False
            if a.facts and "monochrome" in a.facts.color_modes:
                is_mono = True
            elif tf.enabled and any(t.matches(a.text) for t in monochrome_triggers):
                is_mono = True
            if is_mono:
                seen_mono_ids.add(a.atom_id)
                mono_atoms.append(a)

        if mono_atoms:
            mono_winner = min(mono_atoms, key=lambda x: (x.tag_order, x.span_order))
            chroma_atoms = []
            for a in index.get_active_by_slots("lighting", "lighting_palette", "film", "film_stock"):
                if a in mono_atoms or not a.can_detect or not index.is_active(a):
                    continue
                if is_camera_hardware_atom(a):
                    continue
                self._validate_formal_atom(a, rule_item.id)
                is_loser = False
                is_fallback = False
                if a.facts and any(c in a.facts.color_modes for c in ("color", "high_saturation", "neon")):
                    is_loser = True
                elif tf.enabled and any(b.matches(a.text) for b in banned_chroma):
                    is_loser = True
                    is_fallback = True

                if is_loser:
                    chroma_atoms.append((a, is_fallback))

            if chroma_atoms:
                ext_mono = [a for a in mono_atoms if is_external_atom(a)]
                ext_chroma = [a for a, _ in chroma_atoms if is_external_atom(a)]

                if ext_mono and ext_chroma:
                    m1 = ext_mono[0]
                    c1 = ext_chroma[0]
                    raise PromptValidationError(
                        f"External override conflict under rule '{rule_item.id}': monochrome film ('{m1.text}') conflicts with high chroma color ('{c1.text}')",
                        slots=(m1.source_slot, c1.source_slot),
                        texts=(m1.text, c1.text),
                        rule_id=rule_item.id,
                    )

                if ext_chroma and not ext_mono:
                    # 外部色彩优先：系统黑白胶片退让
                    for ma in mono_atoms:
                        if not ma.can_delete_atom or not index.is_active(ma):
                            continue
                        index.drop(ma.atom_id)
                        ledger.record_drop(
                            rule_item.id,
                            rule_item.phase,
                            "chroma_removes_monochrome_film",
                            (ext_chroma[0].atom_id,),
                            ma,
                        )
                else:
                    # 黑白胜出（外部黑白胜过系统色彩，或系统黑白胜过系统色彩）
                    for a, is_fallback in chroma_atoms:
                        if not a.can_delete_atom or not index.is_active(a):
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
            if is_formal_atom(a) or is_external_atom(a):
                if a.facts and a.facts.makeup_base in ("bare", "clean", "natural"):
                    clean_atoms.append(a)
                elif tf.enabled:
                    if any(t.matches(a.text) for t in no_makeup_triggers):
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
                if is_formal_atom(a) or is_external_atom(a):
                    if a.facts and any(e in a.facts.makeup_effects for e in ("heavy", "smudged", "runny", "smeared")):
                        is_loser = True
                    elif tf.enabled:
                        if any(b.matches(a.text) for b in banned_makeup_smudge):
                            is_loser = True
                            is_fallback = True
                elif tf.enabled:
                    if any(b.matches(a.text) for b in banned_makeup_smudge):
                        is_loser = True
                        is_fallback = True

                if is_loser:
                    if not a.can_delete_atom:
                        continue
                    if is_external_atom(a):
                        # 软偏好：外部显式输入或自定义覆盖的妆容细节 100% 原样保留，绝不因偏好删除用户输入
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
                        if is_external_atom(ang_winner) and is_external_atom(a):
                            raise PromptValidationError(
                                f"External override conflict under rule '{rule_item.id}': camera angle ('{ang_winner.text}') conflicts with gaze direction ('{a.text}')",
                                slots=(ang_winner.source_slot, a.source_slot),
                                texts=(ang_winner.text, a.text),
                                rule_id=rule_item.id,
                            )
                        if is_external_atom(a) and not is_external_atom(ang_winner):
                            if ang_winner.can_delete_atom and index.is_active(ang_winner):
                                index.drop(ang_winner.atom_id)
                                ledger.record_drop(
                                    rule_item.id,
                                    rule_item.phase,
                                    "external_gaze_overrides_camera_angle",
                                    (a.atom_id,),
                                    ang_winner,
                                )
                            continue
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
                    if is_external_atom(occ_winner) and is_external_atom(a):
                        raise PromptValidationError(
                            f"External override conflict under rule '{rule_item.id}': eye occlusion ('{occ_winner.text}') conflicts with gaze direction ('{a.text}')",
                            slots=(occ_winner.source_slot, a.source_slot),
                            texts=(occ_winner.text, a.text),
                            rule_id=rule_item.id,
                        )
                    if is_external_atom(a) and not is_external_atom(occ_winner):
                        if occ_winner.can_delete_atom and index.is_active(occ_winner):
                            index.drop(occ_winner.atom_id)
                            ledger.record_drop(
                                rule_item.id,
                                rule_item.phase,
                                "external_gaze_overrides_eye_occlusion",
                                (a.atom_id,),
                                occ_winner,
                            )
                        continue
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
                        if not a.can_delete_atom or is_external_atom(a):
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
        formal_gazes = [a for a in expression_atoms if a.can_detect and index.is_active(a) and (is_formal_atom(a) or is_external_atom(a)) and a.facts and a.facts.gaze]
        if len(formal_gazes) > 1:
            first_gaze = formal_gazes[0]
            for later_gaze in formal_gazes[1:]:
                if later_gaze.facts.gaze != first_gaze.facts.gaze:
                    if is_external_atom(first_gaze) and is_external_atom(later_gaze):
                        raise PromptValidationError(
                            f"External override conflict under rule '{rule_item.id}': slot '{first_gaze.source_slot}' ('{first_gaze.text}') conflicts with slot '{later_gaze.source_slot}' ('{later_gaze.text}')",
                            slots=(first_gaze.source_slot, later_gaze.source_slot),
                            texts=(first_gaze.text, later_gaze.text),
                            rule_id=rule_item.id,
                        )
                    if is_external_atom(later_gaze) and not is_external_atom(first_gaze):
                        if first_gaze.can_delete_atom and index.is_active(first_gaze):
                            index.drop(first_gaze.atom_id)
                            ledger.record_drop(
                                rule_item.id,
                                rule_item.phase,
                                "external_gaze_overrides_gaze",
                                (later_gaze.atom_id,),
                                first_gaze,
                            )
                        first_gaze = later_gaze
                        continue
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
                    m1_ext = any(is_external_atom(a) for a in m1)
                    m2_ext = any(is_external_atom(a) for a in m2)
                    if m1_ext and m2_ext:
                        raise PromptValidationError(
                            f"External override conflict under rule '{rule_item.id}': slot '{m1[0].source_slot}' ('{m1[0].text}') conflicts with slot '{m2[0].source_slot}' ('{m2[0].text}')",
                            slots=(m1[0].source_slot, m2[0].source_slot),
                            texts=(m1[0].text, m2[0].text),
                            rule_id=rule_item.id,
                        )
                    if m1_ext and not m2_ext:
                        winner = m1[0]
                        losers = m2
                    elif m2_ext and not m1_ext:
                        winner = m2[0]
                        losers = m1
                    else:
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
            if not index.is_active(a) or not a.can_detect or is_external_atom(a):
                continue
            self._validate_formal_atom(a, rule_item.id, index=index)

            # 1. 替换高危组合
            if is_formal_atom(a):
                if a.facts and a.facts.liquid_kind in ("sexual_fluid", "cum", "semen") and any(loc in a.facts.liquid_locations for loc in ("eyes", "closed_eyes", "face")):
                    if not a.can_modify_internal:
                        continue
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
            if not index.is_active(a) or not a.can_modify_internal or is_external_atom(a):
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
            if all(is_external_atom(a) for a in tattoo_source_atoms):
                # 外部显式或自定义覆盖的纹身标签享受完整内容保护，绝不追加任何系统增强词
                return index.get_all_active_ordered()
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
            return not loser.can_delete_atom or loser.contains_blackbox or not loser.can_modify_internal or is_external_atom(loser)


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

        # 2. 裸露 vs 服装与跨槽位等级一致性
        r2_tf = rule_tf_map["nudity_clothing_conflicts"]
        r2_l5_banned = select_fallback_patterns(r2_tf, role="banned", group_id="L5")
        nude_atoms = [a for a in detectable if a.source_slot == "nudity"]
        active_nc = getattr(self, "_active_nudity_constraint", None)
        active_lvl = (
            active_nc.effective_level
            if (active_nc is not None and not active_nc.yielded_to_override)
            else None
        )
        active_branch = active_nc.selected_branch if active_nc is not None else None
        if nude_atoms or active_lvl:
            first_nude = min(nude_atoms, key=lambda x: (x.tag_order, x.span_order)) if nude_atoms else None
            detected_lvl = active_lvl
            if not detected_lvl and first_nude is not None:
                raw_id = (
                    (first_nude.source_item_id or "")
                    + " "
                    + ((first_nude.provenance.item_id or "") if first_nude.provenance else "")
                    + " "
                    + ((first_nude.origin.selected_id or "") if first_nude.origin else "")
                ).upper()
                if is_formal_atom(first_nude) and first_nude.facts and len(first_nude.facts.allowed_nudity_levels) == 1:
                    detected_lvl = first_nude.facts.allowed_nudity_levels[0]
                else:
                    for cand_l in ("L6", "L5", "L4", "L3", "L2", "L1"):
                        if cand_l in raw_id:
                            detected_lvl = cand_l
                            break
                    if not detected_lvl and any(s in first_nude.text.lower() for s in ("completely naked", "full nude", "bare body", "no clothes")):
                        detected_lvl = "L5"
            if (
                not active_branch
                and not (active_nc is not None and active_nc.yielded_to_override)
                and first_nude is not None
                and first_nude.facts
                and len(first_nude.facts.nudity_branches) == 1
            ):
                active_branch = first_nude.facts.nudity_branches[0]

            nude_win_id = first_nude.atom_id if first_nude is not None else "__nudity_constraint__"
            if detected_lvl in ("L5", "L6"):
                clothing_atoms = [
                    a for a in detectable
                    if a.source_slot != "nudity"
                    and not _is_non_worn_discarded_narrative_atom(a)
                    and (
                        a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear")
                        or (not is_formal_atom(a) and any(b.matches(a.text) for b in r2_l5_banned))
                        or (detected_lvl == "L6" and _is_upper_body_cutoff_framing_atom(a))
                    )
                ]
                clothing_atoms = [a for a in clothing_atoms if first_nude is None or a.atom_id != first_nude.atom_id]
                if clothing_atoms:
                    first_cloth = min(clothing_atoms, key=lambda x: (x.tag_order, x.span_order))
                    if is_external_atom(first_cloth) and not (first_nude and is_external_atom(first_nude)):
                        winner, loser = first_cloth, (first_nude or first_cloth)
                    else:
                        winner, loser = (first_nude or first_cloth), first_cloth
                    conflicts.append(("residual_nudity_clothing_conflict", "nudity_clothing_conflicts", "nudity_removes_clothing", winner.atom_id, loser.atom_id))
                    if _is_loser_protected(loser):
                        protected_losers.append(loser)
            elif detected_lvl in ("L1", "L2", "L3", "L4"):
                violating_atoms = [
                    a for a in detectable
                    if a.source_slot != "nudity"
                    and not is_external_atom(a)
                    and not _is_atom_compatible_with_level_and_branch(a, detected_lvl, active_branch)
                ]
                if violating_atoms:
                    first_bad = min(violating_atoms, key=lambda x: (x.tag_order, x.span_order))
                    conflicts.append(("residual_nudity_clothing_conflict", "nudity_clothing_conflicts", "nudity_removes_clothing", nude_win_id, first_bad.atom_id))
                    if _is_loser_protected(first_bad):
                        protected_losers.append(first_bad)

        for gid in get_fallback_group_ids(r2_tf, role="trigger"):
            c_trig = select_fallback_patterns(r2_tf, role="trigger", group_id=gid)
            c_ban = select_fallback_patterns(r2_tf, role="banned", group_id=gid)
            trig_atoms = [
                a for a in detectable
                if (is_formal_atom(a) and self._is_formal_section2_nudity_trigger(a))
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
                    if is_external_atom(first_ban) and not is_external_atom(first_trig):
                        winner, loser = first_ban, first_trig
                    else:
                        winner, loser = first_trig, first_ban
                    conflicts.append(("residual_nudity_underwear_conflict", "nudity_clothing_conflicts", "nudity_removes_underwear", winner.atom_id, loser.atom_id))
                    if _is_loser_protected(loser):
                        protected_losers.append(loser)
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
                if not is_external_atom(first_lb):
                    conflicts.append(("residual_framing_lower_body_conflict", "framing_lower_body_coherence", "close_up_removes_lower_body", first_cu.atom_id, first_lb.atom_id))
                    if _is_loser_protected(first_lb):
                        protected_losers.append(first_lb)

        # 4. 姿态双手占用 vs 手持道具
        r4_tf = rule_tf_map["pose_hand_occupation"]
        r4_busy_pats = select_fallback_patterns(r4_tf, role="trigger", group_id="pose_hand")
        r4_hh_pats = select_fallback_patterns(r4_tf, role="handheld", group_id="pose_hand")
        busy_atoms = [
            a for a in detectable
            if ((is_formal_atom(a) or is_external_atom(a)) and a.facts and a.facts.hand_state == "both_busy")
            or (not is_formal_atom(a) and any(t.matches(a.text) for t in r4_busy_pats))
        ]
        prop_atoms = [
            a for a in detectable
            if ((is_formal_atom(a) or is_external_atom(a)) and a.source_slot == "props" and a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"))
            or (not is_formal_atom(a) and any(p.matches(a.text) for p in r4_hh_pats))
        ]
        if busy_atoms and prop_atoms:
            first_busy = min(busy_atoms, key=lambda x: (x.tag_order, x.span_order))
            first_prop = min(prop_atoms, key=lambda x: (x.tag_order, x.span_order))
            if first_busy.atom_id != first_prop.atom_id:
                if is_external_atom(first_prop) and not is_external_atom(first_busy):
                    winner, loser = first_prop, first_busy
                else:
                    winner, loser = first_busy, first_prop
                conflicts.append(("residual_hand_occupation_conflict", "pose_hand_occupation", "busy_hands_remove_props", winner.atom_id, loser.atom_id))
                if _is_loser_protected(loser):
                    protected_losers.append(loser)

        # 5. 单持道具上限
        r5_tf = rule_tf_map["handheld_props_single_holder"]
        r5_pats = select_fallback_patterns(r5_tf, role="handheld", group_id="handheld")
        hh_atoms = [
            a for a in detectable
            if ((is_formal_atom(a) or is_external_atom(a)) and a.source_slot == "props" and a.facts and (a.facts.hands_required > 0 or a.facts.prop_usage == "handheld"))
            or (not is_formal_atom(a) and any(hp.matches(a.text) for hp in r5_pats))
        ]
        if hh_atoms:
            prop_entities = build_prop_entities(hh_atoms)
            total_prop_hands = sum(e.hands_required for e in prop_entities)

            pose_hands = 0
            if busy_atoms:
                first_busy = min(busy_atoms, key=lambda x: (x.tag_order, x.span_order))
                if first_busy.facts:
                    if first_busy.facts.hand_state == "one_busy" or first_busy.facts.hands_required == 1:
                        pose_hands = 1
                    elif first_busy.facts.hand_state in ("both_busy", "restrained") or first_busy.facts.hands_required == 2:
                        pose_hands = 2
                    elif any(w in first_busy.text.lower() for w in ("cheek", "hip", "chin", "touching")):
                        pose_hands = 1
                    else:
                        pose_hands = 2
                else:
                    pose_hands = 2 if any(w in first_busy.text.lower() for w in ("bound", "tied", "behind back", "hands on head", "restrained")) else 1

            if len(prop_entities) > 1 and (total_prop_hands + pose_hands > 2):
                first_hh = prop_entities[0].atoms[0]
                second_hh = prop_entities[1].atoms[0]
                if is_external_atom(second_hh) and not is_external_atom(first_hh):
                    winner, loser = second_hh, first_hh
                else:
                    winner, loser = first_hh, second_hh
                conflicts.append(("residual_single_handheld_prop_conflict", "handheld_props_single_holder", "single_handheld_prop_limit", winner.atom_id, loser.atom_id))
                if _is_loser_protected(loser):
                    protected_losers.append(loser)

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
                    is_removed = (
                        a.facts
                        and "removed" in a.facts.garment_states
                        and not (
                            a.source_item_id in CANONICAL_ABSENCE_STATES
                            or (a.origin and a.origin.selected_id in CANONICAL_ABSENCE_STATES)
                            or ("underwear" in a.facts.garment_topologies)
                        )
                    )

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

        # 6b. 提示词级服装载体前提残余校验（nudity / auto_linkage / clothing_extension）
        existing_loser_ids = {c[-1] for c in conflicts}
        for bad_atom, _req_kind, worn_list in find_carrier_mismatch_in_atoms(detectable):
            if bad_atom.atom_id in existing_loser_ids:
                continue
            existing_loser_ids.add(bad_atom.atom_id)
            win_id = worn_list[0].atom_id if worn_list else "__missing_garment_carrier__"
            if bad_atom.source_slot == "nudity":
                conflicts.append(("residual_nudity_carrier_conflict", "nudity_clothing_conflicts", "clothing_removes_nudity", win_id, bad_atom.atom_id))
            else:
                conflicts.append(("residual_state_lacks_carrier_conflict", "clothing_style_state_coherence", "state_lacks_carrier", win_id, bad_atom.atom_id))
            if _is_loser_protected(bad_atom):
                protected_losers.append(bad_atom)

        # 7. 材质穿透
        r7 = self.registry.get_rule("material_penetration")
        r7_tf = rule_tf_map["material_penetration"]
        r7_banned = select_fallback_patterns(r7_tf, role="banned", group_id="material")
        sheer_atoms = [
            a for a in detectable
            if not is_external_atom(a)
            and not (a.source_slot == "clothing_extension" or (a.provenance and a.provenance.kind == "clothing_extension"))
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
            if a.source_slot in ("film", "film_stock") and not is_camera_hardware_atom(a) and (
                (is_formal_atom(a) and a.facts and "monochrome" in a.facts.color_modes)
                or (not is_formal_atom(a) and any(t.matches(a.text) for t in r10_mono_pats))
            )
        ]
        chroma_atoms = [
            a for a in detectable
            if a.source_slot in ("lighting", "lighting_palette", "film", "film_stock") and not is_camera_hardware_atom(a) and (
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
                # 软偏好：当包含外部输入时，允许共存，不属于不可消解的硬冲突
                if not (is_external_atom(first_bare) or is_external_atom(first_heavy)):
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
