"""
models.py — 提示词结构化数据模型 (PromptFragment & SampleResult)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


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

    def clean_text(self) -> str:
        return self.text.strip().strip(",")
