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

import dataclasses
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

if __package__:
    from .errors import RuleConfigurationError
    from .slot_contract import ALLOWED_SLOTS
else:
    from lib.errors import RuleConfigurationError
    from lib.slot_contract import ALLOWED_SLOTS


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


@dataclass(frozen=True)
class PatternSpec:
    """单一模式匹配强类型规范：统一编译、匹配与替换。"""
    pattern: str
    match_mode: MatchMode

    def compile(self) -> re.Pattern:
        if self.match_mode == "exact":
            p = re.escape(self.pattern.strip(" ,"))
            return re.compile(rf"^\s*{p}\s*$", re.IGNORECASE)
        elif self.match_mode == "word":
            p = re.escape(self.pattern.strip())
            return re.compile(rf"\b{p}\b", re.IGNORECASE)
        elif self.match_mode == "phrase":
            p = re.escape(self.pattern.strip())
            p_pattern = re.sub(r"\\\s+", r"\\s+", p)
            return re.compile(rf"(?:\b|^){p_pattern}(?:\b|$)", re.IGNORECASE)
        elif self.match_mode == "regex":
            return re.compile(self.pattern, re.IGNORECASE)
        else:
            raise RuleConfigurationError(f"Unknown match mode: {self.match_mode}")

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


def parse_pattern_spec(data: Any, context: str = "") -> PatternSpec:
    """严格解析并校验单个 PatternSpec 对象。"""
    if not isinstance(data, dict):
        raise RuleConfigurationError(
            f"Pattern spec must be a dictionary with 'pattern' and 'match_mode', got {type(data).__name__} in {context}"
        )

    allowed_keys = {"pattern", "match_mode"}
    extra_keys = set(data.keys()) - allowed_keys
    if extra_keys:
        raise RuleConfigurationError(
            f"Unknown fields {extra_keys} in pattern spec in {context}"
        )

    pattern = data.get("pattern")
    match_mode = data.get("match_mode")

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

    return PatternSpec(pattern=pattern.strip(), match_mode=match_mode)


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
class SpatialEnvironmentalRuleSpec(RuleSpecMixin):
    id: str
    description: str
    venue_clusters: Mapping[str, Tuple[PatternSpec, ...]]
    outdoor_exclusive: Tuple[PatternSpec, ...]
    indoor_exclusive: Tuple[PatternSpec, ...]
    deprecated_tags: Tuple[ReplacementSpec, ...] = ()


@dataclass(frozen=True)
class NudityClothingRuleSpec(RuleSpecMixin):
    id: str
    description: str
    level_rules: Mapping[str, LevelRuleSpec]
    conflicts: Tuple[TriggerBanConflictSpec, ...] = ()


@dataclass(frozen=True)
class MaterialPenetrationRuleSpec(RuleSpecMixin):
    id: str
    description: str
    banned_words: Tuple[PatternSpec, ...]
    replacements: Tuple[str, ...]
    target_slots: Tuple[str, ...]
    target_provenance_kinds: Tuple[str, ...]


@dataclass(frozen=True)
class ClothingStyleStateRuleSpec(RuleSpecMixin):
    id: str
    description: str
    one_piece_triggers: Tuple[PatternSpec, ...]
    one_piece_banned_states: Tuple[PatternSpec, ...]
    pants_triggers: Tuple[PatternSpec, ...] = ()
    pants_banned_states: Tuple[PatternSpec, ...] = ()
    name_zh: Optional[str] = None


@dataclass(frozen=True)
class GazeAngleGeometryRuleSpec(RuleSpecMixin):
    id: str
    description: str
    mappings: Tuple[AngleGazeMappingSpec, ...]


@dataclass(frozen=True)
class GazeMutualExclusionRuleSpec(RuleSpecMixin):
    id: str
    description: str
    exclusive_pairs: Tuple[Tuple[PatternSpec, PatternSpec], ...]


@dataclass(frozen=True)
class AccessoryOcclusionGazeRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_occlusion_triggers: Tuple[PatternSpec, ...]
    custom_occlusion_triggers: Tuple[PatternSpec, ...]
    catalog_banned_gaze_actions: Tuple[PatternSpec, ...]
    custom_banned_gaze_actions: Tuple[PatternSpec, ...]
    name_zh: Optional[str] = None

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


@dataclass(frozen=True)
class DeviceQualityRuleSpec(RuleSpecMixin):
    id: str
    description: str
    device_constraints: Tuple[DeviceConstraintSpec, ...]


@dataclass(frozen=True)
class TattooDermalFusionRuleSpec(RuleSpecMixin):
    id: str
    description: str
    tattoo_indicators: Tuple[PatternSpec, ...]
    fusion_tags: Tuple[str, ...]


@dataclass(frozen=True)
class PoseHandOccupationRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_busy_pose_triggers: Tuple[PatternSpec, ...]
    custom_busy_pose_triggers: Tuple[PatternSpec, ...]
    catalog_handheld_patterns: Tuple[PatternSpec, ...]
    custom_handheld_patterns: Tuple[PatternSpec, ...]

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


@dataclass(frozen=True)
class EmotionGazeAffinityRuleSpec(RuleSpecMixin):
    id: str
    description: str
    conflicts: Tuple[EmotionGazeConflictSpec, ...]


@dataclass(frozen=True)
class EnvironmentalLightingRuleSpec(RuleSpecMixin):
    id: str
    description: str
    catalog_daylight_triggers: Tuple[PatternSpec, ...]
    custom_daylight_triggers: Tuple[PatternSpec, ...]
    catalog_banned_night_elements: Tuple[PatternSpec, ...]
    custom_banned_night_elements: Tuple[PatternSpec, ...]

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

    @property
    def no_makeup_triggers(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_no_makeup_triggers + self.custom_no_makeup_triggers

    @property
    def banned_makeup_smudge(self) -> Tuple[PatternSpec, ...]:
        return self.catalog_banned_makeup_smudge + self.custom_banned_makeup_smudge


# ─── 统一声明式字段描述符体系 ───

def _pattern_spec_schema(allow_regex: bool = True) -> Dict[str, Any]:
    modes = list(VALID_MATCH_MODES) if allow_regex else ["exact", "word", "phrase"]
    return {
        "type": "object",
        "required": ["pattern", "match_mode"],
        "additionalProperties": False,
        "properties": {
            "pattern": {
                "type": "string",
                "minLength": 1,
                "pattern": r"\S",
            },
            "match_mode": {"type": "string", "enum": modes},
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


class TargetSlotsField(FieldDesc):
    def __init__(self, name: str = "target_slots", required: bool = True):
        super().__init__(name, is_runtime=True, required=required)

    def to_json_schema(self) -> Dict[str, Any]:
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "string",
                "enum": sorted(list(ALLOWED_SLOTS)),
            },
        }

    def parse(self, val: Any, context: str) -> Tuple[str, ...]:
        if not isinstance(val, list) or not val:
            raise RuleConfigurationError(f"Field '{self.name}' must be a non-empty list in {context}")
        out = []
        for x in val:
            if not isinstance(x, str) or not x.strip():
                raise RuleConfigurationError(f"Items in '{self.name}' must be non-empty strings in {context}")
            if x not in ALLOWED_SLOTS:
                raise RuleConfigurationError(
                    f"Invalid slot {x!r} in '{self.name}' for {context}. Allowed slots: {sorted(list(ALLOWED_SLOTS))}"
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
            AngleGazeMappingsField(),
        ],
    ),
    # 6. gaze_mutual_exclusion
    "gaze_mutual_exclusion": RuleContractDescriptor(
        "gaze_mutual_exclusion",
        GazeMutualExclusionRuleSpec,
        [
            StringField("description", is_runtime=False),
            ExclusivePairsField(),
        ],
    ),
    # 7. accessory_occlusion_gaze_coherence
    "accessory_occlusion_gaze_coherence": RuleContractDescriptor(
        "accessory_occlusion_gaze_coherence",
        AccessoryOcclusionGazeRuleSpec,
        [
            StringField("description", is_runtime=False),
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
            DeviceConstraintsField(),
        ],
    ),
    # 11. tattoo_dermal_fusion
    "tattoo_dermal_fusion": RuleContractDescriptor(
        "tattoo_dermal_fusion",
        TattooDermalFusionRuleSpec,
        [
            StringField("description", is_runtime=False),
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
            EmotionConflictsField(),
        ],
    ),
    # 15. environmental_lighting_coherence
    "environmental_lighting_coherence": RuleContractDescriptor(
        "environmental_lighting_coherence",
        EnvironmentalLightingRuleSpec,
        [
            StringField("description", is_runtime=False),
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


@dataclass(frozen=True)
class RuleDocument:
    """冲突规则全量文档：只读深层不可变容器。"""
    rules: Tuple[RuleItem, ...]
    rule_map: Mapping[str, RuleItem]

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
        item = RuleItem(id=rid, description=spec.description, spec=spec)
        items.append(item)
        item_map[rid] = item

    return RuleDocument(rules=tuple(items), rule_map=MappingProxyType(item_map))


def validate_rule_document(doc: Any) -> None:
    """权威单一源验证：严格校验冲突规则配置文件的结构完整性与类型约束。"""
    parse_rule_document(doc)
