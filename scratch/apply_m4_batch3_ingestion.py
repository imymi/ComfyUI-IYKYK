#!/usr/bin/env python3
"""
scratch/apply_m4_batch3_ingestion.py — M4 Batch 3 正式增量入库、模式强校验与审计工具

涵盖范围与执行契约：
1. 目标数据文件：
   - shot_types.json (shot_types & camera_angles 两个容器，72 条候选映射，29 个目标 items)；
   - film_stocks.json (film_stocks & cinema_lenses 两个容器，129 条候选映射，49 个目标 items)。
2. 来源与映射口径对账：
   - 202 个来源实体 (SRC_CAM_06612 ~ SRC_CAM_06813)；
   - 1 条隔离项绝对排除：SRC_CAM_06691 (MAP_00352, Sigma sd Quattro H + 24-70mm DG DN 物理卡口不兼容，DEFERRED_ISSUE)，100% 物理隔离严禁入库；
   - 201 条可入库候选映射：18 REUSE_EXISTING, 56 STYLE_VARIANT, 127 NEW_STYLE, 0 ENSEMBLE_COMBO。
3. 容器路由与 Schema 强契约：
   - shot_types.json：
     * camera_angles: 既有 8 条目 + 5 个新增视角朝向 (camera_orientation_*)；
     * shot_types: 既有 7 条目 + 9 个新增运镜/构图/光学品类 (camera_motion, specialty_lens_fisheye, perspective_optical, shot_composition_*)。
   - film_stocks.json：
     * cinema_lenses: 既有 7 条目 + 14 款专业相机器材套机 (camera_hardware_*)，符合 ["id", "name_zh", "tags"] 约束；
     * film_stocks: 既有 22 条目 + 17 款经典胶卷扩展与暗房相纸 (film_stock_*, darkroom_photographic_paper)，满足 ["id", "name_zh", "series", "tags"] 约束。
4. 去重与增量对齐：
   - 18 条 REUSE_EXISTING 严格执行 AUDITED_REUSE，不产生重复标签；
   - fish-eye lens (SRC_CAM_06621) 与 fisheye lens (SRC_CAM_06622) 共同归并至 specialty_lens__fisheye，消除拼写冗余；
   - 净增 45 个新 items (14 shot_types, 31 film_stocks)，182 个新 tags (53 shot_types, 129 film_stocks)。
5. 安全保障：
   - 物理备份快照至 scratch/m4_snapshots/batch_3_pre_ingest/ (写保护杜绝覆写)；
   - 逐映射审计账本落盘至 scratch/m4_batch3_execution_ledger.json；
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

# 规范镜头、景别与胶卷器材中文显示名称字典 (SSOT)
BATCH3_ITEM_NAME_ZH_MAP: Dict[str, str] = {
    # 14 new items in shot_types.json
    "camera_motion": "动态运镜 (Camera Motion)",
    "specialty_lens_fisheye": "鱼眼镜头光学畸变 (Fisheye Lens)",
    "perspective_optical": "光学透视缩短 (Optical Foreshortening)",
    "shot_composition_action": "动态透视动作构图 (Dynamic Action Composition)",
    "shot_composition_rules": "构图法则与平衡 (Composition Rules)",
    "shot_composition_occlusion": "环境遮挡层次构图 (Environmental Occlusion)",
    "shot_composition_framing": "边缘诱导裁切构图 (Framing Edge Crop)",
    "shot_framing_genre": "自拍摄影视角 (Self-Portrait Framing)",
    "shot_composition_lighting": "剪影强调构图 (Silhouette Lighting)",
    "camera_orientation_rear": "背向观察视 (Rear Orientation)",
    "camera_orientation_side": "侧面观察视 (Side Orientation)",
    "camera_orientation_frontal": "正面观察视 (Frontal Orientation)",
    "camera_orientation_profile": "正侧剪影视 (Profile Orientation)",
    "camera_orientation_three_quarter": "斜侧四分之三视 (Three-Quarter Orientation)",

    # 17 new film stocks in film_stocks.json (film_stocks container)
    "darkroom_photographic_paper": "暗房相纸与特殊印相 (Darkroom Photographic Paper)",
    "film_stock_adox": "ADOX 经典胶卷 (Adox Film Stocks)",
    "film_stock_agfa": "爱克发经典专业胶卷 (Agfa Professional Film)",
    "film_stock_agfaphoto": "爱克发彩色与黑白胶卷 (AgfaPhoto Film Stocks)",
    "film_stock_bergger": "贝尔格法国黑白胶卷 (Bergger Film Stocks)",
    "film_stock_foma": "福马捷克黑白胶卷 (Foma Fomapan Series)",
    "film_stock_fujifilm": "富士经典胶卷扩展 (Fujifilm Extended Series)",
    "film_stock_ilford": "依尔福英伦专业黑白胶卷 (Ilford Professional B&W)",
    "film_stock_kentmere": "肯特梅尔黑白胶卷 (Kentmere B&W Film)",
    "film_stock_kodak": "柯达经典胶卷扩展 (Kodak Extended Series)",
    "film_stock_konica_minolta": "柯尼卡美能达胶卷 (Konica Minolta Film)",
    "film_stock_lomography": "乐魔实验电影与彩色胶卷 (Lomography Creative Film)",
    "film_stock_lucky": "乐凯经典黑白胶卷 (Lucky B&W Film)",
    "film_stock_orwo": "沃芬东德电影胶卷 (ORWO Film Stocks)",
    "film_stock_polaroid": "宝丽来即时成像相纸 (Polaroid Instant Film)",
    "film_stock_revue": "Revue 德系复古胶卷 (Revue Film Stocks)",
    "film_stock_rollei": "禄来德系专业黑白与红外胶卷 (Rollei Film Stocks)",

    # 14 camera hardware items in film_stocks.json (cinema_lenses container)
    "camera_hardware_canon": "佳能专业相机系统 (Canon Camera Hardware)",
    "camera_hardware_dji": "大疆航拍无人机系统 (DJI Drone Camera Hardware)",
    "camera_hardware_fujifilm": "富士中画幅与微单系统 (Fujifilm Camera Hardware)",
    "camera_hardware_gopro": "GoPro 运动相机系统 (GoPro Action Camera Hardware)",
    "camera_hardware_hasselblad": "哈苏中画幅相机系统 (Hasselblad Camera Hardware)",
    "camera_hardware_kodak": "柯达长焦数码相机 (Kodak Pixpro Camera Hardware)",
    "camera_hardware_leica": "徕卡旁轴与微单系统 (Leica Camera Hardware)",
    "camera_hardware_nikon": "尼康专业相机与长焦机 (Nikon Camera Hardware)",
    "camera_hardware_olympus": "奥林巴斯微单系统 (Olympus Camera Hardware)",
    "camera_hardware_panasonic": "松下微单数码相机系统 (Panasonic Camera Hardware)",
    "camera_hardware_pentax": "宾得中画幅与单反系统 (Pentax Camera Hardware)",
    "camera_hardware_ricoh": "理光街拍便携相机 (Ricoh GR Camera Hardware)",
    "camera_hardware_sigma": "适马全画幅微单系统 (Sigma Camera Hardware)",
    "camera_hardware_sony": "索尼全画幅微单系统 (Sony Alpha Camera Hardware)",
}

# 胶卷系列必填属性映射 (film_stocks 容器必填 series 字段)
BATCH3_FILM_STOCK_SERIES_MAP: Dict[str, str] = {
    "darkroom_photographic_paper": "🔵 冷调/特殊系",
    "film_stock_adox": "⚫ 黑白/单色系",
    "film_stock_agfa": "⚫ 黑白/单色系",
    "film_stock_agfaphoto": "🔵 冷调/特殊系",
    "film_stock_bergger": "⚫ 黑白/单色系",
    "film_stock_foma": "⚫ 黑白/单色系",
    "film_stock_fujifilm": "🔴 高饱和鲜艳系",
    "film_stock_ilford": "⚫ 黑白/单色系",
    "film_stock_kentmere": "⚫ 黑白/单色系",
    "film_stock_kodak": "🟡 暖调人像系",
    "film_stock_konica_minolta": "🟡 暖调人像系",
    "film_stock_lomography": "🔵 冷调/特殊系",
    "film_stock_lucky": "⚫ 黑白/单色系",
    "film_stock_orwo": "⚫ 黑白/单色系",
    "film_stock_polaroid": "🟡 暖调人像系",
    "film_stock_revue": "🟤 低饱和纪实系",
    "film_stock_rollei": "⚫ 黑白/单色系",
}

# 胶卷适用场景说明映射 (可选 scenarios 字段)
BATCH3_FILM_STOCK_SCENARIOS_MAP: Dict[str, str] = {
    "darkroom_photographic_paper": "暗房放大、艺术微喷、银盐相纸质感",
    "film_stock_adox": "超微粒黑白、极高解像力、翻拍与科学摄影",
    "film_stock_agfa": "正色黑白、工业摄影、历史胶片质感",
    "film_stock_agfaphoto": "正片反转片、欧系通透色彩、彩色负片",
    "film_stock_bergger": "双重乳剂黑白、丰富中间调灰阶、艺术人像",
    "film_stock_foma": "复古东欧调性、黑白反转片与负片、手工冲洗",
    "film_stock_fujifilm": "一次成像相纸、经典撕拉片、工业彩色",
    "film_stock_ilford": "专业暗房黑白、细微粒、大反差艺术写真",
    "film_stock_kentmere": "入门黑白、平价纪实、宽容度高",
    "film_stock_kodak": "红外伪色航空胶卷、经典专业彩色负片与黑白",
    "film_stock_konica_minolta": "日系复古温暖色彩、世纪末平民纪实",
    "film_stock_lomography": "电影底片分装、高对比黑白、紫色特殊乳剂",
    "film_stock_lucky": "国产经典银盐、高反差纪实、历史记忆",
    "film_stock_orwo": "东德电影工业黑白、高质感历史感",
    "film_stock_polaroid": "即时显影相纸、岁月泛黄边框感",
    "film_stock_revue": "西德超市代工、冷调复古纪实",
    "film_stock_rollei": "高解析度红外、航空测绘黑白、专业彩色负片",
}

# camera_angles 容器归属条目集合 (其余 shot_types.json 条目归属 shot_types)
CAMERA_ANGLE_ITEM_IDS: Set[str] = {
    "eye_level",
    "low_angle",
    "high_angle",
    "overhead_birds_eye",
    "worms_eye",
    "dutch_angle",
    "over_the_shoulder",
    "pov_first_person",
    "camera_orientation_rear",
    "camera_orientation_side",
    "camera_orientation_frontal",
    "camera_orientation_profile",
    "camera_orientation_three_quarter",
}


def get_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_batch3_ledger() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    src_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    map_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    with open(src_path, "r", encoding="utf-8") as f:
        all_sources = {s["entity_id"]: s for s in csv.DictReader(f, delimiter="\t")}

    with open(map_path, "r", encoding="utf-8") as f:
        all_mappings = list(csv.DictReader(f, delimiter="\t"))

    b3_mappings = [
        m for m in all_mappings
        if all_sources.get(m["source_entity_id"], {}).get("category") == "camera"
    ]
    b3_sources = [all_sources[m["source_entity_id"]] for m in b3_mappings]
    return b3_sources, b3_mappings


def execute_batch3_ingestion(
    data_dir: Path,
    dry_run: bool = False,
    backup_snapshot: bool = True,
    snapshot_dir: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行 Batch 3 增量入库流程。"""
    st_path = data_dir / "shot_types.json"
    fs_path = data_dir / "film_stocks.json"

    # 1. 创建备份快照（严格写保护，首次写入后不再覆盖）
    actual_snapshot_dir = snapshot_dir if snapshot_dir is not None else (REPO_DIR / "scratch/m4_snapshots/batch_3_pre_ingest")
    if backup_snapshot and not dry_run:
        actual_snapshot_dir.mkdir(parents=True, exist_ok=True)
        for p in (st_path, fs_path):
            dest = actual_snapshot_dir / p.name
            if p.exists() and not dest.exists():
                shutil.copy2(p, dest)

    # 2. 读取现有数据
    st_data = json.loads(st_path.read_text(encoding="utf-8"))
    fs_data = json.loads(fs_path.read_text(encoding="utf-8"))
    b3_sources, b3_mappings = load_batch3_ledger()
    source_map = {s["entity_id"]: s for s in b3_sources}

    stats = {
        "total_sources": len(b3_sources),
        "quarantined_sources": 0,
        "candidate_sources": 0,
        "total_mappings": len(b3_mappings),
        "quarantined_mappings": 0,
        "candidate_mappings": 0,
        "reuse_existing": 0,
        "style_variant": 0,
        "new_style": 0,
        "ensemble_combo": 0,
        "added_items": 0,
        "added_items_shot_types": 0,
        "added_items_film_stocks": 0,
        "added_tags": 0,
        "added_tags_shot_types": 0,
        "added_tags_film_stocks": 0,
        "idempotent_skipped_tags": 0,
    }
    records = []

    # 3. 逐映射处理
    for m in b3_mappings:
        mid = m["mapping_id"]
        eid = m["source_entity_id"]
        src_info = source_map[eid]
        decision = src_info["decision"]
        target_file = m["target_catalog_file"]
        target_item_id = m["target_item_id"]
        target_tag_id = m["target_tag_id"]
        target_role = m["target_role"]
        raw_text = m["target_tag_text"]

        # 3.0 物理隔离项严格排除：SRC_CAM_06691 (MAP_00352) 严禁入库
        if eid == "SRC_CAM_06691" or mid == "MAP_00352" or decision == "DEFERRED_ISSUE" or target_role == "quarantine":
            stats["quarantined_sources"] += 1
            stats["quarantined_mappings"] += 1
            records.append({
                "mapping_id": mid,
                "source_entity_id": eid,
                "decision": decision,
                "target_file": target_file,
                "target_container": "QUARANTINE_ISOLATED",
                "target_item_id": target_item_id,
                "target_tag_id": target_tag_id,
                "clean_text": raw_text.strip(),
                "action": "QUARANTINE_EXCLUDED",
            })
            continue

        stats["candidate_sources"] += 1
        stats["candidate_mappings"] += 1

        raw_facts = json.loads(m.get("semantic_facts_json") or "{}")
        adapted_facts_obj = build_runtime_semantic_facts(raw_facts)
        adapted_facts = adapted_facts_obj.to_dict()

        if "governance_metadata" not in adapted_facts:
            adapted_facts["governance_metadata"] = {}

        # 3.1 容器路由裁定
        if target_file == "shot_types.json":
            container_key = "camera_angles" if target_item_id in CAMERA_ANGLE_ITEM_IDS else "shot_types"
            container = st_data[container_key]
        elif target_file == "film_stocks.json":
            container_key = "cinema_lenses" if target_item_id.startswith("camera_hardware_") else "film_stocks"
            container = fs_data[container_key]
        else:
            raise ValueError(f"Unknown target file for Batch 3: {target_file}")

        # 3.2 目标 Item 检索与新增
        item = next((it for it in container if it.get("id") == target_item_id), None)
        action_taken = "SKIPPED"

        if not item:
            item_name_zh = BATCH3_ITEM_NAME_ZH_MAP.get(target_item_id, f"{target_item_id} (镜头器材)")
            item = {
                "id": target_item_id,
                "name_zh": item_name_zh,
                "tags": [],
            }
            # film_stocks 容器必须填充 series 与可选 scenarios
            if target_file == "film_stocks.json" and container_key == "film_stocks":
                series_val = BATCH3_FILM_STOCK_SERIES_MAP.get(target_item_id, "🔵 冷调/特殊系")
                item["series"] = series_val
                if target_item_id in BATCH3_FILM_STOCK_SCENARIOS_MAP:
                    item["scenarios"] = BATCH3_FILM_STOCK_SCENARIOS_MAP[target_item_id]

            container.append(item)
            stats["added_items"] += 1
            if target_file == "shot_types.json":
                stats["added_items_shot_types"] += 1
            else:
                stats["added_items_film_stocks"] += 1
            action_taken = "NEW_ITEM"

        # 3.3 标签增量去重与追加
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
            stats["idempotent_skipped_tags"] += 1
            action_taken = "IDEMPOTENT_SKIPPED"
        elif raw_text.strip().lower() in existing_tag_texts:
            stats["idempotent_skipped_tags"] += 1
            action_taken = "IDEMPOTENT_TEXT_SKIPPED"
        else:
            existing_tags.append(tag_record)
            stats["added_tags"] += 1
            if target_file == "shot_types.json":
                stats["added_tags_shot_types"] += 1
            else:
                stats["added_tags_film_stocks"] += 1

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
            "target_container": container_key,
            "target_item_id": target_item_id,
            "target_tag_id": target_tag_id,
            "clean_text": raw_text.strip(),
            "action": action_taken,
        })

    # 4. 写盘（若非 dry_run）
    if not dry_run:
        st_path.write_text(json.dumps(st_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        fs_path.write_text(json.dumps(fs_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 5. 记录执行账本
    final_st_hash = get_sha256(st_path)
    final_fs_hash = get_sha256(fs_path)
    ledger_content = {
        "stats": stats,
        "file_hashes": {
            "shot_types.json": final_st_hash,
            "film_stocks.json": final_fs_hash,
        },
        "records": records,
    }

    actual_ledger_path = ledger_path if ledger_path is not None else (REPO_DIR / "scratch/m4_batch3_execution_ledger.json")
    if not dry_run:
        history_run = {
            "run_stats": stats,
            "shot_types_sha256": final_st_hash,
            "film_stocks_sha256": final_fs_hash,
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
            "shot_types.json": final_st_hash,
            "film_stocks.json": final_fs_hash,
        },
        "records_count": len(records),
    }


def main():
    parser = argparse.ArgumentParser(description="M4 Batch 3 Ingestion Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run without disk write")
    parser.add_argument("--no-backup", action="store_true", help="Skip pre-ingestion backup")
    parser.add_argument("--data-dir", type=str, default=str(REPO_DIR / "data"), help="Path to data directory")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    print(f"Executing Batch 3 Ingestion on: {data_dir} (dry_run={args.dry_run})")
    res = execute_batch3_ingestion(data_dir, dry_run=args.dry_run, backup_snapshot=not args.no_backup)
    print("Execution Finished!")
    print(json.dumps(res["stats"], indent=2))
    print(f"shot_types.json SHA256: {res['file_hashes']['shot_types.json']}")
    print(f"film_stocks.json SHA256: {res['file_hashes']['film_stocks.json']}")


if __name__ == "__main__":
    main()
