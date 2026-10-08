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
    from .conflict_resolver import GarmentCarrierEntity, is_garment_compatible_with_state
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
        PromptAtom,
        SampleResult,
        SampledTag,
        SemanticFacts,
        SpanType,
        TagProvenance,
        ThemeSampleResult,
    )
else:
    from lib.conflict_resolver import GarmentCarrierEntity, is_garment_compatible_with_state
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
        PromptAtom,
        SampleResult,
        SampledTag,
        SemanticFacts,
        SpanType,
        TagProvenance,
        ThemeSampleResult,
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

    def _sample_from_candidates(
        self,
        slot_name: str,
        candidates: Sequence[Dict[str, Any]],
        rng: Random,
        profile: Optional[ContextProfile] = None,
        context: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None
        cand_map = {c.get("id"): c for c in candidates if "id" in c}
        cand_ids = [c["id"] for c in candidates if "id" in c]
        if not cand_ids:
            return self._pick_one(list(candidates), rng)

        if profile is None:
            if context:
                profile = compute_context_profile([context], [])
            else:
                profile = compute_context_profile([], [])

        if hasattr(self, "affinity_registry") and slot_name in self.affinity_registry.matrix.get("generic", {}):
            dist = compute_slot_distribution(
                slot_name,
                profile,
                cand_ids,
                self.affinity_registry.matrix,
            )
            chosen_id = sample_categorical(dist, rng)
            return cand_map.get(chosen_id, self._pick_one(list(candidates), rng))
        else:
            return self._pick_one(list(candidates), rng)

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

        # 1. 复合风格穿搭组合池严格单选 1 套完整穿搭 (杜绝拼接冲突长句与 L1 穿透)
        if c_id == "clothing_ensemble_combos" or chosen_style.get("is_ensemble"):
            if nudity_level_code == "L1":
                banned_l1_words = (
                    "bra", "panties", "breasts", "pussy", "nude", "naked",
                    "undressed", "topless", "upskirt", "thong", "g-string",
                    "boudoir", "lingerie"
                )
                filtered = [
                    t for t in all_tags
                    if not (
                        (isinstance(t, dict) and t.get("facts", {}).get("style_genre") == "boudoir_lingerie")
                        or any(bw in (t.get("text", "") if isinstance(t, dict) else str(t)).lower() for bw in banned_l1_words)
                    )
                ]
                if not filtered:
                    return []
                return [rng.choice(filtered)]
            return [rng.choice(all_tags)]

        # 2. 独立单品款式池（例如 16 个新增独立单品大类）：各标签均为独立完整款式，严格单选 1 件
        if c_id in STANDALONE_CLOTHING_CATEGORIES or chosen_style.get("is_standalone_pieces"):
            return [rng.choice(all_tags)]

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
                if isinstance(t, dict):
                    role = t.get("role")

                    if role == "combinable_attribute":
                        attribute_candidates.append(t)
                    else:
                        silhouette_candidates.append(t)
                else:
                    silhouette_candidates.append(t)

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

        # 4. 其他分类（M3.2 既有款式与套装构件，如 denim_shorts, hanfu, jk_seifuku, maid_dress 等）：
        # 全量传入 _to_sampled_tags 以保全各标签的 dynamic_slots 统计学抽样，随后由受控基线采样器及消解器处理。
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
        raw_tags = t.get("tags", [])
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
        )

    def sample_theme(self, theme: str, rng: Random) -> List[str]:
        if _is_none(theme):
            return []
        res = self.sample_theme_result(theme, rng)
        return list(res.all_text_tags) if res else []

    # ─── 槽位 2: 景别 + 视角 + 画质/设备 ───

    def sample_shot_type_result(self, shot_type: str, rng: Random, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(shot_type):
            return None
        data = self._load("shot_types")
        shots = data.get("shot_types", [])
        if not shots:
            return None

        target_tag = None
        if _is_random(shot_type) or _is_auto(shot_type):
            chosen = self._sample_from_candidates("shot_type", shots, rng, profile=context_profile)
        else:
            chosen = _match_item(shots, shot_type, "shot_types")
            if not chosen:
                tag_match = _match_tag(shots, shot_type)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown shot type: {shot_type!r}")

        raw_tags = chosen.get("tags", [])
        if target_tag:
            selected_raw = [target_tag]
        else:
            selected_raw = raw_tags[:2] if raw_tags else []
        item_id = chosen.get("id", "shot_type_custom")
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
        )

    def sample_shot_type(self, shot_type: str, rng: Random) -> List[str]:
        res = self.sample_shot_type_result(shot_type, rng)
        return list(res.tags) if res else []

    def sample_camera_angle_result(
        self,
        angle: str,
        rng: Random,
        context_profile: Optional[ContextProfile] = None,
        nudity_level_code: Optional[str] = None,
    ) -> Optional[SampleResult]:
        if _is_none(angle):
            return None
        data = self._load("shot_types")
        angles = data.get("camera_angles", [])
        if not angles:
            return None

        # L1 约束：在随机或自动模式下过滤情色/窥底特殊视角 (between_legs_angle)
        if nudity_level_code == "L1" and (_is_random(angle) or _is_auto(angle)):
            angles = [
                a for a in angles
                if a.get("id") != "between_legs_angle"
                and not any(
                    any(bw in (t.get("text", "") if isinstance(t, dict) else str(t)).lower() for bw in ("upskirt", "between legs", "crotch"))
                    for t in a.get("tags", [])
                )
            ]

        target_tag = None
        if _is_random(angle) or _is_auto(angle):
            chosen = self._sample_from_candidates("camera_angle", angles, rng, profile=context_profile)
        else:
            chosen = _match_item(angles, angle, "camera_angles")
            if not chosen:
                tag_match = _match_tag(angles, angle)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown camera angle: {angle!r}")

        raw_tags = chosen.get("tags", [])
        if target_tag:
            selected_raw = [target_tag]
        else:
            selected_raw = self._pick(raw_tags, rng, 1) if raw_tags else []
        item_id = chosen.get("id", "camera_angle_custom")
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
        )

    def sample_quality_tags(self, quality_tier: str) -> List[str]:
        res = self.sample_quality_result(quality_tier)
        return list(res.tags) if res else []

    # ─── 槽位 3: 裸露状态 ───

    def sample_nudity_result(self, level: str | int, rng: Random) -> Tuple[Optional[SampleResult], str]:
        if _is_none(level):
            return (None, "L1")

        data = self._load("nudity_levels")
        levels = data.get("nudity_levels", [])
        if not levels:
            return (None, "L3")

        if _is_random(level):
            lvl = self._pick_one(levels, rng)
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

        raw_tags = lvl.get("tags", [])
        selected = self._pick(raw_tags, rng, min(rng.randint(2, 3), len(raw_tags))) if raw_tags else []
        item_id = lvl.get("id", f"nudity_{lvl_code}")
        tags_str, sampled = self._to_sampled_tags(selected, item_id, "nudity")
        prov = TagProvenance(
            item_id=item_id,
            kind="nudity",
            semantic_ids=(f"nudity:{lvl_code}",),
        )
        return SampleResult(tags=tags_str, item_id=item_id, provenance=prov, sampled_tags=sampled), lvl_code

    def sample_nudity(self, level: str | int, rng: Random) -> Tuple[List[str], str]:
        """返回 (采样tags, 标准等级代码比如L1/L2/L3/L4/L5/L6)"""
        res, lvl_code = self.sample_nudity_result(level, rng)
        return (list(res.tags) if res else [], lvl_code)

    # ─── 槽位 4: 服装款式与穿脱状态 ───

    def sample_clothing_result(
        self,
        style: str,
        state: str,
        nudity_level_code: str,
        rng: Random,
        context: Optional[str] = None,
        context_profile: Optional[ContextProfile] = None,
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
        target_tag = None
        if style_mode == SelectionMode.RANDOM:
            chosen_style = self._sample_from_candidates("clothing", styles, rng, profile=context_profile, context=context)
        else:
            chosen_style = _match_item(styles, style)
            if not chosen_style:
                tag_match = _match_tag(styles, style)
                if tag_match:
                    chosen_style, target_tag = tag_match
            if not chosen_style:
                raise DataSelectionError(f"Unknown clothing style: {style!r}")

        c_id = chosen_style.get("id", "")
        if target_tag:
            if nudity_level_code == "L1":
                banned_l1_words = (
                    "bra", "panties", "breasts", "pussy", "nude", "naked",
                    "undressed", "topless", "upskirt", "thong", "g-string",
                    "boudoir", "lingerie"
                )
                is_banned = (
                    (isinstance(target_tag, dict) and target_tag.get("facts", {}).get("style_genre") == "boudoir_lingerie")
                    or any(bw in (target_tag.get("text", "") if isinstance(target_tag, dict) else str(target_tag)).lower() for bw in banned_l1_words)
                )
                raw_style_tags = [] if is_banned else [target_tag]
            else:
                raw_style_tags = [target_tag]
        else:
            raw_style_tags = self._select_clothing_style_tags(chosen_style, rng, nudity_level_code=nudity_level_code)
        _, all_base_sampled = self._to_sampled_tags(
            raw_style_tags,
            c_id,
            "base_clothing",
            semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}"),
            rng=rng,
        )
        base_tags_tuple = tuple(all_base_sampled)
        has_variant_metadata = self._has_variant_metadata(base_tags_tuple, chosen_style=chosen_style)

        state_mode = get_selection_mode(state)

        # 契约 1: 状态为 None -> 仅保留基础款式，不加状态词、不加裸露联动override、不加扩展
        if state_mode == SelectionMode.NONE:
            if has_variant_metadata:
                controlled_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
            else:
                controlled_base = base_tags_tuple
            return ClothingSampleResult(
                base_tags=controlled_base,
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

        state_tags_list: Sequence[SampledTag] = ()
        state_id: Optional[str] = None

        # L1, L5, L6: 强力应用 style_overrides 保证纯净性与防泄漏
        if nudity_level_code in ("L1", "L5", "L6"):
            if c_id in style_overrides:
                override_tags = list(style_overrides[c_id])
                _, state_tags_list = self._to_sampled_tags(
                    override_tags,
                    c_id,
                    "clothing_state",
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "override:linkage")
                )
                return ClothingSampleResult(
                    base_tags=(),
                    state_tags=tuple(state_tags_list),
                    extension_tags=(),
                    style_id=c_id,
                    state_id="linkage_override",
                    nudity_level=nudity_level_code
                )
            else:
                gen_tags = linkage_data.get("general_tags", [])
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
                return ClothingSampleResult(
                    base_tags=tuple(chosen_base),
                    state_tags=tuple(state_tags_list),
                    extension_tags=(),
                    style_id=c_id,
                    state_id="linkage_general",
                    nudity_level=nudity_level_code
                )

        if state_mode == SelectionMode.AUTO:
            if nudity_level_code in ("L1", "L5", "L6"):
                gen_tags = linkage_data.get("general_tags", [])
                chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
                _, state_tags_list = self._to_sampled_tags(
                    chosen_gen,
                    "linkage_general",
                    "clothing_state",
                    semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "linkage:general")
                )
                if has_variant_metadata:
                    chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
                else:
                    chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
                return ClothingSampleResult(
                    base_tags=tuple(chosen_base),
                    state_tags=tuple(state_tags_list),
                    extension_tags=(),
                    style_id=c_id,
                    state_id="linkage_general",
                    nudity_level=nudity_level_code
                )

            else:
                # L2, L3, L4 Auto mode
                if c_id in style_overrides:
                    override_tags = list(style_overrides[c_id])
                    _, state_tags_list = self._to_sampled_tags(
                        override_tags,
                        c_id,
                        "clothing_state",
                        semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "override:linkage")
                    )
                else:
                    gen_tags = linkage_data.get("general_tags", [])
                    chosen_gen = self._pick(gen_tags, rng, min(2, len(gen_tags)))
                    _, state_tags_list = self._to_sampled_tags(
                        chosen_gen,
                        "linkage_general",
                        "clothing_state",
                        semantic_ids=(f"clothing:{c_id}", f"nudity:{nudity_level_code}", "linkage:general")
                    )
                    if has_variant_metadata:
                        chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
                    else:
                        chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
                    base_tags_tuple = tuple(chosen_base)
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
            if has_variant_metadata:
                chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
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
            if has_variant_metadata:
                chosen_base = self._sample_controlled_base_clothing_tags(base_tags_tuple, rng)
            else:
                chosen_base = self._pick(base_tags_tuple, rng, min(2, len(base_tags_tuple)))
            base_tags_tuple = tuple(chosen_base)

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

    def sample_lighting_result(self, preset: str, rng: Random, nudity_level_code: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(preset):
            return None
        data = self._load("lighting")

        if _is_auto(preset) or _is_random(preset):
            combos = data.get("preset_combos", [])
            if combos and hasattr(self, "affinity_registry"):
                chosen_combo = self._sample_from_candidates("lighting", combos, rng, profile=context_profile)
                if chosen_combo:
                    chosen_id = chosen_combo.get("id", "lighting_combo")
                    parts = []
                    for k in ["main_light", "modifier_light", "atmosphere"]:
                        v = chosen_combo.get(k, "")
                        if v:
                            parts.extend([t.strip() for t in v.split(",") if t.strip()])
                    prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",), parent_ids=(chosen_id,))
                    sampled_tags: List[SampledTag] = []
                    for idx, t_text in enumerate(parts):
                        sampled_tags.append(
                            SampledTag(
                                text=t_text,
                                provenance=prov,
                                id=f"{chosen_id}__tag_{idx:03d}",
                                facts=SemanticFacts(semantic_role="selector"),
                            )
                        )
                    return SampleResult(tags=tuple(parts), item_id=chosen_id, provenance=prov, sampled_tags=tuple(sampled_tags))

            raw_result: List[Any] = []
            chosen_id = "lighting_auto"
            techniques = data.get("professional_lighting", [])
            if techniques:
                tech = self._pick_one(techniques, rng)
                chosen_id = tech.get("id", chosen_id)
                tags = list(tech.get("tags", []))
                if nudity_level_code != "L1":
                    tags.extend(tech.get("erotic_tags", []))
                if tags:
                    raw_result.extend(self._pick(tags, rng, min(2, len(tags))))

            temps = data.get("color_temperature_table", [])
            if temps:
                temp = self._pick_one(temps, rng)
                t_tags = self._flatten_tags(temp)
                if t_tags:
                    raw_result.append(t_tags[0])
            prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",), parent_ids=(chosen_id,))
            tags_str: List[str] = []
            sampled_tags: List[SampledTag] = []
            for idx, item in enumerate(raw_result):
                if isinstance(item, dict):
                    t_text = item.get("text", "")
                    t_id = item.get("id", f"{chosen_id}__tag_{idx:03d}")
                    t_facts = SemanticFacts.from_dict(item.get("facts", {}))
                    if t_facts.semantic_role is None:
                        t_facts = SemanticFacts.from_dict({**item.get("facts", {}), "semantic_role": "selector"})
                else:
                    t_text = str(item).strip()
                    t_id = f"{chosen_id}__tag_{idx:03d}"
                    t_facts = SemanticFacts(semantic_role="selector")
                tags_str.append(t_text)
                sampled_tags.append(
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
            return SampleResult(tags=tuple(tags_str), item_id=chosen_id, provenance=prov, sampled_tags=tuple(sampled_tags))

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
            prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",), parent_ids=(chosen_id,))
            sampled_tags = [
                SampledTag(
                    text=t,
                    provenance=prov,
                    id=f"{chosen_id}__tag_{idx:03d}",
                    facts=SemanticFacts(semantic_role="selector"),
                )
                for idx, t in enumerate(parts)
            ]
            return SampleResult(tags=tuple(parts), item_id=chosen_id, provenance=prov, sampled_tags=tuple(sampled_tags))

        all_other = []
        for sec in ["professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting"]:
            items = data.get(sec, [])
            if isinstance(items, list):
                all_other.extend(items)

        item_match = _match_item(all_other, preset, "lighting_presets")
        target_tag = None
        if not item_match:
            tag_match = _match_tag(all_other, preset)
            if tag_match:
                item_match, target_tag = tag_match

        if item_match:
            tags = list(item_match.get("tags", []))
            if nudity_level_code != "L1":
                tags.extend(item_match.get("erotic_tags", []))
            chosen_id = item_match.get("id", "lighting_preset")
            if target_tag:
                selected = [target_tag]
            else:
                selector_tags = [t for t in tags if (isinstance(t, dict) and (t.get("facts", {}).get("semantic_role") == "selector" or t.get("id", "").endswith("__tag_000")))]
                other_tags = [t for t in tags if t not in selector_tags]
                if selector_tags:
                    sel = self._pick_one(selector_tags, rng)
                    selected = [sel]
                    if other_tags and rng.random() < 0.5:
                        selected.append(self._pick_one(other_tags, rng))
                else:
                    selected = self._pick(tags, rng, min(rng.randint(1, 2), len(tags))) if tags else []
            prov = TagProvenance(item_id=chosen_id, kind="lighting", semantic_ids=(f"lighting:{chosen_id}",))
            tags_str = []
            sampled_tags = []
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
                else:
                    t_text = str(item).strip()
                    t_id = f"{chosen_id}__tag_{idx:03d}"
                    t_facts = SemanticFacts(time_of_day=primary_tod)
                tags_str.append(t_text)
                sampled_tags.append(
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
            return SampleResult(tags=tuple(tags_str), item_id=chosen_id, provenance=prov, sampled_tags=tuple(sampled_tags))

        raise DataSelectionError(f"Unknown lighting preset: {preset!r}")

    def sample_lighting(self, preset: str, rng: Random, nudity_level_code: Optional[str] = None) -> List[str]:
        res = self.sample_lighting_result(preset, rng, nudity_level_code)
        return list(res.tags) if res else []

    # ─── 槽位 6: 姿势动作 ───

    def sample_pose_result(self, category: str, rng: Random, nudity_level_code: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(category):
            return None
        data = self._load("poses")
        categories = data.get("pose_categories", [])
        if not categories:
            return None

        target_tag = None
        if _is_random(category):
            cat = self._sample_from_candidates("pose", categories, rng, profile=context_profile)
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
            return any(b in txt for b in banned_in_l1)

        if nudity_level_code == "L1":
            all_tags = [t for t in all_tags if not _is_banned_by_l1(t)]

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
        return SampleResult(tags=tuple(tags_str), item_id=cat_id, provenance=prov, sampled_tags=tuple(sampled_tags))

    def sample_pose(self, category: str, rng: Random, nudity_level_code: Optional[str] = None) -> List[str]:
        res = self.sample_pose_result(category, rng, nudity_level_code)
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
        return SampleResult(tags=tags_str, item_id=cat_id, provenance=prov, sampled_tags=sampled)

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
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

    def sample_makeup(self, makeup_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_makeup_result(makeup_style, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 10: 发型与饰品 ───

    def sample_hairstyle_result(self, hairstyle: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(hairstyle):
            return None
        data = self._load("accessories")
        styles = data.get("hairstyles", [])
        if not styles:
            return None

        target_tag = None
        if _is_random(hairstyle):
            chosen = self._sample_from_candidates("hairstyle", styles, rng, profile=context_profile, context=context)
        else:
            chosen = _match_item(styles, hairstyle, "hairstyles")
            if not chosen:
                tag_match = _match_tag(styles, hairstyle)
                if tag_match:
                    chosen, target_tag = tag_match
            if not chosen:
                raise DataSelectionError(f"Unknown hairstyle: {hairstyle!r}")

        raw_tags = chosen.get("tags", [])
        if target_tag:
            selected = [target_tag]
        else:
            selected = self._pick_tags_respecting_mutex(raw_tags, rng, chosen.get("id", "")) if raw_tags else []
        c_id = chosen.get("id", "hairstyle_default")
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "hairstyle", rng=rng)
        prov = TagProvenance(item_id=c_id, kind="hairstyle", semantic_ids=(f"hairstyle:{c_id}",))
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

    def sample_hairstyle(self, hairstyle: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_hairstyle_result(hairstyle, rng, context)
        return list(res.tags) if res else []

    def sample_jewelry_result(
        self, jewelry_style: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None
    ) -> Optional[SampleResult]:
        if _is_none(jewelry_style):
            return None
        data = self._load("accessories")
        items = list(data.get("headwear_jewelry", [])) + list(data.get("jewelry", []))
        if not items:
            return None

        target_tag = None
        if _is_random(jewelry_style):
            chosen = self._sample_from_candidates("jewelry", items, rng, profile=context_profile, context=context)
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

        raw_tags = chosen.get("tags", [])
        if target_tag:
            selected = [target_tag]
        else:
            selected = self._pick_tags_respecting_mutex(raw_tags, rng, chosen.get("id", "")) if raw_tags else []
        c_id = chosen.get("id", "jewelry_unknown")
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

        raw_tags = chosen.get("tags", [])
        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        c_id = chosen.get("id", "imperfection_default")
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "imperfections")
        prov = TagProvenance(item_id=c_id, kind="imperfections", semantic_ids=(f"imperfection:{c_id}",))
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

    def sample_imperfections(self, imp_type: str, rng: Random) -> List[str]:
        res = self.sample_imperfections_result(imp_type, rng)
        return list(res.tags) if res else []

    # ─── 槽位 12: 纹身标记与皮肤融合 ───

    def sample_tattoo_result(
        self, tattoo_style: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None
    ) -> Optional[SampleResult]:
        if _is_none(tattoo_style):
            return None
        data = self._load("tattoos")
        tattoos = data.get("categories", [])
        if not tattoos:
            return None

        if _is_random(tattoo_style):
            pool = [t for t in tattoos if t.get("id") != "none"]
            chosen = self._sample_from_candidates("tattoo", pool, rng, profile=context_profile, context=context)
        else:
            chosen = _match_item(tattoos, tattoo_style)
            if not chosen:
                raise DataSelectionError(f"Unknown tattoo style: {tattoo_style!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        raw_tags = chosen.get("tags", [])
        selected = raw_tags[:2] if raw_tags else []
        c_id = chosen.get("id", "tattoo_unknown")
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
        )

    def sample_tattoo(self, tattoo_style: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_tattoo_result(tattoo_style, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 13: 道具宠物 ───

    def sample_prop_result(self, prop_style: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(prop_style):
            return None
        data = self._load("props")
        props = data.get("categories", [])
        if not props:
            return None

        if _is_random(prop_style):
            pool = [p for p in props if p.get("id") != "none"]
            chosen = self._sample_from_candidates("props", pool, rng, profile=context_profile, context=context)
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
            item_id = picked_item.get("id", c_id)
            raw_tags = picked_item.get("tags", [])
            selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        else:
            item_id = c_id
            raw_tags = chosen.get("tags", [])
            selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []

        tags_str, sampled = self._to_sampled_tags(selected, item_id, "prop")
        prov = TagProvenance(item_id=item_id, kind="prop", semantic_ids=(f"prop:{item_id}",))
        return SampleResult(tags=tags_str, item_id=item_id, provenance=prov, sampled_tags=sampled)

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
        raw_tags = chosen.get("tags", [])
        selected = raw_tags[:2] if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "clothing_extension")
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

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
        raw_tags = chosen.get("tags", [])
        selected = raw_tags[:2] if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "clothing_extension")
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

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
        raw_tags = chosen.get("tags", [])
        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "clothing_extension")
        prov = TagProvenance(item_id=c_id, kind="clothing_extension", semantic_ids=(f"extension:{c_id}",))
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

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
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

    def sample_character(self, character_role: str, rng: Random, context: Optional[str] = None) -> List[str]:
        res = self.sample_character_result(character_role, rng, context)
        return list(res.tags) if res else []

    # ─── 槽位 15: 液体体液系统 ───

    def sample_liquid_result(self, liquid_effect: str, rng: Random, context: Optional[str] = None, context_profile: Optional[ContextProfile] = None) -> Optional[SampleResult]:
        if _is_none(liquid_effect):
            return None
        data = self._load("nudity_levels")
        liquids = data.get("body_liquids", []) + data.get("environmental_liquids", [])
        if not liquids:
            liquids = data.get("liquid_effects", [])
        if not liquids:
            return None

        if _is_random(liquid_effect):
            pool = [liq for liq in liquids if liq.get("id") != "none"]
            chosen = self._sample_from_candidates("liquids", pool, rng, profile=context_profile, context=context)
        else:
            chosen = _match_item(liquids, liquid_effect)
            if not chosen:
                raise DataSelectionError(f"Unknown liquid effect: {liquid_effect!r}")

        if not chosen or chosen.get("id") == "none":
            return None

        raw_tags = chosen.get("tags", [])
        selected = self._pick(raw_tags, rng, min(2, len(raw_tags))) if raw_tags else []
        c_id = chosen.get("id", "liquid_default")
        tags_str, sampled = self._to_sampled_tags(selected, c_id, "liquid")
        prov = TagProvenance(item_id=c_id, kind="liquid", semantic_ids=(f"liquid:{c_id}",))
        return SampleResult(tags=tags_str, item_id=c_id, provenance=prov, sampled_tags=sampled)

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
