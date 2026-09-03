"""
assembler.py — 结构化流水线装配与统一 Finalize 引擎 (基于 PromptTag / PromptAtom 契约)

1. 严格按 18 步骤流水线顺序 (SLOT_ORDER) 装配 PromptFragment 结构体列表
2. 消费 lib/atomizer.py 进行无损双向转换，执行完整 Tag 级保序去重
3. 严谨的 250 词边界管理（原子保护、超上限校验 PromptValidationError）
4. 遇到未知非空槽位 Fail-Closed 抛出 PromptValidationError
"""
from __future__ import annotations

from pathlib import Path
from random import Random
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

if __package__:
    from .atomizer import PromptTag, atoms_to_tags, deduplicate_tags, fragments_to_atoms
    from .conflict_resolver import ConflictResolver
    from .errors import PromptValidationError
    from .lexer import split_top_level_tags, validate_prompt_syntax
    from .models import AssemblyResult, PromptAtom, PromptFragment, TagProvenance
    from .slot_contract import AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_slot_mapping
else:
    from lib.atomizer import PromptTag, atoms_to_tags, deduplicate_tags, fragments_to_atoms
    from lib.conflict_resolver import ConflictResolver
    from lib.errors import PromptValidationError
    from lib.lexer import split_top_level_tags, validate_prompt_syntax
    from lib.models import AssemblyResult, PromptAtom, PromptFragment, TagProvenance
    from lib.slot_contract import AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_slot_mapping

MAX_PROMPT_WORDS = 250


def sanitize_prompt(prompt: str) -> str:
    """兼容接口：严格恒等透传，100% 字节不变。"""
    return prompt


def render_tags(tags: Sequence[PromptTag]) -> str:
    """按 tag 分组使用 ', ' 拼接完整 prompt。"""
    return ", ".join(t.text for t in tags if t.text)


def render_atoms(atoms: Sequence[PromptAtom]) -> str:
    """按 tag 分组使用 ', ' 渲染 atom 序列重建 prompt。"""
    tags = atoms_to_tags(atoms)
    return render_tags(tags)


def assemble_result(
    fragments: Sequence[PromptFragment | str] | PromptFragment | str,
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS,
    resolver: Optional[ConflictResolver] = None
) -> AssemblyResult:
    """核心统一入口：执行原子化、冲突消解、去重与截断，返回强类型不可变 AssemblyResult。"""
    if isinstance(max_words, bool) or not isinstance(max_words, int) or max_words < 0:
        raise PromptValidationError(
            f"max_words must be a non-negative integer, got {max_words!r} ({type(max_words).__name__})"
        )

    if rng is None:
        rng = Random(42)

    if isinstance(fragments, (str, PromptFragment)):
        fragments = [fragments]

    # 1. 拆解为原子 Span 序列，记录初始全量 source_atoms
    is_fallback = False
    tags, raw_atoms = fragments_to_atoms(fragments)
    if not raw_atoms:
        is_fallback = True
        default_quality_fragments = [
            PromptFragment(
                text="best quality",
                source_slot="quality",
                source_item_id="quality_default",
                order=0,
                provenance=TagProvenance(
                    item_id="quality_default",
                    kind="quality",
                    semantic_ids=("quality:quality_default",),
                ),
            ),
            PromptFragment(
                text="masterpiece",
                source_slot="quality",
                source_item_id="quality_default",
                order=1,
                provenance=TagProvenance(
                    item_id="quality_default",
                    kind="quality",
                    semantic_ids=("quality:quality_default",),
                ),
            ),
        ]
        tags, raw_atoms = fragments_to_atoms(default_quality_fragments)

    source_atoms = tuple(raw_atoms)

    if max_words == 0:
        return AssemblyResult(
            prompt="",
            accepted_atoms=(),
            source_atoms=source_atoms,
            rules_applied=(),
        )

    # 2. 结构化冲突消解 (支持外部注入复用 Resolver，避免重复加载大型规则配置)
    if resolver is None:
        resolver = ConflictResolver(data_dir)
    resolved_atoms, rules_applied = resolver.resolve_atoms_with_report(raw_atoms, rng)

    # 3. 按 tag_order 汇聚为 PromptTag，并执行完整 Tag 级保序去重
    resolved_tags = atoms_to_tags(resolved_atoms)
    deduped_tags = deduplicate_tags(resolved_tags)

    # 4. 严谨的词数原子预算管理 (硬约束)
    accepted_atoms: List[PromptAtom] = []
    accepted_tag_texts: List[str] = []
    current_word_count = 0

    for tag in deduped_tags:
        tag_text = tag.text
        if not tag_text:
            continue

        # 若非系统 fallback 自动生成的 tag，逐 atom 检查是否有单个 atom 超过总上限
        if not is_fallback:
            for a in tag.atoms:
                atom_words = len(a.text.split())
                if atom_words > max_words:
                    raise PromptValidationError(
                        f"Single atomic span exceeds {max_words} words limit ({atom_words} words): '{a.text[:50]}...'"
                    )

        tag_words = len(tag_text.split())
        if current_word_count + tag_words <= max_words:
            accepted_atoms.extend(tag.atoms)
            accepted_tag_texts.append(tag_text)
            current_word_count += tag_words
        else:
            # 原子 tag 无法完整装入：整块跳过
            continue

    sanitized = ", ".join(accepted_tag_texts)

    # 5. 最终语法校验与词数断言
    validate_prompt_syntax(sanitized)

    final_words = len(sanitized.split())
    if final_words > max_words:
        raise PromptValidationError(
            f"Rendered prompt word count {final_words} exceeds maximum allowed budget {max_words}"
        )

    return AssemblyResult(
        prompt=sanitized,
        accepted_atoms=tuple(accepted_atoms),
        source_atoms=source_atoms,
        rules_applied=rules_applied,
    )


def assemble_prompt_result(
    fragments: Sequence[PromptFragment | str] | PromptFragment | str,
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS,
    resolver: Optional[ConflictResolver] = None
) -> Tuple[List[PromptAtom], str, Tuple[str, ...]]:
    """兼容入口：执行原子化、冲突消解、去重与截断，返回 (accepted_atoms, prompt_str, rules_applied)。"""
    res = assemble_result(fragments, data_dir, rng, max_words, resolver)
    return list(res.accepted_atoms), res.prompt, res.rules_applied


def assemble_prompt_atoms(
    fragments: Sequence[PromptFragment | str],
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS
) -> Tuple[List[PromptAtom], str]:
    """诊断接口：组装并返回采纳的原子序列与最终 Prompt 字符串。"""
    atoms, prompt_str, _ = assemble_prompt_result(fragments, data_dir, rng, max_words)
    return atoms, prompt_str


def assemble_prompt(
    fragments: Sequence[PromptFragment | str],
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS
) -> str:
    """标准入口：组装并返回最终 Prompt 字符串。"""
    _, prompt_str = assemble_prompt_atoms(fragments, data_dir, rng, max_words)
    return prompt_str


def finalize_prompt_atoms(
    fragments: Sequence[PromptFragment | str],
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS
) -> List[PromptAtom]:
    """诊断接口：获取装配最终采纳的 PromptAtom 序列。"""
    atoms, _ = assemble_prompt_atoms(fragments, data_dir, rng, max_words)
    return atoms


def iter_normalized_slot_fragments(
    slot_fragments: Dict[str, Any]
) -> Iterator[PromptFragment]:
    """统一遍历并归一化槽位片段，按 SLOT_ORDER + AUXILIARY_SLOT_ORDER 顺序输出。

    对缺失 provenance 的项赋予默认 TagProvenance(kind="user_input")，绝不向外暴露 None。
    若存在多个非空原始键归一化到同一规范槽位，抛出 PromptValidationError (Fail-Closed)。
    若已知槽位的输入值形状非法（如 set, dict, int 或包含非法元素的列表），抛出 PromptValidationError (P2-1)。
    """
    norm_slots = normalize_slot_mapping(slot_fragments)

    # 先验校验所有已知槽位值的形状与类型 (P2-1)，避免半消费后失败
    for slot_name, raw_val in norm_slots.items():
        if raw_val is None:
            if slot_name in (SLOT_ORDER + AUXILIARY_SLOT_ORDER):
                raise PromptValidationError(
                    f"Slot {slot_name!r} explicitly provided as None (type NoneType). "
                    "Expected str, PromptFragment, or list/tuple of str/PromptFragment."
                )
            continue
        if isinstance(raw_val, (str, PromptFragment)):
            continue
        if isinstance(raw_val, (list, tuple)):
            for idx, item in enumerate(raw_val):
                if not isinstance(item, (str, PromptFragment)):
                    raise PromptValidationError(
                        f"Slot {slot_name!r} contains invalid element at index {idx}: "
                        f"{item!r} ({type(item).__name__}). Expected str or PromptFragment."
                    )
        else:
            raise PromptValidationError(
                f"Invalid value shape for slot {slot_name!r}: {raw_val!r} ({type(raw_val).__name__}). "
                "Expected str, PromptFragment, or list/tuple of str/PromptFragment."
            )

    for slot_name in (SLOT_ORDER + AUXILIARY_SLOT_ORDER):
        items = norm_slots.get(slot_name)
        if items is None:
            continue

        item_list = [items] if isinstance(items, (str, PromptFragment)) else list(items)
        for item in item_list:
            if isinstance(item, PromptFragment):
                prov = item.provenance if item.provenance is not None else TagProvenance(kind="user_input", semantic_ids=(f"slot:{slot_name}",))
                yield PromptFragment(
                    text=item.text,
                    source_slot=slot_name,
                    source_item_id=item.source_item_id,
                    context_ids=item.context_ids,
                    exclusive_group=item.exclusive_group,
                    order=item.order,
                    provenance=prov,
                )
            elif isinstance(item, str):
                for st in split_top_level_tags(item):
                    if st:
                        yield PromptFragment(
                            text=st,
                            source_slot=slot_name,
                            provenance=TagProvenance(kind="user_input", semantic_ids=(f"slot:{slot_name}",))
                        )


class PromptAssembler:
    """18 槽位流水线组装器。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.resolver = ConflictResolver(data_dir)

    @staticmethod
    def iter_normalized_slot_fragments(slot_inputs: Dict[str, Any]):
        return iter_normalized_slot_fragments(slot_inputs)

    def assemble_slots(
        self,
        slot_fragments: Dict[str, List[Any]],
        rng: Optional[Random] = None,
        max_words: int = MAX_PROMPT_WORDS
    ) -> AssemblyResult:
        """从各槽位片段字典执行装配，返回强类型 AssemblyResult。"""
        fragments = list(iter_normalized_slot_fragments(slot_fragments))
        return assemble_result(fragments, self.data_dir, rng, max_words, resolver=self.resolver)

    def assemble_result_with_sources(
        self,
        slot_fragments: Dict[str, List[Any]],
        rng: Optional[Random] = None,
        max_words: int = MAX_PROMPT_WORDS
    ) -> Tuple[str, Tuple[PromptAtom, ...], Tuple[str, ...], Tuple[PromptAtom, ...]]:
        """兼容接口：从各槽位片段字典执行装配，返回 (prompt_str, atoms, rules_applied, source_atoms)。"""
        res = self.assemble_slots(slot_fragments, rng, max_words)
        return res.prompt, res.accepted_atoms, res.rules_applied, res.source_atoms

    def assemble_result(
        self,
        slot_fragments: Dict[str, List[Any]],
        rng: Optional[Random] = None,
        max_words: int = MAX_PROMPT_WORDS
    ) -> Tuple[str, Tuple[PromptAtom, ...], Tuple[str, ...]]:
        """从各槽位片段字典执行装配，返回 (prompt_str, atoms, rules_applied)。"""
        res = self.assemble_slots(slot_fragments, rng, max_words)
        return res.prompt, res.accepted_atoms, res.rules_applied

    def assemble(
        self,
        slot_fragments: Dict[str, List[Any]] | Sequence[PromptFragment | str],
        rng: Optional[Random] = None,
        max_words: int = MAX_PROMPT_WORDS
    ) -> str:
        """从各槽位片段字典或片段列表执行装配，返回最终 Prompt 字符串。"""
        if isinstance(slot_fragments, dict):
            return self.assemble_slots(slot_fragments, rng, max_words).prompt
        return assemble_result(slot_fragments, self.data_dir, rng, max_words, resolver=self.resolver).prompt

    def assemble_to_fragments(
        self,
        slot_fragments: Dict[str, Any]
    ) -> List[PromptFragment]:
        """将槽位字典拆解为扁平化、原子化的 PromptFragment 列表，并赋予全局单调递增 order。"""
        fragments: List[PromptFragment] = []
        for order, frag in enumerate(iter_normalized_slot_fragments(slot_fragments)):
            fragments.append(
                PromptFragment(
                    text=frag.text,
                    source_slot=frag.source_slot,
                    source_item_id=frag.source_item_id,
                    context_ids=frag.context_ids,
                    exclusive_group=frag.exclusive_group,
                    order=order,
                    provenance=frag.provenance if frag.provenance is not None else TagProvenance(kind="user_input"),
                )
            )
        return fragments

    def assemble_preset(
        self,
        preset: Dict[str, Any],
        style_recipe: Optional[Dict[str, Any]],
        quality_tier: str,
        rng: Optional[Random] = None,
        max_words: int = MAX_PROMPT_WORDS,
    ) -> AssemblyResult:
        """预设模板与风格配方统一装配接口，返回包含 source_atoms 的不可变 AssemblyResult。"""
        if rng is None:
            rng = Random(42)

        fragments: List[PromptFragment] = []
        order = 0

        # 1. 预设核心 Prompt
        preset_id = preset.get("id", "preset_custom")
        raw_preset_prompt = preset.get("positive", preset.get("prompt", ""))
        for t in split_top_level_tags(raw_preset_prompt):
            if t:
                fragments.append(
                    PromptFragment(
                        text=t,
                        source_slot="preset_core",
                        source_item_id=preset_id,
                        order=order,
                        provenance=TagProvenance(
                            item_id=preset_id,
                            kind="preset",
                            semantic_ids=(f"preset:{preset_id}",),
                        ),
                    )
                )
                order += 1

        # 2. 风格配方叠加
        if style_recipe:
            recipe_id = style_recipe.get("id", "recipe_custom")
            for k in ["lighting_palette", "style_recipe", "focus_detail"]:
                val = style_recipe.get(k, "")
                if val:
                    for t in split_top_level_tags(str(val)):
                        if t:
                            fragments.append(
                                PromptFragment(
                                    text=t,
                                    source_slot=f"recipe_{k}",
                                    source_item_id=recipe_id,
                                    order=order,
                                    provenance=TagProvenance(
                                        item_id=recipe_id,
                                        kind="style_recipe",
                                        semantic_ids=(f"recipe:{recipe_id}",),
                                    ),
                                )
                            )
                            order += 1

        # 3. 画质等级锚点 (稳定内部 quality ID)
        q_str = str(quality_tier or "").lower()
        if "cctv" in q_str or "监控" in q_str:
            quality_id = "quality_cctv"
            q_tags = ["CCTV footage", "security camera", "low resolution", "grainy"]
        elif "phone" in q_str or "手机" in q_str:
            quality_id = "quality_phone"
            q_tags = ["phone camera", "selfie", "amateur photo", "slightly blurry"]
        elif "masterpiece" in q_str or "顶尖" in q_str:
            quality_id = "quality_masterpiece"
            q_tags = ["masterpiece", "best quality", "ultra detailed", "8k", "photorealistic"]
        else:
            quality_id = "quality_standard"
            q_tags = ["best quality", "masterpiece", "high resolution", "photorealistic"]

        for q in q_tags:
            fragments.append(
                PromptFragment(
                    text=q,
                    source_slot="quality",
                    source_item_id=quality_id,
                    order=order,
                    provenance=TagProvenance(
                        item_id=quality_id,
                        kind="quality",
                        semantic_ids=(f"quality:{quality_id}",),
                    ),
                )
            )
            order += 1

        return assemble_result(fragments, self.data_dir, rng, max_words, resolver=self.resolver)

    def assemble_preset_result(
        self,
        preset: Dict[str, Any],
        style_recipe: Optional[Dict[str, Any]],
        quality_tier: str,
        rng: Optional[Random] = None
    ) -> Tuple[str, Tuple[PromptAtom, ...], Tuple[str, ...]]:
        """兼容包装器：投影 AssemblyResult 为 (prompt_str, accepted_atoms, rules_applied)。"""
        res = self.assemble_preset(preset, style_recipe, quality_tier, rng)
        return res.prompt, res.accepted_atoms, res.rules_applied


finalize_prompt = assemble_prompt
