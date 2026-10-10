from __future__ import annotations

import random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# SSOT Pool Registry for Batch 1 runtime dynamic resolution
BATCH1_POOL_REGISTRY: Dict[str, List[str]] = {
    "10_accessories/necklaces": [
        "necklace",
        "bead necklace",
        "chain necklace",
        "cross necklace",
        "heart necklace",
        "pendant necklace",
        "pearl necklace",
        "silver necklace",
    ],
    "10_accessories/chokers": [
        "choker",
        "bow choker",
        "cross choker",
        "frilled choker",
        "heart choker",
        "lace ribbon choker",
        "o-ring choker",
        "pendant choker",
        "spiked choker",
        "star choker",
        "neck bell choker",
    ],
    "10_accessories/secondary_legwear": [
        "fishnet pantyhose",
        "single fishnet thighhigh",
        "fishnet thighhighs",
        "pantyhose",
        "single thighhigh",
        "thighhighs",
    ],
    "10_accessories/footwear": [
        "boots",
        "ankle boots",
        "thigh boots",
        "sneakers",
    ],
    "09_style/colors": [
        "black",
        "white",
        "red",
        "blue",
        "pink",
        "purple",
        "silver",
        "gold",
        "brown",
        "dark red",
        "navy blue",
    ],
}


def resolve_pool_reference(
    pool_ref: str,
    rng: random.Random,
    visited: Optional[Set[str]] = None,
    depth: int = 0,
) -> str:
    """展开词池引用，具备缺失引用兜底、空池兜底与循环依赖防护。"""
    if visited is None:
        visited = set()

    # 1. 深度与循环引用防护
    if depth > 3 or pool_ref in visited:
        # 截断递归，返回中性回退词
        return pool_ref.split("/")[-1].rstrip("s")

    visited.add(pool_ref)

    # 2. 缺失引用兜底
    candidates = BATCH1_POOL_REGISTRY.get(pool_ref)
    if not candidates:
        return pool_ref.split("/")[-1].rstrip("s")

    # 3. 随机采样候选
    chosen = rng.choice(candidates)
    return chosen


def resolve_dynamic_slots(
    dynamic_slots: Sequence[Dict[str, Any]],
    rng: random.Random,
    base_text: str = "",
) -> Tuple[str, List[str]]:
    """展开动态槽位。返回 (modified_base_text, list_of_additional_tags)。"""
    current_base = base_text
    additional_tags: List[str] = []

    for slot in dynamic_slots:
        branches = slot.get("branches", [])
        if not branches:
            continue

        weights = [b.get("weight", 1) for b in branches]
        total_w = sum(weights)
        if total_w <= 0:
            continue

        # 加权采样分支
        r = rng.randint(1, total_w)
        cum = 0
        chosen_branch = branches[0]
        for b, w in zip(branches, weights):
            cum += w
            if r <= cum:
                chosen_branch = b
                break

        b_type = chosen_branch.get("type", "literal")
        if b_type == "empty":
            continue

        resolved_val = ""
        if b_type == "literal":
            resolved_val = chosen_branch.get("value", "")
        elif b_type == "wildcard_reference":
            target = chosen_branch.get("target", "")
            resolved_val = resolve_pool_reference(target, rng)

        if not resolved_val:
            continue

        mode = slot.get("slot_injection_mode")
        if mode == "prefix_replace_attribute":
            # 前缀修饰基底文本 (例如 'black' + 'o-ring choker')
            if current_base:
                # 若已存在该修饰词则不重复添加
                if not current_base.startswith(resolved_val):
                    current_base = f"{resolved_val} {current_base}"
            else:
                current_base = resolved_val
        else:
            # 独立构件槽位 (例如 legwear, footwear)
            additional_tags.append(resolved_val)

    return current_base, additional_tags
