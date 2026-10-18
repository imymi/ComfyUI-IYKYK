"""
slot_contract.py — 全项目唯一槽位契约与规范化定义 (SSOT)

规范 18 槽位内部装配流水线顺序，统一标准槽位命名，并提供别名预归一化与 Fail-Closed 校验。
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

if __package__:
    from .errors import (
        PromptValidationError,
        CATEGORY_INVALID_INPUT_TYPE,
        CATEGORY_SLOT_ALIAS_COLLISION,
    )
else:
    from lib.errors import (
        PromptValidationError,
        CATEGORY_INVALID_INPUT_TYPE,
        CATEGORY_SLOT_ALIAS_COLLISION,
    )


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

# 中文槽位名称与别名映射 (M4 契约)
CHINESE_SLOT_ALIASES: Dict[str, str] = {
    "场景主题": "scene_theme",
    "场景": "scene_theme",
    "主题": "scene_theme",
    "景别构图": "shot_type",
    "景别": "shot_type",
    "景别视角": "shot_type",
    "拍摄视角": "camera_angle",
    "视角": "camera_angle",
    "角色设定": "character",
    "角色": "character",
    "人格角色": "character",
    "裸露等级": "nudity",
    "裸露状态": "nudity",
    "裸露": "nudity",
    "服装款式": "clothing",
    "服装": "clothing",
    "服装状态": "clothing",
    "光影预设": "lighting",
    "光影氛围": "lighting",
    "光影": "lighting",
    "姿势动作": "pose",
    "姿势": "pose",
    "动作": "pose",
    "情绪表情": "expression",
    "表情眼神": "expression",
    "表情": "expression",
    "妆容细节": "makeup",
    "妆容": "makeup",
    "妆容发型": "makeup",
    "发型发色": "hairstyle",
    "发型": "hairstyle",
    "饰品头饰": "jewelry",
    "饰品": "jewelry",
    "真实微瑕": "imperfections",
    "微瑕细节": "imperfections",
    "微瑕": "imperfections",
    "纹身标记": "tattoo",
    "纹身": "tattoo",
    "道具物件": "props",
    "道具": "props",
    "液体效果": "liquids",
    "角色体液": "liquids",
    "体液": "liquids",
    "液体": "liquids",
    "胶片风格": "film",
    "风格胶片": "film",
    "胶片": "film",
    "画质等级": "quality",
    "画质修饰": "quality",
    "画质": "quality",
    "风格配方": "style_recipe",
    "配方": "style_recipe",
    "自定义提示词": "custom",
    "自定义追加": "custom",
    "自定义": "custom",
}

# 允许的完整槽位集合（包含 18 核心槽位与辅助槽位 style_recipe, custom）
ALLOWED_SLOTS: Set[str] = set(SLOT_ORDER) | set(AUXILIARY_SLOT_ORDER)


def normalize_slot_name(name: str) -> str:
    """规范化槽位名称，映射历史别名与 _override 后缀。"""
    s = str(name).strip()
    if s.endswith("_override") and len(s) > 9:
        s = s[:-9].strip()
    if s in CHINESE_SLOT_ALIASES:
        return CHINESE_SLOT_ALIASES[s]
    s_lower = s.lower()
    return CHINESE_SLOT_ALIASES.get(s_lower, SLOT_ALIASES.get(s_lower, s_lower))


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
                    "Expected str, PromptFragment, or list/tuple of str/PromptFragment.",
                    slots=[norm_key],
                    category=CATEGORY_INVALID_INPUT_TYPE,
                )
            # 未知槽位且为 None，静默忽略
            continue

        is_empty = isinstance(val, (list, tuple, set, dict, str)) and len(val) == 0

        if not is_empty:
            if norm_key not in ALLOWED_SLOTS:
                raise PromptValidationError(
                    f"Unknown non-empty slot {raw_key!r} (normalized: {norm_key!r}) is not allowed in pipeline.",
                    slots=[norm_key],
                    texts=[str(val)],
                    category=CATEGORY_INVALID_INPUT_TYPE,
                )

            if norm_key in sources:
                sources[norm_key].append(raw_key)
                prev_val = normalized[norm_key]
                raise PromptValidationError(
                    f"Conflicting slot inputs normalized to canonical slot '{norm_key}': raw keys {sources[norm_key]}",
                    slots=[norm_key],
                    texts=[str(prev_val), str(val)],
                    category=CATEGORY_SLOT_ALIAS_COLLISION,
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
