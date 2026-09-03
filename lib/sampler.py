"""
sampler.py — 完整 15 槽位数据采样与情境自洽引擎

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
from typing import Any, Dict, List, Optional, Sequence, Tuple

if __package__:
    from .errors import CatalogIndexingError, DataLoadError, DataSelectionError
    from .models import ClothingSampleResult, SampleResult, SampledTag, TagProvenance, ThemeSampleResult
else:
    from lib.errors import CatalogIndexingError, DataLoadError, DataSelectionError
    from lib.models import ClothingSampleResult, SampleResult, SampledTag, TagProvenance, ThemeSampleResult


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


# 全量 14 大情境亲和度映射表（基于项目 24 场景分类与 15 槽位交叉规范）
CONTEXT_AFFINITY = {
    "school": {
        "clothing": ["jk_seifuku", "blazer_uniform", "gym_uniform", "korean_school"],
        "characters": ["jk_schoolgirl", "strict_teacher"],
        "makeup": ["natural_pure", "cute_peachy", "sweet_peach_milk", "clear_water_bare"],
        "hairstyles": ["twin_tails", "high_ponytail", "long_straight_black", "braided_twins", "bob_cut"],
        "headwear_jewelry": ["ribbon_bow", "gold_collarbone_chain"],
        "props": ["glasses_reading", "smartphone_recording", "sex_toy_vibrator", "plush_doll_teddy"],
        "liquids": ["sweat_glistening", "none"],
    },
    "office": {
        "clothing": ["ol_suit", "knit_sweater", "evening_dress", "street_casual"],
        "characters": ["ol_subordinate", "female_boss", "strict_teacher"],
        "makeup": ["mature_wife", "sultry_smoky", "natural_pure", "asian_hybrid_contour"],
        "hairstyles": ["low_ponytail", "collarbone_lob", "big_wavy_curls", "messy_bun"],
        "headwear_jewelry": ["pearl_necklace", "gold_collarbone_chain", "leather_choker"],
        "props": ["glasses_reading", "wine_glass_bottle", "smartphone_recording", "digital_camera_record"],
        "liquids": ["sweat_glistening", "none"],
    },
    "medical": {
        "clothing": ["nurse_uniform"],
        "characters": ["kind_nurse"],
        "makeup": ["natural_pure", "cute_peachy", "clear_water_bare"],
        "hairstyles": ["low_ponytail", "bob_cut", "twin_tails", "messy_bun"],
        "headwear_jewelry": ["nurse_cap"],
        "props": ["glasses_reading", "smartphone_recording"],
        "liquids": ["sweat_glistening", "body_oil_lube"],
    },
    "onsen_bath": {
        "clothing": ["yukata", "kimono", "one_piece_swimsuit", "bikini_micro"],
        "characters": ["married_housewife", "neighbor_girlfriend", "gravure_idol"],
        "makeup": ["wet_dewy", "natural_pure", "climax_flush", "drunken_milky_youth"],
        "hairstyles": ["wet_hair_face", "messy_bun", "low_ponytail"],
        "headwear_jewelry": ["gold_collarbone_chain", "pearl_necklace", "ankle_bracelet"],
        "props": ["wine_glass_bottle", "ice_cubes", "rose_petals_candles"],
        "liquids": ["wet_water_drops", "sweat_glistening"],
    },
    "bondage_sm": {
        "clothing": ["latex_catsuit", "leather_corset", "lingerie_lace"],
        "characters": ["french_maid", "ol_subordinate", "female_boss"],
        "makeup": ["submissive_marked", "ruined_crying", "climax_flush", "gothic_dark"],
        "hairstyles": ["messy_bedhead", "twin_tails", "hair_in_mouth"],
        "headwear_jewelry": ["leather_choker", "lace_blindfold", "nipple_rings", "body_chain"],
        "tattoos": ["lewd_womb_pubic", "barcode_serial_number", "tally_marks_inner_thigh"],
        "props": ["bondage_rope_collar", "sex_toy_vibrator", "ice_cubes"],
        "liquids": ["saliva_drool", "sweat_glistening", "cum_splatter", "body_oil_lube"],
    },
    "traditional": {
        "clothing": ["kimono", "yukata", "furisode", "qipao", "hanfu", "modern_chinese", "hanbok"],
        "characters": ["married_housewife", "neighbor_girlfriend"],
        "makeup": ["vintage_retro", "chinese_vermilion", "natural_pure", "mature_wife"],
        "hairstyles": ["long_straight_black", "hime_cut", "low_ponytail", "braided_twins"],
        "headwear_jewelry": ["pearl_necklace", "gold_collarbone_chain", "ribbon_bow"],
        "tattoos": ["japanese_irezumi_dragon", "cherry_blossom_shoulder"],
        "props": ["oriental_fan_umbrella", "wine_glass_bottle", "flower_bouquet_petals"],
        "liquids": ["sweat_glistening", "none"],
    },
    "nightlife": {
        "clothing": ["bunny_suit", "party_club", "lingerie_lace", "latex_catsuit", "evening_dress"],
        "characters": ["hostess_cabaret", "gravure_idol"],
        "makeup": ["sultry_smoky", "climax_flush", "ruined_crying", "gothic_dark"],
        "hairstyles": ["big_wavy_curls", "twin_tails", "hime_cut", "hair_in_mouth"],
        "headwear_jewelry": ["leather_choker", "body_chain", "bunny_ears", "cat_ears"],
        "props": ["wine_glass_bottle", "sex_toy_vibrator", "smartphone_recording", "digital_camera_record"],
        "liquids": ["sweat_glistening", "saliva_drool"],
    },
    "domestic": {
        "clothing": ["silk_robe", "camisole_slip", "knit_sweater", "street_casual", "lingerie_lace"],
        "characters": ["married_housewife", "neighbor_girlfriend"],
        "makeup": ["mature_wife", "natural_pure", "climax_flush", "pure_desire_white_peach"],
        "hairstyles": ["messy_bedhead", "messy_bun", "long_straight_black", "hair_over_breast"],
        "headwear_jewelry": ["gold_collarbone_chain", "ribbon_bow", "ankle_bracelet"],
        "props": ["cute_cat_on_bed", "pillow_clutching", "game_controller", "wine_glass_bottle", "rose_petals_candles"],
        "liquids": ["sweat_glistening", "saliva_drool"],
    },
    "transit": {
        "clothing": ["jk_seifuku", "blazer_uniform", "ol_suit", "street_casual", "knit_sweater"],
        "characters": ["jk_schoolgirl", "ol_subordinate", "neighbor_girlfriend"],
        "makeup": ["natural_pure", "cute_peachy", "climax_flush"],
        "hairstyles": ["low_ponytail", "high_ponytail", "long_straight_black", "collarbone_lob"],
        "headwear_jewelry": ["gold_collarbone_chain", "ribbon_bow"],
        "props": ["smartphone_recording", "glasses_reading"],
        "liquids": ["sweat_glistening", "none"],
    },
    "outdoor": {
        "clothing": ["bikini_micro", "one_piece_swimsuit", "street_casual", "gym_uniform", "cheerleader"],
        "characters": ["gravure_idol", "neighbor_girlfriend"],
        "makeup": ["natural_pure", "cute_peachy", "wet_dewy", "sun_kissed"],
        "hairstyles": ["high_ponytail", "twin_tails", "space_buns", "messy_bun"],
        "headwear_jewelry": ["ribbon_bow", "gold_collarbone_chain", "ankle_bracelet"],
        "props": ["camera_tripod_flash", "oriental_fan_umbrella", "smartphone_recording", "flower_bouquet_petals"],
        "liquids": ["wet_water_drops", "sweat_glistening"],
    },
    "dining": {
        "clothing": ["maid_dress", "waitress_uniform", "street_casual", "qipao", "kimono"],
        "characters": ["french_maid", "neighbor_girlfriend", "ol_subordinate", "married_housewife"],
        "makeup": ["cute_peachy", "natural_pure", "mature_wife", "sweet_peach_milk"],
        "hairstyles": ["messy_bun", "collarbone_lob", "twin_tails", "bob_cut"],
        "headwear_jewelry": ["maid_headdress", "pearl_necklace", "gold_collarbone_chain"],
        "props": ["wine_glass_bottle", "glasses_reading", "smartphone_recording"],
        "liquids": ["none", "sweat_glistening"],
    },
    "adult": {
        "clothing": ["bunny_suit", "maid_dress", "latex_catsuit", "leather_corset", "bikini_micro", "lingerie_lace"],
        "characters": ["hostess_cabaret", "gravure_idol", "french_maid"],
        "makeup": ["sultry_smoky", "climax_flush", "ruined_crying", "submissive_marked"],
        "hairstyles": ["twin_tails", "big_wavy_curls", "wet_hair_face", "hair_in_mouth"],
        "headwear_jewelry": ["bunny_ears", "cat_ears", "maid_headdress", "lace_blindfold", "leather_choker", "nipple_rings", "body_chain"],
        "tattoos": ["lewd_womb_pubic", "barcode_serial_number", "tally_marks_inner_thigh", "butterfly_lower_back"],
        "props": ["sex_toy_vibrator", "camera_tripod_flash", "bondage_rope_collar", "ice_cubes"],
        "liquids": ["pussy_juice", "cum_splatter", "body_oil_lube", "saliva_drool"],
    },
    "special": {
        "clothing": ["latex_catsuit", "leather_corset", "lingerie_lace"],
        "characters": ["female_boss", "kind_nurse", "strict_teacher"],
        "makeup": ["submissive_marked", "sultry_smoky", "ruined_crying", "gothic_dark"],
        "hairstyles": ["hime_cut", "long_straight_black", "messy_bedhead"],
        "headwear_jewelry": ["leather_choker", "lace_blindfold", "body_chain"],
        "tattoos": ["barcode_serial_number", "snake_coiling", "spine_vertical_script"],
        "props": ["bondage_rope_collar", "camera_tripod_flash", "sex_toy_vibrator"],
        "liquids": ["saliva_drool", "sweat_glistening", "cum_splatter"],
    },
    "generic": {
        "clothing": ["street_casual", "knit_sweater", "camisole_slip", "silk_robe"],
        "characters": ["neighbor_girlfriend", "married_housewife", "jk_schoolgirl"],
        "makeup": ["natural_pure", "cute_peachy", "mature_wife", "pure_desire_white_peach"],
        "hairstyles": ["long_straight_black", "high_ponytail", "low_ponytail", "collarbone_lob"],
        "headwear_jewelry": ["gold_collarbone_chain", "ribbon_bow", "pearl_necklace"],
        "props": ["smartphone_recording", "cute_cat_on_bed", "pillow_clutching"],
        "liquids": ["none", "sweat_glistening"],
    },
}

# 14 大情境直通映射表
CONTEXT_PARENT_MAPPING = {k: k for k in CONTEXT_AFFINITY.keys()}



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

    # ─── 槽位 1: 场景 + 主题 ───

    def sample_scene_result(self, category: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(category):
            return None
        self._ensure_scenes_indexed()
        if not self._all_scene_items:
            return None

        if _is_random(category):
            chosen = self._pick_one(self._all_scene_items, rng)
            if not chosen:
                return None
        elif category in getattr(self, "_scene_groups_by_category", {}):
            pool = self._scene_groups_by_category[category]
            chosen = self._pick_one(pool if pool else self._all_scene_items, rng)
            if not chosen:
                return None
        else:
            # EXPLICIT: exact match by label, id, or alias (zero substring collision)
            chosen = self._scene_by_label.get(category) or self._scene_by_id.get(category) or self._scene_by_alias.get(category)
            if not chosen:
                raise DataSelectionError(f"Unknown scene category: {category!r}")

        anchors = chosen.get("anchor_tags", [])
        details = chosen.get("detail_tags", [])

        if not anchors:
            anchors = chosen.get("tags", ["room"])

        anchor = rng.choice(anchors) if anchors else "room"
        sampled_tags = [anchor]

        if details:
            detail_count = min(rng.randint(1, 2), len(details))
            sampled_tags.extend(rng.sample(details, detail_count))

        c_id = chosen.get("id", "scene_unknown")
        ctx_ids = tuple(chosen.get("context_ids", ("generic",)))
        prov = TagProvenance(
            item_id=c_id,
            semantic_ids=tuple([f"scene:{c_id}"] + [f"context:{c}" for c in ctx_ids]),
            kind="scene"
        )
        return SampleResult(
            tags=tuple(sampled_tags),
            item_id=c_id,
            context_ids=ctx_ids,
            exclusive_group=chosen.get("exclusive_group"),
            provenance=prov,
        )

    def sample_scene(self, category: str, rng: Random) -> List[str]:
        if _is_none(category):
            return []
        res = self.sample_scene_result(category, rng)
        return list(res.tags) if res else []

    def sample_theme_result(self, theme: str, rng: Random) -> Optional[ThemeSampleResult]:
        if _is_none(theme):
            return None
        data = self._load("themes")
        themes = data.get("themes", [])
        if not themes:
            return None

        if _is_random(theme):
            t = self._pick_one(themes, rng)
        else:
            idx = self._get_catalog_index("themes", themes)
            t = idx.get(theme)
            if not t:
                raise DataSelectionError(f"Unknown theme: {theme!r}")

        t_id = t.get("id", "theme_unknown")
        tags = self._flatten_tags(t)
        sampled = self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []
        prov = TagProvenance(
            item_id=t_id,
            semantic_ids=(f"theme:{t_id}",),
            kind="theme"
        )
        sampled_tags = tuple(SampledTag(text=tag, provenance=prov) for tag in sampled)
        return ThemeSampleResult(
            tags=sampled_tags,
            theme_id=t_id,
            provenance=prov,
        )

    def sample_theme(self, theme: str, rng: Random) -> List[str]:
        if _is_none(theme):
            return []
        res = self.sample_theme_result(theme, rng)
        return list(res.all_text_tags) if res else []

    # ─── 槽位 2: 景别 + 视角 + 画质/设备 ───

    def sample_shot_type_result(self, shot_type: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(shot_type):
            return None
        data = self._load("shot_types")
        shots = data.get("shot_types", [])
        if not shots:
            return None

        if _is_random(shot_type) or _is_auto(shot_type):
            chosen = self._pick_one(shots, rng)
        else:
            chosen = _match_item(shots, shot_type, "shot_types")
            if not chosen:
                raise DataSelectionError(f"Unknown shot type: {shot_type!r}")

        tags = self._flatten_tags(chosen)
        selected_tags = tags[:2] if tags else []
        item_id = chosen.get("id", "shot_type_custom")
        prov = TagProvenance(
            item_id=item_id,
            kind="shot_type",
            semantic_ids=(f"shot:{item_id}",),
        )
        return SampleResult(
            tags=tuple(selected_tags),
            item_id=item_id,
            provenance=prov,
        )

    def sample_shot_type(self, shot_type: str, rng: Random) -> List[str]:
        res = self.sample_shot_type_result(shot_type, rng)
        return list(res.tags) if res else []

    def sample_camera_angle_result(self, angle: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(angle):
            return None
        data = self._load("shot_types")
        angles = data.get("camera_angles", [])
        if not angles:
            return None

        if _is_random(angle) or _is_auto(angle):
            chosen = self._pick_one(angles, rng)
        else:
            chosen = _match_item(angles, angle, "camera_angles")
            if not chosen:
                raise DataSelectionError(f"Unknown camera angle: {angle!r}")

        tags = self._flatten_tags(chosen)
        selected_tags = tags[:1] if tags else []
        item_id = chosen.get("id", "camera_angle_custom")
        prov = TagProvenance(
            item_id=item_id,
            kind="camera_angle",
            semantic_ids=(f"angle:{item_id}",),
        )
        return SampleResult(
            tags=tuple(selected_tags),
            item_id=item_id,
            provenance=prov,
        )

    def sample_camera_angle(self, angle: str, rng: Random) -> List[str]:
        res = self.sample_camera_angle_result(angle, rng)
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
        item_id, tags = quality_map[q_norm]
        prov = TagProvenance(
            item_id=item_id,
            kind="quality",
            semantic_ids=(f"quality:{item_id}",),
        )
        return SampleResult(
            tags=tuple(tags),
            item_id=item_id,
            provenance=prov,
        )

    def sample_quality_tags(self, quality_tier: str) -> List[str]:
        res = self.sample_quality_result(quality_tier)
        return list(res.tags) if res else []

    # ─── 槽位 3: 裸露状态 ───

    def sample_nudity(self, level: str | int, rng: Random) -> Tuple[List[str], str]:
        """返回 (采样tags, 标准等级代码比如L1/L2/L3/L4/L5/L6)"""
        if _is_none(level):
            return ([], "L1")

        data = self._load("nudity_levels")
        levels = data.get("nudity_levels", [])
        if not levels:
            return ([], "L3")

        if _is_random(level):
            lvl = self._pick_one(levels, rng)
        else:
            lvl = _match_item(levels, str(level), "nudity_levels")
            if not lvl:
                # 规范化整数字符串或纯数字 (如 1 -> L1)
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

        tags = self._flatten_tags(lvl)
        sampled_tags = self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []
        return (sampled_tags, lvl_code)

    def sample_nudity_result(self, level: str | int, rng: Random) -> Tuple[Optional[SampleResult], str]:
        tags, lvl_code = self.sample_nudity(level, rng)
        if not tags:
            return None, lvl_code
        prov = TagProvenance(
            item_id=f"nudity_{lvl_code}",
            kind="nudity",
            semantic_ids=(f"nudity:{lvl_code}",),
        )
        return SampleResult(tags=tuple(tags), item_id=f"nudity_{lvl_code}", provenance=prov), lvl_code

    # ─── 槽位 4: 服装款式与穿脱状态 ───

    def sample_clothing_result(
        self,
        style: str,
        state: str,
        nudity_level_code: str,
        rng: Random,
        context: Optional[str] = None
    ) -> ClothingSampleResult:
        """
        结构化采样服装标签及语义 Provenance，彻底解耦 DataSampler 与 PromptFragment。
        严格落实四态契约：
        - None: 纯净基础款式，不加状态词、不加裸露联动override、不加扩展库。
        - Auto: 联动裸露等级，L2/L3/L4 接入 24 档数据驱动扩展。
        - Random: 随机合法状态，L2/L3/L4 接入 24 档数据驱动扩展。
        - Explicit: 指定状态，L2/L3/L4 接入 24 档数据驱动扩展。
        """
        if _is_none(style):
            return ClothingSampleResult(base_tags=(), nudity_level=nudity_level_code)

        data = self._load("clothing")
        styles = data.get("categories", [])
        if not styles:
            return ClothingSampleResult(base_tags=(), nudity_level=nudity_level_code)

        # 1. 确定服装款式对象
        style_mode = get_selection_mode(style)
        if style_mode == SelectionMode.RANDOM:
            aff_ids = self._get_affinity_ids(context, "clothing")
            if aff_ids and rng.random() < 0.85:
                pool = [c for c in styles if c.get("id") in aff_ids]
                chosen_style = self._pick_one(pool if pool else styles, rng)
            else:
                chosen_style = self._pick_one(styles, rng)
        else:
            chosen_style = _match_item(styles, style)
            if not chosen_style:
                raise DataSelectionError(f"Unknown clothing style: {style!r}")

        c_id = chosen_style.get("id", "")
        base_style_tags_raw = self._flatten_tags(chosen_style)
        base_tags_tuple = tuple(
            SampledTag(
                text=t,
                provenance=TagProvenance(
                    item_id=c_id,
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"),
                    kind="base_clothing"
                )
            )
            for t in base_style_tags_raw
        )

        state_mode = get_selection_mode(state)

        # 契约 1: 状态为 None -> 仅保留基础款式，不加状态词、不加裸露联动override、不加扩展
        if state_mode == SelectionMode.NONE:
            return ClothingSampleResult(
                base_tags=base_tags_tuple,
                state_tags=(),
                extension_tags=(),
                style_id=c_id,
                state_id=None,
                nudity_level=nudity_level_code
            )

        linkages = data.get("clothing_nudity_linkage", {})
        linkage_data = linkages.get(nudity_level_code, {})
        style_overrides = linkage_data.get("style_overrides", {})
        states = data.get("clothing_states", [])

        state_tags_list: List[SampledTag] = []
        state_id: Optional[str] = None

        # L1, L5, L6: 强力应用 style_overrides 保证纯净性与防泄漏
        if nudity_level_code in ("L1", "L5", "L6") and c_id in style_overrides:
            override_tags = list(style_overrides[c_id])
            state_tags_list = [
                SampledTag(text=t, provenance=TagProvenance(item_id=c_id, semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "override:linkage"), kind="clothing_state"))
                for t in override_tags
            ]
            return ClothingSampleResult(
                base_tags=(),
                state_tags=tuple(state_tags_list),
                extension_tags=(),
                style_id=c_id,
                state_id="linkage_override",
                nudity_level=nudity_level_code
            )

        if state_mode == SelectionMode.AUTO:
            if nudity_level_code in ("L1", "L5", "L6"):
                gen_tags = linkage_data.get("general_tags", [])
                chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
                chosen_base = self._pick([t.text for t in base_tags_tuple], rng, min(2, len(base_tags_tuple)))
                return ClothingSampleResult(
                    base_tags=tuple(SampledTag(text=t, provenance=TagProvenance(item_id=c_id, semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"), kind="base_clothing")) for t in chosen_base),
                    state_tags=tuple(SampledTag(text=t, provenance=TagProvenance(item_id="linkage_general", semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "linkage:general"), kind="clothing_state")) for t in chosen_gen),
                    extension_tags=(),
                    style_id=c_id,
                    state_id="linkage_general",
                    nudity_level=nudity_level_code
                )

            else:
                # L2, L3, L4 Auto mode
                if c_id in style_overrides:
                    override_tags = list(style_overrides[c_id])
                    state_tags_list = [
                        SampledTag(text=t, provenance=TagProvenance(item_id=c_id, semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "override:linkage"), kind="clothing_state"))
                        for t in override_tags
                    ]
                else:
                    gen_tags = linkage_data.get("general_tags", [])
                    chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
                    chosen_base = self._pick([t.text for t in base_tags_tuple], rng, min(2, len(base_tags_tuple)))
                    base_tags_tuple = tuple(SampledTag(text=t, provenance=TagProvenance(item_id=c_id, semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"), kind="base_clothing")) for t in chosen_base)
                    state_tags_list = [
                        SampledTag(text=t, provenance=TagProvenance(item_id="linkage_general", semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "linkage:general"), kind="clothing_state"))
                        for t in chosen_gen
                    ]
                state_id = "auto_linkage"

        elif state_mode == SelectionMode.RANDOM:
            if nudity_level_code == "L1":
                allowed_state_ids = {"normal"}
            elif nudity_level_code == "L2":
                allowed_state_ids = {"normal", "unbuttoned", "slipping_off", "disheveled", "wet_clinging", "sweat_soaked"}
            elif nudity_level_code == "L3":
                allowed_state_ids = {"unbuttoned", "slipping_off", "lifted_up", "pulled_down", "wet_clinging", "torn_shredded"}
            elif nudity_level_code == "L4":
                allowed_state_ids = {"only_lingerie", "pulled_down", "lifted_up", "torn_shredded", "slipping_off"}
            else:
                allowed_state_ids = set()

            pool = [s for s in states if s.get("id") in allowed_state_ids] if allowed_state_ids else states
            chosen_state = self._pick_one(pool if pool else states, rng)
            state_id = chosen_state.get("id", "")
            raw_state_tags = self._pick(self._flatten_tags(chosen_state), rng, min(2, len(self._flatten_tags(chosen_state))))
            chosen_base = self._pick([t.text for t in base_tags_tuple], rng, min(2, len(base_tags_tuple)))
            base_tags_tuple = tuple(SampledTag(text=t, provenance=TagProvenance(item_id=c_id, semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"), kind="base_clothing")) for t in chosen_base)
            state_tags_list = [
                SampledTag(text=t, provenance=TagProvenance(item_id=state_id, semantic_ids=(f"clothing:{c_id}", f"state:{state_id}", f"nudity:{nudity_level_code}"), kind="clothing_state"))
                for t in raw_state_tags
            ]

        else:
            # EXPLICIT
            chosen_state = _match_item(states, state, "clothing_states")
            if not chosen_state:
                raise DataSelectionError(f"Unknown clothing state: {state!r}")
            state_id = chosen_state.get("id", "")
            raw_state_tags = self._pick(self._flatten_tags(chosen_state), rng, min(2, len(self._flatten_tags(chosen_state))))
            chosen_base = self._pick([t.text for t in base_tags_tuple], rng, min(2, len(base_tags_tuple)))
            base_tags_tuple = tuple(SampledTag(text=t, provenance=TagProvenance(item_id=c_id, semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"), kind="base_clothing")) for t in chosen_base)
            state_tags_list = [
                SampledTag(text=t, provenance=TagProvenance(item_id=state_id, semantic_ids=(f"clothing:{c_id}", f"state:{state_id}", f"nudity:{nudity_level_code}"), kind="clothing_state"))
                for t in raw_state_tags
            ]

        # 契约：Auto, Random, Explicit 在 L2/L3/L4 均通过数据驱动采样扩展库
        extension_sampled_tags: List[SampledTag] = []
        if nudity_level_code in ("L2", "L3", "L4"):
            ext_tags = self._sample_clothing_extensions_provenance(nudity_level_code, c_id, rng, data)
            extension_sampled_tags.extend(ext_tags)

        return ClothingSampleResult(
            base_tags=base_tags_tuple,
            state_tags=tuple(state_tags_list),
            extension_tags=tuple(extension_sampled_tags),
            style_id=c_id,
            state_id=state_id,
            nudity_level=nudity_level_code
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
        data: Dict[str, Any]
    ) -> List[SampledTag]:
        """为服装受控应用 9/5/10 扩展库，返回携带真实 Provenance 的 SampledTag 列表。"""
        if nudity_level_code in ("L1", "L5", "L6") or not nudity_level_code:
            return []

        policy = data.get("extension_policy", {}).get(nudity_level_code, {})
        if not policy:
            return []

        result_tags: List[SampledTag] = []
        allowed_exp_ids = set(policy.get("exposure_ids", []))
        allowed_trans_ids = set(policy.get("transparency_ids", []))
        allowed_wardrobe_ids = set(policy.get("wardrobe_ids", []))

        all_exp_tiers = [t for t in data.get("sfw_exposure_tiers", []) if t.get("id") in allowed_exp_ids]
        all_trans_tiers = [t for t in data.get("cloth_transparency_tiers", []) if t.get("id") in allowed_trans_ids]
        all_wardrobe = [t for t in data.get("lingerie_wardrobe", []) if t.get("id") in allowed_wardrobe_ids]

        if nudity_level_code == "L2":
            if all_exp_tiers and rng.random() < 0.7:
                picked = self._pick_one(all_exp_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:sfw_exposure", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))
            if all_trans_tiers and rng.random() < 0.5:
                picked = self._pick_one(all_trans_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:cloth_transparency", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))

        elif nudity_level_code == "L3":
            if all_exp_tiers and rng.random() < 0.7:
                picked = self._pick_one(all_exp_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:sfw_exposure", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))
            if all_trans_tiers and rng.random() < 0.6:
                picked = self._pick_one(all_trans_tiers, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:1]:
                    result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:cloth_transparency", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))

        elif nudity_level_code == "L4":
            if all_wardrobe and (clothing_id == "lingerie_lace" or rng.random() < 0.6):
                picked = self._pick_one(all_wardrobe, rng)
                t_id = picked.get("id", "")
                for t in picked.get("tags", [])[:2]:
                    result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:lingerie_wardrobe", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))
            else:
                if all_exp_tiers and rng.random() < 0.6:
                    picked = self._pick_one(all_exp_tiers, rng)
                    t_id = picked.get("id", "")
                    for t in picked.get("tags", [])[:1]:
                        result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:sfw_exposure", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))
                if all_trans_tiers and rng.random() < 0.5:
                    picked = self._pick_one(all_trans_tiers, rng)
                    t_id = picked.get("id", "")
                    for t in picked.get("tags", [])[:1]:
                        result_tags.append(SampledTag(text=t, provenance=TagProvenance(item_id=t_id, semantic_ids=("extension_family:cloth_transparency", f"extension_tier:{t_id}", f"nudity:{nudity_level_code}"), kind="clothing_extension")))

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

    def sample_lighting_result(self, preset: str, rng: Random, nudity_level_code: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(preset):
            return None
        data = self._load("lighting")

        if _is_auto(preset) or _is_random(preset):
            result: List[str] = []
            chosen_id = "lighting_auto"
            techniques = data.get("professional_lighting", [])
            if techniques:
                tech = self._pick_one(techniques, rng)
                chosen_id = tech.get("id", chosen_id)
                tags = list(tech.get("tags", []))
                if nudity_level_code != "L1":
                    tags.extend(tech.get("erotic_tags", []))
                if tags:
                    result.extend(self._pick(tags, rng, min(2, len(tags))))

            temps = data.get("color_temperature_table", [])
            if temps:
                temp = self._pick_one(temps, rng)
                t_tags = self._flatten_tags(temp)
                if t_tags:
                    result.append(t_tags[0])
            prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",))
            return SampleResult(tags=tuple(result), item_id=chosen_id, provenance=prov)

        # EXPLICIT
        combos = data.get("preset_combos", [])
        p = _match_item(combos, preset, "lighting_combos")
        if p:
            parts = []
            for k in ["main_light", "modifier_light", "atmosphere"]:
                v = p.get(k, "")
                if v:
                    parts.extend([t.strip() for t in v.split(",") if t.strip()])
            chosen_id = p.get("id", "lighting_combo")
            prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",))
            return SampleResult(tags=tuple(parts), item_id=chosen_id, provenance=prov)

        all_other = []
        for sec in ["professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting"]:
            items = data.get(sec, [])
            if isinstance(items, list):
                all_other.extend(items)

        item_match = _match_item(all_other, preset, "lighting_presets")
        if item_match:
            tags = list(item_match.get("tags", []))
            if nudity_level_code != "L1":
                tags.extend(item_match.get("erotic_tags", []))
            chosen_id = item_match.get("id", "lighting_preset")
            selected = tags[:2] if tags else []
            prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",))
            return SampleResult(tags=tuple(selected), item_id=chosen_id, provenance=prov)

        raise DataSelectionError(f"Unknown lighting preset: {preset!r}")

    def sample_lighting(self, preset: str, rng: Random, nudity_level_code: Optional[str] = None) -> List[str]:
        res = self.sample_lighting_result(preset, rng, nudity_level_code)
        return list(res.tags) if res else []

    # ─── 槽位 6: 姿势动作 ───

    def sample_pose_result(self, category: str, rng: Random, nudity_level_code: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(category):
            return None
        data = self._load("poses")
        categories = data.get("pose_categories", [])
        if not categories:
            return None

        if _is_random(category):
            cat = self._pick_one(categories, rng)
        else:
            cat = _match_item(categories, category, "poses")
            if not cat:
                raise DataSelectionError(f"Unknown pose category: {category!r}")

        all_tags: List[str] = []
        for sub in cat.get("subcategories", []):
            all_tags.extend(sub.get("tags", []))
        if not all_tags:
            all_tags = self._flatten_tags(cat)

        if nudity_level_code == "L1":
            banned_in_l1 = ["skirt lifted", "skirt hiked", "skirt pulled", "skirt riding", "bra", "panties", "breasts", "pussy", "nude", "naked", "undressed", "cock sliding", "penetrated", "face-fucked", "cum dripping", "dripping on", "cupping breasts"]
            all_tags = [t for t in all_tags if not any(b in t.lower() for b in banned_in_l1)]

        selected = self._pick(all_tags, rng, min(rng.randint(1, 2), max(1, len(all_tags)))) if all_tags else []
        cat_id = cat.get("id", "pose_default")
        prov = TagProvenance(item_id=cat_id, kind="pose", semantic_ids=(f"pose:{cat_id}",))
        return SampleResult(tags=tuple(selected), item_id=cat_id, provenance=prov)

    def sample_pose(self, category: str, rng: Random, nudity_level_code: Optional[str] = None) -> List[str]:
        res = self.sample_pose_result(category, rng, nudity_level_code)
        return list(res.tags) if res else []

    # ─── 槽位 7: 表情眼神 ───

    def sample_expression_result(self, mood: str, rng: Random) -> Optional[SampleResult]:
        if _is_none(mood):
            return None
        data = self._load("expressions")
        categories = data.get("emotions", [])
        if not categories:
            return None

        if _is_random(mood):
            cat = self._pick_one(categories, rng)
        else:
            cat = _match_item(categories, mood, "expressions")
            if not cat:
                raise DataSelectionError(f"Unknown expression mood: {mood!r}")

        tags = self._flatten_tags(cat)
        selected = self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []
        cat_id = cat.get("id", "expression_default")
        prov = TagProvenance(item_id=cat_id, kind="expression", semantic_ids=(f"expression:{cat_id}",))
        return SampleResult(tags=tuple(selected), item_id=cat_id, provenance=prov)

    def sample_expression(self, mood: str, rng: Random) -> List[str]:
        res = self.sample_expression_result(mood, rng)
        return list(res.tags) if res else []

    # ─── 槽位 8: 风格/胶片 ───

    def sample_film_result(self, stock: str, rng: Random) -> Optional[SampleResult]:
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

        if _is_random(stock):
            film_list = data.get("film_stocks", all_items)
            chosen_film = self._pick_one(film_list, rng)
        else:
            chosen_film = _match_item(all_items, stock)
            if not chosen_film:
                raise DataSelectionError(f"Unknown film stock: {stock!r}")

        if not chosen_film:
            return None

        tags = tuple(self._flatten_tags(chosen_film)[:3])
        c_id = chosen_film.get("id", "film_unknown")
        prov = TagProvenance(
            kind="film",
            item_id=c_id,
            semantic_ids=(f"film:{c_id}",),
        )
        return SampleResult(
            tags=tags,
            item_id=c_id,
            provenance=prov,
        )

    def sample_film(self, stock: str, rng: Random) -> List[str]:
        res = self.sample_film_result(stock, rng)
        return list(res.tags) if res else []

    # ─── 槽位 9: 妆容细节 ───

    def sample_makeup_result(self, makeup_style: str, rng: Random, context: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(makeup_style):
            return None
        data = self._load("makeup")
        styles = data.get("categories", [])
        if not styles:
            return None

        if _is_random(makeup_style):
            aff_ids = self._get_affinity_ids(context, "makeup")
            if aff_ids and rng.random() < 0.85:
                pool = [m for m in styles if m.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else styles, rng)
            else:
                chosen = self._pick_one(styles, rng)
        else:
            chosen = _match_item(styles, makeup_style, "makeup")
            if not chosen:
                raise DataSelectionError(f"Unknown makeup style: {makeup_style!r}")

        tags = self._flatten_tags(chosen)
        selected = self._pick(tags, rng, min(rng.randint(2, 3), len(tags))) if tags else []
        c_id = chosen.get("id", "makeup_default")
        prov = TagProvenance(item_id=c_id, kind="makeup", semantic_ids=(f"makeup:{c_id}",))
        return SampleResult(tags=tuple(selected), item_id=c_id, provenance=prov)

    def sample_makeup(self, makeup_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_makeup_result(makeup_style, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 10: 发型与饰品 ───

    def sample_hairstyle_result(self, hairstyle: str, rng: Random, context: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(hairstyle):
            return None
        data = self._load("accessories")
        styles = data.get("hairstyles", [])
        if not styles:
            return None

        if _is_random(hairstyle):
            aff_ids = self._get_affinity_ids(context, "hairstyles")
            if aff_ids and rng.random() < 0.85:
                pool = [h for h in styles if h.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else styles, rng)
            else:
                chosen = self._pick_one(styles, rng)
        else:
            chosen = _match_item(styles, hairstyle, "hairstyles")
            if not chosen:
                raise DataSelectionError(f"Unknown hairstyle: {hairstyle!r}")

        tags = self._flatten_tags(chosen)
        selected = tags[:2] if tags else []
        c_id = chosen.get("id", "hairstyle_default")
        prov = TagProvenance(item_id=c_id, kind="hairstyle", semantic_ids=(f"hairstyle:{c_id}",))
        return SampleResult(tags=tuple(selected), item_id=c_id, provenance=prov)

    def sample_hairstyle(self, hairstyle: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_hairstyle_result(hairstyle, rng, context)
        return list(res.tags) if res else []

    def sample_jewelry_result(
        self, jewelry_style: str, rng: Random, context: Optional[str] = None
    ) -> Optional[SampleResult]:
        if _is_none(jewelry_style):
            return None
        data = self._load("accessories")
        items = data.get("headwear_jewelry", [])
        if not items:
            return None

        if _is_random(jewelry_style):
            aff_ids = self._get_affinity_ids(context, "headwear_jewelry")
            if aff_ids and rng.random() < 0.85:
                pool = [j for j in items if j.get("id") in aff_ids]
                chosen = self._pick_one(pool if pool else items, rng)
            else:
                chosen = self._pick_one(items, rng)
        else:
            chosen = _match_item(items, jewelry_style)
            if not chosen:
                raise DataSelectionError(f"Unknown jewelry style: {jewelry_style!r}")

        if not chosen:
            return None

        tags = tuple(self._flatten_tags(chosen)[:2])
        c_id = chosen.get("id", "jewelry_unknown")
        prov = TagProvenance(
            kind="jewelry",
            item_id=c_id,
            semantic_ids=(f"jewelry:{c_id}",),
        )
        return SampleResult(
            tags=tags,
            item_id=c_id,
            provenance=prov,
        )

    def sample_jewelry(self, jewelry_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_jewelry_result(jewelry_style, rng, context)
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
        else:
            chosen = _match_item(categories, imp_type)
            if not chosen:
                raise DataSelectionError(f"Unknown imperfection type: {imp_type!r}")

        tags = self._flatten_tags(chosen)
        selected = self._pick(tags, rng, min(2, len(tags))) if tags else []
        c_id = chosen.get("id", "imperfection_default")
        prov = TagProvenance(item_id=c_id, kind="imperfections", semantic_ids=(f"imperfection:{c_id}",))
        return SampleResult(tags=tuple(selected), item_id=c_id, provenance=prov)

    def sample_imperfections(self, imp_type: str, rng: Random) -> List[str]:
        res = self.sample_imperfections_result(imp_type, rng)
        return list(res.tags) if res else []

    # ─── 槽位 12: 纹身标记与皮肤融合 ───

    def sample_tattoo_result(
        self, tattoo_style: str, rng: Random, context: Optional[str] = None
    ) -> Optional[SampleResult]:
        if _is_none(tattoo_style):
            return None
        data = self._load("tattoos")
        tattoos = data.get("categories", [])
        if not tattoos:
            return None

        if _is_random(tattoo_style):
            aff_ids = self._get_affinity_ids(context, "tattoos")
            if aff_ids and rng.random() < 0.85:
                pool = [t for t in tattoos if t.get("id") in aff_ids and t.get("id") != "none"]
                chosen = self._pick_one(pool if pool else tattoos, rng)
            else:
                pool = [t for t in tattoos if t.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = _match_item(tattoos, tattoo_style)
            if not chosen:
                raise DataSelectionError(f"Unknown tattoo style: {tattoo_style!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        tags = tuple(self._flatten_tags(chosen))
        c_id = chosen.get("id", "tattoo_unknown")
        prov = TagProvenance(
            kind="tattoo",
            item_id=c_id,
            semantic_ids=(f"tattoo:{c_id}",),
        )
        return SampleResult(
            tags=tags,
            item_id=c_id,
            provenance=prov,
        )

    def sample_tattoo(self, tattoo_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_tattoo_result(tattoo_style, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 13: 道具宠物 ───

    def sample_prop_result(self, prop_style: str, rng: Random, context: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(prop_style):
            return None
        data = self._load("props")
        props = data.get("categories", [])
        if not props:
            return None

        if _is_random(prop_style):
            aff_ids = self._get_affinity_ids(context, "props")
            if aff_ids and rng.random() < 0.85:
                pool = [p for p in props if p.get("id") in aff_ids and p.get("id") != "none"]
                chosen = self._pick_one(pool if pool else props, rng)
            else:
                pool = [p for p in props if p.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = _match_item(props, prop_style)
            if not chosen:
                raise DataSelectionError(f"Unknown prop style: {prop_style!r}")

        if chosen.get("id") == "none":
            return None

        c_id = chosen.get("id", "prop_default")
        if chosen.get("items"):
            items = chosen["items"]
            picked_item = self._pick_one(items, rng)
            item_tags = self._flatten_tags(picked_item)
            selected = self._pick(item_tags, rng, min(2, len(item_tags))) if item_tags else []
            item_id = picked_item.get("id", c_id)
        else:
            tags = self._flatten_tags(chosen)
            selected = self._pick(tags, rng, min(2, len(tags))) if tags else []
            item_id = c_id

        prov = TagProvenance(item_id=item_id, kind="prop", semantic_ids=(f"prop:{item_id}",))
        return SampleResult(tags=tuple(selected), item_id=item_id, provenance=prov)

    def sample_prop(self, prop_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_prop_result(prop_style, rng, context)
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
        else:
            chosen = next((t for t in tiers if t.get("name_zh") == tier or t.get("id") == tier or t.get("name") == tier), None)
            if not chosen:
                raise DataSelectionError(f"Unknown sfw exposure tier: {tier!r}")
        if not chosen:
            return None
        c_id = chosen.get("id", "sfw_exposure_default")
        tags = list(chosen.get("tags", []))
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(tags=tuple(tags), item_id=c_id, provenance=prov)

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
        else:
            chosen = next((t for t in tiers if t.get("name_zh") == tier or t.get("id") == tier or t.get("name") == tier), None)
            if not chosen:
                raise DataSelectionError(f"Unknown cloth transparency tier: {tier!r}")
        if not chosen:
            return None
        c_id = chosen.get("id", "cloth_transparency_default")
        tags = list(chosen.get("tags", []))
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(tags=tuple(tags), item_id=c_id, provenance=prov)

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
        else:
            chosen = next((w for w in wardrobe if w.get("name_zh") == cat or w.get("id") == cat or w.get("name") == cat), None)
            if not chosen:
                raise DataSelectionError(f"Unknown lingerie wardrobe category: {cat!r}")
        if not chosen:
            return None
        c_id = chosen.get("id", "lingerie_wardrobe_default")
        selected = self._pick(chosen.get("tags", []), rng, min(2, len(chosen.get("tags", [])))) if chosen.get("tags") else []
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(tags=tuple(selected), item_id=c_id, provenance=prov)

    def sample_lingerie_wardrobe(self, cat: str, rng: Random) -> List[str]:
        res = self.sample_lingerie_wardrobe_result(cat, rng)
        return list(res.tags) if res else []

    # ─── 槽位 14: 人格角色卡 ───

    def sample_character_result(self, character_role: str, rng: Random, context: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(character_role):
            return None
        data = self._load("characters")
        chars = data.get("characters", [])
        if not chars:
            return None

        if _is_random(character_role):
            aff_ids = self._get_affinity_ids(context, "characters")
            if aff_ids and rng.random() < 0.85:
                pool = [c for c in chars if c.get("id") in aff_ids and c.get("id") != "none"]
                chosen = self._pick_one(pool if pool else chars, rng)
            else:
                pool = [c for c in chars if c.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = _match_item(chars, character_role)
            if not chosen:
                raise DataSelectionError(f"Unknown character role: {character_role!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        tags = self._flatten_tags(chosen)
        selected = tags[:2] if tags else []
        c_id = chosen.get("id", "character_default")
        prov = TagProvenance(item_id=c_id, kind="character", semantic_ids=(f"character:{c_id}",))
        return SampleResult(tags=tuple(selected), item_id=c_id, provenance=prov)

    def sample_character(self, character_role: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_character_result(character_role, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 15: 液体体液系统 ───

    def sample_liquid_result(self, liquid_effect: str, rng: Random, context: Optional[str] = None) -> Optional[SampleResult]:
        if _is_none(liquid_effect):
            return None
        data = self._load("nudity_levels")
        liquids = data.get("liquid_effects", [])
        if not liquids:
            return None

        if _is_random(liquid_effect):
            aff_ids = self._get_affinity_ids(context, "liquids")
            if aff_ids and rng.random() < 0.85:
                pool = [liq for liq in liquids if liq.get("id") in aff_ids and liq.get("id") != "none"]
                chosen = self._pick_one(pool if pool else liquids, rng)
            else:
                pool = [liq for liq in liquids if liq.get("id") != "none"]
                chosen = self._pick_one(pool, rng)
        else:
            chosen = _match_item(liquids, liquid_effect)
            if not chosen:
                raise DataSelectionError(f"Unknown liquid effect: {liquid_effect!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        tags = self._flatten_tags(chosen)
        selected = self._pick(tags, rng, min(2, len(tags))) if tags else []
        c_id = chosen.get("id", "liquid_default")
        prov = TagProvenance(item_id=c_id, kind="liquid", semantic_ids=(f"liquid:{c_id}",))
        return SampleResult(tags=tuple(selected), item_id=c_id, provenance=prov)

    def sample_liquid(self, liquid_effect: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_liquid_result(liquid_effect, rng, context)
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
