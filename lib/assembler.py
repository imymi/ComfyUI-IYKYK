"""
assembler.py — 完整 15 槽位装配与情境联动引擎

严格遵循 nsfw-prompt-templates-asian 规范定义的 15 步装配流水线与冲突消解规则：
 1. 场景+主题 (01-场景主题.md)
 2. 景别+视角+设备 (02-景别构图.md)
 3. 裸露状态 (03-裸露液体.md)
 4. 服装款式与状态 (04-服装专项.md) — 裸露等级强联动
 5. 光影氛围 (05-光影氛围.md)
 6. 姿势动作 (06-姿势动作.md)
 7. 表情眼神 (07-表情眼神.md)
 8. 风格/胶片 (08-风格胶片.md)
 9. 妆容细节 (09-妆容专项.md)
10. 发型饰品 (10-发型饰品.md)
11. 真实瑕疵与皮肤 (11-瑕疵细节.md)
12. 纹身标记与皮肤融合 (12-纹身标记.md)
13. 道具宠物 (13-道具宠物.md)
14. 人格纵深/角色卡 (14-人格卡片.md)
15. 液体系统 (03-裸露液体.md)
16. 画质强化 (02-景别构图.md)
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

try:
    from .sampler import DataSampler, _is_none, _is_random, _is_auto
    from .conflict_resolver import ConflictResolver, sanitize_prompt
except (ImportError, ValueError):
    from lib.sampler import DataSampler, _is_none, _is_random, _is_auto
    from lib.conflict_resolver import ConflictResolver, sanitize_prompt


# 15 个槽位的严格装配顺序
SLOT_ORDER = [
    "scene_theme",      # 1. 场景+主题
    "shot_angle",       # 2. 景别+视角
    "nudity",           # 3. 裸露状态
    "clothing",         # 4. 服装款式与状态
    "lighting",         # 5. 光影氛围
    "pose",             # 6. 姿势动作
    "expression",       # 7. 表情眼神
    "film_style",       # 8. 风格/胶片
    "makeup",           # 9. 妆容细节
    "accessories",      # 10. 发型饰品
    "imperfections",    # 11. 瑕疵细节
    "tattoo",           # 12. 纹身标记
    "props",            # 13. 道具宠物
    "character",        # 14. 人格设定
    "liquids",          # 15. 液体系统
    "quality",          # 16. 画质强化
]


class PromptAssembler:
    """15 槽位装配引擎。"""

    def __init__(self, data_dir: str):
        self.sampler = DataSampler(data_dir)
        self.resolver = ConflictResolver(data_dir)

    def assemble(
        self,
        seed: Optional[int] = None,
        preset: str = "无 (None)",
        style_recipe: str = "无 (None)",
        scene_category: str = "随机 (Random)",
        theme: str = "无 (None)",
        shot_type: str = "自动 (Auto)",
        camera_angle: str = "自动 (Auto)",
        nudity_level: str = "随机 (Random)",
        clothing_style: str = "随机 (Random)",
        clothing_state: str = "自动联动裸露等级 (Auto Link Nudity)",
        lighting_preset: str = "自动 (Auto)",
        pose_category: str = "随机 (Random)",
        expression: str = "随机 (Random)",
        film_stock: str = "无 (None)",
        makeup_style: str = "无 (None)",
        hairstyle: str = "随机 (Random)",
        jewelry_style: str = "无 (None)",
        imperfection_type: str = "随机 (Random)",
        tattoo_style: str = "无 (None)",
        prop_style: str = "无 (None)",
        character_role: str = "无 (None)",
        liquid_effect: str = "无 (None)",
        quality_tier: str = "高清写真 (High)",
        custom_scene_override: str = "",
    ) -> Dict[str, Any]:
        """
        执行完整 15 槽位流水线装配（含情境自洽、裸露与服装深度联动与冲突消解）。
        """
        # 1. 确定随机生成器
        if seed is not None and seed >= 0:
            rng = random.Random(seed)
        else:
            rng = random.Random()

        # ── 预设模板模式：直接输出 ──
        if not _is_none(preset) and not _is_random(preset):
            p = self.sampler.get_preset(preset, rng)
            if p:
                return {
                    "positive": p.get("positive", ""),
                    "negative": self.sampler.get_negative_prompt(),
                    "description_zh": f"【预设模板】{p.get('name_zh', '')} - {p.get('description_zh', '')}",
                    "slots": {"preset": [preset]},
                }
        elif _is_random(preset) and not _is_none(preset):
            p = self.sampler.get_preset("Random", rng)
            if p:
                return {
                    "positive": p.get("positive", ""),
                    "negative": self.sampler.get_negative_prompt(),
                    "description_zh": f"【随机预设】{p.get('name_zh', '')} - {p.get('description_zh', '')}",
                    "slots": {"preset": [p.get("name_zh", "")]},
                }

        # ── 风格配方读取 ──
        recipe = None
        if not _is_none(style_recipe):
            recipe = self.sampler.get_style_recipe(style_recipe, rng)

        # ── 情境推断（Context Anchor） ──
        context = None
        if not _is_random(scene_category) or not _is_none(theme):
            context = self.sampler.detect_context(scene_category, theme)
        elif not _is_random(clothing_style) and not _is_none(clothing_style):
            context = self.sampler.detect_context(clothing_style, "")
        elif not _is_random(character_role) and not _is_none(character_role):
            context = self.sampler.detect_context(character_role, "")

        # ── 逐槽位装配 ──
        slots: Dict[str, List[str]] = {}

        # 槽位 1: 场景+主题 (01-场景主题)
        if custom_scene_override and custom_scene_override.strip():
            slots["scene_theme"] = [custom_scene_override.strip()]
            if not context:
                context = self.sampler.detect_context(custom_scene_override, "")
        else:
            scene_tags = self.sampler.sample_scene(scene_category, rng)
            theme_tags = self.sampler.sample_theme(theme, rng)
            slots["scene_theme"] = scene_tags + theme_tags
            if not context:
                context = self.sampler.detect_context(", ".join(scene_tags), ", ".join(theme_tags))

        # 槽位 2: 景别+视角 (02-景别构图)
        shot_tags = self.sampler.sample_shot_type(shot_type, rng)
        angle_tags = self.sampler.sample_camera_angle(camera_angle, rng)
        slots["shot_angle"] = shot_tags + angle_tags

        # 槽位 3: 裸露状态 (03-裸露液体)
        if recipe and recipe.get("exposure_mode"):
            exposure_map = {
                "none": "L1", "half_covered": "L2",
                "half_nude": "L3", "upper": "L4",
                "lower": "L4", "both": "L5",
            }
            mapped_l = exposure_map.get(recipe["exposure_mode"], nudity_level)
            nudity_tags, nudity_code = self.sampler.sample_nudity(mapped_l, rng)
        else:
            nudity_tags, nudity_code = self.sampler.sample_nudity(nudity_level, rng)
        slots["nudity"] = nudity_tags

        # 槽位 4: 服装款式与穿脱状态 (04-服装专项，基于裸露等级精准咬合)
        c_tags = self.sampler.sample_clothing_with_nudity_linkage(
            style=clothing_style,
            state=clothing_state,
            nudity_level_code=nudity_code,
            rng=rng,
            context=context,
        )
        slots["clothing"] = c_tags

        # 槽位 5: 光影氛围 (05-光影氛围)
        if recipe and recipe.get("lighting_palette"):
            slots["lighting"] = [recipe["lighting_palette"]]
        else:
            slots["lighting"] = self.sampler.sample_lighting(lighting_preset, rng)

        # 槽位 6: 姿势动作 (06-姿势动作)
        if recipe and recipe.get("pose_direction"):
            slots["pose"] = [recipe["pose_direction"]]
        else:
            slots["pose"] = self.sampler.sample_pose(pose_category, rng)

        # 槽位 7: 表情眼神 (07-表情眼神)
        if recipe and recipe.get("expression_gaze"):
            slots["expression"] = [recipe["expression_gaze"]]
        else:
            slots["expression"] = self.sampler.sample_expression(expression, rng)

        # 槽位 8: 风格/胶片 (08-风格胶片)
        film_tags = []
        if recipe and recipe.get("style_recipe"):
            film_tags.append(recipe["style_recipe"])
        custom_film = self.sampler.sample_film(film_stock, rng)
        if custom_film:
            film_tags.extend(custom_film)
        slots["film_style"] = film_tags

        # 槽位 9: 妆容细节 (09-妆容专项，情境自洽)
        if recipe and recipe.get("makeup_direction"):
            slots["makeup"] = [recipe["makeup_direction"]]
        else:
            slots["makeup"] = self.sampler.sample_makeup(makeup_style, rng, context=context)

        # 槽位 10: 发型饰品 (10-发型饰品，情境自洽)
        hair_tags = self.sampler.sample_hairstyle(hairstyle, rng, context=context)
        jew_tags = self.sampler.sample_jewelry(jewelry_style, rng, context=context)
        slots["accessories"] = hair_tags + jew_tags

        # 槽位 11: 真实瑕疵与皮肤细节 (11-瑕疵细节)
        slots["imperfections"] = self.sampler.sample_imperfections(imperfection_type, rng)

        # 槽位 12: 纹身标记与皮肤融合 (12-纹身标记，情境自洽)
        slots["tattoo"] = self.sampler.sample_tattoo(tattoo_style, rng, context=context)

        # 槽位 13: 道具宠物 (13-道具宠物，情境自洽)
        slots["props"] = self.sampler.sample_prop(prop_style, rng, context=context)

        # 槽位 14: 人格角色设定 (14-人格卡片，情境自洽)
        slots["character"] = self.sampler.sample_character(character_role, rng, context=context)

        # 槽位 15: 液体体液系统 (03-裸露液体，情境自洽)
        slots["liquids"] = self.sampler.sample_liquid(liquid_effect, rng, context=context)

        # 槽位 16: 画质强化 (02-景别构图)
        if recipe and recipe.get("focus_detail"):
            slots["quality"] = [recipe["focus_detail"]]
        else:
            slots["quality"] = self.sampler.sample_quality_tags(quality_tier)

        # ── 冲突检测与自动消解（7大规则库） ──
        slots = self.resolver.resolve(slots)

        # ── 按 15 槽位严格顺序拼装 ──
        parts: List[str] = []
        for slot_name in SLOT_ORDER:
            tags = slots.get(slot_name, [])
            if tags:
                for tag in tags:
                    tag_str = str(tag).strip()
                    if tag_str:
                        parts.append(tag_str)

        positive = sanitize_prompt(", ".join(parts))

        # 词数控制：上限 250 词
        words = positive.split()
        if len(words) > 260:
            positive = " ".join(words[:250])
            positive = sanitize_prompt(positive)

        # 负面提示词
        negative = self.sampler.get_negative_prompt()

        # ── 中文场景描述合成 ──
        desc_items = []
        if custom_scene_override and custom_scene_override.strip():
            desc_items.append(f"场景: {custom_scene_override.strip()}")
        elif not _is_random(scene_category):
            desc_items.append(f"场景: {scene_category}")

        if not _is_none(theme) and not _is_random(theme):
            desc_items.append(f"主题: {theme}")
        if not _is_none(clothing_style) and not _is_random(clothing_style):
            desc_items.append(f"服装: {clothing_style}")
        if not _is_none(clothing_state) and not _is_random(clothing_state) and not _is_auto(clothing_state):
            desc_items.append(f"状态: {clothing_state}")
        else:
            desc_items.append(f"裸露: {nudity_code}")
        if not _is_none(hairstyle) and not _is_random(hairstyle):
            desc_items.append(f"发型: {hairstyle}")
        if not _is_none(makeup_style) and not _is_random(makeup_style):
            desc_items.append(f"妆容: {makeup_style}")
        if not _is_random(pose_category):
            desc_items.append(f"姿势: {pose_category}")
        if not _is_random(expression):
            desc_items.append(f"表情: {expression}")
        if not _is_none(film_stock) and not _is_random(film_stock):
            desc_items.append(f"胶片: {film_stock}")
        if not _is_none(prop_style) and not _is_random(prop_style):
            desc_items.append(f"道具: {prop_style}")
        if not _is_none(character_role) and not _is_random(character_role):
            desc_items.append(f"角色: {character_role}")
        if not _is_none(liquid_effect) and not _is_random(liquid_effect):
            desc_items.append(f"体液: {liquid_effect}")

        description_zh = " | ".join(desc_items) if desc_items else f"🎲 15槽位抽卡 ({nudity_code})"

        return {
            "positive": positive,
            "negative": negative,
            "description_zh": description_zh,
            "slots": {k: v for k, v in slots.items() if v},
        }
