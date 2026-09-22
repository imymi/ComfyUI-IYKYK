"""
models.py — 提示词结构化数据模型 (PromptFragment, PromptAtom, SampleResult & TagProvenance)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class SpanType(Enum):
    PLAIN = "plain"          # 普通提示词文本：可检测、可改写内部字节、可整块删除
    PAREN = "paren"          # 权重标签 (text:1.2)：可检测、不可改写内部字节、可整块删除
    BRACKET = "bracket"      # 提示词调度 [tag1:tag2:10]：可检测、不可改写内部字节、可整块删除
    ANGLE = "angle"          # LoRA/Embedding <lora:...>：不可检测、不可改写内部字节、不可删除
    QUOTED = "quoted"        # 精确引号短语 "..."：不可检测、不可改写内部字节、不可删除
    ESCAPED = "escaped"      # 转义序列 \, \"：不可改写内部字节


@dataclass(frozen=True)
class PromptSpan:
    text: str
    span_type: SpanType
    contains_blackbox: bool = False
    start_idx: int = 0
    end_idx: int = 0
    raw_text: str = ""

    @property
    def can_detect(self) -> bool:
        """规则是否可以检测该 span 的语义内容。"""
        return self.span_type in (SpanType.PLAIN, SpanType.PAREN, SpanType.BRACKET)

    @property
    def can_modify_internal(self) -> bool:
        """规则是否可以改写该 span 内部的字节内容。"""
        return self.span_type == SpanType.PLAIN

    @property
    def can_delete_atom(self) -> bool:
        """规则在命中冲突时是否可以整块移除该原子 span。若包含黑盒后代，绝对不可删除。"""
        if self.contains_blackbox or self.is_blackbox:
            return False
        return self.span_type in (SpanType.PLAIN, SpanType.PAREN, SpanType.BRACKET)

    @property
    def is_blackbox(self) -> bool:
        """纯黑盒语法：不可检测、不可改写、不可删除。"""
        return self.span_type in (SpanType.ANGLE, SpanType.QUOTED)


ORDERED_CONTEXT_IDS: Tuple[str, ...] = (
    "school",
    "office",
    "medical",
    "onsen_bath",
    "bondage_sm",
    "traditional",
    "nightlife",
    "domestic",
    "transit",
    "outdoor",
    "dining",
    "adult",
    "special",
    "generic",
)

VALID_SEMANTIC_ROLES: Tuple[str, ...] = ("scene_anchor", "scene_detail", "selector", "effect", "quality")
VALID_SPACE_KINDS: Tuple[str, ...] = ("indoor", "outdoor", "mixed", "neutral")
VALID_VISIBLE_REGIONS: Tuple[str, ...] = (
    "face", "upper_body", "lower_body", "hands", "feet", "full_body", "intimate_lower_body"
)
VALID_GARMENT_TOPOLOGIES: Tuple[str, ...] = (
    "one_piece", "top", "bottom_pants", "bottom_skirt", "underwear", "outerwear", "none"
)
VALID_GARMENT_STATES: Tuple[str, ...] = (
    "worn", "loosened", "opened", "lifted", "lowered", "removed", "discarded", "wet_clinging", "torn"
)
VALID_HAND_STATES: Tuple[str, ...] = (
    "free", "one_busy", "both_busy", "supports_body", "restrained", "intense_motion"
)
VALID_PROP_USAGES: Tuple[str, ...] = ("handheld", "worn", "ambient", "body_contact", "furniture")
VALID_EMOTIONS: Tuple[str, ...] = (
    "shy", "seductive", "pleasure", "submissive", "playful", "pain", "fear", "dazed",
    "restrained", "detached", "contrast", "neutral"
)
VALID_GAZES: Tuple[str, ...] = (
    "camera", "away", "down", "up", "side", "over_shoulder", "eyes_closed", "obscured", "neutral"
)
VALID_OCCLUSIONS: Tuple[str, ...] = ("none", "eyes", "face", "lower_body")
VALID_TIMES_OF_DAY: Tuple[str, ...] = ("day", "dawn", "dusk", "night", "neutral")
VALID_LIGHT_SOURCES: Tuple[str, ...] = (
    "daylight", "artificial_warm", "artificial_cool", "neon", "candle", "screen", "studio", "mixed", "neutral"
)
VALID_COLOR_MODES: Tuple[str, ...] = ("color", "monochrome", "sepia", "high_saturation", "neutral")
VALID_CAPTURE_DEVICES: Tuple[str, ...] = ("professional", "phone", "cctv", "digital_camera", "film_camera", "neutral")
VALID_QUALITY_CLASSES: Tuple[str, ...] = ("standard", "high", "masterpiece", "phone", "cctv")
VALID_MAKEUP_BASES: Tuple[str, ...] = ("none", "clean", "natural", "full", "special")
VALID_MAKEUP_EFFECTS: Tuple[str, ...] = ("smudged", "tear_streaked", "wet", "flush", "glitter")
VALID_LIQUID_KINDS: Tuple[str, ...] = ("water", "sweat", "saliva", "oil", "sexual_fluid", "none")
VALID_LIQUID_LOCATIONS: Tuple[str, ...] = ("face", "mouth", "hair", "skin", "torso", "lower_body", "background")
VALID_LIQUID_AMOUNTS: Tuple[str, ...] = ("trace", "light", "normal")


@dataclass(frozen=True)
class SemanticFacts:
    """不可变语义事实契约 (rc8 核心数据模型)。

    所有字段具备安全默认值，所有集合字段使用有序去重 Tuple。
    """
    semantic_role: Optional[str] = None
    space_kind: Optional[str] = None
    venue_ids: Tuple[str, ...] = ()
    visible_regions: Tuple[str, ...] = ()
    garment_topologies: Tuple[str, ...] = ()
    garment_states: Tuple[str, ...] = ()
    hand_state: Optional[str] = None
    prop_usage: Optional[str] = None
    hands_required: int = 0
    emotion: Optional[str] = None
    gaze: Optional[str] = None
    occlusion: Optional[str] = None
    time_of_day: Optional[str] = None
    light_sources: Tuple[str, ...] = ()
    color_modes: Tuple[str, ...] = ()
    capture_device: Optional[str] = None
    quality_class: Optional[str] = None
    makeup_base: Optional[str] = None
    makeup_effects: Tuple[str, ...] = ()
    liquid_kind: Optional[str] = None
    liquid_locations: Tuple[str, ...] = ()
    liquid_amount: Optional[str] = None
    mutex_groups: Tuple[str, ...] = ()
    explicit_fields: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        tuple_fields = (
            "venue_ids", "visible_regions", "garment_topologies", "garment_states",
            "light_sources", "color_modes", "makeup_effects", "liquid_locations", "mutex_groups"
        )
        for tf in tuple_fields:
            val = getattr(self, tf)
            if isinstance(val, str):
                raise TypeError(f"{tf} must be a tuple, list or set of str, got str")
            if val is None:
                object.__setattr__(self, tf, ())
            elif isinstance(val, (list, set, tuple)):
                for x in val:
                    if not isinstance(x, str) or isinstance(x, bool):
                        raise TypeError(f"Elements of {tf} must be str, got {type(x).__name__}")
                object.__setattr__(self, tf, tuple(sorted(set(val))))
            else:
                raise TypeError(f"{tf} must be a tuple, list or set of str, got {type(val).__name__}")

        valid_fields = set(self.__dataclass_fields__.keys()) - {"explicit_fields"}
        if self.explicit_fields is not None:
            if isinstance(self.explicit_fields, str):
                raise TypeError("explicit_fields must be a collection of str, got str")
            if not isinstance(self.explicit_fields, (list, tuple, set)):
                raise TypeError(f"explicit_fields must be a collection of str, got {type(self.explicit_fields).__name__}")
            for x in self.explicit_fields:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of explicit_fields must be str, got {type(x).__name__}")
                if x not in valid_fields:
                    raise ValueError(f"Unknown field '{x}' in explicit_fields")
            object.__setattr__(self, "explicit_fields", tuple(sorted(set(self.explicit_fields))))
        else:
            object.__setattr__(self, "explicit_fields", ())

        if not self.explicit_fields:
            computed = set()
            for f_name in valid_fields:
                v = getattr(self, f_name)
                if f_name == "hands_required":
                    if v != 0:
                        computed.add(f_name)
                elif v is not None and v != ():
                    computed.add(f_name)
            object.__setattr__(self, "explicit_fields", tuple(sorted(computed)))

        hr = self.hands_required
        if hr is not None and (not isinstance(hr, int) or isinstance(hr, bool)):
            raise TypeError(f"hands_required must be an int, got {type(hr).__name__}")
        self.validate()

    def validate(self) -> None:
        """校验事实枚举与数值约束，Fail-Closed。"""
        str_scalars = {
            "semantic_role": VALID_SEMANTIC_ROLES,
            "space_kind": VALID_SPACE_KINDS,
            "hand_state": VALID_HAND_STATES,
            "prop_usage": VALID_PROP_USAGES,
            "emotion": VALID_EMOTIONS,
            "gaze": VALID_GAZES,
            "occlusion": VALID_OCCLUSIONS,
            "time_of_day": VALID_TIMES_OF_DAY,
            "capture_device": VALID_CAPTURE_DEVICES,
            "quality_class": VALID_QUALITY_CLASSES,
            "makeup_base": VALID_MAKEUP_BASES,
            "liquid_kind": VALID_LIQUID_KINDS,
            "liquid_amount": VALID_LIQUID_AMOUNTS,
        }
        for fld, valid_enums in str_scalars.items():
            val = getattr(self, fld)
            if val is not None:
                if not isinstance(val, str) or isinstance(val, bool):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                if val not in valid_enums:
                    raise ValueError(f"Invalid {fld}: {val!r}")
        for vr in self.visible_regions:
            if vr not in VALID_VISIBLE_REGIONS:
                raise ValueError(f"Invalid visible_region: {vr!r}")
        for gt in self.garment_topologies:
            if gt not in VALID_GARMENT_TOPOLOGIES:
                raise ValueError(f"Invalid garment_topology: {gt!r}")
        for gs in self.garment_states:
            if gs not in VALID_GARMENT_STATES:
                raise ValueError(f"Invalid garment_state: {gs!r}")
        if self.hands_required not in (0, 1, 2):
            raise ValueError(f"hands_required must be 0, 1, or 2, got {self.hands_required!r}")
        for ls in self.light_sources:
            if ls not in VALID_LIGHT_SOURCES:
                raise ValueError(f"Invalid light_source: {ls!r}")
        for cm in self.color_modes:
            if cm not in VALID_COLOR_MODES:
                raise ValueError(f"Invalid color_mode: {cm!r}")
        for me in self.makeup_effects:
            if me not in VALID_MAKEUP_EFFECTS:
                raise ValueError(f"Invalid makeup_effect: {me!r}")
        for ll in self.liquid_locations:
            if ll not in VALID_LIQUID_LOCATIONS:
                raise ValueError(f"Invalid liquid_location: {ll!r}")
                raise ValueError(f"Invalid liquid_location: {ll!r}")
        if self.liquid_amount is not None and self.liquid_amount not in VALID_LIQUID_AMOUNTS:
            raise ValueError(f"Invalid liquid_amount: {self.liquid_amount!r}")

        # 校验同一叶子的矛盾事实 (6.6.2 要求)
        if "none" in self.garment_topologies and len(self.garment_topologies) > 1:
            raise ValueError(f"garment_topologies cannot contain 'none' alongside other topologies: {self.garment_topologies!r}")
        if any(s in ("removed", "discarded") for s in self.garment_states) and any(s in ("worn", "loosened") for s in self.garment_states):
            raise ValueError(f"garment_states cannot contain both removed/discarded and worn states: {self.garment_states!r}")
        if self.liquid_kind == "none" and (self.liquid_locations or self.liquid_amount):
            raise ValueError("liquid_kind='none' cannot have liquid_locations or liquid_amount")
        if "monochrome" in self.color_modes and any(c in ("color", "high_saturation") for c in self.color_modes):
            raise ValueError(f"color_modes cannot contain both monochrome and color/high_saturation: {self.color_modes!r}")

    def merge(self, child: Optional[SemanticFacts]) -> SemanticFacts:
        """合并规则：叶子显式值覆盖父项单值，元组字段做有序去重并集。"""
        if child is None:
            return self

        def _merge_val(parent_v, child_v):
            return child_v if child_v is not None else parent_v

        def _merge_tuple(parent_t, child_t):
            combined = set(parent_t or ()).union(set(child_t or ()))
            return tuple(sorted(combined))

        if "hands_required" in child.explicit_fields or child.hands_required != 0:
            hands_req = child.hands_required
        else:
            hands_req = self.hands_required

        merged_explicit = tuple(sorted(set(self.explicit_fields).union(set(child.explicit_fields))))

        merged = SemanticFacts(
            semantic_role=_merge_val(self.semantic_role, child.semantic_role),
            space_kind=_merge_val(self.space_kind, child.space_kind),
            venue_ids=_merge_tuple(self.venue_ids, child.venue_ids),
            visible_regions=_merge_tuple(self.visible_regions, child.visible_regions),
            garment_topologies=_merge_tuple(self.garment_topologies, child.garment_topologies),
            garment_states=_merge_tuple(self.garment_states, child.garment_states),
            hand_state=_merge_val(self.hand_state, child.hand_state),
            prop_usage=_merge_val(self.prop_usage, child.prop_usage),
            hands_required=hands_req,
            emotion=_merge_val(self.emotion, child.emotion),
            gaze=_merge_val(self.gaze, child.gaze),
            occlusion=_merge_val(self.occlusion, child.occlusion),
            time_of_day=_merge_val(self.time_of_day, child.time_of_day),
            light_sources=_merge_tuple(self.light_sources, child.light_sources),
            color_modes=_merge_tuple(self.color_modes, child.color_modes),
            capture_device=_merge_val(self.capture_device, child.capture_device),
            quality_class=_merge_val(self.quality_class, child.quality_class),
            makeup_base=_merge_val(self.makeup_base, child.makeup_base),
            makeup_effects=_merge_tuple(self.makeup_effects, child.makeup_effects),
            liquid_kind=_merge_val(self.liquid_kind, child.liquid_kind),
            liquid_locations=_merge_tuple(self.liquid_locations, child.liquid_locations),
            liquid_amount=_merge_val(self.liquid_amount, child.liquid_amount),
            mutex_groups=_merge_tuple(self.mutex_groups, child.mutex_groups),
            explicit_fields=merged_explicit,
        )
        return merged

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> SemanticFacts:
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"SemanticFacts data must be a dict, got {type(d).__name__}")
        known_fields = set(cls.__dataclass_fields__.keys()) - {"explicit_fields"}
        unexpected = set(d.keys()) - known_fields
        if unexpected:
            raise ValueError(f"Unexpected keys in SemanticFacts: {sorted(unexpected)}")
        cleaned: Dict[str, Any] = {}
        tuple_fields = {
            "venue_ids", "visible_regions", "garment_topologies", "garment_states",
            "light_sources", "color_modes", "makeup_effects", "liquid_locations", "mutex_groups"
        }
        for k, v in d.items():
            if k in tuple_fields:
                if isinstance(v, (list, tuple)):
                    for x in v:
                        if not isinstance(x, str):
                            raise TypeError(f"Elements of {k!r} must be str, got {type(x).__name__}")
                    cleaned[k] = tuple(sorted(set(v)))
                elif v is None:
                    cleaned[k] = ()
                else:
                    raise TypeError(f"Field {k!r} must be a list/tuple of strings, got {type(v).__name__}")
            elif k == "hands_required":
                if not isinstance(v, int) or isinstance(v, bool):
                    raise TypeError(f"hands_required must be an int, got {type(v).__name__}")
                cleaned[k] = v
            else:
                if v is not None and not isinstance(v, str):
                    raise TypeError(f"Field {k!r} must be a string or null, got {type(v).__name__}")
                cleaned[k] = v
        cleaned["explicit_fields"] = tuple(sorted(k for k in d.keys() if k in known_fields))
        inst = cls(**cleaned)
        inst.validate()
        return inst

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for f_name in self.__dataclass_fields__.keys():
            if f_name == "explicit_fields":
                continue
            val = getattr(self, f_name)
            if val is not None and val != () and val != 0:
                res[f_name] = list(val) if isinstance(val, tuple) else val
        return res


VALID_ENTRY_POINTS: Tuple[str, ...] = ("generator", "preset_browser", "custom_combiner", "diagnostics")
VALID_SELECTION_MODES: Tuple[str, ...] = ("none", "random", "auto", "explicit", "preset", "recipe", "custom", "resolver")
FORMAL_ORIGIN_MODES: Tuple[str, ...] = ("random", "auto", "explicit", "preset", "recipe")
VALID_SOURCE_MODES: Tuple[str, ...] = (
    "none", "random", "auto", "explicit", "preset", "recipe", "custom", "user_input"
)

CANONICAL_SLOT_SELECTORS: Tuple[str, ...] = (
    "scene", "theme", "scene_theme", "shot", "shot_type", "camera", "camera_angle",
    "nudity", "clothing", "clothing_state", "hair", "hairstyle", "jewelry", "makeup",
    "pose", "expression", "lighting", "film", "liquids", "tattoo", "props",
    "character", "imperfections", "quality", "style_recipe",
    "预设模板", "风格配方", "场景大类", "剧情主题", "景别构图", "拍摄视角", "裸露等级",
    "服装款式", "服装状态", "发型发色", "饰品头饰", "妆容细节", "姿势动作", "情绪表情",
    "光影预设", "胶片风格", "液体效果", "纹身标记", "道具物件", "角色设定", "真实微瑕", "画质等级",
    "场景主题", "景别视角", "裸露状态", "光影氛围", "表情眼神", "妆容发型", "微瑕细节", "角色体液", "画质修饰", "自定义追加",
    "custom", "resolver"
)

CANONICAL_PRESET_SELECTORS: Tuple[str, ...] = (
    "preset_core", "预设模板", "风格配方", "quality", "画质等级"
)
CANONICAL_RECIPE_SELECTORS: Tuple[str, ...] = (
    "style_recipe", "exposure_mode", "pose_direction", "expression_gaze",
    "lighting_palette", "focus_detail", "makeup_direction"
)

CANONICAL_SELECTORS_BY_ENTRY_POINT: Dict[str, Tuple[str, ...]] = {
    "preset_browser": CANONICAL_PRESET_SELECTORS + CANONICAL_RECIPE_SELECTORS + ("custom",),
    "generator": CANONICAL_PRESET_SELECTORS + CANONICAL_SLOT_SELECTORS + CANONICAL_RECIPE_SELECTORS,
    "diagnostics": CANONICAL_PRESET_SELECTORS + CANONICAL_SLOT_SELECTORS + CANONICAL_RECIPE_SELECTORS,
    "custom_combiner": CANONICAL_SLOT_SELECTORS + CANONICAL_RECIPE_SELECTORS + ("custom",),
}


def _normalize_ordered_str_tuple(val: Any, field_name: str) -> Tuple[str, ...]:
    """规范化保序字符串元组 (实施规格 6.6.2 & 权威顺序契约)：
    - list/tuple: 严格按首次出现保序去重，保留原始权威顺序 (例如 ("b", "a", "b") -> ("b", "a"))；
    - set: 无固有顺序，按确定字典序排序 (保证跨 PYTHONHASHSEED 结果稳定一致)；
    - None: 规范化为空元组 ()；
    - 强类型校验：元素必须为非空 str，拒绝 bool、空串或非 str 元素；输入为纯 str 时拒绝。
    """
    if val is None:
        return ()
    if isinstance(val, str):
        raise TypeError(f"{field_name} must be a collection of str, got str")
    if isinstance(val, (list, tuple)):
        seen = set()
        ordered = []
        for x in val:
            if not isinstance(x, str) or isinstance(x, bool) or not x.strip():
                raise TypeError(f"Elements of {field_name} must be non-empty str, got {x!r}")
            if x not in seen:
                seen.add(x)
                ordered.append(x)
        return tuple(ordered)
    elif isinstance(val, set):
        for x in val:
            if not isinstance(x, str) or isinstance(x, bool) or not x.strip():
                raise TypeError(f"Elements of {field_name} must be non-empty str, got {x!r}")
        return tuple(sorted(val))
    else:
        raise TypeError(f"{field_name} must be a tuple, list or set of str, got {type(val).__name__}")


@dataclass(frozen=True)
class SelectionOrigin:
    """选择项来源追踪模型 (rc8 核心数据模型)。"""
    entry_point: str = "generator"  # generator | preset_browser | custom_combiner | diagnostics
    mode: str = "random"           # none | random | auto | explicit | preset | recipe | custom | resolver
    selector: str = ""             # 规范槽位名或 preset/recipe 字段名
    selected_id: Optional[str] = None
    raw_value: Optional[str] = None
    parent_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entry_point, str) or isinstance(self.entry_point, bool):
            raise TypeError(f"entry_point must be str, got {type(self.entry_point).__name__}")
        if self.entry_point not in VALID_ENTRY_POINTS:
            raise ValueError(f"Invalid entry_point: {self.entry_point!r}")
        if not isinstance(self.mode, str) or isinstance(self.mode, bool):
            raise TypeError(f"mode must be str, got {type(self.mode).__name__}")
        if self.mode not in VALID_SELECTION_MODES:
            raise ValueError(f"Invalid mode: {self.mode!r}")

        if not isinstance(self.selector, str) or isinstance(self.selector, bool):
            raise TypeError(f"selector must be str, got {type(self.selector).__name__}")
        if self.selector:
            valid_selectors = CANONICAL_SELECTORS_BY_ENTRY_POINT.get(self.entry_point)
            if valid_selectors is not None and self.selector not in valid_selectors:
                raise ValueError(f"Invalid selector '{self.selector}' for entry_point '{self.entry_point}'")

        if self.selected_id is not None and (not isinstance(self.selected_id, str) or isinstance(self.selected_id, bool)):
            raise TypeError(f"selected_id must be str or None, got {type(self.selected_id).__name__}")
        if self.raw_value is not None and (not isinstance(self.raw_value, str) or isinstance(self.raw_value, bool)):
            raise TypeError(f"raw_value must be str or None, got {type(self.raw_value).__name__}")

        object.__setattr__(self, "parent_ids", _normalize_ordered_str_tuple(self.parent_ids, "parent_ids"))

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> SelectionOrigin:
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"SelectionOrigin data must be a dict, got {type(d).__name__}")
        known_fields = set(cls.__dataclass_fields__.keys())
        unexpected = set(d.keys()) - known_fields
        if unexpected:
            raise ValueError(f"Unexpected keys in SelectionOrigin: {sorted(unexpected)}")
        p_ids = d.get("parent_ids", ())
        if isinstance(p_ids, (list, tuple)):
            for x in p_ids:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of parent_ids must be str, got {type(x).__name__}")
            p_tuple = tuple(p_ids)
        elif p_ids is None:
            p_tuple = ()
        else:
            raise TypeError(f"parent_ids must be a list/tuple of str, got {type(p_ids).__name__}")
        inst = cls(
            entry_point=d.get("entry_point", "generator"),
            mode=d.get("mode", "random"),
            selector=d.get("selector", ""),
            selected_id=d.get("selected_id"),
            raw_value=d.get("raw_value"),
            parent_ids=p_tuple,
        )
        return inst


@dataclass(frozen=True)
class ContextProfile:
    """情境画像契约 (rc8 多信号情境自洽模型)。"""
    schema_version: str = "1.0"
    weights: Tuple[Tuple[str, float], ...] = ()
    scene_item_id: Optional[str] = None
    theme_id: Optional[str] = None
    scene_context_ids: Tuple[str, ...] = ()
    theme_context_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str) or isinstance(self.schema_version, bool):
            raise TypeError(f"schema_version must be str, got {type(self.schema_version).__name__}")
        if self.schema_version != "1.0":
            raise ValueError(f"ContextProfile schema_version must be '1.0', got '{self.schema_version}'")
        if self.scene_item_id is not None and (not isinstance(self.scene_item_id, str) or isinstance(self.scene_item_id, bool)):
            raise TypeError(f"scene_item_id must be str or None, got {type(self.scene_item_id).__name__}")
        if self.theme_id is not None and (not isinstance(self.theme_id, str) or isinstance(self.theme_id, bool)):
            raise TypeError(f"theme_id must be str or None, got {type(self.theme_id).__name__}")

        w = self.weights
        if not isinstance(w, tuple):
            if isinstance(w, list):
                w_tuple = tuple(tuple(item) if isinstance(item, (list, tuple)) else item for item in w)
                object.__setattr__(self, "weights", w_tuple)
            elif w is None:
                object.__setattr__(self, "weights", ())
            else:
                raise TypeError(f"weights must be a tuple, got {type(w).__name__}")
        for fld in ("scene_context_ids", "theme_context_ids"):
            val = getattr(self, fld)
            if isinstance(val, str):
                raise TypeError(f"{fld} must be a collection of str, got str")
            if val is None:
                object.__setattr__(self, fld, ())
            elif isinstance(val, (list, tuple, set)):
                for x in val:
                    if not isinstance(x, str) or isinstance(x, bool):
                        raise TypeError(f"Elements of {fld} must be str, got {type(x).__name__}")
                    if x not in ORDERED_CONTEXT_IDS:
                        raise ValueError(f"Unknown context ID '{x}' in {fld}")
                object.__setattr__(self, fld, tuple(sorted(set(val))))
            else:
                raise TypeError(f"{fld} must be a collection of str, got {type(val).__name__}")

        if self.weights:
            seen_contexts = set()
            weights_dict: Dict[str, float] = {}
            total = 0.0
            for item in self.weights:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    raise TypeError("Each ContextProfile weight item must be a 2-tuple (context_id, weight)")
                c_id, wt = item
                if not isinstance(c_id, str):
                    raise TypeError(f"context_id must be str, got {type(c_id).__name__}")
                if c_id not in ORDERED_CONTEXT_IDS:
                    raise ValueError(f"Unknown context ID '{c_id}' in ContextProfile weights")
                if c_id in seen_contexts:
                    raise ValueError(f"Duplicate context ID '{c_id}' in ContextProfile weights")
                seen_contexts.add(c_id)
                if isinstance(wt, bool) or not isinstance(wt, (int, float)):
                    raise TypeError(f"ContextProfile weight must be int or float, got {type(wt).__name__}")
                wt_float = float(wt)
                if not math.isfinite(wt_float):
                    raise ValueError(f"ContextProfile weight must be finite, got {wt}")
                if wt_float <= 0:
                    raise ValueError(f"ContextProfile weight must be positive, got {wt}")
                weights_dict[c_id] = wt_float
                total += wt_float

            if abs(total - 1.0) > 1e-12:
                raise ValueError(f"ContextProfile weights sum must equal 1.0 within 1e-12, got {total}")

            sorted_weights = tuple((c, weights_dict[c]) for c in ORDERED_CONTEXT_IDS if c in weights_dict)
            object.__setattr__(self, "weights", sorted_weights)


@dataclass(frozen=True)
class ResolutionDecision:
    """原子级消解决策证据 (rc8 决策可追溯模型)。"""
    decision_id: str
    sequence: int
    rule_id: str
    phase: str
    action: str  # drop | replace | inject
    reason_code: str
    winner_atom_ids: Tuple[str, ...] = ()
    target_atom_id: Optional[str] = None
    produced_atom_ids: Tuple[str, ...] = ()
    before_text: Optional[str] = None
    after_text: Optional[str] = None
    parent_source_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for s_fld in ("decision_id", "rule_id", "phase", "reason_code"):
            s_val = getattr(self, s_fld)
            if not isinstance(s_val, str) or isinstance(s_val, bool):
                raise TypeError(f"{s_fld} must be str, got {type(s_val).__name__}")
            if not s_val.strip():
                raise ValueError(f"{s_fld} cannot be empty")

        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise TypeError(f"sequence must be int, got {type(self.sequence).__name__}")
        if self.sequence < 0:
            raise ValueError(f"sequence must be >= 0, got {self.sequence}")

        if self.action not in ("drop", "replace", "inject"):
            raise ValueError(f"Invalid action: {self.action!r}")

        for fld in ("winner_atom_ids", "produced_atom_ids", "parent_source_ids"):
            val = getattr(self, fld)
            object.__setattr__(self, fld, _normalize_ordered_str_tuple(val, fld))

        if self.action == "drop":
            if not isinstance(self.target_atom_id, str) or isinstance(self.target_atom_id, bool) or not self.target_atom_id.strip():
                raise ValueError("drop decision must have non-empty target_atom_id")
            if not isinstance(self.before_text, str) or isinstance(self.before_text, bool) or not self.before_text.strip():
                raise ValueError("drop decision must have non-empty before_text")
            if self.produced_atom_ids != ():
                raise ValueError("drop decision produced_atom_ids must be empty")
            if self.after_text is not None:
                raise ValueError("drop decision after_text must be None")
        elif self.action == "replace":
            if not isinstance(self.target_atom_id, str) or isinstance(self.target_atom_id, bool) or not self.target_atom_id.strip():
                raise ValueError("replace decision must have non-empty target_atom_id")
            if not isinstance(self.before_text, str) or isinstance(self.before_text, bool) or not self.before_text.strip():
                raise ValueError("replace decision must have non-empty before_text")
            if not self.produced_atom_ids:
                raise ValueError("replace decision must have non-empty produced_atom_ids")
            if not isinstance(self.after_text, str) or isinstance(self.after_text, bool) or not self.after_text.strip():
                raise ValueError("replace decision must have non-empty after_text")
        elif self.action == "inject":
            if self.target_atom_id is not None:
                raise ValueError("inject decision target_atom_id must be None")
            if self.before_text is not None:
                raise ValueError("inject decision before_text must be None")
            if not self.produced_atom_ids:
                raise ValueError("inject decision must have non-empty produced_atom_ids")
            if not isinstance(self.after_text, str) or isinstance(self.after_text, bool) or not self.after_text.strip():
                raise ValueError("inject decision must have non-empty after_text")


def _deep_freeze_unresolved(item: Any) -> Any:
    if isinstance(item, (list, tuple)):
        return tuple(_deep_freeze_unresolved(x) for x in item)
    elif isinstance(item, set):
        return tuple(sorted(_deep_freeze_unresolved(x) for x in item))
    elif isinstance(item, dict):
        raise TypeError("unresolved_conflicts cannot contain mutable dicts")
    return item


@dataclass(frozen=True)
class DeduplicationRecord:
    """去重过滤记录模型 (R3-P1-002: 记录被去重目标、保留原子/标签与去重依据)。"""
    atom_id: str
    retained_atom_id: str
    retained_tag_text: str
    basis: str = "normalized_exact_tag_duplicate"

    def __post_init__(self) -> None:
        for fld in ("atom_id", "retained_atom_id", "retained_tag_text", "basis"):
            val = getattr(self, fld)
            if not isinstance(val, str) or isinstance(val, bool) or not val.strip():
                raise TypeError(f"DeduplicationRecord.{fld} must be non-empty str, got {val!r}")


@dataclass(frozen=True)
class BudgetFilterRecord:
    """词数预算超限过滤记录模型 (R3-P1-002: 记录被过滤目标、上限、当时已用词数/候选成本及原因)。"""
    atom_id: str
    word_budget: int
    used_words: int
    candidate_words: int
    reason: str = "word_budget_exceeded"

    def __post_init__(self) -> None:
        for fld in ("atom_id", "reason"):
            val = getattr(self, fld)
            if not isinstance(val, str) or isinstance(val, bool) or not val.strip():
                raise TypeError(f"BudgetFilterRecord.{fld} must be non-empty str, got {val!r}")
        for fld in ("word_budget", "used_words", "candidate_words"):
            val = getattr(self, fld)
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise TypeError(f"BudgetFilterRecord.{fld} must be non-negative int, got {val!r}")
        if self.reason != "word_budget_exceeded":
            raise ValueError(
                f"BudgetFilterRecord.reason must be 'word_budget_exceeded', got {self.reason!r}"
            )


@dataclass(frozen=True)
class ResolutionReport:
    """完整消解审计报告 (rc8 诊断闭环模型)。"""
    schema_version: str = "1.0"
    input_atom_hash: str = ""
    output_atom_hash: str = ""
    rules_applied: Tuple[str, ...] = ()
    decisions: Tuple[ResolutionDecision, ...] = ()
    unresolved_conflicts: Tuple[Any, ...] = ()
    input_count: int = 0
    output_count: int = 0
    dropped_count: int = 0
    replaced_count: int = 0
    injected_count: int = 0
    produced_atoms: Tuple[PromptAtom, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str) or isinstance(self.schema_version, bool):
            raise TypeError(f"schema_version must be str, got {type(self.schema_version).__name__}")
        if self.schema_version != "1.0":
            raise ValueError(f"ResolutionReport schema_version must be '1.0', got '{self.schema_version}'")

        for str_fld in ("input_atom_hash", "output_atom_hash"):
            val = getattr(self, str_fld)
            if not isinstance(val, str) or isinstance(val, bool):
                raise TypeError(f"{str_fld} must be str, got {type(val).__name__}")

        for cnt_fld in ("input_count", "output_count", "dropped_count", "replaced_count", "injected_count"):
            cnt_val = getattr(self, cnt_fld)
            if not isinstance(cnt_val, int) or isinstance(cnt_val, bool):
                raise TypeError(f"{cnt_fld} must be an int, got {type(cnt_val).__name__}")
            if cnt_val < 0:
                raise ValueError(f"{cnt_fld} must be >= 0, got {cnt_val}")

        # rules_applied: deterministic order-preserving tuple of non-empty strings (保留实际执行顺序)
        object.__setattr__(self, "rules_applied", _normalize_ordered_str_tuple(self.rules_applied, "rules_applied"))

        # decisions: tuple of ResolutionDecision
        d_val = self.decisions
        if d_val is None:
            object.__setattr__(self, "decisions", ())
        elif isinstance(d_val, (list, tuple)):
            for d in d_val:
                if not isinstance(d, ResolutionDecision):
                    raise TypeError(f"Elements of decisions must be ResolutionDecision, got {type(d).__name__}")
            object.__setattr__(self, "decisions", tuple(d_val))
        else:
            raise TypeError(f"decisions must be a tuple or list of ResolutionDecision, got {type(d_val).__name__}")

        # unresolved_conflicts: frozen tuple
        u_val = self.unresolved_conflicts
        if u_val is None:
            object.__setattr__(self, "unresolved_conflicts", ())
        elif isinstance(u_val, (list, tuple)):
            object.__setattr__(self, "unresolved_conflicts", tuple(_deep_freeze_unresolved(x) for x in u_val))
        else:
            raise TypeError(f"unresolved_conflicts must be a tuple or list, got {type(u_val).__name__}")

        # Consistency checks between decisions and counts
        actual_dropped = sum(1 for d in self.decisions if d.action == "drop")
        actual_replaced = sum(1 for d in self.decisions if d.action == "replace")
        actual_injected = sum(1 for d in self.decisions if d.action == "inject")

        if self.dropped_count != actual_dropped:
            raise ValueError(
                f"dropped_count mismatch: report says {self.dropped_count} but found {actual_dropped} drop decisions"
            )
        if self.replaced_count != actual_replaced:
            raise ValueError(
                f"replaced_count mismatch: report says {self.replaced_count} but found {actual_replaced} replace decisions"
            )
        if self.injected_count != actual_injected:
            raise ValueError(
                f"injected_count mismatch: report says {self.injected_count} but found {actual_injected} inject decisions"
            )

        # Verify all decision rule_ids are in rules_applied
        rules_applied_set = set(self.rules_applied)
        for d in self.decisions:
            if d.rule_id not in rules_applied_set:
                raise ValueError(f"Decision rule_id '{d.rule_id}' not found in rules_applied")

        # produced_atoms: tuple of PromptAtom
        p_val = self.produced_atoms
        if p_val is None:
            object.__setattr__(self, "produced_atoms", ())
        elif isinstance(p_val, (list, tuple)):
            for a in p_val:
                if not isinstance(a, PromptAtom):
                    raise TypeError(f"Elements of produced_atoms must be PromptAtom, got {type(a).__name__}")
            object.__setattr__(self, "produced_atoms", tuple(p_val))
        else:
            raise TypeError(f"produced_atoms must be a tuple or list of PromptAtom, got {type(p_val).__name__}")

        # Verify input_count vs output_count relationship
        produced_count = sum(len(d.produced_atom_ids) for d in self.decisions if d.action in ("replace", "inject"))
        expected_output = self.input_count - self.dropped_count - self.replaced_count + produced_count
        if self.output_count != expected_output:
            raise ValueError(
                f"output_count mismatch: expected {expected_output} "
                f"(input {self.input_count} - dropped {self.dropped_count} - replaced {self.replaced_count} + produced {produced_count}), "
                f"got {self.output_count}"
            )


@dataclass(frozen=True)
class TagProvenance:
    """
    跨层通用标签溯源元数据：
    记录数据项 ID、语义标签序列（如 clothing:jk_seifuku, nudity:L2, extension_family:cloth_transparency）、
    标签种类（如 base_clothing, clothing_state, clothing_extension, scene_anchor 等）、
    规则生成 ID (rule_id) 与父来源标识 (parent_ids)。
    """
    item_id: Optional[str] = None
    semantic_ids: Tuple[str, ...] = ()
    kind: Optional[str] = None
    rule_id: Optional[str] = None
    parent_ids: Tuple[str, ...] = ()
    source_mode: Optional[str] = None

    def __post_init__(self) -> None:
        for fld in ("item_id", "kind", "rule_id", "source_mode"):
            val = getattr(self, fld)
            if val is not None and (not isinstance(val, str) or isinstance(val, bool)):
                raise TypeError(f"TagProvenance.{fld} must be str or None, got {type(val).__name__}")

        for fld in ("semantic_ids", "parent_ids"):
            val = getattr(self, fld)
            object.__setattr__(self, fld, _normalize_ordered_str_tuple(val, f"TagProvenance.{fld}"))

        if self.source_mode is not None:
            if not self.source_mode.strip():
                raise ValueError("TagProvenance.source_mode must be a non-empty original source mode")
            if self.source_mode not in VALID_SOURCE_MODES:
                raise ValueError(
                    f"Invalid TagProvenance.source_mode: {self.source_mode!r}; "
                    f"expected one of {VALID_SOURCE_MODES!r}"
                )


@dataclass(frozen=True)
class SampledTag:
    """采样层单标签输出对象，携带真实语义来源。"""
    text: str
    provenance: TagProvenance = field(default_factory=TagProvenance)
    id: str = ""
    facts: SemanticFacts = field(default_factory=SemanticFacts)
    origin: Optional[SelectionOrigin] = None
    role: Optional[str] = None
    raw_lines: Tuple[int, ...] = ()


@dataclass(frozen=True)
class SampleResult:
    """
    采样层结构化返回值：
    包含采样的 tags 元组、稳定数据项 ID、所属上下文标签与互斥空间组。
    """
    tags: Tuple[str, ...]
    item_id: str
    context_ids: Tuple[str, ...] = ()
    exclusive_group: Optional[str] = None
    provenance: TagProvenance = field(default_factory=TagProvenance)
    sampled_tags: Tuple[SampledTag, ...] = ()


@dataclass(frozen=True)
class ThemeSampleResult:
    """
    剧情主题采样层结构化结果，携带真实 TagProvenance。
    """
    tags: Tuple[SampledTag, ...]
    theme_id: str
    provenance: TagProvenance = field(default_factory=TagProvenance)
    context_ids: Tuple[str, ...] = ()

    @property
    def sampled_tags(self) -> Tuple[SampledTag, ...]:
        return self.tags

    @property
    def all_text_tags(self) -> List[str]:
        return [t.text for t in self.tags]


@dataclass(frozen=True)
class ClothingSampleResult:
    """
    服装采样层结构化结果，彻底解耦 DataSampler 与 PromptFragment。
    """
    base_tags: Tuple[SampledTag, ...]
    state_tags: Tuple[SampledTag, ...] = ()
    extension_tags: Tuple[SampledTag, ...] = ()
    style_id: str = ""
    state_id: Optional[str] = None
    nudity_level: str = "L1"

    @property
    def all_tags(self) -> Tuple[SampledTag, ...]:
        return self.base_tags + self.state_tags + self.extension_tags

    @property
    def sampled_tags(self) -> Tuple[SampledTag, ...]:
        return self.all_tags

    @property
    def all_text_tags(self) -> List[str]:
        return [t.text for t in self.all_tags]

    @property
    def tags(self) -> Tuple[str, ...]:
        return tuple(self.all_text_tags)


@dataclass(frozen=True)
class PromptFragment:
    """
    结构化提示词片段：
    在组装、冲突消解、去重与截断全过程中保留语义、来源槽位、稳定条目ID、上下文标签与空间互斥组。
    """
    text: str
    source_slot: str
    source_item_id: Optional[str] = None
    context_ids: Tuple[str, ...] = ()
    exclusive_group: Optional[str] = None
    order: int = 0
    provenance: TagProvenance = field(default_factory=TagProvenance)
    id: str = ""
    facts: SemanticFacts = field(default_factory=SemanticFacts)
    origin: Optional[SelectionOrigin] = None
    target_id: Optional[str] = None


@dataclass(frozen=True)
class PromptAtom:
    """
    生产级原子 Span 数据模型：
    全程在组装、消解、去重与截断流水线中流转，保留完备元数据与保序序号。
    """
    text: str
    span_type: SpanType
    source_slot: str
    source_item_id: Optional[str] = None
    context_ids: Tuple[str, ...] = ()
    exclusive_group: Optional[str] = None
    tag_order: int = 0
    span_order: int = 0
    provenance: TagProvenance = field(default_factory=TagProvenance)
    contains_blackbox: bool = False
    atom_id: str = ""
    facts: SemanticFacts = field(default_factory=SemanticFacts)
    origin: Optional[SelectionOrigin] = None
    id: str = ""
    target_id: Optional[str] = None

    @property
    def can_detect(self) -> bool:
        if self.origin and self.origin.mode in ("preset", "recipe", "explicit") and self.facts and self.facts.explicit_fields:
            return True
        return self.span_type in (SpanType.PLAIN, SpanType.PAREN, SpanType.BRACKET)

    @property
    def can_modify_internal(self) -> bool:
        return self.span_type == SpanType.PLAIN

    @property
    def can_delete_atom(self) -> bool:
        if self.contains_blackbox or self.is_blackbox:
            return False
        return self.span_type in (SpanType.PLAIN, SpanType.PAREN, SpanType.BRACKET)

    @property
    def is_blackbox(self) -> bool:
        return self.span_type in (SpanType.ANGLE, SpanType.QUOTED)


@dataclass(frozen=True)
class GenerationResult:
    """提示词生成结构化结果容器 (修订 7 纯函数输出契约)。

    不变量：
    - 完全不可变对象 (frozen=True)；
    - 包含正向提示词、负向提示词、中文概要；
    - 携带最终采纳的 PromptAtom 序列与本次生成的消解规则命中清单；
    - 包含 source_atoms：流水线初始全量原始 PromptAtom 权威源注册表，供 parent_ids 1:1 闭环追溯。
    """
    positive: str
    negative: str
    description: str
    atoms: Tuple[PromptAtom, ...] = ()
    rules_applied: Tuple[str, ...] = ()
    source_atoms: Tuple[PromptAtom, ...] = ()
    effective_seed: Optional[int] = None
    context_profile: Optional[ContextProfile] = None
    selections: Tuple[SelectionOrigin, ...] = ()
    resolution_report: Optional[ResolutionReport] = None
    deduplicated_atoms: Tuple[PromptAtom, ...] = ()
    budget_filtered_atoms: Tuple[PromptAtom, ...] = ()
    deduplication_records: Tuple[DeduplicationRecord, ...] = ()
    budget_filter_records: Tuple[BudgetFilterRecord, ...] = ()


@dataclass(frozen=True)
class AssemblyResult:
    """流水线统一组装结果 (不可变数据容器)。

    不变量：
    - 完全不可变对象 (frozen=True)；
    - prompt：最终装配完成的提示词文本；
    - accepted_atoms：最终被采纳的 PromptAtom 序列；
    - source_atoms：流水线初始全量原始 PromptAtom 权威源注册表；
    - rules_applied：本次消解触发的应用规则清单；
    - deduplicated_atoms：因保序去重被剔除的原子序列；
    - budget_filtered_atoms：因词数上限预算超限被截断的原子序列；
    - deduplication_records：去重过滤具体记录 (R3-P1-002)；
    - budget_filter_records：预算过滤具体记录 (R3-P1-002)。
    """
    prompt: str
    accepted_atoms: Tuple[PromptAtom, ...] = ()
    source_atoms: Tuple[PromptAtom, ...] = ()
    rules_applied: Tuple[str, ...] = ()
    context_profile: Optional[ContextProfile] = None
    resolution_report: Optional[ResolutionReport] = None
    deduplicated_atoms: Tuple[PromptAtom, ...] = ()
    budget_filtered_atoms: Tuple[PromptAtom, ...] = ()
    deduplication_records: Tuple[DeduplicationRecord, ...] = ()
    budget_filter_records: Tuple[BudgetFilterRecord, ...] = ()
