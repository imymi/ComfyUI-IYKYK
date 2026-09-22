"""
atomizer.py — 提示词原子化与标签容器转换模块 (零循环依赖)

负责 PromptFragment / str 与 PromptTag / PromptAtom 的双向无损转换，
实现完整 Tag 级保序去重，绝不暴露半截 Span 给兼容接口调用方。
本模块仅依赖 models.py、lexer.py 与 errors.py，禁止依赖 assembler 与 conflict_resolver。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

if __package__:
    from .errors import PromptValidationError
    from .lexer import parse_prompt
    from .models import (
        DeduplicationRecord,
        PromptAtom,
        PromptFragment,
        SelectionOrigin,
        SemanticFacts,
        TagProvenance,
        VALID_SOURCE_MODES,
    )
else:
    from lib.errors import PromptValidationError
    from lib.lexer import parse_prompt
    from lib.models import (
        DeduplicationRecord,
        PromptAtom,
        PromptFragment,
        SelectionOrigin,
        SemanticFacts,
        TagProvenance,
        VALID_SOURCE_MODES,
    )


@dataclass(frozen=True)
class PromptTag:
    """完整 Top-Level 提示词标签容器，聚合其包含的原子 Span (不可变对象)。"""
    tag_order: int
    source_slot: str = "unknown"
    source_item_id: Optional[str] = None
    context_ids: Tuple[str, ...] = ()
    exclusive_group: Optional[str] = None
    provenance: TagProvenance = field(default_factory=TagProvenance)
    atoms: Tuple[PromptAtom, ...] = ()

    @property
    def text(self) -> str:
        """还原完整标签文本 (严禁字符集合式 strip 破坏转义逗号与转义空白)。"""
        return "".join(a.text for a in self.atoms)

    @property
    def can_delete_tag(self) -> bool:
        return all(a.can_delete_atom for a in self.atoms)

    @property
    def contains_blackbox(self) -> bool:
        return any(a.contains_blackbox for a in self.atoms)

    @property
    def has_protected_spans(self) -> bool:
        """若包含任何不可删除原子（如 ANGLE/QUOTED 黑盒或内嵌黑盒的括号），该 Tag 视为受保护。"""
        return any(not a.can_delete_atom for a in self.atoms)

    @property
    def is_protected(self) -> bool:
        """若包含任何不可删除原子（如 ANGLE/QUOTED 黑盒或内嵌黑盒的括号），该 Tag 视为受保护。"""
        return self.has_protected_spans


def fragments_to_atoms(
    fragments: Sequence[PromptFragment | str]
) -> Tuple[List[PromptTag], List[PromptAtom]]:
    """
    将 PromptFragment 或纯字符串序列原子化为 (PromptTag 列表, PromptAtom 列表)。
    执行严格语法预校验与分词切分，正确赋予各 Span 权限标记。

    顺序契约 (P1-4):
    - PromptFragment.order 为最高权威顺序，输入列表索引仅作为相同 order 下的平局稳定决胜；
    - order 必须是非布尔且 >= 0 的整数，非法值立即抛出 PromptValidationError (Fail-Closed)；
    - 纯字符串兼容输入没有显式 order 时，使用其传入位置索引作为 order；
    - 原子化后的 tag_order 全局严格单调递增，单个片段内的多个顶层 tag 相对顺序连续递增。
    """
    if not isinstance(fragments, (list, tuple)):
        raise PromptValidationError(
            f"Expected list or tuple of PromptFragment or str, got {type(fragments).__name__}"
        )

    indexed_items: List[Tuple[int, int, PromptFragment | str]] = []
    for idx, f in enumerate(fragments):
        if isinstance(f, PromptFragment):
            if isinstance(f.order, bool) or not isinstance(f.order, int) or f.order < 0:
                raise PromptValidationError(
                    f"PromptFragment.order must be a non-negative integer, got {f.order!r} ({type(f.order).__name__})"
                )
            indexed_items.append((f.order, idx, f))
        elif isinstance(f, str):
            indexed_items.append((idx, idx, f))
        else:
            raise PromptValidationError(
                f"Invalid fragment item at index {idx}: expected PromptFragment or str, got {type(f).__name__}"
            )

    # 按照 (order, idx) 进行稳定排序 (P1-4)
    indexed_items.sort(key=lambda x: (x[0], x[1]))

    tags: List[PromptTag] = []
    atoms: List[PromptAtom] = []
    tag_order = 0

    for _, _, f in indexed_items:
        if isinstance(f, str):
            f_text = f
            f_slot = "custom"
            f_item_id = None
            f_ctx = ()
            f_ex = None
            f_prov = TagProvenance(kind="user_input")
            f_facts = SemanticFacts()
            f_origin = SelectionOrigin(entry_point="custom_combiner", mode="custom", selector="custom", raw_value=f)
            f_id = ""
            f_target_id = None
        else:
            f_text = f.text
            f_slot = f.source_slot
            f_item_id = f.source_item_id
            f_ctx = f.context_ids
            f_ex = f.exclusive_group
            f_prov = f.provenance
            f_facts = getattr(f, "facts", SemanticFacts())
            f_origin = getattr(f, "origin", None)
            f_id = getattr(f, "id", "")
            f_target_id = getattr(f, "target_id", None)

        f_text = f.text if isinstance(f, PromptFragment) else (f.text if hasattr(f, "text") else str(f))
        if isinstance(f_text, dict):
            f_text = f_text.get("text", "")
        if not f_text or not f_text.strip():
            continue

        # source_mode records the immutable original selection mode. Resolver
        # is an action origin and must never be promoted to an original source.
        if not f_prov.source_mode and f_origin and f_origin.mode in VALID_SOURCE_MODES:
            f_prov = replace(f_prov, source_mode=f_origin.mode)

        # 单次解析：一次扫描完成语法校验、span 生成与精确 tag 原文切片
        parsed = parse_prompt(f_text)

        for tag in parsed.tags:
            tag_atoms: List[PromptAtom] = []
            entry_str = str(f_origin.entry_point) if (f_origin and f_origin.entry_point) else "unknown"
            item_str = str(f_item_id or (f_prov.item_id if (f_prov and f_prov.item_id) else "item"))
            for span_order, sp in enumerate(tag.spans):
                if sp.text:
                    if f_id:
                        leaf_str = str(f_id)
                    else:
                        leaf_str = hashlib.sha256(sp.text.encode("utf-8")).hexdigest()[:12]
                    seed_str = f"{entry_str}\0{item_str}\0{leaf_str}\0{tag_order}\0{span_order}"
                    atom_id = f"atom_{hashlib.sha256(seed_str.encode('utf-8')).hexdigest()[:24]}"
                    atom = PromptAtom(
                        text=sp.text,
                        span_type=sp.span_type,
                        source_slot=f_slot,
                        source_item_id=f_item_id,
                        context_ids=f_ctx,
                        exclusive_group=f_ex,
                        tag_order=tag_order,
                        span_order=span_order,
                        provenance=f_prov,
                        contains_blackbox=sp.contains_blackbox,
                        atom_id=atom_id,
                        facts=f_facts,
                        origin=f_origin,
                        id=f_id,
                        target_id=f_target_id,
                    )
                    tag_atoms.append(atom)
                    atoms.append(atom)

            if tag_atoms:
                ptag = PromptTag(
                    tag_order=tag_order,
                    source_slot=f_slot,
                    source_item_id=f_item_id,
                    context_ids=f_ctx,
                    exclusive_group=f_ex,
                    provenance=f_prov,
                    atoms=tuple(tag_atoms),
                )
                tags.append(ptag)
                tag_order += 1

    return tags, atoms


def atoms_to_tags(atoms: Sequence[PromptAtom]) -> List[PromptTag]:
    """将存活的 PromptAtom 重新汇聚为 PromptTag 列表（保序并剔除空 Tag）。"""
    tag_groups: dict[int, List[PromptAtom]] = {}
    for a in atoms:
        tag_groups.setdefault(a.tag_order, []).append(a)

    reconstructed_tags: List[PromptTag] = []
    for t_order in sorted(tag_groups.keys()):
        group = sorted(tag_groups[t_order], key=lambda x: x.span_order)
        if not group:
            continue
        first = group[0]
        for other in group[1:]:
            if other.id != first.id or other.facts != first.facts or other.origin != first.origin:
                raise ValueError(
                    f"Metadata mismatch across spans in tag_order {t_order}: "
                    f"span {first.span_order} (id={first.id}, facts={first.facts}, origin={first.origin}) vs "
                    f"span {other.span_order} (id={other.id}, facts={other.facts}, origin={other.origin})"
                )
        ptag = PromptTag(
            tag_order=t_order,
            source_slot=first.source_slot,
            source_item_id=first.source_item_id,
            context_ids=first.context_ids,
            exclusive_group=first.exclusive_group,
            provenance=first.provenance,
            atoms=tuple(group),
        )
        if ptag.text:
            reconstructed_tags.append(ptag)

    return reconstructed_tags


def atoms_to_fragments(atoms: Sequence[PromptAtom]) -> List[PromptFragment]:
    """
    兼容接口：将消解后的 Atom 聚合成完整的 Top-Level PromptFragment 列表。
    绝不将半截 plain 或 LoRA span 暴露为独立 Fragment。
    100% 保持 id, facts, origin 元数据回转。
    """
    tags = atoms_to_tags(atoms)
    frags: List[PromptFragment] = []
    for t in tags:
        if not t.text:
            continue
        first_atom = t.atoms[0] if t.atoms else None
        f_id = (first_atom.id if (first_atom and first_atom.id) else (getattr(first_atom, "atom_id", "") if first_atom else ""))
        f_facts = first_atom.facts if first_atom else SemanticFacts()
        f_origin = first_atom.origin if first_atom else None
        frags.append(
            PromptFragment(
                text=t.text,
                source_slot=t.source_slot,
                source_item_id=t.source_item_id,
                context_ids=t.context_ids,
                exclusive_group=t.exclusive_group,
                order=t.tag_order,
                provenance=t.provenance,
                id=f_id,
                facts=f_facts,
                origin=f_origin,
            )
        )
    return frags


def deduplicate_tags_with_records(
    tags: Sequence[PromptTag],
) -> Tuple[List[PromptTag], List[PromptTag], List[DeduplicationRecord]]:
    """
    执行完整 Tag 级保序去重并返回 (保留Tags, 被去重剔除Tags, 去重记录清单)：
    - 若 Tag 为受保护（包含 ANGLE 或 QUOTED），绝对免疫去重，原样保留；
    - 若 Tag 为全 Plain，按规范化完整文本全局首见去重（跨槽位普通 Tag 首次保留、后续剔除）；
    - 针对剔除 Tag 的每个 Atom，生成精确绑定保留目标与原因依据的 DeduplicationRecord (R3-P1-002)。
    """
    seen_plain_texts: Dict[str, PromptTag] = {}
    result: List[PromptTag] = []
    dropped: List[PromptTag] = []
    records: List[DeduplicationRecord] = []

    for t in tags:
        if t.is_protected:
            # 黑盒 Tag（如 LoRA、双引号短语）绝不参与删除式去重
            result.append(t)
            continue

        norm_text = t.text.strip().casefold()
        if not norm_text:
            continue

        if norm_text not in seen_plain_texts:
            seen_plain_texts[norm_text] = t
            result.append(t)
        else:
            dropped.append(t)
            retained_tag = seen_plain_texts[norm_text]
            retained_atom_id = retained_tag.atoms[0].atom_id if retained_tag.atoms else ""
            retained_text = retained_tag.text
            for a in t.atoms:
                records.append(
                    DeduplicationRecord(
                        atom_id=a.atom_id,
                        retained_atom_id=retained_atom_id,
                        retained_tag_text=retained_text,
                        basis="normalized_exact_tag_duplicate",
                    )
                )

    return result, dropped, records


def deduplicate_tags_with_dropped(tags: Sequence[PromptTag]) -> Tuple[List[PromptTag], List[PromptTag]]:
    """
    执行完整 Tag 级保序去重并返回 (保留Tags, 被去重剔除Tags)：
    - 若 Tag 为受保护（包含 ANGLE 或 QUOTED），绝对免疫去重，原样保留；
    - 若 Tag 为全 Plain，按规范化完整文本全局首见去重（跨槽位普通 Tag 首次保留、后续剔除）。
    """
    result, dropped, _ = deduplicate_tags_with_records(tags)
    return result, dropped


def deduplicate_tags(tags: Sequence[PromptTag]) -> List[PromptTag]:
    """
    执行完整 Tag 级保序去重：
    - 若 Tag 为受保护（包含 ANGLE 或 QUOTED），绝对免疫去重，原样保留；
    - 若 Tag 为全 Plain，按规范化完整文本全局首见去重（跨槽位普通 Tag 首次保留、后续剔除）。
    """
    res, _ = deduplicate_tags_with_dropped(tags)
    return res
