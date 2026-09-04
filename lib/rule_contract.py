"""
rule_contract.py — 冲突消解引擎 17 大规则的强类型权威契约 (Python SSOT)

声明式契约驱动：
1. 单源驱动自动导出 Draft-7 JSON Schema (export_json_schema)；
2. 单源驱动运行时深度解析与 Fail-Closed 强校验 (parse_rule_document)；
3. 严格执行 allowed_keys 校验 (等价于 additionalProperties: false)；
4. 产出 17 个强类型、不可变 (frozen) 规则规格对象；
5. 统一 PatternSpec 编译、匹配与替换引擎。
"""
from __future__ import annotations

from collections import defaultdict

import dataclasses
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple

if __package__:
    from .errors import RuleConfigurationError
    from .slot_contract import ALLOWED_SLOTS, SLOT_ALIASES
else:
    from lib.errors import RuleConfigurationError
    from lib.slot_contract import ALLOWED_SLOTS, SLOT_ALIASES


VALID_PROVENANCE_KINDS: Tuple[str, ...] = (
    "base_clothing",
    "clothing_state",
    "clothing_extension",
    "user_input",
    "resolver_generated",
    "scene_anchor",
    "scene_detail",
    "quality_default",
)


STABLE_RULE_ORDER: Tuple[str, ...] = (
    "spatial_environmental_mutual_exclusion",
    "nudity_clothing_conflicts",
    "material_penetration",
    "clothing_style_state_coherence",
    "gaze_angle_geometry",
    "gaze_mutual_exclusion",
    "accessory_occlusion_gaze_coherence",
    "framing_lower_body_coherence",
    "liquid_restrictions",
    "device_quality_compatibility",
    "tattoo_dermal_fusion",
    "pose_hand_occupation",
    "handheld_props_single_holder",
    "emotion_gaze_affinity",
    "environmental_lighting_coherence",
    "monochrome_film_chroma_coherence",
    "makeup_details_coherence",
)

MatchMode = Literal["exact", "word", "phrase", "regex"]
VALID_MATCH_MODES: Tuple[str, ...] = ("exact", "word", "phrase", "regex")

VALID_PHASES: Tuple[str, ...] = ("anchors", "physical", "semantic", "effects")
PHASE_EXECUTION_ORDER: Dict[str, int] = {
    "anchors": 1,
    "physical": 2,
    "semantic": 3,
    "effects": 4,
}

FROZEN_DAG_METADATA: Dict[str, Dict[str, Any]] = {
    "spatial_environmental_mutual_exclusion": {"phase": "anchors", "priority": 100, "depends_on": ()},
    "nudity_clothing_conflicts": {"phase": "anchors", "priority": 110, "depends_on": ()},
    "framing_lower_body_coherence": {"phase": "anchors", "priority": 120, "depends_on": ()},
    "pose_hand_occupation": {"phase": "physical", "priority": 200, "depends_on": ()},
    "handheld_props_single_holder": {"phase": "physical", "priority": 210, "depends_on": ("pose_hand_occupation",)},
    "clothing_style_state_coherence": {"phase": "physical", "priority": 220, "depends_on": ("nudity_clothing_conflicts",)},
    "material_penetration": {"phase": "physical", "priority": 230, "depends_on": ("clothing_style_state_coherence",)},
    "device_quality_compatibility": {"phase": "physical", "priority": 240, "depends_on": ()},
    "environmental_lighting_coherence": {"phase": "physical", "priority": 250, "depends_on": ("spatial_environmental_mutual_exclusion",)},
    "monochrome_film_chroma_coherence": {"phase": "physical", "priority": 260, "depends_on": ()},
    "makeup_details_coherence": {"phase": "physical", "priority": 270, "depends_on": ()},
    "gaze_angle_geometry": {"phase": "semantic", "priority": 300, "depends_on": ("framing_lower_body_coherence",)},
    "accessory_occlusion_gaze_coherence": {"phase": "semantic", "priority": 310, "depends_on": ("gaze_angle_geometry",)},
    "emotion_gaze_affinity": {"phase": "semantic", "priority": 320, "depends_on": ("accessory_occlusion_gaze_coherence",)},
    "gaze_mutual_exclusion": {
        "phase": "semantic",
        "priority": 330,
        "depends_on": (
            "gaze_angle_geometry",
            "accessory_occlusion_gaze_coherence",
            "emotion_gaze_affinity",
        ),
    },
    "liquid_restrictions": {"phase": "effects", "priority": 400, "depends_on": ("nudity_clothing_conflicts",)},
    "tattoo_dermal_fusion": {"phase": "effects", "priority": 410, "depends_on": ()},
}

DAG_FROZEN_ORDER: Tuple[str, ...] = (
    "spatial_environmental_mutual_exclusion",
    "nudity_clothing_conflicts",
    "framing_lower_body_coherence",
    "pose_hand_occupation",
    "handheld_props_single_holder",
    "clothing_style_state_coherence",
    "material_penetration",
    "device_quality_compatibility",
    "environmental_lighting_coherence",
    "monochrome_film_chroma_coherence",
    "makeup_details_coherence",
    "gaze_angle_geometry",
    "accessory_occlusion_gaze_coherence",
    "emotion_gaze_affinity",
    "gaze_mutual_exclusion",
    "liquid_restrictions",
    "tattoo_dermal_fusion",
)



VALID_PATTERN_ROLES: Tuple[str, ...] = (
    "trigger",
    "banned",
    "indoor",
    "outdoor",
    "venue",
    "device",
    "handheld",
    "emotion",
    "angle",
    "liquid",
    "modifier",
    "tattoo",
    "exclusive_a",
    "exclusive_b",
)

def _freeze_contract_data(val: Any) -> Any:
    """递归将嵌套字典和序列转为深度只读不可变映射代理与元组 (P2)。"""
    if isinstance(val, dict):
        return MappingProxyType({k: _freeze_contract_data(v) for k, v in val.items()})
    elif isinstance(val, (list, tuple)):
        return tuple(_freeze_contract_data(x) for x in val)
    return val

FROZEN_RULE_GROUPS: Mapping[str, Mapping[str, Any]] = _freeze_contract_data({
    "spatial_environmental_mutual_exclusion": {
        "is_single_group": False,
        "allowed_groups": ("bedroom", "dining", "indoor", "office", "onsen", "outdoor", "school", "transport"),
        "group_required_roles": {
            "indoor": ("indoor",),
            "outdoor": ("outdoor",),
            "bedroom": ("venue",),
            "dining": ("venue",),
            "office": ("venue",),
            "onsen": ("venue",),
            "school": ("venue",),
            "transport": ("venue",),
        },
        "role_cardinality": {
            ("indoor", "indoor"): (1, 100),
            ("outdoor", "outdoor"): (1, 100),
            ("bedroom", "venue"): (1, 100),
            ("dining", "venue"): (1, 100),
            ("office", "venue"): (1, 100),
            ("onsen", "venue"): (1, 100),
            ("school", "venue"): (1, 100),
            ("transport", "venue"): (1, 100),
        },
    },
    "nudity_clothing_conflicts": {
        "is_single_group": False,
        "allowed_groups": ("L1", "L2", "L3", "L4", "L5", "L6", "conflict_0", "conflict_1", "conflict_2", "conflict_3"),
        "group_required_roles": {
            "L1": ("banned",),
            "L2": ("banned",),
            "L3": ("banned",),
            "L4": ("banned",),
            "L5": ("banned",),
            "L6": ("banned",),
            "conflict_0": ("trigger", "banned"),
            "conflict_1": ("trigger", "banned"),
            "conflict_2": ("trigger", "banned"),
            "conflict_3": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("L1", "banned"): (1, 100),
            ("L2", "banned"): (1, 50),
            ("L3", "banned"): (1, 50),
            ("L4", "banned"): (1, 50),
            ("L5", "banned"): (1, 50),
            ("L6", "banned"): (1, 50),
            ("conflict_0", "trigger"): (1, 50),
            ("conflict_0", "banned"): (1, 50),
            ("conflict_1", "trigger"): (1, 50),
            ("conflict_1", "banned"): (1, 50),
            ("conflict_2", "trigger"): (1, 50),
            ("conflict_2", "banned"): (1, 50),
            ("conflict_3", "trigger"): (1, 50),
            ("conflict_3", "banned"): (1, 50),
        },
    },
    "material_penetration": {
        "is_single_group": True,
        "allowed_groups": ("material",),
        "group_required_roles": {
            "material": ("banned",),
        },
        "role_cardinality": {
            ("material", "banned"): (1, 50),
        },
    },
    "clothing_style_state_coherence": {
        "is_single_group": False,
        "allowed_groups": ("one_piece", "pants"),
        "group_required_roles": {
            "one_piece": ("trigger", "banned"),
            "pants": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("one_piece", "trigger"): (1, 50),
            ("one_piece", "banned"): (1, 50),
            ("pants", "trigger"): (1, 50),
            ("pants", "banned"): (1, 50),
        },
    },
    "gaze_angle_geometry": {
        "is_single_group": False,
        "allowed_groups": ("high_angle", "low_angle", "pov"),
        "group_required_roles": {
            "high_angle": ("angle", "banned"),
            "low_angle": ("angle", "banned"),
            "pov": ("angle",),
        },
        "role_cardinality": {
            ("high_angle", "angle"): (1, 50),
            ("high_angle", "banned"): (1, 50),
            ("low_angle", "angle"): (1, 50),
            ("low_angle", "banned"): (1, 50),
            ("pov", "angle"): (1, 50),
        },
    },
    "gaze_mutual_exclusion": {
        "is_single_group": False,
        "allowed_groups": ("pair_0", "pair_1", "pair_2"),
        "group_required_roles": {
            "pair_0": ("exclusive_a", "exclusive_b"),
            "pair_1": ("exclusive_a", "exclusive_b"),
            "pair_2": ("exclusive_a", "exclusive_b"),
        },
        "role_cardinality": {
            ("pair_0", "exclusive_a"): (1, 10),
            ("pair_0", "exclusive_b"): (1, 10),
            ("pair_1", "exclusive_a"): (1, 10),
            ("pair_1", "exclusive_b"): (1, 10),
            ("pair_2", "exclusive_a"): (1, 10),
            ("pair_2", "exclusive_b"): (1, 10),
        },
    },
    "accessory_occlusion_gaze_coherence": {
        "is_single_group": True,
        "allowed_groups": ("occlusion",),
        "group_required_roles": {
            "occlusion": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("occlusion", "trigger"): (1, 50),
            ("occlusion", "banned"): (1, 50),
        },
    },
    "framing_lower_body_coherence": {
        "is_single_group": True,
        "allowed_groups": ("framing",),
        "group_required_roles": {
            "framing": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("framing", "trigger"): (1, 50),
            ("framing", "banned"): (1, 50),
        },
    },
    "liquid_restrictions": {
        "is_single_group": False,
        "allowed_groups": ("cum_eyes", "liquid_words", "opaque_paint", "pussy_juice"),
        "group_required_roles": {
            "cum_eyes": ("trigger",),
            "opaque_paint": ("trigger",),
            "pussy_juice": ("trigger",),
            "liquid_words": ("liquid",),
        },
        "role_cardinality": {
            ("cum_eyes", "trigger"): (1, 50),
            ("opaque_paint", "trigger"): (1, 50),
            ("pussy_juice", "trigger"): (1, 50),
            ("liquid_words", "liquid"): (1, 50),
        },
    },
    "device_quality_compatibility": {
        "is_single_group": False,
        "allowed_groups": ("analog_film", "cctv", "phone", "webcam"),
        "group_required_roles": {
            "analog_film": ("device", "banned"),
            "cctv": ("device", "banned"),
            "phone": ("device", "banned"),
            "webcam": ("device", "banned"),
        },
        "role_cardinality": {
            ("analog_film", "device"): (1, 50),
            ("analog_film", "banned"): (1, 50),
            ("cctv", "device"): (1, 50),
            ("cctv", "banned"): (1, 50),
            ("phone", "device"): (1, 50),
            ("phone", "banned"): (1, 50),
            ("webcam", "device"): (1, 50),
            ("webcam", "banned"): (1, 50),
        },
    },
    "tattoo_dermal_fusion": {
        "is_single_group": True,
        "allowed_groups": ("tattoo",),
        "group_required_roles": {
            "tattoo": ("tattoo",),
        },
        "role_cardinality": {
            ("tattoo", "tattoo"): (1, 50),
        },
    },
    "pose_hand_occupation": {
        "is_single_group": True,
        "allowed_groups": ("pose_hand",),
        "group_required_roles": {
            "pose_hand": ("trigger", "handheld"),
        },
        "role_cardinality": {
            ("pose_hand", "trigger"): (1, 100),
            ("pose_hand", "handheld"): (1, 100),
        },
    },
    "handheld_props_single_holder": {
        "is_single_group": True,
        "allowed_groups": ("handheld",),
        "group_required_roles": {
            "handheld": ("handheld",),
        },
        "role_cardinality": {
            ("handheld", "handheld"): (1, 50),
        },
    },
    "emotion_gaze_affinity": {
        "is_single_group": False,
        "allowed_groups": ("bored", "shy"),
        "group_required_roles": {
            "shy": ("emotion", "banned"),
            "bored": ("emotion", "banned"),
        },
        "role_cardinality": {
            ("shy", "emotion"): (1, 50),
            ("shy", "banned"): (1, 50),
            ("bored", "emotion"): (1, 50),
            ("bored", "banned"): (1, 50),
        },
    },
    "environmental_lighting_coherence": {
        "is_single_group": True,
        "allowed_groups": ("daylight",),
        "group_required_roles": {
            "daylight": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("daylight", "trigger"): (1, 50),
            ("daylight", "banned"): (1, 50),
        },
    },
    "monochrome_film_chroma_coherence": {
        "is_single_group": True,
        "allowed_groups": ("monochrome",),
        "group_required_roles": {
            "monochrome": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("monochrome", "trigger"): (1, 50),
            ("monochrome", "banned"): (1, 50),
        },
    },
    "makeup_details_coherence": {
        "is_single_group": True,
        "allowed_groups": ("no_makeup",),
        "group_required_roles": {
            "no_makeup": ("trigger", "banned"),
        },
        "role_cardinality": {
            ("no_makeup", "trigger"): (1, 50),
            ("no_makeup", "banned"): (1, 50),
        },
    },
})



@dataclass(frozen=True)
class PatternSpec:
    """单一模式匹配强类型规范：统一编译、匹配与替换。"""
    pattern: str
    match_mode: MatchMode
    role: Optional[str] = None
    group_id: Optional[str] = None
    _compiled: Optional[re.Pattern] = dataclasses.field(default=None, init=False, repr=False, compare=False)

    def compile(self) -> re.Pattern:
        if self._compiled is not None:
            return self._compiled
        if self.match_mode == "exact":
            p = re.escape(self.pattern.strip(" ,"))
            c = re.compile(rf"^\s*{p}\s*$", re.IGNORECASE)
        elif self.match_mode == "word":
            p = re.escape(self.pattern.strip())
            c = re.compile(rf"\b{p}\b", re.IGNORECASE)
        elif self.match_mode == "phrase":
            p = re.escape(self.pattern.strip())
            p_pattern = re.sub(r"\\\s+", r"\\s+", p)
            c = re.compile(rf"(?:\b|^){p_pattern}(?:\b|$)", re.IGNORECASE)
        elif self.match_mode == "regex":
            c = re.compile(self.pattern, re.IGNORECASE)
        else:
            raise RuleConfigurationError(f"Unknown match mode: {self.match_mode}")
        object.__setattr__(self, "_compiled", c)
        return c

    def matches(self, text: str) -> bool:
        if not self.pattern or not text:
            return False
        if self.match_mode == "exact":
            return self.pattern.strip(" ,").lower() == text.strip(" ,").lower()
        return bool(self.compile().search(text))

    def substitute(self, text: str, repl: str) -> str:
        """统一搜索与替换实现 (2.1 契约规范)。"""
        if not text:
            return text
        if self.match_mode == "exact":
            if self.matches(text):
                return repl
            return text
        return self.compile().sub(repl, text)


def parse_pattern_spec(
    data: Any,
    context: str = "",
    require_role: bool = False,
    require_group_id: bool = False,
) -> PatternSpec:
    """严格解析并校验单个 PatternSpec 对象。"""
    if not isinstance(data, dict):
        raise RuleConfigurationError(
            f"Pattern spec must be a dictionary with 'pattern' and 'match_mode', got {type(data).__name__} in {context}"
        )

    allowed_keys = {"pattern", "match_mode", "role", "group_id"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        raise RuleConfigurationError(
            f"Unknown fields {extra_keys} in pattern spec in {context}"
        )

    pattern = data.get("pattern")
    match_mode = data.get("match_mode")
    role = data.get("role")
    group_id = data.get("group_id")

    if require_role and role is None:
        raise RuleConfigurationError(f"Missing required field 'role' in pattern spec in {context}")

    if role is not None:
        if not isinstance(role, str) or not role.strip():
            raise RuleConfigurationError(f"Pattern role must be a non-empty string in {context}")
        if role.strip() not in VALID_PATTERN_ROLES:
            raise RuleConfigurationError(
                f"Invalid pattern role {role!r} in {context}. Allowed roles: {VALID_PATTERN_ROLES}"
            )

    if require_group_id and group_id is None:
        raise RuleConfigurationError(f"Missing required field 'group_id' in pattern spec in {context}")

    if group_id is not None:
        if not isinstance(group_id, str) or not group_id.strip():
            raise RuleConfigurationError(f"Pattern group_id must be a non-empty string in {context}")

    if not isinstance(pattern, str) or not pattern or not any(not c.isspace() for c in pattern):
        raise RuleConfigurationError(f"Pattern must be a non-empty, non-whitespace string in {context}")

    if match_mode not in VALID_MATCH_MODES:
        raise RuleConfigurationError(
            f"Invalid match_mode {match_mode!r} in {context}. Allowed modes: {VALID_MATCH_MODES}"
        )

    if match_mode == "word":
        if any(c.isspace() for c in pattern) or "-" in pattern or "/" in pattern:
            raise RuleConfigurationError(
                f"Multi-word pattern {pattern!r} cannot use match_mode 'word' in {context}; use 'phrase' instead."
            )

    if match_mode == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            raise RuleConfigurationError(f"Invalid regex {pattern!r} in {context}: {e}") from e

    return PatternSpec(pattern=pattern.strip(), match_mode=match_mode, role=role.strip() if role else None, group_id=group_id.strip() if group_id else None)


# ─── 强类型子规格对象 ───

@dataclass(frozen=True)
class ReplacementSpec:
    banned: PatternSpec
    replacement: str


@dataclass(frozen=True)
class LevelRuleSpec:
    name_zh: str
    banned_patterns: Tuple[PatternSpec, ...]


@dataclass(frozen=True)
class TriggerBanConflictSpec:
    trigger: Tuple[PatternSpec, ...]
    ban: Tuple[PatternSpec, ...]


@dataclass(frozen=True)
class AngleGazeMappingSpec:
    angles: Tuple[PatternSpec, ...]
    banned_gaze: Tuple[PatternSpec, ...]


@dataclass(frozen=True)
class BannedComboSpec:
    triggers: Tuple[PatternSpec, ...]
    replace: str


@dataclass(frozen=True)
class DeviceConstraintSpec:
    devices: Tuple[PatternSpec, ...]
    banned_tags: Tuple[PatternSpec, ...]


@dataclass(frozen=True)
class EmotionGazeConflictSpec:
    catalog_emotion_triggers: Tuple[PatternSpec, ...]
    custom_emotion_triggers: Tuple[PatternSpec, ...]
    catalog_banned_gaze: Tuple[PatternSpec, ...]
    custom_banned_gaze: Tuple[PatternSpec, ...]

    @property
    def emotion_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_emotion_triggers + self.custom_emotion_triggers

    @property
    def banned_gaze(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_gaze + self.custom_banned_gaze

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


# ─── 规则规格基类与通用字典访问协议 ───

class RuleSpecMixin:
    """提供只读字典兼容访问协议：spec['field'], spec.get('field'), 'field' in spec。"""
    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)



@dataclass(frozen=True)
class SemanticConstraintSpec:
    domain: str
    winner: str = ""
    loser: Optional[str] = None
    target_slots: Tuple[str, ...] = ()
    fact_fields: Tuple[str, ...] = ()
    protect: Optional[str] = None


@dataclass(frozen=True)
class TextFallbackSpec:
    strategy: str = "pattern_match"
    enabled: bool = True
    target_slots: Tuple[str, ...] = ()
    patterns: Tuple[PatternSpec, ...] = ()


@dataclass(frozen=True)
class SpatialEnvironmentalRuleSpec(RuleSpecMixin):
    id: str
    description: str
    venue_clusters: Mapping[str, Tuple[PatternSpec, ...]]
    outdoor_exclusive: Tuple[PatternSpec, ...]
    indoor_exclusive: Tuple[PatternSpec, ...]
    deprecated_tags: Tuple[ReplacementSpec, ...] = ()
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class NudityClothingRuleSpec(RuleSpecMixin):
    id: str
    description: str
    level_rules: Mapping[str, LevelRuleSpec]
    conflicts: Tuple[TriggerBanConflictSpec, ...] = ()
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class MaterialPenetrationRuleSpec(RuleSpecMixin):
    id: str
    description: str
    banned_words: Tuple[PatternSpec, ...]
    replacements: Tuple[str, ...]
    target_slots: Tuple[str, ...]
    target_provenance_kinds: Tuple[str, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class ClothingStyleStateRuleSpec(RuleSpecMixin):
    id: str
    description: str
    one_piece_triggers: Tuple[PatternSpec, ...]
    one_piece_banned_states: Tuple[PatternSpec, ...]
    pants_triggers: Tuple[PatternSpec, ...] = ()
    pants_banned_states: Tuple[PatternSpec, ...] = ()
    name_zh: Optional[str] = None
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class GazeAngleGeometryRuleSpec(RuleSpecMixin):
    id: str
    description: str
    mappings: Tuple[AngleGazeMappingSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class GazeMutualExclusionRuleSpec(RuleSpecMixin):
    id: str
    description: str
    exclusive_pairs: Tuple[Tuple[PatternSpec, PatternSpec], ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class AccessoryOcclusionGazeRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_occlusion_triggers: Tuple[PatternSpec, ...]
    custom_occlusion_triggers: Tuple[PatternSpec, ...]
    catalog_banned_gaze_actions: Tuple[PatternSpec, ...]
    custom_banned_gaze_actions: Tuple[PatternSpec, ...]
    name_zh: Optional[str] = None
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


    @property
    def occlusion_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_occlusion_triggers + self.custom_occlusion_triggers

    @property
    def banned_gaze_actions(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_gaze_actions + self.custom_banned_gaze_actions


@dataclass(frozen=True)
class FramingLowerBodyRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_close_up_triggers: Tuple[PatternSpec, ...]
    custom_close_up_triggers: Tuple[PatternSpec, ...]
    catalog_banned_lower_body: Tuple[PatternSpec, ...]
    custom_banned_lower_body: Tuple[PatternSpec, ...]
    name_zh: Optional[str] = None
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


    @property
    def close_up_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_close_up_triggers + self.custom_close_up_triggers

    @property
    def banned_lower_body(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_lower_body + self.custom_banned_lower_body


@dataclass(frozen=True)
class LiquidRestrictionsRuleSpec(RuleSpecMixin):
    id: str
    description: str
    liquid_words: Tuple[PatternSpec, ...]
    modifiers: Tuple[str, ...]
    banned_combos: Tuple[BannedComboSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class DeviceQualityRuleSpec(RuleSpecMixin):
    id: str
    description: str
    device_constraints: Tuple[DeviceConstraintSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class TattooDermalFusionRuleSpec(RuleSpecMixin):
    id: str
    description: str
    tattoo_indicators: Tuple[PatternSpec, ...]
    fusion_tags: Tuple[str, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class PoseHandOccupationRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_busy_pose_triggers: Tuple[PatternSpec, ...]
    custom_busy_pose_triggers: Tuple[PatternSpec, ...]
    catalog_handheld_patterns: Tuple[PatternSpec, ...]
    custom_handheld_patterns: Tuple[PatternSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


    @property
    def busy_pose_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_busy_pose_triggers + self.custom_busy_pose_triggers

    @property
    def banned_handheld_patterns(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_handheld_patterns + self.custom_handheld_patterns


@dataclass(frozen=True)
class HandheldPropsRuleSpec(RuleSpecMixin):
    id: str
    description: str
    handheld_patterns: Tuple[PatternSpec, ...]
    name_zh: Optional[str] = None
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class EmotionGazeAffinityRuleSpec(RuleSpecMixin):
    id: str
    description: str
    conflicts: Tuple[EmotionGazeConflictSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class EnvironmentalLightingRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_daylight_triggers: Tuple[PatternSpec, ...]
    custom_daylight_triggers: Tuple[PatternSpec, ...]
    catalog_banned_night_elements: Tuple[PatternSpec, ...]
    custom_banned_night_elements: Tuple[PatternSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


    @property
    def daylight_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_daylight_triggers + self.custom_daylight_triggers

    @property
    def banned_night_elements(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_night_elements + self.custom_banned_night_elements


@dataclass(frozen=True)
class MonochromeFilmChromaRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_monochrome_triggers: Tuple[PatternSpec, ...]
    custom_monochrome_triggers: Tuple[PatternSpec, ...]
    catalog_banned_chroma: Tuple[PatternSpec, ...]
    custom_banned_chroma: Tuple[PatternSpec, ...]
    name_zh: Optional[str] = None
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


    @property
    def monochrome_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_monochrome_triggers + self.custom_monochrome_triggers

    @property
    def banned_chroma(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_chroma + self.custom_banned_chroma


@dataclass(frozen=True)
class MakeupDetailsRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_no_makeup_triggers: Tuple[PatternSpec, ...]
    custom_no_makeup_triggers: Tuple[PatternSpec, ...]
    catalog_banned_makeup_smudge: Tuple[PatternSpec, ...]
    custom_banned_makeup_smudge: Tuple[PatternSpec, ...]
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


    @property
    def no_makeup_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_no_makeup_triggers + self.custom_no_makeup_triggers

    @property
    def banned_makeup_smudge(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_makeup_smudge + self.custom_banned_makeup_smudge


# ─── 统一声明式字段描述符体系 ───

def _pattern_spec_schema(
    allow_regex: bool = True,
    require_role: bool = False,
    require_group_id: bool = False,
    allowed_groups: Optional[Sequence[str]] = None,
    allowed_roles: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    modes = list(VALID_MATCH_MODES) if allow_regex else ["exact", "word", "phrase"]
    required = ["pattern", "match_mode"]
    if require_role:
        required.append("role")
    if require_group_id:
        required.append("group_id")
    role_schema = (
        {"type": "string", "enum": sorted(list(allowed_roles))}
        if allowed_roles
        else {"type": "string", "enum": list(VALID_PATTERN_ROLES)}
    )
    group_id_schema = (
        {"type": "string", "enum": sorted(list(allowed_groups))}
        if allowed_groups
        else {"type": "string", "minLength": 1}
    )
    return {
        "type": "object",
        "required": required,
        "additionalProperties": False,
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "pattern": r"\S",
            },
            "match_mode": {"type": "string", "enum": modes},
            "role": role_schema,
            "group_id": group_id_schema,
        },
        "allOf": [
            {
                "if": {
                    "properties": {"match_mode": {"const": "word"}}
                },
                "then": {
                    "properties": {
                        "pattern": {"pattern": r"^[^\s\-/]+$"}
                    }
                }
            },
            {
                "if": {
                    "properties": {"match_mode": {"const": "regex"}}
                },
                "then": {
                    "properties": {
                        "pattern": {"format": "regex"}
                    }
                }
            }
        ],
    }


def _pattern_array_schema(min_items: int = 1) -> Dict[str, Any]:
    return {
        "type": "array",
        "minItems": min_items,
        "items": _pattern_spec_schema(),
    }


class FieldDesc:
    def __init__(self, name: str, is_runtime: bool, required: bool = True, default: Any = None):
        self.name = name
        self.is_runtime = is_runtime
        self.required = required
        self.default = default

    def to_json_schema(self) -> Dict[str, Any]:
        raise NotImplementedError

    def parse(self, val: Any, context: str) -> Any:
        raise NotImplementedError


RULE_ALLOWED_SLOTS: Set[str] = set(ALLOWED_SLOTS) | set(SLOT_ALIASES.keys()) | {
    "underwear", "clothing_state", "clothing_extension", "camera", "angle", "shot", "view", "liquid"
}


class TargetSlotsField(FieldDesc):
    def __init__(self, name: str = "target_slots", required: bool = True):
        super().__init__(name, is_runtime=True, required=required)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string",
                "enum": sorted(list(RULE_ALLOWED_SLOTS)),
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[str, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"Field '{self.name}' must be a non-empty list in {context}")
        out = []
        for x in val:
            if not isinstance(x, str) or not x.strip():
                raise RuleConfigurationError(f"Items in '{self.name}' must be non-empty strings in {context}")
            if x not in RULE_ALLOWED_SLOTS:
                raise RuleConfigurationError(
                    f"Invalid slot {x!r} in '{self.name}' for {context}. Allowed slots: {sorted(list(RULE_ALLOWED_SLOTS))}"
                )
            out.append(x)
        return tuple(out)


class TargetProvenanceKindsField(FieldDesc):
    def __init__(self, name: str = "target_provenance_kinds", required: bool = True):
        super().__init__(name, is_runtime=True, required=required)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string",
                "enum": list(VALID_PROVENANCE_KINDS),
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[str, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"Field '{self.name}' must be a non-empty list in {context}")
        out = []
        for x in val:
            if not isinstance(x, str) or not x.strip():
                raise RuleConfigurationError(f"Items in '{self.name}' must be non-empty strings in {context}")
            if x not in VALID_PROVENANCE_KINDS:
                raise RuleConfigurationError(
                    f"Invalid provenance kind {x!r} in '{self.name}' for {context}. Allowed: {VALID_PROVENANCE_KINDS}"
                )
            out.append(x)
        return tuple(out)


class IntegerField(FieldDesc):
    def __init__(self, name: str, min_value: int = 1, is_runtime: bool = True, required: bool = True, default: Any = None):
        super().__init__(name, is_runtime=is_runtime, required=required, default=default)
        self.min_value = min_value

    def to_json_schema(self) -> Dict[str, Any]:
        return {"type": "integer", "minimum": self.min_value}

    def parse(self, val: Any, context: str) -> int:
        if not isinstance(val, int) or isinstance(val, bool) or val < self.min_value:
            raise RuleConfigurationError(f"Field {self.name!r} must be an integer >= {self.min_value} in {context}, got {val!r}")
        return val


class EnumField(FieldDesc):
    def __init__(self, name: str, allowed_values: Sequence[str], is_runtime: bool = True, required: bool = True, default: Any = None):
        super().__init__(name, is_runtime=is_runtime, required=required, default=default)
        self.allowed_values = tuple(allowed_values)

    def to_json_schema(self) -> Dict[str, Any]:
        return {"type": "string", "enum": list(self.allowed_values)}

    def parse(self, val: Any, context: str) -> str:
        if val not in self.allowed_values:
            raise RuleConfigurationError(f"Field {self.name!r} must be in {self.allowed_values} in {context}, got {val!r}")
        return val


FROZEN_RULE_SEMANTICS: Dict[str, Dict[str, Any]] = {
    'spatial_environmental_mutual_exclusion': {
        'expected_domain': 'spatial',
        'expected_winner': 'scene_anchor',
        'expected_loser': 'scene_detail',
        'expected_target_slots': ('scene',),
        'expected_fact_fields': ('space_kind', 'venue_ids'),
        'allowed_reason_codes': ('indoor_outdoor_mutex', 'venue_cluster_mutex', 'deprecated_tag_replaced'),
        'fallback_target_slots': ('scene',),
        'fallback_patterns': (
            ('spinning room', 'phrase'),
            ('onsen', 'word'),
            ('hot spring', 'phrase'),
            ('rotenburo', 'word'),
            ('ryokan bath', 'phrase'),
            ('public bath', 'phrase'),
            ('sento', 'word'),
            ('sauna', 'word'),
            ('jacuzzi', 'word'),
            ('soapland bath', 'phrase'),
            ('cafe booth', 'phrase'),
            ('coffee shop', 'phrase'),
            ('yatai stall', 'phrase'),
            ('street food cart', 'phrase'),
            ('ramen shop', 'phrase'),
            ('izakaya', 'word'),
            ('bar counter', 'phrase'),
            ('love hotel restaurant', 'phrase'),
            ('food stall with curtain', 'phrase'),
            ('classroom', 'word'),
            ('blackboard', 'word'),
            ('student desk', 'phrase'),
            ('teacher desk', 'phrase'),
            ('school library', 'phrase'),
            ('gym storage', 'phrase'),
            ('infirmary', 'word'),
            ('office cubicle', 'phrase'),
            ('conference room', 'phrase'),
            ('executive desk', 'phrase'),
            ('office elevator', 'phrase'),
            ('break room', 'phrase'),
            ('corporate office', 'phrase'),
            ('subway car', 'phrase'),
            ('train seat', 'phrase'),
            ('train door', 'phrase'),
            ('train interior', 'phrase'),
            ('airplane cabin', 'phrase'),
            ('car backseat', 'phrase'),
            ('bus interior', 'phrase'),
            ('shinkansen', 'word'),
            ('riverbank', 'word'),
            ('embankment', 'word'),
            ('behind bushes', 'phrase'),
            ('under bridge', 'phrase'),
            ('park at night', 'phrase'),
            ('beach at night', 'phrase'),
            ('forest clearing', 'phrase'),
            ('mountain trail', 'phrase'),
            ('seaside cave', 'phrase'),
            ('bedroom', 'word'),
            ('love hotel room', 'phrase'),
            ('tatami futon', 'phrase'),
            ('messy bed', 'phrase'),
            ('hotel room bed', 'phrase'),
            ('outdoor bath', 'phrase'),
            ('open-air bath', 'phrase'),
            ('onsen with snow view', 'phrase'),
            ('snow view', 'phrase'),
            ('outdoor hot spring', 'phrase'),
            ('indoor onsen', 'phrase'),
            ('private onsen', 'phrase'),
            ('onsen changing room', 'phrase'),
            ('changing room', 'phrase'),
            ('locker room', 'phrase'),
            ('bathroom', 'word'),
            ('shower room', 'phrase'),
            ('living room', 'phrase'),
            ('kitchen', 'word'),
            ('elevator', 'word'),
            ('dressing room', 'phrase'),
            ('shower stall', 'phrase'),
        ),
    },
    'nudity_clothing_conflicts': {
        'expected_domain': 'nudity_clothing',
        'expected_winner': 'nudity_level',
        'expected_loser': 'clothing_coverage',
        'expected_target_slots': ('nudity', 'clothing', 'underwear'),
        'expected_fact_fields': ('visible_regions', 'garment_topologies', 'garment_states'),
        'allowed_reason_codes': ('nudity_removes_clothing', 'nudity_removes_underwear'),
        'fallback_target_slots': ('nudity', 'clothing', 'underwear'),
        'fallback_patterns': (
            ('pussy visible', 'phrase'),
            ('exposed vagina', 'phrase'),
            ('spread pussy', 'phrase'),
            ('bare pussy', 'phrase'),
            ('panties showing', 'phrase'),
            ('visible panties', 'phrase'),
            ('wearing panties', 'phrase'),
            ('wearing underwear', 'phrase'),
            ('lace panties on', 'phrase'),
            ('topless', 'word'),
            ('bare breasts', 'phrase'),
            ('exposed breasts', 'phrase'),
            ('uncovered breasts', 'phrase'),
            ('wearing bra', 'phrase'),
            ('bra on', 'phrase'),
            ('wearing blouse', 'phrase'),
            ('wearing shirt', 'phrase'),
            ('completely naked', 'phrase'),
            ('full nude', 'phrase'),
            ('bare body', 'phrase'),
            ('no clothes', 'phrase'),
            ('wearing blazer', 'phrase'),
            ('wearing skirt', 'phrase'),
            ('wearing uniform', 'phrase'),
            ('wearing dress', 'phrase'),
            ('wearing sweater', 'phrase'),
            ('no panties', 'phrase'),
            ('cameltoe', 'word'),
            ('skirt pulled up', 'phrase'),
            ('skirt hiked up', 'phrase'),
            ('skirt lifted', 'phrase'),
            ('skirt riding up', 'phrase'),
            ('dress hitched up', 'phrase'),
            ('dress pulled up', 'phrase'),
            ('revealing panties', 'phrase'),
            ('showing panties', 'phrase'),
            ('panties visible', 'phrase'),
            ('panties pulled', 'phrase'),
            ('pulling panties', 'phrase'),
            ('upskirt', 'word'),
            ('flashing skirt', 'phrase'),
            ('stepping out of skirt', 'phrase'),
            ('skirt pooled at feet', 'phrase'),
            ('lifting skirt', 'phrase'),
            ('panties only', 'phrase'),
            ('only panties', 'phrase'),
            ('only lace panties', 'phrase'),
            ('only underwear', 'phrase'),
            ('bra visible', 'phrase'),
            ('bra slipping', 'phrase'),
            ('bra removed', 'phrase'),
            ('bra pushed up', 'phrase'),
            ('bra pulled down', 'phrase'),
            ('adjusting bra', 'phrase'),
            ('unclasped bra', 'phrase'),
            ('lace bra', 'phrase'),
            ('thong', 'word'),
            ('bare chest', 'phrase'),
            ('breasts bare', 'phrase'),
            ('chest exposed', 'phrase'),
            ('cleavage', 'word'),
            ('underboob', 'word'),
            ('sideboob', 'word'),
            ('nipple', 'word'),
            ('pussy', 'word'),
            ('vagina', 'word'),
            ('genitals', 'word'),
            ('labia', 'word'),
            ('crotch close-up', 'phrase'),
            ('spread legs', 'phrase'),
            ('spreading labia', 'phrase'),
            ('thighs spread', 'phrase'),
            ('thighs bare', 'phrase'),
            ('cock sliding', 'phrase'),
            ('penetrated', 'word'),
            ('face-fucked', 'phrase'),
            ('cum dripping down thigh', 'phrase'),
            ('dripping on breasts', 'phrase'),
            ('cupping breasts', 'phrase'),
            ('hands on breasts', 'phrase'),
            ('gripping breasts', 'phrase'),
            ('breasts bouncing', 'phrase'),
            ('nude', 'word'),
            ('naked', 'word'),
            ('fully nude', 'phrase'),
            ('full frontal nudity', 'phrase'),
            ('all clothes removed', 'phrase'),
            ('stripped bare', 'phrase'),
            ('unclothed', 'word'),
            ('birthday suit', 'phrase'),
            ('nude photo', 'phrase'),
            ('nude outline', 'phrase'),
            ('nude silhouette', 'phrase'),
            ('painterly nude', 'phrase'),
            ('half-lit nude', 'phrase'),
            ('high contrast nude', 'phrase'),
            ('flattering nude lighting', 'phrase'),
            ('angelic nude lighting', 'phrase'),
            ('unbuttoned', 'word'),
            ('blouse open', 'phrase'),
            ('shirt open', 'phrase'),
            ('slipping off shoulder', 'phrase'),
            ('dress slipping off', 'phrase'),
            ('panties', 'word'),
            ('matching lace panties', 'phrase'),
            ('underwear', 'word'),
            ('shirt open showing bare breasts', 'phrase'),
            ('bra pushed up above breasts', 'phrase'),
            ('skirt pulled up revealing panties', 'phrase'),
            ('skirt hiked up to waist', 'phrase'),
            ('pulling panties down', 'phrase'),
            ('spread labia', 'phrase'),
            ('vaginal opening', 'phrase'),
            ('wearing coat', 'phrase'),
            ('wearing jacket', 'phrase'),
            ('fully clothed', 'phrase'),
            ('neatly dressed', 'phrase'),
            ('formal kimono', 'phrase'),
            ('buttoned-up blouse', 'phrase'),
            ('vaginal opening exposed', 'phrase'),
            ('wearing pantyhose', 'phrase'),
            ('crisp clothing', 'phrase'),
            ('modest outfit', 'phrase'),
        ),
    },
    'framing_lower_body_coherence': {
        'expected_domain': 'framing_lower_body',
        'expected_winner': 'shot_type',
        'expected_loser': 'lower_body_elements',
        'expected_target_slots': ('shot_type', 'clothing'),
        'expected_fact_fields': ('visible_regions',),
        'allowed_reason_codes': ('close_up_removes_lower_body',),
        'fallback_target_slots': ('shot_type', 'clothing'),
        'fallback_patterns': (
            ('extreme close-up', 'phrase'),
            ('macro detail shot', 'phrase'),
            ('close-up', 'phrase'),
            ('face shot filling frame', 'phrase'),
            ('focused on facial expression', 'phrase'),
            ('portrait close-up', 'phrase'),
            ('tight headshot', 'phrase'),
            ('macro shot of lips', 'phrase'),
            ('eyes close-up', 'phrase'),
            ('facial macro shot', 'phrase'),
            ('garter straps', 'phrase'),
            ('garter belt', 'phrase'),
            ('high heels', 'phrase'),
            ('thigh-high stockings', 'phrase'),
            ('garter_stockings', 'word'),
            ('bare feet', 'phrase'),
            ('stiletto heels', 'phrase'),
            ('knee-high boots', 'phrase'),
            ('strappy sandals', 'phrase'),
            ('feet visible', 'phrase'),
            ('kneeling on tatami', 'phrase'),
        ),
    },
    'pose_hand_occupation': {
        'expected_domain': 'hand_occupation',
        'expected_winner': 'pose_hand_state',
        'expected_loser': 'handheld_props',
        'expected_target_slots': ('pose', 'props'),
        'expected_fact_fields': ('hand_state', 'hands_required', 'prop_usage'),
        'allowed_reason_codes': ('busy_hands_remove_props',),
        'fallback_target_slots': ('pose', 'props'),
        'fallback_patterns': (
            ('hands behind back', 'phrase'),
            ('arms above head', 'phrase'),
            ('lying with arms above head', 'phrase'),
            ('hands behind head', 'phrase'),
            ('gripping sheets', 'phrase'),
            ('pulling shirt over head', 'phrase'),
            ('on all fours', 'phrase'),
            ('on hands and knees', 'phrase'),
            ('spreading labia', 'phrase'),
            ('hands clasped', 'phrase'),
            ('spreading labia with both hands', 'phrase'),
            ('hands clasped behind back', 'phrase'),
            ('hands clasped in prayer', 'phrase'),
            ('hands tied behind back', 'phrase'),
            ('hands bound', 'phrase'),
            ('arms raised high above head', 'phrase'),
            ('hands on head', 'phrase'),
            ('gripping bedsheet', 'phrase'),
            ('clutching pillow with both hands', 'phrase'),
            ('unhooking bra behind back', 'phrase'),
            ('pulling panties down with both hands', 'phrase'),
            ('hands on floor', 'phrase'),
            ('crawling on floor', 'phrase'),
            ('holding legs open', 'phrase'),
            ('arms wrapped around neck', 'phrase'),
            ('hands braced on chest', 'phrase'),
            ('covering eyes with both hands', 'phrase'),
            ('hands over mouth to silence', 'phrase'),
            ('compact camera in hand', 'phrase'),
            ('arms wrapped around cute stuffed animal', 'phrase'),
            ('holding black compact digital camera', 'phrase'),
            ('smartphone in hand recording', 'phrase'),
            ('holding game controller', 'phrase'),
            ('holding embroidered round silk fan', 'phrase'),
            ('holding oiled paper umbrella propped on shoulder', 'phrase'),
            ('holding lush fresh floral bouquet', 'phrase'),
            ('hugging large fluffy plush teddy bear', 'phrase'),
            ('holding smartphone', 'phrase'),
            ('phone held in hand', 'phrase'),
            ('holding camera', 'phrase'),
            ('holding folding fan', 'phrase'),
            ('holding round fan', 'phrase'),
            ('holding fan in hand', 'phrase'),
            ('holding oil paper umbrella', 'phrase'),
            ('holding umbrella in hand', 'phrase'),
            ('holding flower bouquet', 'phrase'),
            ('holding wine glass in hand', 'phrase'),
            ('holding wine glass', 'phrase'),
            ('holding champagne glass', 'phrase'),
            ('wine glass in hand', 'phrase'),
            ('swirling glass in hand', 'phrase'),
            ('holding wand vibrator', 'phrase'),
            ('holding controller', 'phrase'),
            ('holding tea cup', 'phrase'),
            ('holding cigarette', 'phrase'),
            ('cigarette between fingers', 'phrase'),
            ('holding sword', 'phrase'),
            ('holding microphone', 'phrase'),
            ('holding tray', 'phrase'),
        ),
    },
    'handheld_props_single_holder': {
        'expected_domain': 'handheld_props',
        'expected_winner': 'first_handheld_prop',
        'expected_loser': 'subsequent_handheld_props',
        'expected_target_slots': ('props',),
        'expected_fact_fields': ('hands_required', 'prop_usage'),
        'allowed_reason_codes': ('single_handheld_prop_limit',),
        'fallback_target_slots': ('props',),
        'fallback_patterns': (
            ('holding smartphone', 'phrase'),
            ('holding camera', 'phrase'),
            ('holding folding fan', 'phrase'),
            ('holding round silk fan', 'phrase'),
            ('holding round fan', 'phrase'),
            ('holding umbrella', 'phrase'),
            ('holding bouquet', 'phrase'),
            ('holding wine glass', 'phrase'),
            ('holding champagne glass', 'phrase'),
            ('holding tea cup', 'phrase'),
            ('holding sword', 'phrase'),
            ('holding wand vibrator', 'phrase'),
            ('holding microphone', 'phrase'),
            ('holding tray', 'phrase'),
            ('holding game controller', 'phrase'),
            ('holding black compact digital camera', 'phrase'),
            ('holding oiled paper umbrella', 'phrase'),
        ),
    },
    'clothing_style_state_coherence': {
        'expected_domain': 'clothing_structure',
        'expected_winner': 'garment_topology',
        'expected_loser': 'garment_state_action',
        'expected_target_slots': ('clothing',),
        'expected_fact_fields': ('garment_topologies', 'garment_states'),
        'allowed_reason_codes': ('one_piece_state_conflict', 'pants_state_conflict'),
        'fallback_target_slots': ('clothing',),
        'fallback_patterns': (
            ('one-piece swimsuit', 'phrase'),
            ('school swimsuit (sukumizu)', 'phrase'),
            ('sukumizu', 'word'),
            ('competition swimsuit', 'phrase'),
            ('leotard', 'word'),
            ('bodysuit', 'word'),
            ('bodystocking', 'word'),
            ('unbuttoned dress shirt', 'phrase'),
            ('unbuttoned blouse', 'phrase'),
            ('unbuttoning shirt', 'phrase'),
            ('skirt lifted', 'phrase'),
            ('skirt hiked up', 'phrase'),
            ('skirt slit revealing', 'phrase'),
            ('lifting pleated skirt', 'phrase'),
            ('unzipped jeans', 'phrase'),
            ('unzipping pants', 'phrase'),
            ('button undone', 'phrase'),
            ('skinny jeans', 'phrase'),
            ('denim jeans', 'phrase'),
            ('leather pants', 'phrase'),
            ('cargo pants', 'phrase'),
            ('tailored trousers', 'phrase'),
            ('denim shorts', 'phrase'),
            ('hot pants', 'phrase'),
            ('lifting skirt', 'phrase'),
            ('skirt hiked up to waist', 'phrase'),
            ('pleated skirt floating', 'phrase'),
            ('skirt blown by wind', 'phrase'),
        ),
    },
    'material_penetration': {
        'expected_domain': 'material_penetration',
        'expected_protect': 'clothing_extension',
        'expected_target_slots': ('clothing', 'clothing_state'),
        'expected_fact_fields': ('garment_states', 'garment_topologies'),
        'allowed_reason_codes': ('material_penetration_removed', 'material_penetration_replaced'),
        'fallback_target_slots': ('clothing', 'clothing_state'),
        'fallback_patterns': (
            ('sheer', 'word'),
            ('see-through', 'phrase'),
            ('transparent fabric', 'phrase'),
            ('see through', 'phrase'),
            ('sheer fabric', 'phrase'),
            ('translucent dress', 'phrase'),
        ),
    },
    'device_quality_compatibility': {
        'expected_domain': 'device_quality',
        'expected_winner': 'capture_device',
        'expected_loser': 'quality_modifier',
        'expected_target_slots': ('shot_type', 'quality'),
        'expected_fact_fields': ('capture_device', 'quality_class'),
        'allowed_reason_codes': ('device_removes_conflicting_quality',),
        'fallback_target_slots': ('shot_type', 'quality'),
        'fallback_patterns': (
            ('cctv', 'word'),
            ('surveillance', 'word'),
            ('security camera', 'phrase'),
            ('masterpiece', 'word'),
            ('8k', 'word'),
            ('ultra detailed', 'phrase'),
            ('professional photography', 'phrase'),
            ('studio lighting', 'phrase'),
            ('bokeh background', 'phrase'),
            ('film grain', 'phrase'),
            ('phone camera', 'phrase'),
            ('selfie', 'word'),
            ('iphone photo', 'phrase'),
            ('smartphone', 'word'),
            ('85mm lens', 'phrase'),
            ('dslr photo', 'phrase'),
            ('full frame camera', 'phrase'),
            ('studio softbox', 'phrase'),
            ('medium format', 'phrase'),
            ('35mm film', 'phrase'),
            ('analog camera', 'phrase'),
            ('film photography', 'phrase'),
            ('ultrahd', 'word'),
            ('digital rendering', 'phrase'),
            ('cgi', 'word'),
            ('webcam', 'word'),
            ('hidden spy camera', 'phrase'),
            ('pinhole camera', 'phrase'),
            ('sharp focus', 'phrase'),
        ),
    },
    'environmental_lighting_coherence': {
        'expected_domain': 'day_night',
        'expected_winner': 'scene_day_night',
        'expected_loser': 'light_source',
        'expected_target_slots': ('scene', 'lighting'),
        'expected_fact_fields': ('time_of_day', 'light_sources'),
        'allowed_reason_codes': ('night_scene_removes_daylight', 'daylight_removes_night'),
        'fallback_target_slots': ('scene', 'lighting'),
        'fallback_patterns': (
            ('soft sunlight filtered through sheer curtains', 'phrase'),
            ('dappled sunlight filtering through tree canopy', 'phrase'),
            ('golden hour', 'phrase'),
            ('natural daylight', 'phrase'),
            ('morning sunlight', 'phrase'),
            ('golden hour sunset sidelight', 'phrase'),
            ('hotel balcony night', 'phrase'),
            ('park at night', 'phrase'),
            ('beach at night', 'phrase'),
            ('hotel corridor late night', 'phrase'),
            ('nightclub', 'word'),
            ('back alley', 'phrase'),
            ('deep dark night', 'phrase'),
            ('pitch black background', 'phrase'),
        ),
    },
    'monochrome_film_chroma_coherence': {
        'expected_domain': 'color_mode',
        'expected_winner': 'film_color_mode',
        'expected_loser': 'chroma_effects',
        'expected_target_slots': ('film', 'lighting'),
        'expected_fact_fields': ('color_modes',),
        'allowed_reason_codes': ('monochrome_film_removes_chroma',),
        'fallback_target_slots': ('film', 'lighting'),
        'fallback_patterns': (
            ('high contrast B&W', 'phrase'),
            ('fine grain B&W', 'phrase'),
            ('classic monochrome', 'phrase'),
            ('professional monochrome', 'phrase'),
            ('rich tonal B&W', 'phrase'),
            ('cinematic B&W', 'phrase'),
            ('warm brown monochrome', 'phrase'),
            ('kodak tri-x 400', 'phrase'),
            ('ilford hp5 plus', 'phrase'),
            ('fujifilm acros 100', 'phrase'),
            ('black and white film', 'phrase'),
            ('monochrome photography', 'phrase'),
            ('b&w film stock', 'phrase'),
            ('neon rim light', 'phrase'),
            ('neon reflection on skin', 'phrase'),
            ('magenta and cyan', 'phrase'),
            ('love hotel neon glow', 'phrase'),
            ('sunset warmth', 'phrase'),
            ('vibrant neon glow', 'phrase'),
            ('neon rim lighting', 'phrase'),
            ('cyan and magenta lighting', 'phrase'),
            ('rainbow prism flares', 'phrase'),
            ('colorful reflections', 'phrase'),
            ('sunset orange warmth', 'phrase'),
            ('vibrant neon cyan and magenta', 'phrase'),
            ('cyberpunk neon glow', 'phrase'),
            ('bright saturated colors', 'phrase'),
            ('pastel candy palette', 'phrase'),
            ('electric purple rim light', 'phrase'),
        ),
    },
    'makeup_details_coherence': {
        'expected_domain': 'makeup',
        'expected_winner': 'base_makeup',
        'expected_loser': 'smudged_effects',
        'expected_target_slots': ('makeup',),
        'expected_fact_fields': ('makeup_base', 'makeup_effects'),
        'allowed_reason_codes': ('clean_base_removes_heavy_makeup',),
        'fallback_target_slots': ('makeup',),
        'fallback_patterns': (
            ('natural makeup', 'phrase'),
            ('pure face', 'phrase'),
            ('clean beauty', 'phrase'),
            ('low-saturation clear glass skin', 'phrase'),
            ('minimal makeup', 'phrase'),
            ('smeared lipstick on cheek', 'phrase'),
            ('smudged eyeliner', 'phrase'),
            ('smudged kohl eyeliner', 'phrase'),
            ('ruined makeup', 'phrase'),
        ),
    },
    'gaze_angle_geometry': {
        'expected_domain': 'gaze_geometry',
        'expected_winner': 'camera_angle',
        'expected_loser': 'impossible_gaze',
        'expected_target_slots': ('camera', 'camera_angle', 'angle', 'shot', 'view', 'expression'),
        'expected_fact_fields': ('gaze',),
        'allowed_reason_codes': ('camera_angle_removes_impossible_gaze',),
        'fallback_target_slots': ('camera', 'camera_angle', 'angle', 'shot', 'view', 'expression'),
        'fallback_patterns': (
            ('low angle', 'phrase'),
            ('from below', 'phrase'),
            ('worm eye view', 'phrase'),
            ('looking up from below', 'phrase'),
            ('looking up at camera', 'phrase'),
            ('looking up', 'phrase'),
            ('high angle', 'phrase'),
            ('from above', 'phrase'),
            ('overhead', 'word'),
            ('bird eye view', 'phrase'),
            ('top-down', 'phrase'),
            ('looking down at camera', 'phrase'),
            ('looking down', 'phrase'),
            ('point of view', 'phrase'),
            ('pov', 'word'),
            ('selfie', 'word'),
        ),
    },
    'accessory_occlusion_gaze_coherence': {
        'expected_domain': 'occlusion_gaze',
        'expected_winner': 'eye_occlusion',
        'expected_loser': 'gaze_action',
        'expected_target_slots': ('jewelry', 'expression'),
        'expected_fact_fields': ('occlusion', 'gaze'),
        'allowed_reason_codes': ('eye_occlusion_removes_gaze',),
        'fallback_target_slots': ('jewelry', 'expression'),
        'fallback_patterns': (
            ('black lace blindfold covering eyes', 'phrase'),
            ('sheer patterned eye mask', 'phrase'),
            ('blindfold', 'word'),
            ('eyes closed in ecstasy', 'phrase'),
            ('sleeping with eyes closed', 'phrase'),
            ('eyes blindfolded', 'phrase'),
            ('silk blindfold', 'phrase'),
            ('covering eyes with hands', 'phrase'),
            ('covering eyes with both hands', 'phrase'),
            ('hands over eyes', 'phrase'),
            ('making eye contact with camera then breaking away shyly', 'phrase'),
            ('eye-fucking the viewer', 'phrase'),
            ('predatory inviting gaze', 'phrase'),
            ('devouring hungry stare', 'phrase'),
            ('challenging dominant gaze', 'phrase'),
            ('lustful stare', 'phrase'),
            ('winking', 'word'),
            ('direct eye contact', 'phrase'),
            ('direct eye contact with camera', 'phrase'),
            ('looking at viewer', 'phrase'),
            ('looking up at camera', 'phrase'),
            ('looking down at camera', 'phrase'),
            ('playful wink', 'phrase'),
            ('staring into lens', 'phrase'),
            ('dilated pupils', 'phrase'),
            ('sparkling eyes', 'phrase'),
            ('intense eye contact', 'phrase'),
        ),
    },
    'emotion_gaze_affinity': {
        'expected_domain': 'persona_emotion_gaze',
        'expected_winner': 'emotion',
        'expected_loser': 'gaze_action',
        'expected_target_slots': ('expression',),
        'expected_fact_fields': ('emotion', 'gaze'),
        'allowed_reason_codes': ('emotion_removes_conflicting_gaze',),
        'fallback_target_slots': ('expression',),
        'fallback_patterns': (
            ('shy expression', 'phrase'),
            ('blushing cheeks', 'phrase'),
            ('shy smile', 'phrase'),
            ('timid look', 'phrase'),
            ('blushing shyly', 'phrase'),
            ('bashful expression', 'phrase'),
            ('seductive smile', 'phrase'),
            ('sultry gaze', 'phrase'),
            ('predatory inviting gaze', 'phrase'),
            ('eye-fucking', 'phrase'),
            ('challenging dominant gaze', 'phrase'),
            ('winking', 'word'),
            ('winking cheekily', 'phrase'),
            ('direct eye-contact', 'phrase'),
            ('direct eye contact', 'phrase'),
            ('predatory gaze', 'phrase'),
            ('bold seductive stare', 'phrase'),
            ('bored expression', 'phrase'),
            ('deadpan face', 'phrase'),
            ('cold aloof expression', 'phrase'),
            ('emotionless face', 'phrase'),
            ('winking playfully', 'phrase'),
            ('playful wink', 'phrase'),
            ('sweet smile', 'phrase'),
            ('gleeful eyes', 'phrase'),
            ('flirty wink', 'phrase'),
        ),
    },
    'gaze_mutual_exclusion': {
        'expected_domain': 'gaze_direction',
        'expected_winner': 'first_gaze_action',
        'expected_loser': 'conflicting_gaze_action',
        'expected_target_slots': ('expression',),
        'expected_fact_fields': ('gaze',),
        'allowed_reason_codes': ('gaze_mutual_exclusion_preserved_first',),
        'fallback_target_slots': ('expression',),
        'fallback_patterns': (
            ('direct eye contact', 'phrase'),
            ('looking away', 'phrase'),
            ('looking at camera', 'phrase'),
            ('eyes averted', 'phrase'),
            ('locking eyes', 'phrase'),
            ('looking away shyly', 'phrase'),
        ),
    },
    'liquid_restrictions': {
        'expected_domain': 'liquid_bounds',
        'expected_target_slots': ('liquids', 'liquid'),
        'expected_fact_fields': ('liquid_kind', 'liquid_locations', 'liquid_amount'),
        'allowed_reason_codes': ('liquid_combo_replaced', 'liquid_quantifier_added'),
        'fallback_target_slots': ('liquids', 'liquid'),
        'fallback_patterns': (
            ('cum', 'word'),
            ('semen', 'word'),
            ('saliva', 'word'),
            ('drool', 'word'),
            ('pussy juice', 'phrase'),
            ('breast milk', 'phrase'),
            ('fluid', 'word'),
            ('sweat', 'word'),
            ('cum on closed eyes', 'phrase'),
            ('semen in eyes', 'phrase'),
            ('pure white paint-like cum', 'phrase'),
            ('thick opaque white paint', 'phrase'),
            ('milky opaque pussy juice', 'phrase'),
        ),
    },
    'tattoo_dermal_fusion': {
        'expected_domain': 'tattoo_fusion',
        'expected_target_slots': ('tattoo',),
        'expected_fact_fields': (),
        'allowed_reason_codes': ('tattoo_dermal_fusion_injected',),
        'fallback_target_slots': ('tattoo',),
        'fallback_patterns': (
            ('tattoo', 'word'),
            ('tattooed', 'word'),
            ('ink', 'word'),
            ('irezumi', 'word'),
            ('tally marks', 'phrase'),
            ('body art', 'phrase'),
        ),
    },
}

RULE_EXPECTED_DOMAINS: Dict[str, str] = {
    k: v["expected_domain"] for k, v in FROZEN_RULE_SEMANTICS.items()
}


class SemanticConstraintsField(FieldDesc):
    def __init__(self, rule_id: str, name: str = "semantic_constraints", required: bool = True):
        super().__init__(name, is_runtime=True, required=required)
        self.rule_id = rule_id

    def to_json_schema(self) -> Dict[str, Any]:
        expected_domain = RULE_EXPECTED_DOMAINS.get(self.rule_id)
        domain_prop = {"type": "string", "enum": [expected_domain]} if expected_domain else {"type": "string"}
        fsem = FROZEN_RULE_SEMANTICS.get(self.rule_id, {})
        required = ["domain", "target_slots", "fact_fields"]
        if fsem.get("expected_winner"):
            required.append("winner")
        if fsem.get("expected_loser"):
            required.append("loser")
        if fsem.get("expected_protect"):
            required.append("protect")
        return {
            "type": "object",
            "required": required,
            "additionalProperties": False,
            "properties": {
                "domain": domain_prop,
                "winner": {"type": "string", "minLength": 1},
                "loser": {"type": "string", "minLength": 1},
                "target_slots": {"type": "array", "items": {"type": "string", "enum": sorted(list(RULE_ALLOWED_SLOTS))}},
                "fact_fields": {"type": "array", "items": {"type": "string"}},
                "protect": {"type": "string", "minLength": 1},
            },
        }

    def parse(self, val: Any, context: str) -> SemanticConstraintSpec:
        if not isinstance(val, dict):
            raise RuleConfigurationError(f"semantic_constraints must be a dict in {context}")
        allowed_keys = {"domain", "winner", "loser", "target_slots", "fact_fields", "protect"}
        extra = set(val.keys()) - allowed_keys
        if extra:
            raise RuleConfigurationError(f"Unknown fields in semantic_constraints {extra} in {context}")

        fsem = FROZEN_RULE_SEMANTICS.get(self.rule_id, {})
        domain = val.get("domain")
        expected_domain = fsem.get("expected_domain")
        if not domain or domain != expected_domain:
            raise RuleConfigurationError(
                f"Expected domain '{expected_domain}' for rule '{self.rule_id}', got '{domain}' in {context}"
            )

        winner = val.get("winner", "")
        expected_winner = fsem.get("expected_winner")
        if expected_winner is not None and winner != expected_winner:
            raise RuleConfigurationError(
                f"Expected winner '{expected_winner}' for rule '{self.rule_id}', got '{winner}' in {context}"
            )

        loser = val.get("loser")
        expected_loser = fsem.get("expected_loser")
        if expected_loser is not None and loser != expected_loser:
            raise RuleConfigurationError(
                f"Expected loser '{expected_loser}' for rule '{self.rule_id}', got '{loser}' in {context}"
            )

        protect = val.get("protect")
        expected_protect = fsem.get("expected_protect")
        if expected_protect is not None and protect != expected_protect:
            raise RuleConfigurationError(
                f"Expected protect '{expected_protect}' for rule '{self.rule_id}', got '{protect}' in {context}"
            )

        if "target_slots" not in val:
            raise RuleConfigurationError(f"Missing required field 'target_slots' in semantic_constraints for rule '{self.rule_id}' in {context}")
        target_slots = val["target_slots"]
        if not isinstance(target_slots, (list, tuple)):
            raise RuleConfigurationError(f"target_slots must be a list in {context}")
        if len(target_slots) != len(set(target_slots)):
            raise RuleConfigurationError(f"Duplicate slots in target_slots for rule '{self.rule_id}' in {context}")
        expected_target_slots = set(fsem.get("expected_target_slots", ()))
        if set(target_slots) != expected_target_slots:
            raise RuleConfigurationError(
                f"target_slots for rule '{self.rule_id}' must strictly equal {sorted(list(expected_target_slots))}, got {sorted(list(target_slots))} in {context}"
            )
        for s in target_slots:
            if s not in RULE_ALLOWED_SLOTS:
                raise RuleConfigurationError(f"Unknown slot '{s}' in target_slots in {context}")

        if "fact_fields" not in val:
            raise RuleConfigurationError(f"Missing required field 'fact_fields' in semantic_constraints for rule '{self.rule_id}' in {context}")
        fact_fields = val["fact_fields"]
        if not isinstance(fact_fields, (list, tuple)):
            raise RuleConfigurationError(f"fact_fields must be a list in {context}")
        if len(fact_fields) != len(set(fact_fields)):
            raise RuleConfigurationError(f"Duplicate fact fields in fact_fields for rule '{self.rule_id}' in {context}")
        expected_fact_fields = set(fsem.get("expected_fact_fields", ()))
        if set(fact_fields) != expected_fact_fields:
            raise RuleConfigurationError(
                f"fact_fields for rule '{self.rule_id}' must strictly equal {sorted(list(expected_fact_fields))}, got {sorted(list(fact_fields))} in {context}"
            )
        if __package__:
            from .models import SemanticFacts
        else:
            from lib.models import SemanticFacts
        known_facts = set(SemanticFacts.__dataclass_fields__.keys())
        for ff in fact_fields:
            if ff not in known_facts:
                raise RuleConfigurationError(f"Unknown fact field '{ff}' in {context}")

        return SemanticConstraintSpec(
            domain=domain,
            winner=winner,
            loser=loser,
            target_slots=tuple(target_slots),
            fact_fields=tuple(fact_fields),
            protect=protect,
        )


class TextFallbackField(FieldDesc):
    def __init__(self, rule_id: str, name: str = "text_fallback", required: bool = True):
        super().__init__(name, is_runtime=True, required=required)
        self.rule_id = rule_id

    def to_json_schema(self) -> Dict[str, Any]:
        group_spec = FROZEN_RULE_GROUPS.get(self.rule_id, {})
        allowed_groups = sorted(list(group_spec.get("allowed_groups", ())))
        all_roles = set()
        for roles in group_spec.get("group_required_roles", {}).values():
            all_roles.update(roles)
        allowed_roles = sorted(list(all_roles))

        pattern_items_schema = _pattern_spec_schema(
            allow_regex=True,
            require_role=True,
            require_group_id=True,
            allowed_groups=allowed_groups if allowed_groups else None,
            allowed_roles=allowed_roles if allowed_roles else None,
        )

        return {
            "type": "object",
            "required": ["strategy", "enabled", "target_slots", "patterns"],
            "additionalProperties": False,
            "properties": {
                "strategy": {"type": "string", "enum": ["pattern_match"]},
                "enabled": {"type": "boolean"},
                "target_slots": {"type": "array", "items": {"type": "string", "enum": sorted(list(RULE_ALLOWED_SLOTS))}},
                "patterns": {
                    "type": "array",
                    "items": pattern_items_schema,
                    "uniqueItems": True,
                },
            },
        }

    def parse(self, val: Any, context: str) -> TextFallbackSpec:
        if not isinstance(val, dict):
            raise RuleConfigurationError(f"text_fallback must be a dict in {context}")
        allowed_keys = {"strategy", "enabled", "target_slots", "patterns"}
        extra = set(val.keys()) - allowed_keys
        if extra:
            raise RuleConfigurationError(f"Unknown fields in text_fallback {extra} in {context}")

        strategy = val.get("strategy")
        if strategy != "pattern_match":
            raise RuleConfigurationError(
                f"Unsupported text_fallback strategy '{strategy}' in rule '{self.rule_id}'. Only 'pattern_match' is supported."
            )

        enabled = val.get("enabled")
        if not isinstance(enabled, bool) or not enabled:
            raise RuleConfigurationError(f"enabled in text_fallback must be true in {context}")

        fsem = FROZEN_RULE_SEMANTICS.get(self.rule_id, {})
        if "target_slots" not in val:
            raise RuleConfigurationError(f"Missing required field 'target_slots' in text_fallback for rule '{self.rule_id}' in {context}")
        target_slots = val["target_slots"]
        if not isinstance(target_slots, (list, tuple)):
            raise RuleConfigurationError(f"target_slots in text_fallback must be a list in {context}")
        if len(target_slots) != len(set(target_slots)):
            raise RuleConfigurationError(f"Duplicate slots in text_fallback target_slots for rule '{self.rule_id}' in {context}")
        allowed_slots = set(fsem.get("fallback_target_slots", fsem.get("expected_target_slots", ())))
        if set(target_slots) != allowed_slots:
            raise RuleConfigurationError(
                f"text_fallback target_slots for rule '{self.rule_id}' must strictly equal {sorted(list(allowed_slots))}, got {sorted(list(target_slots))} in {context}"
            )
        for s in target_slots:
            if s not in RULE_ALLOWED_SLOTS:
                raise RuleConfigurationError(f"Unknown slot '{s}' in text_fallback in {context}")

        if "patterns" not in val:
            raise RuleConfigurationError(f"Missing required field 'patterns' in text_fallback for rule '{self.rule_id}' in {context}")
        patterns = val["patterns"]
        if not isinstance(patterns, (list, tuple)):
            raise RuleConfigurationError(f"patterns in text_fallback must be a list in {context}")
        parsed_patterns = tuple(
            parse_pattern_spec(
                p,
                context=f"text_fallback patterns in rule '{self.rule_id}'",
                require_role=True,
                require_group_id=True,
            )
            for p in patterns
        )

        seen_pattern_keys = set()
        for p in parsed_patterns:
            key = (p.pattern, p.match_mode, p.role, p.group_id)
            if key in seen_pattern_keys:
                raise RuleConfigurationError(
                    f"Duplicate fallback pattern declaration: (pattern={p.pattern!r}, match_mode={p.match_mode!r}, role={p.role!r}, group_id={p.group_id!r}) in rule '{self.rule_id}' in {context}"
                )
            seen_pattern_keys.add(key)

        group_spec = FROZEN_RULE_GROUPS.get(self.rule_id)
        if group_spec is not None:
            allowed_groups = set(group_spec["allowed_groups"])
            group_required_roles = group_spec["group_required_roles"]
            role_cardinality = group_spec.get("role_cardinality", {})

            # 1. 逐个 pattern 验证未知 group_id、非法 role 组合 (Fail-Closed: 拒绝拼写错误与非法组合)
            groups_seen: Dict[str, Set[str]] = defaultdict(set)
            group_role_counts: Dict[Tuple[str, str], int] = defaultdict(int)
            for p in parsed_patterns:
                if p.group_id not in allowed_groups:
                    raise RuleConfigurationError(
                        f"Unknown or invalid group_id '{p.group_id}' in rule '{self.rule_id}' in {context}. Allowed groups: {sorted(list(allowed_groups))}"
                    )
                allowed_roles_for_group = set(group_required_roles.get(p.group_id, ()))
                if p.role not in allowed_roles_for_group:
                    raise RuleConfigurationError(
                        f"Invalid role '{p.role}' for group '{p.group_id}' in rule '{self.rule_id}' in {context}. Allowed roles: {sorted(list(allowed_roles_for_group))}"
                    )
                groups_seen[p.group_id].add(p.role)
                group_role_counts[(p.group_id, p.role)] += 1

            # 2. 验证是否缺少必需的分组 (Fail-Closed: 孤立分组或单侧缺失)
            missing_groups = allowed_groups - set(groups_seen.keys())
            if missing_groups:
                raise RuleConfigurationError(
                    f"Rule '{self.rule_id}' missing required groups: {sorted(list(missing_groups))} in {context}"
                )

            # 3. 验证每组内部成对 role 完整性 (Fail-Closed: 缺失成对 role)
            for gid, roles_present in groups_seen.items():
                required_roles = set(group_required_roles.get(gid, ()))
                missing_roles = required_roles - roles_present
                if missing_roles:
                    raise RuleConfigurationError(
                        f"Group '{gid}' in rule '{self.rule_id}' missing required paired roles: {sorted(list(missing_roles))} in {context}"
                    )

            # 4. 验证基数 (cardinality)
            for (gid, role), (min_c, max_c) in role_cardinality.items():
                cnt = group_role_counts.get((gid, role), 0)
                if cnt < min_c:
                    raise RuleConfigurationError(
                        f"Rule '{self.rule_id}' group '{gid}' role '{role}' cardinality underflow: got {cnt} < min {min_c}"
                    )
                if cnt > max_c:
                    raise RuleConfigurationError(
                        f"Rule '{self.rule_id}' group '{gid}' role '{role}' cardinality overflow: got {cnt} > max {max_c}"
                    )

        return TextFallbackSpec(
            strategy=strategy,
            enabled=enabled,
            target_slots=tuple(target_slots),
            patterns=parsed_patterns,
        )


class ReasonCodesField(FieldDesc):
    def __init__(self, rule_id: str, name: str = "reason_codes", min_items: int = 1, required: bool = True):
        super().__init__(name, is_runtime=True, required=required)
        self.rule_id = rule_id
        self.min_items = min_items

    def to_json_schema(self) -> Dict[str, Any]:
        return {"type": "array", "minItems": self.min_items, "items": {"type": "string", "minLength": 1}}

    def parse(self, val: Any, context: str) -> Tuple[str, ...]:
        if not isinstance(val, list):
            raise RuleConfigurationError(f"Field {self.name!r} must be a list in {context}, got {type(val).__name__}")
        if len(val) < self.min_items:
            raise RuleConfigurationError(f"Field {self.name!r} requires at least {self.min_items} items in {context}")
        if len(val) != len(set(val)):
            raise RuleConfigurationError(f"Duplicate reason codes in {self.name!r} for rule '{self.rule_id}' in {context}")
        fsem = FROZEN_RULE_SEMANTICS.get(self.rule_id, {})
        allowed = set(fsem.get("allowed_reason_codes", ()))
        if set(val) != allowed:
            raise RuleConfigurationError(
                f"reason_codes for rule '{self.rule_id}' must strictly equal {sorted(list(allowed))}, got {sorted(list(val))} in {context}"
            )
        return tuple(val)


class DictField(FieldDesc):
    def __init__(self, name: str, is_runtime: bool = True, required: bool = True, default: Any = None):
        super().__init__(name, is_runtime=is_runtime, required=required, default=default if default is not None else {})

    def to_json_schema(self) -> Dict[str, Any]:
        return {"type": "object"}

    def parse(self, val: Any, context: str) -> Dict[str, Any]:
        if not isinstance(val, dict):
            raise RuleConfigurationError(f"Field {self.name!r} must be an object in {context}, got {type(val).__name__}")
        return val


class StringField(FieldDesc):
    def __init__(self, name: str, is_runtime: bool = False, required: bool = True, min_length: int = 1, default: Any = None):
        super().__init__(name, is_runtime=is_runtime, required=required, default=default)
        self.min_length = min_length

    def to_json_schema(self) -> Dict[str, Any]:
        return {"type": "string", "minLength": self.min_length}

    def parse(self, val: Any, context: str) -> str:
        if not isinstance(val, str) or len(val) < self.min_length:
            raise RuleConfigurationError(f"Field {self.name!r} must be a string with minLength={self.min_length} in {context}, got {val!r}")
        return val


class PatternArrayField(FieldDesc):
    def __init__(self, name: str, min_items: int = 1, required: bool = True, default: Any = ()):
        super().__init__(name, is_runtime=True, required=required, default=default)
        self.min_items = min_items

    def to_json_schema(self) -> Dict[str, Any]:
        return _pattern_array_schema(self.min_items)

    def parse(self, val: Any, context: str) -> Tuple[PatternSpec, ...]:
        if not isinstance(val, list):
            raise RuleConfigurationError(f"Field {self.name!r} must be a list in {context}, got {type(val).__name__}")
        if len(val) < self.min_items:
            raise RuleConfigurationError(f"Field {self.name!r} requires at least {self.min_items} items in {context}")
        return tuple(parse_pattern_spec(item, f"{context}.{self.name}") for item in val)


class StringArrayField(FieldDesc):
    def __init__(self, name: str, min_items: int = 1, required: bool = True, default: Any = ()):
        super().__init__(name, is_runtime=True, required=required, default=default)
        self.min_items = min_items

    def to_json_schema(self) -> Dict[str, Any]:
        return {"type": "array", "minItems": self.min_items, "items": {"type": "string", "minLength": 1}}

    def parse(self, val: Any, context: str) -> Tuple[str, ...]:
        if not isinstance(val, list):
            raise RuleConfigurationError(f"Field {self.name!r} must be a list in {context}, got {type(val).__name__}")
        if len(val) < self.min_items:
            raise RuleConfigurationError(f"Field {self.name!r} requires at least {self.min_items} items in {context}")
        res = []
        for x in val:
            if not isinstance(x, str) or not x:
                raise RuleConfigurationError(f"Items in {self.name!r} must be non-empty strings in {context}, got {x!r}")
            res.append(x)
        return tuple(res)


class ReplacementArrayField(FieldDesc):
    def __init__(self, name: str = "deprecated_tags", required: bool = False):
        super().__init__(name, is_runtime=True, required=required, default=())

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["banned", "replacement"],
                "additionalProperties": False,
                "properties": {
                    "banned": _pattern_spec_schema(allow_regex=False),
                    "replacement": {"type": "string", "minLength": 1},
                },
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[ReplacementSpec, ...]:
        if not isinstance(val, list):
            raise RuleConfigurationError(f"Field {self.name!r} must be a list in {context}")
        out = []
        for d in val:
            if not isinstance(d, dict):
                raise RuleConfigurationError(f"Invalid item in {self.name!r} in {context}")
            allowed = {"banned", "replacement"}
            if set(d.keys()) != allowed:
                raise RuleConfigurationError(f"Invalid keys in {self.name!r} item in {context}: {d.keys()}")
            banned = parse_pattern_spec(d["banned"], f"{context}.{self.name}.banned")
            if banned.match_mode == "regex":
                raise RuleConfigurationError(f"Regex match_mode is forbidden for replacement in {context}")
            rep = d["replacement"]
            if not isinstance(rep, str) or not rep:
                raise RuleConfigurationError(f"replacement must be non-empty string in {context}")
            out.append(ReplacementSpec(banned=banned, replacement=rep))
        return tuple(out)


class VenueClustersField(FieldDesc):
    def __init__(self, name: str = "venue_clusters"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "minProperties": 1,
            "propertyNames": {
                "pattern": r"\S",
            },
            "additionalProperties": _pattern_array_schema(min_items=1),
        }

    def parse(self, val: Any, context: str) -> Mapping[str, Tuple[PatternSpec, ...]]:
        if not isinstance(val, dict) or not val:
            raise RuleConfigurationError(f"venue_clusters must be a non-empty dict in {context}")
        typed_vc = {}
        for k, v in val.items():
            if not isinstance(k, str) or not k or not k.strip():
                raise RuleConfigurationError(f"venue_clusters cluster key must be non-empty str in {context}")
            if not isinstance(v, list) or not v:
                raise RuleConfigurationError(f"Cluster {k!r} in venue_clusters must be a non-empty list in {context}")
            typed_vc[k] = tuple(parse_pattern_spec(x, f"{context}.venue_clusters.{k}") for x in v)
        return MappingProxyType(typed_vc)


class TriggerBanConflictsField(FieldDesc):
    def __init__(self, name: str = "conflicts", required: bool = False):
        super().__init__(name, is_runtime=True, required=required, default=())

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["trigger", "ban"],
                "additionalProperties": False,
                "properties": {
                    "trigger": _pattern_array_schema(1),
                    "ban": _pattern_array_schema(1),
                },
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[TriggerBanConflictSpec, ...]:
        if not isinstance(val, list):
            raise RuleConfigurationError(f"conflicts must be a list in {context}")
        out = []
        for c in val:
            if not isinstance(c, dict) or set(c.keys()) != {"trigger", "ban"}:
                raise RuleConfigurationError(f"Invalid conflict item in {context}")
            if not isinstance(c["trigger"], list) or not isinstance(c["ban"], list):
                raise RuleConfigurationError(f"trigger and ban must be lists in {context}")
            if not c["trigger"] or not c["ban"]:
                raise RuleConfigurationError(f"trigger and ban cannot be empty in {context}")
            out.append(
                TriggerBanConflictSpec(
                    trigger=tuple(parse_pattern_spec(x, f"{context}.conflict.trigger") for x in c["trigger"]),
                    ban=tuple(parse_pattern_spec(x, f"{context}.conflict.ban") for x in c["ban"]),
                )
            )
        return tuple(out)


class LevelRulesField(FieldDesc):
    def __init__(self, name: str = "level_rules"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "required": [f"L{i}" for i in range(1, 7)],
            "additionalProperties": False,
            "properties": {
                f"L{i}": {
                    "type": "object",
                    "required": ["name_zh", "banned_patterns"],
                    "additionalProperties": False,
                    "properties": {
                        "name_zh": {"type": "string", "minLength": 1},
                        "banned_patterns": _pattern_array_schema(1),
                    },
                }
                for i in range(1, 7)
            },
        }

    def parse(self, val: Any, context: str) -> Mapping[str, LevelRuleSpec]:
        if not isinstance(val, dict):
            raise RuleConfigurationError(f"level_rules must be a dict in {context}")
        expected_keys = {f"L{i}" for i in range(1, 7)}
        if set(val.keys()) != expected_keys:
            raise RuleConfigurationError(f"level_rules must have exactly L1..L6 in {context}, got {val.keys()}")
        typed_lr = {}
        for lvl_key in sorted(expected_keys):
            lvl_val = val[lvl_key]
            if not isinstance(lvl_val, dict) or set(lvl_val.keys()) != {"name_zh", "banned_patterns"}:
                raise RuleConfigurationError(f"Invalid level rule {lvl_key} in {context}")
            name_zh = lvl_val["name_zh"]
            if not isinstance(name_zh, str) or not name_zh:
                raise RuleConfigurationError(f"name_zh in {lvl_key} must be non-empty str in {context}")
            bp = lvl_val["banned_patterns"]
            if not isinstance(bp, list) or not bp:
                raise RuleConfigurationError(f"banned_patterns in {lvl_key} must be non-empty list in {context}")
            typed_lr[lvl_key] = LevelRuleSpec(
                name_zh=name_zh,
                banned_patterns=tuple(parse_pattern_spec(x, f"{context}.{lvl_key}") for x in bp),
            )
        return MappingProxyType(typed_lr)


class AngleGazeMappingsField(FieldDesc):
    def __init__(self, name: str = "mappings"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["angles", "banned_gaze"],
                "additionalProperties": False,
                "properties": {
                    "angles": _pattern_array_schema(1),
                    "banned_gaze": _pattern_array_schema(0),
                },
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[AngleGazeMappingSpec, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"mappings must be a non-empty list in {context}")
        out = []
        for item in val:
            if not isinstance(item, dict) or set(item.keys()) != {"angles", "banned_gaze"}:
                raise RuleConfigurationError(f"Invalid mapping in {context}, allowed keys: angles, banned_gaze")
            if not isinstance(item["angles"], list) or not isinstance(item["banned_gaze"], list):
                raise RuleConfigurationError(f"angles and banned_gaze must be lists in {context}")
            if not item["angles"]:
                raise RuleConfigurationError(f"angles and banned_gaze cannot be empty in {context}")
            out.append(
                AngleGazeMappingSpec(
                    angles=tuple(parse_pattern_spec(x, f"{context}.angles") for x in item["angles"]),
                    banned_gaze=tuple(parse_pattern_spec(x, f"{context}.banned_gaze") for x in item["banned_gaze"]),
                )
            )
        return tuple(out)


class ExclusivePairsField(FieldDesc):
    def __init__(self, name: str = "exclusive_pairs"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "items": _pattern_spec_schema(),
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[Tuple[PatternSpec, PatternSpec], ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"exclusive_pairs must be a non-empty list in {context}")
        out = []
        for pair in val:
            if not isinstance(pair, list) or len(pair) != 2:
                raise RuleConfigurationError(f"Each item in exclusive_pairs must be a 2-element list in {context}")
            out.append((
                parse_pattern_spec(pair[0], f"{context}.exclusive_pairs[0]"),
                parse_pattern_spec(pair[1], f"{context}.exclusive_pairs[1]"),
            ))
        return tuple(out)


class LiquidBannedCombosField(FieldDesc):
    def __init__(self, name: str = "banned_combos"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["trigger", "replace"],
                "additionalProperties": False,
                "properties": {
                    "trigger": _pattern_array_schema(1),
                    "replace": {"type": "string", "minLength": 1},
                },
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[BannedComboSpec, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"banned_combos must be a non-empty list in {context}")
        out = []
        for bc in val:
            if not isinstance(bc, dict) or set(bc.keys()) != {"trigger", "replace"}:
                raise RuleConfigurationError(f"Invalid banned_combo in {context}")
            if not isinstance(bc["trigger"], list) or not bc["trigger"]:
                raise RuleConfigurationError(f"trigger in banned_combo must be non-empty list in {context}")
            rep = bc["replace"]
            if not isinstance(rep, str) or not rep:
                raise RuleConfigurationError(f"replace in banned_combo must be non-empty str in {context}")
            out.append(
                BannedComboSpec(
                    triggers=tuple(parse_pattern_spec(x, f"{context}.trigger") for x in bc["trigger"]),
                    replace=rep,
                )
            )
        return tuple(out)


class DeviceConstraintsField(FieldDesc):
    def __init__(self, name: str = "device_constraints"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["devices", "banned_tags"],
                "additionalProperties": False,
                "properties": {
                    "devices": _pattern_array_schema(1),
                    "banned_tags": _pattern_array_schema(1),
                },
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[DeviceConstraintSpec, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"device_constraints must be non-empty list in {context}")
        out = []
        for item in val:
            if not isinstance(item, dict) or set(item.keys()) != {"devices", "banned_tags"}:
                raise RuleConfigurationError(f"Invalid item in device_constraints in {context}")
            if not isinstance(item["devices"], list) or not isinstance(item["banned_tags"], list):
                raise RuleConfigurationError(f"devices and banned_tags must be lists in {context}")
            if not item["devices"] or not item["banned_tags"]:
                raise RuleConfigurationError(f"devices and banned_tags cannot be empty in {context}")
            out.append(
                DeviceConstraintSpec(
                    devices=tuple(parse_pattern_spec(x, f"{context}.devices") for x in item["devices"]),
                    banned_tags=tuple(parse_pattern_spec(x, f"{context}.banned_tags") for x in item["banned_tags"]),
                )
            )
        return tuple(out)


class EmotionConflictsField(FieldDesc):
    def __init__(self, name: str = "conflicts"):
        super().__init__(name, is_runtime=True, required=True)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": [
                    "catalog_emotion_triggers",
                    "custom_emotion_triggers",
                    "catalog_banned_gaze",
                    "custom_banned_gaze",
                ],
                "additionalProperties": False,
                "properties": {
                    "catalog_emotion_triggers": _pattern_array_schema(0),
                    "custom_emotion_triggers": _pattern_array_schema(0),
                    "catalog_banned_gaze": _pattern_array_schema(0),
                    "custom_banned_gaze": _pattern_array_schema(0),
                },
                "allOf": [
                    {"anyOf": [{"properties": {"catalog_emotion_triggers": {"minItems": 1}}}, {"properties": {"custom_emotion_triggers": {"minItems": 1}}}]},
                    {"anyOf": [{"properties": {"catalog_banned_gaze": {"minItems": 1}}}, {"properties": {"custom_banned_gaze": {"minItems": 1}}}]},
                ],
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[EmotionGazeConflictSpec, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"conflicts must be non-empty list in {context}")
        out = []
        for item in val:
            expected_keys = {
                "catalog_emotion_triggers",
                "custom_emotion_triggers",
                "catalog_banned_gaze",
                "custom_banned_gaze",
            }
            if not isinstance(item, dict) or set(item.keys()) != expected_keys:
                raise RuleConfigurationError(f"Invalid conflict in {context}, expected keys: {expected_keys}")
            c_et = tuple(parse_pattern_spec(x, f"{context}.catalog_emotion_triggers") for x in item["catalog_emotion_triggers"])
            u_et = tuple(parse_pattern_spec(x, f"{context}.custom_emotion_triggers") for x in item["custom_emotion_triggers"])
            c_bg = tuple(parse_pattern_spec(x, f"{context}.catalog_banned_gaze") for x in item["catalog_banned_gaze"])
            u_bg = tuple(parse_pattern_spec(x, f"{context}.custom_banned_gaze") for x in item["custom_banned_gaze"])
            if len(c_et) + len(u_et) < 1:
                raise RuleConfigurationError(f"Combined emotion triggers cannot be empty in {context}")
            if len(c_bg) + len(u_bg) < 1:
                raise RuleConfigurationError(f"Combined banned gaze cannot be empty in {context}")
            out.append(
                EmotionGazeConflictSpec(
                    catalog_emotion_triggers=c_et,
                    custom_emotion_triggers=u_et,
                    catalog_banned_gaze=c_bg,
                    custom_banned_gaze=u_bg,
                )
            )
        return tuple(out)

# ─── 规则声明契约与双向解析器 ───

class RuleContractDescriptor:
    def __init__(
        self,
        rule_id: str,
        spec_cls: type,
        fields: Sequence[FieldDesc],
        cross_validators: Sequence[Callable[[Dict[str, Any], str], None]] = (),
        schema_extra: Optional[Dict[str, Any]] = None,
    ):
        self.rule_id = rule_id
        self.spec_cls = spec_cls
        self.fields = tuple(fields)
        self.cross_validators = tuple(cross_validators)
        self.schema_extra = dict(schema_extra or {})

        # P2-3 描述符定义期自校验
        field_names = [f.name for f in self.fields]
        if len(field_names) != len(set(field_names)):
            duplicates = [n for n in field_names if field_names.count(n) > 1]
            raise ValueError(f"Duplicate field names in descriptor for rule {rule_id}: {set(duplicates)}")

        if "id" in field_names:
            raise ValueError(f"'id' must not appear in descriptor fields for rule {rule_id}")

        if not isinstance(spec_cls, type) or not dataclasses.is_dataclass(spec_cls):
            raise TypeError(f"{spec_cls!r} must be a dataclass class for rule {rule_id}")

        dc_fields = dataclasses.fields(spec_cls)
        dc_field_map = {f.name: f for f in dc_fields}
        if "id" not in dc_field_map:
            raise ValueError(f"Dataclass {spec_cls.__name__} for rule {rule_id} must define an 'id' field")
        if not dc_field_map["id"].init:
            raise ValueError(f"'id' field in dataclass {spec_cls.__name__} for rule {rule_id} must have init=True")

        dc_field_names = {f.name for f in dc_fields if f.name != "id"}
        desc_field_names = set(field_names)
        if desc_field_names != dc_field_names:
            missing = dc_field_names - desc_field_names
            extra = desc_field_names - dc_field_names
            raise ValueError(
                f"Field definition mismatch in RuleContractDescriptor for rule {rule_id}: "
                f"missing={missing}, extra={extra}"
            )

        self.allowed_keys = frozenset(field_names) | {"id"}
        self.required_keys = frozenset(f.name for f in self.fields if f.required) | {"id"}

    def to_json_schema(self) -> Dict[str, Any]:
        props: Dict[str, Any] = {"id": {"const": self.rule_id}}
        for f in self.fields:
            props[f.name] = f.to_json_schema()
        schema: Dict[str, Any] = {
            "type": "object",
            "required": sorted(list(self.required_keys)),
            "additionalProperties": False,
            "properties": props,
        }
        if self.schema_extra:
            schema.update(self.schema_extra)
        return schema

    def parse_and_validate(self, raw: Dict[str, Any]) -> Any:
        if not isinstance(raw, dict):
            raise RuleConfigurationError(f"Rule {self.rule_id} must be a JSON object, got {type(raw).__name__}")
        actual_keys = set(raw.keys())
        extra_keys = actual_keys - self.allowed_keys
        if extra_keys:
            raise RuleConfigurationError(f"Rule {self.rule_id} contains unknown fields: {extra_keys}")
        missing_keys = self.required_keys - actual_keys
        if missing_keys:
            raise RuleConfigurationError(f"Rule {self.rule_id} missing required fields: {missing_keys}")

        kwargs: Dict[str, Any] = {"id": self.rule_id}
        for f in self.fields:
            if f.name in raw:
                kwargs[f.name] = f.parse(raw[f.name], f"rule[{self.rule_id}].{f.name}")
            elif not f.required:
                kwargs[f.name] = f.default

        # 执行跨字段非空与逻辑验证
        for cv in self.cross_validators:
            cv(raw, f"rule[{self.rule_id}]")

        return self.spec_cls(**kwargs)


def _validate_combined_non_empty(k1: str, k2: str):
    def validator(parsed: Dict[str, Any], context: str):
        if len(parsed.get(k1, ())) + len(parsed.get(k2, ())) < 1:
            raise RuleConfigurationError(f"Combined {k1} and {k2} must have at least 1 item in {context}")
    return validator


# ─── 17 规则全量声明式契约注册表 ───

RULE_DESCRIPTORS: Dict[str, RuleContractDescriptor] = {
    # 1. spatial_environmental_mutual_exclusion
    "spatial_environmental_mutual_exclusion": RuleContractDescriptor(
        "spatial_environmental_mutual_exclusion",
        SpatialEnvironmentalRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("spatial_environmental_mutual_exclusion"),
            SemanticConstraintsField("spatial_environmental_mutual_exclusion", required=True),
            TextFallbackField("spatial_environmental_mutual_exclusion", required=True),
            VenueClustersField(),
            PatternArrayField("outdoor_exclusive", min_items=1),
            PatternArrayField("indoor_exclusive", min_items=1),
            ReplacementArrayField("deprecated_tags", required=False),
        ],
    ),
    # 2. nudity_clothing_conflicts
    "nudity_clothing_conflicts": RuleContractDescriptor(
        "nudity_clothing_conflicts",
        NudityClothingRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("nudity_clothing_conflicts"),
            SemanticConstraintsField("nudity_clothing_conflicts", required=True),
            TextFallbackField("nudity_clothing_conflicts", required=True),
            LevelRulesField(),
            TriggerBanConflictsField("conflicts", required=False),
        ],
    ),
    # 3. material_penetration
    "material_penetration": RuleContractDescriptor(
        "material_penetration",
        MaterialPenetrationRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("material_penetration"),
            SemanticConstraintsField("material_penetration", required=True),
            TextFallbackField("material_penetration", required=True),
            PatternArrayField("banned_words", min_items=1),
            StringArrayField("replacements", min_items=1),
            TargetSlotsField("target_slots"),
            TargetProvenanceKindsField("target_provenance_kinds"),
        ],
    ),
    # 4. clothing_style_state_coherence
    "clothing_style_state_coherence": RuleContractDescriptor(
        "clothing_style_state_coherence",
        ClothingStyleStateRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("clothing_style_state_coherence"),
            SemanticConstraintsField("clothing_style_state_coherence", required=True),
            TextFallbackField("clothing_style_state_coherence", required=True),
            StringField("name_zh", is_runtime=False, required=False),
            PatternArrayField("one_piece_triggers", min_items=1),
            PatternArrayField("one_piece_banned_states", min_items=1),
            PatternArrayField("pants_triggers", min_items=0, required=False),
            PatternArrayField("pants_banned_states", min_items=0, required=False),
        ],
    ),
    # 5. gaze_angle_geometry
    "gaze_angle_geometry": RuleContractDescriptor(
        "gaze_angle_geometry",
        GazeAngleGeometryRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("gaze_angle_geometry"),
            SemanticConstraintsField("gaze_angle_geometry", required=True),
            TextFallbackField("gaze_angle_geometry", required=True),
            AngleGazeMappingsField(),
        ],
    ),
    # 6. gaze_mutual_exclusion
    "gaze_mutual_exclusion": RuleContractDescriptor(
        "gaze_mutual_exclusion",
        GazeMutualExclusionRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("gaze_mutual_exclusion"),
            SemanticConstraintsField("gaze_mutual_exclusion", required=True),
            TextFallbackField("gaze_mutual_exclusion", required=True),
            ExclusivePairsField(),
        ],
    ),
    # 7. accessory_occlusion_gaze_coherence
    "accessory_occlusion_gaze_coherence": RuleContractDescriptor(
        "accessory_occlusion_gaze_coherence",
        AccessoryOcclusionGazeRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("accessory_occlusion_gaze_coherence"),
            SemanticConstraintsField("accessory_occlusion_gaze_coherence", required=True),
            TextFallbackField("accessory_occlusion_gaze_coherence", required=True),
            StringField("name_zh", is_runtime=False, required=False),
            PatternArrayField("catalog_occlusion_triggers", min_items=0),
            PatternArrayField("custom_occlusion_triggers", min_items=0),
            PatternArrayField("catalog_banned_gaze_actions", min_items=0),
            PatternArrayField("custom_banned_gaze_actions", min_items=0),
        ],
        cross_validators=[
            _validate_combined_non_empty("catalog_occlusion_triggers", "custom_occlusion_triggers"),
            _validate_combined_non_empty("catalog_banned_gaze_actions", "custom_banned_gaze_actions"),
        ],
        schema_extra={
            "allOf": [
                {"anyOf": [{"properties": {"catalog_occlusion_triggers": {"minItems": 1}}}, {"properties": {"custom_occlusion_triggers": {"minItems": 1}}}]},
                {"anyOf": [{"properties": {"catalog_banned_gaze_actions": {"minItems": 1}}}, {"properties": {"custom_banned_gaze_actions": {"minItems": 1}}}]},
            ]
        }
    ),
    # 8. framing_lower_body_coherence
    "framing_lower_body_coherence": RuleContractDescriptor(
        "framing_lower_body_coherence",
        FramingLowerBodyRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("framing_lower_body_coherence"),
            SemanticConstraintsField("framing_lower_body_coherence", required=True),
            TextFallbackField("framing_lower_body_coherence", required=True),
            StringField("name_zh", is_runtime=False, required=False),
            PatternArrayField("catalog_close_up_triggers", min_items=0),
            PatternArrayField("custom_close_up_triggers", min_items=0),
            PatternArrayField("catalog_banned_lower_body", min_items=0),
            PatternArrayField("custom_banned_lower_body", min_items=0),
        ],
        cross_validators=[
            _validate_combined_non_empty("catalog_close_up_triggers", "custom_close_up_triggers"),
            _validate_combined_non_empty("catalog_banned_lower_body", "custom_banned_lower_body"),
        ],
        schema_extra={
            "allOf": [
                {"anyOf": [{"properties": {"catalog_close_up_triggers": {"minItems": 1}}}, {"properties": {"custom_close_up_triggers": {"minItems": 1}}}]},
                {"anyOf": [{"properties": {"catalog_banned_lower_body": {"minItems": 1}}}, {"properties": {"custom_banned_lower_body": {"minItems": 1}}}]},
            ]
        }
    ),
    # 9. liquid_restrictions
    "liquid_restrictions": RuleContractDescriptor(
        "liquid_restrictions",
        LiquidRestrictionsRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("liquid_restrictions"),
            SemanticConstraintsField("liquid_restrictions", required=True),
            TextFallbackField("liquid_restrictions", required=True),
            PatternArrayField("liquid_words", min_items=1),
            StringArrayField("modifiers", min_items=1),
            LiquidBannedCombosField(),
        ],
    ),
    # 10. device_quality_compatibility
    "device_quality_compatibility": RuleContractDescriptor(
        "device_quality_compatibility",
        DeviceQualityRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("device_quality_compatibility"),
            SemanticConstraintsField("device_quality_compatibility", required=True),
            TextFallbackField("device_quality_compatibility", required=True),
            DeviceConstraintsField(),
        ],
    ),
    # 11. tattoo_dermal_fusion
    "tattoo_dermal_fusion": RuleContractDescriptor(
        "tattoo_dermal_fusion",
        TattooDermalFusionRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("tattoo_dermal_fusion"),
            SemanticConstraintsField("tattoo_dermal_fusion", required=True),
            TextFallbackField("tattoo_dermal_fusion", required=True),
            PatternArrayField("tattoo_indicators", min_items=1),
            StringArrayField("fusion_tags", min_items=1),
        ],
    ),
    # 12. pose_hand_occupation
    "pose_hand_occupation": RuleContractDescriptor(
        "pose_hand_occupation",
        PoseHandOccupationRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("pose_hand_occupation"),
            SemanticConstraintsField("pose_hand_occupation", required=True),
            TextFallbackField("pose_hand_occupation", required=True),
            PatternArrayField("catalog_busy_pose_triggers", min_items=0),
            PatternArrayField("custom_busy_pose_triggers", min_items=0),
            PatternArrayField("catalog_handheld_patterns", min_items=0),
            PatternArrayField("custom_handheld_patterns", min_items=0),
        ],
        cross_validators=[
            _validate_combined_non_empty("catalog_busy_pose_triggers", "custom_busy_pose_triggers"),
            _validate_combined_non_empty("catalog_handheld_patterns", "custom_handheld_patterns"),
        ],
        schema_extra={
            "allOf": [
                {"anyOf": [{"properties": {"catalog_busy_pose_triggers": {"minItems": 1}}}, {"properties": {"custom_busy_pose_triggers": {"minItems": 1}}}]},
                {"anyOf": [{"properties": {"catalog_handheld_patterns": {"minItems": 1}}}, {"properties": {"custom_handheld_patterns": {"minItems": 1}}}]},
            ]
        }
    ),
    # 13. handheld_props_single_holder
    "handheld_props_single_holder": RuleContractDescriptor(
        "handheld_props_single_holder",
        HandheldPropsRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("handheld_props_single_holder"),
            SemanticConstraintsField("handheld_props_single_holder", required=True),
            TextFallbackField("handheld_props_single_holder", required=True),
            StringField("name_zh", is_runtime=False, required=False),
            PatternArrayField("handheld_patterns", min_items=1),
        ],
    ),
    # 14. emotion_gaze_affinity
    "emotion_gaze_affinity": RuleContractDescriptor(
        "emotion_gaze_affinity",
        EmotionGazeAffinityRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("emotion_gaze_affinity"),
            SemanticConstraintsField("emotion_gaze_affinity", required=True),
            TextFallbackField("emotion_gaze_affinity", required=True),
            EmotionConflictsField(),
        ],
    ),
    # 15. environmental_lighting_coherence
    "environmental_lighting_coherence": RuleContractDescriptor(
        "environmental_lighting_coherence",
        EnvironmentalLightingRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("environmental_lighting_coherence"),
            SemanticConstraintsField("environmental_lighting_coherence", required=True),
            TextFallbackField("environmental_lighting_coherence", required=True),
            PatternArrayField("catalog_daylight_triggers", min_items=0),
            PatternArrayField("custom_daylight_triggers", min_items=0),
            PatternArrayField("catalog_banned_night_elements", min_items=0),
            PatternArrayField("custom_banned_night_elements", min_items=0),
        ],
        cross_validators=[
            _validate_combined_non_empty("catalog_daylight_triggers", "custom_daylight_triggers"),
            _validate_combined_non_empty("catalog_banned_night_elements", "custom_banned_night_elements"),
        ],
        schema_extra={
            "allOf": [
                {"anyOf": [{"properties": {"catalog_daylight_triggers": {"minItems": 1}}}, {"properties": {"custom_daylight_triggers": {"minItems": 1}}}]},
                {"anyOf": [{"properties": {"catalog_banned_night_elements": {"minItems": 1}}}, {"properties": {"custom_banned_night_elements": {"minItems": 1}}}]},
            ]
        }
    ),
    # 16. monochrome_film_chroma_coherence
    "monochrome_film_chroma_coherence": RuleContractDescriptor(
        "monochrome_film_chroma_coherence",
        MonochromeFilmChromaRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("monochrome_film_chroma_coherence"),
            SemanticConstraintsField("monochrome_film_chroma_coherence", required=True),
            TextFallbackField("monochrome_film_chroma_coherence", required=True),
            StringField("name_zh", is_runtime=False, required=False),
            PatternArrayField("catalog_monochrome_triggers", min_items=0),
            PatternArrayField("custom_monochrome_triggers", min_items=0),
            PatternArrayField("catalog_banned_chroma", min_items=0),
            PatternArrayField("custom_banned_chroma", min_items=0),
        ],
        cross_validators=[
            _validate_combined_non_empty("catalog_monochrome_triggers", "custom_monochrome_triggers"),
            _validate_combined_non_empty("catalog_banned_chroma", "custom_banned_chroma"),
        ],
        schema_extra={
            "allOf": [
                {"anyOf": [{"properties": {"catalog_monochrome_triggers": {"minItems": 1}}}, {"properties": {"custom_monochrome_triggers": {"minItems": 1}}}]},
                {"anyOf": [{"properties": {"catalog_banned_chroma": {"minItems": 1}}}, {"properties": {"custom_banned_chroma": {"minItems": 1}}}]},
            ]
        }
    ),
    # 17. makeup_details_coherence
    "makeup_details_coherence": RuleContractDescriptor(
        "makeup_details_coherence",
        MakeupDetailsRuleSpec,
        [
            StringField("description", is_runtime=False),
            IntegerField("priority", min_value=1),
            EnumField("phase", VALID_PHASES),
            StringArrayField("depends_on", min_items=0),
            ReasonCodesField("makeup_details_coherence"),
            SemanticConstraintsField("makeup_details_coherence", required=True),
            TextFallbackField("makeup_details_coherence", required=True),
            PatternArrayField("catalog_no_makeup_triggers", min_items=0),
            PatternArrayField("custom_no_makeup_triggers", min_items=0),
            PatternArrayField("catalog_banned_makeup_smudge", min_items=0),
            PatternArrayField("custom_banned_makeup_smudge", min_items=0),
        ],
        cross_validators=[
            _validate_combined_non_empty("catalog_no_makeup_triggers", "custom_no_makeup_triggers"),
            _validate_combined_non_empty("catalog_banned_makeup_smudge", "custom_banned_makeup_smudge"),
        ],
        schema_extra={
            "allOf": [
                {"anyOf": [{"properties": {"catalog_no_makeup_triggers": {"minItems": 1}}}, {"properties": {"custom_no_makeup_triggers": {"minItems": 1}}}]},
                {"anyOf": [{"properties": {"catalog_banned_makeup_smudge": {"minItems": 1}}}, {"properties": {"custom_banned_makeup_smudge": {"minItems": 1}}}]},
            ]
        }
    ),
}


# ─── 统一规则文档容器与解析入口 ───

RULE_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    rid: tuple(sorted(list(desc.required_keys - {"id"})))
    for rid, desc in RULE_DESCRIPTORS.items()
}


@dataclass(frozen=True)
class RuleItem:
    """单一规则容器：强类型、只读深层不可变。"""
    id: str
    description: str
    spec: Any  # 强类型 RuleSpec 实例
    priority: int = 100
    phase: str = "anchors"
    depends_on: Tuple[str, ...] = ()
    reason_codes: Tuple[str, ...] = ()
    semantic_constraints: Optional[SemanticConstraintSpec] = None
    text_fallback: Optional[TextFallbackSpec] = None


@dataclass(frozen=True)
class RuleDocument:
    """冲突规则全量文档：只读深层不可变容器。"""
    rules: Tuple[RuleItem, ...]
    rule_map: Mapping[str, RuleItem]
    execution_order: Tuple[str, ...] = ()

    def get_rule(self, rule_id: str) -> Optional[RuleItem]:
        return self.rule_map.get(rule_id)


def export_json_schema() -> Dict[str, Any]:
    """单源生成 Draft-7 JSON Schema。"""
    rule_schemas = [RULE_DESCRIPTORS[rid].to_json_schema() for rid in STABLE_RULE_ORDER]

    # Draft-7 保证每个规则 ID 恰好出现一次 (P1-3):
    # 结合 minItems=17, maxItems=17 与每个 ID 的 contains 约束，
    # 确保 17 个稳定规则 ID 均出现且恰好出现一次。
    id_contains_constraints = [
        {
            "contains": {
                "type": "object",
                "required": ["id"],
                "properties": {"id": {"const": rid}},
            }
        }
        for rid in STABLE_RULE_ORDER
    ]

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ConflictRulesConfiguration",
        "description": "ComfyUI-IYKYK 17 大冲突消解规则权威配置 Schema (单源由 Python 契约生成)",
        "type": "object",
        "required": ["rules"],
        "additionalProperties": False,
        "properties": {
            "rules": {
                "type": "array",
                "minItems": len(STABLE_RULE_ORDER),
                "maxItems": len(STABLE_RULE_ORDER),
                "items": {"anyOf": rule_schemas},
                "allOf": id_contains_constraints,
            }
        },
    }


def validate_and_sort_dag(rules: Sequence[RuleItem]) -> Tuple[RuleItem, ...]:
    """根据 phase, priority 和 depends_on 执行拓扑排序与 Fail-Closed 深度校验。

    校验项：
    - 恰好存在 17 个规则 ID，无多无少；
    - priority 全局唯一正整数；
    - phase 属于 ('anchors', 'physical', 'semantic', 'effects')；
    - depends_on 均在 17 规则中，无自依赖；
    - 无环路（Kahn's Algorithm 校验，异常抛出 RuleConfigurationError）；
    - 多个当前可执行节点按 priority 升序决胜；
    - 校验 phase 分期一致性（下游规则 phase 不得先于上游依赖的 phase）。
    """
    rule_map = {r.id: r for r in rules}
    if set(rule_map.keys()) != set(FROZEN_DAG_METADATA.keys()):
        missing = set(FROZEN_DAG_METADATA.keys()) - set(rule_map.keys())
        extra = set(rule_map.keys()) - set(FROZEN_DAG_METADATA.keys())
        raise RuleConfigurationError(f"DAG rules mismatch: missing={missing}, extra={extra}")

    priorities = [r.priority for r in rules]
    if len(set(priorities)) != len(priorities):
        dup = [p for p in priorities if priorities.count(p) > 1]
        raise RuleConfigurationError(f"Duplicate rule priorities detected: {set(dup)}")

    in_degree: Dict[str, int] = {}
    adj: Dict[str, List[str]] = {r.id: [] for r in rules}

    for r in rules:
        if r.priority < 1:
            raise RuleConfigurationError(f"Priority must be positive integer, got {r.priority} for {r.id}")
        if r.phase not in VALID_PHASES:
            raise RuleConfigurationError(f"Invalid phase {r.phase!r} for rule {r.id}")

        expected_meta = FROZEN_DAG_METADATA[r.id]
        if r.priority != expected_meta["priority"]:
            raise RuleConfigurationError(f"Rule {r.id} priority mismatch: expected {expected_meta['priority']}, got {r.priority}")
        if r.phase != expected_meta["phase"]:
            raise RuleConfigurationError(f"Rule {r.id} phase mismatch: expected {expected_meta['phase']}, got {r.phase}")
        if tuple(r.depends_on) != expected_meta["depends_on"]:
            raise RuleConfigurationError(f"Rule {r.id} depends_on mismatch: expected {expected_meta['depends_on']}, got {r.depends_on}")

        if r.id in r.depends_on:
            raise RuleConfigurationError(f"Self-dependency detected in rule {r.id}")

        for dep in r.depends_on:
            if dep not in rule_map:
                raise RuleConfigurationError(f"Rule {r.id} depends on unknown rule {dep}")
            dep_rule = rule_map[dep]
            if PHASE_EXECUTION_ORDER[r.phase] < PHASE_EXECUTION_ORDER[dep_rule.phase]:
                raise RuleConfigurationError(
                    f"Rule {r.id} in phase '{r.phase}' cannot depend on rule {dep} in later phase '{dep_rule.phase}'"
                )
            adj[dep].append(r.id)

        in_degree[r.id] = len(r.depends_on)

    import heapq
    ready: List[Tuple[int, str]] = []
    for r in rules:
        if in_degree[r.id] == 0:
            heapq.heappush(ready, (r.priority, r.id))

    sorted_items: List[RuleItem] = []
    while ready:
        _, curr_id = heapq.heappop(ready)
        sorted_items.append(rule_map[curr_id])
        for nxt_id in adj[curr_id]:
            in_degree[nxt_id] -= 1
            if in_degree[nxt_id] == 0:
                heapq.heappush(ready, (rule_map[nxt_id].priority, nxt_id))

    if len(sorted_items) != len(rules):
        raise RuleConfigurationError("Cyclic dependency detected in conflict rules DAG!")

    return tuple(sorted_items)


def parse_rule_document(doc: Any) -> RuleDocument:
    """权威单源运行时解析入口：严格校验顶层与 17 规则，返回深层不可变强类型 RuleDocument。"""
    if not isinstance(doc, dict):
        raise RuleConfigurationError(f"Conflict rules document must be a JSON object, got {type(doc).__name__}")

    # 顶层严格禁止未知字段 (等价于 additionalProperties: false)
    if set(doc.keys()) != {"rules"}:
        raise RuleConfigurationError(f"Root conflict rules document must contain only 'rules', got {set(doc.keys())}")

    rules = doc["rules"]
    if not isinstance(rules, list):
        raise RuleConfigurationError("Field 'rules' must be a list")

    if len(rules) != len(STABLE_RULE_ORDER):
        raise RuleConfigurationError(f"Expected exactly {len(STABLE_RULE_ORDER)} rules, found {len(rules)}")

    rule_ids = [r.get("id") for r in rules if isinstance(r, dict)]
    if len(rule_ids) != len(rules):
        raise RuleConfigurationError("Some rules in 'rules' array are not JSON objects or lack 'id'")

    if len(set(rule_ids)) != len(rule_ids):
        duplicates = [rid for rid in rule_ids if rule_ids.count(rid) > 1]
        raise RuleConfigurationError(f"Duplicate rule IDs detected: {set(duplicates)}")

    if set(rule_ids) != set(STABLE_RULE_ORDER):
        missing = set(STABLE_RULE_ORDER) - set(rule_ids)
        unexpected = set(rule_ids) - set(STABLE_RULE_ORDER)
        raise RuleConfigurationError(f"Rule IDs mismatch! Missing: {missing}, Unexpected: {unexpected}")

    items: List[RuleItem] = []
    item_map: Dict[str, RuleItem] = {}
    for r in rules:
        rid = r["id"]
        descriptor = RULE_DESCRIPTORS[rid]
        spec = descriptor.parse_and_validate(r)
        item = RuleItem(
            id=rid,
            description=spec.description,
            spec=spec,
            priority=spec.priority,
            phase=spec.phase,
            depends_on=spec.depends_on,
            reason_codes=spec.reason_codes,
            semantic_constraints=spec.semantic_constraints,
            text_fallback=spec.text_fallback,
        )
        items.append(item)
        item_map[rid] = item

    sorted_items = validate_and_sort_dag(items)
    return RuleDocument(
        rules=sorted_items,
        rule_map=MappingProxyType(item_map),
        execution_order=tuple(it.id for it in sorted_items),
    )


def validate_rule_document(doc: Any) -> None:
    """权威单一源验证：严格校验冲突规则配置文件的结构完整性与类型约束。"""
    parse_rule_document(doc)
