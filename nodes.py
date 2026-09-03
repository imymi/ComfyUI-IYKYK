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
from typing import Any, Dict, List, Tuple

if __package__:
    from .lib.assembler import PromptAssembler, assemble_result, split_top_level_tags
    from .lib.models import GenerationResult, PromptFragment, TagProvenance
    from .lib.sampler import DataSampler, _is_none
else:
    from lib.assembler import PromptAssembler, assemble_result, split_top_level_tags
    from lib.models import GenerationResult, PromptFragment, TagProvenance
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


def _generate_structured(
    sampler: DataSampler,
    assembler: PromptAssembler,
    inputs: Dict[str, Any],
    rng: random.Random
) -> GenerationResult:
    """提示词生成纯函数流水线 (修订 7 纯函数与丰富 Provenance 契约)。

    不变量：
    - 无副作用、无类/实例级可变状态存储；
    - 统一返回不可变 GenerationResult 对象，携带 positive, negative, description, atoms 与 rules_applied。
    """
    预设模板 = inputs.get("预设模板", "无 (None)")
    风格配方 = inputs.get("风格配方", "无 (None)")
    场景大类 = inputs.get("场景大类", "随机 (Random)")
    剧情主题 = inputs.get("剧情主题", "随机 (Random)")
    景别构图 = inputs.get("景别构图", "自动 (Auto)")
    拍摄视角 = inputs.get("拍摄视角", "自动 (Auto)")
    裸露等级 = inputs.get("裸露等级", "随机 (Random)")
    服装款式 = inputs.get("服装款式", "随机 (Random)")
    服装状态 = inputs.get("服装状态", "自动联动裸露等级 (Auto Link Nudity)")
    发型发色 = inputs.get("发型发色", "随机 (Random)")
    饰品头饰 = inputs.get("饰品头饰", "无 (None)")
    妆容细节 = inputs.get("妆容细节", "无 (None)")
    姿势动作 = inputs.get("姿势动作", "随机 (Random)")
    情绪表情 = inputs.get("情绪表情", "随机 (Random)")
    光影预设 = inputs.get("光影预设", "自动 (Auto)")
    胶片风格 = inputs.get("胶片风格", "无 (None)")
    液体效果 = inputs.get("液体效果", "无 (None)")
    纹身标记 = inputs.get("纹身标记", "无 (None)")
    道具物件 = inputs.get("道具物件", "无 (None)")
    角色设定 = inputs.get("角色设定", "无 (None)")
    真实微瑕 = inputs.get("真实微瑕", "无 (None)")
    画质等级 = inputs.get("画质等级", "高清写真 (High)")

    # 1. 检查是否使用预设模板
    if not _is_none(预设模板):
        preset = sampler.get_preset(预设模板, rng)
        if preset:
            recipe = sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None
            assembly_res = assembler.assemble_preset(preset, recipe, 画质等级, rng=rng)
            neg = sampler.get_negative_prompt()
            desc = f"【预设模板】{preset.get('id', '')} {preset.get('name_zh', '')}"
            if recipe:
                desc += f" | 【叠加配方】{recipe.get('style_name', recipe.get('name_zh', ''))}"
            return GenerationResult(
                positive=assembly_res.prompt,
                negative=neg,
                description=desc,
                atoms=assembly_res.accepted_atoms,
                rules_applied=assembly_res.rules_applied,
                source_atoms=assembly_res.source_atoms,
            )

    # 2. 采样 15 槽位
    # 槽位 1: 场景 + 主题
    scene_res = sampler.sample_scene_result(场景大类, rng)
    slots: Dict[str, List[Any]] = {}

    slots["scene_theme"] = [
        PromptFragment(
            text=t,
            source_slot="scene_theme",
            source_item_id=scene_res.item_id,
            context_ids=scene_res.context_ids,
            exclusive_group=scene_res.exclusive_group,
            provenance=scene_res.provenance,
        )
        for t in (scene_res.tags if scene_res else ())
    ]

    theme_res = sampler.sample_theme_result(剧情主题, rng)
    if theme_res:
        slots["scene_theme"].extend([
            PromptFragment(
                text=t.text,
                source_slot="scene_theme",
                source_item_id=theme_res.theme_id,
                context_ids=(),
                exclusive_group=None,
                provenance=t.provenance,
            )
            for t in theme_res.tags
        ])

    primary_context = scene_res.context_ids[0] if (scene_res and scene_res.context_ids) else "generic"
    context = primary_context if primary_context != "generic" else sampler.detect_context(场景大类, 剧情主题)

    # 槽位 2: 景别 + 视角
    shot_res = sampler.sample_shot_type_result(景别构图, rng)
    slots["shot_type"] = [
        PromptFragment(
            text=t,
            source_slot="shot_type",
            source_item_id=shot_res.item_id,
            provenance=shot_res.provenance,
        )
        for t in (shot_res.tags if shot_res else ())
    ]

    angle_res = sampler.sample_camera_angle_result(拍摄视角, rng)
    slots["camera_angle"] = [
        PromptFragment(
            text=t,
            source_slot="camera_angle",
            source_item_id=angle_res.item_id,
            provenance=angle_res.provenance,
        )
        for t in (angle_res.tags if angle_res else ())
    ]

    # 槽位 3 & 4: 裸露等级与服装穿脱联动
    nudity_res, lvl_code = sampler.sample_nudity_result(裸露等级, rng)
    slots["nudity"] = [
        PromptFragment(
            text=t,
            source_slot="nudity",
            source_item_id=nudity_res.item_id,
            provenance=nudity_res.provenance,
        )
        for t in (nudity_res.tags if nudity_res else ())
    ]
    clothing_res = sampler.sample_clothing_result(
        服装款式, 服装状态, lvl_code, rng, context=context
    )
    slots["clothing"] = [
        PromptFragment(
            text=t.text,
            source_slot="clothing",
            source_item_id=t.provenance.item_id,
            context_ids=(),
            exclusive_group=None,
            provenance=t.provenance,
        )
        for t in clothing_res.all_tags
    ]

    # 槽位 5: 光影氛围
    lighting_res = sampler.sample_lighting_result(光影预设, rng, nudity_level_code=lvl_code)
    slots["lighting"] = [
        PromptFragment(
            text=t,
            source_slot="lighting",
            source_item_id=lighting_res.item_id,
            provenance=lighting_res.provenance,
        )
        for t in (lighting_res.tags if lighting_res else ())
    ]

    # 槽位 6: 姿势动作
    pose_res = sampler.sample_pose_result(姿势动作, rng, nudity_level_code=lvl_code)
    slots["pose"] = [
        PromptFragment(
            text=t,
            source_slot="pose",
            source_item_id=pose_res.item_id,
            provenance=pose_res.provenance,
        )
        for t in (pose_res.tags if pose_res else ())
    ]

    # 槽位 7: 表情眼神
    expression_res = sampler.sample_expression_result(情绪表情, rng)
    slots["expression"] = [
        PromptFragment(
            text=t,
            source_slot="expression",
            source_item_id=expression_res.item_id,
            provenance=expression_res.provenance,
        )
        for t in (expression_res.tags if expression_res else ())
    ]

    # 槽位 8: 风格胶片
    film_res = sampler.sample_film_result(胶片风格, rng)
    slots["film"] = [
        PromptFragment(
            text=t,
            source_slot="film",
            source_item_id=film_res.item_id,
            provenance=film_res.provenance,
        )
        for t in (film_res.tags if film_res else ())
    ]

    # 槽位 9: 妆容细节
    makeup_res = sampler.sample_makeup_result(妆容细节, rng, context=context)
    slots["makeup"] = [
        PromptFragment(
            text=t,
            source_slot="makeup",
            source_item_id=makeup_res.item_id,
            provenance=makeup_res.provenance,
        )
        for t in (makeup_res.tags if makeup_res else ())
    ]

    # 槽位 10: 发型与饰品
    hairstyle_res = sampler.sample_hairstyle_result(发型发色, rng, context=context)
    slots["hairstyle"] = [
        PromptFragment(
            text=t,
            source_slot="hairstyle",
            source_item_id=hairstyle_res.item_id,
            provenance=hairstyle_res.provenance,
        )
        for t in (hairstyle_res.tags if hairstyle_res else ())
    ]
    jewelry_res = sampler.sample_jewelry_result(饰品头饰, rng, context=context)
    slots["jewelry"] = [
        PromptFragment(
            text=t,
            source_slot="jewelry",
            source_item_id=jewelry_res.item_id,
            provenance=jewelry_res.provenance,
        )
        for t in (jewelry_res.tags if jewelry_res else ())
    ]

    # 槽位 11: 真实微瑕
    imperfection_res = sampler.sample_imperfections_result(真实微瑕, rng)
    slots["imperfections"] = [
        PromptFragment(
            text=t,
            source_slot="imperfections",
            source_item_id=imperfection_res.item_id,
            provenance=imperfection_res.provenance,
        )
        for t in (imperfection_res.tags if imperfection_res else ())
    ]

    # 槽位 12: 纹身标记（仅在显式配置时生效）
    tattoo_res = sampler.sample_tattoo_result(纹身标记, rng, context=context)
    slots["tattoo"] = [
        PromptFragment(
            text=t,
            source_slot="tattoo",
            source_item_id=tattoo_res.item_id,
            provenance=tattoo_res.provenance,
        )
        for t in (tattoo_res.tags if tattoo_res else ())
    ]

    # 槽位 13: 道具物件
    prop_res = sampler.sample_prop_result(道具物件, rng, context=context)
    slots["props"] = [
        PromptFragment(
            text=t,
            source_slot="props",
            source_item_id=prop_res.item_id,
            provenance=prop_res.provenance,
        )
        for t in (prop_res.tags if prop_res else ())
    ]

    # 槽位 14: 人格角色
    character_res = sampler.sample_character_result(角色设定, rng, context=context)
    slots["character"] = [
        PromptFragment(
            text=t,
            source_slot="character",
            source_item_id=character_res.item_id,
            provenance=character_res.provenance,
        )
        for t in (character_res.tags if character_res else ())
    ]

    # 槽位 15: 液体体液
    liquid_res = sampler.sample_liquid_result(液体效果, rng, context=context)
    slots["liquids"] = [
        PromptFragment(
            text=t,
            source_slot="liquids",
            source_item_id=liquid_res.item_id,
            provenance=liquid_res.provenance,
        )
        for t in (liquid_res.tags if liquid_res else ())
    ]

    # 画质强化锚点
    quality_res = sampler.sample_quality_result(画质等级)
    slots["quality"] = [
        PromptFragment(
            text=t,
            source_slot="quality",
            source_item_id=quality_res.item_id,
            provenance=quality_res.provenance,
        )
        for t in (quality_res.tags if quality_res else ())
    ]

    # 3. 叠加风格配方
    recipe = sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None
    if recipe:
        recipe_id = recipe.get("id", "recipe_custom")
        for k in ["lighting_palette", "style_recipe", "focus_detail"]:
            val = recipe.get(k, "")
            if val:
                for t in split_top_level_tags(str(val)):
                    if t:
                        slots.setdefault("style_recipe", []).append(
                            PromptFragment(
                                text=t,
                                source_slot=f"recipe_{k}",
                                source_item_id=recipe_id,
                                provenance=TagProvenance(
                                    item_id=recipe_id,
                                    kind="style_recipe",
                                    semantic_ids=(f"recipe:{recipe_id}",),
                                ),
                            )
                        )

    # 4. 组装、冲突消解与统一 Finalize
    positive_prompt, atoms, rules_applied, source_atoms = assembler.assemble_result_with_sources(slots, rng=rng)
    negative_prompt = sampler.get_negative_prompt()

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
    return GenerationResult(
        positive=positive_prompt,
        negative=negative_prompt,
        description=chinese_desc,
        atoms=atoms,
        rules_applied=rules_applied,
        source_atoms=source_atoms,
    )


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
        res = _generate_structured(
            sampler=_sampler,
            assembler=_assembler,
            inputs={
                "预设模板": 预设模板,
                "风格配方": 风格配方,
                "场景大类": 场景大类,
                "剧情主题": 剧情主题,
                "景别构图": 景别构图,
                "拍摄视角": 拍摄视角,
                "裸露等级": 裸露等级,
                "服装款式": 服装款式,
                "服装状态": 服装状态,
                "发型发色": 发型发色,
                "饰品头饰": 饰品头饰,
                "妆容细节": 妆容细节,
                "姿势动作": 姿势动作,
                "情绪表情": 情绪表情,
                "光影预设": 光影预设,
                "胶片风格": 胶片风格,
                "液体效果": 液体效果,
                "纹身标记": 纹身标记,
                "道具物件": 道具物件,
                "角色设定": 角色设定,
                "真实微瑕": 真实微瑕,
                "画质等级": 画质等级,
            },
            rng=rng,
        )
        return (res.positive, res.negative, res.description)


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

    def browse_structured(
        self,
        预设模板: str,
        风格配方: str,
        画质等级: str,
        prompt_seed: int = -1,
    ) -> GenerationResult:
        rng, _ = _get_rng(prompt_seed)
        preset = _sampler.get_preset(预设模板, rng)
        if not preset:
            return GenerationResult(
                positive="",
                negative=_sampler.get_negative_prompt(),
                description="未找到指定预设",
                atoms=(),
                rules_applied=(),
                source_atoms=(),
            )

        recipe = _sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None
        assembly_res = _assembler.assemble_preset(preset, recipe, 画质等级, rng=rng)
        neg = _sampler.get_negative_prompt()

        desc = f"【预设模板】{preset.get('id', '')} {preset.get('name_zh', '')}"
        if recipe:
            desc += f" | 【叠加配方】{recipe.get('style_name', recipe.get('name_zh', ''))}"

        return GenerationResult(
            positive=assembly_res.prompt,
            negative=neg,
            description=desc,
            atoms=assembly_res.accepted_atoms,
            rules_applied=assembly_res.rules_applied,
            source_atoms=assembly_res.source_atoms,
        )

    def browse(
        self,
        预设模板: str,
        风格配方: str,
        画质等级: str,
        prompt_seed: int = -1,
    ) -> Tuple[str, str, str]:
        res = self.browse_structured(预设模板, 风格配方, 画质等级, prompt_seed)
        return (res.positive, res.negative, res.description)


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

    def combine_structured(self, prompt_seed: int = -1, **kwargs) -> GenerationResult:
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
                            provenance=TagProvenance(kind="user_input", semantic_ids=(f"slot:{slot_name}",)),
                        )
                    )
                    order += 1

        assembly_res = assemble_result(fragments, DATA_DIR, rng=rng)
        negative_prompt = _sampler.get_negative_prompt()
        desc = f"已成功拼装 {active_count} 个自定义槽位"

        return GenerationResult(
            positive=assembly_res.prompt,
            negative=negative_prompt,
            description=desc,
            atoms=assembly_res.accepted_atoms,
            rules_applied=assembly_res.rules_applied,
            source_atoms=assembly_res.source_atoms,
        )

    def combine(self, prompt_seed: int = -1, **kwargs) -> Tuple[str, str, str]:
        res = self.combine_structured(prompt_seed, **kwargs)
        return (res.positive, res.negative, res.description)


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
