"""
sampler.py — 完整 15 槽位数据采样与情境自洽引擎

严格实现 nsfw-prompt-templates-asian 项目规范：
1. 场景与主题情境识别（校园、职场、居家、温泉、夜店、SM、和风、医疗）
2. 空间与物理环境自洽（单一场景锚点定位，杜绝跨场所/室内外冲突并存）
3. 裸露等级 × 服装状态强力联动（L1 包裹 → L6 特写脱法咬合）
4. 槽位情境亲和度加权采样（自动杜绝场景与服装/道具/角色错位冲突）
5. 保证用户显式选择 100% 优先（若用户指定，则允许 Cosplay/反差角色扮演）
6. 显式 DataLoadError 错误诊断，杜绝静默失败
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional, Sequence, Tuple


try:
    from .models import SampleResult
except (ImportError, ValueError):
    from lib.models import SampleResult


class DataLoadError(Exception):
    """当必需的数据文件缺失或 JSON 解析失败时抛出。"""
    pass


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


# 8 大情境亲和度映射表（基于项目 24 场景与 15 专项规范）
CONTEXT_AFFINITY = {
    "school": {
        "clothing": ["jk_seifuku", "blazer_uniform", "gym_uniform", "korean_school"],
        "characters": ["jk_schoolgirl", "strict_teacher"],
        "makeup": ["natural_pure", "cute_peachy"],
        "hairstyles": ["twin_tails", "high_ponytail", "long_straight_black", "braided_twins", "bob_cut"],
        "props": ["glasses_reading", "smartphone_recording", "sex_toy_vibrator"],
        "headwear_jewelry": ["ribbon_bow", "gold_collarbone_chain"],
    },
    "office": {
        "clothing": ["ol_suit", "evening_dress", "street_casual"],
        "characters": ["ol_subordinate", "female_boss", "strict_teacher"],
        "makeup": ["mature_wife", "sultry_smoky", "natural_pure"],
        "hairstyles": ["low_ponytail", "collarbone_lob", "big_wavy_curls", "messy_bun"],
        "props": ["glasses_reading", "wine_glass_bottle", "smartphone_recording"],
        "headwear_jewelry": ["pearl_necklace", "gold_collarbone_chain", "leather_choker"],
    },
    "domestic": {
        "clothing": ["silk_robe", "camisole_slip", "lingerie_lace", "knit_sweater", "street_casual"],
        "characters": ["married_housewife", "neighbor_girlfriend"],
        "makeup": ["mature_wife", "natural_pure", "climax_flush"],
        "hairstyles": ["messy_bedhead", "messy_bun", "long_straight_black", "hair_over_breast"],
        "props": ["cute_cat_on_bed", "pillow_clutching", "wine_glass_bottle", "rose_petals_candles"],
        "headwear_jewelry": ["gold_collarbone_chain", "ribbon_bow"],
    },
    "onsen_bath": {
        "clothing": ["yukata", "kimono", "one_piece_swimsuit", "bikini_micro"],
        "characters": ["married_housewife", "neighbor_girlfriend", "gravure_idol"],
        "makeup": ["wet_dewy", "natural_pure", "climax_flush"],
        "hairstyles": ["wet_hair_face", "messy_bun", "low_ponytail"],
        "liquids": ["wet_water_drops", "sweat_glistening"],
        "props": ["wine_glass_bottle", "ice_cubes"],
        "headwear_jewelry": ["gold_collarbone_chain", "pearl_necklace"],
    },
    "nightlife": {
        "clothing": ["party_club", "bunny_suit", "lingerie_lace", "latex_catsuit"],
        "characters": ["hostess_cabaret", "gravure_idol"],
        "makeup": ["sultry_smoky", "climax_flush", "ruined_crying"],
        "hairstyles": ["big_wavy_curls", "twin_tails", "hime_cut", "hair_in_mouth"],
        "props": ["wine_glass_bottle", "sex_toy_vibrator", "smartphone_recording"],
        "headwear_jewelry": ["leather_choker", "body_chain", "bunny_ears"],
    },
    "bondage_sm": {
        "clothing": ["latex_catsuit", "leather_corset", "lingerie_lace", "panties_only"],
        "characters": ["french_maid", "ol_subordinate", "female_boss"],
        "makeup": ["submissive_marked", "ruined_crying", "climax_flush"],
        "hairstyles": ["messy_bedhead", "twin_tails", "hair_in_mouth"],
        "tattoos": ["lewd_womb_pubic", "barcode_serial_number", "tally_marks_inner_thigh"],
        "props": ["bondage_rope_collar", "sex_toy_vibrator", "ice_cubes"],
        "headwear_jewelry": ["leather_choker", "lace_blindfold", "nipple_rings", "body_chain"],
    },
    "traditional": {
        "clothing": ["kimono", "yukata", "qipao", "hanfu", "furisode", "modern_chinese"],
        "characters": ["married_housewife", "neighbor_girlfriend"],
        "makeup": ["vintage_retro", "natural_pure", "mature_wife"],
        "hairstyles": ["long_straight_black", "hime_cut", "low_ponytail"],
        "tattoos": ["japanese_irezumi_dragon", "cherry_blossom_shoulder"],
        "props": ["wine_glass_bottle", "rose_petals_candles"],
        "headwear_jewelry": ["pearl_necklace", "gold_collarbone_chain"],
    },
    "medical": {
        "clothing": ["nurse_uniform"],
        "characters": ["kind_nurse"],
        "makeup": ["natural_pure", "cute_peachy"],
        "hairstyles": ["low_ponytail", "bob_cut", "twin_tails"],
        "props": ["glasses_reading", "smartphone_recording"],
        "headwear_jewelry": ["nurse_cap"],
    },
}

# 14 大情境映射至 8 大亲和度矩阵
CONTEXT_PARENT_MAPPING = {
    "school": "school",
    "office": "office",
    "medical": "medical",
    "onsen_bath": "onsen_bath",
    "bondage_sm": "bondage_sm",
    "traditional": "traditional",
    "nightlife": "nightlife",
    "domestic": "domestic",
    "transit": "office",      # 通勤/电车 -> 职场/校园通勤装
    "outdoor": "domestic",    # 户外/海滩 -> 休闲/居家
    "dining": "nightlife",     # 居酒屋/餐厅 -> 聚会/夜生活
    "adult": "nightlife",      # 摄影棚/成人 -> 夜生活/风俗
    "special": "bondage_sm",   # 特殊密室 -> SM
    "generic": "domestic",     # 通用 -> 居家日常
}


class DataSampler:
    """从 data/ 目录加载所有分类数据，提供各槽位精准/情境加权采样与列举接口。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self._cache: Dict[str, Any] = {}

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
    def _pick_one(items: Sequence[Any], rng: Random) -> Optional[Any]:
        if not items:
            return None
        return rng.choice(items)

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
                return result
            if isinstance(tags, str):
                return [t.strip() for t in tags.split(",") if t.strip()]
        return []

    # ─── 情境推断核心 ───

    def detect_context(self, scene_name: str, theme_name: str) -> str:
        """根据当前场景与主题关键词，推断最匹配的核心情境（使用精准词边界与关键词）。"""
        text = f"{scene_name} {theme_name}".lower()

        # 1. 校园 / 学生
        if any(k in text for k in ["校", "教", "课", "学", "书", "体育", "操场", "走廊", "初恋", "制服", "补习", "学生", "少女"]) or re.search(r"\b(school|classroom|student|teacher|uniform|jk|seifuku|blackboard|gymnasium)\b", text):
            return "school"
        # 2. 职场 / 办公室
        if any(k in text for k in ["办公", "职场", "会议", "经理", "秘书", "加班", "公司", "商务", "西装", "包臀裙", "白领", "总裁"]) or re.search(r"\b(office|business|corporate|meeting|secretary|executive|cubicle|ol)\b", text):
            return "office"
        # 3. 医疗 / 诊所
        if any(k in text for k in ["医", "诊", "护士", "病房", "手术", "药", "体检", "针筒", "病床", "白大褂"]) or re.search(r"\b(hospital|clinic|nurse|medical|doctor|examination|ward|patient)\b", text):
            return "medical"
        # 4. 温泉 / 浴室
        if any(k in text for k in ["温泉", "浴", "风吕", "泳", "桑拿", "水", "海滩", "沙滩", "泳衣", "比基尼", "湿身", "浴缸"]) or re.search(r"\b(onsen|bath|shower|rotenburo|sento|sauna|soapland|jacuzzi|bathtub)\b", text):
            return "onsen_bath"
        # 5. SM / 调教 / 密室
        if any(k in text for k in ["调教", "束缚", "绳", "地牢", "密室", "监禁", "强制", "手铐", "项圈", "皮衣", "乳胶", "惩罚"]) or re.search(r"\b(sm|bondage|shibari|kinbaku|dungeon|collar|handcuffs|latex|restrained|dominant|submissive)\b", text):
            return "bondage_sm"
        # 6. 和风 / 传统
        if any(k in text for k in ["和室", "茶室", "庭院", "古风", "和服", "旗袍", "汉服", "祭典", "榻榻米", "国风", "神社", "振袖"]) or re.search(r"\b(kimono|yukata|qipao|hanfu|tatami|shrine|temple|washitsu|ryokan)\b", text):
            return "traditional"
        # 7. 夜店 / 风俗 / 酒店
        if any(k in text for k in ["情人旅馆", "酒店", "夜店", "风俗", "酒吧", "歌舞伎町", "包厢", "派对", "醉", "兔女郎", "夜总会", "陪酒", "夜市"]) or re.search(r"\b(nightclub|club|bar|cabaret|hostess|karaoke|pub|drunk|party)\b", text):
            return "nightlife"
        # 8. 居家 / 人妻
        if any(k in text for k in ["家", "卧", "客", "厨", "公寓", "人妻", "少妇", "同居", "阳台", "睡衣", "围裙", "居家", "被窝", "床上"]) or re.search(r"\b(bedroom|living room|kitchen|apartment|home|housewife|bed|futon)\b", text):
            return "domestic"

        return "generic"

    # ─── 槽位 1: 场景 + 主题 ───

    def sample_scene_result(self, category: str, rng: Random) -> Optional[SampleResult]:
        data = self._load("scenes")
        scenes = data.get("scenes", [])
        if not scenes:
            return None

        all_items: List[Dict] = []
        target_items: List[Dict] = []

        for scene_group in scenes:
            items = scene_group.get("items", [])
            for item in items:
                sub = item.get("label") or item.get("subcategory", "")
                if sub and sub not in ("章节", "内容"):
                    all_items.append(item)
                    if not _is_random(category) and (category in sub or sub in category or category == item.get("id")):
                        target_items.append(item)

        pool = target_items if target_items else all_items
        if not pool:
            return None

        chosen = self._pick_one(pool, rng)
        if not chosen:
            return None

        anchors = chosen.get("anchor_tags", [])
        details = chosen.get("detail_tags", [])

        if not anchors:
            anchors = chosen.get("tags", ["room"])

        anchor = rng.choice(anchors) if anchors else "room"
        sampled_tags = [anchor]

        if details:
            detail_count = min(rng.randint(1, 2), len(details))
            sampled_tags.extend(rng.sample(details, detail_count))

        return SampleResult(
            tags=tuple(sampled_tags),
            item_id=chosen.get("id", "scene_unknown"),
            context_ids=tuple(chosen.get("context_ids", ("generic",))),
            exclusive_group=chosen.get("exclusive_group")
        )

    def sample_scene(self, category: str, rng: Random) -> List[str]:
        res = self.sample_scene_result(category, rng)
        return list(res.tags) if res else []

    def sample_theme(self, theme: str, rng: Random) -> List[str]:
        if _is_none(theme):
            return []
        data = self._load("themes")
        themes = data.get("themes", [])
        if not themes:
            return []

        if _is_random(theme):
            t = self._pick_one(themes, rng)
        else:
            t = next((x for x in themes
                      if x.get("name_zh", "") == theme
                      or x.get("theme_zh", "") == theme
                      or theme in x.get("name_zh", "")
                      or theme in x.get("theme_zh", "")), None)
        if not t:
            return []

        tags = self._flatten_tags(t)
        return self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []

    # ─── 槽位 2: 景别 + 视角 + 画质/设备 ───

    def sample_shot_type(self, shot_type: str, rng: Random) -> List[str]:
        if _is_none(shot_type):
            return []
        data = self._load("shot_types")
        shots = data.get("shot_types", [])
        if not shots:
            return []

        if _is_random(shot_type) or _is_auto(shot_type):
            chosen = self._pick_one(shots, rng)
        else:
            chosen = next((s for s in shots
                           if s.get("name_zh", "") == shot_type
                           or s.get("id", "") == shot_type
                           or shot_type in s.get("name_zh", "")
                           or s.get("name_zh", "") in shot_type), None)
            if not chosen:
                chosen = self._pick_one(shots, rng)

        tags = self._flatten_tags(chosen)
        return tags[:2] if tags else []

    def sample_camera_angle(self, angle: str, rng: Random) -> List[str]:
        if _is_none(angle):
            return []
        data = self._load("shot_types")
        angles = data.get("camera_angles", [])
        if not angles:
            return []

        if _is_random(angle) or _is_auto(angle):
            chosen = self._pick_one(angles, rng)
        else:
            chosen = next((a for a in angles
                           if a.get("name_zh", "") == angle
                           or a.get("id", "") == angle
                           or angle in a.get("name_zh", "")
                           or a.get("name_zh", "") in angle), None)
            if not chosen:
                chosen = self._pick_one(angles, rng)

        tags = self._flatten_tags(chosen)
        return tags[:1] if tags else []

    def sample_quality_tags(self, quality_tier: str) -> List[str]:
        q_str = str(quality_tier or "").lower()
        if "masterpiece" in q_str or "顶尖" in q_str:
            return ["masterpiece", "best quality", "ultra detailed", "8k", "photorealistic"]
        elif "cctv" in q_str or "监控" in q_str:
            return ["CCTV footage", "security camera", "low resolution", "grainy"]
        elif "phone" in q_str or "手机" in q_str:
            return ["phone camera", "selfie", "amateur photo", "slightly blurry"]
        elif "standard" in q_str or "标准" in q_str:
            return ["good quality", "detailed"]
        else:
            return ["best quality", "detailed", "photorealistic"]

    # ─── 槽位 3: 裸露状态 ───

    def sample_nudity(self, level: str | int, rng: Random) -> Tuple[List[str], str]:
        """返回 (采样tags, 标准等级代码比如L1/L2/L3/L4/L5/L6)"""
        data = self._load("nudity_levels")
        levels = data.get("nudity_levels", [])
        if not levels:
            return ([], "L3")

        if _is_random(level):
            lvl = self._pick_one(levels, rng)
        else:
            level_str = str(level).strip().upper()
            target_l = None
            for l_tag in ["L1", "L2", "L3", "L4", "L5", "L6"]:
                if l_tag in level_str:
                    target_l = l_tag
                    break

            if target_l:
                lvl = next((l for l in levels if target_l in str(l.get("level", ""))), None)
            else:
                lvl = next((l for l in levels
                            if level_str in str(l.get("level", "")).upper()
                            or level_str in str(l.get("name_zh", "")).upper()), None)
        if not lvl:
            lvl = self._pick_one(levels, rng)

        lvl_code = "L3"
        for code in ["L1", "L2", "L3", "L4", "L5", "L6"]:
            if code in str(lvl.get("level", "")) or code in str(lvl.get("name_zh", "")):
                lvl_code = code
                break

        tags = self._flatten_tags(lvl)
        sampled_tags = self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []
        return (sampled_tags, lvl_code)

    # ─── 槽位 4: 服装款式与穿脱状态 ───

    def sample_clothing_with_nudity_linkage(
        self,
        style: str,
        state: str,
        nudity_level_code: str,
        rng: Random,
        context: Optional[str] = None
    ) -> List[str]:
        if _is_none(style):
            return []
        data = self._load("clothing")
        styles = data.get("categories", [])
        if not styles:
            return []

        # 1. 确定服装款式对象
        chosen_style = None
        if _is_random(style):
            aff_ids = self._get_affinity_ids(context, "clothing")
            if aff_ids and rng.random() < 0.85:
                pool = [c for c in styles if c.get("id") in aff_ids]
                chosen_style = self._pick_one(pool if pool else styles, rng)
            else:
                chosen_style = self._pick_one(styles, rng)
        else:
            chosen_style = next((c for c in styles
                                 if c.get("name_zh", "") == style
                                 or c.get("id", "") == style
                                 or style in c.get("name_zh", "")
                                 or c.get("name_zh", "") in style), None)
            if not chosen_style:
                chosen_style = self._pick_one(styles, rng)

        c_id = chosen_style.get("id", "")
        base_style_tags = self._flatten_tags(chosen_style)

        # 2. 检查联动配置
        linkages = data.get("clothing_nudity_linkage", {})
        linkage_data = linkages.get(nudity_level_code, {})

        if _is_auto(state) or _is_none(state) or state == "自动联动裸露等级 (Auto Link Nudity)":
            style_overrides = linkage_data.get("style_overrides", {})
            if c_id in style_overrides:
                return style_overrides[c_id]
            gen_tags = linkage_data.get("general_tags", [])
            chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
            chosen_style_tags = self._pick(base_style_tags, rng, min(2, len(base_style_tags)))
            return chosen_style_tags + chosen_gen

        states = data.get("clothing_states", [])
        if _is_random(state):
            chosen_state = self._pick_one(states, rng)
        else:
            chosen_state = next((s for s in states
                                 if s.get("name_zh", "") == state
                                 or s.get("id", "") == state
                                 or state in s.get("name_zh", "")
                                 or s.get("name_zh", "") in state), None)

        state_tags = self._flatten_tags(chosen_state) if chosen_state else []
        chosen_style_tags = self._pick(base_style_tags, rng, min(2, len(base_style_tags)))
        return chosen_style_tags + self._pick(state_tags, rng, min(2, len(state_tags)))

    # ─── 槽位 5: 光影氛围 ───

    def sample_lighting(self, preset: str, rng: Random) -> List[str]:
        data = self._load("lighting")

        if not _is_auto(preset) and not _is_random(preset):
            combos = data.get("preset_combos", [])
            p = next((c for c in combos
                      if c.get("name", "") == preset
                      or preset in c.get("name", "")
                      or c.get("name", "") in preset), None)
            if p:
                parts = []
                for k in ["main_light", "modifier_light", "atmosphere"]:
                    v = p.get(k, "")
                    if v:
                        parts.extend([t.strip() for t in v.split(",") if t.strip()])
                return parts

        result: List[str] = []
        techniques = data.get("professional_lighting", [])
        if techniques:
            tech = self._pick_one(techniques, rng)
            tags = tech.get("tags", []) + tech.get("erotic_tags", [])
            if tags:
                result.extend(self._pick(tags, rng, min(2, len(tags))))

        temps = data.get("color_temperature_table", [])
        if temps:
            temp = self._pick_one(temps, rng)
            t_tags = self._flatten_tags(temp)
            if t_tags:
                result.append(t_tags[0])
        return result

    # ─── 槽位 6: 姿势动作 ───

    def sample_pose(self, category: str, rng: Random) -> List[str]:
        data = self._load("poses")
        categories = data.get("pose_categories", [])
        if not categories:
            return []

        if _is_random(category):
            cat = self._pick_one(categories, rng)
        else:
            cat = next((c for c in categories
                        if c.get("name_zh", "") == category
                        or c.get("id", "") == category
                        or category in c.get("name_zh", "")
                        or c.get("name_zh", "") in category), None)
        if not cat:
            cat = self._pick_one(categories, rng)

        all_tags: List[str] = []
        for sub in cat.get("subcategories", []):
            all_tags.extend(sub.get("tags", []))
        if not all_tags:
            all_tags = self._flatten_tags(cat)

        return self._pick(all_tags, rng, min(rng.randint(1, 2), max(1, len(all_tags)))) if all_tags else []

    # ─── 槽位 7: 表情眼神 ───

    def sample_expression(self, mood: str, rng: Random) -> List[str]:
        data = self._load("expressions")
        categories = data.get("emotions", [])
        if not categories:
            return []

        if _is_random(mood):
            cat = self._pick_one(categories, rng)
        else:
            cat = next((c for c in categories
                        if c.get("name", "") == mood
                        or mood in c.get("name", "")
                        or c.get("name", "") in mood), None)
        if not cat:
            cat = self._pick_one(categories, rng)

        tags = self._flatten_tags(cat)
        return self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []

    # ─── 槽位 8: 风格/胶片 ───

    def sample_film(self, stock: str, rng: Random) -> List[str]:
        if _is_none(stock):
            return []
        data = self._load("film_stocks")

        all_items: List[Dict] = []
        for group in ["film_stocks", "cinema_lenses", "weather_moods", "photography_styles"]:
            items = data.get(group, [])
            if isinstance(items, list):
                all_items.extend(items)

        if not all_items:
            return []

        if _is_random(stock):
            film_list = data.get("film_stocks", all_items)
            chosen_film = self._pick_one(film_list, rng)
            tags = self._flatten_tags(chosen_film)[:3]
            return tags
        else:
            match = next((x for x in all_items
                          if x.get("name_zh", "") == stock
                          or x.get("id", "") == stock
                          or stock in x.get("name_zh", "")
                          or x.get("name_zh", "") in stock), None)
            if match:
                return self._flatten_tags(match)[:3]

        return []

    # ─── 槽位 9: 妆容细节 ───

    def sample_makeup(self, makeup_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(makeup_style):
            return []
        data = self._load("makeup")
        styles = data.get("categories", [])
        if not styles:
            return []

        if _is_random(makeup_style):
            aff_ids = self._get_affinity_ids(context, "makeup")
            if aff_ids and rng.random() < 0.85:
                pool = [m for m in styles if m.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else styles, rng)
            else:
                chosen = self._pick_one(styles, rng)
        else:
            chosen = next((m for m in styles
                           if m.get("name_zh", "") == makeup_style
                           or m.get("id", "") == makeup_style
                           or makeup_style in m.get("name_zh", "")
                           or m.get("name_zh", "") in makeup_style), None)
            if not chosen:
                chosen = self._pick_one(styles, rng)

        tags = self._flatten_tags(chosen)
        return self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []

    # ─── 槽位 10: 发型与饰品 ───

    def sample_hairstyle(self, hairstyle: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(hairstyle):
            return []
        data = self._load("accessories")
        styles = data.get("hairstyles", [])
        if not styles:
            return []

        if _is_random(hairstyle):
            aff_ids = self._get_affinity_ids(context, "hairstyles")
            if aff_ids and rng.random() < 0.85:
                pool = [h for h in styles if h.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else styles, rng)
            else:
                chosen = self._pick_one(styles, rng)
        else:
            chosen = next((h for h in styles
                           if h.get("name_zh", "") == hairstyle
                           or h.get("id", "") == hairstyle
                           or hairstyle in h.get("name_zh", "")
                           or h.get("name_zh", "") in hairstyle), None)
            if not chosen:
                chosen = self._pick_one(styles, rng)

        tags = self._flatten_tags(chosen)
        return tags[:2] if tags else []

    def sample_jewelry(self, jewelry_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(jewelry_style):
            return []
        data = self._load("accessories")
        items = data.get("headwear_jewelry", [])
        if not items:
            return []

        if _is_random(jewelry_style):
            aff_ids = self._get_affinity_ids(context, "headwear_jewelry")
            if aff_ids and rng.random() < 0.85:
                pool = [j for j in items if j.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else items, rng)
            else:
                chosen = self._pick_one(items, rng)
        else:
            chosen = next((j for j in items
                           if j.get("name_zh", "") == jewelry_style
                           or j.get("id", "") == jewelry_style
                           or jewelry_style in j.get("name_zh", "")
                           or j.get("name_zh", "") in jewelry_style), None)
            if not chosen:
                chosen = self._pick_one(items, rng)

        tags = self._flatten_tags(chosen)
        return tags[:2] if tags else []

    # ─── 槽位 11: 真实瑕疵细节 ───

    def sample_imperfections(self, imp_type: str, rng: Random) -> List[str]:
        if _is_none(imp_type):
            return []
        data = self._load("imperfections")
        categories = data.get("categories", [])
        if not categories:
            return []

        if _is_random(imp_type):
            chosen = self._pick_one(categories, rng)
        else:
            chosen = next((i for i in categories
                           if i.get("name_zh", "") == imp_type
                           or i.get("id", "") == imp_type
                           or imp_type in i.get("name_zh", "")
                           or i.get("name_zh", "") in imp_type), None)
            if not chosen:
                chosen = self._pick_one(categories, rng)

        tags = self._flatten_tags(chosen)
        return self._pick(tags, rng, min(2, len(tags))) if tags else []

    # ─── 槽位 12: 纹身标记与皮肤融合 ───

    def sample_tattoo(self, tattoo_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(tattoo_style):
            return []
        data = self._load("tattoos")
        tattoos = data.get("categories", [])
        if not tattoos:
            return []

        if _is_random(tattoo_style):
            aff_ids = self._get_affinity_ids(context, "tattoos")
            if aff_ids and rng.random() < 0.85:
                pool = [t for t in tattoos if t.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else tattoos, rng)
            else:
                pool = [t for t in tattoos if t.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = next((t for t in tattoos
                           if t.get("name_zh", "") == tattoo_style
                           or t.get("id", "") == tattoo_style
                           or tattoo_style in t.get("name_zh", "")
                           or t.get("name_zh", "") in tattoo_style), None)

        if not chosen or chosen.get("id") == "none":
            return []

        tags = self._flatten_tags(chosen)
        return tags

    # ─── 槽位 13: 道具宠物 ───

    def sample_prop(self, prop_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(prop_style):
            return []
        data = self._load("props")
        props = data.get("categories", [])
        if not props:
            return []

        if _is_random(prop_style):
            aff_ids = self._get_affinity_ids(context, "props")
            if aff_ids and rng.random() < 0.85:
                pool = [p for p in props if p.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else props, rng)
            else:
                pool = [p for p in props if p.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = next((p for p in props
                           if p.get("name_zh", "") == prop_style
                           or p.get("id", "") == prop_style
                           or prop_style in p.get("name_zh", "")
                           or p.get("name_zh", "") in prop_style), None)

        if not chosen or chosen.get("id") == "none":
            return []

        tags = self._flatten_tags(chosen)
        return self._pick(tags, rng, min(2, len(tags))) if tags else []

    # ─── 槽位 14: 人格角色卡 ───

    def sample_character(self, character_role: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(character_role):
            return []
        data = self._load("characters")
        chars = data.get("characters", [])
        if not chars:
            return []

        if _is_random(character_role):
            aff_ids = self._get_affinity_ids(context, "characters")
            if aff_ids and rng.random() < 0.85:
                pool = [c for c in chars if c.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else chars, rng)
            else:
                pool = [c for c in chars if c.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = next((c for c in chars
                           if c.get("name_zh", "") == character_role
                           or c.get("id", "") == character_role
                           or character_role in c.get("name_zh", "")
                           or c.get("name_zh", "") in character_role), None)

        if not chosen or chosen.get("id") == "none":
            return []

        tags = self._flatten_tags(chosen)
        return tags[:2] if tags else []

    # ─── 槽位 15: 液体体液系统 ───

    def sample_liquid(self, liquid_effect: str, rng: Random, context: Optional[str] = None) -> List[str]:
        if _is_none(liquid_effect):
            return []
        data = self._load("nudity_levels")
        liquids = data.get("liquid_effects", [])
        if not liquids:
            return []

        if _is_random(liquid_effect):
            aff_ids = self._get_affinity_ids(context, "liquids")
            if aff_ids and rng.random() < 0.85:
                pool = [l for l in liquids if l.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else liquids, rng)
            else:
                pool = [l for l in liquids if l.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = next((l for l in liquids
                           if l.get("name_zh", "") == liquid_effect
                           or l.get("id", "") == liquid_effect
                           or liquid_effect in l.get("name_zh", "")
                           or l.get("name_zh", "") in liquid_effect), None)

        if not chosen or chosen.get("id") == "none":
            return []

        tags = self._flatten_tags(chosen)
        return self._pick(tags, rng, min(2, len(tags))) if tags else []

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
        return next((r for r in recipes
                     if r.get("style_name") == recipe_name
                     or r.get("name_zh") == recipe_name
                     or r.get("id") == recipe_name
                     or recipe_name in str(r.get("style_name", ""))
                     or str(r.get("style_name", "")) in recipe_name), None)

    def get_preset(self, preset_id: str, rng: Random) -> Optional[Dict[str, Any]]:
        if _is_none(preset_id):
            return None
        data = self._load("presets")
        presets = data.get("presets", [])
        if not presets:
            return None
        if _is_random(preset_id):
            return self._pick_one(presets, rng)
        pid = preset_id.split()[0] if " " in preset_id else preset_id
        return next((p for p in presets
                     if p.get("id") == pid
                     or p.get("name_zh") == preset_id
                     or preset_id.startswith(p.get("id", "\x00"))
                     or p.get("name_zh", "") in preset_id), None)

    def get_negative_prompt(self) -> str:
        data = self._load("negative_prompts")
        return data.get("default",
                        "low quality, worst quality, blurry, jpeg artifacts, "
                        "watermark, deformed, bad anatomy, extra limbs")

    # ─── 列举方法（用于 UI 下拉菜单） ───

    def list_scene_categories(self) -> List[str]:
        data = self._load("scenes")
        names = set()
        for scene_group in data.get("scenes", []):
            for item in scene_group.get("items", []):
                sub = item.get("subcategory", "")
                if sub and sub not in ("章节", "内容"):
                    names.add(sub)
        return sorted(names)

    def list_themes(self) -> List[str]:
        data = self._load("themes")
        return [t.get("name_zh") or t.get("theme_zh", "") for t in data.get("themes", []) if (t.get("name_zh") or t.get("theme_zh"))]

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
        result = []
        for stock in data.get("film_stocks", []):
            if stock.get("name_zh"):
                result.append(stock["name_zh"])
        for lens in data.get("cinema_lenses", []):
            if lens.get("name_zh"):
                result.append(lens["name_zh"])
        for mood in data.get("weather_moods", []):
            if mood.get("name_zh"):
                result.append(mood["name_zh"])
        return result

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
        return [l.get("name_zh", "") for l in data.get("liquid_effects", []) if l.get("name_zh") and l.get("id") != "none"]

    def list_imperfection_types(self) -> List[str]:
        data = self._load("imperfections")
        return [i.get("name_zh", "") for i in data.get("categories", []) if i.get("name_zh") and i.get("id") != "none"]

    def list_preset_names(self) -> List[str]:
        data = self._load("presets")
        return [f"{p.get('id', '')} {p.get('name_zh', '')}"
                for p in data.get("presets", []) if p.get("id")]

    def list_style_recipes(self) -> List[str]:
        data = self._load("style_recipes")
        return [r.get("style_name", r.get("name_zh", r.get("id", "")))
                for r in data.get("recipes", []) if (r.get("style_name") or r.get("name_zh"))]
