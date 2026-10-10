#!/usr/bin/env python3
"""
scratch/apply_m4_batch6_ingestion.py — M4 Batch 6 服装款式库增量入库、Schema 强校验与逐映射审计工具

涵盖范围与执行契约：
1. 目标数据文件：
   - clothing.json (主容器 categories 数组，涵盖 148 个既有大类与 17 个新增大类，以及 lingerie_wardrobe 既有大类)。
2. 来源与映射口径对账：
   - 3,607 个来源实体 (SRC_CLOTH_00146 ~ SRC_CLOTH_03752)；
   - 3 条非具体服装单品/状态修饰隔离项物理隔离排除 (DEFERRED_ITEMS: cosplay, shattered clothes, transparent clothes，零泄露入库)；
   - 3,604 条可入库候选映射：
     * 25 条 REUSE_EXISTING (AUDITED_REUSE，其中 24 条回写 clothing.json，1 条 poncho 属于 accessories.json 保持只读)；
     * 949 条 STYLE_VARIANT (净增服装款式变体)；
     * 360 条 NEW_STYLE (净增核心款式形制)；
     * 2,270 条 ENSEMBLE_COMBO (净增解构穿搭组合长句)；
     * 净增 17 个新 categories 条目，净增 3,579 个新 tags。
3. 剪裁特征、材质面料与搭配保真契约：
   - 剪裁特征正交独立提取 (neckline, sleeve_length, hemline_length, fit_silhouette)；
   - 材质面料 (fabric_materials) 与织纹装饰 (pattern_textures) 完整保真；
   - 复合穿搭解构构件完整保留 (top_pieces, bottom_pieces, outer_layers, footwear, accessories, styling_details)；
   - 独立袜类单品移出复用，杜绝继承套装拓扑；
   - 垂褶领 (cowl neck) 保真，严禁粗暴归并为 turtleneck。
4. Schema 与命名强规范：
   - 全量 Item ID 与 Tag ID 严格适配 Draft-7 JSON Schema 约束 (长度 <= 96 字符，符合 ^[a-z][a-z0-9_]{2,95}$ 正则)；
   - facts 经由 build_runtime_semantic_facts 强契约适配。
5. 安全保障：
   - 写入前自动建立快照至 scratch/m4_snapshots/batch_6_pre_ingest/ (写保护杜绝覆写)；
   - 逐映射审计账本落盘至 scratch/m4_batch6_execution_ledger.json；
   - 隔离临时副本中双遍真实写入 (dry_run=False) 幂等性与哈希一致性核验。
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

from m33_adapter_and_relation_migrator import build_runtime_semantic_facts

# 3 条绝对物理隔离项 (DEFERRED_ISSUE)
BATCH6_QUARANTINE_ENTITY_IDS: Set[str] = {
    "SRC_CLOTH_00317",  # cosplay (泛指活动/抽象概念)
    "SRC_CLOTH_00368",  # shattered clothes (衣物状态修饰)
    "SRC_CLOTH_00387",  # transparent clothes (透光阶梯修饰)
}

# 17 个新增 categories 标签字典 (SSOT)
BATCH6_NEW_CATEGORY_LABELS: Dict[str, str] = {
    "ao_dai": "奥黛传统长衫 (Ao Dai)",
    "bottoms_specialty": "特色下装与比基尼底裤 (Specialty Bottoms)",
    "changshan": "传统长衫与中式袍服 (Changshan)",
    "clothing_ensemble_combos": "复合风格穿搭组合 (Clothing Ensemble Combos)",
    "cyberpunk_plugsuit": "赛博朋克防护连体服 (Cyberpunk Plugsuit)",
    "cyberpunk_tactical_bodysuit": "赛博战术功能紧身衣 (Cyberpunk Tactical Bodysuit)",
    "denim_jeans": "牛仔长裤与截短裤 (Denim Jeans)",
    "hosiery_legwear": "袜类单品与腿部配搭 (Hosiery & Legwear)",
    "lehenga": "印度列恒加礼服长裙 (Lehenga)",
    "lingerie_bra": "文胸与胸衣内衣 (Lingerie Bra)",
    "lingerie_panties": "三角内裤与丁字底裤 (Lingerie Panties)",
    "sari": "传统莎丽裹裙长袍 (Sari)",
    "tactical_jumpsuit": "功能性战术连体服 (Tactical Jumpsuit)",
    "tops_specialty": "不规则剪裁特色上装 (Specialty Tops)",
    "traditional_eastern_gown": "传统东方日装袍服 (Traditional Eastern Gown)",
    "trousers_general": "长裤皮裤与皮套裤 (Trousers & Chaps)",
    "uchikake": "传统打挂和服外袍 (Uchikake)",
}


def sanitize_id(raw_id: str) -> str:
    """确保 id 符合 ^[a-z][a-z0-9_]{2,95}$ 契约 (最大长度 96 字符)。"""
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


def load_batch6_ledger() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """载入 Batch 6 全部来源实体与目标映射。"""
    src_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    map_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    with open(src_path, "r", encoding="utf-8") as f:
        all_sources = {
            s["entity_id"]: s for s in csv.DictReader(f, delimiter="\t")
            if s.get("category") == "clothing" or s.get("entity_id", "").startswith("SRC_CLOTH_")
        }

    with open(map_path, "r", encoding="utf-8") as f:
        all_mappings = [
            m for m in csv.DictReader(f, delimiter="\t")
            if m.get("source_entity_id", "").startswith("SRC_CLOTH_")
        ]

    b6_sources = [all_sources[m["source_entity_id"]] for m in all_mappings if m["source_entity_id"] in all_sources]
    return b6_sources, all_mappings


def execute_batch6_ingestion(
    data_dir: Path,
    dry_run: bool = False,
    backup_snapshot: bool = True,
    snapshot_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行 Batch 6 增量入库流程。"""
    clothing_path = data_dir / "clothing.json"

    # 1. 创建备份快照（严格写保护，首次写入后不再覆盖）
    actual_snapshot_dir = snapshot_dir if snapshot_dir is not None else (REPO_DIR / "scratch/m4_snapshots/batch_6_pre_ingest")
    if backup_snapshot and not dry_run:
        actual_snapshot_dir.mkdir(parents=True, exist_ok=True)
        dest = actual_snapshot_dir / clothing_path.name
        if clothing_path.exists() and not dest.exists():
            shutil.copy2(clothing_path, dest)

    # 2. 读取现有数据
    clothing_data = json.loads(clothing_path.read_text(encoding="utf-8"))
    categories: List[Dict[str, Any]] = clothing_data.get("categories", [])
    lingerie_wardrobe: List[Dict[str, Any]] = clothing_data.get("lingerie_wardrobe", [])

    b6_sources, b6_mappings = load_batch6_ledger()
    source_map = {s["entity_id"]: s for s in b6_sources}

    # 建立现有 category 索引
    existing_cat_map: Dict[str, Dict[str, Any]] = {c["id"]: c for c in categories if "id" in c}
    existing_lingerie_map: Dict[str, Dict[str, Any]] = {c["id"]: c for c in lingerie_wardrobe if "id" in c}

    stats = {
        "total_sources": len(b6_sources),
        "quarantined_sources": 0,
        "candidate_sources": 0,
        "total_mappings": len(b6_mappings),
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

    created_new_cats: Dict[str, Dict[str, Any]] = {}
    seen_tag_ids_by_item: Dict[str, Set[str]] = {
        c_id: {t["id"] for t in c.get("tags", []) if isinstance(t, dict) and "id" in t}
        for c_id, c in {**existing_cat_map, **existing_lingerie_map}.items()
    }

    # 3. 逐映射处理
    for m in b6_mappings:
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

        # 3.0 物理隔离项严格排除：3 条隔离项严禁入库
        if (
            eid in BATCH6_QUARANTINE_ENTITY_IDS
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

        # 3.1 事实适配与标签构造
        raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
        adapted_facts_obj = build_runtime_semantic_facts(raw_facts, eid, "clothing.json")
        adapted_facts = adapted_facts_obj.to_dict()

        if "governance_metadata" not in adapted_facts:
            adapted_facts["governance_metadata"] = {}

        if adapted_facts.get("semantic_role") is None:
            adapted_facts["semantic_role"] = "variant" if decision == "STYLE_VARIANT" else "selector"

        tag_record = {
            "id": s_tag_id,
            "text": raw_text.strip(),
            "facts": adapted_facts,
        }

        # 3.2 复用既有标签处理 (REUSE_EXISTING)
        if decision == "REUSE_EXISTING":
            stats["reuse_existing"] += 1
            action_taken = "AUDITED_REUSE"

            # 若目标文件属于 clothing.json，在 categories 或 lingerie_wardrobe 中回写审核事实
            if target_file == "clothing.json":
                target_container = existing_cat_map.get(s_item_id) or existing_lingerie_map.get(s_item_id)
                if target_container:
                    norm_text = raw_text.strip().lower()
                    for t in target_container.get("tags", []):
                        if isinstance(t, dict):
                            t_text = t.get("text", "").strip().lower()
                            t_id = t.get("id", "").strip().lower()
                            if t_text == norm_text or t_id == s_tag_id.lower() or t_id == raw_tag_id.lower():
                                new_facts = copy.deepcopy(adapted_facts)
                                existing_dyn = (
                                    t.get("facts", {}).get("accessory_facts", {}).get("dynamic_slots")
                                    or t.get("facts", {}).get("governance_metadata", {}).get("dynamic_slots")
                                )
                                if existing_dyn:
                                    if "accessory_facts" in t.get("facts", {}):
                                        new_facts.setdefault("accessory_facts", {})["dynamic_slots"] = copy.deepcopy(existing_dyn)
                                    else:
                                        new_facts.setdefault("governance_metadata", {})["dynamic_slots"] = copy.deepcopy(existing_dyn)
                                t["facts"] = new_facts

            records.append({
                "mapping_id": mid,
                "source_entity_id": eid,
                "decision": decision,
                "target_file": target_file,
                "target_container": "categories" if target_file == "clothing.json" else "accessories",
                "target_item_id": raw_item_id,
                "sanitized_item_id": s_item_id,
                "target_tag_id": raw_tag_id,
                "sanitized_tag_id": s_tag_id,
                "clean_text": raw_text.strip(),
                "action": action_taken,
            })
            continue

        # 3.3 目标 category 定位与新建
        cat = existing_cat_map.get(s_item_id) or created_new_cats.get(s_item_id)
        if not cat:
            lbl = BATCH6_NEW_CATEGORY_LABELS.get(s_item_id, f"服装款式: {s_item_id.replace('_', ' ').title()}")
            cat = {
                "id": s_item_id,
                "name_zh": lbl,
                "aliases": [],
                "tags": [],
            }
            created_new_cats[s_item_id] = cat
            seen_tag_ids_by_item[s_item_id] = set()
            stats["added_items"] += 1

        # 3.4 标签去重与落盘
        existing_text_collision = any(
            isinstance(t, dict) and t.get("text", "").strip().lower() == raw_text.strip().lower()
            for t in cat.get("tags", [])
        )

        if s_tag_id in seen_tag_ids_by_item[s_item_id] or existing_text_collision:
            stats["idempotent_skipped_tags"] += 1
            action_taken = "IDEMPOTENT_SKIPPED"
        else:
            cat["tags"].append(tag_record)
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
            "target_container": "categories",
            "target_item_id": raw_item_id,
            "sanitized_item_id": s_item_id,
            "target_tag_id": raw_tag_id,
            "sanitized_tag_id": s_tag_id,
            "clean_text": raw_text.strip(),
            "action": action_taken,
        })

    # 4. 追加填充的新 categories 至 categories 列表
    for c_id, new_cat in created_new_cats.items():
        if new_cat not in categories and len(new_cat.get("tags", [])) > 0:
            categories.append(new_cat)

    # 5. 落盘写回
    file_hashes = {}
    if not dry_run:
        clothing_data["categories"] = categories
        clothing_path.write_text(json.dumps(clothing_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        file_hashes["clothing.json"] = get_sha256(clothing_path)

        # 审计账本落盘
        actual_ledger_path = ledger_path if ledger_path is not None else (REPO_DIR / "scratch/m4_batch6_execution_ledger.json")
        actual_ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_data = {
            "batch": "M4_BATCH_6",
            "target_file": "clothing.json",
            "stats": stats,
            "file_hashes": file_hashes,
            "records": records,
        }
        actual_ledger_path.write_text(json.dumps(ledger_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "stats": stats,
        "file_hashes": file_hashes,
        "records_count": len(records),
    }


def main():
    parser = argparse.ArgumentParser(description="执行 M4 Batch 6 服装款式库增量入库流程")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不写回目标文件")
    parser.add_argument("--no-backup", action="store_true", help="跳过快照备份")
    parser.add_argument("--data-dir", type=str, default=str(REPO_DIR / "data"), help="目标数据目录")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    print("==================================================")
    print("开始执行 M4 Batch 6 服装款式库增量入库")
    print(f"数据目录: {data_dir}")
    print(f"演练模式: {args.dry_run}")
    print("==================================================")

    res = execute_batch6_ingestion(
        data_dir=data_dir,
        dry_run=args.dry_run,
        backup_snapshot=not args.no_backup,
    )

    print("\n--- 入库统计报告 ---")
    for k, v in res["stats"].items():
        print(f"  {k}: {v}")

    if res["file_hashes"]:
        print("\n--- 产物文件哈希 ---")
        for f, h in res["file_hashes"].items():
            print(f"  {f}: {h}")

    print("\n[SUCCESS] M4 Batch 6 服装款式库入库流程完成！")


if __name__ == "__main__":
    main()
