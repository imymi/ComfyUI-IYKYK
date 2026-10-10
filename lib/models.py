"""
models.py — 提示词结构化数据模型 (PromptFragment, PromptAtom, SampleResult & TagProvenance)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

if __package__:
    from .errors import UnresolvedEnsembleRelationError
else:
    from lib.errors import UnresolvedEnsembleRelationError


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

class _UnspecifiedType:
    _instance: Optional["_UnspecifiedType"] = None

    def __new__(cls) -> "_UnspecifiedType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "UNSPECIFIED"

    def __str__(self) -> str:
        return "unspecified"

    def __eq__(self, other: Any) -> bool:
        return other is self or other == "unspecified"

    def __hash__(self) -> int:
        return hash("UNSPECIFIED")


UNSPECIFIED = _UnspecifiedType()


class _AbsentType:
    _instance: Optional["_AbsentType"] = None

    def __new__(cls) -> "_AbsentType":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "_ABSENT"


_ABSENT = _AbsentType()


VALID_SEMANTIC_ROLES: Tuple[str, ...] = (
    "scene_anchor", "scene_detail", "selector", "effect", "quality", "variant"
)
VALID_SPACE_KINDS: Tuple[str, ...] = (
    "indoor", "outdoor", "semi_open", "subterranean", "mixed", "neutral",
    "indoor_or_outdoor", "outdoor_or_indoor", "indoor_or_semi_open", "unspecified"
)
VALID_VISIBLE_REGIONS: Tuple[str, ...] = (
    "face", "upper_body", "lower_body", "hands", "feet", "full_body", "intimate_lower_body"
)
VALID_GARMENT_TOPOLOGIES: Tuple[str, ...] = (
    "one_piece", "top", "bottom_pants", "bottom_skirt", "underwear", "outerwear", "ensemble_outfit", "none"
)
VALID_GARMENT_STATES: Tuple[str, ...] = (
    "worn", "loosened", "opened", "lifted", "lowered", "removed", "discarded", "wet_clinging", "torn"
)
VALID_HAND_STATES: Tuple[str, ...] = (
    "free", "one_busy", "both_busy", "supports_body", "restrained", "intense_motion", "unspecified"
)
VALID_PROP_USAGES: Tuple[str, ...] = ("handheld", "worn", "ambient", "body_contact", "furniture")
VALID_EMOTIONS: Tuple[str, ...] = (
    "shy", "seductive", "pleasure", "submissive", "playful", "pain", "fear", "dazed",
    "restrained", "detached", "contrast", "neutral",
    "amused", "angry", "anxious", "blissful", "confused", "contemptuous", "disgusted",
    "excited", "focused", "happy", "overwhelmed", "proud", "relaxed", "sad",
    "surprised", "tired", "yearning"
)
VALID_GAZES: Tuple[str, ...] = (
    "camera", "away", "down", "up", "side", "over_shoulder", "eyes_closed", "obscured", "neutral", "intense"
)
VALID_OCCLUSIONS: Tuple[str, ...] = ("none", "eyes", "face", "lower_body")
VALID_TIMES_OF_DAY: Tuple[str, ...] = ("day", "dawn", "dusk", "night", "neutral", "unspecified")
VALID_LIGHT_SOURCES: Tuple[str, ...] = (
    "daylight", "artificial_warm", "artificial_cool", "neon", "candle", "screen", "studio", "mixed", "neutral",
    "flash", "fluorescent", "hmi", "led", "natural", "strobe", "tungsten"
)
VALID_COLOR_MODES: Tuple[str, ...] = ("color", "monochrome", "sepia", "high_saturation", "neutral")
VALID_CAPTURE_DEVICES: Tuple[str, ...] = ("professional", "phone", "cctv", "digital_camera", "film_camera", "neutral")
VALID_QUALITY_CLASSES: Tuple[str, ...] = ("standard", "high", "masterpiece", "phone", "cctv")
VALID_MAKEUP_BASES: Tuple[str, ...] = ("none", "clean", "natural", "full", "special")
VALID_MAKEUP_EFFECTS: Tuple[str, ...] = ("smudged", "tear_streaked", "wet", "flush", "glitter")
VALID_LIQUID_KINDS: Tuple[str, ...] = ("water", "sweat", "saliva", "oil", "sexual_fluid", "none")
VALID_LIQUID_LOCATIONS: Tuple[str, ...] = ("face", "mouth", "hair", "skin", "torso", "lower_body", "background")
VALID_LIQUID_AMOUNTS: Tuple[str, ...] = ("trace", "light", "normal")

VALID_NECKLINES: Tuple[str, ...] = (
    "unspecified", "cowl_neck", "sweetheart", "square_neck", "mock_neck",
    "turtleneck", "halter", "off_shoulder", "v_neck", "boat_neck",
    "mandarin_collar", "sailor_collar", "strapless", "scoop_neck"
)
VALID_SLEEVE_LENGTHS: Tuple[str, ...] = (
    "unspecified", "sleeveless", "detached_sleeves", "long_sleeves",
    "bell_sleeves", "puff_sleeves", "short_sleeves", "cap_sleeves", "three_quarter_sleeves"
)
VALID_HEMLINE_LENGTHS: Tuple[str, ...] = (
    "unspecified", "micro", "mini", "maxi", "knee_length", "tea_length", "floor_length", "ankle_length"
)
VALID_FIT_SILHOUETTES: Tuple[str, ...] = (
    "unspecified", "loose", "form_fitting", "flared_a_line", "pleated", "oversized", "tailored"
)

CUT_FEATURE_VALID_ENUMS: Dict[str, Tuple[str, ...]] = {
    "neckline": VALID_NECKLINES,
    "sleeve_length": VALID_SLEEVE_LENGTHS,
    "hemline_length": VALID_HEMLINE_LENGTHS,
    "fit_silhouette": VALID_FIT_SILHOUETTES,
}

VALID_ENSEMBLE_SLOTS: Tuple[str, ...] = (
    "main_garments", "top_pieces", "bottom_pieces", "outer_layers", "footwear", "accessories", "styling_details"
)

VALID_BINDING_ROLES: Tuple[str, ...] = (
    "standalone", "over", "worn_over", "layered_over", "under", "worn_under", "layered_under",
    "tucked_into", "bloused_into", "cinched_into", "split_over", "paired_with", "coordinated_with"
)

VALID_RELATION_KINDS: Tuple[str, ...] = (
    "worn_over", "worn_under", "tucked_into", "split_over", "coordinated_with"
)

VALID_RELATION_DIRECTIONS: Tuple[str, ...] = (
    "outer_to_inner", "inner_to_outer", "tucked_into", "split_over", "peer_to_peer"
)

VALID_BODY_SUPPORTS: Tuple[str, ...] = (
    "unspecified", "standing", "crouched", "supported_by_surface",
    "sitting", "kneeling", "lying", "aquatic", "airborne", "quadrupedal", "suspended", "neutral"
)

AMBIGUOUS_POSE_SUPPORT_OPTIONS: Dict[str, Tuple[str, ...]] = {
    "ground_or_held": ("ground", "held"),
    "standing_or_crouched": ("crouched", "standing"),
    "standing_or_sitting": ("sitting", "standing"),
    "unspecified": (),
}

VALID_EXTENDED_SPACE_KINDS: Tuple[str, ...] = VALID_SPACE_KINDS
VALID_EXTENDED_TIMES_OF_DAY: Tuple[str, ...] = VALID_TIMES_OF_DAY


@dataclass(frozen=True)
class CutFeatures:
    """服装剪裁与廓形特征模型 (统一入口类型校验与四态保真序列化)。"""
    neckline: Any = field(default=_ABSENT)
    sleeve_length: Any = field(default=_ABSENT)
    hemline_length: Any = field(default=_ABSENT)
    fit_silhouette: Any = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed_fields = ("neckline", "sleeve_length", "hemline_length", "fit_silhouette")
        explicit_set = set()

        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str):
                raise TypeError("explicit_fields must be a collection of str, got str")
            if not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError(f"explicit_fields must be a collection of str, got {type(passed_ef).__name__}")
            for x in passed_ef:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of explicit_fields must be str, got {type(x).__name__}")
                if x not in allowed_fields:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in allowed_fields:
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if isinstance(val, bool) or (not isinstance(val, str) and val is not UNSPECIFIED):
                    raise TypeError(f"{fld} must be str or UNSPECIFIED, got {type(val).__name__}")
                if val == "unspecified" or val is UNSPECIFIED:
                    object.__setattr__(self, fld, UNSPECIFIED)
                else:
                    if val not in CUT_FEATURE_VALID_ENUMS[fld]:
                        raise ValueError(f"Invalid {fld}: {val!r}")
                    object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "CutFeatures":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"CutFeatures data must be a dict, got {type(d).__name__}")
        allowed = {"neckline", "sleeve_length", "hemline_length", "fit_silhouette"}
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in CutFeatures: {sorted(unexpected)}")
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in ("neckline", "sleeve_length", "hemline_length", "fit_silhouette"):
            if fld in self.explicit_fields:
                val = getattr(self, fld)
                res[fld] = "unspecified" if (val is UNSPECIFIED or val == "unspecified") else val
        return res


@dataclass(frozen=True)
class PieceBinding:
    """多件套构件绑定模型 (统一入口类型校验、集合元素防护与四态保真序列化)。"""
    piece_id: Any = field(default=_ABSENT)
    piece_slot: Any = field(default=_ABSENT)
    piece_text: Any = field(default=_ABSENT)
    binding_role: Any = field(default=_ABSENT)
    garment_topology: Any = field(default=_ABSENT)
    cut_features: Any = field(default=_ABSENT)
    fabric_materials: Any = field(default=_ABSENT)
    pattern_textures: Any = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed_fields = {
            "piece_id", "piece_slot", "piece_text", "binding_role",
            "garment_topology", "cut_features", "fabric_materials", "pattern_textures"
        }
        explicit_set = set()

        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str):
                raise TypeError("explicit_fields must be a collection of str, got str")
            if not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError(f"explicit_fields must be a collection of str, got {type(passed_ef).__name__}")
            for x in passed_ef:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of explicit_fields must be str, got {type(x).__name__}")
                if x not in allowed_fields:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        # piece_id
        p_id = self.piece_id
        if p_id is not _ABSENT:
            explicit_set.add("piece_id")
            if not isinstance(p_id, str) or isinstance(p_id, bool):
                raise TypeError(f"piece_id must be str, got {type(p_id).__name__}")
            object.__setattr__(self, "piece_id", p_id)
        else:
            object.__setattr__(self, "piece_id", "")

        # piece_slot
        p_slot = self.piece_slot
        if p_slot is not _ABSENT:
            explicit_set.add("piece_slot")
            if not isinstance(p_slot, str) or isinstance(p_slot, bool):
                raise TypeError(f"piece_slot must be str, got {type(p_slot).__name__}")
            if p_slot not in VALID_ENSEMBLE_SLOTS:
                raise ValueError(f"Invalid piece_slot: {p_slot!r}")
            object.__setattr__(self, "piece_slot", p_slot)
        else:
            object.__setattr__(self, "piece_slot", "")

        # piece_text
        p_text = self.piece_text
        if p_text is not _ABSENT:
            explicit_set.add("piece_text")
            if not isinstance(p_text, str) or isinstance(p_text, bool):
                raise TypeError(f"piece_text must be str, got {type(p_text).__name__}")
            object.__setattr__(self, "piece_text", p_text)
        else:
            object.__setattr__(self, "piece_text", "")

        # binding_role
        b_role = self.binding_role
        if b_role is not _ABSENT:
            explicit_set.add("binding_role")
            if not isinstance(b_role, str) or isinstance(b_role, bool):
                raise TypeError(f"binding_role must be str, got {type(b_role).__name__}")
            if b_role not in VALID_BINDING_ROLES:
                raise ValueError(f"Invalid binding_role: {b_role!r}")
            object.__setattr__(self, "binding_role", b_role)
        else:
            object.__setattr__(self, "binding_role", "standalone")

        # garment_topology
        g_topo = self.garment_topology
        if g_topo is not _ABSENT:
            explicit_set.add("garment_topology")
            if g_topo is not None:
                if not isinstance(g_topo, str) or isinstance(g_topo, bool):
                    raise TypeError(f"garment_topology must be str, got {type(g_topo).__name__}")
                if g_topo not in VALID_GARMENT_TOPOLOGIES:
                    raise ValueError(f"Invalid garment_topology: {g_topo!r}")
                object.__setattr__(self, "garment_topology", g_topo)
            else:
                object.__setattr__(self, "garment_topology", None)
        else:
            object.__setattr__(self, "garment_topology", None)

        # cut_features
        cf = self.cut_features
        if cf is not _ABSENT:
            explicit_set.add("cut_features")
            if isinstance(cf, dict):
                object.__setattr__(self, "cut_features", CutFeatures.from_dict(cf))
            elif isinstance(cf, CutFeatures):
                object.__setattr__(self, "cut_features", cf)
            elif cf is None:
                object.__setattr__(self, "cut_features", None)
            else:
                raise TypeError(f"cut_features must be CutFeatures, dict or None, got {type(cf).__name__}")
        else:
            object.__setattr__(self, "cut_features", None)

        # fabric_materials & pattern_textures
        for fld in ("fabric_materials", "pattern_textures"):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if isinstance(val, str):
                    raise TypeError(f"{fld} must be a list/tuple/set of str, got str")
                if val is None:
                    object.__setattr__(self, fld, None)
                elif isinstance(val, (list, tuple, set, frozenset)):
                    for x in val:
                        if not isinstance(x, str) or isinstance(x, bool):
                            raise TypeError(f"Elements of {fld} must be str, got {type(x).__name__}")
                    object.__setattr__(self, fld, tuple(sorted(set(val))))
                else:
                    raise TypeError(f"{fld} must be a list/tuple/set of str, got {type(val).__name__}")
            else:
                object.__setattr__(self, fld, None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "PieceBinding":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"PieceBinding data must be a dict, got {type(d).__name__}")
        allowed = {
            "piece_id", "piece_slot", "piece_text", "binding_role",
            "garment_topology", "cut_features", "fabric_materials", "pattern_textures"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in PieceBinding: {sorted(unexpected)}")
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in ("piece_id", "piece_slot", "piece_text", "binding_role", "garment_topology"):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        if "cut_features" in self.explicit_fields:
            cf = self.cut_features
            res["cut_features"] = cf.to_dict() if cf else None
        for fld in ("fabric_materials", "pattern_textures"):
            if fld in self.explicit_fields:
                val = getattr(self, fld)
                res[fld] = list(val) if val is not None else None
        return res


@dataclass(frozen=True)
class EnsembleRelation:
    """多件套构件关系三元组 (有向图边)。"""
    relation_kind: str
    source_piece_id: str
    target_piece_id: str
    relation_direction: str

    def __post_init__(self) -> None:
        for fld in ("relation_kind", "source_piece_id", "target_piece_id", "relation_direction"):
            val = getattr(self, fld)
            if not isinstance(val, str) or isinstance(val, bool) or not val.strip():
                raise TypeError(f"EnsembleRelation.{fld} must be non-empty str, got {val!r}")
        if self.relation_kind not in VALID_RELATION_KINDS:
            raise ValueError(f"Invalid relation_kind: {self.relation_kind!r}")
        if self.relation_direction not in VALID_RELATION_DIRECTIONS:
            raise ValueError(f"Invalid relation_direction: {self.relation_direction!r}")
        if self.source_piece_id == self.target_piece_id:
            raise UnresolvedEnsembleRelationError(f"Self-loop relation forbidden: source={self.source_piece_id!r}, target={self.target_piece_id!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relation_kind": self.relation_kind,
            "source_piece_id": self.source_piece_id,
            "target_piece_id": self.target_piece_id,
            "relation_direction": self.relation_direction,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EnsembleRelation":
        if not isinstance(d, dict):
            raise TypeError(f"EnsembleRelation data must be a dict, got {type(d).__name__}")
        allowed = {"relation_kind", "source_piece_id", "target_piece_id", "relation_direction"}
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in EnsembleRelation: {sorted(unexpected)}")
        return cls(
            relation_kind=d["relation_kind"],
            source_piece_id=d["source_piece_id"],
            target_piece_id=d["target_piece_id"],
            relation_direction=d["relation_direction"],
        )


@dataclass(frozen=True)
class EnsemblePieces:
    """多件套结构化构件模型 (含槽位拆分、独立构件绑定、DAG 拓扑关系与环路校验)。"""
    main_garments: Tuple[str, ...] = ()
    top_pieces: Tuple[str, ...] = ()
    bottom_pieces: Tuple[str, ...] = ()
    outer_layers: Tuple[str, ...] = ()
    footwear: Tuple[str, ...] = ()
    accessories: Tuple[str, ...] = ()
    styling_details: Tuple[str, ...] = ()
    piece_bindings: Tuple[PieceBinding, ...] = ()
    relations: Tuple[EnsembleRelation, ...] = ()

    def __post_init__(self) -> None:
        slot_fields = (
            "main_garments", "top_pieces", "bottom_pieces",
            "outer_layers", "footwear", "accessories", "styling_details"
        )
        for sf in slot_fields:
            val = getattr(self, sf)
            if val is not None:
                if isinstance(val, str):
                    raise TypeError(f"{sf} must be a list/tuple of str, got str")
                if isinstance(val, (list, tuple, set)):
                    for x in val:
                        if not isinstance(x, str) or isinstance(x, bool):
                            raise TypeError(f"Elements of {sf} must be str, got {type(x).__name__}")
                    object.__setattr__(self, sf, tuple(val))
                else:
                    raise TypeError(f"{sf} must be a list/tuple of str, got {type(val).__name__}")
            else:
                object.__setattr__(self, sf, ())

        pb_val = self.piece_bindings
        if pb_val is None:
            object.__setattr__(self, "piece_bindings", ())
        elif isinstance(pb_val, (list, tuple)):
            converted_pbs = []
            for pb in pb_val:
                if isinstance(pb, dict):
                    converted_pbs.append(PieceBinding.from_dict(pb))
                elif isinstance(pb, PieceBinding):
                    converted_pbs.append(pb)
                else:
                    raise TypeError(f"piece_bindings elements must be PieceBinding or dict, got {type(pb).__name__}")
            object.__setattr__(self, "piece_bindings", tuple(converted_pbs))
        else:
            raise TypeError(f"piece_bindings must be a list/tuple, got {type(pb_val).__name__}")

        rel_val = self.relations
        if rel_val is None:
            object.__setattr__(self, "relations", ())
        elif isinstance(rel_val, (list, tuple)):
            converted_rels = []
            for rel in rel_val:
                if isinstance(rel, dict):
                    converted_rels.append(EnsembleRelation.from_dict(rel))
                elif isinstance(rel, EnsembleRelation):
                    converted_rels.append(rel)
                else:
                    raise TypeError(f"relations elements must be EnsembleRelation or dict, got {type(rel).__name__}")
            object.__setattr__(self, "relations", tuple(converted_rels))
        else:
            raise TypeError(f"relations must be a list/tuple, got {type(rel_val).__name__}")

        self.validate_dag()

    def validate_dag(self) -> None:
        """验证 relations 构成有向无环图 (DAG)，且无自环、无悬空引用。"""
        piece_ids = {pb.piece_id for pb in self.piece_bindings if pb.piece_id}
        adj: Dict[str, List[str]] = {}
        for rel in self.relations:
            if piece_ids:
                if rel.source_piece_id not in piece_ids:
                    raise UnresolvedEnsembleRelationError(
                        f"Relation source_piece_id {rel.source_piece_id!r} not found in piece_bindings"
                    )
                if rel.target_piece_id not in piece_ids:
                    raise UnresolvedEnsembleRelationError(
                        f"Relation target_piece_id {rel.target_piece_id!r} not found in piece_bindings"
                    )
            adj.setdefault(rel.source_piece_id, []).append(rel.target_piece_id)

        visited: Dict[str, int] = {}
        for node in adj:
            if visited.get(node, 0) == 0:
                stack = [(node, 0)]
                while stack:
                    curr, idx = stack[-1]
                    if visited.get(curr, 0) == 0:
                        visited[curr] = 1
                    children = adj.get(curr, [])
                    if idx < len(children):
                        next_child = children[idx]
                        stack[-1] = (curr, idx + 1)
                        if visited.get(next_child, 0) == 1:
                            raise UnresolvedEnsembleRelationError(
                                f"Cycle detected in ensemble relations involving {curr} -> {next_child}"
                            )
                        elif visited.get(next_child, 0) == 0:
                            stack.append((next_child, 0))
                    else:
                        visited[curr] = 2
                        stack.pop()

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        slot_fields = (
            "main_garments", "top_pieces", "bottom_pieces",
            "outer_layers", "footwear", "accessories", "styling_details"
        )
        for sf in slot_fields:
            val = getattr(self, sf)
            res[sf] = list(val)
        res["piece_bindings"] = [pb.to_dict() for pb in self.piece_bindings]
        if self.relations:
            res["relations"] = [rel.to_dict() for rel in self.relations]
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "EnsemblePieces":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"EnsemblePieces data must be a dict, got {type(d).__name__}")
        allowed = {
            "main_garments", "top_pieces", "bottom_pieces",
            "outer_layers", "footwear", "accessories", "styling_details",
            "piece_bindings", "relations"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in EnsemblePieces: {sorted(unexpected)}")
        return cls(
            main_garments=d.get("main_garments", ()),
            top_pieces=d.get("top_pieces", ()),
            bottom_pieces=d.get("bottom_pieces", ()),
            outer_layers=d.get("outer_layers", ()),
            footwear=d.get("footwear", ()),
            accessories=d.get("accessories", ()),
            styling_details=d.get("styling_details", ()),
            piece_bindings=d.get("piece_bindings", ()),
            relations=d.get("relations", ()),
        )


@dataclass(frozen=True)
class PosePhysicalFacts:
    """姿态物理事实扩展模型 (支持模糊姿态候选集合 support_options 保真)。"""
    body_support: Any = field(default=_ABSENT)
    support_options: Tuple[str, ...] = field(default=_ABSENT)
    hand_state: Any = field(default=_ABSENT)
    hands_required: int = field(default=_ABSENT)
    is_restrained: Any = field(default=_ABSENT)
    restraint_type: Optional[str] = field(default=_ABSENT)
    restrained_body_part: Optional[str] = field(default=_ABSENT)
    role_relationship: Optional[str] = field(default=_ABSENT)
    sitting_orientation: Optional[str] = field(default=_ABSENT)
    leg_state: Optional[str] = field(default=_ABSENT)
    gaze_direction: Optional[str] = field(default=_ABSENT)
    gesture_type: Optional[str] = field(default=_ABSENT)
    limb_position: Optional[str] = field(default=_ABSENT)
    limb_category: Optional[str] = field(default=_ABSENT)
    pose_orientation: Optional[str] = field(default=_ABSENT)
    interlocked_fingers: Optional[bool] = field(default=_ABSENT)
    facial_action: Optional[str] = field(default=_ABSENT)
    interaction_type: Optional[str] = field(default=_ABSENT)
    motion_type: Optional[str] = field(default=_ABSENT)
    posture_group: Optional[str] = field(default=_ABSENT)
    posture_type: Optional[str] = field(default=_ABSENT)
    template_style: Optional[str] = field(default=_ABSENT)
    is_signature_meme: Any = field(default=_ABSENT)
    is_prose_template: Any = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "body_support", "support_options", "hand_state", "hands_required",
            "is_restrained", "restraint_type", "restrained_body_part", "role_relationship",
            "sitting_orientation", "leg_state", "gaze_direction", "gesture_type",
            "limb_position", "limb_category", "pose_orientation", "interlocked_fingers",
            "facial_action", "interaction_type", "motion_type", "posture_group",
            "posture_type", "template_style", "is_signature_meme", "is_prose_template"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str):
                raise TypeError("explicit_fields must be a collection of str, got str")
            if not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError(f"explicit_fields must be a collection of str, got {type(passed_ef).__name__}")
            for x in passed_ef:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of explicit_fields must be str, got {type(x).__name__}")
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        # body_support & support_options
        bs = self.body_support
        so = self.support_options
        if bs is not _ABSENT:
            explicit_set.add("body_support")
            if isinstance(bs, bool) or (not isinstance(bs, str) and bs is not UNSPECIFIED):
                raise TypeError(f"body_support must be str or UNSPECIFIED, got {type(bs).__name__}")
            if bs in AMBIGUOUS_POSE_SUPPORT_OPTIONS:
                object.__setattr__(self, "body_support", UNSPECIFIED)
                if so is _ABSENT:
                    object.__setattr__(self, "support_options", tuple(sorted(AMBIGUOUS_POSE_SUPPORT_OPTIONS[bs])))
                    explicit_set.add("support_options")
            elif bs == "unspecified" or bs is UNSPECIFIED:
                object.__setattr__(self, "body_support", UNSPECIFIED)
                if so is _ABSENT:
                    object.__setattr__(self, "support_options", ())
            else:
                if bs not in VALID_BODY_SUPPORTS:
                    raise ValueError(f"Invalid body_support: {bs!r}")
                object.__setattr__(self, "body_support", bs)
                if so is _ABSENT:
                    object.__setattr__(self, "support_options", (bs,))
        else:
            object.__setattr__(self, "body_support", None)

        if so is not _ABSENT:
            explicit_set.add("support_options")
            if isinstance(so, str):
                raise TypeError("support_options must be a collection of str, got str")
            if not isinstance(so, (list, tuple, set, frozenset)):
                raise TypeError(f"support_options must be a collection of str, got {type(so).__name__}")
            for x in so:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of support_options must be str, got {type(x).__name__}")
            object.__setattr__(self, "support_options", tuple(sorted(set(so))))
        elif self.support_options is _ABSENT:
            object.__setattr__(self, "support_options", ())

        # hands_required
        hr = self.hands_required
        if hr is not _ABSENT:
            explicit_set.add("hands_required")
            if isinstance(hr, bool) or not isinstance(hr, int):
                raise TypeError(f"hands_required must be int, got {type(hr).__name__}")
            if hr not in (0, 1, 2):
                raise ValueError(f"hands_required must be 0, 1, or 2, got {hr}")
            object.__setattr__(self, "hands_required", hr)
        else:
            object.__setattr__(self, "hands_required", 0)

        # is_restrained
        ir = self.is_restrained
        if ir is not _ABSENT:
            explicit_set.add("is_restrained")
            if isinstance(ir, bool):
                object.__setattr__(self, "is_restrained", ir)
            elif isinstance(ir, str):
                if ir == "context_dependent":
                    object.__setattr__(self, "is_restrained", "context_dependent")
                elif ir.lower() in ("true", "false"):
                    object.__setattr__(self, "is_restrained", ir.lower() == "true")
                else:
                    raise ValueError(f"Invalid is_restrained string: {ir!r}")
            else:
                raise TypeError(f"is_restrained must be bool or str, got {type(ir).__name__}")
        else:
            object.__setattr__(self, "is_restrained", False)

        # boolean flags: is_signature_meme, is_prose_template
        for bf in ("is_signature_meme", "is_prose_template"):
            val = getattr(self, bf)
            if val is not _ABSENT:
                explicit_set.add(bf)
                if isinstance(val, bool):
                    object.__setattr__(self, bf, val)
                elif isinstance(val, str) and val.lower() in ("true", "false"):
                    object.__setattr__(self, bf, val.lower() == "true")
                elif val is None:
                    object.__setattr__(self, bf, False)
                else:
                    raise TypeError(f"{bf} must be bool, got {type(val).__name__}")
            else:
                object.__setattr__(self, bf, False)

        # hand_state
        hs = self.hand_state
        if hs is not _ABSENT:
            explicit_set.add("hand_state")
            if hs == "unspecified" or hs is UNSPECIFIED:
                object.__setattr__(self, "hand_state", UNSPECIFIED)
            elif isinstance(hs, str) and not isinstance(hs, bool):
                object.__setattr__(self, "hand_state", hs)
            else:
                raise TypeError(f"hand_state must be str or UNSPECIFIED, got {type(hs).__name__}")
        else:
            object.__setattr__(self, "hand_state", None)

        # Other string fields
        for fld in (
            "restraint_type", "restrained_body_part", "role_relationship",
            "sitting_orientation", "leg_state", "gaze_direction", "gesture_type",
            "limb_position", "limb_category", "pose_orientation", "facial_action",
            "interaction_type", "motion_type", "posture_group", "posture_type",
            "template_style"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and (not isinstance(val, str) or isinstance(val, bool)):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        if self.interlocked_fingers is not _ABSENT:
            explicit_set.add("interlocked_fingers")
            if self.interlocked_fingers is not None and not isinstance(self.interlocked_fingers, bool):
                raise TypeError(f"interlocked_fingers must be bool or None, got {type(self.interlocked_fingers).__name__}")
            object.__setattr__(self, "interlocked_fingers", self.interlocked_fingers)
        else:
            object.__setattr__(self, "interlocked_fingers", None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "hand_state", "hands_required", "is_restrained", "restraint_type",
            "restrained_body_part", "role_relationship", "sitting_orientation",
            "leg_state", "gaze_direction", "gesture_type", "limb_position",
            "limb_category", "pose_orientation", "interlocked_fingers", "facial_action",
            "interaction_type", "motion_type", "posture_group", "posture_type",
            "template_style", "is_signature_meme", "is_prose_template"
        ):
            if fld in self.explicit_fields:
                val = getattr(self, fld)
                res[fld] = "unspecified" if (val is UNSPECIFIED or val == "unspecified") else val
        if "body_support" in self.explicit_fields:
            bs = self.body_support
            res["body_support"] = "unspecified" if (bs is UNSPECIFIED or bs == "unspecified") else bs
        if "support_options" in self.explicit_fields:
            res["support_options"] = list(self.support_options)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "PosePhysicalFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"PosePhysicalFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "body_support", "support_options", "hand_state", "hands_required",
            "is_restrained", "restraint_type", "restrained_body_part", "role_relationship",
            "sitting_orientation", "leg_state", "gaze_direction", "gesture_type",
            "limb_position", "limb_category", "pose_orientation", "interlocked_fingers",
            "facial_action", "interaction_type", "motion_type", "posture_group",
            "posture_type", "template_style", "is_signature_meme", "is_prose_template"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in PosePhysicalFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class SceneEnvironmentalFacts:
    """场景环境事实扩展模型 (支持拓展场所空间及时间四态)。"""
    space_kind: Any = field(default=_ABSENT)
    time_of_day: Any = field(default=_ABSENT)
    venue_category: Optional[str] = field(default=_ABSENT)
    venue_type: Optional[str] = field(default=_ABSENT)
    venue_name: Optional[str] = field(default=_ABSENT)
    extracted_venue: Optional[str] = field(default=_ABSENT)
    setting_genre: Optional[str] = field(default=_ABSENT)
    is_prose_template: bool = field(default=_ABSENT)
    is_composite_event: bool = field(default=_ABSENT)
    embedded_lighting: Optional[str] = field(default=_ABSENT)
    embedded_palette: Optional[str] = field(default=_ABSENT)
    embedded_props: Tuple[str, ...] = field(default=_ABSENT)
    deconstructed_event: Any = field(default=_ABSENT)
    location_type: Optional[str] = field(default=_ABSENT)
    condition: Optional[str] = field(default=_ABSENT)
    decay_state: Optional[str] = field(default=_ABSENT)
    theme: Optional[str] = field(default=_ABSENT)
    baseline_default_time_of_day: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "space_kind", "time_of_day", "venue_category", "venue_type",
            "venue_name", "extracted_venue", "setting_genre", "is_prose_template",
            "is_composite_event", "embedded_lighting", "embedded_palette",
            "embedded_props", "deconstructed_event", "location_type",
            "condition", "decay_state", "theme", "baseline_default_time_of_day"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str):
                raise TypeError("explicit_fields must be a collection of str, got str")
            if not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError(f"explicit_fields must be a collection of str, got {type(passed_ef).__name__}")
            for x in passed_ef:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of explicit_fields must be str, got {type(x).__name__}")
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        # space_kind (four-state)
        sk = self.space_kind
        if sk is not _ABSENT:
            explicit_set.add("space_kind")
            if sk == "unspecified" or sk is UNSPECIFIED:
                object.__setattr__(self, "space_kind", UNSPECIFIED)
            elif isinstance(sk, str) and not isinstance(sk, bool):
                if sk not in VALID_SPACE_KINDS:
                    raise ValueError(f"Invalid space_kind: {sk!r}")
                object.__setattr__(self, "space_kind", sk)
            elif sk is None:
                object.__setattr__(self, "space_kind", None)
            else:
                raise TypeError(f"space_kind must be str or UNSPECIFIED, got {type(sk).__name__}")
        else:
            object.__setattr__(self, "space_kind", None)

        # time_of_day (four-state)
        tod = self.time_of_day
        if tod is not _ABSENT:
            explicit_set.add("time_of_day")
            if tod == "unspecified" or tod is UNSPECIFIED:
                object.__setattr__(self, "time_of_day", UNSPECIFIED)
            elif isinstance(tod, str) and not isinstance(tod, bool):
                if tod not in VALID_TIMES_OF_DAY:
                    raise ValueError(f"Invalid time_of_day: {tod!r}")
                object.__setattr__(self, "time_of_day", tod)
            elif tod is None:
                object.__setattr__(self, "time_of_day", None)
            else:
                raise TypeError(f"time_of_day must be str or UNSPECIFIED, got {type(tod).__name__}")
        else:
            object.__setattr__(self, "time_of_day", None)

        # boolean flags
        for bf in ("is_prose_template", "is_composite_event"):
            val = getattr(self, bf)
            if val is not _ABSENT:
                explicit_set.add(bf)
                if not isinstance(val, bool):
                    raise TypeError(f"{bf} must be bool, got {type(val).__name__}")
                object.__setattr__(self, bf, val)
            else:
                object.__setattr__(self, bf, False)

        # embedded_props
        ep = self.embedded_props
        if ep is not _ABSENT:
            explicit_set.add("embedded_props")
            if isinstance(ep, str):
                raise TypeError("embedded_props must be a collection of str, got str")
            if ep is None:
                object.__setattr__(self, "embedded_props", ())
            elif isinstance(ep, (list, tuple, set, frozenset)):
                for x in ep:
                    if not isinstance(x, str) or isinstance(x, bool):
                        raise TypeError(f"Elements of embedded_props must be str, got {type(x).__name__}")
                object.__setattr__(self, "embedded_props", tuple(sorted(set(ep))))
            else:
                raise TypeError(f"embedded_props must be a collection of str, got {type(ep).__name__}")
        else:
            object.__setattr__(self, "embedded_props", ())

        # deconstructed_event
        de = self.deconstructed_event
        if de is not _ABSENT:
            explicit_set.add("deconstructed_event")
            if de is not None and (not isinstance(de, (str, dict)) or isinstance(de, bool)):
                raise TypeError(f"deconstructed_event must be str, dict, or None, got {type(de).__name__}")
            object.__setattr__(self, "deconstructed_event", de)
        else:
            object.__setattr__(self, "deconstructed_event", None)

        # Other string fields
        for fld in (
            "venue_category", "venue_type", "venue_name", "extracted_venue",
            "setting_genre", "embedded_lighting", "embedded_palette",
            "location_type", "condition", "decay_state", "theme",
            "baseline_default_time_of_day"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and (not isinstance(val, str) or isinstance(val, bool)):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "venue_category", "venue_type", "venue_name", "extracted_venue",
            "setting_genre", "is_prose_template", "is_composite_event",
            "embedded_lighting", "embedded_palette", "deconstructed_event",
            "location_type", "condition", "decay_state", "theme",
            "baseline_default_time_of_day"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        if "space_kind" in self.explicit_fields:
            sk = self.space_kind
            res["space_kind"] = "unspecified" if (sk is UNSPECIFIED or sk == "unspecified") else sk
        if "time_of_day" in self.explicit_fields:
            tod = self.time_of_day
            res["time_of_day"] = "unspecified" if (tod is UNSPECIFIED or tod == "unspecified") else tod
        if "embedded_props" in self.explicit_fields:
            res["embedded_props"] = list(self.embedded_props)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SceneEnvironmentalFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"SceneEnvironmentalFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "space_kind", "time_of_day", "venue_category", "venue_type",
            "venue_name", "extracted_venue", "setting_genre", "is_prose_template",
            "is_composite_event", "embedded_lighting", "embedded_palette",
            "embedded_props", "deconstructed_event", "location_type",
            "condition", "decay_state", "theme", "baseline_default_time_of_day"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in SceneEnvironmentalFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class CameraHardwareFacts:
    """相机硬件与胶片规格事实扩展模型。"""
    device_category: Optional[str] = field(default=_ABSENT)
    device_type: Optional[str] = field(default=_ABSENT)
    film_name: Optional[str] = field(default=_ABSENT)
    manufacturer_brand: Optional[str] = field(default=_ABSENT)
    camera_brand: Optional[str] = field(default=_ABSENT)
    camera_model: Optional[str] = field(default=_ABSENT)
    lens_spec: Optional[str] = field(default=_ABSENT)
    lens_mount: Optional[str] = field(default=_ABSENT)
    measured_iso: Optional[int] = field(default=_ABSENT)
    recommended_ei: Optional[int] = field(default=_ABSENT)
    nominal_name_rating: Optional[int] = field(default=_ABSENT)
    iso_display: Optional[str] = field(default=_ABSENT)
    iso_spec_note: Optional[str] = field(default=_ABSENT)
    emulsion_type: Optional[str] = field(default=_ABSENT)
    developing_process: Optional[str] = field(default=_ABSENT)
    is_camera_film: Any = field(default=_ABSENT)
    sensor_format: Optional[str] = field(default=_ABSENT)
    color_mode: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "device_category", "device_type", "film_name", "manufacturer_brand",
            "camera_brand", "camera_model", "lens_spec", "lens_mount",
            "measured_iso", "recommended_ei", "nominal_name_rating", "iso_display",
            "iso_spec_note", "emulsion_type", "developing_process", "is_camera_film",
            "sensor_format", "color_mode"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in (
            "device_category", "device_type", "film_name", "manufacturer_brand",
            "camera_brand", "camera_model", "lens_spec", "lens_mount",
            "iso_display", "iso_spec_note", "emulsion_type", "developing_process",
            "sensor_format", "color_mode"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        for int_fld in ("measured_iso", "recommended_ei", "nominal_name_rating"):
            val = getattr(self, int_fld)
            if val is not _ABSENT:
                explicit_set.add(int_fld)
                if val is not None and (isinstance(val, bool) or not isinstance(val, int)):
                    raise TypeError(f"{int_fld} must be int or None, got {type(val).__name__}")
                object.__setattr__(self, int_fld, val)
            else:
                object.__setattr__(self, int_fld, None)

        if self.is_camera_film is not _ABSENT:
            explicit_set.add("is_camera_film")
            val = self.is_camera_film
            if isinstance(val, bool):
                object.__setattr__(self, "is_camera_film", val)
            elif isinstance(val, str) and val.lower() in ("true", "false"):
                object.__setattr__(self, "is_camera_film", val.lower() == "true")
            elif val is None:
                object.__setattr__(self, "is_camera_film", False)
            else:
                raise TypeError(f"is_camera_film must be bool, got {type(val).__name__}")
        else:
            object.__setattr__(self, "is_camera_film", False)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "device_category", "device_type", "film_name", "manufacturer_brand",
            "camera_brand", "camera_model", "lens_spec", "lens_mount",
            "measured_iso", "recommended_ei", "nominal_name_rating", "iso_display",
            "iso_spec_note", "emulsion_type", "developing_process", "is_camera_film",
            "sensor_format", "color_mode"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "CameraHardwareFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"CameraHardwareFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "device_category", "device_type", "film_name", "manufacturer_brand",
            "camera_brand", "camera_model", "lens_spec", "lens_mount",
            "measured_iso", "recommended_ei", "nominal_name_rating", "iso_display",
            "iso_spec_note", "emulsion_type", "developing_process", "is_camera_film",
            "sensor_format", "color_mode"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in CameraHardwareFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class HairAttributeFacts:
    """发型与毛发属性事实扩展模型。"""
    hair_category: Optional[str] = field(default=_ABSENT)
    hair_feature: Optional[str] = field(default=_ABSENT)
    hair_part: Optional[str] = field(default=_ABSENT)
    hairstyle: Optional[str] = field(default=_ABSENT)
    parting: Optional[str] = field(default=_ABSENT)
    shaved_level: Optional[str] = field(default=_ABSENT)
    stubble: Optional[str] = field(default=_ABSENT)
    color_group: Optional[str] = field(default=_ABSENT)
    dye_technique: Optional[str] = field(default=_ABSENT)
    volume: Optional[str] = field(default=_ABSENT)
    ends: Optional[str] = field(default=_ABSENT)
    tapered: Any = field(default=_ABSENT)
    length: Optional[str] = field(default=_ABSENT)
    length_tier: Optional[str] = field(default=_ABSENT)
    lengths: Optional[str] = field(default=_ABSENT)
    ears: Optional[str] = field(default=_ABSENT)
    bangs: Optional[str] = field(default=_ABSENT)
    styling: Optional[str] = field(default=_ABSENT)
    texture_type: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "hair_category", "hair_feature", "hair_part", "hairstyle",
            "parting", "shaved_level", "stubble", "color_group",
            "dye_technique", "volume", "ends", "tapered", "length",
            "length_tier", "lengths", "ears", "bangs", "styling",
            "texture_type"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in (
            "hair_category", "hair_feature", "hair_part", "hairstyle",
            "parting", "shaved_level", "stubble", "color_group",
            "dye_technique", "volume", "ends", "length",
            "length_tier", "lengths", "ears", "bangs", "styling",
            "texture_type"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        if self.tapered is not _ABSENT:
            explicit_set.add("tapered")
            val = self.tapered
            if isinstance(val, bool):
                object.__setattr__(self, "tapered", val)
            elif isinstance(val, str) and val.lower() in ("true", "false"):
                object.__setattr__(self, "tapered", val.lower() == "true")
            elif val is None:
                object.__setattr__(self, "tapered", False)
            else:
                raise TypeError(f"tapered must be bool, got {type(val).__name__}")
        else:
            object.__setattr__(self, "tapered", False)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "hair_category", "hair_feature", "hair_part", "hairstyle",
            "parting", "shaved_level", "stubble", "color_group",
            "dye_technique", "volume", "ends", "tapered", "length",
            "length_tier", "lengths", "ears", "bangs", "styling",
            "texture_type"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "HairAttributeFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"HairAttributeFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "hair_category", "hair_feature", "hair_part", "hairstyle",
            "parting", "shaved_level", "stubble", "color_group",
            "dye_technique", "volume", "ends", "tapered", "length",
            "length_tier", "lengths", "ears", "bangs", "styling",
            "texture_type"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in HairAttributeFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class LightingFacts:
    """光照与布光属性事实扩展模型。"""
    light_source: Optional[str] = field(default=_ABSENT)
    light_type: Optional[str] = field(default=_ABSENT)
    light_role: Optional[str] = field(default=_ABSENT)
    key_type: Optional[str] = field(default=_ABSENT)
    setup: Optional[str] = field(default=_ABSENT)
    shadow: Optional[str] = field(default=_ABSENT)
    source_in_scene: Any = field(default=_ABSENT)
    glow: Optional[str] = field(default=_ABSENT)
    lighting: Optional[str] = field(default=_ABSENT)
    mood: Optional[str] = field(default=_ABSENT)
    style: Optional[str] = field(default=_ABSENT)
    property: Optional[str] = field(default=_ABSENT)
    effect: Optional[str] = field(default=_ABSENT)
    time: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "light_source", "light_type", "light_role", "key_type",
            "setup", "shadow", "source_in_scene", "glow", "lighting",
            "mood", "style", "property", "effect", "time"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in (
            "light_source", "light_type", "light_role", "key_type",
            "setup", "shadow", "glow", "lighting",
            "mood", "style", "property", "effect", "time"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        if self.source_in_scene is not _ABSENT:
            explicit_set.add("source_in_scene")
            val = self.source_in_scene
            if isinstance(val, bool):
                object.__setattr__(self, "source_in_scene", val)
            elif isinstance(val, str) and val.lower() in ("true", "false"):
                object.__setattr__(self, "source_in_scene", val.lower() == "true")
            elif val is None:
                object.__setattr__(self, "source_in_scene", False)
            else:
                raise TypeError(f"source_in_scene must be bool, got {type(val).__name__}")
        else:
            object.__setattr__(self, "source_in_scene", False)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "light_source", "light_type", "light_role", "key_type",
            "setup", "shadow", "source_in_scene", "glow", "lighting",
            "mood", "style", "property", "effect", "time"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "LightingFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"LightingFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "light_source", "light_type", "light_role", "key_type",
            "setup", "shadow", "source_in_scene", "glow", "lighting",
            "mood", "style", "property", "effect", "time"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in LightingFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class ShotCompositionFacts:
    """镜头构图与运镜属性事实扩展模型。"""
    shot_size: Optional[str] = field(default=_ABSENT)
    angle_name: Optional[str] = field(default=_ABSENT)
    angle_type: Optional[str] = field(default=_ABSENT)
    axis: Optional[str] = field(default=_ABSENT)
    view_axis: Optional[str] = field(default=_ABSENT)
    perspective: Optional[str] = field(default=_ABSENT)
    direction: Optional[str] = field(default=_ABSENT)
    orientation: Optional[str] = field(default=_ABSENT)
    distance: Optional[str] = field(default=_ABSENT)
    distortion: Optional[str] = field(default=_ABSENT)
    focus: Optional[str] = field(default=_ABSENT)
    framing: Optional[str] = field(default=_ABSENT)
    crop_style: Optional[str] = field(default=_ABSENT)
    composition: Optional[str] = field(default=_ABSENT)
    height_tier: Optional[str] = field(default=_ABSENT)
    layer: Optional[str] = field(default=_ABSENT)
    lens_type: Optional[str] = field(default=_ABSENT)
    mood: Optional[str] = field(default=_ABSENT)
    genre: Optional[str] = field(default=_ABSENT)
    motion_type: Optional[str] = field(default=_ABSENT)
    effect: Optional[str] = field(default=_ABSENT)
    element: Optional[str] = field(default=_ABSENT)
    edge: Optional[str] = field(default=_ABSENT)
    dimension: Optional[str] = field(default=_ABSENT)
    function: Optional[str] = field(default=_ABSENT)
    placement: Optional[str] = field(default=_ABSENT)
    reveal: Optional[str] = field(default=_ABSENT)
    rule: Optional[str] = field(default=_ABSENT)
    stability: Optional[str] = field(default=_ABSENT)
    pose_orientation: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "shot_size", "angle_name", "angle_type", "axis", "view_axis",
            "perspective", "direction", "orientation", "distance",
            "distortion", "focus", "framing", "crop_style", "composition",
            "height_tier", "layer", "lens_type", "mood", "genre",
            "motion_type", "effect", "element", "edge", "dimension",
            "function", "placement", "reveal", "rule", "stability",
            "pose_orientation"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in (
            "shot_size", "angle_name", "angle_type", "axis", "view_axis",
            "perspective", "direction", "orientation", "distance",
            "distortion", "focus", "framing", "crop_style", "composition",
            "height_tier", "layer", "lens_type", "mood", "genre",
            "motion_type", "effect", "element", "edge", "dimension",
            "function", "placement", "reveal", "rule", "stability",
            "pose_orientation"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "shot_size", "angle_name", "angle_type", "axis", "view_axis",
            "perspective", "direction", "orientation", "distance",
            "distortion", "focus", "framing", "crop_style", "composition",
            "height_tier", "layer", "lens_type", "mood", "genre",
            "motion_type", "effect", "element", "edge", "dimension",
            "function", "placement", "reveal", "rule", "stability",
            "pose_orientation"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ShotCompositionFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"ShotCompositionFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "shot_size", "angle_name", "angle_type", "axis", "view_axis",
            "perspective", "direction", "orientation", "distance",
            "distortion", "focus", "framing", "crop_style", "composition",
            "height_tier", "layer", "lens_type", "mood", "genre",
            "motion_type", "effect", "element", "edge", "dimension",
            "function", "placement", "reveal", "rule", "stability",
            "pose_orientation"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in ShotCompositionFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class ClothingAttributeFacts:
    """服装细分属性事实扩展模型。"""
    clothing_slot: Optional[str] = field(default=_ABSENT)
    asymmetric: Any = field(default=_ABSENT)
    coverage: Optional[str] = field(default=_ABSENT)
    height: Optional[str] = field(default=_ABSENT)
    layers: Tuple[str, ...] = field(default=_ABSENT)
    material: Optional[str] = field(default=_ABSENT)
    core_base_tags: Tuple[str, ...] = field(default=_ABSENT)
    hemline: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "clothing_slot", "asymmetric", "coverage", "height",
            "layers", "material", "core_base_tags", "hemline"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in ("clothing_slot", "coverage", "height", "material", "hemline"):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        if self.asymmetric is not _ABSENT:
            explicit_set.add("asymmetric")
            val = self.asymmetric
            if isinstance(val, bool):
                object.__setattr__(self, "asymmetric", val)
            elif isinstance(val, str) and val.lower() in ("true", "false"):
                object.__setattr__(self, "asymmetric", val.lower() == "true")
            elif val is None:
                object.__setattr__(self, "asymmetric", False)
            else:
                raise TypeError(f"asymmetric must be bool, got {type(val).__name__}")
        else:
            object.__setattr__(self, "asymmetric", False)

        for tf in ("layers", "core_base_tags"):
            val = getattr(self, tf)
            if val is not _ABSENT:
                explicit_set.add(tf)
                if val is None:
                    object.__setattr__(self, tf, ())
                elif isinstance(val, (list, tuple, set, frozenset)):
                    for x in val:
                        if not isinstance(x, str) or isinstance(x, bool):
                            raise TypeError(f"Elements of {tf} must be str, got {type(x).__name__}")
                    object.__setattr__(self, tf, tuple(sorted(set(val))))
                else:
                    raise TypeError(f"{tf} must be a collection of str, got {type(val).__name__}")
            else:
                object.__setattr__(self, tf, ())

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in ("clothing_slot", "asymmetric", "coverage", "height", "material", "hemline"):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        for tf in ("layers", "core_base_tags"):
            if tf in self.explicit_fields:
                res[tf] = list(getattr(self, tf))
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ClothingAttributeFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"ClothingAttributeFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "clothing_slot", "asymmetric", "coverage", "height",
            "layers", "material", "core_base_tags", "hemline"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in ClothingAttributeFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class ExpressionFacts:
    """表情与面部动作属性事实扩展模型。"""
    emotion: Optional[str] = field(default=_ABSENT)
    emotion_tendency: Optional[str] = field(default=_ABSENT)
    arousal_level: Optional[str] = field(default=_ABSENT)
    gaze: Optional[str] = field(default=_ABSENT)
    intensity: Optional[str] = field(default=_ABSENT)
    facial_action: Optional[str] = field(default=_ABSENT)
    state: Optional[str] = field(default=_ABSENT)
    tears: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "emotion", "emotion_tendency", "arousal_level", "gaze",
            "intensity", "facial_action", "state", "tears"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in (
            "emotion", "emotion_tendency", "arousal_level", "gaze",
            "intensity", "facial_action", "state", "tears"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "emotion", "emotion_tendency", "arousal_level", "gaze",
            "intensity", "facial_action", "state", "tears"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ExpressionFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"ExpressionFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "emotion", "emotion_tendency", "arousal_level", "gaze",
            "intensity", "facial_action", "state", "tears"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in ExpressionFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class AccessoryAttributeFacts:
    """配饰与挂件属性事实扩展模型。"""
    ornament: Optional[str] = field(default=_ABSENT)
    pendant: Optional[str] = field(default=_ABSENT)
    motif: Optional[str] = field(default=_ABSENT)
    surface: Optional[str] = field(default=_ABSENT)
    shape: Optional[str] = field(default=_ABSENT)
    silhouette: Optional[str] = field(default=_ABSENT)
    position: Optional[str] = field(default=_ABSENT)
    action_type: Optional[str] = field(default=_ABSENT)
    body_part: Optional[str] = field(default=_ABSENT)
    dynamic_slots: Tuple[str, ...] = field(default=_ABSENT)
    material: Optional[str] = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed = {
            "ornament", "pendant", "motif", "surface", "shape",
            "silhouette", "position", "action_type", "body_part",
            "dynamic_slots", "material"
        }
        explicit_set = set()
        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str) or not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError("explicit_fields must be a collection of str")
            for x in passed_ef:
                if x not in allowed:
                    raise KeyError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        for fld in (
            "ornament", "pendant", "motif", "surface", "shape",
            "silhouette", "position", "action_type", "body_part", "material"
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None and not isinstance(val, str):
                    raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                object.__setattr__(self, fld, val)
            else:
                object.__setattr__(self, fld, None)

        val_ds = self.dynamic_slots
        if val_ds is not _ABSENT:
            explicit_set.add("dynamic_slots")
            if val_ds is None:
                object.__setattr__(self, "dynamic_slots", ())
            elif isinstance(val_ds, (list, tuple, set, frozenset)):
                for x in val_ds:
                    if not isinstance(x, (str, dict)) or isinstance(x, bool):
                        raise TypeError(f"Elements of dynamic_slots must be str or dict, got {type(x).__name__}")
                object.__setattr__(self, "dynamic_slots", tuple(val_ds))
            else:
                raise TypeError(f"dynamic_slots must be a collection of str or dict, got {type(val_ds).__name__}")
        else:
            object.__setattr__(self, "dynamic_slots", ())

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for fld in (
            "ornament", "pendant", "motif", "surface", "shape",
            "silhouette", "position", "action_type", "body_part", "material"
        ):
            if fld in self.explicit_fields:
                res[fld] = getattr(self, fld)
        if "dynamic_slots" in self.explicit_fields:
            res["dynamic_slots"] = list(self.dynamic_slots)
        return res

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "AccessoryAttributeFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"AccessoryAttributeFacts data must be a dict, got {type(d).__name__}")
        allowed = {
            "ornament", "pendant", "motif", "surface", "shape",
            "silhouette", "position", "action_type", "body_part",
            "dynamic_slots", "material"
        }
        unexpected = set(d.keys()) - allowed
        if unexpected:
            raise KeyError(f"Unexpected keys in AccessoryAttributeFacts: {sorted(unexpected)}")
        return cls(**d)


@dataclass(frozen=True)
class SemanticFacts:
    """不可变语义事实契约 (rc8/rc10 核心数据模型)。

    所有字段具备安全默认值，所有集合字段使用有序去重 Tuple。
    支持四态层级继承与往返保真序列化 (ABSENT, EXPLICIT_UNSPECIFIED, EXPLICIT_EMPTY, EXPLICIT_VALUE)。
    统一入口强校验防绕过，无论通过直接构造函数还是 from_dict 均执行完全一致的类型与枚举校验。
    """
    semantic_role: Any = field(default=_ABSENT)
    space_kind: Any = field(default=_ABSENT)
    venue_ids: Any = field(default=_ABSENT)
    visible_regions: Any = field(default=_ABSENT)
    garment_topologies: Any = field(default=_ABSENT)
    garment_states: Any = field(default=_ABSENT)
    hand_state: Any = field(default=_ABSENT)
    prop_usage: Any = field(default=_ABSENT)
    hands_required: Any = field(default=_ABSENT)
    emotion: Any = field(default=_ABSENT)
    gaze: Any = field(default=_ABSENT)
    occlusion: Any = field(default=_ABSENT)
    time_of_day: Any = field(default=_ABSENT)
    light_sources: Any = field(default=_ABSENT)
    color_modes: Any = field(default=_ABSENT)
    capture_device: Any = field(default=_ABSENT)
    quality_class: Any = field(default=_ABSENT)
    makeup_base: Any = field(default=_ABSENT)
    makeup_effects: Any = field(default=_ABSENT)
    liquid_kind: Any = field(default=_ABSENT)
    liquid_locations: Any = field(default=_ABSENT)
    liquid_amount: Any = field(default=_ABSENT)
    mutex_groups: Any = field(default=_ABSENT)
    is_ensemble: Any = field(default=_ABSENT)
    style_genre: Any = field(default=_ABSENT)
    fabric_materials: Any = field(default=_ABSENT)
    pattern_textures: Any = field(default=_ABSENT)
    incompatible_with: Any = field(default=_ABSENT)
    cut_features: Any = field(default=_ABSENT)
    ensemble_pieces: Any = field(default=_ABSENT)
    pose_facts: Any = field(default=_ABSENT)
    scene_facts: Any = field(default=_ABSENT)
    camera_facts: Any = field(default=_ABSENT)
    hair_facts: Any = field(default=_ABSENT)
    lighting_facts: Any = field(default=_ABSENT)
    shot_facts: Any = field(default=_ABSENT)
    clothing_facts: Any = field(default=_ABSENT)
    expression_facts: Any = field(default=_ABSENT)
    accessory_facts: Any = field(default=_ABSENT)
    governance_metadata: Any = field(default=_ABSENT)
    explicit_fields: Tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        allowed_fields = set(self.__dataclass_fields__.keys()) - {"explicit_fields"}
        explicit_set = set()

        passed_ef = self.explicit_fields
        if passed_ef:
            if isinstance(passed_ef, str):
                raise TypeError("explicit_fields must be a collection of str, got str")
            if not isinstance(passed_ef, (list, tuple, set, frozenset)):
                raise TypeError(f"explicit_fields must be a collection of str, got {type(passed_ef).__name__}")
            for x in passed_ef:
                if not isinstance(x, str) or isinstance(x, bool):
                    raise TypeError(f"Elements of explicit_fields must be str, got {type(x).__name__}")
                if x not in allowed_fields:
                    raise ValueError(f"Unknown field {x!r} in explicit_fields")
                explicit_set.add(x)

        # 1. 嵌套模型校验与强类型归一化 (防绕过核心)
        submodel_factories = {
            "cut_features": (CutFeatures, CutFeatures.from_dict),
            "ensemble_pieces": (EnsemblePieces, EnsemblePieces.from_dict),
            "pose_facts": (PosePhysicalFacts, PosePhysicalFacts.from_dict),
            "scene_facts": (SceneEnvironmentalFacts, SceneEnvironmentalFacts.from_dict),
            "camera_facts": (CameraHardwareFacts, CameraHardwareFacts.from_dict),
            "hair_facts": (HairAttributeFacts, HairAttributeFacts.from_dict),
            "lighting_facts": (LightingFacts, LightingFacts.from_dict),
            "shot_facts": (ShotCompositionFacts, ShotCompositionFacts.from_dict),
            "clothing_facts": (ClothingAttributeFacts, ClothingAttributeFacts.from_dict),
            "expression_facts": (ExpressionFacts, ExpressionFacts.from_dict),
            "accessory_facts": (AccessoryAttributeFacts, AccessoryAttributeFacts.from_dict),
        }
        for sm_name, (cls_type, factory) in submodel_factories.items():
            val = getattr(self, sm_name)
            if val is not _ABSENT:
                explicit_set.add(sm_name)
                if isinstance(val, cls_type):
                    object.__setattr__(self, sm_name, val)
                elif val is None:
                    object.__setattr__(self, sm_name, None)
                else:
                    raise TypeError(f"{sm_name} must be {cls_type.__name__} or None, got {type(val).__name__}")
            else:
                object.__setattr__(self, sm_name, None)

        # 2. 治理元数据
        gov_val = self.governance_metadata
        if gov_val is not _ABSENT:
            explicit_set.add("governance_metadata")
            if isinstance(gov_val, dict):
                object.__setattr__(self, "governance_metadata", dict(gov_val))
            elif gov_val is None:
                object.__setattr__(self, "governance_metadata", {})
            else:
                raise TypeError(f"governance_metadata must be dict or None, got {type(gov_val).__name__}")
        else:
            object.__setattr__(self, "governance_metadata", {})

        # 3. Tuple 集合字段强类型与有序去重归一化
        tuple_fields = (
            "venue_ids", "visible_regions", "garment_topologies", "garment_states",
            "light_sources", "color_modes", "makeup_effects", "liquid_locations",
            "mutex_groups", "fabric_materials", "pattern_textures", "incompatible_with"
        )
        for tf in tuple_fields:
            val = getattr(self, tf)
            if val is not _ABSENT:
                explicit_set.add(tf)
                if isinstance(val, str):
                    raise TypeError(f"{tf} must be a tuple, list or set of str, got str")
                if val is None:
                    object.__setattr__(self, tf, ())
                elif isinstance(val, (list, set, tuple, frozenset)):
                    for x in val:
                        if not isinstance(x, str) or isinstance(x, bool):
                            raise TypeError(f"Elements of {tf} must be str, got {type(x).__name__}")
                    object.__setattr__(self, tf, tuple(sorted(set(val))))
                else:
                    raise TypeError(f"{tf} must be a tuple, list or set of str, got {type(val).__name__}")
            else:
                object.__setattr__(self, tf, ())

        # 4. hands_required 标量数值校验
        hr = self.hands_required
        if hr is not _ABSENT:
            explicit_set.add("hands_required")
            if hr is not None and (not isinstance(hr, int) or isinstance(hr, bool)):
                raise TypeError(f"hands_required must be an int, got {type(hr).__name__}")
            if hr is None:
                object.__setattr__(self, "hands_required", 0)
            elif hr not in (0, 1, 2):
                raise ValueError(f"hands_required must be 0, 1, or 2, got {hr!r}")
            else:
                object.__setattr__(self, "hands_required", hr)
        else:
            object.__setattr__(self, "hands_required", 0)

        # 5. is_ensemble
        ie = self.is_ensemble
        if ie is not _ABSENT:
            explicit_set.add("is_ensemble")
            if not isinstance(ie, bool):
                raise TypeError(f"is_ensemble must be bool, got {type(ie).__name__}")
            object.__setattr__(self, "is_ensemble", ie)
        else:
            object.__setattr__(self, "is_ensemble", False)

        # 6. 四态标量 (space_kind, time_of_day, hand_state)
        for fld, valid_enums in (
            ("space_kind", VALID_SPACE_KINDS),
            ("time_of_day", VALID_TIMES_OF_DAY),
            ("hand_state", VALID_HAND_STATES),
        ):
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val == "unspecified" or val is UNSPECIFIED:
                    object.__setattr__(self, fld, UNSPECIFIED)
                elif val is None:
                    object.__setattr__(self, fld, None)
                elif isinstance(val, str) and not isinstance(val, bool):
                    if val not in valid_enums and val != "unspecified":
                        raise ValueError(f"Invalid {fld}: {val!r}")
                    object.__setattr__(self, fld, val)
                else:
                    raise TypeError(f"{fld} must be str, UNSPECIFIED or None, got {type(val).__name__}")
            else:
                object.__setattr__(self, fld, None)

        # 7. 其余标量字符串枚举
        str_scalars = {
            "semantic_role": VALID_SEMANTIC_ROLES,
            "prop_usage": VALID_PROP_USAGES,
            "emotion": VALID_EMOTIONS,
            "gaze": VALID_GAZES,
            "occlusion": VALID_OCCLUSIONS,
            "capture_device": VALID_CAPTURE_DEVICES,
            "quality_class": VALID_QUALITY_CLASSES,
            "makeup_base": VALID_MAKEUP_BASES,
            "liquid_kind": VALID_LIQUID_KINDS,
            "liquid_amount": VALID_LIQUID_AMOUNTS,
            "style_genre": None,
        }
        for fld, valid_enums in str_scalars.items():
            val = getattr(self, fld)
            if val is not _ABSENT:
                explicit_set.add(fld)
                if val is not None:
                    if not isinstance(val, str) or isinstance(val, bool):
                        raise TypeError(f"{fld} must be str or None, got {type(val).__name__}")
                    if valid_enums is not None and val not in valid_enums:
                        raise ValueError(f"Invalid {fld}: {val!r}")
                    object.__setattr__(self, fld, val)
                else:
                    object.__setattr__(self, fld, None)
            else:
                object.__setattr__(self, fld, None)

        object.__setattr__(self, "explicit_fields", tuple(sorted(explicit_set)))
        self.validate()

    def validate(self) -> None:
        """校验事实枚举与数值约束，Fail-Closed。"""
        for vr in self.visible_regions:
            if vr not in VALID_VISIBLE_REGIONS:
                raise ValueError(f"Invalid visible_region: {vr!r}")
        for gt in self.garment_topologies:
            if gt not in VALID_GARMENT_TOPOLOGIES:
                raise ValueError(f"Invalid garment_topology: {gt!r}")
        for gs in self.garment_states:
            if gs not in VALID_GARMENT_STATES:
                raise ValueError(f"Invalid garment_state: {gs!r}")
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
        if self.liquid_amount is not None and self.liquid_amount not in VALID_LIQUID_AMOUNTS:
            raise ValueError(f"Invalid liquid_amount: {self.liquid_amount!r}")

        # 校验同一叶子的矛盾事实
        if "none" in self.garment_topologies and len(self.garment_topologies) > 1:
            raise ValueError(f"garment_topologies cannot contain 'none' alongside other topologies: {self.garment_topologies!r}")
        if any(s in ("removed", "discarded") for s in self.garment_states) and any(s in ("worn", "loosened") for s in self.garment_states):
            raise ValueError(f"garment_states cannot contain both removed/discarded and worn states: {self.garment_states!r}")
        if self.liquid_kind == "none" and (self.liquid_locations or self.liquid_amount):
            raise ValueError("liquid_kind='none' cannot have liquid_locations or liquid_amount")
        if "monochrome" in self.color_modes and any(c in ("color", "high_saturation") for c in self.color_modes):
            raise ValueError(f"color_modes cannot contain both monochrome and color/high_saturation: {self.color_modes!r}")

    def merge(self, child: Optional["SemanticFacts"]) -> "SemanticFacts":
        """四态层级继承合并规则 (真值表严格闭包)：
        - Child 缺席 (ABSENT): 保留 Parent 默认值/集合；
        - Child 显式未知 (EXPLICIT_UNSPECIFIED): 清除 Parent 默认值，置为 UNSPECIFIED；
        - Child 显式空集合 (EXPLICIT_EMPTY): 清空 Parent 继承集合，置为 ()；
        - Child 显式具体值 (EXPLICIT_VALUE): 标量覆盖 Parent，集合执行并集合并。
        """
        if child is None:
            return self

        def _merge_scalar(p_val: Any, c_val: Any, fld: str) -> Any:
            if fld in child.explicit_fields:
                return c_val
            return p_val

        def _merge_tuple(p_tup: Tuple[str, ...], c_tup: Tuple[str, ...], fld: str) -> Tuple[str, ...]:
            if fld in child.explicit_fields:
                if not c_tup:
                    return ()
                combined = set(p_tup or ()).union(set(c_tup))
                return tuple(sorted(combined))
            return p_tup or ()

        def _merge_submodel(p_sm: Any, c_sm: Any, fld: str) -> Any:
            if fld in child.explicit_fields:
                return c_sm
            return p_sm

        if "hands_required" in child.explicit_fields or child.hands_required != 0:
            hands_req = child.hands_required
        else:
            hands_req = self.hands_required

        merged_gov = dict(self.governance_metadata)
        if "governance_metadata" in child.explicit_fields:
            merged_gov.update(child.governance_metadata)

        merged_explicit = tuple(sorted(set(self.explicit_fields).union(set(child.explicit_fields))))

        merged = SemanticFacts(
            semantic_role=_merge_scalar(self.semantic_role, child.semantic_role, "semantic_role"),
            space_kind=_merge_scalar(self.space_kind, child.space_kind, "space_kind"),
            venue_ids=_merge_tuple(self.venue_ids, child.venue_ids, "venue_ids"),
            visible_regions=_merge_tuple(self.visible_regions, child.visible_regions, "visible_regions"),
            garment_topologies=_merge_tuple(self.garment_topologies, child.garment_topologies, "garment_topologies"),
            garment_states=_merge_tuple(self.garment_states, child.garment_states, "garment_states"),
            hand_state=_merge_scalar(self.hand_state, child.hand_state, "hand_state"),
            prop_usage=_merge_scalar(self.prop_usage, child.prop_usage, "prop_usage"),
            hands_required=hands_req,
            emotion=_merge_scalar(self.emotion, child.emotion, "emotion"),
            gaze=_merge_scalar(self.gaze, child.gaze, "gaze"),
            occlusion=_merge_scalar(self.occlusion, child.occlusion, "occlusion"),
            time_of_day=_merge_scalar(self.time_of_day, child.time_of_day, "time_of_day"),
            light_sources=_merge_tuple(self.light_sources, child.light_sources, "light_sources"),
            color_modes=_merge_tuple(self.color_modes, child.color_modes, "color_modes"),
            capture_device=_merge_scalar(self.capture_device, child.capture_device, "capture_device"),
            quality_class=_merge_scalar(self.quality_class, child.quality_class, "quality_class"),
            makeup_base=_merge_scalar(self.makeup_base, child.makeup_base, "makeup_base"),
            makeup_effects=_merge_tuple(self.makeup_effects, child.makeup_effects, "makeup_effects"),
            liquid_kind=_merge_scalar(self.liquid_kind, child.liquid_kind, "liquid_kind"),
            liquid_locations=_merge_tuple(self.liquid_locations, child.liquid_locations, "liquid_locations"),
            liquid_amount=_merge_scalar(self.liquid_amount, child.liquid_amount, "liquid_amount"),
            mutex_groups=_merge_tuple(self.mutex_groups, child.mutex_groups, "mutex_groups"),
            is_ensemble=_merge_scalar(self.is_ensemble, child.is_ensemble, "is_ensemble"),
            style_genre=_merge_scalar(self.style_genre, child.style_genre, "style_genre"),
            fabric_materials=_merge_tuple(self.fabric_materials, child.fabric_materials, "fabric_materials"),
            pattern_textures=_merge_tuple(self.pattern_textures, child.pattern_textures, "pattern_textures"),
            incompatible_with=_merge_tuple(self.incompatible_with, child.incompatible_with, "incompatible_with"),
            cut_features=_merge_submodel(self.cut_features, child.cut_features, "cut_features"),
            ensemble_pieces=_merge_submodel(self.ensemble_pieces, child.ensemble_pieces, "ensemble_pieces"),
            pose_facts=_merge_submodel(self.pose_facts, child.pose_facts, "pose_facts"),
            scene_facts=_merge_submodel(self.scene_facts, child.scene_facts, "scene_facts"),
            camera_facts=_merge_submodel(self.camera_facts, child.camera_facts, "camera_facts"),
            hair_facts=_merge_submodel(self.hair_facts, child.hair_facts, "hair_facts"),
            lighting_facts=_merge_submodel(self.lighting_facts, child.lighting_facts, "lighting_facts"),
            shot_facts=_merge_submodel(self.shot_facts, child.shot_facts, "shot_facts"),
            clothing_facts=_merge_submodel(self.clothing_facts, child.clothing_facts, "clothing_facts"),
            expression_facts=_merge_submodel(self.expression_facts, child.expression_facts, "expression_facts"),
            accessory_facts=_merge_submodel(self.accessory_facts, child.accessory_facts, "accessory_facts"),
            governance_metadata=merged_gov,
            explicit_fields=merged_explicit,
        )
        return merged

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SemanticFacts":
        if not d:
            return cls()
        if not isinstance(d, dict):
            raise TypeError(f"SemanticFacts data must be a dict, got {type(d).__name__}")
        known_fields = set(cls.__dataclass_fields__.keys()) - {"explicit_fields"}
        unexpected = set(d.keys()) - known_fields
        if unexpected:
            raise KeyError(f"Unexpected keys in SemanticFacts: {sorted(unexpected)}")
        submodel_factories = {
            "cut_features": CutFeatures.from_dict,
            "ensemble_pieces": EnsemblePieces.from_dict,
            "pose_facts": PosePhysicalFacts.from_dict,
            "scene_facts": SceneEnvironmentalFacts.from_dict,
            "camera_facts": CameraHardwareFacts.from_dict,
            "hair_facts": HairAttributeFacts.from_dict,
            "lighting_facts": LightingFacts.from_dict,
            "shot_facts": ShotCompositionFacts.from_dict,
            "clothing_facts": ClothingAttributeFacts.from_dict,
            "expression_facts": ExpressionFacts.from_dict,
            "accessory_facts": AccessoryAttributeFacts.from_dict,
        }
        kwargs = dict(d)
        for sm_name, factory in submodel_factories.items():
            if sm_name in kwargs:
                raw_sm = kwargs[sm_name]
                if isinstance(raw_sm, dict):
                    kwargs[sm_name] = factory(raw_sm)
        return cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {}
        for f_name in self.__dataclass_fields__.keys():
            if f_name == "explicit_fields":
                continue
            if f_name in self.explicit_fields:
                val = getattr(self, f_name)
                if val is UNSPECIFIED or val == "unspecified":
                    res[f_name] = "unspecified"
                elif isinstance(val, tuple):
                    res[f_name] = list(val)
                elif hasattr(val, "to_dict"):
                    res[f_name] = val.to_dict()
                elif val is not None:
                    res[f_name] = val
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
