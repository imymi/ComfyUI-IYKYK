#!/usr/bin/env python3
"""
scratch/report_m32_ledger_summary.py
自动从当前主表与映射表读取真实数据，生成 100% 准确的 M3.2 进度与去重决策分布 Markdown 报告。
"""
from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent

def generate_ledger_summary() -> str:
    source_entities_path = REPO_DIR / "scratch/rc10_source_entities.tsv"
    target_mappings_path = REPO_DIR / "scratch/rc10_target_mappings.tsv"

    # 读取主表
    with open(source_entities_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    # 读取映射表
    with open(target_mappings_path, "r", encoding="utf-8") as f:
        mreader = csv.DictReader(f, delimiter="\t")
        mappings = list(mreader)

    cat_stats = {}
    ordered_cats = ["expression", "lighting", "accessories", "hair", "camera", "pose_action", "scene", "clothing"]
    cat_names = {
        "expression": "表情",
        "lighting": "灯光",
        "accessories": "鞋袜配饰",
        "hair": "发型",
        "camera": "镜头",
        "pose_action": "动作姿态",
        "scene": "场景环境",
        "clothing": "服装款式",
    }

    for c in ordered_cats:
        cat_stats[c] = Counter()

    for r in rows:
        cat = r["category"]
        dec = r["decision"]
        if cat in cat_stats:
            cat_stats[cat][dec] += 1

    mappings_by_cat = Counter()
    eid_to_cat = {r["entity_id"]: r["category"] for r in rows}
    for m in mappings:
        eid = m["source_entity_id"]
        cat = eid_to_cat.get(eid, "unknown")
        mappings_by_cat[cat] += 1

    lines = []
    lines.append("| 入库类别 | 来源条目 | 复用 (`REUSE`) | 变体 (`VARIANT`) | 新增 (`NEW`) | 复合解构 (`COMBO`) | 暂缓/问题 (`DEFERRED`) | 待审核 (`PENDING`) | 映射数 | 审核进度 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|")

    tot_items = 0
    tot_reuse = 0
    tot_var = 0
    tot_new = 0
    tot_combo = 0
    tot_deferred = 0
    tot_pending = 0
    tot_maps = 0

    for c in ordered_cats:
        st = cat_stats[c]
        c_tot = sum(st.values())
        c_reuse = st["REUSE_EXISTING"]
        c_var = st["STYLE_VARIANT"]
        c_new = st["NEW_STYLE"]
        c_combo = st["ENSEMBLE_COMBO"]
        c_deferred = st["DEFERRED_ISSUE"]
        c_pending = st["PENDING_M32_REVIEW"]
        c_maps = mappings_by_cat[c]

        tot_items += c_tot
        tot_reuse += c_reuse
        tot_var += c_var
        tot_new += c_new
        tot_combo += c_combo
        tot_deferred += c_deferred
        tot_pending += c_pending
        tot_maps += c_maps

        reviewed = c_tot - c_pending
        pct = (reviewed / c_tot * 100) if c_tot else 0.0
        status_str = f"**100%**" if c_pending == 0 else (f"{pct:.1f}%" if reviewed > 0 else "0.0%")

        lines.append(f"| {cat_names[c]} (`{c}`) | {c_tot} | {c_reuse} | {c_var} | {c_new} | {c_combo} | {c_deferred} | {c_pending} | {c_maps} | {status_str} |")

    overall_reviewed = tot_items - tot_pending
    overall_pct = (overall_reviewed / tot_items * 100) if tot_items else 0.0
    lines.append(f"| **合计** | **{tot_items}** | **{tot_reuse}** | **{tot_var}** | **{tot_new}** | **{tot_combo}** | **{tot_deferred}** | **{tot_pending}** | **{tot_maps}** | **{overall_pct:.1f}% ({overall_reviewed}/{tot_items})** |")

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_ledger_summary())
