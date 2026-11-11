"""
sampler.py — 完整槽位数据采样与情境自洽引擎

严格实现 nsfw-prompt-templates-asian 项目规范：
1. 场景与主题情境识别（校园、职场、居家、温泉、夜店、SM、和风、医疗等 14 大核心情境）
2. 空间与物理环境自洽（单一场景锚点定位，杜绝跨场所/室内外冲突并存）
3. 裸露等级 × 服装状态强力联动（L1 包裹 → L6 特写脱法咬合）
4. 槽位情境亲和度加权采样（自动杜绝场景与服装/道具/角色错位冲突）
5. 保证用户显式选择 100% 优先，严格遵循 None / Random / Auto / Explicit 四态契约
6. 未知显式项 Fail-Fast（抛出 DataSelectionError），杜绝静默随机退化
7. 显式 DataLoadError 错误诊断，杜绝静默失败
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

if __package__:
    from .conflict_resolver import (
        ALLOWED_BUTTON_STYLES,
        NON_SKIRT_ONE_PIECE,
        GarmentCarrierEntity,
        get_required_carrier_kind,
        is_carrier_satisfied_by_garments,
        is_garment_compatible_with_state,
    )
    from .context_affinity import (
        ContextAffinityRegistry,
        compute_context_profile,
        compute_slot_distribution,
        sample_categorical,
    )
    from .errors import CatalogIndexingError, DataLoadError, DataSelectionError
    from .pool_resolver import (
        is_container_description,
        resolve_dynamic_slots,
        resolve_pool_reference,
    )
    from .models import (
        ClothingSampleResult,
        ContextProfile,
        NUDITY_BRANCHES_BY_LEVEL,
        NudityConstraint,
        PromptAtom,
        SampleResult,
        SampledTag,
        SemanticFacts,
        SpanType,
        TagProvenance,
        ThemeSampleResult,
        VALID_NUDITY_LEVELS,
        normalize_nudity_level_code,
    )
    from .outfit_completion import (
        check_completion_exemption,
        complete_outfit_tags,
        derive_deterministic_child_rng,
        filter_extensions_for_garments,
        is_tag_eligible_for_nudity_level,
        repair_ensemble_subsampling,
    )
else:
    from lib.conflict_resolver import (
        ALLOWED_BUTTON_STYLES,
        NON_SKIRT_ONE_PIECE,
        GarmentCarrierEntity,
        get_required_carrier_kind,
        is_carrier_satisfied_by_garments,
        is_garment_compatible_with_state,
    )
    from lib.context_affinity import (
        ContextAffinityRegistry,
        compute_context_profile,
        compute_slot_distribution,
        sample_categorical,
    )
    from lib.errors import CatalogIndexingError, DataLoadError, DataSelectionError
    from lib.pool_resolver import (
        is_container_description,
        resolve_dynamic_slots,
        resolve_pool_reference,
    )
    from lib.models import (
        ClothingSampleResult,
        ContextProfile,
        NUDITY_BRANCHES_BY_LEVEL,
        NudityConstraint,
        PromptAtom,
        SampleResult,
        SampledTag,
        SemanticFacts,
        SpanType,
        TagProvenance,
        ThemeSampleResult,
        VALID_NUDITY_LEVELS,
        normalize_nudity_level_code,
    )
    from lib.outfit_completion import (
        check_completion_exemption,
        complete_outfit_tags,
        derive_deterministic_child_rng,
        filter_extensions_for_garments,
        is_tag_eligible_for_nudity_level,
        repair_ensemble_subsampling,
    )


class SelectionMode:
    NONE = "none"
    RANDOM = "random"
    AUTO = "auto"
    EXPLICIT = "explicit"


def _is_none(val: Any) -> bool:
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in ("none", "无", "无 (none)", "none (无)", "null", "false", "")


def _is_random(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("random", "随机", "随机 (random)", "random (随机)")


def _is_auto(val: Any) -> bool:
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in ("auto", "自动", "自动 (auto)", "auto (自动)", "自动联动裸露等级 (auto link nudity)", "自动联动裸露等级")


def get_selection_mode(val: Any) -> str:
    if _is_none(val):
        return SelectionMode.NONE
    if _is_random(val):
        return SelectionMode.RANDOM
    if _is_auto(val):
        return SelectionMode.AUTO
    return SelectionMode.EXPLICIT


def _load_affinity_matrix(data_dir: Path) -> Dict[str, Dict[str, List[str]]]:
    affinity_file = data_dir / "context_affinity.json"
    if not affinity_file.exists():
        return {}
    try:
        data = json.loads(affinity_file.read_text(encoding="utf-8"))
        matrix = data.get("matrix", {})
        result = {}
        for ctx, slots in matrix.items():
            result[ctx] = {}
            for slot, cell in slots.items():
                if cell.get("mode") == "weighted":
                    result[ctx][slot] = [c["id"] for c in cell.get("candidates", []) if "id" in c]
                else:
                    result[ctx][slot] = []
        return result
    except Exception:
        return {}

DATA_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "data"
CONTEXT_AFFINITY = _load_affinity_matrix(DATA_DIR_DEFAULT)
CONTEXT_PARENT_MAPPING = {k: k for k in CONTEXT_AFFINITY.keys()}

STANDALONE_CLOTHING_CATEGORIES = frozenset({
    "cyberpunk_tactical_bodysuit",
    "tactical_jumpsuit",
    "cyberpunk_plugsuit",
    "hosiery_legwear",
    "trousers_general",
    "bottoms_specialty",
    "lingerie_panties",
    "denim_jeans",
    "ao_dai",
    "uchikake",
    "lehenga",
    "changshan",
    "traditional_eastern_gown",
    "sari",
    "tops_specialty",
    "lingerie_bra",
})

_NON_L1_EXPOSED_REGIONS = frozenset({
    "breasts",
    "genitals",
    "anus",
    "buttocks",
    "full_nude",
    "cleavage",
    "midriff",
    "underboob_sideboob",
    "underwear_panties",
    "underwear_bra",
    "thigh_leg",
    "shoulders",
    "back",
})
_NON_L2_EXPOSED_REGIONS = frozenset({
    "breasts",
    "genitals",
    "anus",
    "buttocks",
    "full_nude",
    "underwear_panties",
})
_NON_L1_VISIBLE_REGIONS = frozenset({
    "intimate_lower_body",
    "crotch",
    "underboob",
    "sideboob",
    "breasts",
    "buttocks",
    "pubic",
})
_L1_BANNED_THEMES = frozenset({
    "theme_chikan_ntr",
    "theme_humiliation_training",
    "theme_special_state",
    "theme_multi_person",
    "theme_cohabitation_trip",
    "theme_av_audition",
    "theme_reverse_ntr",
    "theme_yuri",
    "theme_kinbaku",
    "theme_netorare",
    "theme_aphrodisiac",
    "theme_blackmail",
    "theme_drunken",
    "theme_impregnation",
    "theme_deepthroat",
    "theme_facial",
    "theme_nympho",
    "theme_outdoor_exposure",
    "theme_voyeur",
    "theme_unconscious",
    "theme_foot_fetish",
    "theme_sm_dominance",
    "theme_food_play",
    "theme_remote_imagery",
    "theme_nightlife_services",
})
_L2_BANNED_THEMES = frozenset({
    "theme_av_audition",
    "theme_impregnation",
    "theme_deepthroat",
    "theme_facial",
    "theme_multi_person",
    "theme_reverse_ntr",
    "theme_nympho",
    "theme_unconscious",
    "theme_aphrodisiac",
    "theme_outdoor_exposure",
    "theme_remote_imagery",
})
_SAFE_NON_NUDE_PHRASE_RE = re.compile(
    r"\b(?:sheer\s+nude\s+lip|nude\s+lip(?:\s+gloss)?|nude\s+peach|"
    r"bare\s+(?:bulb|bulbs|tree|trees|wall|walls|face|brick|wood|concrete|floor|branch|branches)|"
    r"ankle\s+bracelet)\b",
    re.I,
)
_L1_NSFW_TEXT_RE = re.compile(
    r"\b(?:raw\s+sex|raw\s+dogging|no\s+condom|unprotected\s+sex|creampie|cum\s+inside|"
    r"cumshot|bukkake|cum\b|semen|ejaculation|blowjob|deepthroat|irrumatio|face\s+fucking|"
    r"throat\s+fucking|handjob|fingering|masturbat\w*|orgasm|ahegao|threesome|foursome|"
    r"gangbang|orgy|sex\s+addict|nympho|sleep\s+sex|video\s+call\s+sex|cam\s+sex|"
    r"video\s+chat\s+sex|park\s+sex|outdoor\s+sex|rooftop\s+sex|beach\s+sex|mountain\s+sex|"
    r"walking\s+after\s+sex|after\s+sex|post-sex|sponge\s+bath|mixed\s+bathing|one\s+night\s+stand|"
    r"business\s+trip\s+affair|cheating\s+wife|netorare|chikan|bondage|shibari|kinbaku|"
    r"vibrator|dildo|lube\s+bottle|mind\s+break|knocked\s+up|lactating|sloppy\s+seconds|"
    r"nipple\w*|areola\w*|pussy|vagina|penis|cock|genitals|crotch|upskirt|downblouse|"
    r"flashing|streaking|mooning|public\s+nudity|outdoor\s+exposure|exhibitionism|"
    r"bare\s+breasts?|exposed\s+breasts?|covering\s+breasts?|below\s+breasts?|"
    r"cupping\s+breasts?|skirt\s+(?:lifted|hiked|pulled|riding)|underboob|sideboob|cleavage|"
    r"no\s+panties|no\s+bra|topless|bottomless|naked|nude|undressed|av\s+actress|gravure\s+model)\b",
    re.I,
)
_L2_NSFW_TEXT_RE = re.compile(
    r"\b(?:raw\s+sex|raw\s+dogging|no\s+condom|unprotected\s+sex|creampie|cum\s+inside|"
    r"cumshot|bukkake|cum\b|semen|ejaculation|blowjob|deepthroat|irrumatio|face\s+fucking|"
    r"throat\s+fucking|handjob|fingering|masturbat\w*|orgasm|ahegao|threesome|foursome|"
    r"gangbang|orgy|sleep\s+sex|video\s+call\s+sex|cam\s+sex|park\s+sex|outdoor\s+sex|"
    r"rooftop\s+sex|beach\s+sex|mountain\s+sex|after\s+sex|post-sex|"
    r"vibrator|dildo|pussy|vagina|penis|cock|genitals|bare\s+breasts?|exposed\s+breasts?|"
    r"nipple\w*|no\s+panties|no\s+bra|topless|bottomless|public\s+nudity)\b",
    re.I,
)
_L6_INCOMPATIBLE_SHOT_IDS = frozenset({"close_up", "medium_close_up", "medium_shot"})
_L6_UPPER_CUTOFF_RE = re.compile(
    r"\b(?:waist-up\s+framing|chest-up\s+shot|upper\s+body\s+framing|half-body\s+portrait|"
    r"face\s+shot\s+filling\s+frame|portrait\s+framing|medium\s+shot|medium\s+close-up|"
    r"breast\s+and\s+cleavage\s+focus)\b",
    re.I,
)
_L4_BRANCH_PREFERRED_STYLES: Dict[str, Set[str]] = {
    "l4_hosiery_footwear_only": {
        "clothing_legwear_combo",
        "legwear_fishnet_pantyhose",
        "legwear_fishnet_thighhighs",
        "legwear_pantyhose",
        "legwear_thighhighs",
        "hosiery_legwear",
        "footwear_boots",
        "footwear_sneakers",
    },
    "l4_panties_only": {"lingerie_panties", "bikini_micro", "lingerie_lace"},
    "l4_bra_and_panties": {
        "lingerie_lace",
        "lingerie_bra",
        "lingerie_panties",
        "bikini_classic",
        "bikini_strappy",
        "bikini_creative",
        "bikini_micro",
    },
}
_L3_BRANCH_PREFERRED_STYLES: Dict[str, Set[str]] = {
    "l3_bra_pushed_up": {"lingerie_bra", "lingerie_lace", "jk_seifuku", "blazer_uniform", "ol_suit"},
    "l3_bikini_top_removed": {"bikini_classic", "bikini_strappy", "bikini_creative", "bikini_micro"},
    "l3_panties_only": {"lingerie_panties", "bikini_micro", "lingerie_lace"},
}


def _is_tag_compatible_with_nudity(
    tag: Any,
    nudity_level_code: Optional[str],
    *,
    slot_name: str = "",
    item_id: str = "",
    nudity_branch: Optional[str] = None,
) -> bool:
    """判定跨槽位标签是否与目标裸露等级/分支相容（优先结构化事实，其次安全关键词保底）。"""
    if not nudity_level_code:
        return True

    facts = tag.get("facts", {}) if isinstance(tag, dict) else {}
    txt = str(tag.get("text", "") if isinstance(tag, dict) else tag).strip()
    tl = txt.lower()
    is_safe_phrase = bool(_SAFE_NON_NUDE_PHRASE_RE.search(tl))

    if isinstance(facts, dict):
        allowed_lvls = facts.get("allowed_nudity_levels") or ()
        if allowed_lvls and nudity_level_code not in allowed_lvls:
            return False

        exp_regions = set(facts.get("exposed_regions") or ())
        vis_regions = set(facts.get("visible_regions") or ())
        liq_kind = facts.get("liquid_kind")

        if nudity_level_code == "L1":
            if exp_regions & _NON_L1_EXPOSED_REGIONS:
                return False
            if vis_regions & _NON_L1_VISIBLE_REGIONS:
                return False
            if liq_kind in ("sexual_fluid", "cum", "semen"):
                return False
        elif nudity_level_code == "L2":
            if exp_regions & _NON_L2_EXPOSED_REGIONS:
                return False
            if vis_regions & {"intimate_lower_body", "crotch"}:
                return False
            if liq_kind in ("sexual_fluid", "cum", "semen"):
                return False
        elif nudity_level_code == "L6" and slot_name == "shot_type":
            if item_id in _L6_INCOMPATIBLE_SHOT_IDS:
                return False
            if (vis_regions & {"face", "upper_body"}) and not (vis_regions & {"lower_body", "intimate_lower_body", "full_body"}):
                return False
            if exp_regions and "breasts" in exp_regions and "genitals" not in exp_regions:
                return False

    if nudity_level_code == "L6" and slot_name == "shot_type":
        if item_id in _L6_INCOMPATIBLE_SHOT_IDS or _L6_UPPER_CUTOFF_RE.search(tl):
            return False
    elif nudity_level_code == "L1" and not is_safe_phrase:
        if slot_name in ("theme", "scene_theme") and item_id in _L1_BANNED_THEMES:
            return False
        if slot_name == "props" and item_id in ("sex_toy_vibrator", "bondage_rope_collar"):
            return False
        if _L1_NSFW_TEXT_RE.search(tl):
            return False
    elif nudity_level_code == "L2" and not is_safe_phrase:
        if slot_name in ("theme", "scene_theme") and item_id in _L2_BANNED_THEMES:
            return False
        if slot_name == "props" and item_id in ("sex_toy_vibrator",):
            return False
        if _L2_NSFW_TEXT_RE.search(tl):
            return False

    return True


def _collect_item_tags(item: Dict[str, Any]) -> List[Any]:
    tags: List[Any] = []
    for k in ("tags", "anchor_tags", "detail_tags"):
        if isinstance(item.get(k), list):
            tags.extend(item[k])
    for sub in item.get("subcategories", []):
        if isinstance(sub, dict) and isinstance(sub.get("tags"), list):
            tags.extend(sub["tags"])
    for it in item.get("items", []):
        if isinstance(it, dict) and isinstance(it.get("tags"), list):
            tags.extend(it["tags"])
    return tags


def _choose_coherent_branch(
    lvl_code: str,
    raw_tags: Sequence[Any],
    rng: Random,
    explicit_branch: Optional[str] = None,
    allowed_branches: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """为目标等级选定唯一状态分支；有标签集合时确保所选分支在集合内至少存在 1 条相容词条，并遵循服装约束分支池。"""
    allowed = tuple(NUDITY_BRANCHES_BY_LEVEL.get(lvl_code, ()))
    if not allowed:
        return None
    if explicit_branch and explicit_branch in allowed:
        return explicit_branch

    candidate_allowed = allowed
    if allowed_branches:
        narrowed = tuple(b for b in allowed if b in allowed_branches)
        if narrowed:
            candidate_allowed = narrowed

    tag_branches: Set[str] = set()
    for t in raw_tags:
        if isinstance(t, dict):
            f = t.get("facts") or {}
            if isinstance(f, dict):
                for b in f.get("nudity_branches") or ():
                    if b in candidate_allowed:
                        tag_branches.add(b)
    pool = [b for b in candidate_allowed if b in tag_branches] if raw_tags and tag_branches else list(candidate_allowed)
    return rng.choice(pool) if pool else candidate_allowed[0]


def _is_linkage_tag_compatible_with_branch(
    tag: Any,
    lvl_code: Optional[str],
    nudity_branch: Optional[str],
) -> bool:
    """过滤 clothing_nudity_linkage 的通用联动词条，确保与单一选定分支及等级规则一致。"""
    if not isinstance(tag, dict):
        return True
    facts = tag.get("facts") or {}
    txt = str(tag.get("text", "")).lower()

    if lvl_code == "L4":
        topos = set(facts.get("garment_topologies") or ())
        if topos & {"top", "bottom_pants", "bottom_skirt", "outerwear", "one_piece", "ensemble_outfit"}:
            return False
        if nudity_branch == "l4_bra_and_panties":
            if (
                "topless" in txt
                or "bare breasts" in txt
                or "only lace panties" in txt
                or "breasts" in (facts.get("exposed_regions") or ())
            ):
                return False
        elif nudity_branch in ("l4_hosiery_footwear_only", "l4_towel_sheet_wrap", "l4_accessory_only"):
            if "panties" in txt or "bra" in txt or "underwear" in topos:
                return False

    if not nudity_branch:
        return True

    tag_branches = facts.get("nudity_branches") or ()
    if tag_branches and nudity_branch not in tag_branches:
        return False

    if nudity_branch == "l5_clothes_discarded_nearby":
        return True
    if nudity_branch == "l5_full_nude_standing_or_frontal":
        if "discarded" in (facts.get("garment_states") or ()) or "discarded" in txt or "tossed aside" in txt:
            return False

    if lvl_code == "L2":
        exp_regions = set(facts.get("exposed_regions") or ())
        if nudity_branch == "l2_collar_neckline":
            if exp_regions & {"shoulders", "thigh_leg", "ankle_feet", "midriff", "back", "arms_wrists"}:
                return False
            if any(w in txt for w in ("strap slipping", "off-shoulder", "off shoulder", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "backless", "rolled up", "pushed sleeve")):
                return False
        elif nudity_branch == "l2_shoulder_strap":
            if exp_regions & {"cleavage", "collarbone_neck", "underwear_bra", "thigh_leg", "ankle_feet", "midriff", "back", "arms_wrists"}:
                return False
            if any(w in txt for w in ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "backless", "rolled up", "pushed sleeve")):
                return False
        elif nudity_branch == "l2_leg_slit":
            if exp_regions & {"cleavage", "collarbone_neck", "underwear_bra", "shoulders", "midriff", "back", "arms_wrists"}:
                return False
            if any(w in txt for w in ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "midriff", "waist cutout", "open side", "backless", "rolled up", "pushed sleeve")):
                return False
        elif nudity_branch == "l2_midriff_waist":
            if exp_regions & {"cleavage", "collarbone_neck", "underwear_bra", "shoulders", "thigh_leg", "ankle_feet", "back", "arms_wrists"}:
                return False
            if any(w in txt for w in ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "high slit", "bare leg", "skirt slit", "backless", "rolled up", "pushed sleeve")):
                return False
        elif nudity_branch == "l2_backless":
            if exp_regions & {"cleavage", "collarbone_neck", "underwear_bra", "shoulders", "thigh_leg", "ankle_feet", "midriff", "arms_wrists"}:
                return False
            if any(w in txt for w in ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "rolled up", "pushed sleeve")):
                return False
        elif nudity_branch == "l2_sleeve_wrist":
            if exp_regions & {"cleavage", "collarbone_neck", "underwear_bra", "shoulders", "thigh_leg", "ankle_feet", "midriff", "back"}:
                return False
            if any(w in txt for w in ("collar unbuttoned", "unbuttoned", "button undone", "cleavage", "collarbone", "strap slipping", "off-shoulder", "high slit", "bare leg", "skirt slit", "midriff", "waist cutout", "open side", "backless")):
                return False

    if lvl_code == "L3":
        if nudity_branch == "l3_panties_only":
            if any(w in txt for w in ("shirt", "blouse", "skirt", "dress", "bra", "tank top")):
                return False
        elif nudity_branch == "l3_topless_skirt_kept":
            if any(w in txt for w in ("shirt open", "dress bunched", "dress pulled", "tank top", "bra", "blouse")):
                return False
        elif nudity_branch == "l3_topless_pants_kept":
            if any(w in txt for w in ("shirt open", "dress bunched", "dress pulled", "tank top", "skirt", "bra", "blouse")):
                return False
        elif nudity_branch == "l3_pulled_down_dress":
            if any(w in txt for w in ("shirt open", "skirt hitched", "tank top", "blouse")):
                return False
        elif nudity_branch == "l3_open_shirt_breasts":
            if any(w in txt for w in ("dress bunched", "dress pulled", "skirt hitched", "tank top")):
                return False
        elif nudity_branch == "l3_raised_top_breasts":
            if any(w in txt for w in ("dress bunched", "dress pulled", "skirt hitched", "shirt open", "blouse")):
                return False
        elif nudity_branch == "l3_bra_pushed_up":
            if any(w in txt for w in ("dress bunched", "dress pulled", "skirt hitched", "shirt open", "tank top", "bra removed", "braless", "no bra")):
                return False
        elif nudity_branch == "l3_bikini_top_removed":
            if any(w in txt for w in ("shirt open", "dress bunched", "dress pulled", "skirt hitched", "tank top", "blouse")):
                return False

    if lvl_code == "L4":
        if nudity_branch == "l4_hosiery_footwear_only":
            if any(w in txt for w in ("panties", "bra", "underwear")):
                return False
        elif nudity_branch == "l4_towel_sheet_wrap":
            if any(w in txt for w in ("panties", "bra", "underwear")):
                return False
        elif nudity_branch == "l4_accessory_only":
            if any(w in txt for w in ("panties", "bra", "underwear")):
                return False
        elif nudity_branch == "l4_panties_only":
            if "bra" in txt and "panties" not in txt:
                return False

    return True


class ExactCatalogIndex:
    """单一 selector catalog 的精确索引，支持 id, canonical display fields 与显式 aliases。

    不变量 (修订 5 强约束)：
    - 仅登记：id, level, 该 catalog 的正式显示字段 (name_zh, name, label, theme_zh, style_name, name_en),
      正式组合显示格式 (如 presets 的 'id (name_zh)', icon 项目的 'icon name_zh'), 以及显式 aliases;
    - 严禁隐式自动拆解括号 (paren) 或斜杠 (slash-split) 生成派生 key;
    - 隔离索引：碰撞检查严格限定于当前 selector catalog，杜绝跨文件/跨 catalog 误报;
    - 无 ID 项无法通过 None == None 绕过碰撞：不同对象共享相同 key 时必然 Fail-Closed 抛 CatalogIndexingError;
    - 查询为严格全词/全字段规范化精确字典匹配，零模糊子串与零中文包含回退。
    """
    def __init__(self, catalog_name: str):
        self.catalog_name = catalog_name
        self.key_to_item: Dict[str, Dict[str, Any]] = {}

    def register_item(self, item: Dict[str, Any]) -> None:
        keys_to_reg: List[str] = []
        # 1. 规范标识符与规范显示字段
        for k in ("id", "level", "name_zh", "name", "label", "theme_zh", "style_name", "name_en"):
            val = item.get(k)
            if val is not None and isinstance(val, str) and val.strip():
                keys_to_reg.append(val)

        # 2. 正式 UI 组合显示格式 (由数据规范直接定义，非启发式隐式派生)
        if "id" in item and "name_zh" in item and isinstance(item["name_zh"], str):
            keys_to_reg.append(f"{item['id']} ({item['name_zh']})")
            keys_to_reg.append(f"{item['id']} {item['name_zh']}")
        if "icon" in item and "name_zh" in item and isinstance(item["name_zh"], str):
            keys_to_reg.append(f"{item['icon']} {item['name_zh']}".strip())

        # 3. 显式别名 (必须来自 JSON 数据定义)
        for a in item.get("aliases", []):
            if a and isinstance(a, str) and a.strip():
                keys_to_reg.append(a)

        for key in keys_to_reg:
            norm = key.strip().casefold()
            if not norm:
                continue
            if norm in self.key_to_item:
                existing = self.key_to_item[norm]
                # 严格碰撞检查：同一 catalog 内两个不同对象注册了相同 key，
                # 无论是否有 id 字段，均不得绕过
                existing_id = existing.get("id")
                item_id = item.get("id")
                if existing is not item:
                    raise CatalogIndexingError(
                        f"Exact key collision in catalog '{self.catalog_name}' on key '{key}' "
                        f"(normalized '{norm}') between different items (ids '{existing_id}' and '{item_id}')"
                    )
            self.key_to_item[norm] = item

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        if not query or not str(query).strip():
            return None
        norm = str(query).strip().casefold()
        return self.key_to_item.get(norm)


def _match_item(items: Sequence[Dict[str, Any]], query: str, catalog_name: str = "general") -> Optional[Dict[str, Any]]:
    """向后兼容接口：基于 ExactCatalogIndex 的精确匹配，零模糊子串容忍。"""
    if not query or not items:
        return None
    idx = ExactCatalogIndex(catalog_name)
    for it in items:
        idx.register_item(it)
    return idx.get(query)


def _match_tag(items: Sequence[Dict[str, Any]], query: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """若 query 无法直接匹配任何 item，则在所有 item 的 tags 中查找是否匹配 tag id、text 或 tag 字段。
    返回 (parent_item, matched_tag) 或 None。
    """
    if not query or not items:
        return None
    norm = str(query).strip().casefold()
    norm_short = norm[:96].rstrip('_') if len(norm) > 96 else norm
    for item in items:
        for t in item.get("tags", []):
            if isinstance(t, dict):
                t_id = t.get("id", "").strip().casefold()
                t_text = t.get("text", "").strip().casefold()
                t_tag = t.get("tag", "").strip().casefold()
                if (norm in (t_id, t_text, t_tag) or norm_short == t_id) and norm:
                    return (item, t)
        for sub in item.get("subcategories", []):
            for t in sub.get("tags", []):
                if isinstance(t, dict):
                    t_id = t.get("id", "").strip().casefold()
                    t_text = t.get("text", "").strip().casefold()
                    t_tag = t.get("tag", "").strip().casefold()
                    if (norm in (t_id, t_text, t_tag) or norm_short == t_id) and norm:
                        return (item, t)
def _are_pose_tags_compatible(t1: Any, t2: Any) -> bool:
    """核验两个姿态标签在物理上是否可同时执行（相容性判定）。"""
    f1 = t1.get("facts", {}) if isinstance(t1, dict) else {}
    f2 = t2.get("facts", {}) if isinstance(t2, dict) else {}
    if not f1 or not f2:
        return True

    # 1. 互斥组硬约束
    m1 = set(f1.get("mutex_groups", []))
    m2 = set(f2.get("mutex_groups", []))
    if m1 and m2 and (m1 & m2):
        return False

    pf1 = f1.get("pose_facts", {}) if isinstance(f1.get("pose_facts"), dict) else {}
    pf2 = f2.get("pose_facts", {}) if isinstance(f2.get("pose_facts"), dict) else {}

    # 2. 身体物理支撑约束 (body_support 互斥，如 standing vs sitting vs kneeling vs lying vs quadrupedal)
    bs1 = pf1.get("body_support") or f1.get("body_support")
    bs2 = pf2.get("body_support") or f2.get("body_support")
    if bs1 and bs2 and bs1 != "unspecified" and bs2 != "unspecified" and bs1 != bs2:
        return False

    # 3. 姿势大类互斥 (posture_group 互斥，如 standing vs seated vs kneeling vs reclining)
    pg1 = pf1.get("posture_group") or f1.get("posture_group")
    pg2 = pf2.get("posture_group") or f2.get("posture_group")
    if pg1 and pg2 and pg1 != pg2:
        return False

    # 4. 手部数量物理上限约束 (双臂合计至多 2 只手)
    hr1 = pf1.get("hands_required", f1.get("hands_required", 0))
    hr2 = pf2.get("hands_required", f2.get("hands_required", 0))
    if isinstance(hr1, int) and isinstance(hr2, int) and hr1 + hr2 > 2:
        return False

    # 5. 手部状态冲突 (如 restrained vs intense_motion / supports_body)
    hs1 = pf1.get("hand_state") or f1.get("hand_state")
    hs2 = pf2.get("hand_state") or f2.get("hand_state")
    if hs1 == "restrained" and hs2 in ("intense_motion", "supports_body", "both_busy"):
        return False
    if hs2 == "restrained" and hs1 in ("intense_motion", "supports_body", "both_busy"):
        return False

    # 6. 整身叙述模板互斥 (is_prose_template)
    if (pf1.get("is_prose_template") or f1.get("is_prose_template")) and (pf2.get("is_prose_template") or f2.get("is_prose_template")):
        return False

    return True


class DataSampler:
    """从 data/ 目录加载所有分类数据，提供各槽位精准/情境加权采样与列举接口。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._cache: Dict[str, Any] = {}
        self._scene_by_id: Dict[str, Dict[str, Any]] = {}
        self._scene_by_label: Dict[str, Dict[str, Any]] = {}
        self._scene_by_alias: Dict[str, Dict[str, Any]] = {}
        self._scene_groups_by_category: Dict[str, List[Dict[str, Any]]] = {}
        self._all_scene_items: List[Dict[str, Any]] = []
        self._scenes_indexed: bool = False
        self._catalog_indices: Dict[str, ExactCatalogIndex] = {}
        self.affinity_registry = ContextAffinityRegistry(self.data_dir)


    def _get_catalog_index(self, name: str, items: Sequence[Dict[str, Any]]) -> ExactCatalogIndex:
        if name not in self._catalog_indices:
            idx = ExactCatalogIndex(name)
            for it in items:
                idx.register_item(it)
            self._catalog_indices[name] = idx
        return self._catalog_indices[name]

    def _load(self, name: str) -> Any:
        if name not in self._cache:
            p = self.data_dir / f"{name}.json"
            if not p.is_file():
                raise DataLoadError(f"Missing required data file: {p}")
            try:
                self._cache[name] = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise DataLoadError(f"Invalid JSON in {p} (line {e.lineno}, col {e.colno}): {e.msg}") from e
        return self._cache[name]

    def _ensure_scenes_indexed(self) -> None:
        if self._scenes_indexed:
            return
        data = self._load("scenes")
        self._scene_by_id.clear()
        self._scene_by_label.clear()
        self._scene_by_alias.clear()
        self._scene_groups_by_category.clear()
        self._all_scene_items.clear()
        for g in data.get("scenes", []):
            cat_name = g.get("category", "")
            if cat_name:
                self._scene_groups_by_category[cat_name] = g.get("items", [])
            for item in g.get("items", []):
                self._all_scene_items.append(item)
                if "id" in item:
                    self._scene_by_id[item["id"]] = item
                if "label" in item:
                    self._scene_by_label[item["label"]] = item
                if "subcategory" in item and item["subcategory"] not in self._scene_by_label:
                    self._scene_by_label[item["subcategory"]] = item
                for alias in item.get("aliases", []):
                    if alias:
                        self._scene_by_alias[alias] = item
        self._scenes_indexed = True

    def _match_scene_tag(self, query: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        if not query:
            return None
        norm = str(query).strip().casefold()
        norm_short = norm[:96].rstrip('_') if len(norm) > 96 else norm
        self._ensure_scenes_indexed()
        # 语义优先级：优先匹配独立专属条目的核心 anchor_tags，杜绝从属容器 detail_tags 遮蔽
        for tag_list_key in ("anchor_tags", "detail_tags", "tags"):
            for item in self._all_scene_items:
                for t in item.get(tag_list_key, []):
                    if isinstance(t, dict):
                        t_id = t.get("id", "").strip().casefold()
                        t_text = t.get("text", "").strip().casefold()
                        if (norm in (t_id, t_text) or norm_short == t_id) and norm:
                            return (item, t)
        return None

    def _record_pool_sampling_rationale(
        self,
        chosen: Optional[Dict[str, Any]],
        pool: Sequence[Dict[str, Any]],
        mode: str = "uniform_random",
        active_contexts: Sequence[str] = (),
        excluded_ids: Optional[Sequence[str]] = None,
    ) -> None:
        """从候选池真实规模与候选集合采集并记录采样依据，杜绝事后虚拟默认值。"""
        cand_ids = [it.get("id") for it in pool if isinstance(it, dict) and it.get("id")]
        n_cands = len(cand_ids)
        prob = round(1.0 / n_cands, 6) if n_cands > 0 else 0.0
        dist = {cid: prob for cid in cand_ids}
        excl = sorted(list(excluded_ids)) if excluded_ids else []
        self._last_sampling_rationale = {
            "active_contexts": list(active_contexts),
            "candidate_filter": {
                "total_candidates": n_cands,
                "excluded_hard_conflicts": excl,
                "passed_candidates": n_cands,
            },
            "distribution": dist,
            "fallback": {"used": False, "reason": None},
            "final_selection": {
                "selected_id": chosen.get("id") if isinstance(chosen, dict) else None,
                "mode": mode,
            },
        }

    def _pop_sampling_rationale(
        self,
        default_item_id: Optional[str] = None,
        default_mode: str = "explicit",
        active_contexts: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """弹出最近一次采样依据，若不存在则为显式/默认选择构建合法的 Rationale 字典。"""
        if getattr(self, "_last_sampling_rationale", None) is not None:
            rat = self._last_sampling_rationale
            self._last_sampling_rationale = None
            return rat
        return {
            "active_contexts": list(active_contexts),
            "candidate_filter": {
                "total_candidates": 1 if default_item_id else 0,
                "excluded_hard_conflicts": [],
                "passed_candidates": 1 if default_item_id else 0,
            },
            "distribution": {default_item_id: 1.0} if default_item_id else {},
            "fallback": {"used": False, "reason": None},
            "final_selection": {
                "selected_id": default_item_id,
                "mode": default_mode,
            },
        }

    def _sample_from_candidates(
        self,
        slot_name: str,
        candidates: Sequence[Dict[str, Any]],
        rng: Random,
        profile: Optional[ContextProfile] = None,
        context: Optional[str] = None,
        excluded_ids: Optional[Set[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        total_candidates = len(candidates)
        excluded_list = sorted(list(excluded_ids)) if excluded_ids else []
        if not candidates:
            self._last_sampling_rationale = {
                "active_contexts": [],
                "candidate_filter": {
                    "total_candidates": 0,
                    "excluded_hard_conflicts": [],
                    "passed_candidates": 0,
                },
                "distribution": {},
                "fallback": {"used": True, "reason": "empty_candidates"},
                "final_selection": {"selected_id": None, "mode": "none"},
            }
            return None
        if excluded_ids:
            candidates = [c for c in candidates if c.get("id") not in excluded_ids]
            if not candidates:
                self._last_sampling_rationale = {
                    "active_contexts": [],
                    "candidate_filter": {
                        "total_candidates": total_candidates,
                        "excluded_hard_conflicts": excluded_list,
                        "passed_candidates": 0,
                    },
                    "distribution": {},
                    "fallback": {"used": True, "reason": "all_candidates_excluded_by_hard_conflicts"},
                    "final_selection": {"selected_id": None, "mode": "none"},
                }
                return None

        passed_candidates = len(candidates)
        cand_map = {c.get("id"): c for c in candidates if "id" in c}
        cand_ids = [c["id"] for c in candidates if "id" in c]

        if profile is None:
            if context:
                profile = compute_context_profile([context], [])
            else:
                profile = compute_context_profile([], [])

        active_contexts = list(profile.scene_context_ids) + list(profile.theme_context_ids)

        if not cand_ids:
            chosen = self._pick_one(list(candidates), rng)
            self._last_sampling_rationale = {
                "active_contexts": active_contexts,
                "candidate_filter": {
                    "total_candidates": total_candidates,
                    "excluded_hard_conflicts": excluded_list,
                    "passed_candidates": passed_candidates,
                },
                "distribution": {},
                "fallback": {"used": False, "reason": None},
                "final_selection": {
                    "selected_id": chosen.get("id") if chosen else None,
                    "mode": "uniform_random",
                },
            }
            return chosen

        if hasattr(self, "affinity_registry") and slot_name in self.affinity_registry.matrix.get("generic", {}):
            dist = compute_slot_distribution(
                slot_name,
                profile,
                cand_ids,
                self.affinity_registry.matrix,
            )
            chosen_id = sample_categorical(dist, rng)
            # 严格保留 baseline 随机数消费顺序 (cand_map.get 预求值语义)
            fallback_pick = self._pick_one(list(candidates), rng)
            fallback_used = False
            fallback_reason = None
            if chosen_id in cand_map:
                chosen = cand_map[chosen_id]
            else:
                chosen = fallback_pick
                fallback_used = True
                fallback_reason = "categorical_sample_not_in_candidates"

            self._last_sampling_rationale = {
                "active_contexts": active_contexts,
                "candidate_filter": {
                    "total_candidates": total_candidates,
                    "excluded_hard_conflicts": excluded_list,
                    "passed_candidates": passed_candidates,
                },
                "distribution": {k: round(v, 6) for k, v in dist.items()},
                "fallback": {"used": fallback_used, "reason": fallback_reason},
                "final_selection": {
                    "selected_id": chosen.get("id") if chosen else chosen_id,
                    "mode": "affinity_sampling",
                },
            }
            return chosen
        else:
            uniform_prob = round(1.0 / len(cand_ids), 6)
            dist = {cid: uniform_prob for cid in cand_ids}
            chosen = self._pick_one(list(candidates), rng)
            self._last_sampling_rationale = {
                "active_contexts": active_contexts,
                "candidate_filter": {
                    "total_candidates": total_candidates,
                    "excluded_hard_conflicts": excluded_list,
                    "passed_candidates": passed_candidates,
                },
                "distribution": dist,
                "fallback": {"used": False, "reason": None},
                "final_selection": {
                    "selected_id": chosen.get("id") if chosen else None,
                    "mode": "uniform_random",
                },
            }
            return chosen

    def _lookup_pose_hand_state(self, pose_id: Optional[str]) -> Optional[str]:
        """查询姿势标签的事实 hand_state。若传入的是大类 ID 或未知，返回 None。"""
        if not pose_id:
            return None
        p_low = pose_id.lower().strip()
        data = self._load("poses")
        for cat in data.get("pose_categories", []):
            for sub in cat.get("subcategories", []):
                for t in sub.get("tags", []):
                    if isinstance(t, dict) and t.get("id", "").lower() == p_low:
                        facts = t.get("facts", {})
                        return facts.get("hand_state")
        return None

    @classmethod
    def _is_accessory_or_detail(cls, text: str) -> bool:
        """判定构件文本是否纯属附属配件/饰品/细节（如腰带、袖子、刺绣、领口等），杜绝其被误判为承载动作的完整服装。"""
        if not text:
            return False
        t_low = text.lower()
        garment_nouns = (
            "dress", "skirt", "robe", "gown", "kimono", "yukata", "cheongsam",
            "qipao", "hanfu", "hanbok", "nightgown", "bathrobe", "nightie",
            "sundress", "kilt", "sarong", "lehenga", "chima", "shirt", "blouse",
            "suit", "jacket", "coat", "corset", "vest", "top", "pants", "shorts",
            "jeans", "swimsuit", "bikini", "uniform", "changshan", "daopao",
            "uchikake", "sari", "dirndl", "furisode", "tunic", "slip", "caftan",
            "abaya", "overalls", "dungarees"
        )
        if any(w in t_low for w in garment_nouns):
            return False
        return bool(re.search(
            r"\b(?:untied belt|belts?|sash|obi|sleeves?|floral embroidery|embroidery|embroidered ribbons?|ribbons?|collars?|scarf|scarves|bow tie|necktie|ties?|cuffs?|headbands?|pompoms?|badges?|brooches?|harness|studs|push-up cups|tulle fabric|delicate gauze|fabrics?|details?)\b",
            t_low,
            re.IGNORECASE,
        ))

    @classmethod
    def _tag_has_skirt_carrier(cls, tag: Any, style_id: str) -> bool:
        """判定单个标签（或 PromptAtom / SampledTag）是否包含或代表裙装形制载体。"""
        if not tag:
            return False
        txt = tag.get("text", "").lower() if isinstance(tag, dict) else (getattr(tag, "text", "").lower() if hasattr(tag, "text") else str(tag).lower())
        if cls._is_accessory_or_detail(txt):
            return False

        topos = set()
        facts = None
        if isinstance(tag, dict):
            facts = tag.get("facts", {})
            topos = set(facts.get("garment_topologies") or [])
            ens = facts.get("ensemble_pieces")
            if isinstance(ens, dict):
                bps = ens.get("bottom_pieces", [])
                mgs = ens.get("main_garments", [])
                if any(
                    any(k in p.lower() for k in ("skirt", "dress", "kilt", "pleated", "tutu", "petticoat"))
                    and not any(k in p.lower() for k in ("pants", "jeans", "shorts", "trousers", "slacks", "leggings"))
                    for p in bps + mgs if isinstance(p, str)
                ):
                    return True
                pbs = ens.get("piece_bindings", [])
                if any(pb.get("garment_topology") == "bottom_skirt" for pb in pbs if isinstance(pb, dict)):
                    return True
            supp = set(facts.get("supported_modifiers") or [])
            if "bottom_skirt" in supp:
                return True
        elif hasattr(tag, "facts"):
            facts = getattr(tag, "facts", None)
            topos = set(getattr(facts, "garment_topologies", ()) if facts else ())

        if topos:
            if "bottom_skirt" in topos:
                return True
            if "one_piece" in topos and style_id not in NON_SKIRT_ONE_PIECE:
                return True
            if "ensemble_outfit" in topos:
                if re.search(r"\b(?:skirt|pleated mini skirt|pencil skirt|chima|lehenga)\b", txt):
                    return True
                if re.search(r"\b(?:dress|gown|nightgown|bathrobe|nightie|slip dress)\b", txt):
                    return True
            return False

        if re.search(r"\b(?:skirt|pleated mini skirt|pencil skirt|chima|lehenga)\b", txt):
            return True
        if re.search(r"\b(?:dress|gown|nightgown|bathrobe|nightie|slip dress)\b", txt) and style_id not in NON_SKIRT_ONE_PIECE:
            return True
        return False

    def _is_clothing_skirt_compatible(self, cat: Dict[str, Any]) -> bool:
        """判定服装款式类别是否具备承载裙摆（掀裙）动作的形制载体。
        需满足：自身拓扑含 bottom_skirt、支持对应 modifier，或非排除名单的连体裙装 (one_piece)。
        杜绝仅凭类别名称关键词粗暴漏判。
        """
        cid = cat.get("id", "")
        if cid in NON_SKIRT_ONE_PIECE:
            return False
        for t in cat.get("tags", []):
            if self._tag_has_skirt_carrier(t, cid):
                return True
        return False

    def get_excluded_candidate_ids(
        self,
        slot_name: str,
        nudity_level_code: Optional[str] = None,
        shot_type_id: Optional[str] = None,
        clothing_state: Optional[str] = None,
        pose_id: Optional[str] = None,
        hand_state: Optional[str] = None,
        context: Optional[str] = None,
        nudity_branch: Optional[str] = None,
    ) -> Set[str]:
        """依据已知硬约束规则，在实际抽样前精确生成被排除的冲突候选 ID 集合。"""
        excluded: Set[str] = set()

        def _exclude_if_no_compatible_tag(items: Sequence[Dict[str, Any]], check_slot: str) -> None:
            for it in items:
                iid = it.get("id")
                if not iid or iid == "none":
                    continue
                tags = _collect_item_tags(it)
                if not tags:
                    if not _is_tag_compatible_with_nudity(
                        {"text": it.get("name_zh", iid)},
                        nudity_level_code,
                        slot_name=check_slot,
                        item_id=iid,
                        nudity_branch=nudity_branch,
                    ):
                        excluded.add(iid)
                    continue
                if not any(
                    _is_tag_compatible_with_nudity(
                        t,
                        nudity_level_code,
                        slot_name=check_slot,
                        item_id=iid,
                        nudity_branch=nudity_branch,
                    )
                    for t in tags
                ):
                    excluded.add(iid)

        if slot_name == "camera_angle":
            if nudity_level_code == "L1":
                excluded.add("between_legs_angle")
            elif nudity_level_code in ("L1", "L2"):
                data = self._load("shot_types")
                _exclude_if_no_compatible_tag(data.get("camera_angles", []), "camera_angle")

        elif slot_name == "shot_type":
            if nudity_level_code == "L6":
                excluded.update(_L6_INCOMPATIBLE_SHOT_IDS)
            if nudity_level_code in ("L1", "L2", "L6"):
                data = self._load("shot_types")
                _exclude_if_no_compatible_tag(data.get("shot_types", []), "shot_type")

        elif slot_name in ("theme", "scene_theme"):
            if nudity_level_code == "L1":
                excluded.update(_L1_BANNED_THEMES)
            elif nudity_level_code == "L2":
                excluded.update(_L2_BANNED_THEMES)
            if nudity_level_code in ("L1", "L2"):
                data = self._load("themes")
                _exclude_if_no_compatible_tag(data.get("themes", []), "theme")

        elif slot_name == "hairstyle":
            if nudity_level_code in ("L1", "L2"):
                data = self._load("accessories")
                _exclude_if_no_compatible_tag(data.get("hairstyles", []), "hairstyle")

        elif slot_name == "jewelry":
            if nudity_level_code in ("L1", "L2"):
                data = self._load("accessories")
                items = list(data.get("headwear_jewelry", [])) + list(data.get("jewelry", []))
                _exclude_if_no_compatible_tag(items, "jewelry")

        elif slot_name == "tattoo":
            if nudity_level_code in ("L1", "L2"):
                data = self._load("tattoos")
                _exclude_if_no_compatible_tag(data.get("categories", []), "tattoo")

        elif slot_name == "liquids":
            if nudity_level_code in ("L1", "L2"):
                data = self._load("nudity_levels")
                liquids = data.get("body_liquids", []) + data.get("environmental_liquids", [])
                if not liquids:
                    liquids = data.get("liquid_effects", [])
                _exclude_if_no_compatible_tag(liquids, "liquids")

        elif slot_name == "lighting":
            if nudity_level_code in ("L1", "L2"):
                data = self._load("lighting")
                for group in data.values():
                    if isinstance(group, list):
                        for item in group:
                            if isinstance(item, dict) and item.get("id"):
                                base_tags = item.get("tags", [])
                                if nudity_level_code == "L1" and base_tags:
                                    if not any(
                                        _is_tag_compatible_with_nudity(t, "L1", slot_name="lighting", item_id=item["id"])
                                        for t in base_tags
                                    ):
                                        excluded.add(item["id"])

        elif slot_name == "props":
            hands_occupied = 0
            if hand_state in ("both_busy", "restrained"):
                hands_occupied = 2
            elif hand_state == "one_busy":
                hands_occupied = 1
            elif hand_state in ("free", "unspecified"):
                hands_occupied = 0
            elif pose_id:
                tag_hs = self._lookup_pose_hand_state(pose_id)
                if tag_hs in ("both_busy", "restrained"):
                    hands_occupied = 2
                elif tag_hs == "one_busy":
                    hands_occupied = 1
                else:
                    hands_occupied = 0

            available_hands = max(0, 2 - hands_occupied)

            data = self._load("props")
            for cat in data.get("categories", []):
                cid = cat.get("id")
                if not cid or cid == "none":
                    continue
                all_tags = []
                if cat.get("tags"):
                    all_tags.extend(cat["tags"])
                if cat.get("items"):
                    for it in cat["items"]:
                        all_tags.extend(it.get("tags", []))

                has_valid_variant = False
                for t in all_tags:
                    if not isinstance(t, dict):
                        continue
                    facts = t.get("facts", {})
                    req = facts.get("hands_required", 0)
                    usage = facts.get("prop_usage")
                    if req > available_hands:
                        continue
                    if available_hands == 0 and usage == "handheld":
                        continue
                    if not _is_tag_compatible_with_nudity(t, nudity_level_code, slot_name="props", item_id=cid):
                        continue
                    has_valid_variant = True
                    break

                if not has_valid_variant:
                    excluded.add(cid)

            if nudity_level_code == "L1":
                excluded.update({"sex_toy_vibrator", "bondage_rope_collar"})
            elif nudity_level_code == "L2":
                excluded.add("sex_toy_vibrator")

        elif slot_name == "pose":
            if nudity_level_code in ("L1", "L2"):
                data = self._load("poses")
                _exclude_if_no_compatible_tag(data.get("pose_categories", []), "pose")

        elif slot_name == "clothing":
            data = None
            if clothing_state:
                st_low = clothing_state.lower()
                if any(w in st_low for w in ("unbuttoned", "解开纽扣", "opened", "解扣")):
                    data = self._load("clothing")
                    for c in data.get("categories", []):
                        cid = c.get("id")
                        if cid and cid not in ALLOWED_BUTTON_STYLES:
                            excluded.add(cid)
                elif any(w in st_low for w in ("lifted", "掀起裙摆", "lifted_up", "lifted_skirt")):
                    data = self._load("clothing")
                    for c in data.get("categories", []):
                        cid = c.get("id", "")
                        if cid and not self._is_clothing_skirt_compatible(c):
                            excluded.add(cid)
            if nudity_level_code in ("L1", "L2"):
                if nudity_level_code == "L1":
                    excluded.update({"bikini_micro", "naked_ribbon", "bondage_harness", "sling_bikini"})
                if data is None:
                    data = self._load("clothing")
                for c in data.get("categories", []):
                    cid = c.get("id")
                    tags = c.get("tags", [])
                    if cid and tags and not any(is_tag_eligible_for_nudity_level(t, nudity_level_code) for t in tags):
                        excluded.add(cid)
            elif nudity_level_code == "L4" and nudity_branch in _L4_BRANCH_PREFERRED_STYLES:
                preferred = _L4_BRANCH_PREFERRED_STYLES[nudity_branch]
                if data is None:
                    data = self._load("clothing")
                all_cids = {c.get("id") for c in data.get("categories", []) if c.get("id")}
                if preferred & all_cids:
                    excluded.update(all_cids - preferred)
            elif nudity_level_code == "L3" and nudity_branch:
                if data is None:
                    data = self._load("clothing")
                cats = data.get("categories", [])
                all_cids = {c.get("id") for c in cats if c.get("id")}
                if nudity_branch in _L3_BRANCH_PREFERRED_STYLES:
                    preferred = _L3_BRANCH_PREFERRED_STYLES[nudity_branch]
                    if preferred & all_cids:
                        excluded.update(all_cids - preferred)
                elif nudity_branch == "l3_open_shirt_breasts":
                    excluded.update(all_cids - set(ALLOWED_BUTTON_STYLES))
                elif nudity_branch == "l3_topless_skirt_kept":
                    skirt_ids = {c.get("id") for c in cats if c.get("id") and self._is_clothing_skirt_compatible(c)}
                    if skirt_ids:
                        excluded.update(all_cids - skirt_ids)
                elif nudity_branch == "l3_topless_pants_kept":
                    pants_ids = {
                        c.get("id")
                        for c in cats
                        if c.get("id")
                        and any(
                            "bottom_pants" in ((t.get("facts") or {}).get("garment_topologies") or ())
                            for t in c.get("tags", [])
                            if isinstance(t, dict)
                        )
                    }
                    if pants_ids:
                        excluded.update(all_cids - pants_ids)
                elif nudity_branch == "l3_pulled_down_dress":
                    dress_ids = {
                        c.get("id")
                        for c in cats
                        if c.get("id")
                        and c.get("id") not in NON_SKIRT_ONE_PIECE
                        and any(
                            "one_piece" in ((t.get("facts") or {}).get("garment_topologies") or ())
                            for t in c.get("tags", [])
                            if isinstance(t, dict)
                        )
                    }
                    if dress_ids:
                        excluded.update(all_cids - dress_ids)
                elif nudity_branch == "l3_raised_top_breasts":
                    top_ids = {
                        c.get("id")
                        for c in cats
                        if c.get("id")
                        and any(
                            "top" in ((t.get("facts") or {}).get("garment_topologies") or ())
                            for t in c.get("tags", [])
                            if isinstance(t, dict)
                        )
                    }
                    if top_ids:
                        excluded.update(all_cids - top_ids)
            elif nudity_level_code == "L2" and nudity_branch:
                if data is None:
                    data = self._load("clothing")
                cats = data.get("categories", [])
                all_cids = {c.get("id") for c in cats if c.get("id")}
                if nudity_branch == "l2_backless":
                    backless_ids = {
                        "dress_backless", "evening_dress", "cocktail_dress", "halter_dress",
                        "strapless_dress", "party_club", "tulle_dress", "summer_sundress",
                        "dress_casual", "wedding_dress", "qipao",
                    } & all_cids
                    if backless_ids:
                        excluded.update(all_cids - backless_ids)
                elif nudity_branch == "l2_midriff_waist":
                    non_midriff_one_piece = {
                        c.get("id") for c in cats
                        if c.get("id")
                        and any((t.get("facts") or {}).get("piece_role") == "one_piece" for t in c.get("tags", []))
                        and all((t.get("facts") or {}).get("piece_role") in ("one_piece", "accessory", "legwear", "footwear", "unspecified", None) for t in c.get("tags", []))
                        and "bikini" not in c.get("id", "").lower()
                        and "crop" not in c.get("id", "").lower()
                        and not any(any(k in str(t.get("text", "")).lower() for k in ("midriff", "crop", "cutout", "two-piece")) for t in c.get("tags", []))
                    }
                    excluded.update(non_midriff_one_piece)
                elif nudity_branch == "l2_sleeve_wrist":
                    sleeveless_cids = {
                        "dress_backless", "sleeveless_dress", "strapless_dress", "halter_dress",
                        "off_shoulder_dress", "summer_sundress", "camisole_slip", "crop_top",
                        "strapless_top", "bikini_classic", "bikini_micro", "bikini_creative",
                        "bikini_strappy", "one_piece_swimsuit", "swimsuit_school", "bunny_suit",
                        "lingerie_bra", "lingerie_panties", "lingerie_lace",
                    } & all_cids
                    excluded.update(sleeveless_cids)

        return excluded


    @staticmethod
    def _get_affinity_ids(context: Optional[str], slot_key: str) -> List[str]:
        if not context:
            return []
        resolved_ctx = CONTEXT_PARENT_MAPPING.get(context, context)
        if resolved_ctx in CONTEXT_AFFINITY:
            return CONTEXT_AFFINITY[resolved_ctx].get(slot_key, [])
        return []

    @staticmethod
    def _pick(items: Sequence[Any], rng: Random, count: int = 1) -> List[Any]:
        if not items:
            return []
        return rng.sample(list(items), min(count, len(items)))

    @staticmethod
    def _pick_tags_respecting_mutex(
        raw_tags: Sequence[Any],
        rng: Random,
        item_id: str = "",
    ) -> List[Any]:
        if not raw_tags:
            return []
        is_palette = item_id.endswith("_palette")
        max_sels = 1 if is_palette else 2
        first_mutexes = (
            set(raw_tags[0].get("facts", {}).get("mutex_groups", []))
            if (isinstance(raw_tags[0], dict) and raw_tags[0].get("facts"))
            else set()
        )
        for t in raw_tags:
            if isinstance(t, dict):
                gov = t.get("facts", {}).get("governance_metadata", {})
                if "max_selections" in gov:
                    max_sels = min(max_sels, gov["max_selections"])
                m_set = set(t.get("facts", {}).get("mutex_groups", [])) if t.get("facts") else set()
                if not (first_mutexes & m_set):
                    first_mutexes = set()

        if max_sels <= 1 or first_mutexes or is_palette:
            return [rng.choice(raw_tags)]

        k = min(rng.randint(1, 2), len(raw_tags))
        picked = rng.sample(list(raw_tags), k)
        if len(picked) > 1 and isinstance(picked[0], dict) and isinstance(picked[1], dict):
            m1 = set(picked[0].get("facts", {}).get("mutex_groups", [])) if picked[0].get("facts") else set()
            m2 = set(picked[1].get("facts", {}).get("mutex_groups", [])) if picked[1].get("facts") else set()
            if m1 and (m1 & m2):
                return [picked[0]]
        return picked

    @staticmethod
    def _pick_one(items: Sequence[Any], rng: Random) -> Optional[Any]:
        if not items:
            return None
        return rng.choice(items)

    @classmethod
    def _has_variant_metadata(
        cls,
        tags: Sequence[SampledTag],
        chosen_style: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """检查标签序列或当前款式中是否包含变体角色、互斥组注解或独立单品款式。"""
        if chosen_style:
            c_id = chosen_style.get("id", "")
            if (
                c_id in STANDALONE_CLOTHING_CATEGORIES
                or c_id == "clothing_ensemble_combos"
                or chosen_style.get("is_ensemble")
                or chosen_style.get("is_standalone_pieces")
            ):
                return True
            for t in chosen_style.get("tags", []):
                if isinstance(t, dict):
                    if (
                        t.get("role") is not None
                        or bool(t.get("facts", {}).get("mutex_groups"))
                        or t.get("facts", {}).get("semantic_role") == "variant"
                    ):
                        return True
        return any(
            t.role is not None
            or bool(t.facts.mutex_groups)
            or (t.facts and t.facts.semantic_role == "variant")
            or (t.provenance and t.provenance.item_id in STANDALONE_CLOTHING_CATEGORIES)
            for t in tags
        )

    @classmethod
    def _select_clothing_style_tags(
        cls,
        chosen_style: Dict[str, Any],
        rng: Random,
        nudity_level_code: Optional[str] = None,
        clothing_state: Optional[str] = None,
        style_mode: Optional[str] = None,
    ) -> List[Any]:
        """从服装款式中按两步契约受控选择单品款式及相容构件：
        第一步：先选且仅选 1 个独立款式（主款式/版型/变体）；
        第二步：附加与所选独立款式相容的属性（combinable_attribute）及套装构件；
        杜绝在单品款式池中将多款独立长句同时输出或在有状态时串出两套独立大件。
        """
        all_tags = chosen_style.get("tags", [])
        if not all_tags:
            return []

        c_id = chosen_style.get("id", "")
        enforce_tag_level = (style_mode in (SelectionMode.RANDOM, SelectionMode.AUTO))

        # 1. 复合风格穿搭组合池严格单选 1 套完整穿搭 (杜绝拼接冲突长句与跨等级穿透)
        if c_id == "clothing_ensemble_combos" or chosen_style.get("is_ensemble"):
            filtered = list(all_tags)
            if nudity_level_code in ("L1", "L2", "L3", "L4"):
                filtered = [t for t in filtered if is_tag_eligible_for_nudity_level(t, nudity_level_code)]
            if clothing_state and any(w in clothing_state.lower() for w in ("lifted", "掀起裙摆", "lifted_up", "lifted_skirt")):
                skirt_filtered = [t for t in filtered if cls._tag_has_skirt_carrier(t, c_id)]
                if skirt_filtered:
                    filtered = skirt_filtered
            if not filtered:
                return []
            return [rng.choice(filtered)]

        # 2. 独立单品款式池（例如 16 个新增独立单品大类）：各标签均为独立完整款式，严格单选 1 件
        if c_id in STANDALONE_CLOTHING_CATEGORIES or chosen_style.get("is_standalone_pieces"):
            filtered_standalone = list(all_tags)
            if enforce_tag_level and nudity_level_code in ("L1", "L2", "L3", "L4"):
                filtered_standalone = [t for t in filtered_standalone if is_tag_eligible_for_nudity_level(t, nudity_level_code)]
            if not filtered_standalone:
                return []
            return [rng.choice(filtered_standalone)]

        # 3. 存量扩增分类（包含 Batch 6 新增变体 tags 的分类）：
        # 例如 qipao, kimono, yukata, dress_casual, summer_sundress 等 35 个大类。
        # 需在款式池中按独立单品先单选 1 个版型，再附加相容属性。
        has_variant_srole = any(
            isinstance(t, dict) and t.get("facts", {}).get("semantic_role") == "variant"
            for t in all_tags
        )
        if has_variant_srole:
            silhouette_candidates: List[Any] = []
            attribute_candidates: List[Any] = []

            for t in all_tags:
                if enforce_tag_level and nudity_level_code in ("L1", "L2", "L3", "L4") and not is_tag_eligible_for_nudity_level(t, nudity_level_code):
                    continue
                if isinstance(t, dict):
                    role = t.get("role")

                    if role == "combinable_attribute":
                        attribute_candidates.append(t)
                    else:
                        silhouette_candidates.append(t)
                else:
                    silhouette_candidates.append(t)

            if clothing_state and any(w in clothing_state.lower() for w in ("lifted", "掀起裙摆", "lifted_up", "lifted_skirt")):
                skirt_silhouettes = [t for t in silhouette_candidates if cls._tag_has_skirt_carrier(t, c_id)]
                if skirt_silhouettes:
                    silhouette_candidates = skirt_silhouettes

            if silhouette_candidates:
                chosen_silhouette = rng.choice(silhouette_candidates)
                result = [chosen_silhouette]

                # 第二步：附加与所选独立款式相容的属性
                if attribute_candidates:
                    chosen_facts = chosen_silhouette.get("facts", {}) if isinstance(chosen_silhouette, dict) else {}
                    forbidden_mgs = set(chosen_facts.get("mutex_groups", [])) if isinstance(chosen_facts, dict) else set()
                    compatible_attrs = [
                        t for t in attribute_candidates
                        if not (set(t.get("facts", {}).get("mutex_groups", [])) & forbidden_mgs)
                    ]
                    if compatible_attrs:
                        chosen_attr = rng.choice(compatible_attrs)
                        result.append(chosen_attr)

                return result
            if enforce_tag_level and nudity_level_code in ("L1", "L2", "L3", "L4"):
                return []

        # 4. 其他分类（M3.2 既有款式与套装构件，如 denim_shorts, hanfu, jk_seifuku, maid_dress 等）：
        # 全量传入 _to_sampled_tags 以保全各标签的 dynamic_slots 统计学抽样，随后由受控基线采样器及消解器处理。
        if enforce_tag_level and nudity_level_code in ("L1", "L2", "L3", "L4"):
            return [t for t in all_tags if is_tag_eligible_for_nudity_level(t, nudity_level_code)]
        return list(all_tags)

    @classmethod
    def _sample_controlled_base_clothing_tags(
        cls,
        tags: Sequence[SampledTag],
        rng: Random,
    ) -> Tuple[SampledTag, ...]:
        """对具备变体角色/互斥组的款式执行受控独立采样。

        核心约束：
        1. 变体互斥保护：同一互斥组（如 silhouette 组）有且仅抽取 1 个；
        2. 版型优先保底：优先从 role in ('core_base', 'variant') 或 facts.semantic_role == 'variant' 中抽取 1 个主款版型；
        3. 可组合属性：从 role == 'combinable_attribute' 且与所选版型不冲突的属性中至多抽取 1 个；
        4. 防空与可达性：绝不返回空；各变体均有均等机会被抽样。
        """
        if not tags:
            return ()

        silhouette_candidates = [
            t for t in tags
            if t.role in ("core_base", "variant")
            or (t.facts and t.facts.semantic_role == "variant")
        ]
        attribute_candidates = [
            t for t in tags
            if t.role == "combinable_attribute"
        ]

        if not silhouette_candidates and not attribute_candidates:
            primary_mg = next((t.facts.mutex_groups[0] for t in tags if t.facts.mutex_groups), None)
            if primary_mg:
                silhouette_candidates = [t for t in tags if primary_mg in t.facts.mutex_groups]
                attribute_candidates = [t for t in tags if primary_mg not in t.facts.mutex_groups]
            else:
                picked = cls._pick_one(tags, rng)
                return (picked,) if picked is not None else ()

        result: List[SampledTag] = []
        chosen_silhouette: Optional[SampledTag] = None

        if silhouette_candidates:
            chosen_silhouette = cls._pick_one(silhouette_candidates, rng)
            if chosen_silhouette is not None:
                result.append(chosen_silhouette)

        if attribute_candidates:
            forbidden_mgs = set(chosen_silhouette.facts.mutex_groups) if chosen_silhouette else set()
            compatible_attrs = [
                t for t in attribute_candidates
                if not set(t.facts.mutex_groups).intersection(forbidden_mgs)
            ]
            if compatible_attrs:
                chosen_attr = cls._pick_one(compatible_attrs, rng)
                if chosen_attr is not None:
                    result.append(chosen_attr)

        if not result and tags:
            fallback = cls._pick_one(tags, rng)
            if fallback is not None:
                result.append(fallback)

        # 保留动态槽位与必选构件生成的附加组件 (如次级腿饰、鞋靴等)
        for t in tags:
            if t.role in ("dynamic_slot_component", "mandatory_component"):
                result.append(t)

        return tuple(result)

    @classmethod
    def _pick_state_tags(
        cls,
        raw_state_tags: List[Any],
        state_id: str,
        chosen_style: Dict[str, Any],
        has_variant_metadata: bool,
        rng: Random,
    ) -> List[Any]:
        """受控状态标签采样：
        1. 存量 31 款 (not has_variant_metadata)：严格保持向后兼容性与随机数调用序列保真，
           若 state_id == 'unbuttoned'，仅从前 3 个存量标签中采样；其余状态使用全量 tags。
        2. 新增款式 (has_variant_metadata)：按款式的服装形制拓扑 (garment_topologies)
           从状态候选标签中过滤出相符拓扑的标签（例如短裤/牛仔裤解扣仅取下装解扣标签，半身裙掀裙仅取裙装标签且不取连衣裙标签）。
           若无匹配项，则回退全量状态标签候选池交由 ConflictResolver 消解。
        """
        if not has_variant_metadata:
            if state_id == "unbuttoned":
                legacy_tags = [
                    t for t in raw_state_tags
                    if isinstance(t, dict) and t.get("id") in ("unbuttoned__tag_000", "unbuttoned__tag_001", "unbuttoned__tag_002")
                ]
                if not legacy_tags:
                    legacy_tags = raw_state_tags[:3]
                return cls._pick(legacy_tags, rng, min(2, len(legacy_tags)))
            return cls._pick(raw_state_tags, rng, min(2, len(raw_state_tags)))

        # 新增款式：提取当前款式的形制拓扑
        style_topos: Set[str] = set()
        for tag_item in chosen_style.get("tags", []) if isinstance(chosen_style, dict) else []:
            facts = tag_item.get("facts") if isinstance(tag_item, dict) else None
            if isinstance(facts, dict) and facts.get("garment_topologies"):
                style_topos.update(facts["garment_topologies"])

        if state_id in ("unbuttoned", "opened"):
            # 解扣状态：按服装实体具体动作形制兼容性 (is_garment_compatible_with_state) 精确筛选候选，
            # 杜绝由于细化形制（如长袍、赛车服、西服专属解扣）不兼容导致整组动作被消解器全部剔除
            style_id = chosen_style.get("id", "") if isinstance(chosen_style, dict) else ""
            member_atoms = []
            for tag_item in chosen_style.get("tags", []) if isinstance(chosen_style, dict) else []:
                if isinstance(tag_item, dict):
                    facts = tag_item.get("facts")
                    sem_facts = (
                        SemanticFacts(
                            garment_topologies=tuple(facts.get("garment_topologies", ())),
                        )
                        if isinstance(facts, dict)
                        else SemanticFacts()
                    )
                    member_atoms.append(
                        PromptAtom(
                            text=tag_item.get("text", ""),
                            span_type=SpanType.PLAIN,
                            source_slot="clothing",
                            source_item_id=style_id,
                            facts=sem_facts,
                            id=tag_item.get("id", ""),
                        )
                    )
                elif isinstance(tag_item, str):
                    member_atoms.append(
                        PromptAtom(
                            text=tag_item,
                            span_type=SpanType.PLAIN,
                            source_slot="clothing",
                            source_item_id=style_id,
                            id=tag_item,
                        )
                    )

            carrier = GarmentCarrierEntity(
                entity_id=f"garment:clothing:{style_id}",
                selector="clothing",
                selected_id=style_id,
                member_atoms=member_atoms,
                is_worn=True,
                is_ambient=False,
            )

            matching_tags = []
            for st in raw_state_tags:
                if isinstance(st, dict):
                    st_facts = st.get("facts")
                    st_atom = PromptAtom(
                        text=st.get("text", ""),
                        span_type=SpanType.PLAIN,
                        source_slot="clothing_state",
                        source_item_id=state_id,
                        facts=(
                            SemanticFacts(
                                garment_topologies=tuple(st_facts.get("garment_topologies", ())),
                            )
                            if isinstance(st_facts, dict)
                            else SemanticFacts()
                        ),
                        id=st.get("id", ""),
                    )
                    if is_garment_compatible_with_state(carrier, state_id, st_atom):
                        matching_tags.append(st)

            if matching_tags:
                pool = matching_tags
            else:
                # 负例款式（未授权解扣的款式被指定了 unbuttoned，如泳衣、铠甲）：
                # 回退到拓扑相交候选或全量候选，交由 ConflictResolver 消解并记录冲突
                topo_matching = [
                    st for st in raw_state_tags
                    if isinstance(st, dict) and (set(st.get("facts", {}).get("garment_topologies", [])) & style_topos)
                ]
                pool = topo_matching if topo_matching else raw_state_tags
            return cls._pick(pool, rng, min(2, len(pool)))

        # 其余状态：按形制拓扑相交筛选候选
        matching_tags = []
        for st in raw_state_tags:
            if isinstance(st, dict):
                st_facts = st.get("facts", {})
                st_topos = set(st_facts.get("garment_topologies", []))
                if st_topos and (st_topos & style_topos):
                    matching_tags.append(st)

        pool = matching_tags if matching_tags else raw_state_tags
        return cls._pick(pool, rng, min(2, len(pool)))

    @staticmethod
    def _flatten_tags(item: Any) -> List[str]:
        if isinstance(item, str):
            return [item.strip()] if item.strip() else []
        if isinstance(item, dict):
            tags = item.get("tags", [])
            if isinstance(tags, list):
                result = []
                for t in tags:
                    if isinstance(t, str) and t.strip():
                        result.append(t.strip())
                    elif isinstance(t, dict):
                        text = t.get("text", "")
                        if isinstance(text, str) and text.strip():
                            result.append(text.strip())
                return result
            if isinstance(tags, str):
                return [t.strip() for t in tags.split(",") if t.strip()]
            # 兼容只有 anchor_tags 或 detail_tags 的情况
            combined = item.get("anchor_tags", []) + item.get("detail_tags", [])
            if combined:
                res = []
                for t in combined:
                    if isinstance(t, str) and t.strip():
                        res.append(t.strip())
                    elif isinstance(t, dict):
                        text = t.get("text", "")
                        if isinstance(text, str) and text.strip():
                            res.append(text.strip())
                return res
        return []

    # ─── 情境推断核心 ───

    def detect_context(self, scene_name: str, theme_name: str) -> str:
        """根据当前场景与主题关键词，推断最匹配的核心情境（支持全部 14 大情境）。"""
        text = f"{scene_name} {theme_name}".lower()

        # 1. SM / 调教 / 拘束（优先于常规词判定）
        if any(k in text for k in ["调教", "束缚", "绳缚", "紧缚", "地牢", "惩罚", "拘束", "手铐", "项圈", "皮衣", "乳胶", "sm"]) or re.search(r"\b(sm|bondage|shibari|kinbaku|dungeon|collar|handcuffs|latex|restrained|dominant|submissive)\b", text):
            return "bondage_sm"
        # 2. 校园 / 学生
        if any(k in text for k in ["校", "教室", "课堂", "课桌", "黑板", "学园", "学院", "体育馆", "操场", "走廊", "初恋", "制服", "补习", "学生", "少女"]) or re.search(r"\b(school|classroom|student|teacher|uniform|jk|seifuku|blackboard|gymnasium)\b", text):
            return "school"
        # 3. 职场 / 办公室
        if any(k in text for k in ["办公", "职场", "会议", "经理", "秘书", "加班", "公司", "商务", "西装", "包臀裙", "白领", "总裁"]) or re.search(r"\b(office|business|corporate|meeting|secretary|executive|cubicle|ol)\b", text):
            return "office"
        # 4. 医疗 / 诊所
        if any(k in text for k in ["医", "诊", "护士", "病房", "手术", "药", "体检", "针筒", "病床", "白大褂"]) or re.search(r"\b(hospital|clinic|nurse|medical|doctor|examination|ward|patient)\b", text):
            return "medical"
        # 5. 温泉 / 浴室
        if any(k in text for k in ["温泉", "风吕", "桑拿", "水疗", "浴缸", "澡堂", "泡汤", "浴室", "淋浴"]) or re.search(r"\b(onsen|bath|shower|rotenburo|sento|sauna|soapland|jacuzzi|bathtub)\b", text):
            return "onsen_bath"
        # 6. 和风 / 传统
        if any(k in text for k in ["和室", "茶室", "庭院", "古风", "和服", "旗袍", "汉服", "祭典", "榻榻米", "国风", "神社", "鸟居", "振袖"]) or re.search(r"\b(kimono|yukata|qipao|hanfu|tatami|shrine|temple|washitsu|ryokan)\b", text):
            return "traditional"
        # 7. 电车 / 通勤
        if any(k in text for k in ["电车", "地铁", "车厢", "列车", "公交", "新干线", "站台", "通勤", "巴士", "车座"]) or re.search(r"\b(transit|subway|train|bus|shinkansen|commuter|carriage|cabin)\b", text):
            return "transit"
        # 8. 户外 / 自然 / 海滩
        if any(k in text for k in ["海滩", "沙滩", "泳池", "比基尼", "公园", "森林", "树林", "草丛", "河堤", "户外", "露天", "野外", "山路"]) or re.search(r"\b(outdoor|beach|pool|forest|riverbank|park|mountain|nature|trail)\b", text):
            return "outdoor"
        # 9. 餐饮 / 咖啡厅 / 居酒屋
        if any(k in text for k in ["咖啡", "下午茶", "餐厅", "居酒屋", "屋台", "拉面", "茶屋", "甜品", "蛋糕", "餐馆", "女仆咖啡"]) or re.search(r"\b(dining|cafe|restaurant|izakaya|yatai|tea room|bistro)\b", text):
            return "dining"
        # 10. 夜店 / 酒吧 / 歌舞伎町
        if any(k in text for k in ["夜店", "酒吧", "歌舞伎町", "包厢", "派对", "微醺", "醉", "兔女郎", "夜总会", "陪酒", "夜市", "俱乐部"]) or re.search(r"\b(nightclub|club|bar|cabaret|hostess|karaoke|pub|drunk|party)\b", text):
            return "nightlife"
        # 11. 居家 / 卧室 / 人妻
        if any(k in text for k in ["家", "卧", "客", "厨", "公寓", "人妻", "少妇", "同居", "阳台", "睡衣", "围裙", "居家", "被窝", "床上", "沙发"]) or re.search(r"\b(bedroom|living room|kitchen|apartment|home|housewife|bed|futon|sofa)\b", text):
            return "domestic"
        # 12. 风俗 / 成人私密影棚
        if any(k in text for k in ["风俗", "泡泡浴", "摄影棚", "私密影棚", "试衣间", "情人旅馆", "成人"]) or re.search(r"\b(adult|soapland|love hotel|photo studio|erotic studio|dressing room)\b", text):
            return "adult"
        # 13. 特殊密室 / 废墟
        if any(k in text for k in ["密室", "废墟", "实验室", "地下室", "暗黑", "遗迹", "高科技"]) or re.search(r"\b(special|ruins|laboratory|secret room|dark room|dungeon|sci-fi)\b", text):
            return "special"

        return "generic"

    @staticmethod
    def _to_sampled_tags(
        items: Sequence[Any],
        parent_id: str,
        kind: str,
        semantic_ids: Tuple[str, ...] = (),
        parent_ids: Tuple[str, ...] = (),
        extra_facts: Optional[Dict[str, Any]] = None,
        rng: Optional[Random] = None,
    ) -> Tuple[Tuple[str, ...], Tuple[SampledTag, ...]]:
        tags_str: List[str] = []
        sampled_tags: List[SampledTag] = []
        for idx, item in enumerate(items):
            if isinstance(item, SampledTag):
                tags_str.append(item.text)
                sampled_tags.append(item)
                continue
            if isinstance(item, dict):
                t_text = item.get("text", "")
                t_id = item.get("id", f"{parent_id}__tag_{idx:03d}")
                t_raw_lines = tuple(item.get("raw_lines", ()))
                f_dict = dict(item.get("facts", {}))
                if extra_facts:
                    f_dict.update(extra_facts)
                t_role = item.get("role") or (f_dict.get("semantic_role") if f_dict.get("semantic_role") in ("variant", "core_base", "combinable_attribute") else None)
                facts = SemanticFacts.from_dict(f_dict)
            else:
                t_text = str(item).strip()
                t_id = f"{parent_id}__tag_{idx:03d}"
                t_role = None
                t_raw_lines = ()
                f_dict = dict(extra_facts or {})
                facts = SemanticFacts.from_dict(f_dict)
            if facts.semantic_role is None:
                facts = SemanticFacts.from_dict({**f_dict, "semantic_role": "selector"})
            gov = facts.governance_metadata or {}
            mand_comps = gov.get("mandatory_components", [])
            if not t_text and not gov.get("is_pool_selector") and not mand_comps:
                continue

            p_ids = parent_ids or ((parent_id,) if parent_id else ())
            prov = TagProvenance(
                item_id=parent_id if parent_id else t_id,
                kind=kind,
                parent_ids=p_ids,
                semantic_ids=tuple(semantic_ids or ((f"{kind}:{parent_id}",) if parent_id else ())),
            )

            # 1. 必选构件联合展开 (例如 combo_necklace_choker)
            if mand_comps:
                for c_idx, comp in enumerate(mand_comps):
                    c_pool = comp.get("pool_reference", "")
                    c_id = comp.get("tag_id", f"{t_id}__comp_{c_idx}")
                    c_text = resolve_pool_reference(c_pool, rng) if (rng and c_pool) else comp.get("component_id", "")
                    if c_text and not is_container_description(c_text):
                        tags_str.append(c_text)
                        c_prov = TagProvenance(
                            item_id=comp.get("component_id", parent_id),
                            kind=kind,
                            parent_ids=p_ids,
                            semantic_ids=tuple(semantic_ids or ((f"{kind}:{parent_id}",) if parent_id else ())),
                        )
                        sampled_tags.append(
                            SampledTag(
                                text=c_text,
                                provenance=c_prov,
                                id=c_id,
                                facts=facts,
                                role="mandatory_component",
                                raw_lines=(),
                            )
                        )

            # 2. 品类词池选择器展开 (is_pool_selector)
            if gov.get("is_pool_selector") and rng:
                pool_ref = gov.get("pool_reference", "")
                if pool_ref:
                    t_text = resolve_pool_reference(pool_ref, rng)

            # 3. 动态槽位加权判定与构件展开 (dynamic_slots)
            dyn_slots = []
            if facts.accessory_facts and getattr(facts.accessory_facts, "dynamic_slots", None):
                dyn_slots = facts.accessory_facts.dynamic_slots
            elif facts.clothing_facts and getattr(facts.clothing_facts, "dynamic_slots", None):
                dyn_slots = facts.clothing_facts.dynamic_slots
            elif "dynamic_slots" in gov:
                dyn_slots = gov["dynamic_slots"]
            elif "dynamic_slots" in f_dict:
                dyn_slots = f_dict["dynamic_slots"]

            if dyn_slots and rng:
                mod_base, additional_tags = resolve_dynamic_slots(dyn_slots, rng, base_text=t_text)
                if not is_container_description(t_text, f_dict, t_role):
                    t_text = mod_base
                for a_idx, a_text in enumerate(additional_tags):
                    if a_text and not is_container_description(a_text):
                        tags_str.append(a_text)
                        a_prov = TagProvenance(
                            item_id=parent_id if parent_id else t_id,
                            kind=kind,
                            parent_ids=p_ids,
                            semantic_ids=tuple(semantic_ids or ((f"{kind}:{parent_id}",) if parent_id else ())),
                        )
                        a_facts = SemanticFacts.from_dict({**f_dict, "mutex_groups": ()})
                        sampled_tags.append(
                            SampledTag(
                                text=a_text,
                                provenance=a_prov,
                                id=f"{t_id}__slot_{a_idx}",
                                facts=a_facts,
                                role="dynamic_slot_component",
                                raw_lines=(),
                            )
                        )

            # 4. 组合描述容器拦截：纯容器描述严禁输出至最终提示词
            if is_container_description(t_text, f_dict, t_role):
                continue

            if not t_text:
                continue

            tags_str.append(t_text)
            sampled_tags.append(
                SampledTag(
                    text=t_text,
                    provenance=prov,
                    id=t_id,
                    facts=facts,
                    role=t_role,
                    raw_lines=t_raw_lines,
                )
            )
        return tuple(tags_str), tuple(sampled_tags)

    # ─── 槽位 1: 场景 + 主题 ───

    def sample_scene_result(self, category: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(category):
            return None
        self._ensure_scenes_indexed()
        if not self._all_scene_items:
            return None

        target_tag = None
        if _is_random(category):
            pool = self._all_scene_items
            chosen = self._pick_one(pool, rng)
            if not chosen:
                return None
            self._record_pool_sampling_rationale(
                chosen,
                pool,
                mode="uniform_random",
                active_contexts=chosen.get("context_ids", ()),
            )
        elif category in getattr(self, "_scene_groups_by_category", {}):
            pool = self._scene_groups_by_category[category]
            active_pool = pool if pool else self._all_scene_items
            chosen = self._pick_one(active_pool, rng)
            if not chosen:
                return None
            self._record_pool_sampling_rationale(
                chosen,
                active_pool,
                mode="uniform_random",
                active_contexts=chosen.get("context_ids", ()),
            )
        else:
            # EXPLICIT: exact match by label, id, or alias (zero substring collision)
            chosen = self._scene_by_label.get(category) or self._scene_by_id.get(category) or self._scene_by_alias.get(category)
            if not chosen:
                tag_match = self._match_scene_tag(category)
                if tag_match:
                    chosen, target_tag = tag_match
                else:
                    raise DataSelectionError(f"Unknown scene category: {category!r}")

        if target_tag:
            sampled_raw = [target_tag]
        elif chosen.get("id") == "prose_scene_templates" or chosen.get("is_prose_template"):
            # 自然语言整景叙述模板严格单选 1 条
            anchors = chosen.get("anchor_tags", []) or chosen.get("tags", [])
            anchor = rng.choice(anchors) if anchors else "room"
            sampled_raw = [anchor]
        else:
            anchors = chosen.get("anchor_tags", [])
            details = chosen.get("detail_tags", [])

            if not anchors:
                anchors = chosen.get("tags", ["room"])

            anchor = rng.choice(anchors) if anchors else "room"
            sampled_raw = [anchor]

            if details:
                detail_count = min(rng.randint(1, 2), len(details))
                sampled_raw.extend(rng.sample(details, detail_count))

        c_id = chosen.get("id", "scene_unknown")
        ctx_ids = tuple(chosen.get("context_ids", ("generic",)))
        prov = TagProvenance(
            item_id=c_id,
            semantic_ids=tuple([f"scene:{c_id}"] + [f"context:{c}" for c in ctx_ids]),
            kind="scene"
        )
        sampled_tags: List[SampledTag] = []
        tags_str: List[str] = []
        for idx, item_tag in enumerate(sampled_raw):
            is_anchor = (idx == 0)
            if isinstance(item_tag, dict):
                t_text = item_tag.get("text", "")
                t_id = item_tag.get("id", f"{c_id}__tag_{idx:03d}")
                t_facts = SemanticFacts.from_dict(item_tag.get("facts", {}))
            else:
                t_text = str(item_tag).strip()
                t_id = f"{c_id}__tag_{idx:03d}"
                t_facts = SemanticFacts(
                    semantic_role="scene_anchor" if is_anchor else "scene_detail",
                    space_kind="outdoor" if chosen.get("exclusive_group") in ("beach", "outdoor") else "indoor",
                    venue_ids=(chosen.get("exclusive_group") or c_id,) if chosen.get("exclusive_group") else (c_id,),
                )
            tags_str.append(t_text)
            sampled_tags.append(
                SampledTag(
                    text=t_text,
                    provenance=TagProvenance(
                        item_id=c_id if c_id else t_id,
                        kind="scene",
                        parent_ids=(c_id,),
                        semantic_ids=(f"scene:{c_id}",)
                    ),
                    id=t_id,
                    facts=t_facts,
                )
            )

        return SampleResult(
            tags=tuple(tags_str),
            item_id=c_id,
            context_ids=ctx_ids,
            exclusive_group=chosen.get("exclusive_group"),
            provenance=prov,
            sampled_tags=tuple(sampled_tags),
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(category) else "explicit", active_contexts=ctx_ids),
        )

    def sample_scene(self, category: str, rng: Random) -> List[str]:
        if _is_none(category):
            return []
        res = self.sample_scene_result(category, rng)
        return list(res.tags) if res else []

    def sample_theme_result(
        self,
        theme: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[ThemeSampleResult]:
        if _is_none(theme):
            return None
        data = self._load("themes")
        themes = data.get("themes", [])
        if not themes:
            return None

        is_rand = _is_random(theme) or _is_auto(theme)
        if is_rand:
            excluded_ids = self.get_excluded_candidate_ids("theme", nudity_level_code=nudity_level_code)
            pool = [t for t in themes if t.get("id") not in excluded_ids] if excluded_ids else list(themes)
            if not pool:
                pool = list(themes)
            t = self._pick_one(pool, rng)
            self._record_pool_sampling_rationale(
                t,
                pool,
                mode="uniform_random",
                active_contexts=t.get("context_ids", ()) if t else (),
                excluded_ids=excluded_ids,
            )
        else:
            idx = self._get_catalog_index("themes", themes)
            t = idx.get(theme)
            if not t:
                raise DataSelectionError(f"Unknown theme: {theme!r}")

        t_id = t.get("id", "theme_unknown")
        raw_tags = list(t.get("tags", []))
        if is_rand and nudity_level_code in ("L1", "L2"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(tg, nudity_level_code, slot_name="theme", item_id=t_id)
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        sampled_raw = rng.sample(raw_tags, min(rng.randint(2, 3), len(raw_tags))) if raw_tags else []
        prov = TagProvenance(
            item_id=t_id,
            semantic_ids=(f"theme:{t_id}",),
            kind="theme"
        )
        ctx_ids = tuple(t.get("context_ids", ()))
        sampled_tags_list: List[SampledTag] = []
        for idx, tag in enumerate(sampled_raw):
            if isinstance(tag, dict):
                text = tag.get("text", "")
                tag_id = tag.get("id", f"{t_id}__tag_{idx:03d}")
                tag_facts = SemanticFacts.from_dict(tag.get("facts", {}))
            else:
                text = str(tag).strip()
                tag_id = f"{t_id}__tag_{idx:03d}"
                tag_facts = SemanticFacts(semantic_role="selector")
            sampled_tags_list.append(
                SampledTag(
                    text=text,
                    provenance=TagProvenance(
                        item_id=t_id,
                        kind="theme",
                        parent_ids=(t_id,),
                        semantic_ids=(f"theme:{t_id}",),
                    ),
                    id=tag_id,
                    facts=tag_facts,
                )
            )
        return ThemeSampleResult(
            tags=tuple(sampled_tags_list),
            theme_id=t_id,
            provenance=prov,
            context_ids=ctx_ids,
            sampling_rationale=self._pop_sampling_rationale(t_id, "random" if is_rand else "explicit", active_contexts=ctx_ids),
        )

    def sample_theme(
        self,
        theme: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        if _is_none(theme):
            return []
        res = self.sample_theme_result(theme, rng, nudity_level_code=nudity_level_code)
        return list(res.all_text_tags) if res else []

    # ─── 槽位 2: 景别 + 视角 + 画质/设备 ───

    def sample_shot_type_result(
        self,
        shot_type: str,
        rng: Random,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
        nudity_branch: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(shot_type):
            return None
        data = self._load("shot_types")
        shots = data.get("shot_types", [])
        if not shots:
            return None

        target_tag = None
        is_rand_or_auto = _is_random(shot_type) or _is_auto(shot_type)
        if is_rand_or_auto:
            excluded_ids = self.get_excluded_candidate_ids(
                "shot_type",
                nudity_level_code=nudity_level_code,
                nudity_branch=nudity_branch,
            )
            chosen = self._sample_from_candidates(
                "shot_type",
                shots,
                rng,
                profile=context_profile,
                excluded_ids=excluded_ids,
            )
            if not chosen:
                return None
        else:
            chosen = _match_item(shots, shot_type, "shot_types")
            if not chosen:
                tag_match = _match_tag(shots, shot_type)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown shot type: {shot_type!r}")

        raw_tags = list(chosen.get("tags", []))
        item_id = chosen.get("id", "shot_type_custom")
        if is_rand_or_auto and nudity_level_code in ("L1", "L2", "L6"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(
                    tg,
                    nudity_level_code,
                    slot_name="shot_type",
                    item_id=item_id,
                    nudity_branch=nudity_branch,
                )
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        if target_tag:
            selected_raw = [target_tag]
        else:
            selected_raw = raw_tags[:2] if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected_raw, item_id, "shot_type")
        prov = TagProvenance(
            item_id=item_id,
            kind="shot_type",
            semantic_ids=(f"shot:{item_id}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=item_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(item_id, "random" if is_rand_or_auto else "explicit"),
        )

    def sample_shot_type(
        self,
        shot_type: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
        nudity_branch: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_shot_type_result(
            shot_type,
            rng,
            nudity_level_code=nudity_level_code,
            nudity_branch=nudity_branch,
        )
        return list(res.tags) if res else []

    def sample_camera_angle_result(
        self,
        angle: str,
        rng: Random,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
        shot_type_id: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(angle):
            return None
        data = self._load("shot_types")
        angles = data.get("camera_angles", [])
        if not angles:
            return None

        excluded_ids = self.get_excluded_candidate_ids(
            "camera_angle",
            nudity_level_code=nudity_level_code,
            shot_type_id=shot_type_id,
        )

        target_tag = None
        is_rand_or_auto = _is_random(angle) or _is_auto(angle)
        if is_rand_or_auto:
            chosen = self._sample_from_candidates(
                "camera_angle",
                angles,
                rng,
                profile=context_profile,
                excluded_ids=excluded_ids,
            )
            if not chosen:
                return None
        else:
            chosen = _match_item(angles, angle, "camera_angles")
            if not chosen:
                tag_match = _match_tag(angles, angle)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown camera angle: {angle!r}")

        raw_tags = list(chosen.get("tags", []))
        item_id = chosen.get("id", "camera_angle_custom")
        if is_rand_or_auto and nudity_level_code in ("L1", "L2", "L6"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(tg, nudity_level_code, slot_name="camera_angle", item_id=item_id)
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        if target_tag:
            selected_raw = [target_tag]
        else:
            selected_raw = self._pick(raw_tags, rng, 1) if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected_raw, item_id, "camera_angle")
        prov = TagProvenance(
            item_id=item_id,
            kind="camera_angle",
            semantic_ids=(f"angle:{item_id}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=item_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(item_id, "random" if _is_random(angle) or _is_auto(angle) else "explicit"),
        )

    def sample_camera_angle(
        self,
        angle: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
        shot_type_id: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_camera_angle_result(
            angle,
            rng,
            nudity_level_code=nudity_level_code,
            shot_type_id=shot_type_id,
        )
        return list(res.tags) if res else []

    def sample_quality_result(self, quality_tier: str) -> Optional[SampleResult]:
        if _is_none(quality_tier):
            return None
        q_norm = str(quality_tier or "").strip().casefold()
        quality_map = {
            "高清写真 (high)": ("quality_high", ["best quality", "detailed", "photorealistic"]),
            "high": ("quality_high", ["best quality", "detailed", "photorealistic"]),
            "顶尖艺术 (masterpiece)": ("quality_masterpiece", ["masterpiece", "best quality", "ultra detailed", "8k", "photorealistic"]),
            "masterpiece": ("quality_masterpiece", ["masterpiece", "best quality", "ultra detailed", "8k", "photorealistic"]),
            "手机自拍 (phone camera)": ("quality_phone", ["phone camera", "selfie", "amateur photo", "slightly blurry"]),
            "phone camera": ("quality_phone", ["phone camera", "selfie", "amateur photo", "slightly blurry"]),
            "phone": ("quality_phone", ["phone camera", "selfie", "amateur photo", "slightly blurry"]),
            "监控画质 (cctv footage)": ("quality_cctv", ["CCTV footage", "security camera", "low resolution", "grainy"]),
            "cctv footage": ("quality_cctv", ["CCTV footage", "security camera", "low resolution", "grainy"]),
            "cctv": ("quality_cctv", ["CCTV footage", "security camera", "low resolution", "grainy"]),
            "标准画质 (standard)": ("quality_standard", ["good quality", "detailed"]),
            "standard": ("quality_standard", ["good quality", "detailed"]),
        }
        if q_norm not in quality_map:
            raise DataSelectionError(f"Unknown quality level: {quality_tier!r}")
        item_id, tags_list = quality_map[q_norm]
        q_class = "high"
        c_dev = "professional"
        if "masterpiece" in item_id:
            q_class = "masterpiece"
        elif "phone" in item_id:
            q_class = "phone"
            c_dev = "phone"
        elif "cctv" in item_id:
            q_class = "cctv"
            c_dev = "cctv"
        elif "standard" in item_id:
            q_class = "standard"
            c_dev = "neutral"

        fcts = {"semantic_role": "quality", "quality_class": q_class, "capture_device": c_dev}
        tags_str, sampled = self._to_sampled_tags(tags_list, item_id, "quality", extra_facts=fcts)
        prov = TagProvenance(
            item_id=item_id,
            kind="quality",
            semantic_ids=(f"quality:{item_id}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=item_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(item_id, "explicit"),
        )

    def sample_quality_tags(self, quality_tier: str) -> List[str]:
        res = self.sample_quality_result(quality_tier)
        return list(res.tags) if res else []

    # ─── 槽位 3: 裸露状态 ───

    def _compatible_branches_for_explicit_style(
        self,
        lvl_code: str,
        explicit_clothing: Optional[str],
        clothing_state: Optional[str] = None,
    ) -> Optional[Tuple[str, ...]]:
        """根据显式服装款式与状态，返回目标裸露等级下与之语义/形制相容的分支白名单。"""
        if lvl_code not in ("L2", "L3"):
            return None

        cands: List[str] = list(NUDITY_BRANCHES_BY_LEVEL.get(lvl_code, ()))
        if not cands:
            return None

        st_id = ""
        if clothing_state and get_selection_mode(clothing_state) == SelectionMode.EXPLICIT:
            states = self._load("clothing").get("clothing_states", [])
            st_item = _match_item(states, clothing_state, "clothing_states")
            st_id = st_item.get("id", "") if isinstance(st_item, dict) else ""

        if explicit_clothing and get_selection_mode(explicit_clothing) == SelectionMode.EXPLICIT:
            data = self._load("clothing")
            cats = data.get("categories", [])
            chosen_style = _match_item(cats, explicit_clothing)
            if not chosen_style:
                tm = _match_tag(cats, explicit_clothing)
                if tm:
                    chosen_style = tm[0]
            if chosen_style:
                cid = str(chosen_style.get("id", "") or "").lower()
                tags = chosen_style.get("tags", [])
                roles: Set[str] = set()
                topos: Set[str] = set()
                covered: Set[str] = set()
                txts: List[str] = []
                for t in tags:
                    if isinstance(t, dict):
                        f = t.get("facts") or {}
                        if f.get("piece_role"):
                            roles.add(str(f["piece_role"]))
                        for g in f.get("garment_topologies") or ():
                            topos.add(str(g))
                        for cv in f.get("covered_regions") or ():
                            covered.add(str(cv))
                        if t.get("text"):
                            txts.append(str(t["text"]).lower())
                joined = " ".join(txts)

                is_bra_only = bool(
                    "underwear" in roles
                    and "intimate_upper" in covered
                    and "intimate_lower" not in covered
                    and not (topos & {"top", "bottom_skirt", "bottom_pants", "one_piece", "ensemble_outfit", "outerwear"})
                )
                is_panties_only = bool(
                    "underwear" in roles
                    and "intimate_lower" in covered
                    and "intimate_upper" not in covered
                    and not (topos & {"top", "bottom_skirt", "bottom_pants", "one_piece", "ensemble_outfit", "outerwear"})
                )
                is_bikini = ("bikini" in cid)
                is_lingerie_set = bool(
                    roles <= {"underwear", "accessory", "legwear", "footwear", "unspecified"}
                    and "intimate_upper" in covered
                    and "intimate_lower" in covered
                    and not is_bikini
                )
                has_one_piece = ("one_piece" in roles or "one_piece" in topos)
                has_top = ("top" in roles or "top" in topos)
                has_skirt = ("bottom_skirt" in topos or ("bottom" in roles and self._is_clothing_skirt_compatible(chosen_style)))
                has_pants = ("bottom_pants" in topos)
                has_ensemble = ("ensemble" in roles or "ensemble_outfit" in topos)
                is_pure_one_piece = has_one_piece and not has_top and not has_skirt and not has_pants and not has_ensemble
                is_top_only = has_top and not has_skirt and not has_pants and not has_one_piece and not has_ensemble
                is_skirt_only = has_skirt and not has_top and not has_pants and not has_one_piece and not has_ensemble
                is_pants_only = has_pants and not has_top and not has_skirt and not has_one_piece and not has_ensemble

                if lvl_code == "L2":
                    if "backless" in cid:
                        cands = ["l2_backless"]
                    else:
                        if (
                            st_id != "back_cutout"
                            and cid not in ("dress_backless", "evening_dress", "cocktail_dress", "halter_dress", "party_club")
                            and "backless" not in joined
                        ):
                            cands = [b for b in cands if b != "l2_backless"]
                        has_midriff_cue = any(k in cid or k in joined for k in ("crop", "midriff", "cutout", "two-piece", "bikini"))
                        if (is_pure_one_piece and not has_midriff_cue) or is_bra_only or is_panties_only or is_top_only or is_skirt_only or is_pants_only:
                            cands = [b for b in cands if b != "l2_midriff_waist"]
                        sleeveless_cues = (
                            "backless", "sleeveless", "strapless", "halter", "off_shoulder",
                            "sundress", "camisole", "crop_top", "bikini", "swimsuit", "bunny_suit",
                            "leotard", "bodysuit", "catsuit", "plugsuit", "zentai",
                        )
                        if (
                            is_bra_only
                            or is_panties_only
                            or is_skirt_only
                            or is_pants_only
                            or any(k in cid for k in sleeveless_cues)
                        ):
                            cands = [b for b in cands if b != "l2_sleeve_wrist"]
                        if is_top_only or is_bra_only:
                            cands = [b for b in cands if b != "l2_leg_slit"]
                        if is_skirt_only or is_pants_only or is_panties_only:
                            cands = [b for b in cands if b not in ("l2_collar_neckline", "l2_shoulder_strap")]
                        if is_bra_only or "strapless" in cid:
                            cands = [b for b in cands if b != "l2_shoulder_strap"]
                elif lvl_code == "L3":
                    if is_bra_only:
                        cands = ["l3_bra_pushed_up"]
                    elif is_panties_only:
                        cands = ["l3_panties_only"]
                    elif is_bikini:
                        cands = ["l3_bikini_top_removed"]
                    elif is_lingerie_set:
                        cands = ["l3_bra_pushed_up", "l3_panties_only"]
                    elif cid == "nurse_uniform":
                        cands = ["l3_open_shirt_breasts", "l3_pulled_down_dress"]
                    elif is_pure_one_piece:
                        if cid in NON_SKIRT_ONE_PIECE or cid in ALLOWED_BUTTON_STYLES:
                            cands = ["l3_pulled_down_dress", "l3_open_shirt_breasts"]
                        else:
                            cands = ["l3_pulled_down_dress"]
                    elif is_top_only:
                        if cid in ALLOWED_BUTTON_STYLES or any(k in cid for k in ("shirt", "blouse")):
                            cands = ["l3_open_shirt_breasts", "l3_raised_top_breasts"]
                        else:
                            cands = ["l3_raised_top_breasts"]
                    elif is_skirt_only:
                        cands = ["l3_topless_skirt_kept"]
                    elif is_pants_only:
                        cands = ["l3_topless_pants_kept"]
                    elif has_ensemble or (has_top and (has_skirt or has_pants)):
                        multi: List[str] = []
                        if has_skirt:
                            multi.append("l3_topless_skirt_kept")
                        if has_pants:
                            multi.append("l3_topless_pants_kept")
                        if has_top or has_ensemble:
                            multi.append("l3_raised_top_breasts")
                            if cid in ALLOWED_BUTTON_STYLES or "shirt" in joined or "blouse" in joined:
                                multi.append("l3_open_shirt_breasts")
                        if multi:
                            cands = multi

        if st_id:
            state_branch_map: Dict[str, Tuple[str, ...]] = {
                "unbuttoned": ("l2_collar_neckline", "l3_open_shirt_breasts"),
                "lifted_up": ("l2_leg_slit", "l3_topless_skirt_kept", "l3_pulled_down_dress"),
                "slipping_off": ("l2_shoulder_strap",),
                "off_shoulder_cut": ("l2_shoulder_strap",),
                "bare_shoulders": ("l2_shoulder_strap",),
                "back_cutout": ("l2_backless",),
                "heart_cutout": ("l2_collar_neckline",),
                "only_lingerie": ("l3_panties_only", "l3_bra_pushed_up", "l3_bikini_top_removed"),
            }
            if st_id in state_branch_map:
                narrowed = [b for b in cands if b in state_branch_map[st_id]]
                if narrowed:
                    cands = narrowed

        return tuple(cands) if cands else None

    def _sample_nudity_result_with_branch(
        self,
        level: str | int,
        rng: Random,
        branch: Optional[str] = None,
        explicit_clothing: Optional[str] = None,
        clothing_state: Optional[str] = None,
    ) -> Tuple[Optional[SampleResult], str, Optional[str]]:
        if _is_none(level):
            return (None, "L1", None)

        data = self._load("nudity_levels")
        levels = data.get("nudity_levels", [])
        if not levels:
            return (None, "L3", None)

        if _is_random(level):
            lvl = self._pick_one(levels, rng)
            self._record_pool_sampling_rationale(
                lvl,
                levels,
                mode="uniform_random",
            )
        else:
            lvl = _match_item(levels, str(level), "nudity_levels")
            if not lvl:
                s = str(level).strip().upper()
                code_map = {"1": "L1", "2": "L2", "3": "L3", "4": "L4", "5": "L5", "6": "L6"}
                mapped = code_map.get(s, s if s in ("L1", "L2", "L3", "L4", "L5", "L6") else None)
                if mapped:
                    lvl = next((item for item in levels if item.get("level") == mapped), None)
            if not lvl:
                raise DataSelectionError(f"Unknown nudity level: {level!r}")

        lvl_code = "L3"
        for code in ["L1", "L2", "L3", "L4", "L5", "L6"]:
            if code in str(lvl.get("level", "")) or code in str(lvl.get("name_zh", "")):
                lvl_code = code
                break

        raw_tags = list(lvl.get("tags", []))
        allowed_branches = self._compatible_branches_for_explicit_style(
            lvl_code, explicit_clothing, clothing_state=clothing_state
        )
        selected_branch = _choose_coherent_branch(
            lvl_code, raw_tags, rng, explicit_branch=branch, allowed_branches=allowed_branches
        )
        if selected_branch:
            branch_tags = [
                t for t in raw_tags
                if isinstance(t, dict)
                and selected_branch in ((t.get("facts") or {}).get("nudity_branches") or ())
            ]
            if branch_tags:
                raw_tags = branch_tags
        if explicit_clothing:
            explicit_mode = get_selection_mode(explicit_clothing)
            if explicit_mode == SelectionMode.EXPLICIT:
                styles_data = self._load("clothing").get("categories", [])
                exp_style = _match_item(styles_data, explicit_clothing)
                exp_tag_match = None if exp_style else _match_tag(styles_data, explicit_clothing)
                if exp_tag_match:
                    exp_style, exp_single_tag = exp_tag_match
                    garment_pool = [exp_single_tag]
                elif exp_style:
                    garment_pool = [
                        t for t in exp_style.get("tags", [])
                        if lvl_code not in ("L1", "L2", "L3", "L4")
                        or is_tag_eligible_for_nudity_level(t, lvl_code)
                    ] or list(exp_style.get("tags", []))
                else:
                    garment_pool = []
                if garment_pool:
                    exp_cid = (exp_style or {}).get("id", "")
                    carrier_ok = [
                        t for t in raw_tags
                        if (req := get_required_carrier_kind(t, default_slot="nudity")) is None
                        or is_carrier_satisfied_by_garments(req, garment_pool, style_id=exp_cid)
                    ]
                    if carrier_ok:
                        raw_tags = carrier_ok
            elif _is_none(explicit_clothing):
                carrier_free = [
                    t for t in raw_tags
                    if get_required_carrier_kind(t, default_slot="nudity") is None
                ]
                if carrier_free:
                    raw_tags = carrier_free
        selected = self._pick(raw_tags, rng, min(rng.randint(2, 3), len(raw_tags))) if raw_tags else []
        item_id = lvl.get("id", f"nudity_{lvl_code}")
        tags_str, sampled = self._to_sampled_tags(selected, item_id, "nudity")
        prov = TagProvenance(
            item_id=item_id,
            kind="nudity",
            semantic_ids=(f"nudity:{lvl_code}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=item_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(item_id, "random" if _is_random(level) else "explicit"),
        ), lvl_code, selected_branch

    def sample_nudity_result(
        self,
        level: str | int,
        rng: Random,
        branch: Optional[str] = None,
    ) -> Tuple[Optional[SampleResult], str]:
        res, lvl_code, _ = self._sample_nudity_result_with_branch(level, rng, branch=branch)
        return res, lvl_code

    def sample_nudity(
        self,
        level: str | int,
        rng: Random,
        branch: Optional[str] = None,
    ) -> Tuple[List[str], str]:
        """返回 (采样tags, 标准等级代码比如L1/L2/L3/L4/L5/L6)"""
        res, lvl_code = self.sample_nudity_result(level, rng, branch=branch)
        return (list(res.tags) if res else [], lvl_code)

    def _resolve_explicit_nudity_level_code(self, level: str | int) -> str:
        data = self._load("nudity_levels")
        levels = data.get("nudity_levels", [])
        if not levels:
            return "L3"
        lvl = _match_item(levels, str(level), "nudity_levels")
        if not lvl:
            mapped = normalize_nudity_level_code(level)
            if mapped in VALID_NUDITY_LEVELS:
                lvl = next((item for item in levels if item.get("level") == mapped), None)
        if not lvl:
            raise DataSelectionError(f"Unknown nudity level: {level!r}")
        for code in VALID_NUDITY_LEVELS:
            if code in str(lvl.get("level", "")) or code in str(lvl.get("name_zh", "")):
                return code
        return "L3"

    def resolve_nudity_constraint(
        self,
        level: str | int,
        rng: Random,
        has_preset: bool = False,
        external_overrides: Optional[Dict[str, Any]] = None,
        *,
        explicit_clothing: Optional[str] = None,
        clothing_state: Optional[str] = None,
    ) -> Tuple[Optional[SampleResult], NudityConstraint]:
        """
        解析统一裸露等级约束对象 (NudityConstraint)，并按需执行单一相容分支裸露标签采样。
        - has_preset=False: 显式/随机/None 均记录 requested/effective/source/selected_branch 并返回采样结果；
        - has_preset=True: 若 UI 显式选择 L1-L6 则记录 ui_explicit + selected_branch，若为随机则记录 preset_native；
          若 UI 为 None 则记录 ui_none。
        - external_overrides['nudity'] 若直接携带可识别等级编码，记录 external_override 与让位说明；
          其余外部自由文本覆盖在冲突消解阶段由 ConflictResolver 判定并回填让位标记。
        """
        raw_val = None if level is None else str(level)
        res: Optional[SampleResult] = None
        yielded = False
        yield_reason: Optional[str] = None
        selected_branch: Optional[str] = None
        has_ext_clothing = isinstance(external_overrides, dict) and bool(external_overrides.get("clothing"))
        eff_explicit_clothing = None if (has_preset or has_ext_clothing) else explicit_clothing
        eff_clothing_state = None if (has_preset or has_ext_clothing) else clothing_state

        if _is_none(level):
            requested_level = "none"
            effective_level: Optional[str] = None
            source = "ui_none"
        elif _is_random(level):
            requested_level = "random"
            if has_preset:
                effective_level = None
                source = "preset_native"
            else:
                res, lvl_code, selected_branch = self._sample_nudity_result_with_branch(
                    level,
                    rng,
                    explicit_clothing=eff_explicit_clothing,
                    clothing_state=eff_clothing_state,
                )
                effective_level = lvl_code
                source = "ui_random"
        else:
            if has_preset:
                lvl_code = self._resolve_explicit_nudity_level_code(level)
                data = self._load("nudity_levels")
                lvl_item = next(
                    (it for it in data.get("nudity_levels", []) if it.get("level") == lvl_code),
                    None,
                )
                lvl_tags = lvl_item.get("tags", []) if isinstance(lvl_item, dict) else ()
                selected_branch = _choose_coherent_branch(lvl_code, lvl_tags, rng)
            else:
                res, lvl_code, selected_branch = self._sample_nudity_result_with_branch(
                    level,
                    rng,
                    explicit_clothing=eff_explicit_clothing,
                    clothing_state=eff_clothing_state,
                )
            requested_level = lvl_code
            effective_level = lvl_code
            source = "ui_explicit"

        ext_nudity = external_overrides.get("nudity") if isinstance(external_overrides, dict) else None
        if isinstance(ext_nudity, str) and ext_nudity.strip():
            ext_code = normalize_nudity_level_code(ext_nudity)
            if ext_code in VALID_NUDITY_LEVELS:
                if (effective_level is not None and effective_level != ext_code) or requested_level == "none":
                    yielded = True
                    yield_reason = "external_nudity_override"
                if ext_code != effective_level or not selected_branch:
                    data = self._load("nudity_levels")
                    lvl_item = next(
                        (it for it in data.get("nudity_levels", []) if it.get("level") == ext_code),
                        None,
                    )
                    lvl_tags = lvl_item.get("tags", []) if isinstance(lvl_item, dict) else ()
                    allowed_ext = self._compatible_branches_for_explicit_style(
                        ext_code, eff_explicit_clothing, clothing_state=eff_clothing_state
                    )
                    selected_branch = _choose_coherent_branch(
                        ext_code, lvl_tags, rng, allowed_branches=allowed_ext
                    )
                effective_level = ext_code
                source = "external_override"

        constraint = NudityConstraint(
            requested_level=requested_level,
            effective_level=effective_level,
            source=source,
            selected_branch=selected_branch,
            yielded_to_override=yielded,
            yield_reason=yield_reason,
            raw_requested_value=raw_val,
        )
        return res, constraint

    # ─── 槽位 4: 服装款式与穿脱状态 ───

    def sample_clothing_result(
        self,
        style: str,
        state: str,
        nudity_level_code: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        *,
        completion_rng: Optional[Random] = None,
        nudity_branch: Optional[str] = None,
        extra_excluded_ids: Optional[Set[str]] = None,
        force_minimal_fallback: bool = False,
    ) -> ClothingSampleResult:
        """
        结构化采样服装标签及语义 Provenance，彻底解耦 DataSampler 与 PromptFragment。
        严格落实四态契约：
        - None: 纯净基础款式，不加状态词、不加裸露联动override、不加扩展库；若款式为随机/自动则仍补全基础穿搭。
        - Auto: 联动裸露等级，L2/L3/L4 接入 24 档数据驱动扩展。
        - Random: 随机合法状态，L2/L3/L4 接入 24 档数据驱动扩展。
        - Explicit: 指定状态，L2/L3/L4 接入 24 档数据驱动扩展。
        """
        if _is_none(style):
            rat_none = self._pop_sampling_rationale(None, "none")
            return ClothingSampleResult(
                base_tags=(),
                nudity_level=nudity_level_code,
                sampling_rationale=rat_none,
                completion_info={"exempt": True, "exempt_reason": "style_none"},
            )

        data = self._load("clothing")
        styles = data.get("categories", [])
        if not styles:
            rat_empty = self._pop_sampling_rationale(None, "none")
            return ClothingSampleResult(
                base_tags=(),
                nudity_level=nudity_level_code,
                sampling_rationale=rat_empty,
                completion_info={"exempt": True, "exempt_reason": "empty_catalog"},
            )

        # 1. 确定服装款式对象
        style_mode = get_selection_mode(style)
        target_tag = None
        excluded_ids = self.get_excluded_candidate_ids(
            "clothing",
            nudity_level_code=nudity_level_code,
            clothing_state=state,
            context=context,
            nudity_branch=nudity_branch if style_mode in (SelectionMode.RANDOM, SelectionMode.AUTO) else None,
        )
        if extra_excluded_ids:
            excluded_ids = set(excluded_ids) | set(extra_excluded_ids)

        if style_mode in (SelectionMode.RANDOM, SelectionMode.AUTO) or _is_random(style) or _is_auto(style):
            chosen_style = self._sample_from_candidates(
                "clothing",
                styles,
                rng,
                profile=context_profile,
                context=context,
                excluded_ids=excluded_ids,
            )
            if not chosen_style:
                rat_fallback = self._pop_sampling_rationale(None, "none")
                if force_minimal_fallback and nudity_level_code in ("L1", "L2", "L3"):
                    comp_rng_fb = completion_rng or derive_deterministic_child_rng(
                        rng, style, state, nudity_level_code, nudity_branch or "", "clothing_completion_fb"
                    )
                    affinity_matrix = getattr(getattr(self, "affinity_registry", None), "matrix", None)
                    fb_tags, fb_info = complete_outfit_tags(
                        primary_style_id="",
                        active_tags=(),
                        state_id=None,
                        state_tags=(),
                        nudity_level_code=nudity_level_code,
                        nudity_branch=nudity_branch,
                        categories=styles,
                        excluded_ids=excluded_ids,
                        context_profile=context_profile,
                        context=context,
                        affinity_matrix=affinity_matrix,
                        to_sampled_fn=self._to_sampled_tags,
                        completion_rng=comp_rng_fb,
                        force_minimal_fallback=True,
                    )
                    fb_info["exempt"] = False
                    return ClothingSampleResult(
                        base_tags=(),
                        state_tags=(),
                        extension_tags=(),
                        style_id="",
                        state_id=None,
                        nudity_level=nudity_level_code,
                        sampling_rationale=rat_fallback,
                        complement_tags=fb_tags,
                        completion_info=fb_info,
                    )
                return ClothingSampleResult(
                    base_tags=(),
                    nudity_level=nudity_level_code,
                    sampling_rationale=rat_fallback,
                    completion_info={"exempt": False, "reason": "no_legal_primary_style"},
                )
        else:
            chosen_style = _match_item(styles, style)
            if not chosen_style:
                tag_match = _match_tag(styles, style)
                if tag_match:
                    chosen_style, target_tag = tag_match
            if not chosen_style:
                raise DataSelectionError(f"Unknown clothing style: {style!r}")

        c_id = chosen_style.get("id", "")
        style_rat = self._pop_sampling_rationale(c_id, "random" if _is_random(style) or _is_auto(style) else "explicit")
        if target_tag:
            if nudity_level_code in ("L1", "L2", "L3", "L4") and not is_tag_eligible_for_nudity_level(target_tag, nudity_level_code):
                raw_style_tags = []
            else:
                raw_style_tags = [target_tag]
        else:
            raw_style_tags = self._select_clothing_style_tags(
                chosen_style,
                rng,
                nudity_level_code=nudity_level_code,
                clothing_state=state,
                style_mode=style_mode,
            )
            if c_id == "lingerie_bra" and nudity_branch == "l3_bra_pushed_up":
                bra_pool = [
                    t for t in chosen_style.get("tags", [])
                    if isinstance(t, dict)
                    and "intimate_upper" in ((t.get("facts") or {}).get("covered_regions") or ())
                ]
                if bra_pool and not any(t in bra_pool for t in raw_style_tags):
                    raw_style_tags = [self._pick_one(bra_pool, rng)]
        _, all_base_sampled = self._to_sampled_tags(
            raw_style_tags,
            c_id,
            "base_clothing",
            semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"),
            rng=rng,
        )
        base_tags_tuple = tuple(all_base_sampled)
        full_base_tags_tuple = base_tags_tuple
        has_variant_metadata = self._has_variant_metadata(base_tags_tuple, chosen_style=chosen_style)
        state_mode = get_selection_mode(state)
        exemption = check_completion_exemption(style)
        affinity_matrix = getattr(getattr(self, "affinity_registry", None), "matrix", None)
        _comp_rng_cache: Optional[Random] = completion_rng

        def _get_comp_rng() -> Random:
            nonlocal _comp_rng_cache
            if _comp_rng_cache is None:
                _comp_rng_cache = derive_deterministic_child_rng(
                    rng,
                    style,
                    state,
                    nudity_level_code,
                    nudity_branch or "",
                    c_id,
                    "clothing_completion",
                )
            return _comp_rng_cache

        def _finalize_clothing_result(
            chosen_base_seq: Sequence[SampledTag],
            state_tags_seq: Sequence[SampledTag],
            chosen_state_id: Optional[str],
            *,
            allow_extensions: bool,
        ) -> ClothingSampleResult:
            final_base = tuple(chosen_base_seq)
            final_state_tags = tuple(state_tags_seq)
            final_state_id = chosen_state_id
            repaired_ensemble = False

            if nudity_level_code in ("L5", "L6") and state_mode != SelectionMode.NONE:
                return ClothingSampleResult(
                    base_tags=(),
                    state_tags=final_state_tags,
                    extension_tags=(),
                    style_id=c_id,
                    state_id=final_state_id,
                    nudity_level=nudity_level_code,
                    sampling_rationale=style_rat,
                    complement_tags=(),
                    completion_info={"exempt": True, "exempt_reason": f"level_{nudity_level_code}"},
                )

            if (
                not has_variant_metadata
                and len(full_base_tags_tuple) >= 2
                and state_mode != SelectionMode.NONE
                and not target_tag
                and final_base
                and any(bool(t.facts and t.facts.piece_role) for t in full_base_tags_tuple)
            ):
                repair_rng = derive_deterministic_child_rng(
                    rng,
                    style,
                    state,
                    nudity_level_code,
                    nudity_branch or "",
                    c_id,
                    "ensemble_repair",
                )
                final_base, repaired_ensemble = repair_ensemble_subsampling(
                    full_base_tags_tuple,
                    final_base,
                    style_id=c_id,
                    state_id=final_state_id,
                    state_tags=final_state_tags,
                    nudity_level_code=nudity_level_code,
                    nudity_branch=nudity_branch,
                    repair_rng=repair_rng,
                )

            if exemption.is_exempt:
                comp_tags_tuple: Tuple[SampledTag, ...] = ()
                comp_info: Dict[str, Any] = {
                    "exempt": True,
                    "exempt_reason": exemption.reason,
                    "repaired_ensemble": repaired_ensemble,
                }
            else:
                comp_tags_tuple, comp_info = complete_outfit_tags(
                    primary_style_id=c_id,
                    active_tags=final_base,
                    state_id=final_state_id,
                    state_tags=final_state_tags,
                    nudity_level_code=nudity_level_code,
                    nudity_branch=nudity_branch,
                    categories=styles,
                    excluded_ids=excluded_ids,
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=self._to_sampled_tags,
                    completion_rng=_get_comp_rng(),
                    force_minimal_fallback=force_minimal_fallback,
                )
                comp_info["exempt"] = False
                comp_info["repaired_ensemble"] = repaired_ensemble
                if comp_info.get("replace_base_with_fallback"):
                    final_base = ()
                    final_state_tags = ()
                    final_state_id = None

            if (
                final_state_tags
                and final_state_id != "linkage_override"
                and (final_base or comp_tags_tuple)
            ):
                actual_garments = list(final_base) + list(comp_tags_tuple)
                compat_state_tags = tuple(
                    st for st in final_state_tags
                    if (
                        (getattr(st, "provenance", None) is not None and "override:linkage" in (st.provenance.semantic_ids or ()))
                        or (req := get_required_carrier_kind(st, default_slot="clothing_state")) is None
                        or is_carrier_satisfied_by_garments(req, actual_garments, style_id=c_id)
                    )
                )
                if state_mode != SelectionMode.EXPLICIT or compat_state_tags:
                    final_state_tags = compat_state_tags

            extension_sampled_tags: List[SampledTag] = []
            if allow_extensions and nudity_level_code in ("L2", "L3", "L4"):
                garment_tags_for_ext = list(final_base) + list(comp_tags_tuple) + (
                    list(final_state_tags) if final_state_id == "linkage_override" else []
                )
                # 若补全、回退或套装修复改动过衣物清单，扩展采样必须走隔离子流，严禁扰动主 rng 状态
                ext_rng = _get_comp_rng() if (
                    bool(comp_tags_tuple)
                    or bool(comp_info.get("fallback_used"))
                    or repaired_ensemble
                ) else rng
                ext_tags = self._sample_clothing_extensions_provenance(
                    nudity_level_code,
                    c_id,
                    ext_rng,
                    data,
                    garment_tags=garment_tags_for_ext,
                    nudity_branch=nudity_branch,
                )
                extension_sampled_tags.extend(ext_tags)

            return ClothingSampleResult(
                base_tags=final_base,
                state_tags=final_state_tags,
                extension_tags=tuple(extension_sampled_tags),
                style_id=c_id,
                state_id=final_state_id,
                nudity_level=nudity_level_code,
                sampling_rationale=style_rat,
                complement_tags=comp_tags_tuple,
                completion_info=comp_info,
            )

        # 契约 1: 状态为 None -> 不加状态词、不加裸露联动override、不加扩展；若款式为随机/自动，仍检查并补全基础穿搭
        if state_mode == SelectionMode.NONE:
            if has_variant_metadata:
                controlled_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
            else:
                controlled_base = base_tags_tuple
            return _finalize_clothing_result(
                controlled_base,
                (),
                None,
                allow_extensions=False,
            )

        linkages = data.get("clothing_nudity_linkage", {})
        linkage_data = linkages.get(nudity_level_code, {})
        style_overrides = linkage_data.get("style_overrides", {})
        states = data.get("clothing_states", [])

        state_tags_list: Sequence[SampledTag] = ()
        state_id: Optional[str] = None

        # L1, L5, L6: 强力应用 style_overrides 保证纯净性与防泄漏
        if nudity_level_code in ("L1", "L5", "L6"):
            if c_id in style_overrides:
                override_tags = list(style_overrides[c_id])
                if nudity_level_code == "L5" and nudity_branch:
                    branch_ov = [
                        t for t in override_tags
                        if _is_linkage_tag_compatible_with_branch(t, nudity_level_code, nudity_branch)
                    ]
                    if branch_ov:
                        override_tags = branch_ov
                _, state_tags_list = self._to_sampled_tags(
                    override_tags,
                    c_id,
                    "clothing_state",
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "override:linkage")
                )
                return _finalize_clothing_result(
                    (),
                    state_tags_list,
                    "linkage_override",
                    allow_extensions=False,
                )
            else:
                gen_tags = list(linkage_data.get("general_tags", []))
                if nudity_branch:
                    branch_gen = [
                        t for t in gen_tags
                        if _is_linkage_tag_compatible_with_branch(t, nudity_level_code, nudity_branch)
                    ]
                    if branch_gen:
                        gen_tags = branch_gen
                if nudity_level_code == "L5" and nudity_branch == "l5_clothes_discarded_nearby":
                    discarded_pool = [
                        t for t in gen_tags
                        if isinstance(t, dict) and "discarded" in ((t.get("facts") or {}).get("garment_states") or ())
                    ]
                    non_discarded_pool = [t for t in gen_tags if t not in discarded_pool]
                    if discarded_pool:
                        chosen_gen = [self._pick_one(discarded_pool, rng)]
                        if non_discarded_pool:
                            chosen_gen.append(self._pick_one(non_discarded_pool, rng))
                    else:
                        chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
                else:
                    chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
                _, state_tags_list = self._to_sampled_tags(
                    chosen_gen,
                    "linkage_general",
                    "clothing_state",
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "linkage:general")
                )
                if nudity_level_code == "L1":
                    if has_variant_metadata:
                        chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
                    else:
                        chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
                else:
                    chosen_base = ()
                return _finalize_clothing_result(
                    chosen_base,
                    state_tags_list,
                    "linkage_general",
                    allow_extensions=False,
                )

        if state_mode == SelectionMode.AUTO:
            # L2, L3, L4 Auto mode
            if c_id in style_overrides:
                override_tags = list(style_overrides[c_id])
                if nudity_level_code in ("L2", "L3", "L4") or nudity_branch:
                    branch_ov = [
                        t for t in override_tags
                        if _is_linkage_tag_compatible_with_branch(t, nudity_level_code, nudity_branch)
                    ]
                    if nudity_branch or nudity_level_code in ("L3", "L4"):
                        override_tags = branch_ov
                _, state_tags_list = self._to_sampled_tags(
                    override_tags,
                    c_id,
                    "clothing_state",
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "override:linkage")
                )
            else:
                gen_tags = list(linkage_data.get("general_tags", []))
                if nudity_level_code in ("L2", "L3", "L4") or nudity_branch:
                    branch_gen = [
                        t for t in gen_tags
                        if _is_linkage_tag_compatible_with_branch(t, nudity_level_code, nudity_branch)
                    ]
                    if nudity_branch or nudity_level_code in ("L3", "L4"):
                        gen_tags = branch_gen
                chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags))) if gen_tags else []
                _, state_tags_list = self._to_sampled_tags(
                    chosen_gen,
                    "linkage_general",
                    "clothing_state",
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "linkage:general")
                )
                if (
                    nudity_level_code == "L4"
                    and nudity_branch in ("l4_towel_sheet_wrap", "l4_accessory_only")
                    and style_mode in (SelectionMode.RANDOM, SelectionMode.AUTO)
                ):
                    chosen_base = ()
                elif has_variant_metadata:
                    chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
                else:
                    chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
                base_tags_tuple = tuple(chosen_base)
            if (
                nudity_level_code == "L4"
                and nudity_branch in ("l4_towel_sheet_wrap", "l4_accessory_only")
                and style_mode in (SelectionMode.RANDOM, SelectionMode.AUTO)
            ):
                base_tags_tuple = ()
            state_id = "auto_linkage"

        elif state_mode == SelectionMode.RANDOM:
            if nudity_level_code == "L1":
                allowed_state_ids = {"normal"}
            elif nudity_level_code == "L2":
                l2_branch_states = {
                    "l2_collar_neckline": {"normal", "unbuttoned", "disheveled", "wet_clinging", "sweat_soaked"},
                    "l2_shoulder_strap": {"normal", "slipping_off", "disheveled"},
                    "l2_leg_slit": {"normal", "lifted_up", "torn_shredded"},
                    "l2_midriff_waist": {"normal", "disheveled", "taut_tight"},
                    "l2_backless": {"normal", "taut_tight", "wet_pure"},
                    "l2_sleeve_wrist": {"normal", "disheveled"},
                }
                allowed_state_ids = l2_branch_states.get(
                    nudity_branch,
                    {"normal", "unbuttoned", "slipping_off", "disheveled", "wet_clinging", "sweat_soaked"},
                )
            elif nudity_level_code == "L3":
                if nudity_branch == "l3_open_shirt_breasts":
                    allowed_state_ids = {"unbuttoned", "disheveled", "wet_clinging"}
                elif nudity_branch == "l3_pulled_down_dress":
                    allowed_state_ids = {"pulled_down", "slipping_off", "wet_clinging"}
                elif nudity_branch == "l3_topless_skirt_kept":
                    allowed_state_ids = {"lifted_up", "wet_clinging", "torn_shredded"}
                elif nudity_branch in ("l3_panties_only", "l3_bikini_top_removed"):
                    allowed_state_ids = {"pulled_down", "only_lingerie", "torn_shredded"}
                else:
                    allowed_state_ids = {"unbuttoned", "slipping_off", "lifted_up", "pulled_down", "wet_clinging", "torn_shredded"}
            elif nudity_level_code == "L4":
                if nudity_branch in ("l4_panties_only", "l4_bra_and_panties"):
                    allowed_state_ids = {"only_lingerie", "pulled_down", "slipping_off"}
                elif nudity_branch in ("l4_hosiery_footwear_only", "l4_towel_sheet_wrap", "l4_accessory_only"):
                    allowed_state_ids = {"only_lingerie", "torn_shredded"}
                else:
                    allowed_state_ids = {"only_lingerie", "pulled_down", "lifted_up", "torn_shredded", "slipping_off"}
            else:
                allowed_state_ids = set()

            pool = [s for s in states if s.get("id") in allowed_state_ids] if allowed_state_ids else states
            chosen_state = self._pick_one(pool if pool else states, rng)
            state_id = chosen_state.get("id", "")
            raw_state_tags = chosen_state.get("tags", [])
            picked_state_tags = self._pick_state_tags(
                raw_state_tags, state_id, chosen_style, has_variant_metadata, rng
            )
            _, state_tags_list = self._to_sampled_tags(
                picked_state_tags,
                state_id,
                "clothing_state",
                semantic_ids=(f"clothing:{c_id}", f"state:{state_id}", f"nudity:{nudity_level_code}")
            )
            is_lifted_skirt = (
                state_id in ("lifted_up", "lifted", "lifted_skirt")
                or (state and any(w in state.lower() for w in ("lifted", "掀起裙摆", "lifted_up", "lifted_skirt")))
            )
            if has_variant_metadata:
                chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
            else:
                if is_lifted_skirt:
                    skirt_base = [t for t in base_tags_tuple if self._tag_has_skirt_carrier(t, c_id)]
                    if skirt_base:
                        picked_skirt = self._pick_one(skirt_base, rng)
                        non_skirt = [t for t in base_tags_tuple if t != picked_skirt]
                        count = min(2, len(base_tags_tuple))
                        other_pick = self._pick(non_skirt, rng, count - 1) if count > 1 and non_skirt else []
                        chosen_base = [picked_skirt] + other_pick
                    else:
                        chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
                else:
                    chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
            base_tags_tuple = tuple(chosen_base)

        else:
            # EXPLICIT
            chosen_state = _match_item(states, state, "clothing_states")
            if not chosen_state:
                raise DataSelectionError(f"Unknown clothing state: {state!r}")
            state_id = chosen_state.get("id", "")
            raw_state_tags = chosen_state.get("tags", [])
            picked_state_tags = self._pick_state_tags(
                raw_state_tags, state_id, chosen_style, has_variant_metadata, rng
            )
            _, state_tags_list = self._to_sampled_tags(
                picked_state_tags,
                state_id,
                "clothing_state",
                semantic_ids=(f"clothing:{c_id}", f"state:{state_id}", f"nudity:{nudity_level_code}")
            )
            is_lifted_skirt = (
                state_id in ("lifted_up", "lifted", "lifted_skirt")
                or (state and any(w in state.lower() for w in ("lifted", "掀起裙摆", "lifted_up", "lifted_skirt")))
            )
            if has_variant_metadata:
                chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
            else:
                if is_lifted_skirt:
                    skirt_base = [t for t in base_tags_tuple if self._tag_has_skirt_carrier(t, c_id)]
                    if skirt_base:
                        picked_skirt = self._pick_one(skirt_base, rng)
                        non_skirt = [t for t in base_tags_tuple if t != picked_skirt]
                        count = min(2, len(base_tags_tuple))
                        other_pick = self._pick(non_skirt, rng, count - 1) if count > 1 and non_skirt else []
                        chosen_base = [picked_skirt] + other_pick
                    else:
                        chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
                else:
                    chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
            base_tags_tuple = tuple(chosen_base)

        return _finalize_clothing_result(
            base_tags_tuple,
            state_tags_list,
            state_id,
            allow_extensions=True,
        )

    def sample_clothing_with_nudity_linkage(
        self,
        style: str,
        state: str,
        nudity_level_code: str,
        rng: Random,
        context: Optional[str] = None
    ) -> List[str]:
        res = self.sample_clothing_result(style, state, nudity_level_code, rng, context)
        return res.all_text_tags

    def _sample_clothing_extensions_provenance(
        self,
        nudity_level_code: Optional[str],
        clothing_id: str,
        rng: Random,
        data: Dict[str, Any],
        *,
        garment_tags: Sequence[Any] = (),
        nudity_branch: Optional[str] = None,
    ) -> List[SampledTag]:
        """为服装受控应用 9/5/10 扩展库，按材质/分支过滤不相容扩展并返回携带真实 Provenance 的 SampledTag 列表。"""
        if nudity_level_code in ("L1", "L5", "L6") or not nudity_level_code:
            return []

        policy = data.get("extension_policy", {}).get(nudity_level_code, {})
        if not policy:
            return []

        result_tags: List[SampledTag] = []
        allowed_exp_ids = set(policy.get("exposure_ids", []))
        allowed_trans_ids = set(policy.get("transparency_ids", []))
        allowed_wardrobe_ids = set(policy.get("wardrobe_ids", []))

        has_structured_catalog_facts = any(
            isinstance(t, dict) and bool((t.get("facts") or {}).get("piece_role"))
            for c in data.get("categories", [])[:5]
            for t in c.get("tags", [])
        )
        raw_exp_tiers = [t for t in data.get("sfw_exposure_tiers", []) if t.get("id") in allowed_exp_ids]
        raw_trans_tiers = [t for t in data.get("cloth_transparency_tiers", []) if t.get("id") in allowed_trans_ids]
        if has_structured_catalog_facts:
            all_exp_tiers = filter_extensions_for_garments(
                raw_exp_tiers,
                kind="sfw_exposure",
                nudity_level_code=nudity_level_code,
                clothing_id=clothing_id,
                garment_tags=garment_tags,
                nudity_branch=nudity_branch,
            )
            all_trans_tiers = filter_extensions_for_garments(
                raw_trans_tiers,
                kind="cloth_transparency",
                nudity_level_code=nudity_level_code,
                clothing_id=clothing_id,
                garment_tags=garment_tags,
                nudity_branch=nudity_branch,
            )
        else:
            all_exp_tiers = raw_exp_tiers
            all_trans_tiers = raw_trans_tiers
        all_wardrobe = (
            [t for t in data.get("lingerie_wardrobe", []) if t.get("id") in allowed_wardrobe_ids]
            if (nudity_level_code == "L4" and nudity_branch in ("l4_panties_only", "l4_bra_and_panties", None))
            else []
        )
        if nudity_level_code == "L4" and nudity_branch == "l4_panties_only":
            all_wardrobe = [
                t for t in all_wardrobe
                if (t.get("id") or "") in ("crotchless_panties", "garter_stockings")
            ]
        elif nudity_level_code == "L4" and nudity_branch == "l4_bra_and_panties":
            all_wardrobe = [
                t for t in all_wardrobe
                if (t.get("id") or "") not in ("crotchless_panties", "crotchless_free")
            ]
        if nudity_level_code == "L4" and nudity_branch in ("l4_towel_sheet_wrap", "l4_accessory_only"):
            return []

        def _make_ext_tag(itm: Any, parent_id: str, family: str) -> SampledTag:
            if isinstance(itm, dict):
                txt = itm.get("text", "")
                tid = itm.get("id", parent_id)
                fcts = SemanticFacts.from_dict(itm.get("facts", {}))
            else:
                txt = str(itm).strip()
                tid = parent_id
                fcts = SemanticFacts()
            prov = TagProvenance(
                item_id=parent_id,
                semantic_ids=(f"extension_family:{family}", f"extension_tier:{parent_id}", f"nudity:{nudity_level_code}"),
                kind="clothing_extension",
                parent_ids=(parent_id,),
            )
            return SampledTag(text=txt, provenance=prov, id=tid, facts=fcts)

        if nudity_level_code == "L2":
            if all_exp_tiers and rng.random() < 0.7:
                picked = self._pick_one(all_exp_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(_make_ext_tag(t, t_id, "sfw_exposure"))
            if all_trans_tiers and rng.random() < 0.5:
                picked = self._pick_one(all_trans_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(_make_ext_tag(t, t_id, "cloth_transparency"))

        elif nudity_level_code == "L3":
            if all_exp_tiers and rng.random() < 0.7:
                picked = self._pick_one(all_exp_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(_make_ext_tag(t, t_id, "sfw_exposure"))
            if all_trans_tiers and rng.random() < 0.6:
                picked = self._pick_one(all_trans_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(_make_ext_tag(t, t_id, "cloth_transparency"))

        elif nudity_level_code == "L4":
            if all_wardrobe and (clothing_id == "lingerie_lace" or rng.random() < 0.6):
                picked = self._pick_one(all_wardrobe, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:2]:
                    result_tags.append(_make_ext_tag(t, t_id, "lingerie_wardrobe"))
            else:
                if all_exp_tiers and rng.random() < 0.6:
                    picked = self._pick_one(all_exp_tiers, rng)
                    t_id = picked.get("id", "")
                    for t in picked.get("tags", [])[:1]:
                        result_tags.append(_make_ext_tag(t, t_id, "sfw_exposure"))
                if all_trans_tiers and rng.random() < 0.5:
                    picked = self._pick_one(all_trans_tiers, rng)
                    t_id = picked.get("id", "")
                    for t in picked.get("tags", [])[:1]:
                        result_tags.append(_make_ext_tag(t, t_id, "cloth_transparency"))

        return result_tags

    def _apply_clothing_extensions(
        self,
        base_tags: List[str],
        nudity_level_code: Optional[str],
        clothing_id: str,
        rng: Random,
        data: Dict[str, Any]
    ) -> List[str]:
        ext_sampled = self._sample_clothing_extensions_provenance(nudity_level_code, clothing_id, rng, data)
        return list(base_tags) + [t.text for t in ext_sampled]

    # ─── 槽位 5: 光影氛围 ───

    def _format_lighting_sample_result(
        self,
        chosen: Dict[str, Any],
        rng: Random,
        nudity_level_code: Optional[str] = None,
        target_tag: Optional[Any] = None,
        preset: str = "随机 (Random)",
    ) -> SampleResult:
        chosen_id = chosen.get("id", "lighting_preset")
        # Combo presets have main_light / modifier_light / atmosphere
        if any(k in chosen for k in ("main_light", "modifier_light", "atmosphere")):
            parts: List[str] = []
            for k in ["main_light", "modifier_light", "atmosphere"]:
                v = chosen.get(k, "")
                if v:
                    parts.extend([t.strip() for t in v.split(",") if t.strip()])
            prov = TagProvenance(
                item_id=chosen_id,
                kind="lighting",
                semantic_ids=(f"lighting:{chosen_id}",),
                parent_ids=(chosen_id,),
            )
            sampled_tags: List[SampledTag] = [
                SampledTag(
                    text=t,
                    provenance=prov,
                    id=f"{chosen_id}__tag_{idx:03d}",
                    facts=SemanticFacts(semantic_role="selector"),
                )
                for idx, t in enumerate(parts)
            ]
            return SampleResult(
                tags=tuple(parts),
                item_id=chosen_id,
                provenance=prov,
                sampled_tags=tuple(sampled_tags),
                sampling_rationale=self._pop_sampling_rationale(chosen_id, "random" if _is_random(preset) or _is_auto(preset) else "explicit"),
            )

        # Single presets (professional, cinematic, special, erotic)
        tags = list(chosen.get("tags", []))
        if nudity_level_code != "L1":
            tags.extend(chosen.get("erotic_tags", []))
        else:
            # L1 级别下按标签事实执行情色过滤，保留合法光影 (如屏幕光、走廊漏光、冰箱光等)
            banned_l1_lighting = (
                "nude", "naked", "cum", "breast", "breasts", "pussy",
                "crotch", "buttocks", "red light district"
            )
            tags = [
                t for t in tags
                if not any(
                    bw in (t.get("text", "") if isinstance(t, dict) else str(t)).lower()
                    for bw in banned_l1_lighting
                )
            ]

        if target_tag:
            if nudity_level_code == "L1" and any(
                bw in (target_tag.get("text", "") if isinstance(target_tag, dict) else str(target_tag)).lower()
                for bw in ("nude", "naked", "cum", "breast", "breasts", "pussy", "crotch", "buttocks", "red light district")
            ):
                selected = []
            else:
                selected = [target_tag]
        else:
            selector_tags = [
                t for t in tags
                if (isinstance(t, dict) and (t.get("facts", {}).get("semantic_role") == "selector" or t.get("id", "").endswith("__tag_000")))
            ]
            other_tags = [t for t in tags if t not in selector_tags]
            if selector_tags:
                sel = self._pick_one(selector_tags, rng)
                selected = [sel]
                if other_tags and rng.random() < 0.5:
                    selected.append(self._pick_one(other_tags, rng))
            else:
                selected = self._pick(tags, rng, min(rng.randint(1, 2), len(tags))) if tags else []

        prov = TagProvenance(
            item_id=chosen_id,
            kind="lighting",
            semantic_ids=(f"lighting:{chosen_id}",),
            parent_ids=(chosen_id,),
        )
        tags_str: List[str] = []
        sampled_tags_list: List[SampledTag] = []
        primary_tod = None
        for item in selected:
            if isinstance(item, dict):
                f = item.get("facts", {})
                if f.get("time_of_day"):
                    primary_tod = f.get("time_of_day")
                    break

        for idx, item in enumerate(selected):
            if isinstance(item, dict):
                t_text = item.get("text", "")
                t_id = item.get("id", f"{chosen_id}__tag_{idx:03d}")
                f_dict = dict(item.get("facts", {}))
                if f_dict.get("time_of_day") is None and primary_tod:
                    f_dict["time_of_day"] = primary_tod
                t_facts = SemanticFacts.from_dict(f_dict)
                if t_facts.semantic_role is None:
                    t_facts = SemanticFacts.from_dict({**f_dict, "semantic_role": "selector"})
            else:
                t_text = str(item).strip()
                t_id = f"{chosen_id}__tag_{idx:03d}"
                t_facts = SemanticFacts(semantic_role="selector", time_of_day=primary_tod)
            tags_str.append(t_text)
            sampled_tags_list.append(
                SampledTag(
                    text=t_text,
                    provenance=TagProvenance(
                        item_id=chosen_id,
                        kind="lighting",
                        parent_ids=(chosen_id,),
                        semantic_ids=(f"lighting:{chosen_id}",),
                    ),
                    id=t_id,
                    facts=t_facts,
                )
            )
        return SampleResult(
            tags=tuple(tags_str),
            item_id=chosen_id,
            provenance=prov,
            sampled_tags=tuple(sampled_tags_list),
            sampling_rationale=self._pop_sampling_rationale(chosen_id, "random" if _is_random(preset) or _is_auto(preset) else "explicit"),
        )

    def sample_lighting_result(
        self,
        preset: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
    ) -> Optional[SampleResult]:
        if _is_none(preset):
            return None
        data = self._load("lighting")
        combos = data.get("preset_combos", [])
        prof = data.get("professional_lighting", [])
        cine = data.get("cinematic_lighting", [])
        spec = data.get("special_effects", [])
        erot = data.get("erotic_lighting", [])
        all_lighting_candidates = list(combos) + list(prof) + list(cine) + list(spec) + list(erot)

        if not all_lighting_candidates:
            return None

        excluded_ids = self.get_excluded_candidate_ids(
            "lighting",
            nudity_level_code=nudity_level_code,
        )

        target_tag = None
        if _is_auto(preset) or _is_random(preset):
            chosen = self._sample_from_candidates(
                "lighting",
                all_lighting_candidates,
                rng,
                profile=context_profile,
                excluded_ids=excluded_ids,
            )
            if not chosen:
                return None
        else:
            # EXPLICIT
            p = _match_item(combos, preset, "lighting_combos")
            if p:
                chosen = p
            else:
                all_other = list(prof) + list(cine) + list(spec) + list(erot)
                item_match = _match_item(all_other, preset, "lighting_presets")
                if not item_match:
                    tag_match = _match_tag(all_other, preset)
                    if tag_match:
                        item_match, target_tag = tag_match
                if item_match:
                    chosen = item_match
                else:
                    raise DataSelectionError(f"Unknown lighting preset: {preset!r}")

        return self._format_lighting_sample_result(
            chosen, rng, nudity_level_code=nudity_level_code, target_tag=target_tag, preset=preset
        )

    def sample_lighting(
        self,
        preset: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_lighting_result(preset, rng, nudity_level_code)
        return list(res.tags) if res else []

    # ─── 槽位 6: 姿势动作 ───

    def sample_pose_result(
        self,
        category: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        shot_type_id: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(category):
            return None
        data = self._load("poses")
        categories = data.get("pose_categories", [])
        if not categories:
            return None

        excluded_ids = self.get_excluded_candidate_ids(
            "pose",
            nudity_level_code=nudity_level_code,
            shot_type_id=shot_type_id,
        )

        target_tag = None
        if _is_random(category) or _is_auto(category):
            cat = self._sample_from_candidates(
                "pose",
                categories,
                rng,
                profile=context_profile,
                excluded_ids=excluded_ids,
            )
            if not cat:
                return None
        else:
            cat = _match_item(categories, category, "poses")
            if not cat:
                tag_match = _match_tag(categories, category)
                if tag_match:
                    cat, target_tag = tag_match
            if not cat:
                raise DataSelectionError(f"Unknown pose category: {category!r}")

        all_tags: List[Any] = []
        for sub in cat.get("subcategories", []):
            all_tags.extend(sub.get("tags", []))
        if not all_tags:
            all_tags = self._flatten_tags(cat)

        cat_id = cat.get("id", "pose_default")
        prov = TagProvenance(item_id=cat_id, kind="pose", semantic_ids=(f"pose:{cat_id}",))

        banned_in_l1 = [
            "skirt lifted", "skirt hiked", "skirt pulled", "skirt riding",
            "bra", "panties", "breasts", "pussy", "nude", "naked",
            "undressed", "cock sliding", "penetrated", "face-fucked",
            "cum dripping", "dripping on", "cupping breasts"
        ]

        def _is_banned_by_l1(tag: Any) -> bool:
            txt = (tag.get("text", "") if isinstance(tag, dict) else str(tag)).lower()
            if any(b in txt for b in banned_in_l1):
                return True
            return not _is_tag_compatible_with_nudity(tag, "L1", slot_name="pose", item_id=cat_id)

        if nudity_level_code == "L1":
            all_tags = [t for t in all_tags if not _is_banned_by_l1(t)]
        elif nudity_level_code == "L2" and (_is_random(category) or _is_auto(category)):
            filtered_l2_pose = [
                t for t in all_tags
                if _is_tag_compatible_with_nudity(t, "L2", slot_name="pose", item_id=cat_id)
            ]
            if filtered_l2_pose:
                all_tags = filtered_l2_pose

        if target_tag:
            if nudity_level_code == "L1" and _is_banned_by_l1(target_tag):
                # 标签直达与文本直达严格执行同一 L1 过滤约束，命中排除列表直接置空杜绝泄露
                selected = []
            else:
                selected = [target_tag]
        elif cat_id in ("prose_pose_templates", "signature_poses"):
            # 完整整身叙述模板与标志性姿态为整身姿势规范，运行时必须严格单选 1 条
            selected = self._pick(all_tags, rng, 1) if all_tags else []
        else:
            k = min(rng.randint(1, 2), max(1, len(all_tags))) if all_tags else 0
            picked = self._pick(all_tags, rng, k) if all_tags else []
            if len(picked) > 1 and not _are_pose_tags_compatible(picked[0], picked[1]):
                selected = [picked[0]]
            else:
                selected = picked
        tags_str: List[str] = []
        sampled_tags: List[SampledTag] = []
        for idx, itm in enumerate(selected):
            if isinstance(itm, dict):
                t_text = itm.get("text", "")
                t_id = itm.get("id", f"{cat_id}__tag_{idx:03d}")
                t_facts = SemanticFacts.from_dict(itm.get("facts", {}))
                if t_facts.semantic_role is None:
                    t_facts = SemanticFacts.from_dict({**itm.get("facts", {}), "semantic_role": "selector"})
            else:
                t_text = str(itm).strip()
                t_id = f"{cat_id}__tag_{idx:03d}"
                t_facts = SemanticFacts(semantic_role="selector")
            tags_str.append(t_text)
            sampled_tags.append(
                SampledTag(
                    text=t_text,
                    provenance=TagProvenance(
                        item_id=cat_id if cat_id else t_id,
                        kind="pose",
                        parent_ids=(cat_id,),
                        semantic_ids=(f"pose:{cat_id}",)
                    ),
                    id=t_id,
                    facts=t_facts,
                )
            )
        return SampleResult(
            tags=tuple(tags_str),
            item_id=cat_id,
            provenance=prov,
            sampled_tags=tuple(sampled_tags),
            sampling_rationale=self._pop_sampling_rationale(cat_id, "random" if _is_random(category) else "explicit"),
        )

    def sample_pose(
        self,
        category: str,
        rng: Random,
        nudity_level_code: Optional[str] = None,
        shot_type_id: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_pose_result(
            category,
            rng,
            nudity_level_code=nudity_level_code,
            shot_type_id=shot_type_id,
        )
        return list(res.tags) if res else []

    # ─── 槽位 7: 表情眼神 ───

    def sample_expression_result(self, mood: str, rng: Random, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(mood):
            return None
        data = self._load("expressions")
        categories = list(data.get("emotions", [])) + list(data.get("mouth_actions", []))
        if not categories:
            return None

        target_tag = None
        if _is_random(mood):
            cat = self._sample_from_candidates("expression", categories, rng, profile=context_profile)
        else:
            cat = _match_item(categories, mood, "expressions")
            if not cat:
                tag_match = _match_tag(categories, mood)
                if tag_match:
                    cat, target_tag = tag_match
            if not cat:
                raise DataSelectionError(f"Unknown expression mood: {mood!r}")

        raw_tags = cat.get("tags", [])
        if target_tag:
            selected = [target_tag]
        else:
            selected = self._pick(raw_tags, rng, min(rng.randint(2, 3), len(raw_tags))) if raw_tags else []
        cat_id = cat.get("id", "expression_default")
        tags_str, sampled = self._to_sampled_tags(selected, cat_id, "expression")
        prov = TagProvenance(item_id=cat_id, kind="expression", semantic_ids=(f"expression:{cat_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=cat_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(cat_id, "random" if _is_random(mood) else "explicit"),
        )

    def sample_expression(self, mood: str, rng: Random) -> List[str]:
        res = self.sample_expression_result(mood, rng)
        return list(res.tags) if res else []

    # ─── 槽位 8: 风格/胶片 ───

    def sample_film_result(self, stock: str, rng: Random, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(stock):
            return None
        data = self._load("film_stocks")

        all_items: List[Dict[str, Any]] = []
        for group in ["film_stocks", "cinema_lenses", "weather_moods", "photography_styles"]:
            items = data.get(group, [])
            if isinstance(items, list):
                all_items.extend(items)

        if not all_items:
            return None

        target_tag = None
        if _is_random(stock):
            film_list = data.get("film_stocks", all_items)
            chosen_film = self._sample_from_candidates("film", film_list, rng, profile=context_profile)
        else:
            chosen_film = _match_item(all_items, stock)
            if not chosen_film:
                tag_match = _match_tag(all_items, stock)
                if tag_match:
                    chosen_film, target_tag = tag_match
            if not chosen_film:
                raise DataSelectionError(f"Unknown film stock: {stock!r}")

        if not chosen_film:
            return None

        raw_tags = chosen_film.get("tags", [])
        c_id = chosen_film.get("id", "film_unknown")
        if target_tag:
            selected = [target_tag]
        elif (
            c_id.startswith("camera_hardware_")
            or c_id.startswith("film_stock_")
            or c_id == "darkroom_photographic_paper"
        ):
            # 品牌集合与器材套机集合条目包含互斥的具体型号产品，运行时必须严格单选 1 项产品标签
            selected = self._pick(raw_tags, rng, 1) if raw_tags else []
        else:
            # 传统单款胶卷预设：必须确保主视觉标志性标签（tag_000）被稳定产出，
            # 并支持在 1~2 之间抽样次级质感细节
            k = min(rng.randint(1, 2), len(raw_tags)) if raw_tags else 0
            if k <= 1 or len(raw_tags) <= 1:
                selected = [raw_tags[0]] if raw_tags else []
            else:
                selected = [raw_tags[0]] + self._pick(raw_tags[1:], rng, 1)

        tags_str, sampled = self._to_sampled_tags(selected, c_id, "film")
        prov = TagProvenance(
            kind="film",
            item_id=c_id,
            semantic_ids=(f"film:{c_id}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(stock) else "explicit"),
        )

    def sample_film(self, stock: str, rng: Random) -> List[str]:
        res = self.sample_film_result(stock, rng)
        return list(res.tags) if res else []

    # ─── 槽位 9: 妆容细节 ───

    def sample_makeup_result(self, makeup_style: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(makeup_style):
            return None
        data = self._load("makeup")
        styles: List[Dict[str, Any]] = []
        for grp in ["natural_makeup", "creative_artistic", "japanese_style", "erotic_sensual"]:
            styles.extend(data.get(grp, []))
        if not styles:
            styles = data.get("categories", [])
        if not styles:
            return None

        if _is_random(makeup_style):
            chosen = self._sample_from_candidates("makeup", styles, rng, profile=context_profile, context=context)
        else:
            chosen = _match_item(styles, makeup_style, "makeup")
            if not chosen:
                raise DataSelectionError(f"Unknown makeup style: {makeup_style!r}")

        raw_tags = chosen.get("tags", [])
        selected = self._pick(raw_tags, rng, min(rng.randint(2, 3), len(raw_tags))) if raw_tags else []
        c_id = chosen.get("id", "makeup_default")
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "makeup")
        prov = TagProvenance(item_id=c_id, kind="makeup", semantic_ids=(f"makeup:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(makeup_style) else "explicit"),
        )

    def sample_makeup(self, makeup_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_makeup_result(makeup_style, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 10: 发型与饰品 ───

    def sample_hairstyle_result(
        self,
        hairstyle: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(hairstyle):
            return None
        data = self._load("accessories")
        styles = data.get("hairstyles", [])
        if not styles:
            return None

        target_tag = None
        is_rand = _is_random(hairstyle) or _is_auto(hairstyle)
        if is_rand:
            excluded_ids = self.get_excluded_candidate_ids("hairstyle", nudity_level_code=nudity_level_code)
            chosen = self._sample_from_candidates(
                "hairstyle",
                styles,
                rng,
                profile=context_profile,
                context=context,
                excluded_ids=excluded_ids,
            )
            if not chosen:
                return None
        else:
            chosen = _match_item(styles, hairstyle, "hairstyles")
            if not chosen:
                tag_match = _match_tag(styles, hairstyle)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown hairstyle: {hairstyle!r}")

        c_id = chosen.get("id", "hairstyle_default")
        raw_tags = list(chosen.get("tags", []))
        if is_rand and nudity_level_code in ("L1", "L2"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(tg, nudity_level_code, slot_name="hairstyle", item_id=c_id)
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        if target_tag:
            selected = [target_tag]
        else:
            selected = self._pick_tags_respecting_mutex(raw_tags, rng, c_id) if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "hairstyle", rng=rng)
        prov = TagProvenance(item_id=c_id, kind="hairstyle", semantic_ids=(f"hairstyle:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if is_rand else "explicit"),
        )

    def sample_hairstyle(
        self,
        hairstyle: str,
        rng: Random,
        context: Optional[str] = None,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_hairstyle_result(hairstyle, rng, context, nudity_level_code=nudity_level_code)
        return list(res.tags) if res else []

    def sample_jewelry_result(
        self,
        jewelry_style: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(jewelry_style):
            return None
        data = self._load("accessories")
        items = list(data.get("headwear_jewelry", [])) + list(data.get("jewelry", []))
        if not items:
            return None

        target_tag = None
        is_rand = _is_random(jewelry_style) or _is_auto(jewelry_style)
        if is_rand:
            excluded_ids = self.get_excluded_candidate_ids("jewelry", nudity_level_code=nudity_level_code)
            chosen = self._sample_from_candidates(
                "jewelry",
                items,
                rng,
                profile=context_profile,
                context=context,
                excluded_ids=excluded_ids,
            )
        else:
            chosen = _match_item(items, jewelry_style)
            if not chosen:
                tag_match = _match_tag(items, jewelry_style)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown jewelry style: {jewelry_style!r}")

        if not chosen:
            return None

        c_id = chosen.get("id", "jewelry_unknown")
        raw_tags = list(chosen.get("tags", []))
        if is_rand and nudity_level_code in ("L1", "L2"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(tg, nudity_level_code, slot_name="jewelry", item_id=c_id)
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        if target_tag:
            selected = [target_tag]
        else:
            selected = self._pick_tags_respecting_mutex(raw_tags, rng, c_id) if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "jewelry", rng=rng)
        prov = TagProvenance(
            kind="jewelry",
            item_id=c_id,
            semantic_ids=(f"jewelry:{c_id}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if is_rand else "explicit"),
        )

    def sample_jewelry(
        self,
        jewelry_style: str,
        rng: Random,
        context: Optional[str] = None,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_jewelry_result(jewelry_style, rng, context, nudity_level_code=nudity_level_code)
        return list(res.tags) if res else []

    # ─── 槽位 11: 真实瑕疵细节 ───

    def sample_imperfections_result(self, imp_type: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(imp_type):
            return None
        data = self._load("imperfections")
        categories = data.get("categories", [])
        if not categories:
            return None

        if _is_random(imp_type):
            chosen = self._pick_one(categories, rng)
            self._record_pool_sampling_rationale(
                chosen,
                categories,
                mode="uniform_random",
            )
        else:
            chosen = _match_item(categories, imp_type)
            if not chosen:
                raise DataSelectionError(f"Unknown imperfection type: {imp_type!r}")

        raw_tags = chosen.get("tags", [])
        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        c_id = chosen.get("id", "imperfection_default")
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "imperfections")
        prov = TagProvenance(item_id=c_id, kind="imperfections", semantic_ids=(f"imperfection:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(imp_type) else "explicit"),
        )

    def sample_imperfections(self, imp_type: str, rng: Random) -> List[str]:
        res = self.sample_imperfections_result(imp_type, rng)
        return list(res.tags) if res else []

    # ─── 槽位 12: 纹身标记与皮肤融合 ───

    def sample_tattoo_result(
        self,
        tattoo_style: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(tattoo_style):
            return None
        data = self._load("tattoos")
        tattoos = data.get("categories", [])
        if not tattoos:
            return None

        is_rand = _is_random(tattoo_style) or _is_auto(tattoo_style)
        if is_rand:
            pool = [t for t in tattoos if t.get("id") != "none"]
            excluded_ids = self.get_excluded_candidate_ids("tattoo", nudity_level_code=nudity_level_code)
            chosen = self._sample_from_candidates(
                "tattoo",
                pool,
                rng,
                profile=context_profile,
                context=context,
                excluded_ids=excluded_ids,
            )
        else:
            chosen = _match_item(tattoos, tattoo_style)
            if not chosen:
                raise DataSelectionError(f"Unknown tattoo style: {tattoo_style!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        c_id = chosen.get("id", "tattoo_unknown")
        raw_tags = list(chosen.get("tags", []))
        if is_rand and nudity_level_code in ("L1", "L2"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(tg, nudity_level_code, slot_name="tattoo", item_id=c_id)
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        selected = raw_tags[:2] if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "tattoo")
        prov = TagProvenance(
            kind="tattoo",
            item_id=c_id,
            semantic_ids=(f"tattoo:{c_id}",),
        )
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if is_rand else "explicit"),
        )

    def sample_tattoo(
        self,
        tattoo_style: str,
        rng: Random,
        context: Optional[str] = None,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_tattoo_result(tattoo_style, rng, context, nudity_level_code=nudity_level_code)
        return list(res.tags) if res else []

    # ─── 槽位 13: 道具宠物 ───

    def sample_prop_result(
        self,
        prop_style: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        pose_id: Optional[str] = None,
        hand_state: Optional[str] = None,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(prop_style):
            return None
        data = self._load("props")
        props = data.get("categories", [])
        if not props:
            return None

        if _is_random(prop_style) or _is_auto(prop_style):
            pool = [p for p in props if p.get("id") != "none"]
            excluded_ids = self.get_excluded_candidate_ids(
                "props",
                nudity_level_code=nudity_level_code,
                pose_id=pose_id,
                hand_state=hand_state,
                context=context,
            )
            chosen = self._sample_from_candidates(
                "props",
                pool,
                rng,
                profile=context_profile,
                context=context,
                excluded_ids=excluded_ids,
            )
            if not chosen:
                return None
        else:
            chosen = _match_item(props, prop_style)
            if not chosen:
                raise DataSelectionError(f"Unknown prop style: {prop_style!r}")

        if chosen.get("id") == "none":
            return None

        c_id = chosen.get("id", "prop_default")
        hands_occupied = 0
        if hand_state in ("both_busy", "restrained"):
            hands_occupied = 2
        elif hand_state == "one_busy":
            hands_occupied = 1
        elif hand_state in ("free", "unspecified"):
            hands_occupied = 0
        elif pose_id:
            tag_hs = self._lookup_pose_hand_state(pose_id)
            if tag_hs in ("both_busy", "restrained"):
                hands_occupied = 2
            elif tag_hs == "one_busy":
                hands_occupied = 1
        available_hands = max(0, 2 - hands_occupied)

        if chosen.get("items"):
            items = chosen["items"]
            valid_items = [
                it for it in items
                if any(
                    isinstance(t, dict) and t.get("facts", {}).get("hands_required", 0) <= available_hands
                    and (available_hands > 0 or t.get("facts", {}).get("prop_usage") != "handheld")
                    for t in it.get("tags", [])
                )
            ]
            picked_item = self._pick_one(valid_items if valid_items else items, rng)
            item_id = picked_item.get("id", c_id)
            raw_tags = picked_item.get("tags", [])
        else:
            item_id = c_id
            raw_tags = chosen.get("tags", [])

        # 依据剩余手部容量严格过滤变体标签，杜绝 1+2>2 或双手占用时输出手持标签
        if raw_tags:
            valid_tags = [
                t for t in raw_tags
                if isinstance(t, dict)
                and t.get("facts", {}).get("hands_required", 0) <= available_hands
                and (available_hands > 0 or t.get("facts", {}).get("prop_usage") != "handheld")
                and (
                    not (_is_random(prop_style) or _is_auto(prop_style))
                    or _is_tag_compatible_with_nudity(t, nudity_level_code, slot_name="props", item_id=c_id)
                )
            ]
            if valid_tags:
                raw_tags = valid_tags

        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []

        tags_str, sampled = self._to_sampled_tags(selected, item_id, "prop")
        prov = TagProvenance(item_id=item_id, kind="prop", semantic_ids=(f"prop:{item_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=item_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(item_id, "random" if _is_random(prop_style) else "explicit"),
        )

    def sample_prop(
        self,
        prop_style: str,
        rng: Random,
        context: Optional[str] = None,
        pose_id: Optional[str] = None,
        hand_state: Optional[str] = None,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_prop_result(
            prop_style,
            rng,
            context=context,
            pose_id=pose_id,
            hand_state=hand_state,
            nudity_level_code=nudity_level_code,
        )
        return list(res.tags) if res else []

    # ─── 服装扩展梯度采样辅助方法 ───

    def list_sfw_exposure_tiers(self) -> List[str]:
        data = self._load("clothing")
        return [t.get("name_zh", t.get("id", "")) for t in data.get("sfw_exposure_tiers", [])]

    def list_cloth_transparency_tiers(self) -> List[str]:
        data = self._load("clothing")
        return [t.get("name_zh", t.get("id", "")) for t in data.get("cloth_transparency_tiers", [])]

    def list_lingerie_wardrobe(self) -> List[str]:
        data = self._load("clothing")
        return [t.get("name_zh", t.get("id", "")) for t in data.get("lingerie_wardrobe", [])]

    def sample_sfw_exposure_result(self, tier: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(tier):
            return None
        data = self._load("clothing")
        tiers = data.get("sfw_exposure_tiers", [])
        if not tiers:
            return None
        if _is_random(tier):
            chosen = self._pick_one(tiers, rng)
            self._record_pool_sampling_rationale(chosen, tiers, mode="uniform_random")
        else:
            chosen = next((t for t in tiers if t.get("name_zh") == tier or t.get("id") == tier or t.get("name") == tier), None)
            if not chosen:
                raise DataSelectionError(f"Unknown sfw exposure tier: {tier!r}")
        if not chosen:
            return None
        c_id = chosen.get("id", "sfw_exposure_default")
        raw_tags = chosen.get("tags", [])
        selected = raw_tags[:2] if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "clothing_extension")
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(tier) else "explicit"),
        )

    def sample_sfw_exposure(self, tier: str, rng: Random) -> List[str]:
        res = self.sample_sfw_exposure_result(tier, rng)
        return list(res.tags) if res else []

    def sample_cloth_transparency_result(self, tier: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(tier):
            return None
        data = self._load("clothing")
        tiers = data.get("cloth_transparency_tiers", [])
        if not tiers:
            return None
        if _is_random(tier):
            chosen = self._pick_one(tiers, rng)
            self._record_pool_sampling_rationale(chosen, tiers, mode="uniform_random")
        else:
            chosen = next((t for t in tiers if t.get("name_zh") == tier or t.get("id") == tier or t.get("name") == tier), None)
            if not chosen:
                raise DataSelectionError(f"Unknown cloth transparency tier: {tier!r}")
        if not chosen:
            return None
        c_id = chosen.get("id", "cloth_transparency_default")
        raw_tags = chosen.get("tags", [])
        selected = raw_tags[:2] if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "clothing_extension")
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(tier) else "explicit"),
        )

    def sample_cloth_transparency(self, tier: str, rng: Random) -> List[str]:
        res = self.sample_cloth_transparency_result(tier, rng)
        return list(res.tags) if res else []

    def sample_lingerie_wardrobe_result(self, cat: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(cat):
            return None
        data = self._load("clothing")
        wardrobe = data.get("lingerie_wardrobe", [])
        if not wardrobe:
            return None
        if _is_random(cat):
            chosen = self._pick_one(wardrobe, rng)
            self._record_pool_sampling_rationale(chosen, wardrobe, mode="uniform_random")
        else:
            chosen = next((w for w in wardrobe if w.get("name_zh") == cat or w.get("id") == cat or w.get("name") == cat), None)
            if not chosen:
                raise DataSelectionError(f"Unknown lingerie wardrobe category: {cat!r}")
        if not chosen:
            return None
        c_id = chosen.get("id", "lingerie_wardrobe_default")
        raw_tags = chosen.get("tags", [])
        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "clothing_extension")
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(cat) else "explicit"),
        )

    def sample_lingerie_wardrobe(self, cat: str, rng: Random) -> List[str]:
        res = self.sample_lingerie_wardrobe_result(cat, rng)
        return list(res.tags) if res else []

    # ─── 槽位 14: 人格角色卡 ───

    def sample_character_result(self, character_role: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(character_role):
            return None
        data = self._load("characters")
        chars = data.get("characters", [])
        if not chars:
            return None

        if _is_random(character_role):
            pool = [c for c in chars if c.get("id") != "none"]
            chosen = self._sample_from_candidates("character", pool, rng, profile=context_profile, context=context)
        else:
            chosen = _match_item(chars, character_role)
            if not chosen:
                raise DataSelectionError(f"Unknown character role: {character_role!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        raw_tags = chosen.get("tags", [])
        selected = raw_tags[:2] if raw_tags else []
        c_id = chosen.get("id", "character_default")
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "character")
        prov = TagProvenance(item_id=c_id, kind="character", semantic_ids=(f"character:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if _is_random(character_role) else "explicit"),
        )

    def sample_character(self, character_role: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_character_result(character_role, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 15: 液体体液系统 ───

    def sample_liquid_result(
        self,
        liquid_effect: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(liquid_effect):
            return None
        data = self._load("nudity_levels")
        liquids = data.get("body_liquids", []) + data.get("environmental_liquids", [])
        if not liquids:
            liquids = data.get("liquid_effects", [])
        if not liquids:
            return None

        is_rand = _is_random(liquid_effect) or _is_auto(liquid_effect)
        if is_rand:
            pool = [liq for liq in liquids if liq.get("id") != "none"]
            excluded_ids = self.get_excluded_candidate_ids("liquids", nudity_level_code=nudity_level_code)
            chosen = self._sample_from_candidates(
                "liquids",
                pool,
                rng,
                profile=context_profile,
                context=context,
                excluded_ids=excluded_ids,
            )
        else:
            chosen = _match_item(liquids, liquid_effect)
            if not chosen:
                raise DataSelectionError(f"Unknown liquid effect: {liquid_effect!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        c_id = chosen.get("id", "liquid_default")
        raw_tags = list(chosen.get("tags", []))
        if is_rand and nudity_level_code in ("L1", "L2"):
            filtered_tags = [
                tg for tg in raw_tags
                if _is_tag_compatible_with_nudity(tg, nudity_level_code, slot_name="liquids", item_id=c_id)
            ]
            if filtered_tags:
                raw_tags = filtered_tags
        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "liquid")
        prov = TagProvenance(item_id=c_id, kind="liquid", semantic_ids=(f"liquid:{c_id}",))
        return SampleResult(
            tags=tags_str,
            item_id=c_id,
            provenance=prov,
            sampled_tags=sampled,
            sampling_rationale=self._pop_sampling_rationale(c_id, "random" if is_rand else "explicit"),
        )

    def sample_liquid(
        self,
        liquid_effect: str,
        rng: Random,
        context: Optional[str] = None,
        nudity_level_code: Optional[str] = None,
    ) -> List[str]:
        res = self.sample_liquid_result(liquid_effect, rng, context, nudity_level_code=nudity_level_code)
        return list(res.tags) if res else []

    # ─── 风格配方与预设 ───

    def get_style_recipe(self, recipe_name: str, rng: Optional[Random] = None) -> Optional[Dict[str, Any]]:
        if _is_none(recipe_name):
            return None
        data = self._load("style_recipes")
        recipes = data.get("recipes", [])
        if not recipes:
            return None
        if _is_random(recipe_name):
            if rng is None:
                rng = Random(42)
            return rng.choice(recipes)
        match = _match_item(recipes, recipe_name, "style_recipes")
        if not match:
            raise DataSelectionError(f"Unknown style recipe: {recipe_name!r}")
        return match

    def get_preset(self, preset_id: str, rng: Random) -> Optional[Dict[str, Any]]:
        if _is_none(preset_id):
            return None
        data = self._load("presets")
        presets = data.get("presets", [])
        if not presets:
            return None
        if _is_random(preset_id):
            return self._pick_one(presets, rng)
        match = _match_item(presets, preset_id, "presets")
        if not match:
            raise DataSelectionError(f"Unknown preset: {preset_id!r}")
        return match



    def get_negative_prompt(self) -> str:
        data = self._load("negative_prompts")
        return data.get("default",
                        "low quality, worst quality, blurry, jpeg artifacts, "
                        "watermark, deformed, bad anatomy, extra limbs")

    # ─── 列举方法（用于 UI 下拉菜单） ───

    def list_scene_categories(self) -> List[str]:
        data = self._load("scenes")
        names = []
        for scene_group in data.get("scenes", []):
            for item in scene_group.get("items", []):
                sub = item.get("label") or item.get("subcategory", "")
                if sub and sub not in ("章节", "内容") and sub not in names:
                    names.append(sub)
        return names

    def list_themes(self) -> List[str]:
        data = self._load("themes")
        return [t.get("name_zh") or t.get("theme_zh", "") for t in data.get("themes", []) if t.get("name_zh") or t.get("theme_zh")]

    def list_clothing_styles(self) -> List[str]:
        data = self._load("clothing")
        return [c.get("name_zh", "") for c in data.get("categories", []) if c.get("name_zh")]

    def list_clothing_states(self) -> List[str]:
        data = self._load("clothing")
        return [s.get("name_zh", "") for s in data.get("clothing_states", []) if s.get("name_zh")]

    def list_makeup_styles(self) -> List[str]:
        data = self._load("makeup")
        return [m.get("name_zh", "") for m in data.get("categories", []) if m.get("name_zh")]

    def list_hairstyles(self) -> List[str]:
        data = self._load("accessories")
        return [h.get("name_zh", "") for h in data.get("hairstyles", []) if h.get("name_zh")]

    def list_jewelry(self) -> List[str]:
        data = self._load("accessories")
        return [j.get("name_zh", "") for j in data.get("headwear_jewelry", []) if j.get("name_zh")]

    def list_shot_types(self) -> List[str]:
        data = self._load("shot_types")
        return [s.get("name_zh", "") for s in data.get("shot_types", []) if s.get("name_zh")]

    def list_camera_angles(self) -> List[str]:
        data = self._load("shot_types")
        return [a.get("name_zh", "") for a in data.get("camera_angles", []) if a.get("name_zh")]

    def list_pose_categories(self) -> List[str]:
        data = self._load("poses")
        return [c.get("name_zh", "") for c in data.get("pose_categories", []) if c.get("name_zh")]

    def list_expression_moods(self) -> List[str]:
        data = self._load("expressions")
        return [e.get("name", "") for e in data.get("emotions", []) if e.get("name")]

    def list_film_stocks(self) -> List[str]:
        data = self._load("film_stocks")
        stocks = data.get("film_stocks", [])
        return [f.get("name_zh", "") for f in stocks if f.get("name_zh")]

    def list_lighting_presets(self) -> List[str]:
        data = self._load("lighting")
        return [p.get("name", "") for p in data.get("preset_combos", []) if p.get("name")]

    def list_tattoo_styles(self) -> List[str]:
        data = self._load("tattoos")
        return [t.get("name_zh", "") for t in data.get("categories", []) if t.get("name_zh") and t.get("id") != "none"]

    def list_prop_styles(self) -> List[str]:
        data = self._load("props")
        return [p.get("name_zh", "") for p in data.get("categories", []) if p.get("name_zh") and p.get("id") != "none"]

    def list_character_roles(self) -> List[str]:
        data = self._load("characters")
        return [c.get("name_zh", "") for c in data.get("characters", []) if c.get("name_zh") and c.get("id") != "none"]

    def list_liquid_effects(self) -> List[str]:
        data = self._load("nudity_levels")
        return [liq.get("name_zh", "") for liq in data.get("liquid_effects", []) if liq.get("name_zh") and liq.get("id") != "none"]

    def list_imperfection_types(self) -> List[str]:
        data = self._load("imperfections")
        return [i.get("name_zh", "") for i in data.get("categories", []) if i.get("name_zh")]

    def list_preset_names(self) -> List[str]:
        data = self._load("presets")
        return [f"{p.get('id', '')} ({p.get('name_zh', '')})" for p in data.get("presets", [])]

    def list_style_recipes(self) -> List[str]:
        data = self._load("style_recipes")
        return [r.get("style_name", "") for r in data.get("recipes", []) if r.get("style_name")]
