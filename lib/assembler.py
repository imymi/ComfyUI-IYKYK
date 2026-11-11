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
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

import json
import re
from dataclasses import replace

if __package__:
    from .atomizer import (
        PromptTag,
        atoms_to_tags,
        deduplicate_tags_with_records,
        fragments_to_atoms,
    )
    from .conflict_resolver import (
        ConflictResolver,
        is_external_atom,
    )
    from .errors import PromptValidationError, UnresolvedConflictError
    from .lexer import split_top_level_tags, validate_prompt_syntax
    from .models import (
        AssemblyResult,
        BudgetFilterRecord,
        ContextProfile,
        NudityConstraint,
        OverrideRecord,
        PromptAtom,
        PromptFragment,
        SampledTag,
        SelectionOrigin,
        SemanticFacts,
        TagProvenance,
        VALID_NUDITY_LEVELS,
    )
    from .outfit_completion import assess_clothing_completeness
    from .slot_contract import ALLOWED_SLOTS, AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_raw_input_text, normalize_slot_mapping, normalize_slot_name
else:
    from lib.atomizer import (
        PromptTag,
        atoms_to_tags,
        deduplicate_tags_with_records,
        fragments_to_atoms,
    )
    from lib.conflict_resolver import (
        ConflictResolver,
        is_external_atom,
    )
    from lib.errors import PromptValidationError, UnresolvedConflictError
    from lib.lexer import split_top_level_tags, validate_prompt_syntax
    from lib.models import (
        AssemblyResult,
        BudgetFilterRecord,
        ContextProfile,
        NudityConstraint,
        OverrideRecord,
        PromptAtom,
        PromptFragment,
        SampledTag,
        SelectionOrigin,
        SemanticFacts,
        TagProvenance,
        VALID_NUDITY_LEVELS,
    )
    from lib.outfit_completion import assess_clothing_completeness
    from lib.slot_contract import ALLOWED_SLOTS, AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_raw_input_text, normalize_slot_mapping, normalize_slot_name

MAX_PROMPT_WORDS = 250

CANONICAL_20_SLOTS: Tuple[str, ...] = (*SLOT_ORDER, *AUXILIARY_SLOT_ORDER)


def _inspect_blank_input(raw_val: Any) -> Tuple[bool, Optional[str]]:
    """检查输入是否为空白，并返回 (is_blank, blank_reason)."""
    if raw_val is None:
        return True, "omitted"
    if isinstance(raw_val, str):
        if len(raw_val) == 0:
            return True, "empty_string"
        if not raw_val.strip():
            return True, "whitespace_only"
        return False, None
    if isinstance(raw_val, (list, tuple)):
        if len(raw_val) == 0:
            return True, "empty_list"
        if all(isinstance(x, str) and not x.strip() for x in raw_val):
            return True, "empty_list"
        return False, None
    return False, None


def build_default_canonical_override_records() -> Tuple[OverrideRecord, ...]:
    """生成 20 规范槽位默认全未提供 (not_provided) 记录。"""
    return tuple(
        OverrideRecord(
            canonical_slot=slot,
            status="not_provided",
            source_port=None,
            raw_input=None,
            blank_fallback_reason="omitted",
            replaced_target_ids=(),
            replaced_texts=(),
            injected_atom_ids=(),
        )
        for slot in CANONICAL_20_SLOTS
    )

_CATALOG_TAG_TO_SLOT_CACHE: Dict[Path, Dict[str, str]] = {}
_CATALOG_TEXT_TO_FACTS_CACHE: Dict[Path, Dict[str, SemanticFacts]] = {}
_RESOLVER_CACHE: Dict[str, ConflictResolver] = {}

PRESET_SLOT_PATTERNS = [
    # quality (包含拍摄方式、相机设备与画质描述，绝不作为手持道具)
    (re.compile(r'\b(masterpiece|best quality|ultra detailed|8k|photorealistic|highres|high resolution|cctv|security camera|amateur photo|low quality|grainy|timestamp overlay|front camera quality|rear camera quality|camera quality|front camera|rear camera|smartphone photo|phone photo|phone camera|pinhole camera|disposable camera|webcam quality|webcam photo|snapshot|casual snapshot)\b', re.I), 'quality'),
    # lighting & time of day
    (re.compile(r'\b(light|lighting|shadow|shadows|glow|sunlight|moonlight|backlight|rim light|neon|candlelight|dim|bright|golden hour|blue hour|sunset|sunrise|night|chiaroscuro|volumetric|lens flare|bokeh|fluorescent|lamp|overcast|rays|ambient)\b', re.I), 'lighting'),
    # tattoo (严格排除 tattoo studio, parlor 等场景场所词与家具器材词)
    (re.compile(r'\b(tattoo|tattoos|tattooed|inked|ink on|fresh ink)\b(?!\s+(?:studio|parlor|parlour|shop|salon|chair|artist|machine|needle))', re.I), 'tattoo'),
    # liquids
    (re.compile(r'\b(sweat|sweaty|sweating|drool|saliva|milk|lotion|oil|slime|cum|semen|ejaculation|juice|liquid|liquids|dripping|splash|wet skin|glistening moisture)\b', re.I), 'liquids'),
    # camera_angle (支持各类偷窥视角、窥视角度与门缝镜头)
    (re.compile(r'\b(angle|angles|perspective|eye level|voyeur angle|dutch angle|low angle|high angle|overhead|birds eye|worms eye|straight-on|tilted|view from|door crack view|peephole view|hidden camera feel|hidden camera angle|angle from adjacent aisle|camera view|pov|point of view)\b', re.I), 'camera_angle'),
    # shot_type
    (re.compile(r'\b(shot|close-up|extreme close-up|medium shot|full body shot|cowboy shot|wide shot|macro|depth of field|field of view|fisheye|portrait shot|framing)\b', re.I), 'shot_type'),
    # film
    (re.compile(r'\b(film|kodak|fujifilm|polaroid|cinestill|ilford|grain|vintage|35mm|analog|monochrome|black and white|pastel|accurate colors|warm accurate colors)\b', re.I), 'film'),
    # makeup / imperfections
    (re.compile(r'\b(makeup|lipstick|eyeliner|eyeshadow|mascara|blush|foundation|lip gloss|freckles|mole|pores|skin texture)\b', re.I), 'makeup'),
    # clothing / clothing_state / underwear / lingerie (排除皮椅、沙发等家具皮革)
    (re.compile(r'\b(uniform|shirt|blouse|skirt|dress|suit|pants|shorts|jacket|coat|sweater|swimsuit|bikini|lingerie|bra|panties|underwear|stockings|socks|boots|shoes|heels|catsuit|latex|corset|robe|kimono|yukata|cheongsam|qipao|bodysuit|leotard|apron|maid|hoodie|cardigan|tank top|camisole|t-shirt|jeans|vest|collar|tie|bow|sash|belt|cape|gloves|cuffs|garter|thighhighs|unbuttoned|lifted|pulled down|partially|untied|disheveled|torn|see-through|translucent|serafuku|sleeves|cleavage|slip|strap|zipper|outfit|costume|clothes|fabric|silk|lace|cotton|denim|leather(?!\s+(?:chair|couch|sofa|bench|seat|car|interior|furniture|stool|armchair))|slipping off shoulder|off-shoulder|bare shoulder|hiked up|undressing|open front)\b', re.I), 'clothing'),
    # props (排除摄影角度、视线注视与画质词)
    (re.compile(r'\b(holding\s+(?:a\s+|an\s+)?(?!head|face|knees|legs|hair|chest|breasts|waist)\w+|prop|props|spatula|(?<!looking at\s)(?<!looking down at\s)(?:smart)?phone(?!\s+camera|\s+photo|\s+screen|\s+lighting|\s+wallpaper|\s+charging|\s+quality)|(?<!looking at\s)(?<!unaware of\s)(?<!towards\s)(?<!front of\s)(?<!in front of\s)(?<!hidden\s)(?<!security\s)(?<!cctv\s)(?<!phone\s)(?<!front\s)(?<!rear\s)(?<!webcam\s)camera(?!\s+quality|\s+angle|\s+view|\s+shot|\s+style|\s+feel|\s+rolling)|controller|(?<!glass\s)glass(?!\s+walls|\s+case|\s+door|\s+window|\s+table)|cup|bottle|pen|pencil|book|bag|umbrella|fan|doll|plush|(?<!beach\s)pillow|(?<!beach\s)towel(?!\s+on lap)|cigarette(?!\s+smoke)|gun|sword|instrument|toy(?!\s+section|\s+store|\s+shop)|dumbbell|mic|product|needle)\b', re.I), 'props'),
    # pose
    (re.compile(r'\b(standing|sitting|lying|kneeling|squatting|bending|leaning|walking|running|pose|legs|arms|hands|crossed|spread|bent|arching|stretching|turned|bound|tied|hold|holding|touching|hand on|hands on|hands behind|crawling|spread legs|kicking feet|hugging knees|toes|entering|watching|tracing|tracing inner thigh|hand tracing)\b', re.I), 'pose'),
    # scene_theme & furniture & scenery
    (re.compile(r'\b(classroom|desk|chair|bench|table|bed|sofa|couch|school|room|office|bedroom|bathroom|onsen|hot spring|pool|beach|street|alley|park|forest|hospital|clinic|library|bookstore|kitchen|balcony|rooftop|subway|train|bus|station|hotel|bar|club|gym|temple|shrine|tatami|futon|garden|restaurant|cafe|car|elevator|hallway|corridor|staircase|back row|class in progress|chalkboard|blackboard|podium|window seat|empty room|counter|outdoors|indoors|scenery|background|interior|city|scenic|nature|dock|deck|shelves|shelf|fence|dormitory|bunk|curtain|atmosphere|corner|shower area|steam|space|after class|dust particles|studio|parlor|parlour|salon|shop|environment|sterile environment|glass walls|glass case|toy section|cigarette smoke|phone charging)\b', re.I), 'scene_theme'),
    # expression / emotion
    (re.compile(r'\b(expression|smile|smiling|smirk|frown|pout|tears|crying|blushing|gaze|looking|face|bored|shy|embarrassed|pleasure|aroused|seductive|angry|surprised|neutral|eyes closed|half closed|wink|mouth|biting lip|lip|looking at camera|unaware of camera|looking at phone|looking at screen|looking at mirror|looking away)\b', re.I), 'expression'),
    # hairstyle / character
    (re.compile(r'\b(hair|ponytail|twintails|braid|bangs|bob cut|bun|curly|straight|blonde|brunette|black hair|girl|woman|mature|petite|curves|skin|eyes)\b', re.I), 'hairstyle'),
    # nudity (纯裸露部位与状态，体液分流至 liquids)
    (re.compile(r'\b(nude|naked|bare|exposed|nipple|nipples|breast|breasts|areola|areolae|pussy|vagina|penis|crotch|ass|buttocks|cleavage|shaved|pubic|topless|bottomless|underboob|sideboob)\b', re.I), 'nudity'),
]


def get_catalog_tag_cache(data_dir: Path) -> Tuple[Dict[str, str], Dict[str, SemanticFacts]]:
    data_dir = Path(data_dir)
    if data_dir in _CATALOG_TAG_TO_SLOT_CACHE and data_dir in _CATALOG_TEXT_TO_FACTS_CACHE:
        return _CATALOG_TAG_TO_SLOT_CACHE[data_dir], _CATALOG_TEXT_TO_FACTS_CACHE[data_dir]

    tag_map: Dict[str, str] = {}
    facts_map: Dict[str, SemanticFacts] = {}

    def register(text: str, slot: str, facts: Optional[SemanticFacts] = None):
        if not text:
            return
        t = text.strip().lower()
        if t and t not in tag_map:
            tag_map[t] = slot
        if t and facts and t not in facts_map:
            facts_map[t] = facts

    for q in [
        "masterpiece", "best quality", "ultra detailed", "8k", "photorealistic",
        "high resolution", "highres", "absurdres", "incredibly absurdres",
        "cctv footage", "security camera", "low resolution", "grainy",
        "phone camera", "selfie", "amateur photo", "slightly blurry",
    ]:
        register(q, "quality")

    FILE_SLOT_MAP = [
        ("clothing.json", "clothing"),
        ("shot_types.json", "shot_type"),
        ("scenes.json", "scene_theme"),
        ("themes.json", "scene_theme"),
        ("lighting.json", "lighting"),
        ("poses.json", "pose"),
        ("props.json", "props"),
        ("expressions.json", "expression"),
        ("makeup.json", "makeup"),
        ("accessories.json", "jewelry"),
        ("characters.json", "character"),
        ("film_stocks.json", "film"),
        ("imperfections.json", "imperfections"),
        ("nudity_levels.json", "nudity"),
        ("tattoos.json", "tattoo"),
    ]

    for fname, default_slot in FILE_SLOT_MAP:
        p = data_dir / fname
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue

        def walk(obj, cur_slot=default_slot):
            if isinstance(obj, dict):
                s = obj.get("slot", cur_slot)
                raw_facts = obj.get("facts")
                sem_facts = None
                if isinstance(raw_facts, dict):
                    try:
                        sem_facts = SemanticFacts.from_dict(raw_facts)
                    except Exception:
                        pass
                if "text" in obj and isinstance(obj["text"], str):
                    register(obj["text"], s, sem_facts)
                if "tag" in obj and isinstance(obj["tag"], str):
                    register(obj["tag"], s, sem_facts)
                if "tags" in obj and isinstance(obj["tags"], list):
                    for item in obj["tags"]:
                        if isinstance(item, str):
                            register(item, s)
                        elif isinstance(item, dict):
                            item_slot = item.get("slot", s)
                            item_f = None
                            if isinstance(item.get("facts"), dict):
                                try:
                                    item_f = SemanticFacts.from_dict(item["facts"])
                                except Exception:
                                    pass
                            register(item.get("tag") or item.get("text") or "", item_slot, item_f)
                for k, v in obj.items():
                    if k not in ("tags", "text", "tag", "facts"):
                        child_slot = "camera_angle" if k == "camera_angles" else ("shot_type" if k == "shot_types" else s)
                        walk(v, child_slot)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v, cur_slot)

        if fname == "shot_types.json" and isinstance(d, dict):
            if "shot_types" in d:
                walk(d["shot_types"], "shot_type")
            if "camera_angles" in d:
                walk(d["camera_angles"], "camera_angle")
        else:
            walk(d, default_slot)

    _CATALOG_TAG_TO_SLOT_CACHE[data_dir] = tag_map
    _CATALOG_TEXT_TO_FACTS_CACHE[data_dir] = facts_map
    return tag_map, facts_map


def classify_preset_tag_and_facts(
    text: str,
    data_dir: Path,
    target_slot: Optional[str] = None,
) -> Tuple[str, SemanticFacts]:
    """根据目录事实和语义正则将预设/配方片段映射至权威 18 槽位契约，并补齐必要事实字段。"""
    t = text.strip().lower()
    tag_map, facts_map = get_catalog_tag_cache(data_dir)

    # 1. 确定槽位归属：若调用方已指定且已知则保留，否则优先以语义模式匹配确定槽位主体，再回退至目录权威映射
    if target_slot and target_slot in ALLOWED_SLOTS:
        slot = target_slot
    else:
        slot = None
        for pat, s in PRESET_SLOT_PATTERNS:
            if pat.search(t):
                slot = s
                break
        if not slot:
            slot = tag_map.get(t)
        if not slot:
            slot = "preset_core"

    # 2. 查 facts_map 获得目录权威事实
    facts = facts_map.get(t)
    if facts is None:
        facts = SemanticFacts(semantic_role="selector")
    elif slot not in ("scene", "scene_theme", "theme"):
        if facts.space_kind is not None or facts.venue_ids:
            facts = replace(facts, space_kind=None, venue_ids=())

    # 3. 按槽位补齐必要事实字段，避免 _validate_formal_atom Fail-Closed
    if slot == "nudity":
        if not facts.visible_regions:
            if any(w in t for w in ("breast", "nipple", "areola", "topless", "bare chest", "cleavage", "midriff", "bare shoulder")):
                facts = facts.merge(SemanticFacts(visible_regions=("upper_body",)))
            elif any(w in t for w in ("pussy", "vagina", "crotch", "pubic", "ass", "buttocks", "bottomless")):
                facts = facts.merge(SemanticFacts(visible_regions=("intimate_lower_body",)))
            elif any(w in t for w in ("nude", "naked", "bare body", "fully naked")):
                facts = facts.merge(SemanticFacts(visible_regions=("full_body",)))
            else:
                facts = facts.merge(SemanticFacts(visible_regions=("upper_body",)))
    elif slot in ("clothing", "clothing_state", "clothing_extension", "underwear"):
        if not facts.garment_topologies and not facts.garment_states:
            if any(w in t for w in ("catsuit", "latex suit", "bodysuit", "swimsuit", "leotard", "one-piece", "robe", "kimono", "yukata", "dress", "uniform", "serafuku")):
                facts = facts.merge(SemanticFacts(garment_topologies=("one_piece",), fabric_materials=("latex",) if "latex" in t else ()))
            elif any(w in t for w in ("skirt", "pleated skirt", "mini skirt")):
                facts = facts.merge(SemanticFacts(garment_topologies=("bottom_skirt",)))
            elif any(w in t for w in ("pants", "shorts", "jeans", "trousers")):
                facts = facts.merge(SemanticFacts(garment_topologies=("bottom_pants",)))
            elif any(w in t for w in ("shirt", "blouse", "top", "jacket", "coat", "sweater", "hoodie")):
                facts = facts.merge(SemanticFacts(garment_topologies=("top",)))
            elif any(w in t for w in ("panties", "bra", "underwear", "lingerie", "no panties")):
                facts = facts.merge(SemanticFacts(garment_topologies=("underwear",)))
            else:
                facts = facts.merge(SemanticFacts(garment_topologies=("top",)))
    elif slot in ("scene_theme", "scene", "theme"):
        if not facts.space_kind:
            if any(w in t for w in ("outdoor", "park", "street", "beach", "forest", "onsen", "hot spring", "pool", "garden", "balcony", "rooftop", "alley")):
                facts = facts.merge(SemanticFacts(space_kind="outdoor"))
            elif any(w in t for w in ("indoor", "classroom", "bedroom", "bathroom", "office", "hotel", "kitchen", "library", "corridor", "hallway", "room")):
                facts = facts.merge(SemanticFacts(space_kind="indoor"))
        if not facts.time_of_day:
            if any(w in t for w in ("night", "dark", "midnight")):
                facts = facts.merge(SemanticFacts(time_of_day="night"))
            elif any(w in t for w in ("sunset", "dusk", "evening", "golden hour")):
                facts = facts.merge(SemanticFacts(time_of_day="dusk"))
            elif any(w in t for w in ("dawn", "sunrise", "morning")):
                facts = facts.merge(SemanticFacts(time_of_day="dawn"))
            elif any(w in t for w in ("day", "afternoon", "noon")):
                facts = facts.merge(SemanticFacts(time_of_day="day"))
            else:
                facts = facts.merge(SemanticFacts(time_of_day="unspecified"))
    elif slot in ("lighting", "lighting_palette"):
        if not facts.light_sources and not facts.time_of_day:
            ls = ("studio",) if any(w in t for w in ("neon", "lamp", "fluorescent", "candlelight", "studio", "window side light", "soft classroom light")) else ("natural",)
            facts = facts.merge(SemanticFacts(light_sources=ls, time_of_day="day"))
    elif slot in ("pose", "poses"):
        if not facts.hand_state:
            if any(w in t for w in ("bound", "tied", "behind back", "hands on head")):
                hs = "both_busy"
                hr = 2
            elif any(w in t for w in ("hand on", "touching", "holding", "biting", "tracing", "hand tracing", "other hand", "one hand")):
                hs = "one_busy"
                hr = 1
            else:
                hs = "free"
                hr = 0
            facts = facts.merge(SemanticFacts(hand_state=hs, hands_required=hr))
    elif slot in ("props", "prop"):
        if facts.hands_required == 0 and not facts.prop_usage:
            hr = 2 if any(w in t for w in ("controller", "sword", "rifle")) else 1
            facts = facts.merge(SemanticFacts(hands_required=hr, prop_usage="handheld"))
    elif slot in ("shot_type", "shot"):
        if not facts.visible_regions:
            vr = ("upper_body",) if any(w in t for w in ("close-up", "portrait", "medium shot", "upper body")) else ("full_body",)
            facts = facts.merge(SemanticFacts(visible_regions=vr))
    elif slot in ("film", "film_stock"):
        if not facts.color_modes:
            cm = ("monochrome",) if any(w in t for w in ("monochrome", "black and white", "bw", "b&w")) else ("color",)
            facts = facts.merge(SemanticFacts(color_modes=cm))
    elif slot in ("expression", "expressions"):
        if not facts.emotion and not facts.gaze:
            gaze = "away" if any(w in t for w in ("window", "away", "side", "down", "up")) else "camera"
            emo = "neutral"
            for e in ("shy", "seductive", "pleasure", "submissive", "playful", "pain", "fear", "dazed", "happy", "relaxed", "tired"):
                if e in t:
                    emo = e
                    break
            facts = facts.merge(SemanticFacts(emotion=emo, gaze=gaze))
    elif slot in ("liquids", "liquid"):
        if not facts.liquid_kind:
            facts = facts.merge(SemanticFacts(liquid_kind="water", liquid_amount="normal"))
    elif slot in ("tattoos", "tattoo"):
        if not facts.visible_regions:
            facts = facts.merge(SemanticFacts(visible_regions=("upper_body",)))
    elif slot in ("camera_angle", "camera", "angle"):
        if not facts.semantic_role:
            facts = facts.merge(SemanticFacts(semantic_role="selector"))
    elif slot == "quality":
        if not facts.quality_class:
            facts = facts.merge(SemanticFacts(quality_class="standard", capture_device="professional"))
    elif slot == "makeup":
        if not facts.makeup_base and not facts.makeup_effects:
            facts = facts.merge(SemanticFacts(makeup_base="natural"))

    return slot, facts


def classify_preset_tag(text: str, data_dir: Path) -> str:
    """根据目录事实和语义正则将预设/配方片段映射至权威 18 槽位契约。"""
    slot, _ = classify_preset_tag_and_facts(text, data_dir)
    return slot


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


_NUDITY_LEVELS_DOC_CACHE: Dict[Path, List[Dict[str, Any]]] = {}

_PRESET_L1_BANNED_RE = re.compile(
    r"\b(?:one\s+breast\s+exposed|bare\s+breasts?|exposed\s+breasts?|erect\s+nipple\w*|"
    r"nipple\s+(?:visible|erect)|nipples?|dark\s+areola\w*|areola\w*|no\s+bra|no\s+panties|"
    r"pussy\b|vagina|penis|crotch|genitals|skirt\s+hiked\s+up|hiked\s+up|shirt\s+unbuttoned|"
    r"unbuttoned|slipping\s+off\s+shoulder|bare\s+shoulder|naked\s+under|panties\s+pulled|"
    r"panties\s+pushed|hand\s+in\s+panties|fingering|spread\s+legs|legs\s+spread|"
    r"topless|bottomless|cleavage|underboob|sideboob|naked|nude)\b",
    re.I,
)
_PRESET_L2_BANNED_RE = re.compile(
    r"\b(?:one\s+breast\s+(?:fully\s+)?exposed|bare\s+breasts?|exposed\s+breasts?|"
    r"erect\s+nipple\w*|nipple\s+(?:visible|erect)|nipples?|dark\s+areola\w*|areola\w*|"
    r"no\s+bra|no\s+panties|pussy\b|vagina|penis|crotch|genitals|naked\s+under|"
    r"panties\s+pulled|panties\s+pushed|hand\s+in\s+panties|fingering|topless|bottomless|naked|nude)\b",
    re.I,
)
_PRESET_WORN_CLOTHING_RE = re.compile(
    r"\b(?:uniform|furisode|kimono|yukata|qipao|cheongsam|dress|skirt|shirt|blouse|sweater|"
    r"hoodie|jacket|coat|blazer|pants|shorts|jeans|trousers|suit|catsuit|bodysuit|leotard|"
    r"swimsuit|bikini|lingerie|bra|panties|underwear|apron|stockings|socks|boots|shoes|heels|"
    r"slipping\s+off\s+shoulder)\b",
    re.I,
)
_PRESET_L56_SUPERSEDED_NUDITY_RE = re.compile(
    r"\b(?:one\s+breast\s+(?:fully\s+)?exposed|bare\s+shoulder|slipping\s+off\s+shoulder|"
    r"nipple\s+visible\s+through\s+fabric)\b",
    re.I,
)
_PRESET_L6_UPPER_FRAMING_RE = re.compile(
    r"\b(?:medium\s+shot|waist-up(?:\s+framing)?|chest-up(?:\s+shot)?|medium\s+close-up|"
    r"upper[- ]body(?:\s+framing)?|half-body\s+portrait|face\s+shot\s+filling\s+frame|"
    r"portrait\s+framing|close-up\s+(?:face\s+)?portrait|face\s+close-up|extreme\s+close-up|headshot)\b",
    re.I,
)


def _infer_active_nudity_from_tags(
    tags: Sequence[PromptTag],
    nudity_constraint: Optional[NudityConstraint],
) -> Tuple[Optional[str], Optional[str]]:
    if nudity_constraint is not None and not nudity_constraint.yielded_to_override and nudity_constraint.effective_level:
        return nudity_constraint.effective_level, nudity_constraint.selected_branch
    for tag in tags:
        for a in tag.atoms:
            if a.source_slot == "nudity":
                if a.facts and len(a.facts.allowed_nudity_levels) == 1:
                    lvl = a.facts.allowed_nudity_levels[0]
                    br = a.facts.nudity_branches[0] if (a.facts.nudity_branches and len(a.facts.nudity_branches) == 1) else None
                    return lvl, br
                raw_id = ((a.source_item_id or "") + " " + (a.provenance.item_id if a.provenance else "")).upper()
                for lvl in VALID_NUDITY_LEVELS:
                    if lvl in raw_id:
                        return lvl, None
                if any(s in a.text.lower() for s in ("fully clothed", "completely covered")):
                    return "L1", "l1_full_clothed"
    return None, None


def _select_budget_core_tag_indices(
    deduped_tags: Sequence[PromptTag],
    active_lvl: Optional[str],
    active_branch: Optional[str],
) -> set[int]:
    """识别构成当前完整穿搭/分支必需载体的最低核心 tag 索引，在词数预算收紧时优先保护。"""
    core_indices: set[int] = set()
    clothing_entries: List[Tuple[int, List[PromptAtom]]] = []
    for idx, tag in enumerate(deduped_tags):
        cl_atoms = [a for a in tag.atoms if a.source_slot in ("clothing", "clothing_state", "underwear")]
        if cl_atoms:
            clothing_entries.append((idx, cl_atoms))

    if not clothing_entries:
        return core_indices

    def _entry_topologies(atoms_seq: Sequence[PromptAtom]) -> set[str]:
        topos: set[str] = set()
        for a in atoms_seq:
            if a.facts and a.facts.garment_topologies:
                topos.update(a.facts.garment_topologies)
        return topos

    def _entry_roles(atoms_seq: Sequence[PromptAtom]) -> set[str]:
        roles: set[str] = set()
        for a in atoms_seq:
            if a.facts and a.facts.piece_role:
                roles.add(a.facts.piece_role)
        return roles

    # 1. L5 discarded narrative protection
    if active_lvl == "L5":
        for idx, cl_atoms in clothing_entries:
            if any(a.facts and "discarded" in a.facts.garment_states for a in cl_atoms):
                core_indices.add(idx)
                break
        return core_indices

    # 2. L4 branch minimal-cover carrier protection
    if active_lvl == "L4":
        for idx, cl_atoms in clothing_entries:
            topos = _entry_topologies(cl_atoms)
            roles = _entry_roles(cl_atoms)
            if active_branch == "l4_hosiery_footwear_only" and (roles & {"legwear", "footwear"} or topos & {"legwear", "footwear"}):
                core_indices.add(idx)
            elif active_branch in ("l4_panties_only", "l4_bra_and_panties", None) and ("underwear" in topos or "underwear" in roles):
                core_indices.add(idx)
        return core_indices

    # 3. 检查是否已有 one_piece / ensemble_outfit 主锚点
    for idx, cl_atoms in clothing_entries:
        topos = _entry_topologies(cl_atoms)
        roles = _entry_roles(cl_atoms)
        if ("one_piece" in topos or "ensemble_outfit" in topos or "one_piece" in roles or "ensemble" in roles):
            core_indices.add(idx)
            return core_indices

    # 4. 分体上下装（top + bottom_pants/bottom_skirt）：成对保护主上装与主下装
    first_top_idx: Optional[int] = None
    first_bottom_idx: Optional[int] = None
    first_underwear_idx: Optional[int] = None
    for idx, cl_atoms in clothing_entries:
        topos = _entry_topologies(cl_atoms)
        roles = _entry_roles(cl_atoms)
        if first_top_idx is None and ("top" in topos or "top" in roles):
            first_top_idx = idx
        if first_bottom_idx is None and (topos & {"bottom_pants", "bottom_skirt"} or "bottom" in roles):
            first_bottom_idx = idx
        if first_underwear_idx is None and ("underwear" in topos or "underwear" in roles):
            first_underwear_idx = idx

    if active_lvl == "L3" and active_branch == "l3_panties_only":
        if first_underwear_idx is not None:
            core_indices.add(first_underwear_idx)
        return core_indices
    if active_lvl == "L3" and active_branch in ("l3_topless_skirt_kept", "l3_topless_pants_kept"):
        if first_bottom_idx is not None:
            core_indices.add(first_bottom_idx)
        return core_indices

    if first_top_idx is not None:
        core_indices.add(first_top_idx)
    if first_bottom_idx is not None:
        core_indices.add(first_bottom_idx)
    if not core_indices and first_underwear_idx is not None:
        core_indices.add(first_underwear_idx)
    return core_indices


def _tag_budget_priority_tier(idx: int, tag: PromptTag, core_tag_indices: set[int]) -> int:
    if any(is_external_atom(a) for a in tag.atoms):
        return 0
    if idx in core_tag_indices:
        return 1
    slots = {a.source_slot for a in tag.atoms}
    if "nudity" in slots:
        return 1
    if slots & {"clothing_extension"}:
        return 4
    if slots & {"clothing", "clothing_state", "underwear"}:
        # 套装配饰/修饰词 (accessory/necktie/pompoms) 降为 4，其余状态让位于 core_tag_indices
        roles = {a.facts.piece_role for a in tag.atoms if a.facts and a.facts.piece_role}
        if "accessory" in roles:
            return 4
        return 3
    if slots & {"scene_theme", "scene", "theme", "shot_type", "shot", "camera_angle", "camera", "character"}:
        if any(a.facts and a.facts.semantic_role == "scene_detail" for a in tag.atoms):
            return 4
        return 2
    if slots & {"quality"}:
        return 5
    if slots & {"jewelry", "tattoo", "props", "liquids"}:
        return 4
    return 3


def _load_nudity_level_fragments_for_preset(
    data_dir: Path,
    constraint: NudityConstraint,
    entry_point: str,
    start_order: int,
) -> List[PromptFragment]:
    eff_lvl = constraint.effective_level
    if eff_lvl not in VALID_NUDITY_LEVELS:
        return []
    if data_dir not in _NUDITY_LEVELS_DOC_CACHE:
        p = data_dir / "nudity_levels.json"
        if p.is_file():
            try:
                _NUDITY_LEVELS_DOC_CACHE[data_dir] = json.loads(p.read_text(encoding="utf-8")).get("nudity_levels", [])
            except Exception:
                _NUDITY_LEVELS_DOC_CACHE[data_dir] = []
        else:
            _NUDITY_LEVELS_DOC_CACHE[data_dir] = []
    levels = _NUDITY_LEVELS_DOC_CACHE[data_dir]
    lvl_item = next((it for it in levels if it.get("level") == eff_lvl), None)
    if not isinstance(lvl_item, dict):
        return []
    lvl_id = lvl_item.get("id", f"nudity_{eff_lvl.lower()}")
    raw_tags = list(lvl_item.get("tags", []))
    branch = constraint.selected_branch
    if branch:
        b_tags = [
            t for t in raw_tags
            if isinstance(t, dict) and branch in ((t.get("facts") or {}).get("nudity_branches") or ())
        ]
        if b_tags:
            raw_tags = b_tags
    if not raw_tags:
        return []
    picked = raw_tags[:2] if len(raw_tags) >= 2 else raw_tags[:1]
    origin = SelectionOrigin(
        entry_point=entry_point,
        mode="resolver",
        selector="nudity",
        selected_id=lvl_id,
        raw_value=constraint.raw_requested_value,
        parent_ids=(lvl_id,),
    )
    out: List[PromptFragment] = []
    cur_order = start_order
    for t in picked:
        if not isinstance(t, dict) or not t.get("text"):
            continue
        out.append(
            PromptFragment(
                text=t["text"],
                source_slot="nudity",
                source_item_id=lvl_id,
                order=cur_order,
                provenance=TagProvenance(
                    item_id=lvl_id,
                    kind="nudity",
                    parent_ids=(lvl_id,),
                    semantic_ids=(f"nudity:{eff_lvl}",),
                ),
                id=t.get("id", ""),
                facts=SemanticFacts.from_dict(t.get("facts", {})),
                origin=origin,
            )
        )
        cur_order += 1
    return out


def _is_preset_fragment_compatible_with_nudity(
    frag: PromptFragment,
    target_level: str,
    target_branch: Optional[str],
) -> bool:
    """在 UI 显式/覆盖裸露等级作用于预设与配方时，精准过滤不相容片段（保留 preset_native 原意不受影响）。"""
    f = frag.facts
    tl = frag.text.strip().lower()
    if f and f.allowed_nudity_levels and target_level not in f.allowed_nudity_levels:
        return False
    if target_level == "L1":
        if f and any(r in ("breasts", "genitals", "anus", "buttocks", "full_nude", "cleavage", "midriff", "underboob_sideboob", "underwear_panties", "underwear_bra", "shoulders") for r in f.exposed_regions):
            return False
        if f and any(r in ("intimate_lower_body", "crotch", "breasts", "buttocks", "underboob", "sideboob") for r in f.visible_regions):
            return False
        if _PRESET_L1_BANNED_RE.search(tl):
            return False
        return True
    if target_level == "L2":
        if f and any(r in ("breasts", "genitals", "anus", "buttocks", "full_nude") for r in f.exposed_regions):
            return False
        if f and any(r in ("intimate_lower_body", "crotch") for r in f.visible_regions):
            return False
        if _PRESET_L2_BANNED_RE.search(tl):
            return False
        return True
    if target_level in ("L5", "L6"):
        if f and any(r == "none" for r in f.exposed_regions):
            return False
        if _PRESET_WORN_CLOTHING_RE.search(tl) and "no panties" not in tl and "no bra" not in tl:
            return False
        if frag.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear") and "no panties" not in tl and "no bra" not in tl:
            return False
        if _PRESET_L56_SUPERSEDED_NUDITY_RE.search(tl):
            return False
        if target_level == "L6" and (frag.source_slot in ("shot_type", "shot", "preset_shot_type") or _PRESET_L6_UPPER_FRAMING_RE.search(tl)):
            if _PRESET_L6_UPPER_FRAMING_RE.search(tl):
                return False
            if f and f.visible_regions:
                vis = set(f.visible_regions)
                if (vis & {"face", "upper_body"}) and not (vis & {"lower_body", "intimate_lower_body", "full_body", "legs"}):
                    return False
        return True
    if target_level == "L4":
        if f and any(t in ("top", "bottom_pants", "bottom_skirt", "outerwear", "one_piece", "ensemble_outfit") for t in f.garment_topologies):
            return False
        if _PRESET_WORN_CLOTHING_RE.search(tl) and not any(w in tl for w in ("panties", "bra", "stockings", "socks", "heels", "towel", "lingerie", "no panties", "no bra")):
            return False
        return True
    return True


def assemble_result(
    fragments: Sequence[PromptFragment | str] | PromptFragment | str,
    data_dir: str | Path,
    rng: Optional[Random] = None,
    max_words: int = MAX_PROMPT_WORDS,
    resolver: Optional[ConflictResolver] = None,
    context_profile: Optional[ContextProfile] = None,
    overrides: Sequence[OverrideRecord] = (),
    nudity_constraint: Optional[NudityConstraint] = None,
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

    # 将外部覆盖记录中的注入 ID 映射为真实的 authoritative atom_id
    if overrides:
        updated_overrides = []
        for o in overrides:
            if o.status == "applied":
                matched_atom_ids = tuple(
                    a.atom_id for a in raw_atoms
                    if a.origin and a.origin.mode == "override" and a.source_slot == o.canonical_slot
                )
                if not matched_atom_ids:
                    matched_atom_ids = tuple(
                        a.atom_id for a in raw_atoms
                        if a.origin and a.origin.mode == "override" and (
                            a.source_slot == o.canonical_slot
                            or (a.provenance and o.canonical_slot in a.provenance.semantic_ids)
                        )
                    )
                updated_overrides.append(
                    OverrideRecord(
                        canonical_slot=o.canonical_slot,
                        status=o.status,
                        source_port=o.source_port,
                        raw_input=o.raw_input,
                        blank_fallback_reason=o.blank_fallback_reason,
                        replaced_target_ids=o.replaced_target_ids,
                        replaced_texts=o.replaced_texts,
                        injected_atom_ids=matched_atom_ids if matched_atom_ids else o.injected_atom_ids,
                    )
                )
            else:
                updated_overrides.append(o)
        overrides = tuple(updated_overrides)

    if max_words == 0:
        budget_records = tuple(
            BudgetFilterRecord(
                atom_id=a.atom_id,
                word_budget=0,
                used_words=0,
                candidate_words=len(a.text.split()),
                reason="word_budget_exceeded",
            )
            for a in source_atoms
        )
        return AssemblyResult(
            prompt="",
            accepted_atoms=(),
            source_atoms=source_atoms,
            rules_applied=(),
            budget_filtered_atoms=source_atoms,
            budget_filter_records=budget_records,
            overrides=tuple(overrides) if overrides else build_default_canonical_override_records(),
        )

    # 2. 结构化冲突消解 (支持外部注入复用 Resolver，避免重复加载大型规则配置)
    if resolver is None:
        cache_key = str(Path(data_dir).resolve())
        if cache_key not in _RESOLVER_CACHE:
            _RESOLVER_CACHE[cache_key] = ConflictResolver(Path(data_dir))
        resolver = _RESOLVER_CACHE[cache_key]
    try:
        resolved_atoms, rules_applied, resolution_report = resolver.resolve_atoms_with_full_report(
            raw_atoms, rng, context_profile, nudity_constraint=nudity_constraint
        )
    except (UnresolvedConflictError, PromptValidationError) as e:
        e.overrides = tuple(overrides) if overrides else build_default_canonical_override_records()
        e.source_atoms = tuple(source_atoms)
        e.raw_atoms = tuple(raw_atoms)
        e.context_profile = context_profile
        raise

    # 3. 按 tag_order 汇聚为 PromptTag，并执行完整 Tag 级保序去重
    resolved_tags = atoms_to_tags(resolved_atoms)
    deduped_tags, dropped_dedup_tags, dedup_records = deduplicate_tags_with_records(resolved_tags)
    deduplicated_atoms = [a for t in dropped_dedup_tags for a in t.atoms]

    # 4. 严谨的词数原子预算管理 (外部优先 + 核心穿搭与等级锚点优先级保护 + Fail-Closed 校验)
    accepted_atoms: List[PromptAtom] = []
    accepted_tag_texts: List[str] = []
    budget_filtered_atoms: List[PromptAtom] = []
    budget_filter_records: List[BudgetFilterRecord] = []
    current_word_count = 0

    # 4.1 预先核算全部外部内容与单原子长度限制（外部超预算优先准确归类为 external_budget_exceeded）
    non_empty_tags: List[PromptTag] = [tag for tag in deduped_tags if tag.text]

    ext_tags = [t for t in non_empty_tags if any(is_external_atom(a) for a in t.atoms)]
    total_ext_words = sum(len(t.text.split()) for t in ext_tags)
    if total_ext_words > max_words:
        raise PromptValidationError(
            f"External overrides exceed maximum allowed budget of {max_words} words ({total_ext_words} words)",
            category="external_budget_exceeded",
        )

    if not is_fallback:
        for tag in non_empty_tags:
            for a in tag.atoms:
                atom_words = len(a.text.split())
                if atom_words > max_words:
                    raise PromptValidationError(
                        f"Single atomic span exceeds {max_words} words limit ({atom_words} words): '{a.text[:50]}...'",
                        category="external_budget_exceeded",
                    )

    eff_nc = resolution_report.nudity_constraint if (resolution_report and resolution_report.nudity_constraint is not None) else nudity_constraint
    active_lvl, active_branch = _infer_active_nudity_from_tags(non_empty_tags, eff_nc)
    total_non_empty_words = sum(len(t.text.split()) for t in non_empty_tags)

    if total_non_empty_words <= max_words:
        for tag in non_empty_tags:
            accepted_atoms.extend(tag.atoms)
            accepted_tag_texts.append(tag.text)
            current_word_count += len(tag.text.split())
    else:
        core_tag_indices = _select_budget_core_tag_indices(non_empty_tags, active_lvl, active_branch)
        kept_indices: set[int] = set()
        remaining_budget = max_words - total_ext_words
        non_ext_ranked: List[Tuple[int, int, PromptTag]] = []
        for idx, tag in enumerate(non_empty_tags):
            if any(is_external_atom(a) for a in tag.atoms):
                kept_indices.add(idx)
            else:
                tier = _tag_budget_priority_tier(idx, tag, core_tag_indices)
                non_ext_ranked.append((tier, idx, tag))

        non_ext_ranked.sort(key=lambda x: (x[0], x[1]))
        for _, idx, tag in non_ext_ranked:
            tag_words = len(tag.text.split())
            if tag_words <= remaining_budget:
                kept_indices.add(idx)
                remaining_budget -= tag_words

        for idx, tag in enumerate(non_empty_tags):
            tag_words = len(tag.text.split())
            if idx in kept_indices:
                accepted_atoms.extend(tag.atoms)
                accepted_tag_texts.append(tag.text)
                current_word_count += tag_words
            else:
                budget_filtered_atoms.extend(tag.atoms)
                for a in tag.atoms:
                    budget_filter_records.append(
                        BudgetFilterRecord(
                            atom_id=a.atom_id,
                            word_budget=max_words,
                            used_words=current_word_count,
                            candidate_words=tag_words,
                            reason="word_budget_exceeded",
                        )
                    )

        # 4.2 预算裁剪后针对实际 accepted_atoms 执行完整穿搭复验：禁止把已截断的服装仍算作完整穿搭
        if not is_fallback and active_lvl in ("L1", "L2", "L3") and any(
            a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear")
            for a in budget_filtered_atoms
        ):
            pre_cl_atoms = [
                a for t in non_empty_tags for a in t.atoms
                if a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear")
            ]
            post_cl_atoms = [
                a for a in accepted_atoms
                if a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear")
            ]
            if pre_cl_atoms and not any(is_external_atom(a) for a in pre_cl_atoms):
                pre_assess = assess_clothing_completeness(
                    pre_cl_atoms, (), nudity_level_code=active_lvl, nudity_branch=active_branch
                )
                post_assess = assess_clothing_completeness(
                    post_cl_atoms, (), nudity_level_code=active_lvl, nudity_branch=active_branch
                )
                if pre_assess.is_complete and not post_assess.is_complete:
                    dropped_cl = [
                        a.text for a in budget_filtered_atoms
                        if a.source_slot in ("clothing", "clothing_state", "clothing_extension", "underwear")
                    ]
                    raise PromptValidationError(
                        f"Word budget ({max_words}) cannot retain a complete outfit under {active_lvl} "
                        f"(missing_after_budget={post_assess.missing_parts}, dropped_clothing={dropped_cl})",
                        category="external_budget_exceeded",
                        error_code="insufficient_budget_for_complete_outfit",
                        slots=("clothing",),
                        texts=dropped_cl,
                        details={
                            "max_words": max_words,
                            "level": active_lvl,
                            "branch": active_branch,
                            "missing_parts": list(post_assess.missing_parts),
                            "dropped_clothing": dropped_cl,
                        },
                    )

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
        context_profile=context_profile,
        resolution_report=resolution_report,
        deduplicated_atoms=tuple(deduplicated_atoms),
        budget_filtered_atoms=tuple(budget_filtered_atoms),
        deduplication_records=tuple(dedup_records),
        budget_filter_records=tuple(budget_filter_records),
        overrides=tuple(overrides) if overrides else build_default_canonical_override_records(),
        nudity_constraint=eff_nc,
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

        item_list = [items] if isinstance(items, (str, PromptFragment, SampledTag)) else list(items)
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
                    id=item.id,
                    facts=item.facts,
                    origin=item.origin,
                )
            elif isinstance(item, SampledTag):
                prov = item.provenance if item.provenance is not None else TagProvenance(kind="user_input", semantic_ids=(f"slot:{slot_name}",))
                yield PromptFragment(
                    text=item.text,
                    source_slot=slot_name,
                    source_item_id=prov.item_id,
                    order=0,
                    provenance=prov,
                    id=item.id,
                    facts=item.facts,
                    origin=item.origin,
                )
            elif isinstance(item, str):
                raw_item = item
                custom_origin = SelectionOrigin(
                    entry_point="custom_combiner",
                    mode="custom",
                    selector=slot_name,
                    raw_value=raw_item,
                )
                for st in split_top_level_tags(item.strip()):
                    if st:
                        yield PromptFragment(
                            text=st,
                            source_slot=slot_name,
                            provenance=TagProvenance(kind="user_input", semantic_ids=(f"slot:{slot_name}",)),
                            origin=custom_origin,
                        )


def make_override_fragments(
    slot_name: str,
    raw_val: Any,
    entry_point: str = "generator",
    catalog_facts: Optional[Dict[Tuple[str, str], SemanticFacts]] = None,
    data_dir: Optional[Path] = None,
) -> List[PromptFragment]:
    """将外部覆盖原始值切分为合法的 PromptFragment 列表，并标记 mode="override" 与 source_mode="override"."""
    tag_texts: List[str] = []
    if isinstance(raw_val, str):
        tag_texts = [t.strip() for t in split_top_level_tags(raw_val) if t.strip()]
    elif hasattr(raw_val, "text"):
        tag_texts = [t.strip() for t in split_top_level_tags(str(raw_val.text)) if t.strip()]
    elif isinstance(raw_val, dict) and "text" in raw_val:
        tag_texts = [t.strip() for t in split_top_level_tags(str(raw_val["text"])) if t.strip()]
    elif isinstance(raw_val, (list, tuple)):
        for item in raw_val:
            if isinstance(item, str):
                for sub in split_top_level_tags(item):
                    if sub.strip():
                        tag_texts.append(sub.strip())
            elif hasattr(item, "text"):
                for sub in split_top_level_tags(str(item.text)):
                    if sub.strip():
                        tag_texts.append(sub.strip())
            elif isinstance(item, dict) and "text" in item:
                for sub in split_top_level_tags(str(item["text"])):
                    if sub.strip():
                        tag_texts.append(sub.strip())
            else:
                for sub in split_top_level_tags(str(item)):
                    if sub.strip():
                        tag_texts.append(sub.strip())
    else:
        tag_texts = [t.strip() for t in split_top_level_tags(str(raw_val)) if t.strip()]

    new_frags: List[PromptFragment] = []
    frag_entry_point = entry_point or "generator"
    raw_value_canonical = normalize_raw_input_text(raw_val)
    slot_selected_id = f"override_{slot_name}"
    slot_origin = SelectionOrigin(
        entry_point=frag_entry_point,
        mode="override",
        selector=slot_name,
        selected_id=slot_selected_id,
        raw_value=raw_value_canonical,
        sampling_rationale={
            "active_contexts": [],
            "candidate_filter": {
                "total_candidates": 1,
                "excluded_hard_conflicts": [],
                "passed_candidates": 1,
            },
            "distribution": {},
            "fallback": {
                "used": False,
                "reason": None,
            },
            "final_selection": {
                "selected_id": slot_selected_id,
                "mode": "override",
            },
        },
    )

    for order, tag_text in enumerate(tag_texts):
        facts = SemanticFacts()
        t_lower = tag_text.lower().strip()
        item_id = f"override_{slot_name}_{order:03d}"

        # 优先从目录已知权威事实继承 (例如 holding game controller -> hands_required: 2)
        if data_dir is not None:
            _, facts_cache = get_catalog_tag_cache(Path(data_dir))
            if t_lower in facts_cache:
                facts = facts_cache[t_lower]

        if slot_name == "nudity":
            if not facts.visible_regions:
                if any(w in t_lower for w in ("completely nude", "bare body", "nude", "naked", "unclothed", "full nude")):
                    facts = SemanticFacts(visible_regions=("full_body",))
                    item_id = f"nudity_l5_override_{order:03d}"
                elif any(w in t_lower for w in ("topless", "bare breasts", "exposed breasts")):
                    facts = SemanticFacts(visible_regions=("breasts", "topless"))
                    item_id = f"nudity_l4_override_{order:03d}"
                elif any(w in t_lower for w in ("micro bikini", "sheer lingerie", "revealing panties")):
                    facts = SemanticFacts(visible_regions=("crotch", "buttocks"))
                    item_id = f"nudity_l3_override_{order:03d}"
        elif slot_name == "props":
            if facts.hands_required == 0 and facts.prop_usage is None:
                if any(w in t_lower for w in ("holding", "in hand", "held", "carrying", "camera in hand")):
                    facts = SemanticFacts(prop_usage="handheld", hands_required=1)
        elif slot_name == "pose":
            if facts.hand_state is None and facts.hands_required == 0:
                if any(w in t_lower for w in ("hands behind back", "hands tied", "arms above head", "tied behind back")):
                    facts = SemanticFacts(hand_state="both_busy", hands_required=2)
                elif any(w in t_lower for w in ("hand on cheek", "hand on hip", "hand on", "touching", "tracing", "hand tracing", "other hand", "one hand")):
                    facts = SemanticFacts(hand_state="one_busy", hands_required=1)
        elif slot_name == "clothing":
            if not facts.garment_topologies:
                if any(w in t_lower for w in ("catsuit", "latex suit", "bodysuit", "swimsuit", "leotard", "one-piece")):
                    facts = SemanticFacts(garment_topologies=("one_piece",), fabric_materials=("latex",) if "latex" in t_lower else ())
                elif any(w in t_lower for w in ("parka", "down coat", "jacket", "coat", "winter boots", "scarf")):
                    facts = SemanticFacts(garment_topologies=("outerwear", "bottom_pants"))

        frag = PromptFragment(
            text=tag_text,
            source_slot=slot_name,
            source_item_id=item_id,
            order=order,
            provenance=TagProvenance(
                item_id=item_id,
                kind="user_input",
                semantic_ids=(f"slot_override:{slot_name}",),
                source_mode="override",
            ),
            id=item_id,
            facts=facts,
            origin=slot_origin,
        )
        new_frags.append(frag)
    return new_frags


def apply_slot_overrides(
    slot_fragments: Dict[str, Any],
    external_overrides: Optional[Dict[str, Any]],
    entry_point: str = "generator",
    catalog_facts: Optional[Dict[Tuple[str, str], SemanticFacts]] = None,
    data_dir: Optional[Path] = None,
    return_records: bool = False,
) -> Dict[str, List[Any]] | Tuple[Dict[str, List[Any]], Tuple[OverrideRecord, ...]]:
    """落实 20 槽位外部覆盖契约 (M3 核心契约)：
    1. 非空覆盖整个槽位：外部指定非空内容时，替换该槽位全部系统/预设生成 tags，绝不混杂；
    2. 空白回退：外部输入为 None, "", "   " 或 [] 时，完全回退保留内部/预设生成内容；
    3. 整槽清空联动：当覆盖 clothing / scene_theme 等槽位时，连带清空旧附属与别名槽位；
    4. 权威 Provenance 与来源追踪：标记 mode="override", kind="user_input", source_mode="override"。
    """
    if not external_overrides:
        res = {k: list(v) if isinstance(v, list) else v for k, v in slot_fragments.items()}
        if return_records:
            return res, build_default_canonical_override_records()
        return res

    res: Dict[str, List[Any]] = {k: list(v) if isinstance(v, list) else v for k, v in slot_fragments.items()}
    applied_records: Dict[str, OverrideRecord] = {}

    for raw_slot, raw_val in external_overrides.items():
        norm_slot = normalize_slot_name(raw_slot)
        if norm_slot not in ALLOWED_SLOTS:
            raise PromptValidationError(
                f"Unknown slot '{raw_slot}' (normalized: '{norm_slot}') in external overrides. Allowed slots: {sorted(ALLOWED_SLOTS)}",
                slots=(raw_slot,),
            )

        is_blank, blank_reason = _inspect_blank_input(raw_val)
        if is_blank:
            applied_records[norm_slot] = OverrideRecord(
                canonical_slot=norm_slot,
                status="fallback_blank",
                source_port=raw_slot,
                raw_input=raw_val,
                blank_fallback_reason=blank_reason,
                replaced_target_ids=(),
                replaced_texts=(),
                injected_atom_ids=(),
            )
            continue

        replaced_items: List[Any] = []
        if norm_slot in res and isinstance(res[norm_slot], list):
            replaced_items.extend(res[norm_slot])

        # 非空覆盖整个槽位：清空旧内容
        res[norm_slot] = []

        # 槽位整槽清空旧载体事实与状态 (CE-OVERRIDE-01)
        if norm_slot == "clothing":
            for related in ("clothing_state", "clothing_extension", "underwear"):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "scene_theme":
            for related in ("scene", "theme"):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "lighting":
            for related in ("lighting_palette",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "pose":
            for related in ("poses",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "props":
            for related in ("prop",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "shot_type":
            for related in ("shot",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "camera_angle":
            for related in ("camera", "angle"):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "tattoo":
            for related in ("tattoos",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot == "liquids":
            for related in ("liquid",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
        elif norm_slot in ("style_recipe", "recipe"):
            for related in ("recipe",):
                if related in res and isinstance(res[related], list):
                    replaced_items.extend(res[related])
                    res[related] = []
            # 覆盖风格配方：从所有槽位中彻底清除来自原配方的全部内容
            for k in list(res.keys()):
                if isinstance(res[k], list):
                    purged_from_k = []
                    kept_in_k = []
                    for item in res[k]:
                        if (
                            (getattr(item, "origin", None) and getattr(item.origin, "mode", None) == "recipe")
                            or (getattr(item, "provenance", None) and getattr(item.provenance, "kind", None) == "style_recipe")
                            or (getattr(item, "origin", None) and getattr(item.origin, "selector", None) == "style_recipe")
                        ):
                            purged_from_k.append(item)
                        else:
                            kept_in_k.append(item)
                    replaced_items.extend(purged_from_k)
                    res[k] = kept_in_k

        # 若存在 preset_core，清洗属于被覆盖槽位的旧预设内容
        if "preset_core" in res and isinstance(res["preset_core"], list):
            target_purged_slots = {norm_slot}
            if norm_slot == "clothing":
                target_purged_slots.update(["clothing", "clothing_state", "clothing_extension", "underwear"])
            elif norm_slot == "scene_theme":
                target_purged_slots.update(["scene_theme", "scene", "theme"])
            elif norm_slot == "lighting":
                target_purged_slots.update(["lighting", "lighting_palette"])
            elif norm_slot == "pose":
                target_purged_slots.update(["pose", "poses"])
            elif norm_slot == "props":
                target_purged_slots.update(["props", "prop"])
            elif norm_slot == "shot_type":
                target_purged_slots.update(["shot_type", "shot"])
            elif norm_slot == "camera_angle":
                target_purged_slots.update(["camera_angle", "camera", "angle"])
            elif norm_slot == "tattoo":
                target_purged_slots.update(["tattoo", "tattoos"])
            elif norm_slot == "liquids":
                target_purged_slots.update(["liquids", "liquid"])
            cleaned_core = []
            for item in res["preset_core"]:
                item_text = getattr(item, "text", str(item))
                item_slot = getattr(item, "source_slot", "")
                if data_dir is not None and (not item_slot or item_slot in ("preset_core", norm_slot)):
                    cls_slot, _ = classify_preset_tag_and_facts(item_text, Path(data_dir))
                else:
                    cls_slot = item_slot
                if cls_slot in target_purged_slots or item_slot in target_purged_slots:
                    replaced_items.append(item)
                else:
                    cleaned_core.append(item)
            res["preset_core"] = cleaned_core

        replaced_ids = tuple(
            str(getattr(it, "id", None) or getattr(it, "source_item_id", None) or (it.atom_id if hasattr(it, "atom_id") else str(it)))
            for it in replaced_items
            if (getattr(it, "id", None) or getattr(it, "source_item_id", None) or hasattr(it, "atom_id"))
        )
        replaced_texts = tuple(
            str(getattr(it, "text", it))
            for it in replaced_items
            if str(getattr(it, "text", it)).strip()
        )

        new_frags = make_override_fragments(
            norm_slot,
            raw_val,
            entry_point=entry_point,
            catalog_facts=catalog_facts,
            data_dir=data_dir,
        )
        res[norm_slot] = new_frags
        injected_ids = tuple(str(f.id) for f in new_frags if getattr(f, "id", None))

        applied_records[norm_slot] = OverrideRecord(
            canonical_slot=norm_slot,
            status="applied",
            source_port=raw_slot,
            raw_input=raw_val,
            blank_fallback_reason=None,
            replaced_target_ids=replaced_ids,
            replaced_texts=replaced_texts,
            injected_atom_ids=injected_ids,
        )

    canonical_records = []
    for s in CANONICAL_20_SLOTS:
        if s in applied_records:
            canonical_records.append(applied_records[s])
        else:
            canonical_records.append(
                OverrideRecord(
                    canonical_slot=s,
                    status="not_provided",
                    source_port=None,
                    raw_input=None,
                    blank_fallback_reason="omitted",
                    replaced_target_ids=(),
                    replaced_texts=(),
                    injected_atom_ids=(),
                )
            )

    if return_records:
        return res, tuple(canonical_records)
    return res


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
        max_words: int = MAX_PROMPT_WORDS,
        context_profile: Optional[ContextProfile] = None,
        external_overrides: Optional[Dict[str, Any]] = None,
        entry_point: str = "generator",
        nudity_constraint: Optional[NudityConstraint] = None,
    ) -> AssemblyResult:
        """从各槽位片段字典执行装配，返回强类型 AssemblyResult。"""
        override_records: Tuple[OverrideRecord, ...] = ()
        if external_overrides:
            slot_fragments, override_records = apply_slot_overrides(
                slot_fragments,
                external_overrides,
                entry_point=entry_point,
                catalog_facts=getattr(self.resolver, "canonical_catalog_facts", None),
                data_dir=self.data_dir,
                return_records=True,
            )
        else:
            override_records = build_default_canonical_override_records()
        fragments = list(iter_normalized_slot_fragments(slot_fragments))
        return assemble_result(
            fragments,
            self.data_dir,
            rng,
            max_words,
            resolver=self.resolver,
            context_profile=context_profile,
            overrides=override_records,
            nudity_constraint=nudity_constraint,
        )

    def assemble_result_with_sources(
        self,
        slot_fragments: Dict[str, List[Any]],
        rng: Optional[Random] = None,
        max_words: int = MAX_PROMPT_WORDS,
        context_profile: Optional[ContextProfile] = None,
    ) -> Tuple[str, Tuple[PromptAtom, ...], Tuple[str, ...], Tuple[PromptAtom, ...]]:
        """兼容接口：从各槽位片段字典执行装配，返回 (prompt_str, atoms, rules_applied, source_atoms)。"""
        res = self.assemble_slots(slot_fragments, rng, max_words, context_profile=context_profile)
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
                    id=frag.id,
                    facts=frag.facts,
                    origin=frag.origin,
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
        entry_point: str = "preset_browser",
        preset_raw_value: Optional[str] = None,
        recipe_raw_value: Optional[str] = None,
        extra_fragments: Sequence[PromptFragment] = (),
        external_overrides: Optional[Dict[str, Any]] = None,
        nudity_constraint: Optional[NudityConstraint] = None,
    ) -> AssemblyResult:
        """预设模板与风格配方统一装配接口，返回包含 source_atoms 的不可变 AssemblyResult。"""
        if rng is None:
            rng = Random(42)

        fragments: List[PromptFragment] = []
        order = 0

        # 1. 预设核心 Prompt (优先消费结构化 fragments)
        preset_id = preset.get("id", "preset_custom")
        raw_p = preset_raw_value or preset.get("ui_name") or preset.get("name_zh") or preset_id
        preset_origin = SelectionOrigin(
            entry_point=entry_point,
            mode="preset",
            selector="preset_core",
            selected_id=preset_id,
            raw_value=raw_p,
            parent_ids=(preset_id,),
        )
        preset_frags = preset.get("fragments")
        if preset_frags and isinstance(preset_frags, list):
            for f_data in preset_frags:
                text = f_data.get("text", "")
                if text:
                    f_id = f_data.get("id", "")
                    f_entity_id = f_data.get("entity_id") or f_data.get("item_id") or f_data.get("prop_id")
                    f_slot = f_data.get("slot")
                    raw_facts = f_data.get("facts", {})
                    f_facts = SemanticFacts.from_dict(raw_facts)
                    if f_slot in (None, "", "preset_core"):
                        f_slot, inferred_facts = classify_preset_tag_and_facts(text, Path(self.data_dir))
                        f_facts = inferred_facts.merge(f_facts)
                    elif f_slot in ALLOWED_SLOTS:
                        _, inferred_facts = classify_preset_tag_and_facts(text, Path(self.data_dir), target_slot=f_slot)
                        f_facts = inferred_facts.merge(f_facts)
                    sid = f_entity_id if f_entity_id else preset_id
                    pid = f_entity_id if f_entity_id else (f_id or preset_id)
                    fragments.append(
                        PromptFragment(
                            text=text,
                            source_slot=f_slot,
                            source_item_id=sid,
                            order=order,
                            provenance=TagProvenance(
                                item_id=pid,
                                kind="preset",
                                semantic_ids=(f"preset:{preset_id}",),
                                parent_ids=(preset_id,),
                            ),
                            id=f_id,
                            facts=f_facts,
                            origin=preset_origin,
                            target_id=f_entity_id,
                            entity_id=f_entity_id,
                        )
                    )
                    order += 1
        else:
            raw_preset_prompt = preset.get("positive", preset.get("prompt", ""))
            for t in split_top_level_tags(raw_preset_prompt):
                if t:
                    f_slot, f_facts = classify_preset_tag_and_facts(t, Path(self.data_dir))
                    fragments.append(
                        PromptFragment(
                            text=t,
                            source_slot=f_slot,
                            source_item_id=preset_id,
                            order=order,
                            provenance=TagProvenance(
                                item_id=preset_id,
                                kind="preset",
                                semantic_ids=(f"preset:{preset_id}",),
                                parent_ids=(preset_id,),
                            ),
                            origin=preset_origin,
                            facts=f_facts,
                        )
                    )
                    order += 1

        # 2. 风格配方叠加 (优先消费结构化 fragments)
        if style_recipe:
            recipe_id = style_recipe.get("id", "recipe_custom")
            raw_r = recipe_raw_value or style_recipe.get("ui_name") or style_recipe.get("style_name") or style_recipe.get("name_zh") or recipe_id
            recipe_origin = SelectionOrigin(
                entry_point=entry_point,
                mode="recipe",
                selector="style_recipe",
                selected_id=recipe_id,
                raw_value=raw_r,
                parent_ids=(recipe_id,),
            )
            recipe_frags = style_recipe.get("fragments")
            if recipe_frags and isinstance(recipe_frags, list):
                for f_data in recipe_frags:
                    text = f_data.get("text", "")
                    if text:
                        f_id = f_data.get("id", "")
                        f_entity_id = f_data.get("entity_id") or f_data.get("item_id") or f_data.get("prop_id")
                        f_slot = f_data.get("slot")
                        raw_facts = f_data.get("facts", {})
                        f_facts = SemanticFacts.from_dict(raw_facts)
                        if f_slot in (None, "", "style_recipe", "preset_core"):
                            f_slot, inferred_facts = classify_preset_tag_and_facts(text, Path(self.data_dir))
                            f_facts = inferred_facts.merge(f_facts)
                        elif f_slot in ALLOWED_SLOTS:
                            _, inferred_facts = classify_preset_tag_and_facts(text, Path(self.data_dir), target_slot=f_slot)
                            f_facts = inferred_facts.merge(f_facts)
                        sid = f_entity_id if f_entity_id else recipe_id
                        pid = f_entity_id if f_entity_id else (f_id or recipe_id)
                        fragments.append(
                            PromptFragment(
                                text=text,
                                source_slot=f_slot,
                                source_item_id=sid,
                                order=order,
                                provenance=TagProvenance(
                                    item_id=pid,
                                    kind="style_recipe",
                                    semantic_ids=(f"recipe:{recipe_id}",),
                                    parent_ids=(recipe_id,),
                                ),
                                id=f_id,
                                facts=f_facts,
                                origin=recipe_origin,
                                target_id=f_entity_id,
                                entity_id=f_entity_id,
                            )
                        )
                        order += 1
            else:
                for k in ["lighting_palette", "style_recipe", "focus_detail"]:
                    val = style_recipe.get(k, "")
                    if val:
                        for t in split_top_level_tags(str(val)):
                            if t:
                                f_slot, f_facts = classify_preset_tag_and_facts(t, Path(self.data_dir))
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
                                        origin=recipe_origin,
                                        facts=f_facts,
                                    )
                                )
                                order += 1

        # 3. 画质等级锚点 (稳定内部 quality ID 与事实)
        q_str = str(quality_tier or "").lower()
        if "cctv" in q_str or "监控" in q_str:
            quality_id = "quality_cctv"
            q_tags = ["CCTV footage", "security camera", "low resolution", "grainy"]
            q_facts = SemanticFacts(semantic_role="quality", quality_class="cctv", capture_device="cctv")
        elif "phone" in q_str or "手机" in q_str:
            quality_id = "quality_phone"
            q_tags = ["phone camera", "selfie", "amateur photo", "slightly blurry"]
            q_facts = SemanticFacts(semantic_role="quality", quality_class="phone", capture_device="phone")
        elif "masterpiece" in q_str or "顶尖" in q_str:
            quality_id = "quality_masterpiece"
            q_tags = ["masterpiece", "best quality", "ultra detailed", "8k", "photorealistic"]
            q_facts = SemanticFacts(semantic_role="quality", quality_class="masterpiece", capture_device="professional")
        else:
            quality_id = "quality_standard"
            q_tags = ["best quality", "masterpiece", "high resolution", "photorealistic"]
            q_facts = SemanticFacts(semantic_role="quality", quality_class="standard", capture_device="professional")

        quality_origin = SelectionOrigin(
            entry_point=entry_point,
            mode="explicit",
            selector="quality",
            selected_id=quality_id,
            raw_value=quality_tier,
            parent_ids=(quality_id,),
        )

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
                    id=f"{quality_id}__tag_{order:03d}",
                    facts=q_facts,
                    origin=quality_origin,
                )
            )
            order += 1

        if extra_fragments:
            fragments.extend(extra_fragments)

        # 3b. 当存在显式/随机 UI 或外部裸露等级约束（非 preset_native）时，对预设与配方片段执行统一等级过滤与锚点适配
        preset_adjusted = False
        active_preset_lvl = (
            nudity_constraint.effective_level
            if (
                nudity_constraint is not None
                and nudity_constraint.effective_level in VALID_NUDITY_LEVELS
                and nudity_constraint.source != "preset_native"
            )
            else None
        )
        has_ext_nudity_override = bool(
            isinstance(external_overrides, dict)
            and "nudity" in external_overrides
            and not _inspect_blank_input(external_overrides.get("nudity"))[0]
        )
        if active_preset_lvl and not (external_overrides and "clothing" in external_overrides and not _inspect_blank_input(external_overrides.get("clothing"))[0] and nudity_constraint and nudity_constraint.yielded_to_override):
            preset_non_quality = [
                f for f in fragments
                if f.origin and f.origin.mode == "preset" and f.source_slot != "quality"
            ]
            filtered_frags: List[PromptFragment] = []
            for f in fragments:
                if f.source_slot == "quality" or f in extra_fragments:
                    filtered_frags.append(f)
                    continue
                if _is_preset_fragment_compatible_with_nudity(
                    f, active_preset_lvl, nudity_constraint.selected_branch if nudity_constraint else None
                ):
                    filtered_frags.append(f)
                else:
                    preset_adjusted = True

            surviving_preset_non_quality = [
                f for f in filtered_frags
                if f.origin and f.origin.mode == "preset" and f.source_slot != "quality"
            ]
            if preset_non_quality and not surviving_preset_non_quality:
                raise PromptValidationError(
                    f"Preset '{preset_id}' core content cannot satisfy target nudity level {active_preset_lvl}",
                    category="unresolved_hard_conflict",
                    error_code="preset_incompatible_with_nudity_level",
                    slots=("nudity", "clothing"),
                    texts=[f.text for f in preset_non_quality],
                    details={
                        "preset_id": preset_id,
                        "nudity_constraint": nudity_constraint.to_dict() if nudity_constraint else None,
                    },
                )

            if not has_ext_nudity_override:
                existing_nudity = any(f.source_slot == "nudity" for f in filtered_frags)
                if not existing_nudity:
                    nudity_frags = _load_nudity_level_fragments_for_preset(
                        Path(self.data_dir),
                        nudity_constraint,
                        entry_point=entry_point,
                        start_order=order,
                    )
                    if nudity_frags:
                        # 将等级锚点插入 preset 核心之后 / quality 之前，保留 canonical order
                        insert_pos = len(filtered_frags)
                        for idx_f, ff in enumerate(filtered_frags):
                            if ff.source_slot == "quality" or ff in extra_fragments:
                                insert_pos = idx_f
                                break
                        filtered_frags = filtered_frags[:insert_pos] + nudity_frags + filtered_frags[insert_pos:]
                        order += len(nudity_frags)
                        preset_adjusted = True

            # 重新顺排 order，确保碎片连续单调递增
            fragments = [
                replace(f, order=i) if f.order != i else f
                for i, f in enumerate(filtered_frags)
            ]

        override_records: Tuple[OverrideRecord, ...] = ()
        if external_overrides:
            applied_records: Dict[str, OverrideRecord] = {}
            overridden_slots: Set[str] = set()
            new_override_frags: List[PromptFragment] = []
            for raw_s, raw_v in external_overrides.items():
                ns = normalize_slot_name(raw_s)
                if ns not in ALLOWED_SLOTS:
                    raise PromptValidationError(
                        f"Unknown slot '{raw_s}' in external overrides. Allowed slots: {sorted(ALLOWED_SLOTS)}",
                        slots=(raw_s,),
                    )
                is_blank, blank_reason = _inspect_blank_input(raw_v)
                if is_blank:
                    applied_records[ns] = OverrideRecord(
                        canonical_slot=ns,
                        status="fallback_blank",
                        source_port=raw_s,
                        raw_input=raw_v,
                        blank_fallback_reason=blank_reason,
                        replaced_target_ids=(),
                        replaced_texts=(),
                        injected_atom_ids=(),
                    )
                    continue

                overridden_slots.add(ns)
                target_purged_slots = {ns}
                if ns == "clothing":
                    target_purged_slots.update(["clothing", "clothing_state", "clothing_extension", "underwear"])
                elif ns == "scene_theme":
                    target_purged_slots.update(["scene_theme", "scene", "theme"])
                elif ns == "lighting":
                    target_purged_slots.update(["lighting", "lighting_palette"])
                elif ns == "pose":
                    target_purged_slots.update(["pose", "poses"])
                elif ns == "props":
                    target_purged_slots.update(["props", "prop"])
                elif ns == "shot_type":
                    target_purged_slots.update(["shot_type", "shot"])
                elif ns == "camera_angle":
                    target_purged_slots.update(["camera_angle", "camera", "angle"])
                elif ns == "tattoo":
                    target_purged_slots.update(["tattoo", "tattoos"])
                elif ns == "liquids":
                    target_purged_slots.update(["liquids", "liquid"])
                elif ns in ("style_recipe", "recipe"):
                    target_purged_slots.update(["style_recipe", "recipe"])
                overridden_slots.update(target_purged_slots)

                purge_recipe = bool({"style_recipe", "recipe"}.intersection(target_purged_slots))
                replaced_frags = [
                    f for f in fragments
                    if f.source_slot in target_purged_slots
                    or (
                        purge_recipe and (
                            (f.origin and f.origin.mode == "recipe")
                            or (f.provenance and f.provenance.kind == "style_recipe")
                            or (f.origin and f.origin.selector == "style_recipe")
                        )
                    )
                ]
                replaced_ids = tuple(str(f.id) for f in replaced_frags if getattr(f, "id", None))
                replaced_texts = tuple(str(f.text) for f in replaced_frags if getattr(f, "text", None))

                frags_for_slot = make_override_fragments(
                    ns,
                    raw_v,
                    entry_point=entry_point,
                    catalog_facts=getattr(self.resolver, "canonical_catalog_facts", None),
                    data_dir=Path(self.data_dir),
                )
                new_override_frags.extend(frags_for_slot)
                injected_ids = tuple(str(f.id) for f in frags_for_slot if getattr(f, "id", None))

                applied_records[ns] = OverrideRecord(
                    canonical_slot=ns,
                    status="applied",
                    source_port=raw_s,
                    raw_input=raw_v,
                    blank_fallback_reason=None,
                    replaced_target_ids=replaced_ids,
                    replaced_texts=replaced_texts,
                    injected_atom_ids=injected_ids,
                )

            if overridden_slots:
                purge_recipe = bool({"style_recipe", "recipe"}.intersection(overridden_slots))
                fragments = [
                    f for f in fragments
                    if f.source_slot not in overridden_slots
                    and not (
                        purge_recipe and (
                            (f.origin and f.origin.mode == "recipe")
                            or (f.provenance and f.provenance.kind == "style_recipe")
                            or (f.origin and f.origin.selector == "style_recipe")
                        )
                    )
                ]
                fragments.extend(new_override_frags)

            canonical_records = []
            for s in CANONICAL_20_SLOTS:
                if s in applied_records:
                    canonical_records.append(applied_records[s])
                else:
                    canonical_records.append(
                        OverrideRecord(
                            canonical_slot=s,
                            status="not_provided",
                            source_port=None,
                            raw_input=None,
                            blank_fallback_reason="omitted",
                            replaced_target_ids=(),
                            replaced_texts=(),
                            injected_atom_ids=(),
                        )
                    )
            override_records = tuple(canonical_records)
        else:
            override_records = build_default_canonical_override_records()

        res = assemble_result(
            fragments,
            self.data_dir,
            rng,
            max_words,
            resolver=self.resolver,
            overrides=override_records,
            nudity_constraint=nudity_constraint,
        )
        if preset_adjusted and not res.preset_adjusted:
            res = replace(res, preset_adjusted=True)
        if nudity_constraint is not None and res.nudity_constraint is None:
            res = replace(res, nudity_constraint=nudity_constraint)
        return res

    def assemble_preset_result(
        self,
        preset: Dict[str, Any],
        style_recipe: Optional[Dict[str, Any]],
        quality_tier: str,
        rng: Optional[Random] = None,
        entry_point: str = "preset_browser",
        preset_raw_value: Optional[str] = None,
        recipe_raw_value: Optional[str] = None,
    ) -> Tuple[str, Tuple[PromptAtom, ...], Tuple[str, ...]]:
        """兼容包装器：投影 AssemblyResult 为 (prompt_str, accepted_atoms, rules_applied)。"""
        res = self.assemble_preset(
            preset,
            style_recipe,
            quality_tier,
            rng,
            entry_point=entry_point,
            preset_raw_value=preset_raw_value,
            recipe_raw_value=recipe_raw_value,
        )
        return res.prompt, res.accepted_atoms, res.rules_applied


finalize_prompt = assemble_prompt
