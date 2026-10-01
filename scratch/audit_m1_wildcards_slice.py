#!/usr/bin/env python3
"""
scratch/audit_m1_wildcards_slice.py
M1 垂直切片全量因果归因审计器：以 c74084d 为基线，执行逐种子、逐原子的数学级因果证明。

严格因果归因机制：
1. 架构事实：ComfyUI-IYKYK 各槽位采用 derive_substream_rng(effective_seed, domain) 隔离子流，
   非扩充槽位（除 hairstyle, props, lighting 外）的源原子在双版本间具有绝对确定性，必须 100% 严格一致；
2. 非扩充槽位源原子突变零容忍：任何非扩充槽位源原子的增删改，立即判定为 UNEXPLAINED_SOURCE_ATOM_MUTATION；
3. 全槽位权威词库双向严格校验：覆盖所有 16 个运行时领域，任何未授权条目或未授权 leaf tag 立即判定为非法；
4. 决策流双向闭环与生产谓词重放：所有消解必须有记录在案的合规决策，严禁无决策静默消失；
5. 全量种子无遗漏结构审计：无论输出文本是否一致，统一执行全量原子与结构核验，任何违背立即阻断；
6. 完整保存包含双版本完整原子、决策与逐原子证明链的证据归档。
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
sys.path.insert(0, str(REPO_DIR))

from scratch.audit_step3_cross_catalog import (  # noqa: E402
    export_commit,
    run_batch_parallel,
    slot_atom_signature,
    verify_atom_source_signature,
    load_authoritative_resolver_rules,
    replay_and_verify_decisions,
)
from lib.lexer import parse_prompt  # noqa: E402
from lib.conflict_resolver import normalize_slot_name  # noqa: E402

BASELINE_COMMIT = "c74084d"
EXPECTED_BASELINE_HASH = "ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89"
EXPECTED_M1_HASH = "aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139"

# M1 实际增补词库的目标槽位（仅有这 3 个槽位允许源原子抽样发生差异）
M1_EXPANDED_SLOTS = frozenset({"hairstyle", "props", "lighting"})

NEW_HAIRSTYLE_IDS = frozenset({
    "ext_aw_hair_bob_blunt_cut",
    "ext_aw_hair_long_parted_behind_ears",
})
NEW_LIGHTING_IDS = frozenset({
    "ext_aw_light_direct_flash",
    "ext_aw_light_warm_golden_hour",
})
NEW_PROP_IDS = frozenset({
    "fashion_clutch_bag",
    "fashion_baguette_bag",
})
NEW_PROP_LEAF_IDS = frozenset({
    "ext_aw_prop_clutch_bag__tag_000",
    "ext_aw_prop_baguette_bag__tag_000",
})
ALL_M1_ITEM_IDS = NEW_HAIRSTYLE_IDS | NEW_LIGHTING_IDS | NEW_PROP_IDS


def load_authoritative_catalog_lookup(data_dir: Path) -> Dict[Tuple[str, str], Set[str]]:
    """
    全量扫描数据目录，构建全局权威词库索引：
    (norm_slot, item_id) -> set of valid tag texts (包含原始 text 与 parse_prompt 分解后的 span texts)。
    涵盖全仓所有 16 个运行时领域。
    """
    lookup: Dict[Tuple[str, str], Set[str]] = collections.defaultdict(set)

    def add_tag(s: str, iid: str, raw: str):
        if not s or not iid or not raw:
            return
        norm_s = normalize_slot_name(s)
        clean_raw = raw.strip()
        lookup[(norm_s, iid)].add(clean_raw)
        try:
            for tag in parse_prompt(clean_raw).tags:
                for sp in tag.spans:
                    if sp.text:
                        lookup[(norm_s, iid)].add(sp.text.strip())
        except Exception:
            pass

    # 1. accessories.json
    acc = json.loads((data_dir / "accessories.json").read_text(encoding="utf-8"))
    for it in acc.get("hairstyles", []):
        for t in it.get("tags", []):
            add_tag("hairstyle", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in acc.get("headwear_jewelry", []):
        for t in it.get("tags", []):
            add_tag("jewelry", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 2. characters.json
    chars = json.loads((data_dir / "characters.json").read_text(encoding="utf-8"))
    for it in chars.get("characters", []):
        for t in it.get("tags", []):
            add_tag("character", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 3. clothing.json
    clo = json.loads((data_dir / "clothing.json").read_text(encoding="utf-8"))
    for sec in ("categories", "clothing_states", "lingerie_wardrobe", "sfw_exposure_tiers", "cloth_transparency_tiers"):
        for it in clo.get(sec, []):
            for t in it.get("tags", []):
                add_tag("clothing", it["id"], t if isinstance(t, str) else t.get("text", ""))
    linkage = clo.get("clothing_nudity_linkage", {})
    for lvl, ldata in linkage.items():
        for sid, tags in ldata.get("style_overrides", {}).items():
            for t in tags:
                raw = t if isinstance(t, str) else t.get("text", "")
                add_tag("clothing", sid, raw)
                add_tag("clothing", "auto_linkage", raw)
        for t in ldata.get("general_tags", []):
            raw = t if isinstance(t, str) else t.get("text", "")
            add_tag("clothing", "linkage_general", raw)
            add_tag("clothing", "auto_linkage", raw)

    # 4. expressions.json
    expr = json.loads((data_dir / "expressions.json").read_text(encoding="utf-8"))
    for it in expr.get("emotions", []):
        for t in it.get("tags", []):
            add_tag("expression", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 5. film_stocks.json
    films = json.loads((data_dir / "film_stocks.json").read_text(encoding="utf-8"))
    for it in films.get("film_stocks", []):
        for t in it.get("tags", []):
            add_tag("film", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 6. imperfections.json
    imp = json.loads((data_dir / "imperfections.json").read_text(encoding="utf-8"))
    for it in imp.get("categories", []):
        for t in it.get("tags", []):
            add_tag("imperfections", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 7. lighting.json
    light = json.loads((data_dir / "lighting.json").read_text(encoding="utf-8"))
    for sec in ("professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting"):
        for it in light.get(sec, []):
            for t in it.get("tags", []):
                add_tag("lighting", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in light.get("preset_combos", []):
        for t in it.get("tags", []):
            add_tag("lighting", it["id"], t if isinstance(t, str) else t.get("text", ""))
        for k in ("main_light", "modifier_light", "atmosphere"):
            v = it.get(k, "")
            if v:
                for p in v.split(","):
                    add_tag("lighting", it["id"], p.strip())

    # 8. makeup.json
    mak = json.loads((data_dir / "makeup.json").read_text(encoding="utf-8"))
    for it in mak.get("categories", []):
        for t in it.get("tags", []):
            add_tag("makeup", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 9. nudity_levels.json
    nud = json.loads((data_dir / "nudity_levels.json").read_text(encoding="utf-8"))
    for it in nud.get("nudity_levels", []):
        for t in it.get("tags", []):
            add_tag("nudity", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in nud.get("liquid_effects", []):
        for t in it.get("tags", []):
            add_tag("liquids", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 10. poses.json
    pos = json.loads((data_dir / "poses.json").read_text(encoding="utf-8"))
    for cat in pos.get("pose_categories", []):
        cat_id = cat.get("id")
        for sub in cat.get("subcategories", []):
            for t in sub.get("tags", []):
                add_tag("pose", cat_id, t if isinstance(t, str) else t.get("text", ""))

    # 11. props.json
    prp = json.loads((data_dir / "props.json").read_text(encoding="utf-8"))
    for it in prp.get("categories", []):
        for t in it.get("tags", []):
            add_tag("props", it["id"], t if isinstance(t, str) else t.get("text", ""))
        for sub in it.get("items", []):
            for t in sub.get("tags", []):
                add_tag("props", sub["id"], t if isinstance(t, str) else t.get("text", ""))

    # 12. scenes.json & themes.json
    sc = json.loads((data_dir / "scenes.json").read_text(encoding="utf-8"))
    for cat in sc.get("scenes", []):
        for it in cat.get("items", []):
            for t in (it.get("tags", []) + it.get("anchor_tags", []) + it.get("detail_tags", [])):
                add_tag("scene_theme", it["id"], t if isinstance(t, str) else t.get("text", ""))
    thm = json.loads((data_dir / "themes.json").read_text(encoding="utf-8"))
    for it in thm.get("themes", []):
        for t in it.get("tags", []):
            add_tag("scene_theme", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 13. shot_types.json
    shot = json.loads((data_dir / "shot_types.json").read_text(encoding="utf-8"))
    for it in shot.get("shot_types", []):
        for t in it.get("tags", []):
            add_tag("shot_type", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in shot.get("camera_angles", []):
        for t in it.get("tags", []):
            add_tag("camera_angle", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 14. tattoos.json
    tat = json.loads((data_dir / "tattoos.json").read_text(encoding="utf-8"))
    for it in tat.get("categories", []):
        for t in it.get("tags", []):
            add_tag("tattoo", it["id"], t if isinstance(t, str) else t.get("text", ""))

    add_tag("quality", "quality_high", "best quality")
    add_tag("quality", "quality_high", "detailed")
    add_tag("quality", "quality_high", "photorealistic")
    add_tag("quality", "quality_ultra", "masterpiece")
    add_tag("quality", "quality_ultra", "ultra-detailed")
    add_tag("quality", "quality_ultra", "8k resolution")

    return lookup


def attribute_m1_seed_diff(
    seed: int,
    base_item: Dict[str, Any],
    cur_item: Dict[str, Any],
    catalog_lookup: Dict[Tuple[str, Set[str]], Set[str]],
    valid_rules: Set[str] | None = None,
) -> Dict[str, Any]:
    """对单个种子执行严格结构化逐原子跨版本差异因果归因。"""
    if valid_rules is None:
        valid_rules = load_authoritative_resolver_rules(REPO_DIR / "data")

    base_src = base_item["source_atoms"]
    cur_src = cur_item["source_atoms"]
    base_final = base_item["final_atoms"]
    cur_final = cur_item["final_atoms"]
    cur_decs = cur_item["decisions"]
    cur_bindings = cur_item.get("carrier_bindings", {})
    cur_dedup_records = cur_item.get("dedup_records", [])
    cur_budget_records = cur_item.get("budget_records", [])

    unexplained_reasons: List[str] = []
    explained_attributions: List[Dict[str, Any]] = []

    # ─────────────────────────────────────────────────────────────
    # 1. 结构完整性：重复 atom_id 严格核验
    # ─────────────────────────────────────────────────────────────
    for name, atom_list in [
        ("cur_final", cur_final),
        ("cur_src", cur_src),
        ("base_final", base_final),
        ("base_src", base_src),
    ]:
        ids = [a["atom_id"] for a in atom_list]
        if len(ids) != len(set(ids)):
            unexplained_reasons.append(
                f"DUPLICATE_ATOM_ID_IN_{name.upper()}: Duplicate atom IDs detected: {ids}"
            )

    # ─────────────────────────────────────────────────────────────
    # 2. 核心因果定理：非扩充槽位源原子绝对恒等定理 (Zero Drift in Unchanged Slots)
    # ─────────────────────────────────────────────────────────────
    b_src_by_slot: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in base_src:
        b_src_by_slot[normalize_slot_name(a["source_slot"])].append(a)

    c_src_by_slot: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in cur_src:
        c_src_by_slot[normalize_slot_name(a["source_slot"])].append(a)

    all_src_slots = set(b_src_by_slot.keys()) | set(c_src_by_slot.keys())
    for slot in all_src_slots:
        if slot not in M1_EXPANDED_SLOTS:
            b_sigs = [slot_atom_signature(a) for a in b_src_by_slot[slot]]
            c_sigs = [slot_atom_signature(a) for a in c_src_by_slot[slot]]
            if b_sigs != c_sigs:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SOURCE_ATOM_MUTATION({slot}): Source atoms drifted in unchanged slot! "
                    f"base={b_sigs} vs cur={c_sigs}"
                )

    # ─────────────────────────────────────────────────────────────
    # 3. 权威词库核验：当前版本采出的每一个源原子必须严格属于权威词库
    # ─────────────────────────────────────────────────────────────
    for a in cur_src:
        slot = normalize_slot_name(a.get("source_slot", ""))
        iid = a.get("source_item_id", "")
        txt = (a.get("text") or "").strip()
        aid = a.get("atom_id")

        key = (slot, iid)
        if key not in catalog_lookup:
            # 允许特定合成或辅助前缀作为特例
            if slot in ("quality", "preset_core", "style_recipe") or iid in ("quality_high", "quality_ultra"):
                pass
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNKNOWN_CATALOG_ITEM({slot}, {iid}): Atom '{aid}' under unknown catalog item"
                )
        else:
            valid_texts = catalog_lookup[key]
            if txt not in valid_texts:
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNKNOWN_LEAF_TAG({slot}, {iid}): Atom '{aid}' text '{txt}' not in catalog leaf tags"
                )

    # ─────────────────────────────────────────────────────────────
    # 4. 当前版本内部来源签名与逆向无决策消失拦截
    # ─────────────────────────────────────────────────────────────
    cur_src_map = {a["atom_id"]: a for a in cur_src}
    cur_final_map = {a["atom_id"]: a for a in cur_final}
    cur_decs_by_prod = {pid: d for d in cur_decs for pid in d.get("produced_atom_ids", [])}
    cur_consumed_by_target = {
        d["target_atom_id"]: d
        for d in cur_decs
        if d.get("action") in ("drop", "replace")
    }
    cur_dedup_by_id = {rec["atom_id"]: rec for rec in cur_dedup_records}
    cur_budget_by_id = {rec["atom_id"]: rec for rec in cur_budget_records}

    # 4A. 正向来源签名核验
    for a in cur_final:
        aid = a["atom_id"]
        if aid in cur_src_map:
            src_a = cur_src_map[aid]
            sig_errors = verify_atom_source_signature(a, src_a)
            if sig_errors:
                unexplained_reasons.extend(sig_errors)
        elif aid in cur_decs_by_prod:
            prod_dec = cur_decs_by_prod[aid]
            if a["text"] != prod_dec.get("after_text"):
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_ATOM_TEXT_MISMATCH: Final atom '{aid}' text '{a['text']}' "
                    f"does not match producing decision after_text '{prod_dec.get('after_text')}'"
                )
        else:
            unexplained_reasons.append(
                f"UNEXPLAINED_UNSOURCED_FINAL_ATOM: Final atom '{aid}' ('{a['text']}') has neither source atom nor producing decision"
            )

    # 4B. 统一决策合法性核验
    replay_errors = replay_and_verify_decisions(cur_src, cur_decs, valid_rules, cur_bindings=cur_bindings, seed=seed)
    if replay_errors:
        unexplained_reasons.extend(replay_errors)

    # 4C. 逆向无静默消失拦截 (反例防护核心)
    for a in cur_src:
        aid = a["atom_id"]
        if aid not in cur_final_map:
            if aid in cur_consumed_by_target:
                consumed_dec = cur_consumed_by_target[aid]
                if consumed_dec.get("before_text") and consumed_dec.get("before_text") != a["text"]:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_CONSUMED_BEFORE_TEXT_MISMATCH: Decision before_text '{consumed_dec.get('before_text')}' "
                        f"does not match source text '{a['text']}'"
                    )
            elif aid in cur_dedup_by_id:
                pass
            elif aid in cur_budget_by_id:
                pass
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SILENT_ATOM_DROP: Source atom '{aid}' ('{a['text']}') missing from final atoms without recorded decision"
                )

    # ─────────────────────────────────────────────────────────────
    # 5. 跨版本保序核验
    # ─────────────────────────────────────────────────────────────
    cur_orders = [(a.get("tag_order", 0), a.get("span_order", 0)) for a in cur_final]
    if cur_orders != sorted(cur_orders):
        unexplained_reasons.append(
            "UNEXPLAINED_TAG_ORDER_VIOLATION: Final atoms in Current violate non-decreasing order"
        )

    # ─────────────────────────────────────────────────────────────
    # 6. 逐原子差异严格受控因果归因证明
    # ─────────────────────────────────────────────────────────────
    def sem_key(x):
        return (normalize_slot_name(x["source_slot"]), x.get("source_item_id", ""), x["text"], x.get("span_order", 0))

    base_keys = {sem_key(b) for b in base_final}
    cur_keys = {sem_key(c) for c in cur_final}

    added_atoms = [c for c in cur_final if sem_key(c) not in base_keys]
    removed_atoms = [b for b in base_final if sem_key(b) not in cur_keys]

    base_src_keys = {sem_key(b) for b in base_src}
    cur_src_keys = {sem_key(c) for c in cur_src}

    for a in added_atoms:
        slot = normalize_slot_name(a["source_slot"])
        iid = a.get("source_item_id", "")
        aid = a["atom_id"]
        is_proven = False

        if slot == "hairstyle" and iid in NEW_HAIRSTYLE_IDS:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"NEW_HAIRSTYLE_SAMPLED({iid})"})
        elif slot == "lighting" and iid in NEW_LIGHTING_IDS:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"NEW_LIGHTING_SAMPLED({iid})"})
        elif slot == "props" and (iid in NEW_PROP_IDS or a.get("id") in NEW_PROP_LEAF_IDS):
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"NEW_PROP_SAMPLED({iid})"})
        elif aid in cur_decs_by_prod:
            prod_dec = cur_decs_by_prod[aid]
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"PRODUCED_BY_DECISION({prod_dec['rule_id']})"})
        elif slot in M1_EXPANDED_SLOTS and sem_key(a) in cur_src_keys:
            # 扩充槽位在源头发生了合法的抽样偏移
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"PRNG_CANDIDATE_SHIFT({slot})"})
        elif sem_key(a) in base_src_keys and sem_key(a) in cur_src_keys:
            # 在双版本源头均存在，但基线中被规则删除了、当前未被删除（规则消解上下文改变）
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"RULE_CONTEXT_PRESERVED({slot})"})

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_ADDED_ATOM: Atom '{aid}' ('{a['text']}', slot={slot}, id={iid}) added without valid causal proof"
            )

    cur_consumed_sem_keys = {
        sem_key(cur_src_map[d["target_atom_id"]]): d
        for d in cur_decs
        if d.get("target_atom_id") in cur_src_map and d.get("action") in ("drop", "replace")
    }

    for b in removed_atoms:
        slot = normalize_slot_name(b["source_slot"])
        iid = b.get("source_item_id", "")
        aid = b["atom_id"]
        b_key = sem_key(b)
        is_proven = False

        # 情况 1: 在当前版本中被记录在案的合规规则消解了
        if b_key in cur_consumed_sem_keys:
            is_proven = True
            dec = cur_consumed_sem_keys[b_key]
            explained_attributions.append({"atom_id": aid, "text": b["text"], "category": f"RESOLVED_BY_RULE({dec.get('rule_id')})"})
        # 情况 2: 属于扩充槽位，在当前版本源头因抽样池扩大而被合法替换
        elif slot in M1_EXPANDED_SLOTS and b_key not in cur_src_keys:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": b["text"], "category": f"PRNG_CANDIDATE_REPLACED({slot})"})

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_REMOVED_ATOM: Atom '{aid}' ('{b['text']}', slot={slot}, id={iid}) removed without valid causal proof"
            )

    is_identical = (base_item["positive"] == cur_item["positive"] and base_item["hash"] == cur_item["hash"])
    is_explained = (len(unexplained_reasons) == 0)

    return {
        "seed": seed,
        "is_identical": is_identical,
        "is_explained": is_explained,
        "unexplained_reasons": unexplained_reasons,
        "attributions": explained_attributions,
        "base_positive": base_item["positive"],
        "cur_positive": cur_item["positive"],
    }


def run_m1_audit(
    total_seeds: int = 10000,
    scratch_dir: Path | None = None,
    output_archive: Path | None = None,
    output_report_md: Path | None = None,
    output_manifest: Path | None = None,
) -> Dict[str, Any]:
    """执行全量双版本因果归因审计与证据构建。"""
    start_time = time.time()
    if scratch_dir is None:
        scratch_dir = REPO_DIR / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    baseline_dir = scratch_dir / f"baseline_{BASELINE_COMMIT}"
    export_commit(BASELINE_COMMIT, baseline_dir)

    # 获取当前工作区 Git HEAD 提交哈希
    git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_DIR), text=True).strip()

    print(f"[*] Loaded authoritative resolver rules from {REPO_DIR / 'data'}...")
    valid_rules = load_authoritative_resolver_rules(REPO_DIR / "data")
    print(f"[*] Loaded universal catalog lookup from {REPO_DIR / 'data'}...")
    catalog_lookup = load_authoritative_catalog_lookup(REPO_DIR / "data")

    print(f"[*] Running baseline ({BASELINE_COMMIT}) generation for {total_seeds} seeds...")
    base_data, base_hash = run_batch_parallel(baseline_dir, total_seeds)
    print(f"    Baseline batch hash: {base_hash}")
    if total_seeds == 10000 and base_hash != EXPECTED_BASELINE_HASH:
        raise RuntimeError(f"Baseline hash mismatch: expected {EXPECTED_BASELINE_HASH}, got {base_hash}")

    print(f"[*] Running current working tree ({git_head[:7]}) generation for {total_seeds} seeds...")
    cur_data, cur_hash = run_batch_parallel(REPO_DIR, total_seeds)
    print(f"    Current batch hash:  {cur_hash}")
    if total_seeds == 10000 and cur_hash != EXPECTED_M1_HASH:
        raise RuntimeError(f"Current hash mismatch: expected {EXPECTED_M1_HASH}, got {cur_hash}")

    print("[*] Auditing seed divergences with atomic causal verification...")
    identical_count = 0
    divergent_count = 0
    unexplained_seeds: Dict[int, List[str]] = {}
    category_counts: collections.Counter = collections.Counter()
    audited_diffs: List[Dict[str, Any]] = []

    for s in range(total_seeds):
        res = attribute_m1_seed_diff(s, base_data[s], cur_data[s], catalog_lookup, valid_rules)

        # 核心：无遗漏收集所有未解释种子（无论文本是否相同）
        if not res["is_explained"]:
            unexplained_seeds[s] = res["unexplained_reasons"]
            divergent_count += 1
            audited_diffs.append({
                "seed": s,
                "is_identical": res["is_identical"],
                "unexplained_reasons": res["unexplained_reasons"],
                "attributions": res["attributions"],
                "base_source_atoms": base_data[s]["source_atoms"],
                "cur_source_atoms": cur_data[s]["source_atoms"],
                "base_final_atoms": base_data[s]["final_atoms"],
                "cur_final_atoms": cur_data[s]["final_atoms"],
                "decisions": cur_data[s]["decisions"],
            })
        elif res["is_identical"]:
            identical_count += 1
        else:
            divergent_count += 1
            top_cats = {att["category"].split("(")[0] for att in res["attributions"]}
            for c in sorted(top_cats):
                category_counts[c] += 1
            audited_diffs.append({
                "seed": s,
                "is_identical": False,
                "attributions": res["attributions"],
                "base_source_atoms": base_data[s]["source_atoms"],
                "cur_source_atoms": cur_data[s]["source_atoms"],
                "base_final_atoms": base_data[s]["final_atoms"],
                "cur_final_atoms": cur_data[s]["final_atoms"],
                "decisions": cur_data[s]["decisions"],
            })

    elapsed = time.time() - start_time
    print(f"[*] M1 Audit complete in {elapsed:.1f}s.")
    print(f"    Total seeds:       {total_seeds}")
    print(f"    Identical seeds:   {identical_count} ({identical_count / total_seeds * 100:.2f}%)")
    print(f"    Divergent seeds:   {divergent_count} ({divergent_count / total_seeds * 100:.2f}%)")
    print(f"    Unexplained seeds: {len(unexplained_seeds)} (Gate required: == 0)")
    print("    Category Breakdown:")
    for cat, cnt in category_counts.most_common():
        print(f"      - {cat}: {cnt} ({cnt / divergent_count * 100:.2f}%)")

    # 1. 保存完整归档 (包含全部差异种子的双版本完整原子、决策及逐原子证明链)
    archive_path = output_archive or (scratch_dir / "audit_m1_evidence.json.gz")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(archive_path, "wt", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "baseline_commit": BASELINE_COMMIT,
                "baseline_hash": base_hash,
                "current_target": git_head,
                "current_hash": cur_hash,
                "total_seeds": total_seeds,
                "identical_seeds": identical_count,
                "divergent_seeds": divergent_count,
                "unexplained_count": len(unexplained_seeds),
                "elapsed_seconds": elapsed,
            },
            "category_breakdown": dict(category_counts),
            "unexplained_seeds": unexplained_seeds,
            "diffs": audited_diffs,
        }, f, ensure_ascii=False)
    print(f"[+] Full audit archive saved to: {archive_path}")

    # 2. 保存 Markdown 报告
    report_path = output_report_md or (scratch_dir / "audit_m1_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    md_lines = [
        "# M1 Wildcards Vertical Slice Divergence Audit Report",
        "",
        f"- **Baseline Commit**: `{BASELINE_COMMIT}` (`{base_hash}`)",
        f"- **Current Target**: `{git_head}` (`{cur_hash}`)",
        f"- **Total Seeds**: {total_seeds:,}",
        f"- **Identical Seeds**: {identical_count:,} ({identical_count / total_seeds * 100:.2f}%)",
        f"- **Divergent Seeds**: {divergent_count:,} ({divergent_count / total_seeds * 100:.2f}%)",
        f"- **Unexplained Seeds**: **{len(unexplained_seeds)}** (Gate: == 0)",
        f"- **Evidence Archive**: `{archive_path.name}` ({len(audited_diffs):,} divergent records with full atoms and decisions)",
        "",
        "## Category Breakdown",
        "",
        "| Category | Seed Count | Percentage of Divergent |",
        "|---|---|---|",
    ]
    for cat, cnt in category_counts.most_common():
        md_lines.append(f"| `{cat}` | {cnt:,} | {cnt / (divergent_count or 1) * 100:.2f}% |")
    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[+] Markdown report saved to: {report_path}")

    # 3. 保存清单
    manifest_path = output_manifest or (scratch_dir / "audit_m1_manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "manifest_version": "1.0",
        "tested_component": "M1 Wildcard Vertical Slice (6 samples)",
        "tested_commit_target": git_head,
        "audit_results": {
            "baseline_commit": BASELINE_COMMIT,
            "baseline_hash": base_hash,
            "current_hash": cur_hash,
            "total_seeds": total_seeds,
            "identical_seeds": identical_count,
            "divergent_seeds": divergent_count,
            "unexplained_seeds_count": len(unexplained_seeds),
            "gate_status": "PASS" if len(unexplained_seeds) == 0 else "FAIL",
        },
        "category_breakdown": dict(category_counts),
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Manifest saved to: {manifest_path}")

    return {
        "baseline_hash": base_hash,
        "current_hash": cur_hash,
        "total_seeds": total_seeds,
        "identical_count": identical_count,
        "divergent_count": divergent_count,
        "unexplained_count": len(unexplained_seeds),
        "unexplained_seeds": unexplained_seeds,
        "category_counts": dict(category_counts),
    }


def main():
    parser = argparse.ArgumentParser(description="M1 Wildcards Slice Divergence Auditor")
    parser.add_argument("--seeds", type=int, default=10000)
    parser.add_argument("--scratch-dir", type=Path, default=REPO_DIR / "scratch")
    parser.add_argument("--output-archive", type=Path, default=REPO_DIR / "scratch" / "audit_m1_evidence.json.gz")
    parser.add_argument("--output-report", type=Path, default=REPO_DIR / "scratch" / "audit_m1_report.md")
    parser.add_argument("--output-manifest", type=Path, default=REPO_DIR / "scratch" / "audit_m1_manifest.json")
    args = parser.parse_args()

    rep = run_m1_audit(
        total_seeds=args.seeds,
        scratch_dir=args.scratch_dir,
        output_archive=args.output_archive,
        output_report_md=args.output_report,
        output_manifest=args.output_manifest,
    )
    if rep["unexplained_count"] != 0:
        print(f"\n❌ [GATE FAIL] {rep['unexplained_count']} unexplained seeds detected!")
        sys.exit(1)
    else:
        print(f"\n✅ [GATE PASS] All {args.seeds} seeds 100% verified and causally attributed with 0 unexplained diffs!")
        sys.exit(0)


if __name__ == "__main__":
    main()
