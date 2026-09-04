"""
rng.py — 确定性 SHA-256 独立 RNG 子流派生规范实现 (rc8 核心契约)
"""
from __future__ import annotations

import hashlib
import random
from typing import Optional


def derive_substream_seed(effective_seed: int, namespace: str) -> int:
    """基于 SHA-256 派生子流种子，保证不同槽位与规则的随机数序列互不干扰。

    契约公式：
    material = "iykyk-rc8\0" + decimal(effective_seed) + "\0" + namespace
    derived_seed = int.from_bytes(SHA256(material).digest()[0:16], "big")
    """
    if not isinstance(effective_seed, int) or isinstance(effective_seed, bool):
        raise TypeError(f"effective_seed must be an int, got {type(effective_seed).__name__}")
    if not isinstance(namespace, str):
        raise TypeError(f"namespace must be a str, got {type(namespace).__name__}")

    material = f"iykyk-rc8\0{effective_seed}\0{namespace}"
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return int.from_bytes(digest[:16], "big")


def derive_substream_rng(effective_seed: int, namespace: str) -> random.Random:
    """根据 effective_seed 和命名空间派生独立的 random.Random 实例。"""
    return random.Random(derive_substream_seed(effective_seed, namespace))


_SEED_CACHE: dict[int, int] = {}


def recover_effective_seed(rng: random.Random) -> Optional[int]:
    """尝试从 Random 实例中恢复原始非负整型种子 (保持纯函数兼容性)。"""
    if hasattr(rng, "_effective_seed"):
        return getattr(rng, "_effective_seed")
    state = rng.getstate()
    if isinstance(state, tuple) and len(state) >= 2 and isinstance(state[1], tuple) and len(state[1]) >= 2:
        val = state[1][1]
        if not _SEED_CACHE:
            for i in range(10001):
                _SEED_CACHE[random.Random(i).getstate()[1][1]] = i
        return _SEED_CACHE.get(val)
    return None
