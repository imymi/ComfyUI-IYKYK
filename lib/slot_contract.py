"""
slot_contract.py — 全项目唯一槽位契约与规范化定义 (SSOT)

规范 18 槽位内部装配流水线顺序，统一标准槽位命名，并提供别名预归一化与 Fail-Closed 校验。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

if __package__:
    from .errors import PromptValidationError
else:
    from lib.errors import PromptValidationError


# 18 个正式内部装配流水线槽位（规范顺序保持既有语义不变）
SLOT_ORDER: Tuple[str, ...] = (
    "scene_theme",
    "shot_type",
    "camera_angle",
    "character",
    "nudity",
    "clothing",
    "lighting",
    "pose",
    "expression",
    "makeup",
    "hairstyle",
    "jewelry",
    "imperfections",
    "tattoo",
    "props",
    "liquids",
    "film",
    "quality",
)

# 辅助槽位顺序：风格配方与自定义扩展槽位
AUXILIARY_SLOT_ORDER: Tuple[str, ...] = ("style_recipe", "custom")

# 历史别名与兼容映射表 (全面对齐外部文档与调用方历史命名)
SLOT_ALIASES: Dict[str, str] = {
    "scene": "scene_theme",
    "theme": "scene_theme",
    "poses": "pose",
    "lighting_palette": "lighting",
    "hairstyles": "hairstyle",
    "expressions": "expression",
    "film_stock": "film",
    "accessories": "jewelry",
    "tattoos": "tattoo",
    "prop": "props",
    "liquid": "liquids",
    "recipe": "style_recipe",
}

# 允许的完整槽位集合（包含 18 核心槽位与辅助槽位 style_recipe, custom）
ALLOWED_SLOTS: Set[str] = set(SLOT_ORDER) | set(AUXILIARY_SLOT_ORDER)


def normalize_slot_name(name: str) -> str:
    """规范化槽位名称，映射历史别名。"""
    s = str(name).strip().lower()
    return SLOT_ALIASES.get(s, s)


def normalize_slot_mapping(slots: Dict[str, Any]) -> Dict[str, Any]:
    """
    对槽位字典执行一次性归一化，并执行 Fail-Closed 冲突检测：
    - 若存在多个非空原始键归一化到同一个规范槽位（如同时提供 scene 与 theme，或 scene_theme 与 scene），
      立即抛出 PromptValidationError 并列出冲突的原始键；
    - 校验未知非空槽位；
    - 返回归一化后的规范槽位字典。
    """
    normalized: Dict[str, Any] = {}
    sources: Dict[str, List[str]] = {}

    for raw_key, val in slots.items():
        norm_key = normalize_slot_name(raw_key)

        if val is None:
            if norm_key in ALLOWED_SLOTS:
                raise PromptValidationError(
                    f"Slot {norm_key!r} (from raw key {raw_key!r}) explicitly provided as None (type NoneType). "
                    "Expected str, PromptFragment, or list/tuple of str/PromptFragment."
                )
            # 未知槽位且为 None，静默忽略
            continue

        is_empty = isinstance(val, (list, tuple, set, dict, str)) and len(val) == 0

        if not is_empty:
            if norm_key not in ALLOWED_SLOTS:
                raise PromptValidationError(
                    f"Unknown non-empty slot {raw_key!r} (normalized: {norm_key!r}) is not allowed in pipeline."
                )

            if norm_key in sources:
                sources[norm_key].append(raw_key)
                raise PromptValidationError(
                    f"Conflicting slot inputs normalized to canonical slot '{norm_key}': raw keys {sources[norm_key]}"
                )
            else:
                sources[norm_key] = [raw_key]
                normalized[norm_key] = val
        else:
            if norm_key not in normalized:
                normalized[norm_key] = val

    return normalized


def validate_slots_subset(slots: Dict[str, Any]) -> None:
    """
    严格校验传入的槽位字典：
    - 允许空的未知槽位静默忽略；
    - 若存在任何不在 ALLOWED_SLOTS 中且具有非空内容的未知槽位，立即 Fail-Closed 抛出 PromptValidationError。
    """
    normalize_slot_mapping(slots)
