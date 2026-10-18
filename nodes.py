"""
nodes.py — ComfyUI 原生自定义节点定义与注册

包含 4 个原生节点：
1. IYKYKPromptGenerator (🎴 IYKYK 提示词生成器) — 全维度槽位独立控制与情境自洽采样
2. IYKYKPresetBrowser (📋 IYKYK 模板浏览器) — 77 套手写预设模板与 8 大风格配方叠加
3. IYKYKCustomSlotCombiner (🧩 IYKYK 自定义槽位拼装器) — 自由输入多槽位文本与冲突消解
4. IYKYKPromptDiagnostics (🔎 IYKYK 提示词诊断) — 复用完整生成器输入并输出提示词与审计轨迹

特性：
- 完整支持 prompt_seed（-1 为动态随机抽卡，非负整数为 100% 确定性复现）
- 接入 ComfyUI 原生 control_after_generate 与 IS_CHANGED 缓存
- 统一调用 finalize_prompt 结构化流水线
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__:
    from .lib.assembler import CANONICAL_20_SLOTS, PromptAssembler, _inspect_blank_input, make_override_fragments, split_top_level_tags
    from .lib.context_affinity import compute_context_profile
    from .lib.errors import (
        CATEGORY_EXTERNAL_BUDGET_EXCEEDED,
        CATEGORY_EXTERNAL_OVERRIDE_CONFLICT,
        CATEGORY_GENERIC_ERROR,
        CATEGORY_INVALID_INPUT_TYPE,
        CATEGORY_SLOT_ALIAS_COLLISION,
        CATEGORY_UNRESOLVED_HARD_CONFLICT,
        PromptValidationError,
        UnresolvedConflictError,
    )
    from .lib.models import GenerationResult, ORDERED_CONTEXT_IDS, PromptAtom, PromptFragment, SelectionOrigin, SemanticFacts, TagProvenance, is_external_atom
    from .lib.rng import derive_substream_rng, recover_effective_seed
    from .lib.sampler import DataSampler, _is_none, get_selection_mode
    from .lib.slot_contract import AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_slot_mapping, normalize_slot_name
else:
    from lib.assembler import CANONICAL_20_SLOTS, PromptAssembler, _inspect_blank_input, make_override_fragments, split_top_level_tags
    from lib.context_affinity import compute_context_profile
    from lib.errors import (
        CATEGORY_EXTERNAL_BUDGET_EXCEEDED,
        CATEGORY_EXTERNAL_OVERRIDE_CONFLICT,
        CATEGORY_GENERIC_ERROR,
        CATEGORY_INVALID_INPUT_TYPE,
        CATEGORY_SLOT_ALIAS_COLLISION,
        CATEGORY_UNRESOLVED_HARD_CONFLICT,
        PromptValidationError,
        UnresolvedConflictError,
    )
    from lib.models import GenerationResult, ORDERED_CONTEXT_IDS, PromptAtom, PromptFragment, SelectionOrigin, SemanticFacts, TagProvenance, is_external_atom
    from lib.rng import derive_substream_rng, recover_effective_seed
    from lib.sampler import DataSampler, _is_none, get_selection_mode
    from lib.slot_contract import AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_slot_mapping, normalize_slot_name

DATA_DIR = Path(__file__).parent / "data"
_sampler = DataSampler(DATA_DIR)
_assembler = PromptAssembler(DATA_DIR)


def _get_rng(prompt_seed: int) -> Tuple[random.Random, int]:
    if prompt_seed is None or prompt_seed == -1:
        effective_seed = random.randint(0, 0x7FFFFFFF)
    else:
        effective_seed = int(prompt_seed)
    rng = random.Random(effective_seed)
    rng._effective_seed = effective_seed
    return rng, effective_seed


def _compute_is_changed(prompt_seed: int, inputs: Dict[str, Any]) -> Any:
    if prompt_seed == -1:
        return float("NaN")

    hasher = hashlib.sha256()
    hasher.update(str(prompt_seed).encode("utf-8"))

    for key in sorted(inputs.keys()):
        hasher.update(f"{key}:{inputs[key]}".encode("utf-8"))

    return hasher.hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# 20 槽位外部覆盖端口定义与提取器 (M4 契约)
# ═══════════════════════════════════════════════════════════════════════════

SLOT_OVERRIDE_TOOLTIPS: Dict[str, str] = {
    "scene_theme": "外部场景主题覆盖：完全重写场景与剧情环境；留空则回退至内部采样或预设。",
    "shot_type": "外部景别覆盖：完全重写画面景别（如 close-up shot）；留空则回退至内部采样或预设。",
    "camera_angle": "外部视角覆盖：完全重写机位拍摄角度（如 low angle）；留空则回退至内部采样或预设。",
    "character": "外部角色设定覆盖：完全重写角色身份设定；留空则回退至内部采样或预设。",
    "nudity": "外部裸露等级覆盖：完全重写裸露程度与状态；留空则回退至内部采样或预设。",
    "clothing": "外部服装款式覆盖：完全重写服装款式与状态；留空则回退至内部采样或预设。",
    "lighting": "外部光影氛围覆盖：完全重写光影与照明效果；留空则回退至内部采样或预设。",
    "pose": "外部姿势动作覆盖：完全重写人物姿势与手部动作；留空则回退至内部采样或预设。",
    "expression": "外部情绪表情覆盖：完全重写面部表情与眼神；留空则回退至内部采样或预设。",
    "makeup": "外部妆容细节覆盖：完全重写面部妆容与眼唇细节；留空则回退至内部采样或预设。",
    "hairstyle": "外部发型发色覆盖：完全重写发型、长度与发色；留空则回退至内部采样或预设。",
    "jewelry": "外部饰品头饰覆盖：完全重写首饰与佩戴物；留空则回退至内部采样或预设。",
    "imperfections": "外部真实微瑕覆盖：完全重写皮肤质感与微瑕细节；留空则回退至内部采样或预设。",
    "tattoo": "外部纹身标记覆盖：完全重写纹身部位与图案；留空则回退至内部采样或预设。",
    "props": "外部道具物件覆盖：完全重写手持与交互道具；留空则回退至内部采样或预设。",
    "liquids": "外部液体效果覆盖：完全重写体液与环境液体；留空则回退至内部采样或预设。",
    "film": "外部风格胶片覆盖：完全重写胶片色彩与颗粒质感；留空则回退至内部采样或预设。",
    "quality": "外部画质等级覆盖：完全重写渲染画质与摄影器材修饰；留空则回退至内部采样或预设。",
    "style_recipe": "外部风格配方覆盖：完全重写叠加的艺术风格配方；留空则回退至内部采样或预设。",
    "custom": "外部自定义追加覆盖：额外注入自定义提示词；留空则不追加。",
}

SLOT_OVERRIDE_OPTIONAL_INPUTS: Dict[str, Tuple[str, Dict[str, Any]]] = {
    f"{slot}_override": (
        "STRING",
        {
            "forceInput": True,
            "multiline": True,
            "default": "",
            "tooltip": SLOT_OVERRIDE_TOOLTIPS.get(
                slot, f"外部覆盖槽位 '{slot}'：连接外部文本完全重写该槽位；留空则回退至内部采样或预设。"
            ),
        },
    )
    for slot in (*SLOT_ORDER, *AUXILIARY_SLOT_ORDER)
}

_FRAMEWORK_RESERVED_KEYS = {
    "prompt_seed",
    "随机种子",
    "seed",
    "自定义提示词",
    "control_after_generate",
    "node_id",
    "extra_pnginfo",
    "dynprompt",
    "预设模板",
    "风格配方",
    "场景大类",
    "场景预设",
    "剧情主题",
    "景别构图",
    "拍摄视角",
    "裸露等级",
    "服装款式",
    "服装状态",
    "发型发色",
    "饰品头饰",
    "妆容细节",
    "姿势动作",
    "情绪表情",
    "光影预设",
    "胶片风格",
    "液体效果",
    "纹身标记",
    "道具物件",
    "角色设定",
    "真实微瑕",
    "画质等级",
}


def _extract_external_overrides(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    从节点输入字典中提取外部槽位覆盖 (M4 契约)：
    1. 识别并归一化来自 explicit dict inputs["external_overrides"] 的槽位；
    2. 识别并归一化来自 20 个规范槽位 *_override 端口的输入；
    3. 识别并归一化直接通过规范槽位名传入的输入（排除 22 个主生成器下拉框与框架元数据）；
    4. 凡非空且不在 20 槽位 (ALLOWED_SLOTS) 中的未知输入，严格 Fail-Closed 抛出 PromptValidationError；
    5. 返回归一化为规范槽位名且仅包含非空有效覆盖的字典。
    """
    candidate_overrides: Dict[str, Any] = {}

    # 1. 显式 external_overrides 字典
    ext_dict = inputs.get("external_overrides")
    if ext_dict is not None:
        if not isinstance(ext_dict, dict):
            raise PromptValidationError(
                f"external_overrides must be a dict if provided, got {type(ext_dict).__name__}"
            )
        for k, v in ext_dict.items():
            candidate_overrides[k] = v

    # 2. 扫描顶层输入字典中的 override 端口与直接槽位
    for k, v in inputs.items():
        if k == "external_overrides" or k in _FRAMEWORK_RESERVED_KEYS:
            continue
        candidate_overrides[k] = v

    if not candidate_overrides:
        return {}

    # 3. 校验并归一化全部候选覆盖输入 (Fail-Closed)
    normalized = normalize_slot_mapping(candidate_overrides)
    return normalized


def _extract_ordered_selections(atoms: Sequence[PromptAtom]) -> Tuple[SelectionOrigin, ...]:
    seen: set[SelectionOrigin] = set()
    ordered: List[SelectionOrigin] = []
    for atom in atoms:
        if atom.origin is not None and atom.origin not in seen:
            seen.add(atom.origin)
            ordered.append(atom.origin)
    return tuple(ordered)


def _custom_prompt_fragments(text: str, entry_point: str) -> List[PromptFragment]:
    if not text or not text.strip():
        return []
    origin = SelectionOrigin(
        entry_point=entry_point, mode="custom", selector="custom", raw_value=text,
    )
    return [
        PromptFragment(
            text=tag,
            source_slot="custom",
            order=order,
            provenance=TagProvenance(kind="user_input", semantic_ids=("slot:custom",)),
            origin=origin,
        )
        for order, tag in enumerate(split_top_level_tags(text.strip()))
    ]


def _make_slot_fragments(
    res: Any,
    slot_name: str,
    user_input_val: str,
    entry_point: str = "generator",
    selector: Optional[str] = None,
) -> List[PromptFragment]:
    if not res:
        return []
    mode = get_selection_mode(user_input_val)
    item_id = (
        getattr(res, "style_id", None)
        or getattr(res, "theme_id", None)
        or getattr(res, "item_id", None)
    )
    actual_selector = selector if selector is not None else slot_name
    slot_rationale = getattr(res, "sampling_rationale", None)
    slot_origin = SelectionOrigin(
        entry_point=entry_point,
        mode=mode,
        selector=actual_selector,
        selected_id=item_id,
        raw_value=user_input_val,
        parent_ids=(item_id,) if item_id else (),
        sampling_rationale=slot_rationale,
    )
    frags: List[PromptFragment] = []

    # ClothingSampleResult (if somehow called directly)
    if hasattr(res, "all_tags"):
        for order, stag in enumerate(res.all_tags):
            tag_item_id = (
                (stag.provenance.item_id if stag.provenance and stag.provenance.item_id else None)
                or item_id
            )
            tag_origin = SelectionOrigin(
                entry_point=entry_point,
                mode=mode,
                selector=actual_selector,
                selected_id=tag_item_id,
                raw_value=user_input_val,
                parent_ids=(tag_item_id,) if tag_item_id else (),
                sampling_rationale=slot_rationale,
            )
            frags.append(
                PromptFragment(
                    text=stag.text,
                    source_slot=slot_name,
                    source_item_id=tag_item_id,
                    order=order,
                    provenance=stag.provenance,
                    id=stag.id,
                    facts=stag.facts,
                    origin=stag.origin or tag_origin,
                )
            )
        return frags

    # ThemeSampleResult
    if hasattr(res, "tags") and hasattr(res, "theme_id"):
        for order, stag in enumerate(res.tags):
            frags.append(
                PromptFragment(
                    text=stag.text,
                    source_slot=slot_name,
                    source_item_id=item_id,
                    context_ids=getattr(res, "context_ids", ()),
                    order=order,
                    provenance=stag.provenance,
                    id=stag.id,
                    facts=stag.facts,
                    origin=slot_origin,
                )
            )
        return frags

    # SampleResult with sampled_tags
    if hasattr(res, "sampled_tags") and res.sampled_tags:
        for order, stag in enumerate(res.sampled_tags):
            frags.append(
                PromptFragment(
                    text=stag.text,
                    source_slot=slot_name,
                    source_item_id=item_id,
                    context_ids=getattr(res, "context_ids", ()),
                    exclusive_group=getattr(res, "exclusive_group", None),
                    order=order,
                    provenance=stag.provenance,
                    id=stag.id,
                    facts=stag.facts,
                    origin=slot_origin,
                )
            )
        return frags

    # Fallback to tags (str or SampledTag)
    tags = getattr(res, "tags", ())
    for order, t in enumerate(tags):
        t_text = t.text if hasattr(t, "text") else str(t)
        t_id = getattr(t, "id", f"{item_id}__tag_{order:03d}" if item_id else "")
        t_facts = getattr(t, "facts", SemanticFacts())
        t_prov = getattr(t, "provenance", None)
        if not t_prov or not t_prov.item_id:
            prov_item_id = item_id if item_id else t_id
            t_prov = TagProvenance(
                item_id=prov_item_id,
                kind=actual_selector,
                parent_ids=(item_id,) if item_id else (),
                semantic_ids=((f"{actual_selector}:{item_id}",) if item_id else ()),
            )
        frags.append(
            PromptFragment(
                text=t_text,
                source_slot=slot_name,
                source_item_id=item_id,
                context_ids=getattr(res, "context_ids", ()),
                exclusive_group=getattr(res, "exclusive_group", None),
                order=order,
                provenance=t_prov,
                id=t_id,
                facts=t_facts,
                origin=slot_origin,
            )
        )
    return frags


def _make_clothing_fragments(
    res: Any,
    clothing_style_val: str,
    clothing_state_val: str,
    entry_point: str = "generator",
) -> List[PromptFragment]:
    if not res:
        return []
    frags: List[PromptFragment] = []

    style_mode = get_selection_mode(clothing_style_val)
    style_id = getattr(res, "style_id", None)
    clothing_origin = SelectionOrigin(
        entry_point=entry_point,
        mode=style_mode,
        selector="clothing",
        selected_id=style_id,
        raw_value=clothing_style_val,
        parent_ids=(style_id,) if style_id else (),
        sampling_rationale=getattr(res, "sampling_rationale", None),
    )

    state_mode = get_selection_mode(clothing_state_val)
    state_id = getattr(res, "state_id", None)
    clothing_state_origin = SelectionOrigin(
        entry_point=entry_point,
        mode=state_mode,
        selector="clothing_state",
        selected_id=state_id,
        raw_value=clothing_state_val,
        parent_ids=(state_id,) if state_id else (),
    )

    order = 0
    # base_tags -> clothing_origin
    base_tags = getattr(res, "base_tags", ())
    for stag in base_tags:
        t_text = stag.text if hasattr(stag, "text") else str(stag)
        t_id = getattr(stag, "id", f"{style_id}__tag_{order:03d}" if style_id else "")
        t_facts = getattr(stag, "facts", SemanticFacts())
        t_prov = getattr(stag, "provenance", None)
        if not t_prov or not t_prov.item_id:
            prov_item_id = style_id if style_id else t_id
            t_prov = TagProvenance(
                item_id=prov_item_id,
                kind="clothing",
                parent_ids=(style_id,) if style_id else (),
                semantic_ids=((f"clothing:{style_id}",) if style_id else ()),
            )
        frags.append(
            PromptFragment(
                text=t_text,
                source_slot="clothing",
                source_item_id=style_id,
                order=order,
                provenance=t_prov,
                id=t_id,
                facts=t_facts,
                origin=clothing_origin,
            )
        )
        order += 1

    # state_tags -> clothing_state_origin (unless linkage_override, which overrides clothing style tags)
    state_tags = getattr(res, "state_tags", ())
    is_linkage_override = (state_id == "linkage_override")
    for stag in state_tags:
        t_text = stag.text if hasattr(stag, "text") else str(stag)
        t_id = getattr(stag, "id", f"{state_id}__tag_{order:03d}" if state_id else "")
        t_facts = getattr(stag, "facts", SemanticFacts())
        t_prov = getattr(stag, "provenance", None)
        if is_linkage_override:
            effective_origin = clothing_origin
            effective_slot = "clothing"
            effective_item_id = style_id
            if not t_prov or not t_prov.item_id:
                t_prov = TagProvenance(
                    item_id=style_id or t_id,
                    kind="clothing",
                    parent_ids=(style_id,) if style_id else (),
                    semantic_ids=((f"clothing:{style_id}",) if style_id else ()),
                )
        else:
            effective_origin = clothing_state_origin
            effective_slot = "clothing"
            effective_item_id = state_id or style_id
            if not t_prov or not t_prov.item_id:
                t_prov = TagProvenance(
                    item_id=state_id if state_id else (style_id or t_id),
                    kind="clothing_state",
                    parent_ids=(state_id,) if state_id else ((style_id,) if style_id else ()),
                    semantic_ids=((f"clothing_state:{state_id}",) if state_id else ()),
                )
        frags.append(
            PromptFragment(
                text=t_text,
                source_slot=effective_slot,
                source_item_id=effective_item_id,
                order=order,
                provenance=t_prov,
                id=t_id,
                facts=t_facts,
                origin=effective_origin,
            )
        )
        order += 1

    # extension_tags -> clothing_origin (extensions of clothing style)
    extension_tags = getattr(res, "extension_tags", ())
    for stag in extension_tags:
        t_text = stag.text if hasattr(stag, "text") else str(stag)
        t_facts = getattr(stag, "facts", SemanticFacts())
        t_prov = getattr(stag, "provenance", None)
        item_id = t_prov.item_id if (t_prov and t_prov.item_id) else style_id
        t_id = getattr(stag, "id", f"{item_id}__ext_{order:03d}" if item_id else "")
        if not t_prov or not t_prov.item_id:
            prov_item_id = style_id if style_id else t_id
            t_prov = TagProvenance(
                item_id=prov_item_id,
                kind="clothing",
                parent_ids=(style_id,) if style_id else (),
                semantic_ids=((f"clothing:{style_id}",) if style_id else ()),
            )
        frags.append(
            PromptFragment(
                text=t_text,
                source_slot="clothing",
                source_item_id=item_id,
                order=order,
                provenance=t_prov,
                id=t_id,
                facts=t_facts,
                origin=clothing_origin,
            )
        )
        order += 1

    # Fallback if res didn't have base_tags/state_tags/extension_tags
    if not base_tags and not state_tags and not extension_tags and hasattr(res, "all_tags"):
        for stag in getattr(res, "all_tags", ()):
            t_text = stag.text if hasattr(stag, "text") else str(stag)
            t_id = getattr(stag, "id", f"{style_id}__tag_{order:03d}" if style_id else "")
            frags.append(
                PromptFragment(
                    text=t_text,
                    source_slot="clothing",
                    source_item_id=style_id,
                    order=order,
                    provenance=getattr(stag, "provenance", None),
                    id=t_id,
                    facts=getattr(stag, "facts", SemanticFacts()),
                    origin=clothing_origin,
                )
            )
            order += 1

    return frags


def _generate_structured(
    sampler: DataSampler,
    assembler: PromptAssembler,
    inputs: Dict[str, Any],
    rng: random.Random,
    entry_point: str = "generator",
    effective_seed: Optional[int] = None,
) -> GenerationResult:
    """提示词生成纯函数流水线 (修订 7 纯函数与丰富 Provenance 契约)。

    不变量：
    - 无副作用、无类/实例级可变状态存储；
    - 统一返回不可变 GenerationResult 对象，携带 positive, negative, description, atoms 与 rules_applied。
    """
    if effective_seed is None:
        effective_seed = getattr(rng, "_effective_seed", None)
        if effective_seed is None:
            effective_seed = recover_effective_seed(rng)
        if effective_seed is None:
            raw_seed = inputs.get("prompt_seed")
            if raw_seed is None or raw_seed == -1:
                raw_seed = inputs.get("随机种子", inputs.get("seed", -1))
            if raw_seed is not None and raw_seed != -1:
                effective_seed = int(raw_seed)
        if effective_seed is None:
            effective_seed = rng.getrandbits(32)

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

    custom_fragments = _custom_prompt_fragments(inputs.get("自定义提示词", ""), entry_point)

    # 提取外部覆盖字典并预先统一规范化所有槽位别名 (M4 契约)
    external_overrides = _extract_external_overrides(inputs)

    # 1. 检查是否使用预设模板
    if not _is_none(预设模板):
        rng_preset = derive_substream_rng(effective_seed, "selector:preset_core")
        preset = sampler.get_preset(预设模板, rng_preset)
        if preset:
            rng_recipe = derive_substream_rng(effective_seed, "selector:style_recipe")
            recipe = sampler.get_style_recipe(风格配方, rng_recipe) if not _is_none(风格配方) else None
            assembly_res = assembler.assemble_preset(
                preset,
                recipe,
                画质等级,
                rng=rng,
                entry_point=entry_point,
                extra_fragments=custom_fragments,
                preset_raw_value=预设模板,
                recipe_raw_value=风格配方 if not _is_none(风格配方) else None,
                external_overrides=external_overrides,
            )
            neg = sampler.get_negative_prompt()
            desc = f"【预设模板】{preset.get('id', '')} {preset.get('name_zh', '')}"
            if recipe:
                desc += f" | 【叠加配方】{recipe.get('style_name', recipe.get('name_zh', ''))}"
            selections = _extract_ordered_selections(assembly_res.source_atoms)
            return GenerationResult(
                positive=assembly_res.prompt,
                negative=neg,
                description=desc,
                atoms=assembly_res.accepted_atoms,
                rules_applied=assembly_res.rules_applied,
                source_atoms=assembly_res.source_atoms,
                effective_seed=effective_seed,
                context_profile=assembly_res.context_profile,
                selections=selections,
                resolution_report=assembly_res.resolution_report,
                deduplicated_atoms=assembly_res.deduplicated_atoms,
                budget_filtered_atoms=assembly_res.budget_filtered_atoms,
                deduplication_records=assembly_res.deduplication_records,
                budget_filter_records=assembly_res.budget_filter_records,
                overrides=assembly_res.overrides,
            )

    # 2. 采样各槽位
    # 槽位 1: 场景 + 主题
    rng_scene = derive_substream_rng(effective_seed, "selector:scene_theme")
    scene_res = sampler.sample_scene_result(场景大类, rng_scene)
    slots: Dict[str, List[Any]] = {}

    slots["scene_theme"] = _make_slot_fragments(scene_res, "scene_theme", 场景大类, entry_point, selector="scene")

    rng_theme = derive_substream_rng(effective_seed, "selector:theme")
    theme_res = sampler.sample_theme_result(剧情主题, rng_theme)
    if theme_res:
        slots["scene_theme"].extend(_make_slot_fragments(theme_res, "scene_theme", 剧情主题, entry_point, selector="theme"))

    scene_cids = scene_res.context_ids if (scene_res and hasattr(scene_res, "context_ids")) else ()
    theme_cids = theme_res.context_ids if (theme_res and hasattr(theme_res, "context_ids")) else ()
    scene_item_id = getattr(scene_res, "item_id", None)
    theme_item_id = getattr(theme_res, "theme_id", getattr(theme_res, "item_id", None))

    primary_context = scene_res.context_ids[0] if (scene_res and scene_res.context_ids) else "generic"
    context = primary_context if primary_context != "generic" else sampler.detect_context(场景大类, 剧情主题)

    # 外部场景覆盖情境自洽更新 (M3 契约):
    ext_scene = external_overrides.get("scene_theme")
    if ext_scene:
        if isinstance(ext_scene, (list, tuple)):
            ext_scene_str = ", ".join(str(getattr(x, "text", x)) for x in ext_scene if str(getattr(x, "text", x)).strip())
        elif isinstance(ext_scene, str):
            ext_scene_str = ext_scene.strip()
        else:
            ext_scene_str = str(ext_scene).strip()

        if ext_scene_str:
            detected_ext_ctx = sampler.detect_context(ext_scene_str, "")
            ctx_val = detected_ext_ctx if (detected_ext_ctx and detected_ext_ctx in ORDERED_CONTEXT_IDS) else "generic"
            scene_cids = (ctx_val,)
            primary_context = ctx_val
            context = ctx_val
            # 彻底清除被替换的旧场景/主题贡献及来源 ID
            theme_cids = ()
            theme_item_id = None
            scene_item_id = f"override_scene_{ctx_val}"
            # 注意：不在此处提前覆写 slots["scene_theme"]，保留原采样片段供 apply_slot_overrides 真实捕获 replaced_texts

    context_profile = compute_context_profile(scene_cids, theme_cids, scene_item_id, theme_item_id)

    # 槽位 3 & 4: 裸露等级与服装穿脱联动 (优先解析裸露等级以约束视角与服装)
    rng_nudity = derive_substream_rng(effective_seed, "selector:nudity")
    nudity_res, lvl_code = sampler.sample_nudity_result(裸露等级, rng_nudity)
    slots["nudity"] = _make_slot_fragments(nudity_res, "nudity", 裸露等级, entry_point)

    # 槽位 2: 景别 + 视角
    rng_shot = derive_substream_rng(effective_seed, "selector:shot_type")
    shot_res = sampler.sample_shot_type_result(景别构图, rng_shot, context_profile=context_profile)
    slots["shot_type"] = _make_slot_fragments(shot_res, "shot_type", 景别构图, entry_point)
    shot_type_id = shot_res.item_id if shot_res else None

    rng_angle = derive_substream_rng(effective_seed, "selector:camera_angle")
    angle_res = sampler.sample_camera_angle_result(
        拍摄视角,
        rng_angle,
        context_profile=context_profile,
        nudity_level_code=lvl_code,
        shot_type_id=shot_type_id,
    )
    slots["camera_angle"] = _make_slot_fragments(angle_res, "camera_angle", 拍摄视角, entry_point)

    rng_clothing = derive_substream_rng(effective_seed, "selector:clothing")
    clothing_res = sampler.sample_clothing_result(
        服装款式, 服装状态, lvl_code, rng_clothing, context=context, context_profile=context_profile
    )
    slots["clothing"] = _make_clothing_fragments(clothing_res, 服装款式, 服装状态, entry_point)

    # 槽位 5: 光影氛围
    rng_lighting = derive_substream_rng(effective_seed, "selector:lighting")
    lighting_res = sampler.sample_lighting_result(
        光影预设, rng_lighting, nudity_level_code=lvl_code, context_profile=context_profile
    )
    slots["lighting"] = _make_slot_fragments(lighting_res, "lighting", 光影预设, entry_point)

    # 槽位 6: 姿势动作
    rng_pose = derive_substream_rng(effective_seed, "selector:pose")
    pose_res = sampler.sample_pose_result(
        姿势动作,
        rng_pose,
        nudity_level_code=lvl_code,
        context_profile=context_profile,
        shot_type_id=shot_type_id,
    )
    slots["pose"] = _make_slot_fragments(pose_res, "pose", 姿势动作, entry_point)

    # 槽位 7: 表情眼神
    rng_expr = derive_substream_rng(effective_seed, "selector:expression")
    expression_res = sampler.sample_expression_result(情绪表情, rng_expr, context_profile=context_profile)
    slots["expression"] = _make_slot_fragments(expression_res, "expression", 情绪表情, entry_point)

    # 槽位 8: 风格胶片
    rng_film = derive_substream_rng(effective_seed, "selector:film")
    film_res = sampler.sample_film_result(胶片风格, rng_film, context_profile=context_profile)
    slots["film"] = _make_slot_fragments(film_res, "film", 胶片风格, entry_point)

    # 槽位 9: 妆容细节
    rng_makeup = derive_substream_rng(effective_seed, "selector:makeup")
    makeup_res = sampler.sample_makeup_result(妆容细节, rng_makeup, context=context, context_profile=context_profile)
    slots["makeup"] = _make_slot_fragments(makeup_res, "makeup", 妆容细节, entry_point)

    # 槽位 10: 发型与饰品
    rng_hair = derive_substream_rng(effective_seed, "selector:hairstyle")
    hairstyle_res = sampler.sample_hairstyle_result(发型发色, rng_hair, context=context, context_profile=context_profile)
    slots["hairstyle"] = _make_slot_fragments(hairstyle_res, "hairstyle", 发型发色, entry_point)

    rng_jewel = derive_substream_rng(effective_seed, "selector:jewelry")
    jewelry_res = sampler.sample_jewelry_result(饰品头饰, rng_jewel, context=context, context_profile=context_profile)
    slots["jewelry"] = _make_slot_fragments(jewelry_res, "jewelry", 饰品头饰, entry_point)

    # 槽位 11: 真实微瑕
    rng_imp = derive_substream_rng(effective_seed, "selector:imperfections")
    imperfection_res = sampler.sample_imperfections_result(真实微瑕, rng_imp)
    slots["imperfections"] = _make_slot_fragments(imperfection_res, "imperfections", 真实微瑕, entry_point)

    # 槽位 12: 纹身标记（仅在显式配置时生效）
    rng_tat = derive_substream_rng(effective_seed, "selector:tattoo")
    tattoo_res = sampler.sample_tattoo_result(纹身标记, rng_tat, context=context, context_profile=context_profile)
    slots["tattoo"] = _make_slot_fragments(tattoo_res, "tattoo", 纹身标记, entry_point)

    # 槽位 13: 道具物件
    pose_id = pose_res.item_id if pose_res else None
    hand_state = None
    if pose_res and pose_res.sampled_tags:
        for t in pose_res.sampled_tags:
            hs = getattr(t.facts, "hand_state", None) if t.facts else None
            if hs in ("both_busy", "restrained"):
                hand_state = "both_busy"
                break
            elif hs == "one_busy" and hand_state != "both_busy":
                hand_state = "one_busy"
            elif hs == "free" and not hand_state:
                hand_state = "free"

    rng_prop = derive_substream_rng(effective_seed, "selector:props")
    prop_res = sampler.sample_prop_result(
        道具物件,
        rng_prop,
        context=context,
        context_profile=context_profile,
        pose_id=pose_id,
        hand_state=hand_state,
        nudity_level_code=lvl_code,
    )
    slots["props"] = _make_slot_fragments(prop_res, "props", 道具物件, entry_point)

    # 槽位 14: 人格角色
    rng_char = derive_substream_rng(effective_seed, "selector:character")
    character_res = sampler.sample_character_result(角色设定, rng_char, context=context, context_profile=context_profile)
    slots["character"] = _make_slot_fragments(character_res, "character", 角色设定, entry_point)

    # 槽位 15: 液体体液
    rng_liq = derive_substream_rng(effective_seed, "selector:liquids")
    liquid_res = sampler.sample_liquid_result(液体效果, rng_liq, context=context, context_profile=context_profile)
    slots["liquids"] = _make_slot_fragments(liquid_res, "liquids", 液体效果, entry_point)

    # 画质强化锚点
    quality_res = sampler.sample_quality_result(画质等级)
    slots["quality"] = _make_slot_fragments(quality_res, "quality", 画质等级, entry_point)

    # 3. 叠加风格配方
    rng_recipe = derive_substream_rng(effective_seed, "selector:style_recipe")
    recipe = sampler.get_style_recipe(风格配方, rng_recipe) if not _is_none(风格配方) else None
    if recipe:
        recipe_id = recipe.get("id", "recipe_custom")
        raw_r = 风格配方 if not _is_none(风格配方) else (recipe.get("style_name") or recipe.get("name_zh") or recipe_id)
        recipe_origin = SelectionOrigin(
            entry_point=entry_point,
            mode="recipe",
            selector="style_recipe",
            selected_id=recipe_id,
            raw_value=raw_r,
            parent_ids=(recipe_id,),
        )
        recipe_frags = recipe.get("fragments")
        if recipe_frags and isinstance(recipe_frags, list):
            for f_data in recipe_frags:
                text = f_data.get("text", "")
                if text:
                    f_id = f_data.get("id", "")
                    f_slot = f_data.get("slot", "style_recipe")
                    f_facts = SemanticFacts.from_dict(f_data.get("facts", {}))
                    slots.setdefault("style_recipe", []).append(
                        PromptFragment(
                            text=text,
                            source_slot=f_slot,
                            source_item_id=recipe_id,
                            provenance=TagProvenance(
                                item_id=f_id or recipe_id,
                                kind="style_recipe",
                                semantic_ids=(f"recipe:{recipe_id}",),
                                parent_ids=(recipe_id,),
                            ),
                            id=f_id,
                            facts=f_facts,
                            origin=recipe_origin,
                        )
                    )
        else:
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
                                    origin=recipe_origin,
                                )
                            )

    if custom_fragments:
        slots["custom"] = custom_fragments

    # 4. 组装、冲突消解与统一 Finalize
    assembly_res = assembler.assemble_slots(
        slots,
        rng=rng,
        context_profile=context_profile,
        external_overrides=external_overrides,
        entry_point=entry_point,
    )
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
    selections = _extract_ordered_selections(assembly_res.source_atoms)
    return GenerationResult(
        positive=assembly_res.prompt,
        negative=negative_prompt,
        description=chinese_desc,
        atoms=assembly_res.accepted_atoms,
        rules_applied=assembly_res.rules_applied,
        source_atoms=assembly_res.source_atoms,
        effective_seed=effective_seed,
        context_profile=context_profile,
        selections=selections,
        resolution_report=assembly_res.resolution_report,
        deduplicated_atoms=assembly_res.deduplicated_atoms,
        budget_filtered_atoms=assembly_res.budget_filtered_atoms,
        deduplication_records=assembly_res.deduplication_records,
        budget_filter_records=assembly_res.budget_filter_records,
        overrides=assembly_res.overrides,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 节点 1: 🎴 IYKYK 提示词生成器
# ═══════════════════════════════════════════════════════════════════════════

class IYKYKPromptGenerator:
    """提示词生成器"""

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
                "自定义提示词": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "追加到正向提示词，支持多行文本、LoRA 和权重语法；留空不追加。",
                }),
                **SLOT_OVERRIDE_OPTIONAL_INPUTS,
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("正面提示词 (STRING)", "负面提示词 (STRING)", "中文场景描述 (STRING)")
    FUNCTION = "generate"
    CATEGORY = "IYKYK / 提示词生成"

    @classmethod
    def IS_CHANGED(cls, prompt_seed: int = -1, **kwargs) -> Any:
        return _compute_is_changed(prompt_seed, kwargs)

    def generate_structured(
        self,
        预设模板: str = "无 (None)",
        风格配方: str = "无 (None)",
        场景大类: str = "随机 (Random)",
        剧情主题: str = "随机 (Random)",
        景别构图: str = "自动 (Auto)",
        拍摄视角: str = "自动 (Auto)",
        裸露等级: str = "随机 (Random)",
        服装款式: str = "随机 (Random)",
        服装状态: str = "自动联动裸露等级 (Auto Link Nudity)",
        发型发色: str = "随机 (Random)",
        饰品头饰: str = "无 (None)",
        妆容细节: str = "无 (None)",
        姿势动作: str = "随机 (Random)",
        情绪表情: str = "随机 (Random)",
        光影预设: str = "自动 (Auto)",
        胶片风格: str = "无 (None)",
        液体效果: str = "无 (None)",
        纹身标记: str = "无 (None)",
        道具物件: str = "无 (None)",
        角色设定: str = "无 (None)",
        真实微瑕: str = "无 (None)",
        画质等级: str = "高清写真 (High)",
        prompt_seed: int = -1,
        自定义提示词: str = "",
        **kwargs: Any,
    ) -> GenerationResult:
        rng, effective_seed = _get_rng(prompt_seed)
        inputs = {
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
            "自定义提示词": 自定义提示词,
        }
        inputs.update(kwargs)
        res = _generate_structured(
            sampler=_sampler,
            assembler=_assembler,
            inputs=inputs,
            rng=rng,
            entry_point="generator",
            effective_seed=effective_seed,
        )
        return res

    def generate(
        self,
        预设模板: str = "无 (None)",
        风格配方: str = "无 (None)",
        场景大类: str = "随机 (Random)",
        剧情主题: str = "随机 (Random)",
        景别构图: str = "自动 (Auto)",
        拍摄视角: str = "自动 (Auto)",
        裸露等级: str = "随机 (Random)",
        服装款式: str = "随机 (Random)",
        服装状态: str = "自动联动裸露等级 (Auto Link Nudity)",
        发型发色: str = "随机 (Random)",
        饰品头饰: str = "无 (None)",
        妆容细节: str = "无 (None)",
        姿势动作: str = "随机 (Random)",
        情绪表情: str = "随机 (Random)",
        光影预设: str = "自动 (Auto)",
        胶片风格: str = "无 (None)",
        液体效果: str = "无 (None)",
        纹身标记: str = "无 (None)",
        道具物件: str = "无 (None)",
        角色设定: str = "无 (None)",
        真实微瑕: str = "无 (None)",
        画质等级: str = "高清写真 (High)",
        prompt_seed: int = -1,
        自定义提示词: str = "",
        **kwargs: Any,
    ) -> Tuple[str, str, str]:
        res = self.generate_structured(
            预设模板=预设模板,
            风格配方=风格配方,
            场景大类=场景大类,
            剧情主题=剧情主题,
            景别构图=景别构图,
            拍摄视角=拍摄视角,
            裸露等级=裸露等级,
            服装款式=服装款式,
            服装状态=服装状态,
            发型发色=发型发色,
            饰品头饰=饰品头饰,
            妆容细节=妆容细节,
            姿势动作=姿势动作,
            情绪表情=情绪表情,
            光影预设=光影预设,
            胶片风格=胶片风格,
            液体效果=液体效果,
            纹身标记=纹身标记,
            道具物件=道具物件,
            角色设定=角色设定,
            真实微瑕=真实微瑕,
            画质等级=画质等级,
            prompt_seed=prompt_seed,
            自定义提示词=自定义提示词,
            **kwargs,
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
                "自定义提示词": ("STRING", {
                    "multiline": True,
                    "default": "",
                    "tooltip": "追加到正向提示词，支持多行文本、LoRA 和权重语法；留空不追加。",
                }),
                **SLOT_OVERRIDE_OPTIONAL_INPUTS,
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
        自定义提示词: str = "",
        **kwargs: Any,
    ) -> GenerationResult:
        rng, effective_seed = _get_rng(prompt_seed)
        preset = _sampler.get_preset(预设模板, rng)
        if not preset:
            return GenerationResult(
                positive="",
                negative=_sampler.get_negative_prompt(),
                description="未找到指定预设",
                atoms=(),
                rules_applied=(),
                source_atoms=(),
                effective_seed=effective_seed,
            )

        recipe = _sampler.get_style_recipe(风格配方, rng) if not _is_none(风格配方) else None

        # 提取外部覆盖字典 (M4 契约)
        all_inputs = dict(kwargs)
        all_inputs["预设模板"] = 预设模板
        all_inputs["风格配方"] = 风格配方
        all_inputs["画质等级"] = 画质等级
        all_inputs["自定义提示词"] = 自定义提示词
        all_inputs["prompt_seed"] = prompt_seed
        external_overrides = _extract_external_overrides(all_inputs)

        assembly_res = _assembler.assemble_preset(
            preset,
            recipe,
            画质等级,
            rng=rng,
            entry_point="preset_browser",
            extra_fragments=_custom_prompt_fragments(自定义提示词, "preset_browser"),
            preset_raw_value=预设模板,
            recipe_raw_value=风格配方 if not _is_none(风格配方) else None,
            external_overrides=external_overrides,
        )
        neg = _sampler.get_negative_prompt()

        desc = f"【预设模板】{preset.get('id', '')} {preset.get('name_zh', '')}"
        if recipe:
            desc += f" | 【叠加配方】{recipe.get('style_name', recipe.get('name_zh', ''))}"

        selections = _extract_ordered_selections(assembly_res.source_atoms)
        return GenerationResult(
            positive=assembly_res.prompt,
            negative=neg,
            description=desc,
            atoms=assembly_res.accepted_atoms,
            rules_applied=assembly_res.rules_applied,
            source_atoms=assembly_res.source_atoms,
            effective_seed=effective_seed,
            context_profile=assembly_res.context_profile,
            selections=selections,
            resolution_report=assembly_res.resolution_report,
            deduplicated_atoms=assembly_res.deduplicated_atoms,
            budget_filtered_atoms=assembly_res.budget_filtered_atoms,
            deduplication_records=assembly_res.deduplication_records,
            budget_filter_records=assembly_res.budget_filter_records,
            overrides=assembly_res.overrides,
        )

    def browse(
        self,
        预设模板: str,
        风格配方: str,
        画质等级: str,
        prompt_seed: int = -1,
        自定义提示词: str = "",
        **kwargs: Any,
    ) -> Tuple[str, str, str]:
        res = self.browse_structured(
            预设模板=预设模板,
            风格配方=风格配方,
            画质等级=画质等级,
            prompt_seed=prompt_seed,
            自定义提示词=自定义提示词,
            **kwargs,
        )
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
                # 保持历史 15 槽位完全兼容且默认值与顺序完全一致
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
                # 补齐 5 大规范正交槽位 (M4 契约)
                "拍摄视角": ("STRING", {"multiline": True, "default": ""}),
                "发型发色": ("STRING", {"multiline": True, "default": ""}),
                "饰品头饰": ("STRING", {"multiline": True, "default": ""}),
                "角色设定": ("STRING", {"multiline": True, "default": ""}),
                "风格配方": ("STRING", {"multiline": True, "default": ""}),
                # 外部覆盖端口 (20 规范槽位 *_override)
                **SLOT_OVERRIDE_OPTIONAL_INPUTS,
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
        rng, effective_seed = _get_rng(prompt_seed)

        # 1. 划分输入：框架保留键、外部覆盖键、基础自定义槽位键
        base_inputs: Dict[str, Any] = {}
        raw_override_inputs: Dict[str, Any] = {}

        ext_dict = kwargs.get("external_overrides")
        if ext_dict is not None:
            if not isinstance(ext_dict, dict):
                raise PromptValidationError(
                    f"external_overrides must be a dict if provided, got {type(ext_dict).__name__}"
                )
            raw_override_inputs["external_overrides"] = ext_dict

        for k, v in kwargs.items():
            if k in ("prompt_seed", "control_after_generate", "node_id", "extra_pnginfo", "dynprompt", "external_overrides"):
                continue
            if k.endswith("_override"):
                raw_override_inputs[k] = v
            else:
                base_inputs[k] = v

        # 处理画质默认值与显式 quality 覆盖的共存：
        # 若在 override 或 base 中提供了显式画质覆盖 (quality, quality_override, 画质等级 等非空)，
        # 且 "画质修饰" 仍为其静态默认值 "best quality, masterpiece"，则自动让位于显式覆盖
        has_explicit_quality = False
        for q_key in ("quality", "quality_override", "画质等级", "画质等级_override"):
            val = kwargs.get(q_key)
            if val is not None and str(val).strip():
                has_explicit_quality = True
                break
        if not has_explicit_quality and ext_dict and isinstance(ext_dict, dict):
            for q_key in ("quality", "quality_override", "画质等级", "画质等级_override", "画质修饰", "画质"):
                val = ext_dict.get(q_key)
                if val is not None and str(val).strip():
                    has_explicit_quality = True
                    break
        if has_explicit_quality and base_inputs.get("画质修饰") == "best quality, masterpiece":
            del base_inputs["画质修饰"]

        # 2. 分别执行规范化与 Fail-Closed 校验
        normalized_base = normalize_slot_mapping(base_inputs)
        clean_overrides = _extract_external_overrides(raw_override_inputs)

        # 3. 构建基础自定义槽位片段 (标记 mode="custom")
        slot_fragments: Dict[str, List[PromptFragment]] = {}
        order = 0

        for slot_name in (*SLOT_ORDER, *AUXILIARY_SLOT_ORDER):
            if slot_name in normalized_base:
                val = normalized_base[slot_name]
                if val is not None and str(val).strip():
                    slot_raw_val = str(val)
                    tags = split_top_level_tags(slot_raw_val.strip())
                    frags: List[PromptFragment] = []
                    for t in tags:
                        slot_origin = SelectionOrigin(
                            entry_point="custom_combiner",
                            mode="custom",
                            selector=slot_name,
                            raw_value=slot_raw_val,
                        )
                        frags.append(
                            PromptFragment(
                                text=t,
                                source_slot=slot_name,
                                order=order,
                                provenance=TagProvenance(
                                    item_id=f"combiner_{slot_name}_{order:03d}",
                                    kind="user_input",
                                    semantic_ids=(f"slot:{slot_name}",),
                                ),
                                origin=slot_origin,
                                facts=SemanticFacts(),
                            )
                        )
                        order += 1
                    if frags:
                        slot_fragments[slot_name] = frags

        # 4. 复用 M3 标准 assemble_slots 执行覆盖与消解装配
        assembly_res = _assembler.assemble_slots(
            slot_fragments,
            rng=rng,
            external_overrides=clean_overrides,
            entry_point="custom_combiner",
        )

        negative_prompt = _sampler.get_negative_prompt()

        # 计算激活槽位数：基础有效槽位与外部有效覆盖槽位的并集
        active_slots = set()
        for slot, val in normalized_base.items():
            if val is not None and str(val).strip():
                active_slots.add(slot)
        for slot, val in clean_overrides.items():
            if val is not None and str(val).strip():
                active_slots.add(slot)
        desc = f"已成功拼装 {len(active_slots)} 个自定义槽位"
        selections = _extract_ordered_selections(assembly_res.source_atoms)

        return GenerationResult(
            positive=assembly_res.prompt,
            negative=negative_prompt,
            description=desc,
            atoms=assembly_res.accepted_atoms,
            rules_applied=assembly_res.rules_applied,
            source_atoms=assembly_res.source_atoms,
            effective_seed=effective_seed,
            context_profile=assembly_res.context_profile,
            selections=selections,
            resolution_report=assembly_res.resolution_report,
            deduplicated_atoms=assembly_res.deduplicated_atoms,
            budget_filtered_atoms=assembly_res.budget_filtered_atoms,
            deduplication_records=assembly_res.deduplication_records,
            budget_filter_records=assembly_res.budget_filter_records,
            overrides=assembly_res.overrides,
        )

    def combine(self, prompt_seed: int = -1, **kwargs) -> Tuple[str, str, str]:
        res = self.combine_structured(prompt_seed, **kwargs)
        return (res.positive, res.negative, res.description)


# ═══════════════════════════════════════════════════════════════════════════
# 审计 JSON 确定性格式化器
# ═══════════════════════════════════════════════════════════════════════════

def _quantize_weight(w: float) -> float:
    """对情境权重精确四舍五入至小数点后 12 位，并消除负零 (-0.0 -> 0.0)。"""
    q = round(float(w), 12)
    return 0.0 if q == 0.0 else q


def _serialize_atom_item(a: PromptAtom, is_accepted: bool) -> Dict[str, Any]:
    """序列化原子 Span 身份、层级序号与完整来源元数据。"""
    return {
        "atom_id": a.atom_id,
        "id": a.id,
        "is_accepted": is_accepted,
        "parent_ids": list(a.provenance.parent_ids),
        "semantic_ids": list(a.provenance.semantic_ids),
        "source_item_id": a.source_item_id,
        "source_slot": a.source_slot,
        "span_order": a.span_order,
        "tag_order": a.tag_order,
        "target_id": a.target_id,
        "text": a.text,
    }


def serialize_raw_input(val: Any) -> Any:
    """稳定且无损失地将任意原始输入对象（含 PromptFragment, dict, tuple 等）序列化为标准 JSON 数据。"""
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (list, tuple, set)):
        return [serialize_raw_input(x) for x in val]
    if isinstance(val, dict):
        return {str(k): serialize_raw_input(v) for k, v in val.items()}
    if hasattr(val, "text"):
        d: Dict[str, Any] = {"text": str(getattr(val, "text", ""))}
        if getattr(val, "id", None):
            d["id"] = str(getattr(val, "id"))
        if getattr(val, "source_slot", None):
            d["source_slot"] = str(getattr(val, "source_slot"))
        return d
    return str(val)


def _safe_extract_slot_inputs(inputs: Optional[Dict[str, Any]]) -> Dict[str, List[Tuple[str, Any]]]:
    """
    安全提取原始输入字典中针对各槽位的输入键值对列表，杜绝任何二次抛错。
    返回 {canonical_slot: [(raw_key, raw_val), ...]}
    """
    res: Dict[str, List[Tuple[str, Any]]] = {}
    if not inputs or not isinstance(inputs, dict):
        return res

    # 1. external_overrides 字典
    ext_dict = inputs.get("external_overrides")
    if isinstance(ext_dict, dict):
        for k, v in ext_dict.items():
            norm = normalize_slot_name(k)
            res.setdefault(norm, []).append((k, v))

    # 2. 顶层输入键
    for k, v in inputs.items():
        if k == "external_overrides" or k in _FRAMEWORK_RESERVED_KEYS:
            continue
        norm = normalize_slot_name(k)
        res.setdefault(norm, []).append((k, v))

    return res


def build_error_override_records(
    inputs: Optional[Dict[str, Any]],
    err: Optional[Exception] = None,
    existing_overrides: Optional[Sequence[Any]] = None,
) -> List[Dict[str, Any]]:
    """在生成失败或消解抛错时，根据已有覆盖记录或原始输入安全构建 20 规范槽位的错误状态覆盖报告。"""
    # 确定涉及错误/冲突的规范槽位集合
    err_slots = set(getattr(err, "slots", ()))
    unres_conflicts = getattr(err, "unresolved_conflicts", ())
    for c in unres_conflicts:
        if isinstance(c, (list, tuple)):
            for item in c:
                if isinstance(item, str):
                    err_slots.add(normalize_slot_name(item))

    # 判定错误类型 (硬冲突 vs 输入校验失败)
    is_conflict = (
        isinstance(err, (UnresolvedConflictError,))
        or getattr(err, "category", "") in (CATEGORY_UNRESOLVED_HARD_CONFLICT, CATEGORY_EXTERNAL_OVERRIDE_CONFLICT)
        or (
            getattr(err, "category", "") not in (CATEGORY_SLOT_ALIAS_COLLISION, CATEGORY_INVALID_INPUT_TYPE, CATEGORY_EXTERNAL_BUDGET_EXCEEDED)
            and ("external override conflict" in str(err).lower() or "unresolved conflict" in str(err).lower())
        )
    )
    failed_status = "failed_conflict" if is_conflict else "failed_validation"

    # 场景 A: 存在下游组装器执行后的已有覆盖记录 (existing_overrides)
    if existing_overrides:
        records: List[Dict[str, Any]] = []
        for o in existing_overrides:
            slot = o.canonical_slot if hasattr(o, "canonical_slot") else o.get("canonical_slot")
            raw_input_val = o.raw_input if hasattr(o, "raw_input") else o.get("raw_input")
            source_port = o.source_port if hasattr(o, "source_port") else o.get("source_port")
            status = o.status if hasattr(o, "status") else o.get("status")
            blank_reason = o.blank_fallback_reason if hasattr(o, "blank_fallback_reason") else o.get("blank_fallback_reason")
            replaced_targets = list(o.replaced_target_ids if hasattr(o, "replaced_target_ids") else o.get("replaced_target_ids", []))
            replaced_txts = list(o.replaced_texts if hasattr(o, "replaced_texts") else o.get("replaced_texts", []))
            injected_atoms = list(o.injected_atom_ids if hasattr(o, "injected_atom_ids") else o.get("injected_atom_ids", []))

            # 仅将真正参与冲突/校验失败的槽位标记为失败状态
            if slot in err_slots:
                records.append({
                    "blank_fallback_reason": None,
                    "canonical_slot": slot,
                    "injected_atom_ids": [],
                    "raw_input": serialize_raw_input(raw_input_val),
                    "replaced_target_ids": [],
                    "replaced_texts": [],
                    "source_port": source_port,
                    "status": failed_status,
                })
            else:
                records.append({
                    "blank_fallback_reason": blank_reason,
                    "canonical_slot": slot,
                    "injected_atom_ids": injected_atoms,
                    "raw_input": serialize_raw_input(raw_input_val),
                    "replaced_target_ids": replaced_targets,
                    "replaced_texts": replaced_txts,
                    "source_port": source_port,
                    "status": status,
                })
        return records

    # 场景 B: 组装前校验即失败 (如 NoneType、未知槽位或重复别名冲突)
    # 安全从原始 inputs 提取各槽位输入候选，绝不调用会抛错的 _extract_external_overrides
    slot_input_map = _safe_extract_slot_inputs(inputs)
    canonical_20 = (*SLOT_ORDER, *AUXILIARY_SLOT_ORDER)
    records = []

    for slot in canonical_20:
        entries = slot_input_map.get(slot, [])
        if not entries:
            records.append({
                "blank_fallback_reason": "omitted",
                "canonical_slot": slot,
                "injected_atom_ids": [],
                "raw_input": None,
                "replaced_target_ids": [],
                "replaced_texts": [],
                "source_port": None,
                "status": "not_provided",
            })
            continue

        raw_key, raw_val = entries[0]
        source_port = raw_key
        has_multiple_entries = len(entries) > 1

        is_slot_failed = (
            slot in err_slots
            or any(v is None for _, v in entries)
            or has_multiple_entries
        )

        if is_slot_failed:
            if has_multiple_entries:
                raw_input_repr = [serialize_raw_input(v) for _, v in entries]
                source_port = ", ".join(k for k, _ in entries)
            elif raw_val is None:
                raw_input_repr = None
            else:
                raw_input_repr = serialize_raw_input(raw_val)

            records.append({
                "blank_fallback_reason": None,
                "canonical_slot": slot,
                "injected_atom_ids": [],
                "raw_input": raw_input_repr,
                "replaced_target_ids": [],
                "replaced_texts": [],
                "source_port": source_port,
                "status": failed_status,
            })
        else:
            is_blank, blank_reason = _inspect_blank_input(raw_val)
            if is_blank:
                records.append({
                    "blank_fallback_reason": blank_reason,
                    "canonical_slot": slot,
                    "injected_atom_ids": [],
                    "raw_input": serialize_raw_input(raw_val),
                    "replaced_target_ids": [],
                    "replaced_texts": [],
                    "source_port": source_port,
                    "status": "fallback_blank",
                })
            else:
                # 合法输入但因全局其它槽位校验失败未被组装
                records.append({
                    "blank_fallback_reason": None,
                    "canonical_slot": slot,
                    "injected_atom_ids": [],
                    "raw_input": serialize_raw_input(raw_val),
                    "replaced_target_ids": [],
                    "replaced_texts": [],
                    "source_port": source_port,
                    "status": "failed_validation",
                })

    return records


def format_diagnostics_dict(res: GenerationResult) -> Dict[str, Any]:
    """构建符合 schemas/diagnostics.schema.json 契约的结构化字典 (8 大顶层字段)。"""
    # 1. context_profile (空配置输出 {})
    if res.context_profile is not None:
        cp_dict: Dict[str, Any] = {
            "schema_version": res.context_profile.schema_version,
            "scene_context_ids": list(res.context_profile.scene_context_ids),
            "scene_item_id": res.context_profile.scene_item_id,
            "theme_context_ids": list(res.context_profile.theme_context_ids),
            "theme_id": res.context_profile.theme_id,
            "weights": {
                c: _quantize_weight(w)
                for c, w in res.context_profile.weights
            },
        }
    else:
        cp_dict = {}

    # 2. selections (携带 Atom/source/produced/accepted 身份与 provenance 闭环)
    accepted_atom_ids = {a.atom_id for a in res.atoms}
    rep = res.resolution_report
    all_source_atoms = list(res.source_atoms)
    all_produced_atoms = list(rep.produced_atoms if rep and hasattr(rep, "produced_atoms") else ())

    # 建立 Atom ID 到 SelectionOrigin 的映射，供去重与预算记录归并
    atom_origin_map: Dict[str, SelectionOrigin] = {}
    for a in all_source_atoms:
        if a.origin is not None:
            atom_origin_map[a.atom_id] = a.origin
    for a in all_produced_atoms:
        if a.origin is not None:
            atom_origin_map[a.atom_id] = a.origin

    seen_origins: set[SelectionOrigin] = set()
    ordered_origins: List[SelectionOrigin] = []

    for a in all_source_atoms:
        if a.origin is not None and a.origin not in seen_origins:
            seen_origins.add(a.origin)
            ordered_origins.append(a.origin)

    for a in all_produced_atoms:
        if a.origin is not None and a.origin not in seen_origins:
            seen_origins.add(a.origin)
            ordered_origins.append(a.origin)

    for a in res.atoms:
        if a.origin is not None and a.origin not in seen_origins:
            seen_origins.add(a.origin)
            ordered_origins.append(a.origin)

    selections_list = []
    for s in ordered_origins:
        source_atoms_for_s = [a for a in all_source_atoms if a.origin == s]
        produced_atoms_for_s = [a for a in all_produced_atoms if a.origin == s]
        # Retained source spans remain owned by their original selection even
        # when a sibling in the same protected tag was replaced.
        accepted_atoms_for_s = [
            a for a in res.atoms
            if atom_origin_map.get(a.atom_id, a.origin) == s
        ]
        dedup_records_for_s = [r for r in res.deduplication_records if atom_origin_map.get(r.atom_id) == s]
        budget_records_for_s = [r for r in res.budget_filter_records if atom_origin_map.get(r.atom_id) == s]

        selections_list.append({
            "accepted_atoms": [
                _serialize_atom_item(a, True)
                for a in accepted_atoms_for_s
            ],
            "budget_filtered_records": [
                {
                    "atom_id": r.atom_id,
                    "candidate_words": r.candidate_words,
                    "reason": r.reason,
                    "used_words": r.used_words,
                    "word_budget": r.word_budget,
                }
                for r in budget_records_for_s
            ],
            "deduplicated_records": [
                {
                    "atom_id": r.atom_id,
                    "basis": r.basis,
                    "retained_atom_id": r.retained_atom_id,
                    "retained_tag_text": r.retained_tag_text,
                }
                for r in dedup_records_for_s
            ],
            "entry_point": s.entry_point,
            "mode": s.mode,
            "parent_ids": list(s.parent_ids),
            "produced_atoms": [
                _serialize_atom_item(a, a.atom_id in accepted_atom_ids)
                for a in produced_atoms_for_s
            ],
            "raw_value": s.raw_value,
            "sampling_rationale": s.sampling_rationale,
            "selected_id": s.selected_id,
            "selector": s.selector,
            "source_atoms": [
                _serialize_atom_item(a, a.atom_id in accepted_atom_ids)
                for a in source_atoms_for_s
            ],
        })

    # 3. decisions (完整输出冻结模型的 13 个字段，包含 fact_trace)
    decisions_list = []
    if rep and rep.decisions:
        for d in rep.decisions:
            decisions_list.append({
                "action": d.action,
                "after_text": d.after_text,
                "before_text": d.before_text,
                "decision_id": d.decision_id,
                "fact_trace": d.fact_trace,
                "parent_source_ids": list(d.parent_source_ids),
                "phase": d.phase,
                "produced_atom_ids": list(d.produced_atom_ids),
                "reason_code": d.reason_code,
                "rule_id": d.rule_id,
                "sequence": d.sequence,
                "target_atom_id": d.target_atom_id,
                "winner_atom_ids": list(d.winner_atom_ids),
            })

    # 4. rules_applied
    rules_applied_list = list(res.rules_applied)

    # 5. unresolved_conflicts
    unresolved_list = [
        list(c) if isinstance(c, (list, tuple)) else c
        for c in (rep.unresolved_conflicts if rep else ())
    ]

    # 6. counts (精确由集合分区推导)
    counts_dict = {
        "accepted_atoms": len(res.atoms),
        "budget_filtered": len(res.budget_filter_records),
        "deduplicated": len(res.deduplication_records),
        "dropped": rep.dropped_count if rep else 0,
        "injected": rep.injected_count if rep else 0,
        "produced": len(all_produced_atoms),
        "replaced": rep.replaced_count if rep else 0,
        "source_atoms": len(all_source_atoms),
    }

    # 7. overrides (20 规范槽位覆盖溯源)
    overrides_list = []
    if res.overrides:
        for o in res.overrides:
            overrides_list.append({
                "blank_fallback_reason": o.blank_fallback_reason,
                "canonical_slot": o.canonical_slot,
                "injected_atom_ids": list(o.injected_atom_ids),
                "raw_input": serialize_raw_input(o.raw_input),
                "replaced_target_ids": list(o.replaced_target_ids),
                "replaced_texts": list(o.replaced_texts),
                "source_port": o.source_port,
                "status": o.status,
            })
    else:
        for slot in (*SLOT_ORDER, *AUXILIARY_SLOT_ORDER):
            overrides_list.append({
                "blank_fallback_reason": "omitted",
                "canonical_slot": slot,
                "injected_atom_ids": [],
                "raw_input": None,
                "replaced_target_ids": [],
                "replaced_texts": [],
                "source_port": None,
                "status": "not_provided",
            })

    audit_dict = {
        "context_profile": cp_dict,
        "counts": counts_dict,
        "decisions": decisions_list,
        "effective_seed": int(res.effective_seed if res.effective_seed is not None else 0),
        "error_info": None,
        "overrides": overrides_list,
        "rules_applied": rules_applied_list,
        "schema_version": "2.0",
        "selections": selections_list,
        "status": "success",
        "unresolved_conflicts": unresolved_list,
    }
    return audit_dict


def format_diagnostics_error_dict(
    err: Exception,
    effective_seed: int = 0,
    overrides: Optional[Sequence[Any]] = None,
    raw_inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建符合 schemas/diagnostics.schema.json 契约的错误状态审计报告字典 (2.0 契约)。"""
    existing_ov = overrides if overrides is not None else getattr(err, "overrides", None)
    overrides_list = build_error_override_records(raw_inputs, err, existing_overrides=existing_ov)

    raw_atoms = getattr(err, "source_atoms", None) or getattr(err, "raw_atoms", None)
    selections_list: List[Dict[str, Any]] = []
    if raw_atoms:
        seen_origins: set[SelectionOrigin] = set()
        ordered_origins: List[SelectionOrigin] = []
        for a in raw_atoms:
            if a.origin is not None and a.origin not in seen_origins:
                seen_origins.add(a.origin)
                ordered_origins.append(a.origin)
        for s in ordered_origins:
            source_atoms_for_s = [a for a in raw_atoms if a.origin == s]
            selections_list.append({
                "accepted_atoms": [],
                "budget_filtered_records": [],
                "deduplicated_records": [],
                "entry_point": s.entry_point,
                "mode": s.mode,
                "parent_ids": list(s.parent_ids),
                "produced_atoms": [],
                "raw_value": s.raw_value,
                "sampling_rationale": s.sampling_rationale,
                "selected_id": s.selected_id,
                "selector": s.selector,
                "source_atoms": [
                    _serialize_atom_item(a, False)
                    for a in source_atoms_for_s
                ],
            })

    cp = getattr(err, "context_profile", None)
    if cp is not None:
        cp_dict: Dict[str, Any] = {
            "schema_version": cp.schema_version,
            "scene_context_ids": list(cp.scene_context_ids),
            "scene_item_id": cp.scene_item_id,
            "theme_context_ids": list(cp.theme_context_ids),
            "theme_id": cp.theme_id,
            "weights": {
                c: _quantize_weight(w)
                for c, w in cp.weights
            },
        }
    else:
        cp_dict = {}

    injected_count = sum(1 for a in raw_atoms if is_external_atom(a)) if raw_atoms else 0
    source_atoms_count = len(raw_atoms) if raw_atoms else 0
    counts_dict = {
        "accepted_atoms": 0,
        "budget_filtered": 0,
        "deduplicated": 0,
        "dropped": 0,
        "injected": injected_count,
        "produced": 0,
        "replaced": 0,
        "source_atoms": source_atoms_count,
    }

    unres_raw = getattr(err, "unresolved_conflicts", ())
    unresolved_list = [
        list(c) if isinstance(c, (list, tuple)) else c
        for c in unres_raw
    ]

    error_info_dict = {
        "category": getattr(err, "category", "generic_error"),
        "details": getattr(err, "details", {}),
        "error_code": getattr(err, "error_code", type(err).__name__),
        "message": str(err),
        "rule_id": getattr(err, "rule_id", None),
        "slots": list(getattr(err, "slots", ())),
        "texts": list(getattr(err, "texts", ())),
    }

    return {
        "context_profile": cp_dict,
        "counts": counts_dict,
        "decisions": [],
        "effective_seed": int(effective_seed),
        "error_info": error_info_dict,
        "overrides": overrides_list,
        "rules_applied": [],
        "schema_version": "2.0",
        "selections": selections_list,
        "status": "error",
        "unresolved_conflicts": unresolved_list,
    }


def format_diagnostics_error_json(
    err: Exception,
    effective_seed: int = 0,
    overrides: Optional[Sequence[Any]] = None,
    raw_inputs: Optional[Dict[str, Any]] = None,
) -> str:
    """确定性序列化为紧凑 UTF-8 错误审计报告 JSON 字符串。"""
    d = format_diagnostics_error_dict(err, effective_seed, overrides=overrides, raw_inputs=raw_inputs)
    return json.dumps(
        d,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def format_diagnostics_json(res: GenerationResult) -> str:
    """确定性序列化为紧凑 UTF-8 审计报告 JSON 字符串 (键字典序、紧凑无空、无结尾换行)。"""
    d = format_diagnostics_dict(res)
    return json.dumps(
        d,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


# ═══════════════════════════════════════════════════════════════════════════
# 节点 4: 🔎 IYKYK 提示词诊断
# ═══════════════════════════════════════════════════════════════════════════

class IYKYKPromptDiagnostics:
    """提示词诊断节点 (ComfyUI-IYKYK 审计与可解释性节点)"""

    @classmethod
    def INPUT_TYPES(cls):
        return IYKYKPromptGenerator.INPUT_TYPES()

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "正面提示词 (STRING)",
        "负面提示词 (STRING)",
        "中文场景描述 (STRING)",
        "审计报告 JSON (STRING)",
    )
    FUNCTION = "diagnose"
    CATEGORY = "IYKYK / 提示词生成"

    @classmethod
    def IS_CHANGED(cls, prompt_seed: int = -1, **kwargs) -> Any:
        return _compute_is_changed(prompt_seed, kwargs)

    def diagnose_structured(
        self,
        预设模板: str = "无 (None)",
        风格配方: str = "无 (None)",
        场景大类: str = "随机 (Random)",
        剧情主题: str = "随机 (Random)",
        景别构图: str = "自动 (Auto)",
        拍摄视角: str = "自动 (Auto)",
        裸露等级: str = "随机 (Random)",
        服装款式: str = "随机 (Random)",
        服装状态: str = "自动联动裸露等级 (Auto Link Nudity)",
        发型发色: str = "随机 (Random)",
        饰品头饰: str = "无 (None)",
        妆容细节: str = "无 (None)",
        姿势动作: str = "随机 (Random)",
        情绪表情: str = "随机 (Random)",
        光影预设: str = "自动 (Auto)",
        胶片风格: str = "无 (None)",
        液体效果: str = "无 (None)",
        纹身标记: str = "无 (None)",
        道具物件: str = "无 (None)",
        角色设定: str = "无 (None)",
        真实微瑕: str = "无 (None)",
        画质等级: str = "高清写真 (High)",
        prompt_seed: int = -1,
        raise_on_error: bool = True,
        **kwargs: Any,
    ) -> Tuple[Optional[GenerationResult], str]:
        rng, effective_seed = _get_rng(prompt_seed)
        inputs = {
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
        }
        inputs.update(kwargs)
        if not raise_on_error:
            try:
                res = _generate_structured(
                    sampler=_sampler,
                    assembler=_assembler,
                    inputs=inputs,
                    rng=rng,
                    entry_point="diagnostics",
                    effective_seed=effective_seed,
                )
                audit_json = format_diagnostics_json(res)
                return res, audit_json
            except Exception as err:
                audit_json = format_diagnostics_error_json(err, effective_seed, overrides=getattr(err, "overrides", None), raw_inputs=inputs)
                return None, audit_json

        res = _generate_structured(
            sampler=_sampler,
            assembler=_assembler,
            inputs=inputs,
            rng=rng,
            entry_point="diagnostics",
            effective_seed=effective_seed,
        )
        audit_json = format_diagnostics_json(res)
        return res, audit_json

    def diagnose(
        self,
        预设模板: str = "无 (None)",
        风格配方: str = "无 (None)",
        场景大类: str = "随机 (Random)",
        剧情主题: str = "随机 (Random)",
        景别构图: str = "自动 (Auto)",
        拍摄视角: str = "自动 (Auto)",
        裸露等级: str = "随机 (Random)",
        服装款式: str = "随机 (Random)",
        服装状态: str = "自动联动裸露等级 (Auto Link Nudity)",
        发型发色: str = "随机 (Random)",
        饰品头饰: str = "无 (None)",
        妆容细节: str = "无 (None)",
        姿势动作: str = "随机 (Random)",
        情绪表情: str = "随机 (Random)",
        光影预设: str = "自动 (Auto)",
        胶片风格: str = "无 (None)",
        液体效果: str = "无 (None)",
        纹身标记: str = "无 (None)",
        道具物件: str = "无 (None)",
        角色设定: str = "无 (None)",
        真实微瑕: str = "无 (None)",
        画质等级: str = "高清写真 (High)",
        prompt_seed: int = -1,
        **kwargs: Any,
    ) -> Tuple[str, str, str, str]:
        res, audit_json = self.diagnose_structured(
            预设模板=预设模板,
            风格配方=风格配方,
            场景大类=场景大类,
            剧情主题=剧情主题,
            景别构图=景别构图,
            拍摄视角=拍摄视角,
            裸露等级=裸露等级,
            服装款式=服装款式,
            服装状态=服装状态,
            发型发色=发型发色,
            饰品头饰=饰品头饰,
            妆容细节=妆容细节,
            姿势动作=姿势动作,
            情绪表情=情绪表情,
            光影预设=光影预设,
            胶片风格=胶片风格,
            液体效果=液体效果,
            纹身标记=纹身标记,
            道具物件=道具物件,
            角色设定=角色设定,
            真实微瑕=真实微瑕,
            画质等级=画质等级,
            prompt_seed=prompt_seed,
            **kwargs,
        )
        if res is None:
            return ("", "", "Error: generation failed (see audit_json)", audit_json)
        return (res.positive, res.negative, res.description, audit_json)


NODE_CLASS_MAPPINGS = {
    "IYKYKPromptGenerator": IYKYKPromptGenerator,
    "IYKYKPresetBrowser": IYKYKPresetBrowser,
    "IYKYKCustomSlotCombiner": IYKYKCustomSlotCombiner,
    "IYKYKPromptDiagnostics": IYKYKPromptDiagnostics,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "IYKYKPromptGenerator": "🎴 IYKYK 提示词生成器",
    "IYKYKPresetBrowser": "📋 IYKYK 模板浏览器",
    "IYKYKCustomSlotCombiner": "🧩 IYKYK 自定义槽位拼装器",
    "IYKYKPromptDiagnostics": "🔎 IYKYK 提示词诊断",
}
