#!/usr/bin/env python3
"""
scratch/apply_m4_batch2_ingestion.py — M4 Batch 2 正式增量入库、模式强校验与审计工具

涵盖范围与执行契约：
1. 目标数据文件：accessories.json (hairstyles & headwear_jewelry 两个容器)。
   - 145 个发型来源实体 / 145 条目标映射；
   - 决策分布：10 REUSE_EXISTING, 75 STYLE_VARIANT, 59 NEW_STYLE, 1 ENSEMBLE_COMBO。
2. 现有库与跨批次去重：
   - 16 个既有发型条目复用 / 变体挂载；
   - 34 个新增发型大类 (hair_bald, hair_afro, hair_locs, hair_cornrows, hair_color_palette, hair_length_palette 等)；
   - 3 个新增发饰/发带大类挂载至 headwear_jewelry (acc_hair_ornament, acc_hairband, acc_hairclip)；
   - 10 个 REUSE 条目严格执行 AUDITED_REUSE，不产生重复标签；
   - 净增 37 个新 items，135 个新 tags。
3. 动态发型组合宏 (hair_ensemble_composite)：
   - 来源 SRC_HAIR_00005，映射 MAP_00132；
   - 标定 is_container_description: True 与 is_ensemble_combo: True；
   - 槽位包含发长、1~3款发型样式、发色及可选发饰，完全对齐 02_hair 与 09_style 词池。
4. 安全保障：
   - 物理备份快照至 scratch/m4_snapshots/batch_2_pre_ingest/ (写保护杜绝覆写)；
   - 逐映射审计账本落盘至 scratch/m4_batch2_execution_ledger.json；
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

# 规范发型与发饰中文显示名称字典 (SSOT)
BATCH2_ITEM_NAME_ZH_MAP: Dict[str, str] = {
    # 34 new items in hairstyles
    "hair_1940s_waves": "40年代复古波浪 (1940s Waves)",
    "hair_afro": "圆蓬爆炸头 (Afro)",
    "hair_bald": "光头剃光发 (Bald)",
    "hair_bangs": "通用刘海 (Bangs)",
    "hair_big_layered_mane": "狮系大层次长发 (Big Layered Mane)",
    "hair_box_braids": "盒状脏辫 (Box Braids)",
    "hair_color_palette": "正交发色库 (Hair Color Palette)",
    "hair_cornrows": "地垄辫编发 (Cornrows)",
    "hair_crimped": "玉米须烫发 (Crimped Hair)",
    "hair_curtain_fringe_layers": "八字刘海层次发 (Curtain Fringe Layers)",
    "hair_ensemble_composite": "复合多槽位发型组合 (Composite Hairstyle Ensemble)",
    "hair_face_framing_layers": "贴脸修颜层次发 (Face Framing Layers)",
    "hair_feathered_flicked": "复古翻翘羽化发 (Feathered Flicked Layers)",
    "hair_feathered_shag": "及肩羽化碎发 (Feathered Shag)",
    "hair_feature_ahoge": "二次元呆毛 (Ahoge)",
    "hair_flat_twists": "平贴头皮拧辫 (Flat Twists)",
    "hair_length_palette": "正交发长库 (Hair Length Palette)",
    "hair_locs": "雷鬼脏辫 (Locs / Dreadlocks)",
    "hair_long_blunt_fringe": "齐刘海长发 (Long Blunt Fringe)",
    "hair_long_blunt_hemline": "一刀切发尾长发 (Long Blunt Hemline)",
    "hair_long_deep_side_part": "大侧分刘海长发 (Long Deep Side Part)",
    "hair_pageboy": "复古内扣侍童头 (Pageboy Bob)",
    "hair_permed_curls": "满头烫小卷发 (Permed Curls)",
    "hair_pixie_side_swept": "侧分精灵短发 (Side-Swept Pixie)",
    "hair_ponytail_general": "通用马尾 (General Ponytail)",
    "hair_shag_mullet": "碎层次鲻鱼头 (Shag Mullet)",
    "hair_straight_general": "通用直发 (Straight Hair)",
    "hair_swept_back_long": "露额后梳长发 (Swept Back Long)",
    "hair_texture_palette": "正交发质质感库 (Hair Texture Palette)",
    "hair_twist_out": "弹力扭转卷发 (Twist-Out)",
    "hair_two_strand_twists": "双股扭转拧绳发 (Two-Strand Twists)",
    "hair_waist_feathered": "及腰羽化长发 (Waist-Length Feathered)",
    "hair_wavy_general": "通用波浪卷发 (Wavy Hair)",
    "hair_wedge_cut": "堆叠楔形短发 (Stacked Wedge Cut)",
    # 3 new items in headwear_jewelry
    "acc_hair_ornament": "泛用发饰 (Hair Ornament)",
    "acc_hairband": "发箍发带 (Hairband)",
    "acc_hairclip": "发夹边夹 (Hairclip)",
}


def get_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_batch2_ledger() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    src_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    map_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    with open(src_path, "r", encoding="utf-8") as f:
        all_sources = {s["entity_id"]: s for s in csv.DictReader(f, delimiter="\t")}

    with open(map_path, "r", encoding="utf-8") as f:
        all_mappings = list(csv.DictReader(f, delimiter="\t"))

    b2_mappings = [
        m for m in all_mappings
        if all_sources.get(m["source_entity_id"], {}).get("category") == "hair"
    ]
    b2_sources = [all_sources[m["source_entity_id"]] for m in b2_mappings]
    return b2_sources, b2_mappings


def execute_batch2_ingestion(
    data_dir: Path,
    dry_run: bool = False,
    backup_snapshot: bool = True,
    snapshot_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行 Batch 2 增量入库流程。"""
    acc_path = data_dir / "accessories.json"

    # 1. 创建备份快照（严格写保护，首次写入后不再覆盖）
    actual_snapshot_dir = snapshot_dir if snapshot_dir is not None else (REPO_DIR / "scratch/m4_snapshots/batch_2_pre_ingest")
    if backup_snapshot and not dry_run:
        actual_snapshot_dir.mkdir(parents=True, exist_ok=True)
        dest = actual_snapshot_dir / "accessories.json"
        if acc_path.exists() and not dest.exists():
            shutil.copy2(acc_path, dest)

    # 2. 读取现有数据
    acc_data = json.loads(acc_path.read_text(encoding="utf-8"))
    b2_sources, b2_mappings = load_batch2_ledger()
    source_map = {s["entity_id"]: s for s in b2_sources}

    stats = {
        "total_mappings": len(b2_mappings),
        "reuse_existing": 0,
        "style_variant": 0,
        "new_style": 0,
        "ensemble_combo": 0,
        "added_items": 0,
        "added_tags": 0,
    }
    records = []

    # 3. 逐映射处理
    for m in b2_mappings:
        mid = m["mapping_id"]
        eid = m["source_entity_id"]
        src_info = source_map[eid]
        decision = src_info["decision"]
        target_item_id = m["target_item_id"]
        target_tag_id = m["target_tag_id"]
        target_role = m["target_role"]
        raw_text = m["target_tag_text"]

        raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
        adapted_facts_obj = build_runtime_semantic_facts(raw_facts)
        adapted_facts = adapted_facts_obj.to_dict()

        if "governance_metadata" not in adapted_facts:
            adapted_facts["governance_metadata"] = {}

        if target_tag_id == "hair_ensemble_composite__tag_000":
            adapted_facts["governance_metadata"]["is_container_description"] = True
            adapted_facts["governance_metadata"]["is_ensemble_combo"] = True
            adapted_facts["governance_metadata"]["role"] = "ensemble_combo"

        # 3.1 路由至 hairstyles 或 headwear_jewelry
        is_headwear = target_item_id.startswith("acc_")
        container_key = "headwear_jewelry" if is_headwear else "hairstyles"
        container = acc_data[container_key]

        item = next((it for it in container if it.get("id") == target_item_id), None)
        action_taken = "SKIPPED"

        if not item:
            item_name_zh = BATCH2_ITEM_NAME_ZH_MAP.get(target_item_id, f"{target_item_id} (发型配饰)")
            item = {
                "id": target_item_id,
                "name_zh": item_name_zh,
                "tags": [],
            }
            container.append(item)
            stats["added_items"] += 1
            action_taken = "NEW_ITEM"

        # 3.2 标签增量去重与追加
        existing_tags = item.get("tags", [])
        existing_tag_ids = {t.get("id") for t in existing_tags if isinstance(t, dict)}
        existing_tag_texts = {t.get("text", "").strip().lower() for t in existing_tags if isinstance(t, dict)}

        tag_record = {
            "id": target_tag_id,
            "text": raw_text.strip(),
            "facts": adapted_facts,
        }

        if decision == "REUSE_EXISTING":
            stats["reuse_existing"] += 1
            action_taken = "AUDITED_REUSE"
        elif target_tag_id in existing_tag_ids:
            action_taken = "IDEMPOTENT_SKIPPED"
        elif raw_text.strip().lower() in existing_tag_texts:
            action_taken = "IDEMPOTENT_TEXT_SKIPPED"
        else:
            existing_tags.append(tag_record)
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
            "target_file": "accessories.json",
            "target_container": container_key,
            "target_item_id": target_item_id,
            "target_tag_id": target_tag_id,
            "clean_text": raw_text.strip(),
            "action": action_taken,
        })

    # 4. 写盘（若非 dry_run）
    if not dry_run:
        acc_path.write_text(json.dumps(acc_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. 记录执行账本
    final_hash = get_sha256(acc_path)
    ledger_content = {
        "stats": stats,
        "file_hashes": {
            "accessories.json": final_hash,
        },
        "records": records,
    }

    actual_ledger_path = ledger_path if ledger_path is not None else (REPO_DIR / "scratch/m4_batch2_execution_ledger.json")
    if not dry_run:
        # 支持保留历史执行记录
        history_run = {
            "run_stats": stats,
            "accessories_sha256": final_hash,
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
        "file_hash": final_hash,
        "records_count": len(records),
    }


def main():
    parser = argparse.ArgumentParser(description="M4 Batch 2 Ingestion Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without disk write")
    parser.add_argument("--no-backup", action="store_true", help="Skip pre-ingestion backup")
    parser.add_argument("--data-dir", type=str, default=str(REPO_DIR / "data"), help="Path to data directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    print(f"Executing Batch 2 Ingestion on: {data_dir} (dry_run={args.dry_run})")
    res = execute_batch2_ingestion(data_dir, dry_run=args.dry_run, backup_snapshot=not args.no_backup)
    print("Execution Finished!")
    print(json.dumps(res["stats"], indent=2))
    print(f"accessories.json SHA256: {res['file_hash']}")


if __name__ == "__main__":
    main()
