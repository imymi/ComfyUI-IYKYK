"""
nodes.py — ComfyUI-IYKYK 自定义节点主入口

完整实现 nsfw-prompt-templates-asian 规范的 15 槽位装配流水线：
- 场景与主题 (01-场景主题.md)
- 景别与视角 (02-景别构图.md)
- 裸露状态 (03-裸露液体.md)
- 服装款式与穿脱状态 (04-服装专项.md) — 裸露与服装深度咬合
- 光影氛围 (05-光影氛围.md)
- 姿势动作 (06-姿势动作.md)
- 表情眼神 (07-表情眼神.md)
- 风格胶片 (08-风格胶片.md)
- 妆容细节 (09-妆容专项.md)
- 发型饰品 (10-发型饰品.md)
- 真实瑕疵 (11-瑕疵细节.md)
- 纹身标记 (12-纹身标记.md)
- 道具宠物 (13-道具宠物.md)
- 角色设定 (14-人格卡片.md)
- 液体系统 (03-裸露液体.md)
- 画质强化 (02-景别构图.md)
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any, Dict, Tuple

try:
    from .lib.assembler import PromptAssembler
    from .lib.sampler import DataSampler
    from .lib.conflict_resolver import sanitize_prompt
except (ImportError, ValueError):
    from lib.assembler import PromptAssembler
    from lib.sampler import DataSampler
    from lib.conflict_resolver import sanitize_prompt


DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class IYKYKPromptGenerator:
    """
    IYKYK 15 槽位提示词生成器 (ComfyUI-IYKYK)
    完整支持服装、妆容、发型、饰品、景别、视角、道具、角色等 15 大维度。
    服装状态默认自动联动裸露等级（L1 包裹至 L6 特写），杜绝互斥冲突。
    """

    @classmethod
    def INPUT_TYPES(cls):
        sampler = DataSampler(DATA_DIR)

        # 1. 预设与配方
        presets = ["无 (None)", "随机 (Random)"] + sampler.list_preset_names()
        recipes = ["无 (None)", "随机 (Random)"] + sampler.list_style_recipes()

        # 2. 场景与主题
        scenes = ["随机 (Random)"] + sampler.list_scene_categories()
        themes = ["无 (None)", "随机 (Random)"] + sampler.list_themes()

        # 3. 景别与视角
        shots = ["自动 (Auto)", "随机 (Random)"] + sampler.list_shot_types()
        angles = ["自动 (Auto)", "随机 (Random)"] + sampler.list_camera_angles()

        # 4. 裸露
        nudity = [
            "随机 (Random)",
            "L1 包裹暗示 (Wrapped/Suggestive)",
            "L2 差分微露 (1-2 Subtle Peeks)",
            "L3 半裸诱惑 (Half Nude)",
            "L4 重点暴露 (Topless / Panties Only)",
            "L5 极致全裸 (Full Nude)",
            "L6 特写全见 (Erotic Explicit)",
        ]

        # 5. 服装款式与穿脱状态（默认自动联动裸露等级）
        clothing_styles = ["无 (None)", "随机 (Random)"] + sampler.list_clothing_styles()
        clothing_states = [
            "自动联动裸露等级 (Auto Link Nudity)",
            "无 (None)",
            "随机 (Random)",
        ] + [s for s in sampler.list_clothing_states() if "自动联动" not in s]

        # 6. 发型与饰品
        hairstyles = ["无 (None)", "随机 (Random)"] + sampler.list_hairstyles()
        jewelry = ["无 (None)", "随机 (Random)"] + sampler.list_jewelry()

        # 7. 妆容细节
        makeup_styles = ["无 (None)", "随机 (Random)"] + sampler.list_makeup_styles()

        # 8. 动作姿势与情绪
        poses = ["随机 (Random)"] + sampler.list_pose_categories()
        expressions = ["随机 (Random)"] + sampler.list_expression_moods()

        # 9. 光影与胶片
        lighting = ["自动 (Auto)", "随机 (Random)"] + sampler.list_lighting_presets()
        films = ["无 (None)", "随机 (Random)"] + sampler.list_film_stocks()

        # 10. 液体与标记
        liquids = ["无 (None)", "随机 (Random)"] + sampler.list_liquid_effects()
        tattoos = ["无 (None)", "随机 (Random)"] + sampler.list_tattoo_styles()
        props = ["无 (None)", "随机 (Random)"] + sampler.list_prop_styles()
        characters = ["无 (None)", "随机 (Random)"] + sampler.list_character_roles()
        imperfections = ["无 (None)", "随机 (Random)"] + sampler.list_imperfection_types()

        # 11. 画质等级
        qualities = [
            "高清写真 (High)",
            "顶尖画质 (Masterpiece)",
            "标准质感 (Standard)",
            "手机私密 (Phone Camera)",
            "监控纪实 (CCTV)",
        ]

        return {
            "required": {
                "预设模板": (presets, {"default": "无 (None)"}),
                "风格配方": (recipes, {"default": "无 (None)"}),
                "场景大类": (scenes, {"default": "随机 (Random)"}),
                "剧情主题": (themes, {"default": "无 (None)"}),
                "景别构图": (shots, {"default": "自动 (Auto)"}),
                "拍摄视角": (angles, {"default": "自动 (Auto)"}),
                "裸露等级": (nudity, {"default": "随机 (Random)"}),
                "服装款式": (clothing_styles, {"default": "随机 (Random)"}),
                "服装状态": (clothing_states, {"default": "自动联动裸露等级 (Auto Link Nudity)"}),
                "发型发色": (hairstyles, {"default": "随机 (Random)"}),
                "饰品头饰": (jewelry, {"default": "无 (None)"}),
                "妆容细节": (makeup_styles, {"default": "无 (None)"}),
                "姿势动作": (poses, {"default": "随机 (Random)"}),
                "情绪表情": (expressions, {"default": "随机 (Random)"}),
                "光影预设": (lighting, {"default": "自动 (Auto)"}),
                "胶片风格": (films, {"default": "无 (None)"}),
                "液体效果": (liquids, {"default": "无 (None)"}),
                "纹身标记": (tattoos, {"default": "无 (None)"}),
                "道具物件": (props, {"default": "无 (None)"}),
                "角色设定": (characters, {"default": "无 (None)"}),
                "真实微瑕": (imperfections, {"default": "随机 (Random)"}),
                "画质等级": (qualities, {"default": "高清写真 (High)"}),
            },
            "optional": {
                "自定义场景覆盖": ("STRING", {"multiline": False, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词", "负面提示词", "中文场景描述")
    FUNCTION = "generate"
    CATEGORY = "IYKYK"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # 每次 Queue Prompt 时动态重新计算随机槽位
        return float("NaN")

    def generate(self, **kwargs) -> Tuple[str, str, str]:
        assembler = PromptAssembler(DATA_DIR)

        preset = kwargs.get("预设模板", kwargs.get("preset", "无 (None)"))
        style_recipe = kwargs.get("风格配方", kwargs.get("style_recipe", "无 (None)"))
        scene_category = kwargs.get("场景大类", kwargs.get("scene_category", "随机 (Random)"))
        theme = kwargs.get("剧情主题", kwargs.get("theme", "无 (None)"))
        shot_type = kwargs.get("景别构图", kwargs.get("shot_type", "自动 (Auto)"))
        camera_angle = kwargs.get("拍摄视角", kwargs.get("camera_angle", "自动 (Auto)"))
        nudity_level = kwargs.get("裸露等级", kwargs.get("nudity_level", "随机 (Random)"))
        clothing_style = kwargs.get("服装款式", kwargs.get("clothing_style", "随机 (Random)"))
        clothing_state = kwargs.get("服装状态", kwargs.get("clothing_state", "自动联动裸露等级 (Auto Link Nudity)"))
        hairstyle = kwargs.get("发型发色", kwargs.get("hairstyle", "随机 (Random)"))
        jewelry_style = kwargs.get("饰品头饰", kwargs.get("jewelry_style", "无 (None)"))
        makeup_style = kwargs.get("妆容细节", kwargs.get("makeup_style", "无 (None)"))
        pose_category = kwargs.get("姿势动作", kwargs.get("pose_category", "随机 (Random)"))
        expression = kwargs.get("情绪表情", kwargs.get("expression", "随机 (Random)"))
        lighting_preset = kwargs.get("光影预设", kwargs.get("lighting_preset", "自动 (Auto)"))
        film_stock = kwargs.get("胶片风格", kwargs.get("film_stock", "无 (None)"))
        liquid_effect = kwargs.get("液体效果", kwargs.get("liquid_effect", "无 (None)"))
        tattoo_style = kwargs.get("纹身标记", kwargs.get("tattoo_style", "无 (None)"))
        prop_style = kwargs.get("道具物件", kwargs.get("prop_style", "无 (None)"))
        character_role = kwargs.get("角色设定", kwargs.get("character_role", "无 (None)"))
        imperfection_type = kwargs.get("真实微瑕", kwargs.get("imperfection_type", "随机 (Random)"))
        quality_tier = kwargs.get("画质等级", kwargs.get("quality_tier", "高清写真 (High)"))
        custom_scene_override = kwargs.get("自定义场景覆盖", kwargs.get("custom_scene_override", ""))

        result = assembler.assemble(
            preset=preset,
            style_recipe=style_recipe,
            scene_category=scene_category,
            theme=theme,
            shot_type=shot_type,
            camera_angle=camera_angle,
            nudity_level=nudity_level,
            clothing_style=clothing_style,
            clothing_state=clothing_state,
            lighting_preset=lighting_preset,
            pose_category=pose_category,
            expression=expression,
            film_stock=film_stock,
            makeup_style=makeup_style,
            hairstyle=hairstyle,
            jewelry_style=jewelry_style,
            imperfection_type=imperfection_type,
            tattoo_style=tattoo_style,
            prop_style=prop_style,
            character_role=character_role,
            liquid_effect=liquid_effect,
            quality_tier=quality_tier,
            custom_scene_override=custom_scene_override,
        )

        return (
            result["positive"],
            result["negative"],
            result["description_zh"],
        )


class IYKYKPresetBrowser:
    """
    IYKYK 模板浏览器 — 浏览并一键直出 77 套经典预设与 8 大导演配方。
    """

    @classmethod
    def INPUT_TYPES(cls):
        sampler = DataSampler(DATA_DIR)
        presets = sampler.list_preset_names()
        recipes = ["无 (None)"] + sampler.list_style_recipes()
        return {
            "required": {
                "预设模板": (presets, {"default": presets[0] if presets else "01"}),
                "风格配方叠加": (recipes, {"default": "无 (None)"}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词", "负面提示词", "中文描述")
    FUNCTION = "browse"
    CATEGORY = "IYKYK"

    def browse(self, **kwargs) -> Tuple[str, str, str]:
        assembler = PromptAssembler(DATA_DIR)
        preset_name = kwargs.get("预设模板", kwargs.get("preset_name", ""))
        recipe = kwargs.get("风格配方叠加", kwargs.get("recipe", "无 (None)"))

        result = assembler.assemble(
            preset=preset_name,
            style_recipe=recipe if recipe != "无 (None)" else "None",
        )
        return (
            result["positive"],
            result["negative"],
            result["description_zh"],
        )


class IYKYKCustomSlotCombiner:
    """
    IYKYK 自定义槽位拼装器 — 自由输入各槽位提示词，自动执行冲突消解与画质强化。
    """

    @classmethod
    def INPUT_TYPES(cls):
        qualities = [
            "高清写真 (High)",
            "顶尖画质 (Masterpiece)",
            "标准质感 (Standard)",
            "手机私密 (Phone Camera)",
            "监控纪实 (CCTV)",
        ]
        return {
            "required": {
                "画质等级": (qualities, {"default": "高清写真 (High)"}),
                "自动冲突修正": ("BOOLEAN", {"default": True}),
            },
            "optional": {
                "场景与环境": ("STRING", {"multiline": True, "default": ""}),
                "人物与身份": ("STRING", {"multiline": False, "default": ""}),
                "服装与状态": ("STRING", {"multiline": False, "default": ""}),
                "发型与饰品": ("STRING", {"multiline": False, "default": ""}),
                "妆容细节": ("STRING", {"multiline": False, "default": ""}),
                "姿势与动作": ("STRING", {"multiline": False, "default": ""}),
                "表情与眼神": ("STRING", {"multiline": False, "default": ""}),
                "光影与胶片": ("STRING", {"multiline": False, "default": ""}),
                "道具与宠物": ("STRING", {"multiline": False, "default": ""}),
                "纹身与微瑕": ("STRING", {"multiline": False, "default": ""}),
                "额外提示词": ("STRING", {"multiline": True, "default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词", "负面提示词", "组装摘要")
    FUNCTION = "combine"
    CATEGORY = "IYKYK"

    def combine(self, **kwargs) -> Tuple[str, str, str]:
        assembler = PromptAssembler(DATA_DIR)
        resolver = assembler.resolver

        quality_tier = kwargs.get("画质等级", "高清写真 (High)")
        auto_resolve = kwargs.get("自动冲突修正", True)

        slots = {}
        slot_keys = [
            ("scene_theme", "场景与环境"),
            ("character", "人物与身份"),
            ("clothing", "服装与状态"),
            ("accessories", "发型与饰品"),
            ("makeup", "妆容细节"),
            ("pose", "姿势与动作"),
            ("expression", "表情与眼神"),
            ("film_style", "光影与胶片"),
            ("props", "道具与宠物"),
            ("tattoo", "纹身与微瑕"),
        ]

        summary_parts = []
        for slot_name, widget_name in slot_keys:
            val = kwargs.get(widget_name, "").strip()
            if val:
                slots[slot_name] = [t.strip() for t in val.split(",") if t.strip()]
                summary_parts.append(f"{widget_name}: {val[:15]}...")

        extra = kwargs.get("额外提示词", "").strip()
        if extra:
            slots["extra"] = [t.strip() for t in extra.split(",") if t.strip()]

        slots["quality"] = assembler.sampler.sample_quality_tags(quality_tier)

        if auto_resolve:
            slots = resolver.resolve(slots)

        all_tags = []
        for tag_list in slots.values():
            all_tags.extend(tag_list)

        positive = sanitize_prompt(", ".join(all_tags))
        negative = assembler.sampler.get_negative_prompt()
        summary = " | ".join(summary_parts) if summary_parts else "自定义组合"

        return (positive, negative, summary)


NODE_CLASS_MAPPINGS = {
    "IYKYKPromptGenerator": IYKYKPromptGenerator,
    "IYKYKPresetBrowser": IYKYKPresetBrowser,
    "IYKYKCustomSlotCombiner": IYKYKCustomSlotCombiner,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "IYKYKPromptGenerator": "IYKYK 15槽位提示词生成器 (Prompt Generator)",
    "IYKYKPresetBrowser": "IYKYK 模板浏览器 (Preset Browser)",
    "IYKYKCustomSlotCombiner": "IYKYK 自定义槽位拼装器 (Slot Combiner)",
}
