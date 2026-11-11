"""
outfit_completion.py — 随机/自动服装完整穿搭检查、相容补件、状态/扩展兼容性与受控回退器 (v1.2.1 M2)

职责边界：
1. 按 target nudity_level_code & nudity_branch 评估服装覆盖完整性；
2. 严守豁免契约：服装=None、显式单品、外部服装覆盖绝不触发跨类自动补全；
3. 状态=None 且 款式=随机/自动 时，不补状态/扩展，但照常补齐缺失基础件；
4. 多构件套装/制服子采样时保底核心上下装或连体款，并与所选服装状态兼容；
5. 重/厚实/硬质面料禁止叠加薄透扩展（cloth_transparency_tiers / l2_sheer_hint）；
6. 找不到相容补件时触发最小安全组合回退，均使用独立 RNG 子流且完整保留来源 ID。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from random import Random
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

if __package__:
    from .conflict_resolver import GarmentCarrierEntity, is_garment_compatible_with_state
    from .context_affinity import compute_context_profile, compute_slot_distribution, sample_categorical
    from .models import (
        ContextProfile,
        PromptAtom,
        SampledTag,
        SemanticFacts,
        SpanType,
        TagProvenance,
    )
else:
    from lib.conflict_resolver import GarmentCarrierEntity, is_garment_compatible_with_state
    from lib.context_affinity import compute_context_profile, compute_slot_distribution, sample_categorical
    from lib.models import (
        ContextProfile,
        PromptAtom,
        SampledTag,
        SemanticFacts,
        SpanType,
        TagProvenance,
    )

# ---------------------------------------------------------------------------
# 常量与匹配规则
# ---------------------------------------------------------------------------

L1_BANNED_TEXT_RE = re.compile(
    r"\b(bra|panties|breasts|pussy|nude|naked|undressed|topless|upskirt|thong|g-string|boudoir|lingerie|cleavage|underboob|sideboob)\b"
)

HEAVY_OPAQUE_CATEGORY_MARKERS: Tuple[str, ...] = (
    "knit",
    "sweater",
    "down_jacket",
    "winter_parka",
    "duffel_coat",
    "knight_armor",
    "berserker_armor",
    "mecha_",
    "firefighter_gear",
    "denim_",
    "leather_",
    "latex_",
    "tactical",
    "rainwear",
    "military_",
    "outerwear_coat",
    "outerwear_overcoat",
    "outerwear_jacket",
    "hoodie",
    "sweatshirt",
    "combat_tactical",
    "waist_apron",
    "apron_dress",
    "racing_suit",
    "zentai_suit",
    "lab_coat",
    "business_suit",
)

HEAVY_OPAQUE_TEXT_RE = re.compile(
    r"\b(knit|knitted|chunky knit|cable knit|sweater|wool|woolen|fleece|tweed|corduroy|flannel|denim|jeans|"
    r"leather|latex|vinyl|rubber|pvc|armor|armored|plated|plate|metal|steel|chainmail|mecha|exoskeleton|"
    r"down jacket|parka|duffel|overcoat|puffer|quilted|raincoat|tactical)\b"
)

SWIMWEAR_SET_RE = re.compile(r"\b(bikini|swimsuit|monokini)\b")
SWIMWEAR_SINGLE_PART_RE = re.compile(r"\b(top|bottom|bottoms|panties|briefs|bra|thong|g-string)\b")

_SKIRT_TEXT_RE = re.compile(r"\b(skirt|miniskirt|microskirt|pleated|pettiskirt|tutu|sundress|dress|qipao|cheongsam|kimono|yukata|furisode|hanfu|hanbok|apron|gown)\b")
_PANTS_TEXT_RE = re.compile(r"\b(pants|trousers|jeans|shorts|hot pants|leggings|bloomers|slacks|denim|overalls|dungarees|jumpsuit)\b")
_DRESS_CARRIER_TEXT_RE = re.compile(
    r"\b(dress|gown|sundress|underdress|pinafore|qipao|cheongsam|kimono|yukata|furisode|hanfu|hanbok|robe|nightdress|nightgown|slip)\b"
)
_NON_DRESS_ONE_PIECE_RE = re.compile(
    r"\b(dungarees|overalls|jumpsuit|romper|catsuit|bodysuit|plugsuit|skinsuit|techsuit|zentai|swimsuit|sukumizu|leotard|racing suit|bunny suit)\b"
)
_BRA_CARRIER_TEXT_RE = re.compile(r"\b(bra|bralette|bustier|bikini top|sports bra|cage bra|lace bra)\b")
_NON_BRA_UNDERWEAR_RE = re.compile(r"\b(panties only|nipple covers|pasties|braless|thong|g-string|garter belt|panties|bloomers|briefs)\b")
_PANTIES_CARRIER_TEXT_RE = re.compile(r"\b(panties|thong|g-string|bloomers|briefs|bikini bottoms|lingerie set|boyshorts|hipsters|underwear)\b")
_BUTTON_TEXT_RE = re.compile(r"\b(shirt|blouse|blazer|cardigan|jacket|coat|vest|uniform|polo|tunic|trench)\b")

_OVERRIDE_UPPER_TEXT_RE = re.compile(
    r"\b(sweater|shirt|blouse|top|t-shirt|tee|hoodie|sweatshirt|camisole|corset|vest|blazer|jacket|bodice)\b"
)
_OVERRIDE_LOWER_TEXT_RE = re.compile(
    r"\b(skirt|miniskirt|microskirt|pants|trousers|jeans|shorts|bloomers|leggings|bottoms|chima)\b"
)
_OVERRIDE_ONE_PIECE_TEXT_RE = re.compile(
    r"\b(dress|gown|qipao|cheongsam|hanfu|kimono|yukata|furisode|nightgown|slip|catsuit|bodysuit|swimsuit|bunny suit|rashguard|robe|hanbok|uniform|outfit|suit)\b"
)

EVERYDAY_TOP_IDS: Tuple[str, ...] = (
    "t_shirt",
    "shirts_blouses",
    "sweatshirt",
    "sweater_casual",
    "knit_sweater",
    "hoodie",
    "crop_top",
    "knit_vest",
    "tops_specialty",
    "strapless_top",
    "sailor_shirt",
    "sportswear_active",
)

EVERYDAY_BOTTOM_IDS: Tuple[str, ...] = (
    "pleated_skirt",
    "miniskirt",
    "plaid_skirt",
    "layered_skirt",
    "long_skirt",
    "pencil_skirt",
    "skirts_general",
    "denim_shorts",
    "hot_pants",
    "bottom_shorts_casual",
    "bottom_jeans_casual",
    "denim_jeans",
    "trousers_general",
    "black_leggings",
    "suspender_skirt",
    "pettiskirt",
    "leather_skirt",
)

EVERYDAY_ONE_PIECE_IDS: Tuple[str, ...] = (
    "dress_casual",
    "summer_sundress",
    "sweater_dress",
    "pleated_dress",
    "floral_dress_black",
    "floral_dress_white",
    "chiffon_dress",
    "off_shoulder_dress",
    "sleeveless_dress",
    "sundress_layered",
    "dress_backless",
    "cocktail_dress",
    "halter_dress",
    "strapless_dress",
)

# 按结构化 L2/L3 分支严格匹配的扩展层级 ID（空元组表示该分支禁止叠加无关露肤扩展）
BRANCH_PREFERRED_EXPOSURE_IDS: Dict[str, Tuple[str, ...]] = {
    "l2_collar_neckline": ("collar_cutout", "subtle_cleavage"),
    "l2_shoulder_strap": ("off_shoulder",),
    "l2_leg_slit": ("high_slit",),
    "l2_midriff_waist": ("crop_midriff", "waist_cutouts", "open_sides"),
    "l2_backless": ("plunging_backless",),
    "l2_sleeve_wrist": (),
    "l3_topless_skirt_kept": ("high_slit",),
    "l3_topless_pants_kept": (),
    "l3_open_shirt_breasts": ("multipoint_cutout", "open_sides"),
    "l3_raised_top_breasts": ("crop_midriff", "waist_cutouts"),
    "l3_pulled_down_dress": ("high_slit", "plunging_backless"),
    "l3_bra_pushed_up": (),
    "l3_panties_only": (),
    "l3_bikini_top_removed": (),
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompletionExemption:
    is_exempt: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class OutfitAssessment:
    is_complete: bool
    has_upper: bool
    has_lower: bool
    has_one_piece: bool
    has_outerwear: bool
    has_legwear_or_footwear: bool
    has_underwear_only: bool
    missing_parts: Tuple[str, ...] = ()
    violating_item_ids: Tuple[str, ...] = ()
    reason: str = ""


# ---------------------------------------------------------------------------
# 基础提取与子流 RNG 工具
# ---------------------------------------------------------------------------

def derive_deterministic_child_rng(
    base_rng: Random,
    *salt_parts: str,
) -> Random:
    """在不消耗 base_rng 任何状态的前提下，确定性派生独立子流 Random。"""
    state_head = repr(base_rng.getstate()[1][:6])
    payload = "|".join([state_head, *[str(p) for p in salt_parts]])
    seed_int = int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")
    return Random(seed_int)


def _resolve_selection_mode_local(val: Any) -> str:
    if val is None:
        return "none"
    s = str(val).strip().lower()
    if s in ("none", "无", "无 (none)", "none (无)", "null", "false", ""):
        return "none"
    if s in ("random", "随机", "随机 (random)", "random (随机)"):
        return "random"
    if s in ("auto", "自动", "自动 (auto)", "auto (自动)", "自动联动裸露等级 (auto link nudity)", "自动联动裸露等级"):
        return "auto"
    return "explicit"


def check_completion_exemption(
    style_val: Any,
    has_external_clothing_override: bool = False,
) -> CompletionExemption:
    """检查是否属于自动补全豁免入口（服装=None / 显式单品 / 外部覆盖）。"""
    mode = _resolve_selection_mode_local(style_val)
    if mode == "none":
        return CompletionExemption(is_exempt=True, reason="style_none")
    if has_external_clothing_override:
        return CompletionExemption(is_exempt=True, reason="external_clothing_override")
    if mode not in ("random", "auto"):
        return CompletionExemption(is_exempt=True, reason="explicit_style")
    return CompletionExemption(is_exempt=False, reason=None)


def _extract_facts_dict(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj.get("facts") or {})
    f = getattr(obj, "facts", None)
    if isinstance(f, dict):
        return dict(f)
    if isinstance(f, SemanticFacts):
        return {
            "piece_role": f.piece_role,
            "covered_regions": list(f.covered_regions or ()),
            "exposed_regions": list(f.exposed_regions or ()),
            "visible_regions": list(f.visible_regions or ()),
            "garment_topologies": list(f.garment_topologies or ()),
            "allowed_nudity_levels": list(f.allowed_nudity_levels or ()),
            "style_genre": f.style_genre,
            "garment_states": list(f.garment_states or ()),
            "semantic_role": f.semantic_role,
            "mutex_groups": list(f.mutex_groups or ()),
        }
    return {}


def _extract_text(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("text", "")).strip()
    return str(getattr(obj, "text", obj)).strip()


def _extract_id(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("id", "")).strip()
    return str(getattr(obj, "id", getattr(obj, "atom_id", "")) or "").strip()


def _extract_provenance_item_id(obj: Any) -> str:
    if isinstance(obj, dict):
        prov = obj.get("provenance")
        if isinstance(prov, dict):
            return str(prov.get("item_id", "") or "")
        return ""
    prov = getattr(obj, "provenance", None)
    if prov is not None and getattr(prov, "item_id", None):
        return str(prov.item_id)
    src = getattr(obj, "source_item_id", None)
    if src:
        return str(src)
    orig = getattr(obj, "origin", None)
    if orig is not None and getattr(orig, "selected_id", None):
        return str(orig.selected_id)
    return ""


def _is_linkage_general_or_extension(obj: Any) -> bool:
    item_id = _extract_provenance_item_id(obj)
    if item_id in ("linkage_general",):
        return True
    if isinstance(obj, dict):
        prov = obj.get("provenance") or {}
        if isinstance(prov, dict):
            sem_ids = prov.get("semantic_ids") or ()
            if "override:linkage" in sem_ids:
                return False
            if prov.get("kind") in ("clothing_state", "clothing_extension"):
                return True
    else:
        prov = getattr(obj, "provenance", None)
        kind = getattr(prov, "kind", None) if prov is not None else None
        sem_ids = getattr(prov, "semantic_ids", ()) if prov is not None else ()
        if "override:linkage" in (sem_ids or ()):
            return False
        if kind in ("clothing_state", "clothing_extension"):
            return True
        orig = getattr(obj, "origin", None)
        if orig is not None and getattr(orig, "selector", None) == "clothing_state":
            # linkage_override 属于覆盖主款文本，其它 clothing_state 仅是状态修饰
            if item_id != getattr(orig, "selected_id", None) or getattr(orig, "selected_id", None) in (
                "normal", "unbuttoned", "slipping_off", "lifted_up", "pulled_down",
                "wet_clinging", "sweat_soaked", "torn_shredded", "disheveled",
                "only_lingerie", "discarded", "taut_tight", "wet_pure",
                "heart_cutout", "back_cutout", "underboob_cutout", "off_shoulder_cut",
                "bare_shoulders", "braless", "underwearless", "undergarment_leotard",
                "skirt_under_kimono", "auto_linkage",
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# 等级准入与材质/状态兼容性判断
# ---------------------------------------------------------------------------

def is_tag_eligible_for_nudity_level(tag: Any, nudity_level_code: Optional[str]) -> bool:
    """判定单条服装标签在给定裸露等级下是否准入。"""
    if not nudity_level_code:
        return True
    facts = _extract_facts_dict(tag)
    allowed = facts.get("allowed_nudity_levels") or []
    if allowed and nudity_level_code not in allowed:
        return False

    piece_role = facts.get("piece_role")
    if piece_role == "none":
        return False

    if nudity_level_code == "L1":
        if piece_role == "underwear":
            return False
        if facts.get("style_genre") == "boudoir_lingerie":
            return False
        visible = facts.get("visible_regions") or []
        if any(r in visible for r in ("intimate_lower_body", "cleavage", "breasts", "crotch", "buttocks", "underboob", "sideboob")):
            return False
        exposed = facts.get("exposed_regions") or []
        if any(r in exposed for r in ("genitals", "anus", "buttocks", "breasts", "cleavage", "underboob", "sideboob")):
            return False
        gstates = facts.get("garment_states") or []
        if any(gs in gstates for gs in ("lifted", "opened", "removed", "lifted_skirt", "discarded")):
            return False
        txt_low = _extract_text(tag).lower()
        if txt_low and L1_BANNED_TEXT_RE.search(txt_low):
            return False

    return True


def is_heavy_or_rigid_garment(
    clothing_id: str,
    garment_tags: Sequence[Any] = (),
) -> bool:
    """判定主款/活跃衣物是否属于厚重针织、羊毛、皮革、牛仔、盔甲或硬质阻透材质。"""
    cid_low = (clothing_id or "").lower()
    if cid_low == "armored_skirt":
        return False
    if cid_low and any(marker in cid_low for marker in HEAVY_OPAQUE_CATEGORY_MARKERS):
        return True
    for t in garment_tags:
        if _is_linkage_general_or_extension(t):
            continue
        facts = _extract_facts_dict(t)
        if facts.get("piece_role") in ("accessory", "legwear", "footwear", "none"):
            continue
        txt_low = _extract_text(t).lower()
        if txt_low and HEAVY_OPAQUE_TEXT_RE.search(txt_low):
            return True
    return False


def filter_extensions_for_garments(
    ext_tiers: Sequence[Dict[str, Any]],
    *,
    kind: str,
    nudity_level_code: Optional[str],
    clothing_id: str,
    garment_tags: Sequence[Any] = (),
    nudity_branch: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """根据材质、服装形制与裸露分支严格过滤相容扩展库层级。"""
    tiers = list(ext_tiers or ())
    if not tiers:
        return []

    heavy = is_heavy_or_rigid_garment(clothing_id, garment_tags)
    if kind == "cloth_transparency":
        if heavy:
            return []
        return tiers

    if kind == "sfw_exposure":
        filtered = list(tiers)
        cid_low = (clothing_id or "").lower()
        if heavy and any(k in cid_low for k in ("knight_armor", "berserker_armor", "mecha_")):
            filtered = [x for x in filtered if x.get("id") not in ("off_shoulder", "subtle_cleavage", "high_slit", "plunging_backless")]

        if garment_tags:
            garment_roles: Set[str] = set()
            garment_topos: Set[str] = set()
            garment_txts: List[str] = []
            for gt in garment_tags:
                gf = _extract_facts_dict(gt)
                if gf.get("piece_role"):
                    garment_roles.add(str(gf["piece_role"]))
                for gp in gf.get("garment_topologies") or ():
                    garment_topos.add(str(gp))
                gt_txt = _extract_text(gt).lower()
                if gt_txt:
                    garment_txts.append(gt_txt)
            joined_gt = " ".join(garment_txts)
            has_skirt_or_dress = bool(
                ("bottom_skirt" in garment_topos)
                or ("one_piece" in garment_roles)
                or ("one_piece" in garment_topos)
                or ("ensemble" in garment_roles)
                or ("ensemble_outfit" in garment_topos)
                or _SKIRT_TEXT_RE.search(joined_gt)
            )
            has_upper_or_dress = bool(
                garment_roles & {"top", "one_piece", "ensemble", "outerwear"}
                or garment_topos & {"top", "one_piece", "ensemble_outfit", "outerwear"}
            )
            underwear_only_garments = bool(
                garment_roles
                and garment_roles <= {"underwear", "accessory", "legwear", "footwear", "unspecified"}
                and not (garment_topos & {"top", "bottom_skirt", "bottom_pants", "one_piece", "ensemble_outfit", "outerwear"})
            )
            if not has_skirt_or_dress:
                filtered = [x for x in filtered if x.get("id") != "high_slit"]
            if underwear_only_garments:
                filtered = [
                    x for x in filtered
                    if x.get("id") not in (
                        "collar_cutout", "off_shoulder", "subtle_cleavage", "high_slit",
                        "plunging_backless", "crop_midriff", "waist_cutouts", "open_sides",
                    )
                ]
            elif garment_roles and not has_upper_or_dress:
                filtered = [
                    x for x in filtered
                    if x.get("id") not in (
                        "collar_cutout", "off_shoulder", "subtle_cleavage", "plunging_backless",
                        "crop_midriff", "waist_cutouts", "open_sides",
                    )
                ]
            if ("backless" in cid_low or "backless" in joined_gt) and not ("midriff" in joined_gt or "crop" in joined_gt):
                filtered = [
                    x for x in filtered
                    if x.get("id") not in ("crop_midriff", "waist_cutouts", "open_sides", "off_shoulder", "subtle_cleavage", "collar_cutout")
                ]

        if nudity_branch and nudity_branch in BRANCH_PREFERRED_EXPOSURE_IDS:
            pref_ids = set(BRANCH_PREFERRED_EXPOSURE_IDS[nudity_branch])
            return [x for x in filtered if x.get("id") in pref_ids]

        # 若明确传入 L2/L3 分支且未匹配到任何容许分支，禁止回退放行跨分支露肤扩展
        if nudity_branch and str(nudity_branch).startswith(("l2_", "l3_")):
            branch_matched = []
            for x in filtered:
                for xt in x.get("tags") or ():
                    xb = ((xt.get("facts") or {}).get("nudity_branches") or ()) if isinstance(xt, dict) else ()
                    if xb and nudity_branch in xb:
                        branch_matched.append(x)
                        break
            return branch_matched

        return filtered

    return tiers


def _make_dummy_atom(tag: Any, style_id: str = "candidate") -> PromptAtom:
    """将词条/SampledTag 转为用于 is_garment_compatible_with_state 的轻量 PromptAtom。"""
    if isinstance(tag, PromptAtom):
        return tag
    facts_raw = getattr(tag, "facts", None) if not isinstance(tag, dict) else tag.get("facts")
    if isinstance(facts_raw, SemanticFacts):
        facts = facts_raw
    elif isinstance(facts_raw, dict):
        facts = SemanticFacts.from_dict(facts_raw)
    else:
        facts = SemanticFacts()
    tid = _extract_id(tag) or style_id
    txt = _extract_text(tag)
    return PromptAtom(
        atom_id=tid,
        text=txt,
        span_type=SpanType.PLAIN,
        source_slot="clothing",
        source_item_id=style_id,
        id=tid,
        facts=facts,
    )


def is_tags_compatible_with_state(
    tags: Sequence[Any],
    state_id: Optional[str],
    state_tags: Sequence[Any] = (),
    style_id: str = "candidate",
) -> bool:
    """复用 ConflictResolver 的 is_garment_compatible_with_state 检查服装构件是否承载给定状态。"""
    if not state_id or state_id in ("normal", "auto_link", "auto_linkage", "linkage_override", "linkage_general"):
        return True
    if not tags:
        return False

    non_detail_tags = [
        t for t in tags
        if _extract_facts_dict(t).get("piece_role") not in ("accessory", "legwear", "footwear", "none")
    ]
    if not non_detail_tags:
        return False

    atoms = [_make_dummy_atom(t, style_id=style_id) for t in non_detail_tags]
    entity = GarmentCarrierEntity(
        entity_id=f"completion_probe:{style_id}",
        selector="clothing",
        selected_id=style_id,
        member_atoms=atoms,
    )
    if state_tags:
        for st in state_tags:
            st_atom = _make_dummy_atom(st, style_id=state_id)
            if not is_garment_compatible_with_state(entity, state_id, st_atom):
                return False
        return True
    return is_garment_compatible_with_state(entity, state_id, None)


def _is_auxiliary_expanded_component(obj: Any) -> bool:
    """判定元素是否属于动态槽位扩展/必选配件构件（如鞋履/配饰），严禁伪装成主上下装载体。"""
    if isinstance(obj, dict):
        role = obj.get("role")
    else:
        role = getattr(obj, "role", None)
    if role in ("dynamic_slot_component", "mandatory_component"):
        return True
    tid = _extract_id(obj)
    return "__slot_" in tid or "__comp_" in tid


def _tag_is_button_capable(tag: Any) -> bool:
    txt_low = _extract_text(tag).lower()
    if _BUTTON_TEXT_RE.search(txt_low):
        return True
    role = _extract_facts_dict(tag).get("piece_role")
    return role == "outerwear" and "armor" not in txt_low


def _tag_is_skirt_capable(tag: Any, cat_id: str = "") -> bool:
    facts = _extract_facts_dict(tag)
    topos = set(facts.get("garment_topologies") or ())
    txt_low = _extract_text(tag).lower()
    if "bottom_skirt" in topos:
        return True
    if "bottom_pants" in topos and not _SKIRT_TEXT_RE.search(txt_low):
        return False
    if "dress" in topos:
        return True
    if _SKIRT_TEXT_RE.search(txt_low) and not _PANTS_TEXT_RE.search(txt_low):
        return True
    cid_low = (cat_id or "").lower()
    return "skirt" in cid_low or cid_low in ("qipao", "kimono", "yukata", "furisode", "hanfu", "hanbok", "summer_sundress", "dress_casual")


def _tag_is_pants_capable(tag: Any, cat_id: str = "") -> bool:
    facts = _extract_facts_dict(tag)
    topos = set(facts.get("garment_topologies") or ())
    txt_low = _extract_text(tag).lower()
    if "bottom_skirt" in topos and "bottom_pants" not in topos:
        return False
    if _PANTS_TEXT_RE.search(txt_low) and not _SKIRT_TEXT_RE.search(txt_low):
        return True
    cid_low = (cat_id or "").lower()
    if any(k in cid_low for k in ("pants", "jeans", "trousers", "shorts", "leggings", "dungarees")):
        return True
    return "bottom_pants" in topos and facts.get("piece_role") in ("bottom", "one_piece") and cid_low != "bottoms_specialty"


def _tag_is_dress_carrier(tag: Any, cat_id: str = "") -> bool:
    facts = _extract_facts_dict(tag)
    role = facts.get("piece_role")
    topos = set(facts.get("garment_topologies") or ())
    covered = set(facts.get("covered_regions") or ())
    txt_low = _extract_text(tag).lower()
    cid_low = (cat_id or "").lower()
    # 独立 top + bottom 拼装绝不是连衣裙载体
    if role in ("top", "bottom", "underwear", "outerwear", "accessory", "legwear", "footwear", "none"):
        return False
    is_single_body = (role in ("one_piece", "ensemble")) or ("one_piece" in topos) or ({"upper_torso", "lower_torso"} <= covered)
    if not is_single_body:
        return False
    if _NON_DRESS_ONE_PIECE_RE.search(txt_low):
        return False
    if _DRESS_CARRIER_TEXT_RE.search(txt_low):
        return True
    return (
        "dress" in cid_low
        or cid_low in EVERYDAY_ONE_PIECE_IDS
        or cid_low in ("qipao", "kimono", "yukata", "furisode", "hanfu", "modern_chinese", "silk_robe", "camisole_slip", "traditional_eastern_gown", "sari", "ao_dai", "uchikake")
    )


def _tag_is_bra_carrier(tag: Any, cat_id: str = "") -> bool:
    txt_low = _extract_text(tag).lower()
    if _BRA_CARRIER_TEXT_RE.search(txt_low) and "braless" not in txt_low:
        return True
    if _NON_BRA_UNDERWEAR_RE.search(txt_low):
        return False
    cid_low = (cat_id or "").lower()
    return cid_low == "lingerie_bra" and "pasties" not in txt_low and "nipple" not in txt_low


def _tag_is_panties_carrier(tag: Any, cat_id: str = "") -> bool:
    txt_low = _extract_text(tag).lower()
    if _PANTIES_CARRIER_TEXT_RE.search(txt_low):
        return True
    return (cat_id or "").lower() == "lingerie_panties"


# ---------------------------------------------------------------------------
# 完整性评估 (assess_clothing_completeness)
# ---------------------------------------------------------------------------

def assess_clothing_completeness(
    items: Sequence[Any],
    state_items: Sequence[Any] = (),
    nudity_level_code: Optional[str] = "L1",
    nudity_branch: Optional[str] = None,
) -> OutfitAssessment:
    """
    评估当前衣物集合在指定 (nudity_level_code, nudity_branch) 下的完整度。
    支持 SampledTag、原始 dict tag 以及 Resolved PromptAtom。
    """
    lvl = nudity_level_code or "L1"
    if lvl in ("L5", "L6"):
        return OutfitAssessment(
            is_complete=True,
            has_upper=False,
            has_lower=False,
            has_one_piece=False,
            has_outerwear=False,
            has_legwear_or_footwear=False,
            has_underwear_only=False,
            missing_parts=(),
            reason=f"level_{lvl}_no_clothing_required",
        )

    if lvl == "L4":
        return OutfitAssessment(
            is_complete=True,
            has_upper=False,
            has_lower=False,
            has_one_piece=False,
            has_outerwear=False,
            has_legwear_or_footwear=False,
            has_underwear_only=False,
            missing_parts=(),
            reason="level_L4_no_full_completion",
        )

    has_upper = False
    has_lower = False
    has_one_piece = False
    has_dress_carrier = False
    has_outerwear = False
    has_legwear_or_footwear = False
    has_underwear = False
    has_skirt = False
    has_pants = False
    has_bra = False
    has_panties = False
    violating: List[str] = []

    combined: List[Any] = list(items or ()) + list(state_items or ())
    # 收集实件与 linkage_override 文本（忽略 linkage_general 修饰句、扩展词及动态配件构件）
    for obj in combined:
        if _is_linkage_general_or_extension(obj) or _is_auxiliary_expanded_component(obj):
            if lvl == "L1" and not is_tag_eligible_for_nudity_level(obj, "L1"):
                violating.append(_extract_id(obj) or _extract_text(obj))
            continue

        facts = _extract_facts_dict(obj)
        txt = _extract_text(obj)
        txt_low = txt.lower()
        role = facts.get("piece_role")
        covered = set(facts.get("covered_regions") or ())
        item_id = _extract_provenance_item_id(obj) or _extract_id(obj)

        if not is_tag_eligible_for_nudity_level(obj, lvl):
            violating.append(item_id or txt)
            continue

        if role in ("one_piece", "ensemble"):
            has_one_piece = True
            has_upper = True
            has_lower = True
            if _tag_is_dress_carrier(obj, item_id):
                has_dress_carrier = True
            if _tag_is_skirt_capable(obj, item_id):
                has_skirt = True
            if _tag_is_pants_capable(obj, item_id):
                has_pants = True
            continue

        if role == "top":
            has_upper = True
            if _tag_is_bra_carrier(obj, item_id):
                has_bra = True
            continue

        if role == "bottom":
            has_lower = True
            if _tag_is_skirt_capable(obj, item_id):
                has_skirt = True
            if _tag_is_pants_capable(obj, item_id):
                has_pants = True
            continue

        if role == "outerwear":
            has_outerwear = True
            # outer_layer 本身不算内搭完整上/下装，除非明确覆盖 upper+lower
            if "upper_torso" in covered and "lower_torso" in covered:
                has_one_piece = True
                has_upper = True
                has_lower = True
            continue

        if role in ("legwear", "footwear"):
            has_legwear_or_footwear = True
            continue

        if role in ("accessory", "unspecified", "none"):
            continue

        if role == "underwear":
            has_underwear = True
            if _tag_is_bra_carrier(obj, item_id):
                has_bra = True
            if _tag_is_panties_carrier(obj, item_id):
                has_panties = True
            # L2 整套比基尼/泳衣可自成套装，其余内衣（尤其单件/L1）不算完整日常穿着
            if lvl == "L2" and SWIMWEAR_SET_RE.search(txt_low) and not SWIMWEAR_SINGLE_PART_RE.search(txt_low):
                has_upper = True
                has_lower = True
                has_one_piece = True
            continue

        # 无 piece_role 的结构（例如 clothing_nudity_linkage style_overrides 覆盖主款词条）
        if "upper_torso" in covered and "lower_torso" in covered:
            has_one_piece = True
            has_upper = True
            has_lower = True
            if _tag_is_dress_carrier(obj, item_id):
                has_dress_carrier = True
            if _tag_is_skirt_capable(obj, item_id):
                has_skirt = True
            if _tag_is_pants_capable(obj, item_id):
                has_pants = True
            continue
        if "upper_torso" in covered:
            has_upper = True
        if "lower_torso" in covered:
            has_lower = True
            if _tag_is_skirt_capable(obj, item_id):
                has_skirt = True
            if _tag_is_pants_capable(obj, item_id):
                has_pants = True

        # 文本启发式仅针对无 piece_role 的 style_overrides 实体句
        if not role and txt_low:
            if _OVERRIDE_ONE_PIECE_TEXT_RE.search(txt_low):
                has_one_piece = True
                has_upper = True
                has_lower = True
                if _DRESS_CARRIER_TEXT_RE.search(txt_low) and not _NON_DRESS_ONE_PIECE_RE.search(txt_low):
                    has_dress_carrier = True
                if _tag_is_skirt_capable(obj, item_id):
                    has_skirt = True
            else:
                if _OVERRIDE_UPPER_TEXT_RE.search(txt_low):
                    has_upper = True
                if _BRA_CARRIER_TEXT_RE.search(txt_low):
                    has_bra = True
                if _OVERRIDE_LOWER_TEXT_RE.search(txt_low):
                    has_lower = True
                    if _SKIRT_TEXT_RE.search(txt_low):
                        has_skirt = True
                    if _PANTS_TEXT_RE.search(txt_low):
                        has_pants = True

    has_underwear_only = bool(has_underwear and not has_upper and not has_lower and not has_one_piece)

    if lvl == "L3":
        if nudity_branch == "l3_topless_skirt_kept":
            missing = () if has_skirt else ("bottom_skirt",)
            return OutfitAssessment(
                is_complete=not missing and not violating,
                has_upper=has_upper,
                has_lower=has_lower,
                has_one_piece=has_one_piece,
                has_outerwear=has_outerwear,
                has_legwear_or_footwear=has_legwear_or_footwear,
                has_underwear_only=has_underwear_only,
                missing_parts=missing,
                violating_item_ids=tuple(violating),
                reason="l3_branch_skirt" if missing else "ok",
            )
        if nudity_branch == "l3_topless_pants_kept":
            missing = () if has_pants else ("bottom_pants",)
            return OutfitAssessment(
                is_complete=not missing and not violating,
                has_upper=has_upper,
                has_lower=has_lower,
                has_one_piece=has_one_piece,
                has_outerwear=has_outerwear,
                has_legwear_or_footwear=has_legwear_or_footwear,
                has_underwear_only=has_underwear_only,
                missing_parts=missing,
                violating_item_ids=tuple(violating),
                reason="l3_branch_pants" if missing else "ok",
            )
        if nudity_branch == "l3_bra_pushed_up":
            missing = () if has_bra else ("bra",)
            return OutfitAssessment(
                is_complete=not missing and not violating,
                has_upper=has_upper,
                has_lower=has_lower,
                has_one_piece=has_one_piece,
                has_outerwear=has_outerwear,
                has_legwear_or_footwear=has_legwear_or_footwear,
                has_underwear_only=has_underwear_only,
                missing_parts=missing,
                violating_item_ids=tuple(violating),
                reason="l3_branch_bra" if missing else "ok",
            )
        if nudity_branch in ("l3_open_shirt_breasts", "l3_raised_top_breasts"):
            ok = has_upper or has_dress_carrier
            missing = () if ok else ("top",)
            return OutfitAssessment(
                is_complete=not missing and not violating,
                has_upper=has_upper,
                has_lower=has_lower,
                has_one_piece=has_one_piece,
                has_outerwear=has_outerwear,
                has_legwear_or_footwear=has_legwear_or_footwear,
                has_underwear_only=has_underwear_only,
                missing_parts=missing,
                violating_item_ids=tuple(violating),
                reason="l3_branch_upper" if missing else "ok",
            )
        if nudity_branch == "l3_pulled_down_dress":
            missing = () if has_dress_carrier else ("one_piece",)
            return OutfitAssessment(
                is_complete=not missing and not violating,
                has_upper=has_upper,
                has_lower=has_lower,
                has_one_piece=has_one_piece,
                has_outerwear=has_outerwear,
                has_legwear_or_footwear=has_legwear_or_footwear,
                has_underwear_only=has_underwear_only,
                missing_parts=missing,
                violating_item_ids=tuple(violating),
                reason="l3_branch_dress" if missing else "ok",
            )
        if nudity_branch in ("l3_panties_only", "l3_bikini_top_removed"):
            missing = () if has_panties else ("panties",)
            return OutfitAssessment(
                is_complete=not missing and not violating,
                has_upper=has_upper,
                has_lower=has_lower,
                has_one_piece=has_one_piece,
                has_outerwear=has_outerwear,
                has_legwear_or_footwear=has_legwear_or_footwear,
                has_underwear_only=has_underwear_only,
                missing_parts=missing,
                violating_item_ids=tuple(violating),
                reason="l3_branch_panties" if missing else "ok",
            )
        # 未指定分支：只要未违背 L3 规则即放行（兼容 REP-NUDITY-01 及矩阵测试）
        return OutfitAssessment(
            is_complete=not violating,
            has_upper=has_upper,
            has_lower=has_lower,
            has_one_piece=has_one_piece,
            has_outerwear=has_outerwear,
            has_legwear_or_footwear=has_legwear_or_footwear,
            has_underwear_only=has_underwear_only,
            missing_parts=(),
            violating_item_ids=tuple(violating),
            reason="ok" if not violating else "violating_items",
        )

    # L1 / L2 完整性要求：必须同时具备上装与下装覆盖（或 one_piece / ensemble）且无违规项
    missing_parts: Tuple[str, ...]
    if has_upper and has_lower:
        missing_parts = ()
    elif has_upper and not has_lower:
        missing_parts = ("bottom",)
    elif has_lower and not has_upper:
        missing_parts = ("top",)
    else:
        missing_parts = ("inner_full",)

    is_complete = (not missing_parts) and (not violating)
    reason = "ok" if is_complete else (
        f"missing:{','.join(missing_parts)}" if missing_parts else f"violating:{','.join(violating)}"
    )
    return OutfitAssessment(
        is_complete=is_complete,
        has_upper=has_upper,
        has_lower=has_lower,
        has_one_piece=has_one_piece,
        has_outerwear=has_outerwear,
        has_legwear_or_footwear=has_legwear_or_footwear,
        has_underwear_only=has_underwear_only,
        missing_parts=missing_parts,
        violating_item_ids=tuple(violating),
        reason=reason,
    )


# ---------------------------------------------------------------------------
# 多构件制服/套装子采样修复 (REP-OUTFIT-10)
# ---------------------------------------------------------------------------

def repair_ensemble_subsampling(
    all_base_tags: Sequence[SampledTag],
    chosen_base: Sequence[SampledTag],
    *,
    style_id: str,
    state_id: Optional[str],
    state_tags: Sequence[Any],
    nudity_level_code: str,
    nudity_branch: Optional[str],
    repair_rng: Random,
) -> Tuple[Tuple[SampledTag, ...], bool]:
    """
    对多标签非变体款式（如 blazer_uniform / jk_seifuku / ol_suit / bunny_suit）做子采样修复：
    - 若原 chosen_base 已合法且完整且承载 state_id，原样保留；
    - 若原 chosen_base 漏掉了同一套装内已有的核心上下装/连体款或丢失状态载体（如 blazer_uniform 只抽到领带+长筒袜），
      利用独立的 repair_rng 从 all_base_tags 内部修复成完整、与 state_id 相容的构件集合（不引入外部大类）。
    """
    if not all_base_tags:
        return tuple(chosen_base), False

    legal_pool = [t for t in all_base_tags if is_tag_eligible_for_nudity_level(t, nudity_level_code)]
    if not legal_pool:
        return tuple(chosen_base), False

    chosen_list = list(chosen_base)
    chosen_assessment = assess_clothing_completeness(
        chosen_list, (), nudity_level_code=nudity_level_code, nudity_branch=nudity_branch
    )
    pool_assessment = assess_clothing_completeness(
        legal_pool, (), nudity_level_code=nudity_level_code, nudity_branch=nudity_branch
    )
    state_ok = is_tags_compatible_with_state(chosen_list, state_id, state_tags, style_id=style_id)
    pool_state_ok = is_tags_compatible_with_state(legal_pool, state_id, state_tags, style_id=style_id)
    has_illegal = any(not is_tag_eligible_for_nudity_level(t, nudity_level_code) for t in chosen_list)

    # 判断是否确实需要修复：
    # 1) 抽中了等级违规标签；
    # 2) 全池本身完整 (pool_assessment.is_complete)，但子采样结果残缺 (not chosen_assessment.is_complete)；
    # 3) 全池能承载当前 state_id，但子采样结果丢失状态载体 (not state_ok)。
    needs_core_repair = pool_assessment.is_complete and not chosen_assessment.is_complete
    needs_state_repair = pool_state_ok and not state_ok
    if not (has_illegal or needs_core_repair or needs_state_repair):
        return tuple(chosen_base), False

    legal_chosen = [t for t in chosen_list if is_tag_eligible_for_nudity_level(t, nudity_level_code)]
    order_index = {id(t): idx for idx, t in enumerate(all_base_tags)}

    by_role: Dict[str, List[SampledTag]] = {}
    for t in legal_pool:
        role = _extract_facts_dict(t).get("piece_role") or "unspecified"
        by_role.setdefault(role, []).append(t)

    picked: List[SampledTag] = []

    def _add_unique(tag_obj: Optional[SampledTag]) -> None:
        if tag_obj is not None and tag_obj not in picked:
            picked.append(tag_obj)

    # 若原选择中有状态相容的核心件，优先保留
    def _pick_role(role_candidates: Sequence[SampledTag], prefer_button: bool = False, prefer_skirt: bool = False) -> Optional[SampledTag]:
        if not role_candidates:
            return None
        existing = [t for t in legal_chosen if t in role_candidates]
        cands = existing if existing else list(role_candidates)
        if prefer_button:
            btn = [t for t in cands if is_tags_compatible_with_state([t], state_id, state_tags, style_id=style_id)]
            if not btn and existing:
                btn = [t for t in role_candidates if is_tags_compatible_with_state([t], state_id, state_tags, style_id=style_id)]
            if btn:
                cands = btn
        if prefer_skirt:
            sk = [t for t in cands if _tag_is_skirt_capable(t, style_id)]
            if not sk and existing:
                sk = [t for t in role_candidates if _tag_is_skirt_capable(t, style_id)]
            if sk:
                cands = sk
        return repair_rng.choice(cands)

    need_btn = (state_id == "unbuttoned")
    need_skirt = (state_id == "lifted_up" or nudity_branch == "l3_topless_skirt_kept")
    has_top_and_bottom = bool(by_role.get("top")) and bool(by_role.get("bottom"))
    one_pool = by_role.get("ensemble", []) + by_role.get("one_piece", [])
    has_one_or_ens = bool(one_pool)
    one_pool_state_compatible = (not pool_state_ok) or any(
        is_tags_compatible_with_state([t], state_id, state_tags, style_id=style_id) for t in one_pool
    )

    if nudity_level_code in ("L1", "L2") and (has_top_and_bottom or has_one_or_ens):
        # 优先在 top+bottom 与 one_piece/ensemble 之间与原选保持一致；但若 state_id 要求仅 top/bottom 可承载（如 jk_seifuku unbuttoned），切回 top+bottom
        chosen_has_one = any(t in one_pool for t in legal_chosen)
        if has_one_or_ens and one_pool_state_compatible and (chosen_has_one or not has_top_and_bottom):
            _add_unique(_pick_role(one_pool, prefer_button=need_btn, prefer_skirt=need_skirt))
        elif has_top_and_bottom:
            top_tag = _pick_role(by_role["top"], prefer_button=need_btn)
            bot_tag = _pick_role(by_role["bottom"], prefer_skirt=need_skirt)
            _add_unique(top_tag)
            _add_unique(bot_tag)
        else:
            _add_unique(_pick_role(one_pool, prefer_button=need_btn, prefer_skirt=need_skirt))
        # 若还需要状态载体（如 blazer_uniform 的 blazer/shirt 承载 unbuttoned），补充实际可承载构件
        if pool_state_ok and not is_tags_compatible_with_state(picked, state_id, state_tags, style_id=style_id):
            compat_pool = [t for t in legal_pool if is_tags_compatible_with_state([t], state_id, state_tags, style_id=style_id)]
            if compat_pool:
                _add_unique(repair_rng.choice(compat_pool))
    else:
        # 仅保留 legal_chosen（若含非法）或确保满足 state_id 载体
        for t in legal_chosen:
            if _extract_facts_dict(t).get("piece_role") in ("top", "bottom", "one_piece", "ensemble", "outerwear"):
                _add_unique(t)
        if not picked and legal_pool:
            # 优先选取非 accessory 实件
            real_pool = [t for t in legal_pool if _extract_facts_dict(t).get("piece_role") not in ("accessory", "legwear", "footwear", "unspecified", "none")]
            _add_unique(repair_rng.choice(real_pool or legal_pool))
        if not is_tags_compatible_with_state(picked, state_id, state_tags, style_id=style_id):
            compat_pool = [t for t in legal_pool if is_tags_compatible_with_state([t], state_id, state_tags, style_id=style_id)]
            if compat_pool:
                _add_unique(repair_rng.choice(compat_pool))

    picked.sort(key=lambda t: order_index.get(id(t), 9999))
    return tuple(picked), True


# ---------------------------------------------------------------------------
# 相容补件选择器与最小组合回退 (complete_outfit_tags)
# ---------------------------------------------------------------------------

def _pick_category_via_affinity(
    candidates: Sequence[Dict[str, Any]],
    profile: Optional[ContextProfile],
    context: Optional[str],
    affinity_matrix: Optional[Dict[str, Any]],
    rng: Random,
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    cand_map = {c.get("id"): c for c in candidates if c.get("id")}
    cand_ids = [c["id"] for c in candidates if c.get("id")]
    if profile is None:
        if context:
            try:
                profile = compute_context_profile([context], [])
            except Exception:
                profile = compute_context_profile([], [])
        else:
            profile = compute_context_profile([], [])

    if affinity_matrix and "clothing" in (affinity_matrix.get("generic") or {}) and cand_ids:
        try:
            dist = compute_slot_distribution("clothing", profile, cand_ids, affinity_matrix)
            chosen_id = sample_categorical(dist, rng)
            if chosen_id in cand_map:
                return cand_map[chosen_id]
        except Exception:
            pass
    return rng.choice(list(candidates))


def _filter_role_tag_candidates(
    categories: Sequence[Dict[str, Any]],
    *,
    role: str,
    nudity_level_code: str,
    excluded_ids: Set[str],
    state_id: Optional[str] = None,
    state_tags: Sequence[Any] = (),
    require_skirt: bool = False,
    require_pants: bool = False,
    require_dress: bool = False,
    require_button: bool = False,
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """从 categories 中过滤具备合法 role 标签且排除禁用大类的候选项。"""
    results: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    for cat in categories:
        cid = cat.get("id", "")
        if not cid or cid in excluded_ids:
            continue
        eligible_tags: List[Dict[str, Any]] = []
        for t in cat.get("tags", []):
            if not isinstance(t, dict):
                continue
            if t.get("role") == "combinable_attribute":
                continue
            if not is_tag_eligible_for_nudity_level(t, nudity_level_code):
                continue
            facts = t.get("facts") or {}
            pr = facts.get("piece_role")
            if role == "one_piece":
                if pr not in ("one_piece", "ensemble"):
                    continue
            elif role == "bra":
                if not _tag_is_bra_carrier(t, cid):
                    continue
            elif role == "panties":
                if not _tag_is_panties_carrier(t, cid):
                    continue
            elif pr != role:
                continue
            if require_skirt and not _tag_is_skirt_capable(t, cid):
                continue
            if require_pants and not _tag_is_pants_capable(t, cid):
                continue
            if require_dress and not _tag_is_dress_carrier(t, cid):
                continue
            if require_button and not _tag_is_button_capable(t):
                continue
            if state_id and state_id in ("unbuttoned", "lifted_up", "pulled_down", "slipping_off"):
                # 若明确要求单独承担某状态（仅对相关载体过滤，不误伤搭配件）
                if require_skirt and not is_tags_compatible_with_state([t], state_id, state_tags, style_id=cid):
                    continue
                if require_button and not is_tags_compatible_with_state([t], state_id, state_tags, style_id=cid):
                    continue
            eligible_tags.append(t)
        if eligible_tags:
            results.append((cat, eligible_tags))
    return results


def _sanitize_aux_component_tag(stag: SampledTag, comp_prov: TagProvenance) -> SampledTag:
    """将动态槽位或必选构件（如鞋履/配饰）的语义事实归一化，禁止继承主衣物的 bottom/top 覆盖声明。"""
    f_dict = _extract_facts_dict(stag)
    txt_low = _extract_text(stag).lower()
    dyn_slots: Sequence[Any] = ()
    if stag.facts and stag.facts.accessory_facts and getattr(stag.facts.accessory_facts, "dynamic_slots", None):
        dyn_slots = stag.facts.accessory_facts.dynamic_slots
    slot_names = {str(s.get("slot_name", "")).lower() for s in dyn_slots if isinstance(s, dict)}
    if (
        "footwear" in slot_names
        or any(w in txt_low for w in ("sneaker", "boot", "heel", "shoe", "sandal", "loafer", "slipper", "pump"))
    ):
        aux_role = "footwear"
        aux_topos: List[str] = []
        aux_covered: List[str] = []
    elif (
        "legwear" in slot_names
        or any(w in txt_low for w in ("sock", "stocking", "thighhigh", "pantyhose", "tights"))
    ):
        aux_role = "legwear"
        aux_topos = ["underwear"]
        aux_covered = ["legs"]
    else:
        aux_role = "accessory"
        aux_topos = []
        aux_covered = []

    clean_facts = SemanticFacts.from_dict(
        {
            **f_dict,
            "piece_role": aux_role,
            "garment_topologies": aux_topos,
            "covered_regions": aux_covered,
            "mutex_groups": [],
        }
    )
    return replace(stag, provenance=comp_prov, facts=clean_facts)


def _select_companion_piece(
    categories: Sequence[Dict[str, Any]],
    *,
    role: str,
    preferred_ids: Sequence[str],
    nudity_level_code: str,
    excluded_ids: Set[str],
    context_profile: Optional[ContextProfile],
    context: Optional[str],
    affinity_matrix: Optional[Dict[str, Any]],
    to_sampled_fn: Callable[..., Tuple[Any, List[SampledTag]]],
    rng: Random,
    state_id: Optional[str] = None,
    state_tags: Sequence[Any] = (),
    require_skirt: bool = False,
    require_pants: bool = False,
    require_dress: bool = False,
    require_button: bool = False,
) -> Optional[Tuple[str, Tuple[SampledTag, ...]]]:
    all_cands = _filter_role_tag_candidates(
        categories,
        role=role,
        nudity_level_code=nudity_level_code,
        excluded_ids=excluded_ids,
        state_id=state_id,
        state_tags=state_tags,
        require_skirt=require_skirt,
        require_pants=require_pants,
        require_dress=require_dress,
        require_button=require_button,
    )
    if not all_cands:
        return None

    pref_set = set(preferred_ids)
    tier1 = [(c, tags) for c, tags in all_cands if c.get("id") in pref_set]
    pool = tier1 if tier1 else all_cands

    chosen_cat = _pick_category_via_affinity(
        [c for c, _ in pool],
        profile=context_profile,
        context=context,
        affinity_matrix=affinity_matrix,
        rng=rng,
    )
    if not chosen_cat:
        return None
    cid = chosen_cat["id"]
    tag_list = next(tags for c, tags in pool if c.get("id") == cid)
    picked_raw = rng.choice(tag_list)

    _, sampled = to_sampled_fn(
        [picked_raw],
        cid,
        "clothing_completion",
        semantic_ids=(f"clothing:{cid}", f"nudity:{nudity_level_code}"),
        rng=rng,
    )
    if not sampled:
        return None

    comp_prov = TagProvenance(
        item_id=cid,
        semantic_ids=(f"clothing:{cid}", f"nudity:{nudity_level_code}"),
        kind="clothing_completion",
        parent_ids=(cid,),
    )
    # 展开结果中动态槽位/必选配件可能排在主衣物之前（例如 bottom_jeans_casual 先出 sneakers 再出 jeans pants）：
    # 必须识别并保留实际主衣物置前，且将附属构件语义角色归一化，禁止动态配件冒充上下装。
    main_tags = [
        replace(stag, provenance=comp_prov)
        for stag in sampled
        if getattr(stag, "role", None) not in ("dynamic_slot_component", "mandatory_component")
    ]
    if not main_tags:
        return None
    aux_tags = [
        _sanitize_aux_component_tag(stag, comp_prov)
        for stag in sampled
        if getattr(stag, "role", None) in ("dynamic_slot_component", "mandatory_component")
    ]
    return cid, tuple(main_tags + aux_tags)


def _select_branch_minimal_fallback(
    *,
    nudity_level_code: str,
    nudity_branch: Optional[str],
    categories: Sequence[Dict[str, Any]],
    excluded_ids: Set[str],
    context_profile: Optional[ContextProfile],
    context: Optional[str],
    affinity_matrix: Optional[Dict[str, Any]],
    to_sampled_fn: Callable[..., Tuple[Any, List[SampledTag]]],
    completion_rng: Random,
) -> Tuple[List[str], List[SampledTag]]:
    """按目标 (nudity_level_code, nudity_branch) 执行严格遵守分支语义的最小安全组合回退。"""
    def _pick_single(role: str, prefs: Sequence[str], **kw: Any) -> Optional[Tuple[List[str], List[SampledTag]]]:
        r = _select_companion_piece(
            categories,
            role=role,
            preferred_ids=prefs,
            nudity_level_code=nudity_level_code,
            excluded_ids=excluded_ids,
            context_profile=context_profile,
            context=context,
            affinity_matrix=affinity_matrix,
            to_sampled_fn=to_sampled_fn,
            rng=completion_rng,
            **kw,
        )
        if not r:
            return None
        return [r[0]], list(r[1])

    if nudity_level_code == "L3" and nudity_branch:
        if nudity_branch == "l3_topless_skirt_kept":
            res = _pick_single("bottom", EVERYDAY_BOTTOM_IDS, require_skirt=True)
            return res if res else ([], [])
        if nudity_branch == "l3_topless_pants_kept":
            res = _pick_single("bottom", EVERYDAY_BOTTOM_IDS, require_pants=True)
            return res if res else ([], [])
        if nudity_branch == "l3_pulled_down_dress":
            res = _pick_single("one_piece", EVERYDAY_ONE_PIECE_IDS, require_dress=True)
            return res if res else ([], [])
        if nudity_branch == "l3_bra_pushed_up":
            res = _pick_single("bra", ("lingerie_bra", "lingerie_lace"))
            return res if res else ([], [])
        if nudity_branch == "l3_open_shirt_breasts":
            res = _pick_single("top", EVERYDAY_TOP_IDS, require_button=True) or _pick_single("top", EVERYDAY_TOP_IDS)
            return res if res else ([], [])
        if nudity_branch == "l3_raised_top_breasts":
            res = _pick_single("top", EVERYDAY_TOP_IDS)
            return res if res else ([], [])
        if nudity_branch in ("l3_panties_only", "l3_bikini_top_removed"):
            res = _pick_single("panties", ("lingerie_panties", "bikini_classic", "bikini_micro"))
            return res if res else ([], [])

    # L1 / L2 / 无明确分支 L3：先尝试最小完整 top + bottom，再尝试 one_piece
    t_fb = _select_companion_piece(
        categories,
        role="top",
        preferred_ids=EVERYDAY_TOP_IDS,
        nudity_level_code=nudity_level_code,
        excluded_ids=excluded_ids,
        context_profile=context_profile,
        context=context,
        affinity_matrix=affinity_matrix,
        to_sampled_fn=to_sampled_fn,
        rng=completion_rng,
    )
    b_fb = _select_companion_piece(
        categories,
        role="bottom",
        preferred_ids=EVERYDAY_BOTTOM_IDS,
        nudity_level_code=nudity_level_code,
        excluded_ids=excluded_ids | ({t_fb[0]} if t_fb else set()),
        context_profile=context_profile,
        context=context,
        affinity_matrix=affinity_matrix,
        to_sampled_fn=to_sampled_fn,
        rng=completion_rng,
    )
    if t_fb and b_fb:
        return [t_fb[0], b_fb[0]], list(t_fb[1]) + list(b_fb[1])
    one_fb = _pick_single("one_piece", EVERYDAY_ONE_PIECE_IDS)
    return one_fb if one_fb else ([], [])


def complete_outfit_tags(
    *,
    primary_style_id: str,
    active_tags: Sequence[Any],
    state_id: Optional[str],
    state_tags: Sequence[Any],
    nudity_level_code: str,
    nudity_branch: Optional[str],
    categories: Sequence[Dict[str, Any]],
    excluded_ids: Set[str],
    context_profile: Optional[ContextProfile],
    context: Optional[str],
    affinity_matrix: Optional[Dict[str, Any]],
    to_sampled_fn: Callable[..., Tuple[Any, List[SampledTag]]],
    completion_rng: Random,
    force_minimal_fallback: bool = False,
) -> Tuple[Tuple[SampledTag, ...], Dict[str, Any]]:
    """
    依据缺失部位为非豁免服装补充相容伴侣构件，并返回 (complement_tags, completion_info)。
    不修改原主款标签，所有补件携带各自独立 item_id 与 provenance。
    """
    assessment = assess_clothing_completeness(
        active_tags,
        state_tags,
        nudity_level_code=nudity_level_code,
        nudity_branch=nudity_branch,
    )
    info: Dict[str, Any] = {
        "primary_style_id": primary_style_id,
        "missing_parts": list(assessment.missing_parts),
        "complement_ids": [],
        "fallback_used": False,
        "fallback_reason": None,
        "replace_base_with_fallback": False,
        "failed": False,
    }
    if assessment.is_complete and not force_minimal_fallback:
        return (), info

    base_excl = set(excluded_ids)
    if primary_style_id:
        base_excl.add(primary_style_id)

    # 判断当前状态是否已在主款上存在载体；若无且状态需要载体，则补件必须承担该载体要求
    existing_has_carrier = is_tags_compatible_with_state(active_tags, state_id, state_tags, style_id=primary_style_id)
    need_skirt_carrier = (state_id == "lifted_up" and not existing_has_carrier) or (
        nudity_branch in ("l3_topless_skirt_kept", "l2_midriff_waist", "l2_leg_slit")
    )
    need_button_carrier = (state_id == "unbuttoned" and not existing_has_carrier) or (nudity_branch == "l3_open_shirt_breasts")

    complements: List[SampledTag] = []
    comp_ids: List[str] = []

    if not force_minimal_fallback and assessment.missing_parts:
        for part in assessment.missing_parts:
            if part == "bottom":
                res = _select_companion_piece(
                    categories,
                    role="bottom",
                    preferred_ids=EVERYDAY_BOTTOM_IDS,
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                    state_id=state_id if need_skirt_carrier else None,
                    state_tags=state_tags,
                    require_skirt=need_skirt_carrier,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "bottom_skirt":
                res = _select_companion_piece(
                    categories,
                    role="bottom",
                    preferred_ids=EVERYDAY_BOTTOM_IDS,
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                    require_skirt=True,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "bottom_pants":
                res = _select_companion_piece(
                    categories,
                    role="bottom",
                    preferred_ids=EVERYDAY_BOTTOM_IDS,
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                    require_pants=True,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "top":
                res = _select_companion_piece(
                    categories,
                    role="top",
                    preferred_ids=EVERYDAY_TOP_IDS,
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                    state_id=state_id if need_button_carrier else None,
                    state_tags=state_tags,
                    require_button=need_button_carrier,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "bra":
                res = _select_companion_piece(
                    categories,
                    role="bra",
                    preferred_ids=("lingerie_bra", "lingerie_lace"),
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "panties":
                res = _select_companion_piece(
                    categories,
                    role="panties",
                    preferred_ids=("lingerie_panties", "bikini_classic", "bikini_micro"),
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "one_piece":
                req_dress = (nudity_branch == "l3_pulled_down_dress")
                res = _select_companion_piece(
                    categories,
                    role="one_piece",
                    preferred_ids=EVERYDAY_ONE_PIECE_IDS,
                    nudity_level_code=nudity_level_code,
                    excluded_ids=base_excl | set(comp_ids),
                    context_profile=context_profile,
                    context=context,
                    affinity_matrix=affinity_matrix,
                    to_sampled_fn=to_sampled_fn,
                    rng=completion_rng,
                    state_id=state_id if (need_skirt_carrier or need_button_carrier) else None,
                    state_tags=state_tags,
                    require_skirt=need_skirt_carrier,
                    require_dress=req_dress,
                    require_button=need_button_carrier,
                )
                if res:
                    comp_ids.append(res[0])
                    complements.extend(res[1])
            elif part == "inner_full":
                prefer_two_piece = need_button_carrier or (completion_rng.random() < 0.65)
                inner_added = False
                if prefer_two_piece:
                    t_res = _select_companion_piece(
                        categories,
                        role="top",
                        preferred_ids=EVERYDAY_TOP_IDS,
                        nudity_level_code=nudity_level_code,
                        excluded_ids=base_excl | set(comp_ids),
                        context_profile=context_profile,
                        context=context,
                        affinity_matrix=affinity_matrix,
                        to_sampled_fn=to_sampled_fn,
                        rng=completion_rng,
                        state_id=state_id if need_button_carrier else None,
                        state_tags=state_tags,
                        require_button=need_button_carrier,
                    )
                    b_res = _select_companion_piece(
                        categories,
                        role="bottom",
                        preferred_ids=EVERYDAY_BOTTOM_IDS,
                        nudity_level_code=nudity_level_code,
                        excluded_ids=base_excl | set(comp_ids) | ({t_res[0]} if t_res else set()),
                        context_profile=context_profile,
                        context=context,
                        affinity_matrix=affinity_matrix,
                        to_sampled_fn=to_sampled_fn,
                        rng=completion_rng,
                        state_id=state_id if need_skirt_carrier else None,
                        state_tags=state_tags,
                        require_skirt=need_skirt_carrier,
                    )
                    if t_res and b_res:
                        comp_ids.extend([t_res[0], b_res[0]])
                        complements.extend(t_res[1])
                        complements.extend(b_res[1])
                        inner_added = True
                if not inner_added:
                    d_res = _select_companion_piece(
                        categories,
                        role="one_piece",
                        preferred_ids=EVERYDAY_ONE_PIECE_IDS,
                        nudity_level_code=nudity_level_code,
                        excluded_ids=base_excl | set(comp_ids),
                        context_profile=context_profile,
                        context=context,
                        affinity_matrix=affinity_matrix,
                        to_sampled_fn=to_sampled_fn,
                        rng=completion_rng,
                        state_id=state_id if need_skirt_carrier else None,
                        state_tags=state_tags,
                        require_skirt=need_skirt_carrier,
                    )
                    if d_res:
                        comp_ids.append(d_res[0])
                        complements.extend(d_res[1])

    # 检查补全后是否真正达成完整度与可承载性
    after = assess_clothing_completeness(
        list(active_tags) + complements,
        state_tags,
        nudity_level_code=nudity_level_code,
        nudity_branch=nudity_branch,
    )
    if after.is_complete and not force_minimal_fallback:
        info["complement_ids"] = comp_ids
        return tuple(complements), info

    # -----------------------------------------------------------------------
    # 最小安全组合回退 (Minimal Combination Fallback, 严格遵守 L1-L6 分支)
    # -----------------------------------------------------------------------
    fallback_ids, fallback_complements = _select_branch_minimal_fallback(
        nudity_level_code=nudity_level_code,
        nudity_branch=nudity_branch,
        categories=categories,
        excluded_ids=set(excluded_ids),
        context_profile=context_profile,
        context=context,
        affinity_matrix=affinity_matrix,
        to_sampled_fn=to_sampled_fn,
        completion_rng=completion_rng,
    )

    if fallback_complements:
        clean_active = (
            [] if (force_minimal_fallback or (nudity_level_code == "L3" and nudity_branch))
            else [t for t in active_tags if is_tag_eligible_for_nudity_level(t, nudity_level_code)]
        )
        replace_base = (
            force_minimal_fallback
            or bool(assessment.violating_item_ids)
            or (len(clean_active) < len(active_tags))
            or bool(nudity_level_code == "L3" and nudity_branch)
        )
        candidate_tags = fallback_complements if replace_base else (clean_active + fallback_complements)
        fb_check = assess_clothing_completeness(
            candidate_tags,
            () if replace_base else state_tags,
            nudity_level_code=nudity_level_code,
            nudity_branch=nudity_branch,
        )
        if not fb_check.is_complete and not replace_base:
            replace_base = True
            fb_check = assess_clothing_completeness(
                fallback_complements,
                (),
                nudity_level_code=nudity_level_code,
                nudity_branch=nudity_branch,
            )
        if fb_check.is_complete:
            info["complement_ids"] = fallback_ids
            info["fallback_used"] = True
            info["replace_base_with_fallback"] = replace_base
            info["fallback_reason"] = (
                "post_resolution_rebuild_minimal_combo" if force_minimal_fallback else "no_compatible_companion_fallback"
            )
            return tuple(fallback_complements), info

    info["complement_ids"] = comp_ids
    info["fallback_used"] = True
    info["fallback_reason"] = "minimal_combination_exhausted"
    info["failed"] = True
    return tuple(complements), info
