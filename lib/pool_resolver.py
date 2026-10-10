"""
lib/pool_resolver.py — 运行时词池解析、动态槽位展开与组合容器契约引擎

严格落实 M4 Batch 1 动态组合执行契约：
1. 词池引用展开 (resolve_pool_reference)：
   - 建立已审核可追溯词池 (BATCH1_POOL_REGISTRY)，完整保留 key/lock/magatama/ring/star/tooth necklace 等候选；
   - 颈圈词池严格保留来源动态颜色语法 ({__by_source/...|4::black})，递归展开嵌套属性；
   - 缺失引用、空池、循环依赖严格 Fail-Closed 抛出 PoolResolutionError，杜绝静默降为通用词；
   - 杜绝在最终提示词中残留 '__by_source/...__' 或 '{...}' 裸语法标记。
2. 动态槽位加权判定 (resolve_dynamic_slots)：
   - 必选/可选槽位识别；
   - 加权分支裁决 (例如 2::| 空分支 66.7% 概率)；
   - 属性前缀修饰模式 (prefix_replace_attribute，例如 'black o-ring choker') 与构件附加模式。
3. 组合容器语义隔离 (is_container_description)：
   - 严禁组合描述文本 (例如 'legwear with optional footwear', 'necklace and choker combo') 进入最终提示词。
4. 双构件组合入口与展开 (resolve_ensemble_combo)：
   - 联合展开 mandatory_components，确保项链与项圈同时共存，单选独立品类时不交叉触发。
"""
from __future__ import annotations

import logging
from pathlib import Path
from random import Random
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


class PoolResolutionError(RuntimeError):
    """词池解析异常：缺失引用、空池、超出最大递归深度或检测到循环依赖时显式抛出。"""
    pass


def _load_canonical_colors() -> List[str]:
    """从冻结来源 ai-image-wildcards/wildcards/09_style/colors.txt 加载完整颜色词池（统一小写归一化去重，共 312 项）。
    若外部路径不可达，则保底加载与该文件 100% 对齐的 312 项冻结候选列表。
    """
    colors_file = Path(__file__).resolve().parent.parent.parent / "ai-image-wildcards" / "wildcards" / "09_style" / "colors.txt"
    if colors_file.is_file():
        lines = [
            line.strip().lower()
            for line in colors_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        seen = set()
        unique_colors: List[str] = []
        for c in lines:
            if c not in seen:
                seen.add(c)
                unique_colors.append(c)
        if len(unique_colors) == 312:
            return unique_colors

    # 严格对齐 wildcards/09_style/colors.txt 的 312 项冻结候选 SSOT 列表
    return [
        "advent purple", "agreeable gray", "aluminum", "amaranth", "amber", "amber brown", "amethyst",
        "antique gray", "apricot", "aqua blue", "aqua green", "army brown", "army green", "ash gray",
        "asphalt gray", "baby blue", "baby pink", "baby yellow", "banana yellow", "bear brown", "birch",
        "bisque", "blood red", "blue screen of death", "blush pink", "bone", "brass", "brick red",
        "bright blue", "bright green", "bright navy", "bright orange", "bright pink", "bright purple",
        "bright red", "bright yellow", "bubblegum pink", "burlap brown", "burnt orange", "camel",
        "canary yellow", "caramel", "carnation pink", "carolina blue", "celadon", "cement gray",
        "cerise", "cetacean blue", "chambray blue", "champagne pink", "charcoal gray", "chartreuse",
        "cherry blossom pink", "cherry red", "chocolate brown", "christmas green", "christmas red",
        "chrome", "classic purple", "clay", "cloud burst blue", "cobalt blue", "cocoa brown",
        "coffee brown", "columbia green", "comic book red", "comic book yellow", "concrete",
        "construction orange", "cool gray", "copper", "coral pink", "corn green", "cornflower blue",
        "cortana blue", "cotton candy pink", "coyote brown", "cream", "crimson", "cyan", "dark blue",
        "dark brown", "dark chocolate brown", "dark gray", "dark green", "dark orange", "dark pink",
        "dark purple", "dark red", "dark sand brown", "dark steel gray", "dark yellow", "dazzling blue",
        "deep pink", "denim blue", "dirt brown", "dorian gray", "dusty rose pink", "earthy red",
        "electric blue", "emerald green", "espresso brown", "eucalyptus", "eye brown", "fire truck red",
        "flamingo pink", "flower pink", "fluorescent blue", "fluorescent green", "fluorescent pink",
        "fluorescent yellow", "forest green", "french blue", "french gray", "fun yellow", "gainsboro gray",
        "girl scout green", "glowing moon yellow", "gold pink", "golden brown", "golden yellow",
        "goldfish orange", "grapefruit pink", "graphite gray", "grass green", "green screen color",
        "gun smoke gray", "gunmetal gray", "hair brown", "halloween orange", "harvest gold", "heather gray",
        "heliconia", "highlighter pink", "honolulu blue", "hot pink", "hunter blaze orange", "hunter green",
        "ice gray", "indigo blue", "irish green", "ivory", "jade green", "kelly green", "king blue",
        "knockout pink", "lavender", "leaf green", "leather brown", "legal pad yellow", "legendary gray",
        "lemon yellow", "light blue", "light blush", "light brown", "light gray", "light orange",
        "light pink", "light purple", "light red", "light yellow", "light zerg purple", "lightsaber blue",
        "lightsaber green", "lightsaber red", "lilac", "lime green", "linen", "lip pink", "magenta",
        "malibu blue", "marines blue", "matte gray", "mauve", "medium brown", "medium dark red", "melon",
        "metallic gray", "midnight blue", "midnight green", "midnight purple", "milano", "military green",
        "millennial pink", "mint blue", "mint green", "mocha", "monarch orange", "moss", "mulberry",
        "musket brown", "mustard yellow", "navy blue", "neon blue", "neon green", "neon orange",
        "neon pink", "neon purple", "neon red", "neon yellow", "neutral gray", "nickel gray", "nude pink",
        "oatmeal", "ocean blue", "olive brown", "olive green", "oxblood red", "oxford brown", "oxford gray",
        "pacific blue", "pale blue", "pale gold", "pale pink", "pale yellow", "paper bag brown",
        "passion pink", "pastel blue", "pastel gray", "pastel green", "pastel orange", "pastel pink",
        "pastel purple", "pastel red", "pastel yellow", "peacock blue", "pearl pink", "periwinkle",
        "petrol", "pewter", "pine green", "platinum", "plum purple", "police blue", "powder blue",
        "primary blue", "pumpkin spice orange", "racing red", "red orange", "reflex blue", "rhodamine red",
        "rich blue", "rich brown", "rich gray", "rich red", "road sign blue", "road sign brown",
        "road sign green", "road sign orange", "road sign yellow", "robin egg blue", "rose red",
        "rosy brown", "rosy pink", "rotary blue", "royal blue", "ruby red", "rustic red", "safety yellow",
        "saffron", "sage gray", "sage green", "salmon pink", "sandstone", "sapphire blue", "satin sheets gold",
        "sawgrass brown", "scarlet red", "school bus yellow", "sea blue", "seafoam green", "sepia brown",
        "sheffield gray", "shrek green", "siena brown", "silvery pink", "sky blue", "slate gray", "soft pink",
        "spearmint", "spring green", "steel gray", "strawberry", "summer yellow", "sweet pink", "taupe brown",
        "taxi cab yellow", "teal blue", "templeton gray", "tennis court blue", "tennis court green",
        "terracotta", "tiger orange", "timberwolf gray", "tortoise shell brown", "true silver", "us navy blue",
        "valentine pink", "varsity red", "vermillion", "very gray", "vibrant pink", "violet", "viridian",
        "walnut brown", "warm brown", "warm gray", "watermelon", "whiskey brown", "wine red", "wood brown",
        "yellow green"
    ]


def _load_hair_lengths() -> List[str]:
    """从冻结来源 ai-image-wildcards/wildcards/by_source/skyyysi/02_hair/hair_lengths.txt 加载完整发长词池 (5 项)。"""
    lengths_file = Path(__file__).resolve().parent.parent.parent / "ai-image-wildcards" / "wildcards" / "by_source" / "skyyysi" / "02_hair" / "hair_lengths.txt"
    if lengths_file.is_file():
        lines = [
            line.strip().lower()
            for line in lengths_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if lines:
            return lines
    return ["long hair", "medium hair", "short hair", "very long hair", "very short hair"]


def _load_hair_styles() -> List[str]:
    """从冻结来源 ai-image-wildcards/wildcards/by_source/skyyysi/02_hair/hair_styles.txt 加载发型款式词池 (9 项)。"""
    styles_file = Path(__file__).resolve().parent.parent.parent / "ai-image-wildcards" / "wildcards" / "by_source" / "skyyysi" / "02_hair" / "hair_styles.txt"
    if styles_file.is_file():
        lines = [
            line.strip().lower()
            for line in styles_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if lines:
            return lines
    return [
        "ahoge",
        "bangs",
        "blunt bangs",
        "curly hair",
        "high ponytail",
        "ponytail",
        "straight hair",
        "twintails",
        "wavy hair",
    ]


def _load_hair_ornaments() -> List[str]:
    """从冻结来源 ai-image-wildcards/wildcards/by_source/skyyysi/02_hair/hair_ornaments.txt 加载发饰词池 (15 项)。"""
    orn_file = Path(__file__).resolve().parent.parent.parent / "ai-image-wildcards" / "wildcards" / "by_source" / "skyyysi" / "02_hair" / "hair_ornaments.txt"
    if orn_file.is_file():
        lines = [
            line.strip().lower()
            for line in orn_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if lines:
            return lines
    return [
        "hair ornament",
        "hair ornament, butterfly hair ornament",
        "hair ornament, cat hair ornament",
        "hair ornament, crescent hair ornament",
        "hair ornament, feather hair ornament",
        "hair ornament, frog hair ornament",
        "hair ornament, hair bell",
        "hair ornament, hair flower",
        "hair ornament, heart hair ornament",
        "hair ornament, leaf hair ornament",
        "hair ornament, skull hair ornament",
        "hair ornament, star hair ornament",
        "hair ornament, x hair ornament",
        "hairband",
        "hairclip",
    ]


# Batch 1 & Batch 2 运行时词池注册表 (SSOT，完全基于已审核候选与来源规范建立)
BATCH1_POOL_REGISTRY: Dict[str, List[str]] = {
    # 严格对齐 wildcards/10_accessories/necklaces.txt 及审核映射 MAP_00105 ~ MAP_00116
    "10_accessories/necklaces": [
        "necklace",
        "bead necklace",
        "chain necklace",
        "cross necklace",
        "heart necklace",
        "key necklace",
        "lock necklace",
        "magatama necklace",
        "pearl necklace",
        "ring necklace",
        "star necklace",
        "tooth necklace",
    ],
    # 严格对齐 wildcards/10_accessories/chokers.txt 及审核映射 MAP_00089 ~ MAP_00104，保留动态颜色规则
    "10_accessories/chokers": [
        "{__by_source/skyyysi/09_style/colors__|4::black} choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} bow choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} cross choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} frilled choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} heart choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} lace ribbon choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} o-ring choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} pendant choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} neck ribbon",
        "{__by_source/skyyysi/09_style/colors__|4::black} spiked choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} star choker",
        "{__by_source/skyyysi/09_style/colors__|4::black} neck bell choker",
    ],
    # 严格对齐审核映射 MAP_00100 ~ MAP_00123
    "10_accessories/secondary_legwear": [
        "fishnet pantyhose",
        "single fishnet thighhigh",
        "fishnet thighhighs",
        "pantyhose",
        "single thighhigh",
        "thighhighs",
    ],
    # 严格对齐审核映射 MAP_00124 ~ MAP_00127
    "10_accessories/footwear": [
        "boots",
        "ankle boots",
        "thigh boots",
        "sneakers",
    ],
    # 严格对齐 wildcards/09_style/colors.txt 来源属性词池（全部 312 项小写归一化去重）
    "09_style/colors": _load_canonical_colors(),
    # Batch 2: 严格对齐 wildcards/by_source/skyyysi/02_hair/hair_lengths.txt (5项)
    "02_hair/hair_lengths": _load_hair_lengths(),
    # Batch 2: 严格对齐 wildcards/by_source/skyyysi/02_hair/hair_styles.txt (9项)
    "02_hair/hair_styles": _load_hair_styles(),
    # Batch 2: 严格对齐 wildcards/by_source/skyyysi/02_hair/hair_ornaments.txt (15项)
    "02_hair/hair_ornaments": _load_hair_ornaments(),
}



def normalize_pool_key(ref: str) -> str:
    """归一化词池引用键，剥离 __by_source/ 前缀及两端下划线。"""
    c = ref.strip()
    if c.startswith("__by_source/skyyysi/"):
        c = c[len("__by_source/skyyysi/"):]
    elif c.startswith("__by_source/"):
        c = c[len("__by_source/"):]
    if c.endswith("__"):
        c = c[:-2]
    return c.strip()


def is_container_description(text: str, facts: Optional[Dict[str, Any]] = None, role: Optional[str] = None) -> bool:
    """判定是否为纯组合描述容器文本，严禁进入最终提示词。"""
    normalized = text.strip().lower()
    if normalized in (
        "legwear with optional footwear",
        "necklace and choker combo",
        "choker and necklace combo",
        "complete composite hairstyle",
    ):
        return True
    if role == "ensemble_combo":
        return True
    if facts:
        gov = facts.get("governance_metadata", {})
        if gov.get("is_container_description") or gov.get("is_ensemble_combo"):
            return True
    return False


def expand_pattern_template(
    text: str,
    rng: Random,
    visited: Set[str],
    depth: int = 0,
) -> str:
    """递归展开动态模式模板（包含加权分支与嵌套词池引用）。

    例如: '{__by_source/skyyysi/09_style/colors__|4::black} bow choker'
    -> 依据权重裁决后递归解析颜色词池，最终输出 'black bow choker' 或 'red bow choker' 等具体自然文本。
    """
    if depth > 5:
        raise PoolResolutionError(f"Maximum recursion depth exceeded while expanding template: {text!r}")

    def replace_bracket(match: re.Match) -> str:
        content = match.group(1)
        branches = content.split("|")
        parsed: List[Tuple[int, str]] = []
        for b in branches:
            if "::" in b:
                w_str, val = b.split("::", 1)
                try:
                    w = int(w_str.strip())
                except ValueError:
                    w = 1
                parsed.append((w, val.strip()))
            else:
                parsed.append((1, b.strip()))

        weights = [p[0] for p in parsed]
        values = [p[1] for p in parsed]
        if sum(weights) <= 0:
            weights = [1] * len(values)

        chosen = rng.choices(values, weights=weights, k=1)[0]
        if "__by_source" in chosen:
            chosen = resolve_pool_reference(chosen, rng, visited.copy(), depth + 1)
        return chosen

    current = text
    loop_guard = 0
    while "{" in current and loop_guard < 5:
        prev = current
        current = re.sub(r"\{([^{}]+)\}", replace_bracket, current)
        loop_guard += 1
        if current == prev:
            break

    # 解析可能独立残留在括号外部的裸词池引用
    def replace_by_source(m: re.Match) -> str:
        return resolve_pool_reference(m.group(0), rng, visited.copy(), depth + 1)

    current = re.sub(r"__by_source/[a-zA-Z0-9_\/]+__", replace_by_source, current)
    return re.sub(r"\s+", " ", current).strip()


def resolve_pool_reference(
    pool_ref: str,
    rng: Random,
    visited: Optional[Set[str]] = None,
    depth: int = 0,
) -> str:
    """展开词池引用，具备严格深度限制、缺失引用校验、空池校验与循环依赖防护。

    异常契约：
    - 缺失池或未知引用：抛出 PoolResolutionError；
    - 空词池：抛出 PoolResolutionError；
    - 检测到循环依赖或递归超限：抛出 PoolResolutionError；
    - 严禁静默回退或吞噬语义。
    """
    if visited is None:
        visited = set()

    if depth > 5:
        raise PoolResolutionError(f"Maximum recursion depth exceeded for pool reference: {pool_ref!r}")

    clean_key = normalize_pool_key(pool_ref)

    # 1. 循环依赖防护
    if clean_key in visited:
        raise PoolResolutionError(
            f"Circular dependency detected in pool reference: {pool_ref!r} (visited: {visited})"
        )

    # 2. 候选池匹配校验
    if clean_key not in BATCH1_POOL_REGISTRY:
        raise PoolResolutionError(
            f"Missing required pool reference in registry: {pool_ref!r} (normalized key: {clean_key!r})"
        )

    candidates = BATCH1_POOL_REGISTRY[clean_key]
    if not candidates:
        raise PoolResolutionError(f"Pool reference is empty: {pool_ref!r} (normalized key: {clean_key!r})")

    # 3. 递归安全标记与随机抽取候选
    new_visited = visited.union({clean_key})
    chosen = rng.choice(candidates)

    # 4. 动态模板与嵌套引用递归展开
    if "{" in chosen or "__by_source" in chosen:
        chosen = expand_pattern_template(chosen, rng, new_visited, depth + 1)

    return chosen


# 发型组合互斥组与级联约束常量 (SSOT)
TIED_HAIR_STYLES: Set[str] = {"high ponytail", "ponytail", "twintails"}
SILHOUETTE_STYLES: Set[str] = {"straight hair", "curly hair", "wavy hair"}
BANGS_STYLES: Set[str] = {"bangs", "blunt bangs"}


def resolve_dynamic_slots(
    dynamic_slots: Sequence[Dict[str, Any]],
    rng: Random,
    base_text: str = "",
) -> Tuple[str, List[str]]:
    """展开动态槽位。

    返回:
        (modified_base_text, list_of_additional_component_tags)
    """
    current_base = base_text
    additional_tags: List[str] = []
    resolved_slot_values: Dict[str, List[str]] = {}

    for slot in dynamic_slots:
        branches = slot.get("branches", [])
        if not branches:
            continue

        weights = [b.get("weight", 1) for b in branches]
        total_w = sum(weights)
        if total_w <= 0:
            continue

        chosen_branch = rng.choices(branches, weights=weights, k=1)[0]
        b_type = chosen_branch.get("type", "literal")
        b_val = chosen_branch.get("value")

        # 空分支直接跳过
        if b_type == "empty" or (b_type == "literal" and not b_val):
            continue

        slot_name = slot.get("slot_name", "")
        min_c = slot.get("min_count", 1)
        max_c = slot.get("max_count", 1)
        k_count = rng.randint(min_c, max_c) if (max_c > 1 or min_c > 1) else 1

        resolved_vals: List[str] = []
        if b_type == "wildcard_reference":
            target = chosen_branch.get("target", "")
            clean_tgt = normalize_pool_key(target)
            if clean_tgt == "02_hair/hair_styles":
                # 针对发型组合：严格遵守 styles 互斥规则与发长级联约束
                candidates = list(BATCH1_POOL_REGISTRY.get("02_hair/hair_styles", []))
                # 1. 发长级联约束：短发/极短发严禁扎发（高马尾、单马尾、双马尾）
                chosen_length = " ".join(resolved_slot_values.get("length", [])) + " " + base_text
                if any(sk in chosen_length for sk in ("short hair", "very short")):
                    candidates = [c for c in candidates if c not in TIED_HAIR_STYLES]

                rng.shuffle(candidates)
                chosen_styles: List[str] = []
                has_tied = False
                has_sil = False
                has_bangs = False
                for s in candidates:
                    # 扎发结构互斥：high ponytail, ponytail, twintails 至多选 1 个
                    if s in TIED_HAIR_STYLES:
                        if has_tied:
                            continue
                        has_tied = True
                    # 基础廓形互斥：straight, curly, wavy 间至多选 1 个
                    if s in SILHOUETTE_STYLES:
                        if has_sil:
                            continue
                        has_sil = True
                    # 刘海互斥：bangs, blunt bangs 间至多选 1 个
                    if s in BANGS_STYLES:
                        if has_bangs:
                            continue
                        has_bangs = True
                    chosen_styles.append(s)
                    if len(chosen_styles) >= k_count:
                        break
                resolved_vals.extend(chosen_styles)
            elif target:
                val = resolve_pool_reference(target, rng)
                if val:
                    if slot_name == "color" or slot.get("color_emphasis_wrapper"):
                        val = f"({val} hair)"
                    resolved_vals.append(val)
        elif b_type == "literal":
            if b_val is not None:
                val = str(b_val)
                if val:
                    resolved_vals.append(val)

        if slot_name:
            resolved_slot_values[slot_name] = resolved_vals

        for resolved_val in resolved_vals:
            if not resolved_val or is_container_description(resolved_val):
                continue

            mode = slot.get("slot_injection_mode", "append_tag")
            if mode == "prefix_replace_attribute":
                # 属性修饰模式：拼接为前缀修饰
                current_base = f"{resolved_val} {current_base}".strip()
            elif mode == "append_tag":
                additional_tags.append(resolved_val)
            else:
                additional_tags.append(resolved_val)

    return current_base, additional_tags
