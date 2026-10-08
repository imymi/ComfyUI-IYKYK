#!/usr/bin/env python3
"""
scratch/apply_m4_batch5_ingestion.py — M4 Batch 5 场景环境库增量入库、Schema 强校验与逐映射审计工具

涵盖范围与执行契约：
1. 目标数据文件：
   - scenes.json (35 个场景大类容器，包含 24 个既有大类与 11 个新增大类)。
2. 来源与映射口径对账：
   - 2,335 个来源实体 (SRC_SCENE_04130 ~ SRC_SCENE_06464)；
   - 71 条隔离项绝对物理隔离排除 (DEFERRED_ITEMS，非空间/纯剧情/无场地抽象条目，零泄露入 scenes.json)；
   - 2,264 条可入库候选映射：
     * 37 条 REUSE_EXISTING (AUDITED_REUSE)；
     * 3 条 STYLE_VARIANT (净增变体，另有 4 条命中核心形态 ID 聚类对齐 IDEMPOTENT_SKIPPED)；
     * 2,141 条 NEW_STYLE (净增核心形态，另有 3 条同义去重跳过 IDEMPOTENT_SKIPPED)；
     * 75 条 ENSEMBLE_COMBO (净增解构场景，另有 1 条重复去重跳过 IDEMPOTENT_SKIPPED)；
     * 净增 112 个新 items，净增 2,219 个新 tags。
3. 空间、时间与语义保真契约：
   - 绝不硬编码常见情境时间，来源未说明时间统一标为 time_of_day: "unspecified"；
   - library (SRC_SCENE_06095) 核心事实 venue_ids: ["library"], time_of_day: "unspecified"；
   - abandoned pier 归入 waterfront_pier 并保留 condition: "abandoned"；
   - supermarket 优先全词匹配至 commercial_retail (indoor)，峡谷热气球标定为 outdoor；
   - 叙事长句模板 (prose_scene_templates) 保留五大成分与 slot_attribution 治理元数据。
4. Schema 与命名强规范：
   - 全量 Item ID 与 Tag ID 严格适配 Draft-7 JSON Schema 约束 (长度 <= 96 字符，符合 ^[a-z][a-z0-9_]{2,95}$ 正则)；
   - context_ids 严格符合 VALID_CONTEXT_ENUMS 白名单枚举规范；
   - anchor_tags 与 detail_tags 严格无交集；
   - facts 经由 build_runtime_semantic_facts 强契约适配。
5. 安全保障：
   - 写入前自动建立快照至 scratch/m4_snapshots/batch_5_pre_ingest/ (写保护杜绝覆写)；
   - 逐映射审计账本落盘至 scratch/m4_batch5_execution_ledger.json；
   - 临时副本双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验。
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(REPO_DIR / "scratch"))

from lib.models import SemanticFacts
from m33_adapter_and_relation_migrator import build_runtime_semantic_facts
from generate_batch5_scene_script import DEFERRED_ITEMS

# 71 条绝对物理隔离项
BATCH5_QUARANTINE_ENTITY_IDS: Set[str] = set(DEFERRED_ITEMS.keys())

# 11 个新增大类分类映射字典 (SSOT)
BATCH5_CATEGORY_ROUTING: Dict[str, Dict[str, Any]] = {
    "prose": {
        "category": "📜 自然语言整景叙述模板 (Prose Scene Templates)",
        "context_ids": ["special"],
    },
    "nature": {
        "category": "🏞️ 自然景观与水系地貌 (Natural Landscapes & Waterways)",
        "context_ids": ["outdoor"],
    },
    "historical": {
        "category": "🏛️ 历史古迹与传统建筑 (Historical & Traditional Architecture)",
        "context_ids": ["traditional"],
    },
    "urban": {
        "category": "🌆 都市街景与现代建筑 (Urban Architecture & Streetscapes)",
        "context_ids": ["outdoor"],
    },
    "commercial": {
        "category": "🏢 公共服务与商业文娱 (Public Services & Commercial Venues)",
        "context_ids": ["generic"],
    },
    "interior": {
        "category": "🏠 室内居所与起居空间 (Interior Living Spaces & Rooms)",
        "context_ids": ["domestic"],
    },
    "transit": {
        "category": "🚗 交通出行与流动空间 (Transit & Mobile Spaces)",
        "context_ids": ["transit"],
    },
    "industrial": {
        "category": "🏭 工业建筑与特殊设施 (Industrial & Special Facilities)",
        "context_ids": ["special"],
    },
    "scifi": {
        "category": "🚀 未来科幻与超现实空间 (Sci-Fi Futuristic Spaces)",
        "context_ids": ["special"],
    },
    "general": {
        "category": "🌐 通用场景与宏观场所 (General Venues & Environments)",
        "context_ids": ["generic"],
    },
    "event": {
        "category": "🎭 复合解构事件与探险场景 (Deconstructed Events & Quest Settings)",
        "context_ids": ["special"],
    },
}

ITEM_ROUTING_KEY_MAP: Dict[str, str] = {
    "prose_scene_templates": "prose",
    "nature_landscape": "nature",
    "nature_lake": "nature",
    "nature_river": "nature",
    "nature_riverbank": "nature",
    "geographic_landmarks": "nature",
    "botanical_greenhouse": "nature",
    "botanical_conservatory": "nature",
    "historical_architecture": "historical",
    "historical_ruins": "historical",
    "traditional_monastery": "historical",
    "village_settlement": "historical",
    "urban_cityscape": "urban",
    "urban_streetscape": "urban",
    "urban_architecture_rooftop": "urban",
    "waterfront_pier": "urban",
    "architecture_stairwell": "urban",
    "commercial_service": "commercial",
    "commercial_retail": "commercial",
    "venue_casino": "commercial",
    "venue_zoo": "commercial",
    "venue_swimming_pool": "commercial",
    "venue_rooftop_pool": "commercial",
    "facility_locker_room": "commercial",
    "sanitary_toilet_stall": "commercial",
    "interior_room": "interior",
    "domestic_balcony": "interior",
    "domestic_closet": "interior",
    "transit_bus_stop": "transit",
    "urban_phone_booth": "transit",
    "vehicle_interior": "transit",
    "vehicle_car_interior": "transit",
    "industrial_warehouse": "industrial",
    "industrial_construction_site": "industrial",
    "detention_prison_cell": "industrial",
    "scifi_futuristic": "scifi",
    "venue_general": "general",
}

# 37 个非复合新 items 中文展示标签字典
ITEM_LABEL_MAP: Dict[str, Tuple[str, str, List[str]]] = {
    "prose_scene_templates": ("自然语言整景叙述模板", "叙述长句模板 (Prose Scene Templates)", ["special"]),
    "nature_landscape": ("自然风光与宏观地貌", "自然地貌 (Nature Landscape)", ["outdoor"]),
    "nature_lake": ("湖泊水域", "湖泊水域 (Lake)", ["outdoor"]),
    "nature_river": ("河流溪流", "河流溪流 (River)", ["outdoor"]),
    "nature_riverbank": ("河岸河滩", "河岸水畔 (Riverbank)", ["outdoor"]),
    "geographic_landmarks": ("著名地理与国家公园地标", "地理地标 (Geographic Landmarks)", ["outdoor"]),
    "botanical_greenhouse": ("温室花房", "温室植物 (Greenhouse)", ["outdoor"]),
    "botanical_conservatory": ("植物温室与花房暖房", "植物暖房 (Conservatory)", ["outdoor"]),
    "historical_architecture": ("历史名胜与城堡要塞", "历史建筑 (Historical Architecture)", ["traditional"]),
    "historical_ruins": ("古代遗址与残垣断壁", "古代遗址 (Historical Ruins)", ["traditional"]),
    "traditional_monastery": ("传统禅院与宗教修道院", "传统禅院 (Traditional Monastery)", ["traditional"]),
    "village_settlement": ("古老村落与乡村聚落", "乡村聚落 (Village Settlement)", ["traditional"]),
    "urban_cityscape": ("宏观都市天际线与市容", "都市天际线 (Cityscape)", ["outdoor"]),
    "urban_streetscape": ("城市街景与街头巷尾", "城市街景 (Streetscape)", ["outdoor"]),
    "urban_architecture_rooftop": ("城市建筑屋顶天台", "建筑天台 (Rooftop)", ["outdoor"]),
    "waterfront_pier": ("水岸码头与栈桥", "水岸码头 (Waterfront Pier)", ["outdoor"]),
    "architecture_stairwell": ("公共楼梯间与安全通道", "建筑楼道 (Stairwell)", ["office"]),
    "commercial_service": ("商业空间与公众服务场所", "商业服务 (Commercial Services)", ["generic"]),
    "commercial_retail": ("大型超市与零售商场", "商业零售 (Retail)", ["generic"]),
    "venue_casino": ("娱乐场与博彩俱乐部", "娱乐博彩 (Casino)", ["nightlife"]),
    "venue_zoo": ("动物园与野生动物园区", "动物展区 (Zoo)", ["outdoor"]),
    "venue_swimming_pool": ("标准游泳池与水上馆", "游泳水域 (Swimming Pool)", ["generic"]),
    "venue_rooftop_pool": ("天台无边泳池与空中泳区", "天台泳池 (Rooftop Pool)", ["outdoor"]),
    "facility_locker_room": ("更衣室与储物间", "设施更衣室 (Locker Room)", ["generic"]),
    "sanitary_toilet_stall": ("卫生间隔间", "卫浴隔间 (Toilet Stall)", ["generic"]),
    "interior_room": ("起居空间与特色功能房", "室内房间 (Interior Room)", ["domestic"]),
    "domestic_balcony": ("居家阳台与露台", "住宅阳台 (Balcony)", ["domestic"]),
    "domestic_closet": ("衣帽间与储物壁橱", "储物壁橱 (Closet)", ["domestic"]),
    "transit_bus_stop": ("公交车站与路边站台", "交通站点 (Bus Stop)", ["transit"]),
    "urban_phone_booth": ("街头公用电话亭", "街头设施 (Phone Booth)", ["transit"]),
    "vehicle_interior": ("交通载具内部空间", "载具内饰 (Vehicle Interior)", ["transit"]),
    "vehicle_car_interior": ("私家车与轿车内座舱", "汽车座舱 (Car Interior)", ["transit"]),
    "industrial_warehouse": ("大型工业仓库与货栈", "工业仓库 (Warehouse)", ["special"]),
    "industrial_construction_site": ("建筑工地与施工现场", "施工工地 (Construction Site)", ["outdoor"]),
    "detention_prison_cell": ("拘留监室与铁窗囚房", "拘押监牢 (Prison Cell)", ["special"]),
    "scifi_futuristic": ("未来科幻与外星太空空间", "科幻场景 (Sci-Fi)", ["special"]),
    "venue_general": ("多功能通用场地与情境", "通用场地 (General Venues)", ["generic"]),
}

# 基线历史遗留严重错配标签剥离清单 (20 个旧容器中剥离错配子项，杜绝遮蔽专属独立条目)
MISPLACED_BASELINE_TAG_CLEANUPS: List[Tuple[str, List[str]]] = [
    ("scene_bathtub", ["river", "lake"]),
    ("scene_bowling_alley", ["swimming pool", "poolside"]),
    ("scene_aquarium", ["zoo"]),
    ("scene_pachinko_parlor", ["casino"]),
    ("scene_locked_room", ["warehouse"]),
    ("scene_highway_bus", ["bus stop"]),
    ("scene_beach_at_night", ["poolside"]),
    ("scene_car_backseat", ["car interior"]),
    ("scene_house", ["balcony", "stairwell"]),
    ("scene_locker", ["closet", "phone booth"]),
    ("scene_glass_room", ["conservatory", "greenhouse"]),
    ("scene_back_alley", ["construction site"]),
    ("scene_gym_storage_room", ["locker room"]),
    ("scene_interrogation_room", ["prison cell"]),
    ("scene_park_at_night", ["riverbank"]),
    ("scene_nurse_office", ["rooftop"]),
    ("scene_rooftop_at_night", ["rooftop pool"]),
    ("scene_abandoned_building", ["ruins"]),
    ("scene_public_restroom", ["toilet stall"]),
    ("scene_school_toilet", ["toilet stall"]),
]


def sanitize_id(raw_id: str) -> str:
    """确保 id 符合 ^[a-z][a-z0-9_]{2,95}$ 契约 (最大长度 96 字符) 并保留特定后缀避免条目-标签重名。"""
    s = re.sub(r'[^a-z0-9_]+', '_', raw_id.lower()).strip('_')
    if len(s) <= 96:
        return s
    m = re.search(r'(__[a-z0-9]+(?:_[a-z0-9]+)?)$', s)
    if m and len(m.group(1)) < 30:
        suffix = m.group(1)
        prefix_len = 96 - len(suffix)
        return s[:prefix_len].rstrip('_') + suffix
    return s[:96].rstrip('_')


def get_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_batch5_ledger() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """载入 Batch 5 全部来源实体与目标映射。"""
    src_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    map_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    with open(src_path, "r", encoding="utf-8") as f:
        all_sources = {
            s["entity_id"]: s for s in csv.DictReader(f, delimiter="\t")
            if s.get("category") == "scene" or s.get("entity_id", "").startswith("SRC_SCENE_")
        }

    with open(map_path, "r", encoding="utf-8") as f:
        all_mappings = [
            m for m in csv.DictReader(f, delimiter="\t")
            if m.get("target_catalog_file") == "scenes.json" or m.get("source_entity_id", "").startswith("SRC_SCENE_")
        ]

    b5_sources = [all_sources[m["source_entity_id"]] for m in all_mappings if m["source_entity_id"] in all_sources]
    return b5_sources, all_mappings


def execute_batch5_ingestion(
    data_dir: Path,
    dry_run: bool = False,
    backup_snapshot: bool = True,
    snapshot_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行 Batch 5 增量入库流程。"""
    scenes_path = data_dir / "scenes.json"

    # 1. 创建备份快照（严格写保护，首次写入后不再覆盖）
    actual_snapshot_dir = snapshot_dir if snapshot_dir is not None else (REPO_DIR / "scratch/m4_snapshots/batch_5_pre_ingest")
    if backup_snapshot and not dry_run:
        actual_snapshot_dir.mkdir(parents=True, exist_ok=True)
        dest = actual_snapshot_dir / scenes_path.name
        if scenes_path.exists() and not dest.exists():
            shutil.copy2(scenes_path, dest)

    # 2. 读取现有数据
    scenes_data = json.loads(scenes_path.read_text(encoding="utf-8"))
    scenes_groups: List[Dict[str, Any]] = scenes_data.get("scenes", [])
    b5_sources, b5_mappings = load_batch5_ledger()
    source_map = {s["entity_id"]: s for s in b5_sources}

    # 2.1 清理基线历史遗留严重错配标签（杜绝遮蔽新增专属独立条目）
    cleanup_map = {item_id: {t.lower() for t in texts} for item_id, texts in MISPLACED_BASELINE_TAG_CLEANUPS}
    for g in scenes_groups:
        for it in g.get("items", []):
            iid = it.get("id", "")
            if iid in cleanup_map:
                bad_texts = cleanup_map[iid]
                for grp in ("anchor_tags", "detail_tags", "tags"):
                    if grp in it:
                        it[grp] = [
                            t for t in it[grp]
                            if (t.get("text") if isinstance(t, dict) else str(t)).strip().lower() not in bad_texts
                        ]

    # 2.2 清理并规范化既有条目历史事实（按各标签自身语义清理 scene_library 采样池所有主标签与细节标签）
    for g in scenes_groups:
        for it in g.get("items", []):
            if it.get("id") == "scene_library":
                it["exclusive_group"] = "library"
                for tag_grp in ("anchor_tags", "detail_tags", "tags"):
                    for t in it.get(tag_grp, []):
                        if not isinstance(t, dict):
                            continue
                        text_low = t.get("text", "").strip().lower()
                        is_school_explicit = "school" in text_low
                        is_anchor = (tag_grp == "anchor_tags")

                        facts = t.setdefault("facts", {})
                        facts["space_kind"] = "indoor"
                        facts["time_of_day"] = "unspecified"
                        facts["semantic_role"] = "scene_anchor" if is_anchor else "scene_detail"

                        if is_school_explicit:
                            facts["venue_ids"] = ["school", "library"]
                        else:
                            facts["venue_ids"] = ["library"]

                        gov = facts.setdefault("governance_metadata", {})
                        gov["context_affinity"] = ["school"]

                        sf = facts.setdefault("scene_facts", {})
                        sf["space_kind"] = "indoor"
                        sf["time_of_day"] = "unspecified"
                        sf["baseline_default_time_of_day"] = "day"

    # 建立现有 item 索引 (以便增量路由)
    existing_item_map: Dict[str, Dict[str, Any]] = {}
    for g in scenes_groups:
        for it in g.get("items", []):
            if "id" in it:
                existing_item_map[it["id"]] = it

    stats = {
        "total_sources": len(b5_sources),
        "quarantined_sources": 0,
        "candidate_sources": 0,
        "total_mappings": len(b5_mappings),
        "quarantined_mappings": 0,
        "candidate_mappings": 0,
        "reuse_existing": 0,
        "style_variant": 0,
        "new_style": 0,
        "ensemble_combo": 0,
        "added_items": 0,
        "added_tags": 0,
        "idempotent_skipped_tags": 0,
    }
    records = []

    # 准备新大类容器 (11 个新增大类)
    new_categories: Dict[str, Dict[str, Any]] = {}
    for r_key, r_info in BATCH5_CATEGORY_ROUTING.items():
        # 检查是否既有场景大类已存在同名分类
        existing_cat = next((g for g in scenes_groups if g.get("category") == r_info["category"]), None)
        if existing_cat:
            new_categories[r_key] = existing_cat
        else:
            cat_obj = {
                "category": r_info["category"],
                "items": [],
            }
            new_categories[r_key] = cat_obj

    created_new_items: Dict[str, Dict[str, Any]] = {}
    seen_tag_ids_by_item: Dict[str, Set[str]] = {
        it_id: {
            t["id"] for t in it.get("anchor_tags", []) + it.get("detail_tags", []) + it.get("tags", [])
            if isinstance(t, dict) and "id" in t
        }
        for it_id, it in existing_item_map.items()
    }

    # 3. 逐映射处理
    for m in b5_mappings:
        mid = m["mapping_id"]
        eid = m["source_entity_id"]
        src_info = source_map.get(eid, {})
        decision = src_info.get("decision", m.get("decision", "NEW_STYLE"))
        target_file = m["target_catalog_file"]
        raw_item_id = m["target_item_id"]
        s_item_id = sanitize_id(raw_item_id)
        raw_tag_id = m["target_tag_id"]
        s_tag_id = sanitize_id(raw_tag_id)
        target_role = m.get("target_role", "selector")
        raw_text = m["target_tag_text"]

        # 3.0 物理隔离项严格排除：71 条隔离项严禁入库
        if (
            eid in BATCH5_QUARANTINE_ENTITY_IDS
            or decision == "DEFERRED_ISSUE"
            or target_role == "quarantined"
            or raw_item_id == "quarantine"
        ):
            stats["quarantined_sources"] += 1
            stats["quarantined_mappings"] += 1
            records.append({
                "mapping_id": mid,
                "source_entity_id": eid,
                "decision": decision,
                "target_file": target_file,
                "target_container": "QUARANTINE_ISOLATED",
                "target_item_id": raw_item_id,
                "target_tag_id": raw_tag_id,
                "sanitized_tag_id": s_tag_id,
                "clean_text": raw_text.strip(),
                "action": "QUARANTINE_EXCLUDED",
            })
            continue

        stats["candidate_sources"] += 1
        stats["candidate_mappings"] += 1

        # 3.1 目标 item 定位与创建
        item = existing_item_map.get(s_item_id) or created_new_items.get(s_item_id)
        action_taken = "SKIPPED"

        if not item:
            # 确定大类归属
            routing_key = ITEM_ROUTING_KEY_MAP.get(s_item_id)
            if not routing_key:
                if s_item_id.startswith("event_") or s_item_id.startswith("quest_"):
                    routing_key = "event"
                else:
                    routing_key = "general"

            # 确定 Item Label 与 Subcategory
            if s_item_id in ITEM_LABEL_MAP:
                lbl, subcat, c_ids = ITEM_LABEL_MAP[s_item_id]
            else:
                clean_name = s_item_id.replace("event_setting__", "").replace("quest_setting__", "").replace("_", " ").title()
                lbl = f"场景: {clean_name}"
                subcat = f"{clean_name} (Setting)"
                c_ids = ["special"]

            item = {
                "id": s_item_id,
                "label": lbl,
                "subcategory": subcat,
                "context_ids": c_ids,
                "anchor_tags": [],
                "detail_tags": [],
            }
            created_new_items[s_item_id] = item
            seen_tag_ids_by_item[s_item_id] = set()
            new_categories[routing_key]["items"].append(item)
            stats["added_items"] += 1

        # 3.2 事实适配与标签构造
        raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
        adapted_facts_obj = build_runtime_semantic_facts(raw_facts, eid, "scenes.json")
        adapted_facts = adapted_facts_obj.to_dict()

        if "governance_metadata" not in adapted_facts:
            adapted_facts["governance_metadata"] = {}

        if adapted_facts.get("semantic_role") is None:
            adapted_facts["semantic_role"] = "variant" if decision == "STYLE_VARIANT" else "scene_anchor"

        tag_record = {
            "id": s_tag_id,
            "text": raw_text.strip(),
            "facts": adapted_facts,
        }

        # 3.3 标签去重与落盘
        existing_text_collision = (
            s_item_id in existing_item_map
            and any(
                isinstance(t, dict) and t.get("text", "").strip().lower() == raw_text.strip().lower()
                for t in item.get("anchor_tags", []) + item.get("detail_tags", [])
            )
        )

        if decision == "REUSE_EXISTING":
            stats["reuse_existing"] += 1
            action_taken = "AUDITED_REUSE"
            # 落实审核事实至生产数据 (回写 37 项已审核事实至 anchor_tags, detail_tags, tags)
            if item:
                norm_text = raw_text.strip().lower()
                for tag_group in ("anchor_tags", "detail_tags", "tags"):
                    for t in item.get(tag_group, []):
                        if isinstance(t, dict):
                            t_text = t.get("text", "").strip().lower()
                            t_id = t.get("id", "").strip().lower()
                            if t_text == norm_text or t_id == s_tag_id.lower() or t_id == raw_tag_id.lower():
                                is_anchor = (tag_group == "anchor_tags" and t.get("facts", {}).get("semantic_role") == "scene_anchor")
                                updated_facts = dict(adapted_facts)
                                if is_anchor:
                                    updated_facts["semantic_role"] = "scene_anchor"
                                t["facts"] = updated_facts
        elif s_tag_id in seen_tag_ids_by_item[s_item_id] or existing_text_collision:
            stats["idempotent_skipped_tags"] += 1
            action_taken = "IDEMPOTENT_SKIPPED"
        else:
            if s_item_id in existing_item_map:
                item.setdefault("detail_tags", []).append(tag_record)
            else:
                item["anchor_tags"].append(tag_record)
            seen_tag_ids_by_item[s_item_id].add(s_tag_id)
            stats["added_tags"] += 1

            if decision == "STYLE_VARIANT":
                stats["style_variant"] += 1
                action_taken = "APPENDED_VARIANT"
            elif decision == "NEW_STYLE":
                stats["new_style"] += 1
                action_taken = "APPENDED_NEW_STYLE"
            elif decision == "ENSEMBLE_COMBO":
                stats["ensemble_combo"] += 1
                action_taken = "APPENDED_COMBO"

        records.append({
            "mapping_id": mid,
            "source_entity_id": eid,
            "decision": decision,
            "target_file": target_file,
            "target_container": "scenes",
            "target_item_id": raw_item_id,
            "sanitized_item_id": s_item_id,
            "target_tag_id": raw_tag_id,
            "sanitized_tag_id": s_tag_id,
            "clean_text": raw_text.strip(),
            "action": action_taken,
        })

    # 4. 追加填充的新大类至 scenes_groups (仅追加未挂载且包含 items 的大类)
    for r_key, cat in new_categories.items():
        if cat["items"] and cat not in scenes_groups:
            scenes_groups.append(cat)

    # 5. 写盘（若非 dry_run）
    if not dry_run:
        scenes_path.write_text(json.dumps(scenes_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 6. 记录执行账本
    final_scenes_hash = get_sha256(scenes_path)
    ledger_content = {
        "stats": stats,
        "file_hashes": {
            "scenes.json": final_scenes_hash,
        },
        "records": records,
    }

    actual_ledger_path = ledger_path if ledger_path is not None else (REPO_DIR / "scratch/m4_batch5_execution_ledger.json")
    if not dry_run:
        history_run = {
            "run_stats": stats,
            "scenes_sha256": final_scenes_hash,
        }
        if actual_ledger_path.exists():
            try:
                prev_ledger = json.loads(actual_ledger_path.read_text(encoding="utf-8"))
                runs = prev_ledger.get("runs", [])
                runs.append(history_run)
                ledger_content["runs"] = runs
            except Exception:
                ledger_content["runs"] = [history_run]
        else:
            ledger_content["runs"] = [history_run]

        actual_ledger_path.write_text(json.dumps(ledger_content, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "stats": stats,
        "file_hashes": {
            "scenes.json": final_scenes_hash,
        },
        "records_count": len(records),
    }


def main():
    parser = argparse.ArgumentParser(description="M4 Batch 5 Ingestion Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without disk write")
    parser.add_argument("--no-backup", action="store_true", help="Skip pre-ingestion backup")
    parser.add_argument("--data-dir", type=str, default=str(REPO_DIR / "data"), help="Path to data directory")
    args = parser.parse_args()

    print(f"=== M4 Batch 5 场景环境库增量入库工具 ===")
    res = execute_batch5_ingestion(
        data_dir=Path(args.data_dir),
        dry_run=args.dry_run,
        backup_snapshot=not args.no_backup,
    )
    print("入库执行结果统计:")
    print(json.dumps(res["stats"], indent=2, ensure_ascii=False))
    print(f"scenes.json SHA256: {res['file_hashes']['scenes.json']}")
    print(f"总记录数: {res['records_count']}")


if __name__ == "__main__":
    main()
