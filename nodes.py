"""
nodes.py — ComfyUI 原生自定义节点定义与注册

包含 3 个核心节点：
1. IYKYKPromptGenerator (🎴 IYKYK 15槽位提示词生成器) — 全维度 15 槽位独立控制与情境自洽采样
2. IYKYKPresetBrowser (📋 IYKYK 模板浏览器) — 77 套手写预设模板与 8 大风格配方叠加
3. IYKYKCustomSlotCombiner (🧩 IYKYK 自定义槽位拼装器) — 自由输入多槽位文本与冲突消解

特性：
- 完整支持 prompt_seed（-1 为动态随机抽卡，非负整数为 100% 确定性复现）
- 接入 ComfyUI 原生 control_after_generate 与 IS_CHANGED 缓存
- 统一调用 finalize_prompt 结构化流水线
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from .lib.assembler import PromptAssembler, finalize_prompt, split_top_level_tags
    from .lib.models import PromptFragment
    from .lib.sampler import DataSampler, _is_none
except (ImportError, ValueError):
    from lib.assembler import PromptAssembler, finalize_prompt, split_top_level_tags
    from lib.models import PromptFragment
    from lib.sampler import DataSampler, _is_none

DATA_DIR = Path(__file__).parent / "data"
_sampler = DataSampler(DATA_DIR)
_assembler = PromptAssembler(DATA_DIR)


def _get_rng(prompt_seed: int) -> Tuple[random.Random, int]:
    if prompt_seed is None or prompt_seed == -1:
        effective_seed = random.randint(0, 0x7FFFFFFF)
    else:
        effective_seed = int(prompt_seed)
    return random.Random(effective_seed), effective_seed


def _compute_is_changed(prompt_seed: int, inputs: Dict[str, Any]) -> Any:
    if prompt_seed == -1:
        return float("NaN")

    hasher = hashlib.sha256()
    hasher.update(str(prompt_seed).encode("utf-8"))

    for key in sorted(inputs.keys()):
        hasher.update(f"{key}:{inputs[key]}".encode("utf-8"))

    return hasher.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# 节点 1: 🎴 IYKYK 15槽位提示词生成器
# ═══════════════════════════════════════════════════════════════════════════

class IYKYKPromptGenerator:
    """15 槽位提示词生成器"""

    @classmethod
    def INPUT_TYPES(cls):
        def with_defaults(items: List[str], default_mode: str = "random") -> List[str]:
            if default_mode == "none":
                return ["无 (None)", "随机 (Random)"] + items
            elif default_mode == "auto":
                return ["自动 (Auto)", "随机 (Random)", "无 (None)"] + items
            else:
                return ["随机 (Random)", "无 (None)"] + items

        return {
            "required": {
                "预设模板": (["无 (None)", "随机 (Random)"] + _sampler.list_preset_names(), {"default": "无 (None)"}),
                "风格配方": (["无 (None)", "随机 (Random)"] + _sampler.list_style_recipes(), {"default": "无 (None)"}),
                "场景大类": (with_defaults(_sampler.list_scene_categories(), "random"), {"default": "随机 (Random)"}),
                "剧情主题": (with_defaults(_sampler.list_themes(), "random"), {"default": "随机 (Random)"}),
                "景别构图": (with_defaults(_sampler.list_shot_types(), "auto"), {"default": "自动 (Auto)"}),
                "拍摄视角": (with_defaults(_sampler.list_camera_angles(), "auto"), {"default": "自动 (Auto)"}),
                "裸露等级": ([
                    "随机 (Random)",
                    "L1 包裹暗示 (Fully Clothed / Suggestive)",
                    "L2 差分微露 (Partially Exposed)",
                    "L3 半裸诱惑 (Half Nude)",
                    "L4 重点暴露 (Topless / Bottomless)",
                    "L5 极致全裸 (Full Nude)",
                    "L6 特写全见 (Explicit Genital Close-up)",
                ], {"default": "随机 (Random)"}),
                "服装款式": (with_defaults(_sampler.list_clothing_styles(), "random"), {"default": "随机 (Random)"}),
                "服装状态": (["自动联动裸露等级 (Auto Link Nudity)", "随机 (Random)", "无 (None)"] + _sampler.list_clothing_states(), {"default": "自动联动裸露等级 (Auto Link Nudity)"}),
                "发型发色": (with_defaults(_sampler.list_hairstyles(), "random"), {"default": "随机 (Random)"}),
                "饰品头饰": (with_defaults(_sampler.list_jewelry(), "none"), {"default": "无 (None)"}),
                "妆容细节": (with_defaults(_sampler.list_makeup_styles(), "none"), {"default": "无 (None)"}),
                "姿势动作": (with_defaults(_sampler.list_pose_categories(), "random"), {"default": "随机 (Random)"}),
                "情绪表情": (with_defaults(_sampler.list_expression_moods(), "random"), {"default": "随机 (Random)"}),
                "光影预设": (with_defaults(_sampler.list_lighting_presets(), "auto"), {"default": "自动 (Auto)"}),
                "胶片风格": (with_defaults(_sampler.list_film_stocks(), "none"), {"default": "无 (None)"}),
                "液体效果": (with_defaults(_sampler.list_liquid_effects(), "none"), {"default": "无 (None)"}),
                "纹身标记": (with_defaults(_sampler.list_tattoo_styles(), "none"), {"default": "无 (None)"}),
                "道具物件": (with_defaults(_sampler.list_prop_styles(), "none"), {"default": "无 (None)"}),
                "角色设定": (with_defaults(_sampler.list_character_roles(), "none"), {"default": "无 (None)"}),
                "真实微瑕": (with_defaults(_sampler.list_imperfection_types(), "none"), {"default": "无 (None)"}),
                "画质等级": ([
                    "高清写真 (High)",
                    "顶尖艺术 (Masterpiece)",
                    "手机自拍 (Phone Camera)",
                    "监控画质 (CCTV Footage)",
                    "标准画质 (Standard)",
                ], {"default": "高清写真 (High)"}),
            },
            "optional": {
                "prompt_seed": ("INT", {
                    "default": -1,
                    "min": -1,
                    "max": 0xFFFFFFFFFFFFFFFF,
                    "control_after_generate": True,
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词 (STRING)", "负面提示词 (STRING)", "中文场景描述 (STRING)")
    FUNCTION = "generate"
    CATEGORY = "IYKYK / 提示词生成"

    @classmethod
    def IS_CHANGED(cls, prompt_seed: int = -1, **kwargs) -> Any:
        return _compute_is_changed(prompt_seed, kwargs)

    def generate(
        self,
        预设模板: str,
        风格配方: str,
        场景大类: str,
        剧情主题: str,
        景别构图: str,
        拍摄视角: str,
        裸露等级: str,
        服装款式: str,
        服装状态: str,
        发型发色: str,
        饰品头饰: str,
        妆容细节: str,
        姿势动作: str,
        情绪表情: str,
        光影预设: str,
        胶片风格: str,
        液体效果: str,
        纹身标记: str,
        道具物件: str,
        角色设定: str,
        真实微瑕: str,
        画质等级: str,
        prompt_seed: int = -1,
    ) -> Tuple[str, str, str]:
        rng, _ = _get_rng(prompt_seed)

        # 1. 检查是否使用预设模板
        if not _is_none(预设模板):
            preset = _sampler.get_preset(预设模板, rng)
            if preset:
                recipe = _sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None
                pos = _assembler.assemble_preset(preset, recipe, 画质等级, rng=rng)
                neg = _sampler.get_negative_prompt()
                desc = f"【预设模板】{preset.get('id', '')} {preset.get('name_zh', '')}"
                if recipe:
                    desc += f" | 【叠加配方】{recipe.get('style_name', recipe.get('name_zh', ''))}"
                return (pos, neg, desc)

        # 2. 采样 15 槽位
        # 槽位 1: 场景 + 主题
        scene_res = _sampler.sample_scene_result(场景大类, rng)
        slots: Dict[str, List[Any]] = {}

        if scene_res:
            scene_frags: List[Any] = [
                PromptFragment(
                    text=t,
                    source_slot="scene_theme",
                    source_item_id=scene_res.item_id,
                    context_ids=scene_res.context_ids,
                    exclusive_group=scene_res.exclusive_group,
                )
                for t in scene_res.tags
            ]
            slots["scene_theme"] = scene_frags
            primary_context = scene_res.context_ids[0] if scene_res.context_ids else "generic"
        else:
            slots["scene_theme"] = _sampler.sample_scene(场景大类, rng)
            primary_context = "generic"

        theme_tags = _sampler.sample_theme(剧情主题, rng)
        slots["scene_theme"].extend(theme_tags)

        context = primary_context if primary_context != "generic" else _sampler.detect_context(场景大类, 剧情主题)

        # 槽位 2: 景别 + 视角
        slots["shot_type"] = _sampler.sample_shot_type(景别构图, rng)
        slots["camera_angle"] = _sampler.sample_camera_angle(拍摄视角, rng)

        # 槽位 3 & 4: 裸露等级与服装穿脱联动
        nudity_tags, lvl_code = _sampler.sample_nudity(裸露等级, rng)
        slots["nudity"] = nudity_tags
        slots["clothing"] = _sampler.sample_clothing_with_nudity_linkage(
            服装款式, 服装状态, lvl_code, rng, context=context
        )

        # 槽位 5: 光影氛围
        slots["lighting"] = _sampler.sample_lighting(光影预设, rng)

        # 槽位 6: 姿势动作
        slots["pose"] = _sampler.sample_pose(姿势动作, rng)

        # 槽位 7: 表情眼神
        slots["expression"] = _sampler.sample_expression(情绪表情, rng)

        # 槽位 8: 风格胶片
        slots["film"] = _sampler.sample_film(胶片风格, rng)

        # 槽位 9: 妆容细节
        slots["makeup"] = _sampler.sample_makeup(妆容细节, rng, context=context)

        # 槽位 10: 发型与饰品
        slots["hairstyle"] = _sampler.sample_hairstyle(发型发色, rng, context=context)
        slots["jewelry"] = _sampler.sample_jewelry(饰品头饰, rng, context=context)

        # 槽位 11: 真实微瑕
        slots["imperfections"] = _sampler.sample_imperfections(真实微瑕, rng)

        # 槽位 12: 纹身标记（仅在显式配置时生效）
        slots["tattoo"] = _sampler.sample_tattoo(纹身标记, rng, context=context)

        # 槽位 13: 道具物件
        slots["props"] = _sampler.sample_prop(道具物件, rng, context=context)

        # 槽位 14: 人格角色
        slots["character"] = _sampler.sample_character(角色设定, rng, context=context)

        # 槽位 15: 液体体液
        slots["liquids"] = _sampler.sample_liquid(液体效果, rng, context=context)

        # 画质强化锚点
        slots["quality"] = _sampler.sample_quality_tags(画质等级)

        # 3. 叠加风格配方
        recipe = _sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None
        if recipe:
            for k in ["lighting_palette", "style_recipe", "focus_detail"]:
                val = recipe.get(k, "")
                if val:
                    slots.setdefault("style_recipe", []).extend([t.strip() for t in str(val).split(",") if t.strip()])

        # 4. 组装、冲突消解与统一 Finalize
        positive_prompt = _assembler.assemble(slots, rng=rng)
        negative_prompt = _sampler.get_negative_prompt()

        # 5. 生成中文概要
        desc_parts = []
        if not _is_none(场景大类):
            desc_parts.append(f"场景: {场景大类}")
        if not _is_none(剧情主题):
            desc_parts.append(f"主题: {剧情主题}")
        if not _is_none(服装款式):
            desc_parts.append(f"服装: {服装款式}")
        desc_parts.append(f"裸露: {lvl_code}")
        if not _is_none(发型发色):
            desc_parts.append(f"发型: {发型发色}")
        if not _is_none(妆容细节):
            desc_parts.append(f"妆容: {妆容细节}")
        if not _is_none(液体效果):
            desc_parts.append(f"体液: {液体效果}")
        if recipe:
            desc_parts.append(f"配方: {recipe.get('style_name', recipe.get('name_zh', ''))}")

        chinese_desc = " | ".join(desc_parts)
        return (positive_prompt, negative_prompt, chinese_desc)


# ═══════════════════════════════════════════════════════════════════════════
# 节点 2: 📋 IYKYK 模板浏览器
# ═══════════════════════════════════════════════════════════════════════════

class IYKYKPresetBrowser:
    """模板浏览器"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "预设模板": (_sampler.list_preset_names(),),
                "风格配方": (["无 (None)", "随机 (Random)"] + _sampler.list_style_recipes(), {"default": "无 (None)"}),
                "画质等级": ([
                    "高清写真 (High)",
                    "顶尖艺术 (Masterpiece)",
                    "手机自拍 (Phone Camera)",
                    "监控画质 (CCTV Footage)",
                    "标准画质 (Standard)",
                ], {"default": "高清写真 (High)"}),
            },
            "optional": {
                "prompt_seed": ("INT", {
                    "default": -1,
                    "min": -1,
                    "max": 0xFFFFFFFFFFFFFFFF,
                    "control_after_generate": True,
                }),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词 (STRING)", "负面提示词 (STRING)", "中文场景描述 (STRING)")
    FUNCTION = "browse"
    CATEGORY = "IYKYK / 提示词生成"

    @classmethod
    def IS_CHANGED(cls, prompt_seed: int = -1, **kwargs) -> Any:
        return _compute_is_changed(prompt_seed, kwargs)

    def browse(
        self,
        预设模板: str,
        风格配方: str,
        画质等级: str,
        prompt_seed: int = -1,
    ) -> Tuple[str, str, str]:
        rng, _ = _get_rng(prompt_seed)
        preset = _sampler.get_preset(预设模板, rng)
        if not preset:
            return ("", _sampler.get_negative_prompt(), "未找到指定预设")

        recipe = _sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None
        pos = _assembler.assemble_preset(preset, recipe, 画质等级, rng=rng)
        neg = _sampler.get_negative_prompt()

        desc = f"【预设模板】{preset.get('id', '')} {preset.get('name_zh', '')}"
        if recipe:
            desc += f" | 【叠加配方】{recipe.get('style_name', recipe.get('name_zh', ''))}"

        return (pos, neg, desc)


# ═══════════════════════════════════════════════════════════════════════════
# 节点 3: 🧩 IYKYK 自定义槽位拼装器
# ═══════════════════════════════════════════════════════════════════════════

class IYKYKCustomSlotCombiner:
    """自定义槽位拼装器"""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "optional": {
                "prompt_seed": ("INT", {
                    "default": -1,
                    "min": -1,
                    "max": 0xFFFFFFFFFFFFFFFF,
                    "control_after_generate": True,
                }),
                "场景主题": ("STRING", {"multiline": True, "default": ""}),
                "景别视角": ("STRING", {"multiline": True, "default": ""}),
                "裸露状态": ("STRING", {"multiline": True, "default": ""}),
                "服装款式": ("STRING", {"multiline": True, "default": ""}),
                "光影氛围": ("STRING", {"multiline": True, "default": ""}),
                "姿势动作": ("STRING", {"multiline": True, "default": ""}),
                "表情眼神": ("STRING", {"multiline": True, "default": ""}),
                "风格胶片": ("STRING", {"multiline": True, "default": ""}),
                "妆容发型": ("STRING", {"multiline": True, "default": ""}),
                "微瑕细节": ("STRING", {"multiline": True, "default": ""}),
                "纹身标记": ("STRING", {"multiline": True, "default": ""}),
                "道具物件": ("STRING", {"multiline": True, "default": ""}),
                "角色体液": ("STRING", {"multiline": True, "default": ""}),
                "画质修饰": ("STRING", {"multiline": True, "default": "best quality, masterpiece"}),
                "自定义追加": ("STRING", {"multiline": True, "default": ""}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词 (STRING)", "负面提示词 (STRING)", "已拼装槽位数 (STRING)")
    FUNCTION = "combine"
    CATEGORY = "IYKYK / 提示词生成"

    @classmethod
    def IS_CHANGED(cls, prompt_seed: int = -1, **kwargs) -> Any:
        return _compute_is_changed(prompt_seed, kwargs)

    def combine(self, prompt_seed: int = -1, **kwargs) -> Tuple[str, str, str]:
        rng, _ = _get_rng(prompt_seed)

        slot_mapping = {
            "场景主题": "scene_theme",
            "景别视角": "shot_type",
            "裸露状态": "nudity",
            "服装款式": "clothing",
            "光影氛围": "lighting",
            "姿势动作": "pose",
            "表情眼神": "expression",
            "风格胶片": "film",
            "妆容发型": "makeup",
            "微瑕细节": "imperfections",
            "纹身标记": "tattoo",
            "道具物件": "props",
            "角色体液": "liquids",
            "画质修饰": "quality",
            "自定义追加": "custom",
        }

        fragments: List[PromptFragment] = []
        order = 0
        active_count = 0

        for user_key, slot_name in slot_mapping.items():
            val = kwargs.get(user_key, "")
            if val and str(val).strip():
                active_count += 1
                tags = split_top_level_tags(str(val))
                for t in tags:
                    fragments.append(
                        PromptFragment(
                            text=t,
                            source_slot=slot_name,
                            order=order,
                        )
                    )
                    order += 1

        positive_prompt = finalize_prompt(fragments, data_dir=DATA_DIR, rng=rng)
        negative_prompt = _sampler.get_negative_prompt()
        desc = f"已成功拼装 {active_count} 个自定义槽位"

        return (positive_prompt, negative_prompt, desc)


NODE_CLASS_MAPPINGS = {
    "IYKYKPromptGenerator": IYKYKPromptGenerator,
    "IYKYKPresetBrowser": IYKYKPresetBrowser,
    "IYKYKCustomSlotCombiner": IYKYKCustomSlotCombiner,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "IYKYKPromptGenerator": "🎴 IYKYK 15槽位提示词生成器",
    "IYKYKPresetBrowser": "📋 IYKYK 模板浏览器",
    "IYKYKCustomSlotCombiner": "🧩 IYKYK 自定义槽位拼装器",
}
