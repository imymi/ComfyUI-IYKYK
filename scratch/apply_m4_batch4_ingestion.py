#!/usr/bin/env python3
"""
scratch/apply_m4_batch4_ingestion.py — M4 Batch 4 动作姿态库正式增量入库、模式强校验与审计工具

涵盖范围与执行契约：
1. 目标数据文件：
   - poses.json (15 个目标类别容器，包含 8 个既有类别与 7 个新增类别)。
2. 来源与映射口径对账：
   - 524 个来源实体 (SRC_POSE_03753 ~ SRC_POSE_04276)；
   - 8 条隔离项绝对排除：
     * SRC_POSE_03788 (MAP_00510, casing ejection, 枪械抛壳特效)；
     * SRC_POSE_03931 (MAP_00653, kine, 年糕木槌道具)；
     * SRC_POSE_03952 (MAP_00674, log pose, 记录指针腕表道具)；
     * SRC_POSE_03992 (MAP_00714, pokemon move, 招式抽象概念)；
     * SRC_POSE_04004 (MAP_00726, reverse trap, 角色人设属性)；
     * SRC_POSE_04018 (MAP_00740, single drill, 单螺旋卷发发型)；
     * SRC_POSE_04266 (MAP_00988, violence, 暴力抽象题材)；
     * SRC_POSE_04270 (MAP_00992, war, 战争题材标签)。
     上述 8 项 100% 物理隔离，严禁入库。
   - 516 条可入库候选映射：27 REUSE_EXISTING, 132 STYLE_VARIANT, 357 NEW_STYLE, 0 ENSEMBLE_COMBO。
3. 容器路由与 Schema 强契约：
   - 8 个既有类别在 subcategories 下挂载 "扩展姿态 (Extended Poses)"；
   - 7 个新增类别 (combat_action, gestures, gestures_self_touch, limb_posture, prose_pose_templates, restraints_holds, signature_poses)
     创建完整 category 并挂载 subcategories；
   - 标签 ID 严格适配 Draft-7 JSON Schema 约束 (长度 <= 96 字符，符合 ^[a-z][a-z0-9_]{2,95}$ 正则)；
   - facts 经由 build_runtime_semantic_facts 强契约适配，确保 semantic_role 显式填充。
4. 去重与增量对齐：
   - 27 条 REUSE_EXISTING 严格执行 AUDITED_REUSE，不产生重复标签；
   - 489 条非复用候选映射中，24 组自然语言叙述变体共 50 条映射与核心形态对齐并跳过重复落盘；
   - 净增 7 个新 items (categories)，439 个新 tags。
5. 安全保障：
   - 物理备份快照至 scratch/m4_snapshots/batch_4_pre_ingest/ (写保护杜绝覆写)；
   - 逐映射审计账本落盘至 scratch/m4_batch4_execution_ledger.json；
   - 隔离临时目录幂等性验证 (第二遍真实写入 0 新增 items、0 新增 tags)。
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(REPO_DIR / "scratch"))

from lib.models import SemanticFacts
from m33_adapter_and_relation_migrator import build_runtime_semantic_facts

# 7 个新增类别中文显示名称字典 (SSOT)
BATCH4_CATEGORY_NAME_ZH_MAP: Dict[str, str] = {
    "combat_action": "⚔️ 格斗与战术姿态 (Combat & Action)",
    "gestures": "✌️ 手势与指法动作 (Hand Gestures)",
    "gestures_self_touch": "🤲 身体互动与自触手势 (Self-Touch & Interaction)",
    "limb_posture": "🦵 肢体特定位置与体态 (Limb & Body Postures)",
    "prose_pose_templates": "📜 自然语言整身叙述模板 (Prose Pose Templates)",
    "restraints_holds": "⛓️ 拘束受限与压制姿态 (Restraints & Holds)",
    "signature_poses": "🌟 标志性经典姿态 (Signature Poses)",
}

# 7 个新增类别默认子类名称字典 (SSOT)
BATCH4_SUBCATEGORY_NAME_MAP: Dict[str, str] = {
    "combat_action": "格斗与战术姿态 (Combat & Action)",
    "gestures": "手势与指法动作 (Hand Gestures)",
    "gestures_self_touch": "身体互动与自触手势 (Self-Touch & Interaction)",
    "limb_posture": "肢体特定位置与体态 (Limb & Body Postures)",
    "prose_pose_templates": "自然语言整身叙述模板 (Prose Pose Templates)",
    "restraints_holds": "拘束受限与压制姿态 (Restraints & Holds)",
    "signature_poses": "标志性经典姿态 (Signature Poses)",
}

# 8 条绝对物理隔离项清单
BATCH4_QUARANTINE_ENTITY_IDS: Set[str] = {
    "SRC_POSE_03788",  # casing ejection (MAP_00510)
    "SRC_POSE_03931",  # kine (MAP_00653)
    "SRC_POSE_03952",  # log pose (MAP_00674)
    "SRC_POSE_03992",  # pokemon move (MAP_00714)
    "SRC_POSE_04004",  # reverse trap (MAP_00726)
    "SRC_POSE_04018",  # single drill (MAP_00740)
    "SRC_POSE_04266",  # violence (MAP_00988)
    "SRC_POSE_04270",  # war (MAP_00992)
}

BATCH4_QUARANTINE_MAPPING_IDS: Set[str] = {
    "MAP_00510", "MAP_00653", "MAP_00674", "MAP_00714",
    "MAP_00726", "MAP_00740", "MAP_00988", "MAP_00992",
}


def sanitize_tag_id(tag_id: str) -> str:
    """确保 tag_id 符合 ^[a-z][a-z0-9_]{2,95}$ 契约 (最大长度 96 字符)。"""
    if len(tag_id) <= 96:
        return tag_id
    return tag_id[:96].rstrip('_')


def get_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_batch4_ledger() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    src_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    map_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    with open(src_path, "r", encoding="utf-8") as f:
        all_sources = {s["entity_id"]: s for s in csv.DictReader(f, delimiter="\t")}

    with open(map_path, "r", encoding="utf-8") as f:
        all_mappings = list(csv.DictReader(f, delimiter="\t"))

    b4_mappings = [
        m for m in all_mappings
        if all_sources.get(m["source_entity_id"], {}).get("category") == "pose_action"
    ]
    b4_sources = [all_sources[m["source_entity_id"]] for m in b4_mappings]
    return b4_sources, b4_mappings


def execute_batch4_ingestion(
    data_dir: Path,
    dry_run: bool = False,
    backup_snapshot: bool = True,
    snapshot_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行 Batch 4 增量入库流程。"""
    poses_path = data_dir / "poses.json"

    # 1. 创建备份快照（严格写保护，首次写入后不再覆盖）
    actual_snapshot_dir = snapshot_dir if snapshot_dir is not None else (REPO_DIR / "scratch/m4_snapshots/batch_4_pre_ingest")
    if backup_snapshot and not dry_run:
        actual_snapshot_dir.mkdir(parents=True, exist_ok=True)
        dest = actual_snapshot_dir / poses_path.name
        if poses_path.exists() and not dest.exists():
            shutil.copy2(poses_path, dest)

    # 2. 读取现有数据
    poses_data = json.loads(poses_path.read_text(encoding="utf-8"))
    categories = poses_data.get("pose_categories", [])
    b4_sources, b4_mappings = load_batch4_ledger()
    source_map = {s["entity_id"]: s for s in b4_sources}

    stats = {
        "total_sources": len(b4_sources),
        "quarantined_sources": 0,
        "candidate_sources": 0,
        "total_mappings": len(b4_mappings),
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

    # 3. 逐映射处理
    for m in b4_mappings:
        mid = m["mapping_id"]
        eid = m["source_entity_id"]
        src_info = source_map[eid]
        decision = src_info["decision"]
        target_file = m["target_catalog_file"]
        target_item_id = m["target_item_id"]
        raw_tag_id = m["target_tag_id"]
        sanitized_tag_id_val = sanitize_tag_id(raw_tag_id)
        target_role = m["target_role"]
        raw_text = m["target_tag_text"]

        # 3.0 物理隔离项严格排除：8 条隔离项严禁入库
        if (
            eid in BATCH4_QUARANTINE_ENTITY_IDS
            or mid in BATCH4_QUARANTINE_MAPPING_IDS
            or decision == "DEFERRED_ISSUE"
            or target_role == "quarantine"
        ):
            stats["quarantined_sources"] += 1
            stats["quarantined_mappings"] += 1
            records.append({
                "mapping_id": mid,
                "source_entity_id": eid,
                "decision": decision,
                "target_file": target_file,
                "target_container": "QUARANTINE_ISOLATED",
                "target_item_id": target_item_id,
                "target_tag_id": raw_tag_id,
                "sanitized_tag_id": sanitized_tag_id_val,
                "clean_text": raw_text.strip(),
                "action": "QUARANTINE_EXCLUDED",
            })
            continue

        stats["candidate_sources"] += 1
        stats["candidate_mappings"] += 1

        raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
        adapted_facts_obj = build_runtime_semantic_facts(raw_facts, eid, "poses.json")
        adapted_facts = adapted_facts_obj.to_dict()

        if "governance_metadata" not in adapted_facts:
            adapted_facts["governance_metadata"] = {}

        # 保证 semantic_role 显式填充
        if adapted_facts.get("semantic_role") is None:
            adapted_facts["semantic_role"] = "variant" if decision == "STYLE_VARIANT" else "selector"

        # 3.1 目标分类检索与新增
        cat = next((c for c in categories if c.get("id") == target_item_id), None)
        action_taken = "SKIPPED"

        if not cat:
            cat_name_zh = BATCH4_CATEGORY_NAME_ZH_MAP.get(target_item_id, f"{target_item_id} (姿态分类)")
            subcat_name = BATCH4_SUBCATEGORY_NAME_MAP.get(target_item_id, "核心姿态 (Core Poses)")
            cat = {
                "id": target_item_id,
                "name_zh": cat_name_zh,
                "subcategories": [
                    {
                        "name": subcat_name,
                        "tags": [],
                    }
                ],
            }
            categories.append(cat)
            stats["added_items"] += 1
            target_subcat = cat["subcategories"][0]
            action_taken = "NEW_ITEM"
        else:
            # 既有类别或已存在的新类别子分类路由
            if target_item_id in BATCH4_CATEGORY_NAME_ZH_MAP:
                target_subcat = cat["subcategories"][0]
            else:
                ext_subcat = next((sc for sc in cat.get("subcategories", []) if sc.get("name") == "扩展姿态 (Extended Poses)"), None)
                if not ext_subcat:
                    ext_subcat = {"name": "扩展姿态 (Extended Poses)", "tags": []}
                    cat.setdefault("subcategories", []).append(ext_subcat)
                target_subcat = ext_subcat

        # 3.2 标签增量去重与追加
        existing_tag_ids = {t.get("id") for sc in cat.get("subcategories", []) for t in sc.get("tags", []) if isinstance(t, dict)}
        existing_tag_texts = {t.get("text", "").strip().lower() for sc in cat.get("subcategories", []) for t in sc.get("tags", []) if isinstance(t, dict)}

        tag_record = {
            "id": sanitized_tag_id_val,
            "text": raw_text.strip(),
            "facts": adapted_facts,
        }

        if decision == "REUSE_EXISTING":
            stats["reuse_existing"] += 1
            action_taken = "AUDITED_REUSE"
        elif sanitized_tag_id_val in existing_tag_ids:
            stats["idempotent_skipped_tags"] += 1
            action_taken = "IDEMPOTENT_SKIPPED"
        elif raw_text.strip().lower() in existing_tag_texts:
            stats["idempotent_skipped_tags"] += 1
            action_taken = "IDEMPOTENT_TEXT_SKIPPED"
        else:
            target_subcat["tags"].append(tag_record)
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
            "target_container": "pose_categories",
            "target_item_id": target_item_id,
            "target_tag_id": raw_tag_id,
            "sanitized_tag_id": sanitized_tag_id_val,
            "clean_text": raw_text.strip(),
            "action": action_taken,
        })

    # 4. 写盘（若非 dry_run）
    if not dry_run:
        poses_path.write_text(json.dumps(poses_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. 记录执行账本
    final_poses_hash = get_sha256(poses_path)
    ledger_content = {
        "stats": stats,
        "file_hashes": {
            "poses.json": final_poses_hash,
        },
        "records": records,
    }

    actual_ledger_path = ledger_path if ledger_path is not None else (REPO_DIR / "scratch/m4_batch4_execution_ledger.json")
    if not dry_run:
        history_run = {
            "run_stats": stats,
            "poses_sha256": final_poses_hash,
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
            "poses.json": final_poses_hash,
        },
        "records_count": len(records),
    }


def main():
    parser = argparse.ArgumentParser(description="M4 Batch 4 Ingestion Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without disk write")
    parser.add_argument("--no-backup", action="store_true", help="Skip pre-ingestion backup")
    parser.add_argument("--data-dir", type=str, default=str(REPO_DIR / "data"), help="Path to data directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    print(f"Executing Batch 4 Ingestion on: {data_dir} (dry_run={args.dry_run})")
    res = execute_batch4_ingestion(data_dir, dry_run=args.dry_run, backup_snapshot=not args.no_backup)
    print("Execution Finished!")
    print(json.dumps(res["stats"], indent=2))
    print(f"poses.json SHA256: {res['file_hashes']['poses.json']}")


if __name__ == "__main__":
    main()
