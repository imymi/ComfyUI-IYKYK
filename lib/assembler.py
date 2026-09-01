"""
assembler.py — 结构化流水线装配与统一 Finalize 引擎

1. 严格按 16 步骤流水线顺序装配 PromptFragment 结构体列表
2. 提供强化的顶层逗号解析器 split_top_level_tags：
   - 保护权重括号 (), [], <>, ""
   - 支持反斜杠转义逗号 \\, 与转义双引号 \\"
3. 提供三入口统一公共流水线 finalize_prompt：
   - 空片段规范化
   - 结构化冲突消解 (ConflictResolver)
   - 首见保序去重
   - 严谨的 250 词边界管理（结构化片段原子保护、普通片段单词边界截断、单结构超长校验 PromptValidationError）
   - 最终格式清洗与非空安全保障
"""
from __future__ import annotations

import re
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence

from .conflict_resolver import ConflictResolver, sanitize_prompt, is_protected_fragment
from .models import PromptFragment

MAX_PROMPT_WORDS = 250


class PromptValidationError(Exception):
    """当单个不可拆分的结构化片段超出词数预算时抛出。"""
    pass


SLOT_PIPELINE_ORDER = [
    ("scene_theme", 1),
    ("shot_type", 2),
    ("camera_angle", 3),
    ("character", 4),
    ("nudity", 5),
    ("clothing", 6),
    ("lighting", 7),
    ("pose", 8),
    ("expression", 9),
    ("makeup", 10),
    ("hairstyle", 11),
    ("jewelry", 12),
    ("imperfections", 13),
    ("tattoo", 14),
    ("props", 15),
    ("liquids", 16),
    ("film", 17),
    ("quality", 18),
]


def split_top_level_tags(text: str) -> List[str]:
    """
    按顶层逗号拆分提示词文本，严格忽略被 (), [], <>, "" 包裹的内部逗号，
    支持反斜杠转义逗号 \\, 与转义引号 \\"，完整保护 ComfyUI 权重与语法。
    """
    if not text or not text.strip():
        return []

    tags: List[str] = []
    current: List[str] = []
    paren_depth = 0
    bracket_depth = 0
    angle_depth = 0
    in_quote = False
    escaped = False

    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == '\\':
            escaped = True
            current.append(char)
            continue

        if char == '"':
            in_quote = not in_quote
            current.append(char)
        elif in_quote:
            current.append(char)
        elif char == '(':
            paren_depth += 1
            current.append(char)
        elif char == ')':
            paren_depth = max(0, paren_depth - 1)
            current.append(char)
        elif char == '[':
            bracket_depth += 1
            current.append(char)
        elif char == ']':
            bracket_depth = max(0, bracket_depth - 1)
            current.append(char)
        elif char == '<':
            angle_depth += 1
            current.append(char)
        elif char == '>':
            angle_depth = max(0, angle_depth - 1)
            current.append(char)
        elif char == ',' and paren_depth == 0 and bracket_depth == 0 and angle_depth == 0:
            tag = "".join(current).strip()
            if tag:
                tags.append(tag)
            current = []
        else:
            current.append(char)

    if current:
        tag = "".join(current).strip()
        if tag:
            tags.append(tag)

    return tags


def finalize_prompt(
    fragments: Sequence[PromptFragment],
    *,
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS
) -> str:
    """
    三入口统一公共流水线：
    1. 标准化空白与空片段
    2. 基于 PromptFragment 进行冲突消解
    3. 保留首次出现顺序的去重
    4. 严谨的 250 词边界管理（结构化片段原子保护、普通片段单词边界截断）
    5. 格式清洗与标点规范化
    """
    if rng is None:
        rng = Random(42)

    # 1. 过滤空片段
    valid_frags: List[PromptFragment] = []
    for f in fragments:
        t = f.text if is_protected_fragment(f.text) else f.text.strip().strip(",")
        if t:
            valid_frags.append(
                PromptFragment(
                    text=t,
                    source_slot=f.source_slot,
                    source_item_id=f.source_item_id,
                    context_ids=f.context_ids,
                    exclusive_group=f.exclusive_group,
                    order=f.order,
                )
            )

    if not valid_frags:
        return "best quality, masterpiece"

    # 2. 结构化冲突消解
    resolver = ConflictResolver(data_dir)
    resolved_frags = resolver.resolve_fragments(valid_frags, rng)

    # 3. 按稳定 key 去重（保留首次出现顺序）
    seen_keys = set()
    deduped_frags: List[PromptFragment] = []
    for f in resolved_frags:
        key = f.text.lower().strip()
        if key not in seen_keys:
            seen_keys.add(key)
            deduped_frags.append(f)

    # 4. 严谨的 250 词边界管理（以 PromptFragment 序列为单位计算预算）
    accepted_texts: List[str] = []
    current_word_count = 0

    for f in deduped_frags:
        is_structural = is_protected_fragment(f.text)
        frag_text = f.text if is_structural else f.text.strip().strip(",")
        if not frag_text:
            continue
        frag_words = len(frag_text.split())

        # 单个结构化片段超长检查
        if is_structural and frag_words > max_words:
            raise PromptValidationError(
                f"Single structural fragment exceeds {max_words} words limit: '{frag_text[:50]}...'"
            )

        if current_word_count + frag_words <= max_words:
            accepted_texts.append(frag_text)
            current_word_count += frag_words
        else:
            # 超出当前预算
            if not is_structural:
                remaining = max_words - current_word_count
                if remaining > 0:
                    words = frag_text.split()[:remaining]
                    accepted_texts.append(" ".join(words))
                    current_word_count += len(words)
            else:
                # 结构化片段必须保持原子性，绝不中途切断，跳过当前片段
                continue

    # 5. 由渲染器按 ", " 规范化拼接
    raw_prompt = ", ".join(accepted_texts)
    sanitized = sanitize_prompt(raw_prompt)

    # 最终完整性与词数断言（绝不二次盲目截断字符串破坏语法）
    final_words = len(sanitized.split())
    if final_words > max_words:
        raise PromptValidationError(
            f"Rendered prompt word count {final_words} exceeds maximum allowed budget {max_words}"
        )

    return sanitized


class PromptAssembler:
    """15 槽位流水线组装器。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.resolver = ConflictResolver(data_dir)

    def assemble_to_fragments(self, slots: Dict[str, List[Any]]) -> List[PromptFragment]:
        """按流水线顺序将槽位数据转换为 PromptFragment 列表，完整透传已有 PromptFragment 的结构化元数据。"""
        fragments: List[PromptFragment] = []
        order = 0

        for slot_name, _ in SLOT_PIPELINE_ORDER:
            items = slots.get(slot_name, [])
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, PromptFragment):
                        fragments.append(
                            PromptFragment(
                                text=item.text,
                                source_slot=item.source_slot or slot_name,
                                source_item_id=item.source_item_id,
                                context_ids=item.context_ids,
                                exclusive_group=item.exclusive_group,
                                order=order,
                            )
                        )
                        order += 1
                    elif str(item).strip():
                        sub_tags = split_top_level_tags(str(item))
                        for st in sub_tags:
                            fragments.append(
                                PromptFragment(
                                    text=st,
                                    source_slot=slot_name,
                                    order=order,
                                )
                            )
                            order += 1

        # 处理可能在 SLOT_PIPELINE_ORDER 外的其他槽位
        for slot_name, items in slots.items():
            if slot_name not in [s[0] for s in SLOT_PIPELINE_ORDER]:
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, PromptFragment):
                            fragments.append(
                                PromptFragment(
                                    text=item.text,
                                    source_slot=item.source_slot or slot_name,
                                    source_item_id=item.source_item_id,
                                    context_ids=item.context_ids,
                                    exclusive_group=item.exclusive_group,
                                    order=order,
                                )
                            )
                            order += 1
                        elif str(item).strip():
                            sub_tags = split_top_level_tags(str(item))
                            for st in sub_tags:
                                fragments.append(
                                    PromptFragment(
                                        text=st,
                                        source_slot=slot_name,
                                        order=order,
                                    )
                                )
                                order += 1

        return fragments

    def assemble(self, slots: Dict[str, List[str]], rng: Optional[Random] = None) -> str:
        """主组装接口：组装各槽位并执行统一 finalize。"""
        if rng is None:
            rng = Random(42)

        fragments = self.assemble_to_fragments(slots)
        return finalize_prompt(fragments, data_dir=self.data_dir, rng=rng, max_words=MAX_PROMPT_WORDS)

    def assemble_preset(
        self,
        preset: Dict[str, Any],
        style_recipe: Optional[Dict[str, Any]],
        quality_tier: str,
        rng: Optional[Random] = None
    ) -> str:
        """预设模板与风格配方组装接口，统一接入 finalize_prompt。"""
        if rng is None:
            rng = Random(42)

        fragments: List[PromptFragment] = []
        order = 0

        # 1. 预设核心 Prompt
        raw_preset_prompt = preset.get("positive", preset.get("prompt", ""))
        for t in split_top_level_tags(raw_preset_prompt):
            fragments.append(
                PromptFragment(
                    text=t,
                    source_slot="preset_core",
                    order=order,
                )
            )
            order += 1

        # 2. 风格配方叠加
        if style_recipe:
            for k in ["lighting_palette", "style_recipe", "focus_detail"]:
                val = style_recipe.get(k, "")
                if val:
                    for t in split_top_level_tags(str(val)):
                        fragments.append(
                            PromptFragment(
                                text=t,
                                source_slot=f"recipe_{k}",
                                order=order,
                            )
                        )
                        order += 1

        # 3. 画质等级锚点
        q_str = str(quality_tier or "").lower()
        if "cctv" in q_str or "监控" in q_str:
            q_tags = ["CCTV footage", "security camera", "low resolution", "grainy"]
        elif "phone" in q_str or "手机" in q_str:
            q_tags = ["phone camera", "selfie", "amateur photo", "slightly blurry"]
        elif "masterpiece" in q_str or "顶尖" in q_str:
            q_tags = ["masterpiece", "best quality", "ultra detailed", "8k", "photorealistic"]
        else:
            q_tags = ["best quality", "detailed", "photorealistic"]

        for qt in q_tags:
            fragments.append(
                PromptFragment(
                    text=qt,
                    source_slot="quality",
                    order=order,
                )
            )
            order += 1

        return finalize_prompt(fragments, data_dir=self.data_dir, rng=rng, max_words=MAX_PROMPT_WORDS)
