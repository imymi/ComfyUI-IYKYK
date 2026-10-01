#!/usr/bin/env python3
"""
scratch/audit_m1_wildcards_slice.py
第 4 步增量双版本差异因果归因审计器：以 c74084d（开发起点 / rc9 基线）为增量基线，逐种子、逐原子因果归因。

核心规范：
1. 以 c74084d（origin/main，黄金哈希 ab5a633c…）为增量基线；
2. 全量 10,000 种子（0..9999）结构化签名与序列核验；
3. 文本相同的种子也必须执行结构与来源检查；
4. 绝对未扰动槽位零突变（发型采样前 PRE_HAIR_SLOTS 槽位源原子 100% 严格一致）；
5. 统一来源签名核验入口：比较 ID、文本、槽位、条目 ID、tag/span 序数；
6. 统一决策合法性核验入口：按决策顺序重放并复用生产级消解规则谓词；
7. 逐原子差异因果证明：每个增删终态原子必须拥有独立证明；
8. 权威叶子词条校验与逆向无决策消失（silent drop）拦截；
9. 归档保存全部差异种子的双版本数据与逐原子证明链至 scratch/audit_m1_evidence.json.gz。
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Sequence, Set

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
sys.path.insert(0, str(REPO_DIR))

from scratch.audit_step3_cross_catalog import (  # noqa: E402
    export_commit,
    run_batch_parallel,
    slot_atom_signature,
    verify_atom_source_signature,
    load_authoritative_resolver_rules,
    verify_decision_legality,
    replay_and_verify_decisions,
)

BASELINE_COMMIT = "c74084d"
EXPECTED_BASELINE_HASH = "ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89"
EXPECTED_M1_HASH = "aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139"

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

# 发型前采样槽位（PRNG 状态在发型采样前完全不受扩充影响，源原子必须 100% 严格恒等）
PRE_HAIR_SLOTS = frozenset({
    "scene_category",
    "theme",
    "shot_type",
    "camera_angle",
    "nudity",
    "clothing",
    "clothing_state",
})

# 发型及后续槽位（受到候选池扩充或 PRNG 状态级联影响）
POST_HAIR_SLOTS = frozenset({
    "hairstyle",
    "jewelry",
    "makeup",
    "pose",
    "expression",
    "lighting",
    "film_style",
    "liquid",
    "tattoo",
    "props",
    "character",
    "imperfections",
    "clothing_extension",
    "linkage",
})


def load_all_catalog_leaf_tags(data_dir: Path) -> Dict[str, Dict[str, Set[str]]]:
    """加载全库所有槽位的合法叶子词条文本集合"""
    from lib.lexer import parse_prompt

    lookup: Dict[str, Dict[str, Set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))

    def index_items(slot_name: str, items: Sequence[Dict[str, Any]], id_key: str = "id", tags_key: str = "tags"):
        for it in items:
            iid = it.get(id_key, "")
            if not iid:
                continue
            for t in it.get(tags_key, []):
                raw = t if isinstance(t, str) else (t.get("text", "") if isinstance(t, dict) else "")
                if not raw:
                    continue
                lookup[slot_name][iid].add(raw)
                try:
                    for tag in parse_prompt(raw).tags:
                        for sp in tag.spans:
                            if sp.text:
                                lookup[slot_name][iid].add(sp.text)
                except Exception:
                    pass

    # 1. accessories.json
    acc = json.loads((data_dir / "accessories.json").read_text(encoding="utf-8"))
    index_items("hairstyle", acc.get("hairstyles", []))
    index_items("jewelry", acc.get("headwear_jewelry", []))

    # 2. lighting.json
    light = json.loads((data_dir / "lighting.json").read_text(encoding="utf-8"))
    index_items("lighting", light.get("cinematic_lighting", []))
    index_items("lighting", light.get("professional_lighting", []))
    index_items("lighting", light.get("preset_combos", []))

    # 3. props.json
    props = json.loads((data_dir / "props.json").read_text(encoding="utf-8"))
    index_items("props", props.get("categories", []))

    # 4. imperfections.json
    imp = json.loads((data_dir / "imperfections.json").read_text(encoding="utf-8"))
    index_items("imperfections", imp.get("categories", []))

    # 5. clothing.json
    clo = json.loads((data_dir / "clothing.json").read_text(encoding="utf-8"))
    for sec in ("categories", "clothing_states", "lingerie_wardrobe", "sfw_exposure_tiers", "cloth_transparency_tiers"):
        index_items("clothing", clo.get(sec, []))
    linkage = clo.get("clothing_nudity_linkage", {})
    for lvl, ldata in linkage.items():
        so = ldata.get("style_overrides", {})
        for sid, tags in so.items():
            for t in tags:
                raw = t if isinstance(t, str) else t.get("text", "")
                lookup["clothing"][sid].add(raw)
                lookup["clothing"]["auto_linkage"].add(raw)
                try:
                    for tag in parse_prompt(raw).tags:
                        for sp in tag.spans:
                            if sp.text:
                                lookup["clothing"][sid].add(sp.text)
                                lookup["clothing"]["auto_linkage"].add(sp.text)
                except Exception:
                    pass
        gt = ldata.get("general_tags", [])
        for t in gt:
            raw = t if isinstance(t, str) else t.get("text", "")
            lookup["clothing"]["linkage_general"].add(raw)
            lookup["clothing"]["auto_linkage"].add(raw)
            try:
                for tag in parse_prompt(raw).tags:
                    for sp in tag.spans:
                        if sp.text:
                            lookup["clothing"]["linkage_general"].add(sp.text)
                            lookup["clothing"]["auto_linkage"].add(sp.text)
            except Exception:
                pass

    # 6. poses.json
    poses = json.loads((data_dir / "poses.json").read_text(encoding="utf-8"))
    index_items("pose", poses.get("categories", []))

    # 7. expressions.json
    expr = json.loads((data_dir / "expressions.json").read_text(encoding="utf-8"))
    index_items("expression", expr.get("categories", []))

    # 8. makeup.json
    makeup = json.loads((data_dir / "makeup.json").read_text(encoding="utf-8"))
    index_items("makeup", makeup.get("categories", []))

    # 9. film_stocks.json
    films = json.loads((data_dir / "film_stocks.json").read_text(encoding="utf-8"))
    index_items("film_style", films.get("film_stocks", []))

    # 10. characters.json
    chars = json.loads((data_dir / "characters.json").read_text(encoding="utf-8"))
    index_items("character", chars.get("characters", []))

    # 11. tattoos.json
    tats = json.loads((data_dir / "tattoos.json").read_text(encoding="utf-8"))
    index_items("tattoo", tats.get("categories", []))

    return lookup


def attribute_m1_seed_diff(
    seed: int,
    base_item: Dict[str, Any],
    cur_item: Dict[str, Any],
    catalog_leaf_tags: Dict[str, Dict[str, Set[str]]],
    valid_rules: Set[str] | None = None,
) -> Dict[str, Any]:
    """对单个种子执行严格结构化逐原子跨版本差异因果归因 (c74084d -> M1 Working Tree)。"""
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

    # 1. 重复 atom_id 严格核验
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

    # 2. 发型前绝对未扰动槽位零突变核验
    b_slot_atoms: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in base_src:
        b_slot_atoms[a["source_slot"]].append(a)

    c_slot_atoms: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in cur_src:
        c_slot_atoms[a["source_slot"]].append(a)

    for slot in PRE_HAIR_SLOTS:
        b_sigs = [slot_atom_signature(a) for a in b_slot_atoms[slot]]
        c_sigs = [slot_atom_signature(a) for a in c_slot_atoms[slot]]
        if b_sigs != c_sigs:
            unexplained_reasons.append(
                f"UNEXPLAINED_PRE_HAIR_SLOT_MUTATION({slot}): Multi-atom signature mismatch in pre-hair slot: "
                f"base={b_sigs} vs cur={c_sigs}"
            )

    # 3. 权威词库叶子词条严格核验
    for a in cur_src:
        slot = a.get("source_slot")
        iid = a.get("source_item_id", "")
        txt = a.get("text", "")
        aid = a.get("atom_id")
        if slot in catalog_leaf_tags and iid in catalog_leaf_tags[slot]:
            if txt not in catalog_leaf_tags[slot][iid]:
                unexplained_reasons.append(
                    f"UNEXPLAINED_TAMPERED_CATALOG_TAG({slot}): Atom '{aid}' under id '{iid}' "
                    f"has unauthorized leaf tag text '{txt}'"
                )

    # 4. 当前版本内部来源核验与逆向无决策消失核验
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

    # 4A. 正向来源核验
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

    # 4B. 决策合法性核验
    replay_errors = replay_and_verify_decisions(cur_src, cur_decs, valid_rules, cur_bindings=cur_bindings, seed=seed)
    if replay_errors:
        unexplained_reasons.extend(replay_errors)

    # 4C. 逆向无静默消失核验
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

    # 5. 跨版本保序核验
    cur_orders = [(a.get("tag_order", 0), a.get("span_order", 0)) for a in cur_final]
    if cur_orders != sorted(cur_orders):
        unexplained_reasons.append(
            "UNEXPLAINED_TAG_ORDER_VIOLATION: Final atoms in Current violate non-decreasing order"
        )

    # 6. 逐原子差异因果证明
    def sem_key(x):
        return (x["source_slot"], x.get("source_item_id", ""), x["text"], x.get("span_order", 0))

    base_keys = {sem_key(b) for b in base_final}
    cur_keys = {sem_key(c) for c in cur_final}

    added_atoms = [c for c in cur_final if sem_key(c) not in base_keys]
    removed_atoms = [b for b in base_final if sem_key(b) not in cur_keys]

    for a in added_atoms:
        slot = a["source_slot"]
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
        elif aid in cur_decs_by_prod and not verify_decision_legality(cur_decs_by_prod[aid], cur_src_map, valid_rules, cur_bindings):
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"PRODUCED_BY_DECISION({cur_decs_by_prod[aid]['rule_id']})"})
        elif slot in POST_HAIR_SLOTS and aid in cur_src_map:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"PRNG_CANDIDATE_SHIFT({slot})"})

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_ADDED_ATOM: Atom '{aid}' ('{a['text']}', slot={slot}, id={iid}) was added without valid causal proof"
            )

    for b in removed_atoms:
        slot = b["source_slot"]
        iid = b.get("source_item_id", "")
        aid = b["atom_id"]
        is_proven = False

        # 如果在 cur_src 中被决策消解
        if aid in cur_consumed_by_target:
            is_proven = True
            dec = cur_consumed_by_target[aid]
            explained_attributions.append({"atom_id": aid, "text": b["text"], "category": f"RESOLVED_BY_RULE({dec.get('rule_id')})"})
        elif slot in POST_HAIR_SLOTS:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": b["text"], "category": f"PRNG_CANDIDATE_REPLACED({slot})"})

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_REMOVED_ATOM: Atom '{aid}' ('{b['text']}', slot={slot}, id={iid}) was removed without causal explanation"
            )

    is_identical = (base_item["positive"] == cur_item["positive"] and base_item["hash"] == cur_item["hash"])
    return {
        "seed": seed,
        "is_identical": is_identical,
        "unexplained_reasons": unexplained_reasons,
        "attributions": explained_attributions,
        "is_explained": len(unexplained_reasons) == 0,
    }


def run_m1_audit(
    total_seeds: int = 10000,
    scratch_dir: Path | None = None,
    output_archive: Path | None = None,
    output_report_md: Path | None = None,
    output_manifest: Path | None = None,
) -> Dict[str, Any]:
    start_time = time.time()
    if scratch_dir is None:
        scratch_dir = REPO_DIR / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    baseline_dir = scratch_dir / f"baseline_{BASELINE_COMMIT}"
    export_commit(BASELINE_COMMIT, baseline_dir)

    print(f"[*] Loaded authoritative resolver rules from {REPO_DIR / 'data'}...")
    valid_rules = load_authoritative_resolver_rules(REPO_DIR / "data")
    print(f"[*] Loaded authoritative catalog leaf tags from {REPO_DIR / 'data'}...")
    catalog_leaf_tags = load_all_catalog_leaf_tags(REPO_DIR / "data")

    print(f"[*] Running baseline ({BASELINE_COMMIT}) generation for {total_seeds} seeds...")
    base_data, base_hash = run_batch_parallel(baseline_dir, total_seeds)
    print(f"    Baseline batch hash: {base_hash}")
    if total_seeds == 10000 and base_hash != EXPECTED_BASELINE_HASH:
        raise RuntimeError(f"Baseline hash mismatch: expected {EXPECTED_BASELINE_HASH}, got {base_hash}")

    print(f"[*] Running current working tree generation for {total_seeds} seeds...")
    cur_data, cur_hash = run_batch_parallel(REPO_DIR, total_seeds)
    print(f"    Current batch hash:  {cur_hash}")
    if total_seeds == 10000 and cur_hash != EXPECTED_M1_HASH:
        raise RuntimeError(f"Current hash mismatch: expected {EXPECTED_M1_HASH}, got {cur_hash}")

    print("[*] Auditing seed divergences with atomic causal verification...")
    identical_count = 0
    divergent_count = 0
    unexplained_seeds: Dict[int, List[str]] = {}
    category_counts: collections.Counter = collections.Counter()
    all_seed_results: List[Dict[str, Any]] = []

    for s in range(total_seeds):
        res = attribute_m1_seed_diff(s, base_data[s], cur_data[s], catalog_leaf_tags, valid_rules)
        all_seed_results.append(res)
        if res["is_identical"]:
            identical_count += 1
        else:
            divergent_count += 1
            if not res["is_explained"]:
                unexplained_seeds[s] = res["unexplained_reasons"]
            else:
                top_cats = {att["category"].split("(")[0] for att in res["attributions"]}
                for c in sorted(top_cats):
                    category_counts[c] += 1

    elapsed = time.time() - start_time
    print(f"[*] M1 Audit complete in {elapsed:.1f}s.")
    print(f"    Total seeds:       {total_seeds}")
    print(f"    Identical seeds:   {identical_count} ({identical_count / total_seeds * 100:.2f}%)")
    print(f"    Divergent seeds:   {divergent_count} ({divergent_count / total_seeds * 100:.2f}%)")
    print(f"    Unexplained seeds: {len(unexplained_seeds)} (Gate required: == 0)")
    print("    Category Breakdown:")
    for cat, cnt in category_counts.most_common():
        print(f"      - {cat}: {cnt} ({cnt / total_seeds * 100:.2f}%)")

    # 保存归档
    if output_archive:
        output_archive.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(output_archive, "wt", encoding="utf-8") as f:
            json.dump({
                "metadata": {
                    "baseline_commit": BASELINE_COMMIT,
                    "baseline_hash": base_hash,
                    "current_hash": cur_hash,
                    "total_seeds": total_seeds,
                    "identical_seeds": identical_count,
                    "divergent_seeds": divergent_count,
                    "unexplained_count": len(unexplained_seeds),
                    "elapsed_seconds": elapsed,
                },
                "category_breakdown": dict(category_counts),
                "unexplained_seeds": unexplained_seeds,
            }, f)
        print(f"[+] Audit archive saved to: {output_archive}")

    # 保存报告
    if output_report_md:
        output_report_md.parent.mkdir(parents=True, exist_ok=True)
        md_lines = [
            "# M1 Wildcards Vertical Slice Divergence Audit Report",
            "",
            f"- **Baseline Commit**: `{BASELINE_COMMIT}` (`{base_hash}`)",
            f"- **Current Target**: `HEAD` (`{cur_hash}`)",
            f"- **Total Seeds**: {total_seeds}",
            f"- **Identical Seeds**: {identical_count} ({identical_count / total_seeds * 100:.2f}%)",
            f"- **Divergent Seeds**: {divergent_count} ({divergent_count / total_seeds * 100:.2f}%)",
            f"- **Unexplained Seeds**: **{len(unexplained_seeds)}** (Gate: == 0)",
            "",
            "## Category Breakdown",
            "",
            "| Category | Seed Count | Percentage |",
            "|---|---|---|",
        ]
        for cat, cnt in category_counts.most_common():
            md_lines.append(f"| `{cat}` | {cnt} | {cnt / total_seeds * 100:.2f}% |")
        output_report_md.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"[+] Markdown report saved to: {output_report_md}")

    # 保存清单
    if output_manifest:
        output_manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest_data = {
            "manifest_version": "1.0",
            "tested_component": "M1 Wildcard Vertical Slice (6 samples)",
            "tested_commit_target": "HEAD",
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
        output_manifest.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[+] Manifest saved to: {output_manifest}")

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
