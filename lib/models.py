"""
models.py — 提示词结构化数据模型 (PromptFragment, PromptAtom, SampleResult & TagProvenance)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


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


@dataclass(frozen=True)
class SampledTag:
    """采样层单标签输出对象，携带真实语义来源。"""
    text: str
    provenance: TagProvenance = field(default_factory=TagProvenance)


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


@dataclass(frozen=True)
class ThemeSampleResult:
    """
    剧情主题采样层结构化结果，携带真实 TagProvenance。
    """
    tags: Tuple[SampledTag, ...]
    theme_id: str
    provenance: TagProvenance = field(default_factory=TagProvenance)

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
    def all_text_tags(self) -> List[str]:
        return [t.text for t in self.all_tags]


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

    @property
    def can_detect(self) -> bool:
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


@dataclass(frozen=True)
class AssemblyResult:
    """流水线统一组装结果 (不可变数据容器)。

    不变量：
    - 完全不可变对象 (frozen=True)；
    - prompt：最终装配完成的提示词文本；
    - accepted_atoms：最终被采纳的 PromptAtom 序列；
    - source_atoms：流水线初始全量原始 PromptAtom 权威源注册表；
    - rules_applied：本次消解触发的应用规则清单。
    """
    prompt: str
    accepted_atoms: Tuple[PromptAtom, ...] = ()
    source_atoms: Tuple[PromptAtom, ...] = ()
    rules_applied: Tuple[str, ...] = ()
