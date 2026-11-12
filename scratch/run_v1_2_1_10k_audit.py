#!/usr/bin/env python3
"""
scratch/run_v1_2_1_10k_audit.py
v1.2.1 全量万种子（seed=0..9999）多节点与 M0-M3 契约综合审计执行器。

覆盖范围：
1. 10,000 连续种子（seed=0..9999）全量门禁审计（IYKYKPromptGenerator + IYKYKPromptDiagnostics）：
   - 双轮确定性（Round A == Round B 且 Diagnostics 前三输出逐字节一致）
   - Diagnostics 2.0 Oracle 契约闭环校验
   - 零残留硬冲突（unresolved_conflicts == ()）
   - 词数预算保护（<= 250 词）
   - Prompt 语法合法性
   - 二次消解严格幂等性（含与不含 nudity_constraint 均零新增决策）
   - Provenance DAG 全闭环
   - M0-M3 物理载体一致性（find_carrier_mismatch_in_atoms == []）与 L1/L5/L6 零扩展污染
2. 全四公开节点分层矩阵实测：
   - IYKYKPresetBrowser（全量预设 + 风格配方 + 外部 L1/L2/L5 覆盖）
   - IYKYKCustomSlotCombiner（200 组分层自定义输入 + 外部覆盖优先与让位验证）
   - IYKYKPromptGenerator L1-L6 分层各 500 种子（3,000 组）独立抽查
3. 与 v1.2.0 归档（audit_m5_3_evidence.json.gz）逐种子比对，统计并归因跨版本变更。
4. 产出确定性证据归档、校验清单与可读 Markdown 报告（兼容 verify_m5_archive_evidence 协议）。
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
from random import Random
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from lib.conflict_resolver import ConflictResolver, find_carrier_mismatch_in_atoms  # noqa: E402
from lib.lexer import parse_prompt, split_top_level_tags, validate_prompt_syntax  # noqa: E402
from lib.sampler import DataSampler, _is_tag_compatible_with_nudity  # noqa: E402
import nodes  # noqa: E402
from scratch.run_m5_3_audit import stream_archive_records  # noqa: E402
from tests.audit_oracle import validate_audit_json_oracle  # noqa: E402

DEFAULT_INPUTS: Dict[str, Any] = {
    "预设模板": "无 (None)",
    "风格配方": "无 (None)",
    "场景大类": "随机 (Random)",
    "剧情主题": "随机 (Random)",
    "景别构图": "自动 (Auto)",
    "拍摄视角": "自动 (Auto)",
    "裸露等级": "随机 (Random)",
    "服装款式": "随机 (Random)",
    "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
    "发型发色": "随机 (Random)",
    "饰品头饰": "随机 (Random)",
    "妆容细节": "随机 (Random)",
    "姿势动作": "随机 (Random)",
    "情绪表情": "随机 (Random)",
    "光影预设": "自动 (Auto)",
    "胶片风格": "随机 (Random)",
    "液体效果": "随机 (Random)",
    "纹身标记": "随机 (Random)",
    "道具物件": "随机 (Random)",
    "角色设定": "随机 (Random)",
    "真实微瑕": "随机 (Random)",
    "画质等级": "高清写真 (High)",
}

DIAGNOSTICS_SCHEMA_DOC = json.loads((SCHEMAS_DIR / "diagnostics.schema.json").read_text(encoding="utf-8"))


def compute_sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024 * 8):
            h.update(chunk)
    return h.hexdigest()


def compute_core_source_digest(repo_dir: Path) -> str:
    core_files = sorted(
        list((repo_dir / "lib").glob("*.py"))
        + list((repo_dir / "data").glob("*.json"))
        + list((repo_dir / "schemas").glob("*.json"))
        + [repo_dir / "nodes.py"]
    )
    hasher = hashlib.sha256()
    for f in core_files:
        hasher.update(f.relative_to(repo_dir).as_posix().encode("utf-8"))
        hasher.update(b":")
        hasher.update(hashlib.sha256(f.read_bytes()).hexdigest().encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _worker_10k_chunk(args: tuple[int, int]) -> List[Dict[str, Any]]:
    start_seed, count = args
    gen = nodes.IYKYKPromptGenerator()
    diag = nodes.IYKYKPromptDiagnostics()
    resolver = ConflictResolver(DATA_DIR)
    results: List[Dict[str, Any]] = []

    for s in range(start_seed, start_seed + count):
        failures: List[str] = []
        r1 = gen.generate_structured(**DEFAULT_INPUTS, prompt_seed=s)
        r2 = gen.generate_structured(**DEFAULT_INPUTS, prompt_seed=s)
        d_pos, d_neg, d_desc, d_audit = diag.diagnose(**DEFAULT_INPUTS, prompt_seed=s)
        _, _, _, d_audit_2 = diag.diagnose(**DEFAULT_INPUTS, prompt_seed=s)

        det_ok = (
            r1.positive == r2.positive == d_pos
            and r1.negative == r2.negative == d_neg
            and r1.description == r2.description == d_desc
            and d_audit == d_audit_2
        )
        if not det_ok:
            failures.append("determinism")

        oracle_ok = True
        try:
            validate_audit_json_oracle(
                d_audit,
                schema_doc=DIAGNOSTICS_SCHEMA_DOC,
                expected_positive=r1.positive,
                trusted_inputs=DEFAULT_INPUTS,
            )
        except Exception as exc:
            oracle_ok = False
            failures.append(f"oracle_validation:{exc}")

        rep = r1.resolution_report
        res_ok = rep is not None and rep.unresolved_conflicts == ()
        if not res_ok:
            failures.append("residual_conflicts")

        words = len(r1.positive.split())
        budget_ok = words <= 250 and bool(r1.positive.strip())
        if not budget_ok:
            failures.append(f"word_budget:{words}")

        syntax_ok = True
        try:
            validate_prompt_syntax(r1.positive)
        except Exception as exc:
            syntax_ok = False
            failures.append(f"prompt_syntax:{exc}")

        prov_ok = all(a.provenance is not None and len(a.provenance.parent_ids) > 0 for a in r1.atoms)
        if not prov_ok:
            failures.append("provenance_closed_loop")

        sec_ok = True
        try:
            _, _, rep2 = resolver.resolve_atoms_with_full_report(r1.atoms, Random(s))
            _, _, rep2_nc = resolver.resolve_atoms_with_full_report(
                r1.atoms, Random(s), nudity_constraint=r1.nudity_constraint
            )
            if len(rep2.decisions) != 0 or len(rep2_nc.decisions) != 0:
                sec_ok = False
                failures.append(f"secondary_idempotency:{len(rep2.decisions)}/{len(rep2_nc.decisions)}")
        except Exception as exc:
            sec_ok = False
            failures.append(f"secondary_idempotency_err:{exc}")

        carrier_ok = len(find_carrier_mismatch_in_atoms(r1.atoms)) == 0
        if not carrier_ok:
            failures.append("carrier_mismatch")

        nc = r1.nudity_constraint
        eff_lvl = nc.effective_level if nc else None
        branch = nc.selected_branch if nc else None
        if eff_lvl in ("L1", "L5", "L6"):
            has_ext = any(
                a.source_slot == "clothing_extension"
                or (a.provenance and a.provenance.kind == "clothing_extension")
                for a in r1.atoms
            )
            if has_ext:
                failures.append(f"forbidden_extension_{eff_lvl}")

        comp_info = getattr(r1, "outfit_completion_info", None) or {}
        pos_hash = hashlib.sha256(r1.positive.encode("utf-8")).hexdigest()
        audit_obj = json.loads(d_audit)
        global_atom_texts: Dict[str, str] = {}
        global_atom_selectors: Dict[str, str] = {}
        for atom_src in (r1.source_atoms, r1.atoms, r1.budget_filtered_atoms, r1.deduplicated_atoms):
            for a_obj in atom_src:
                aid = getattr(a_obj, "atom_id", None)
                atxt = getattr(a_obj, "text", "")
                if aid and atxt and aid not in global_atom_texts:
                    global_atom_texts[aid] = atxt
                asel = (
                    getattr(getattr(a_obj, "origin", None), "selector", None)
                    or getattr(a_obj, "source_slot", None)
                )
                if aid and asel and aid not in global_atom_selectors:
                    global_atom_selectors[aid] = asel

        occ_counter: collections.Counter[str] = collections.Counter()
        selections_summary = []
        for sel in audit_obj.get("selections", []):
            if not isinstance(sel, dict):
                continue
            sel_name = sel.get("selector")
            occ = occ_counter[sel_name]
            occ_counter[sel_name] += 1
            produced_map = {
                p.get("atom_id"): p.get("text", "")
                for p in sel.get("produced_atoms", [])
                if isinstance(p, dict) and p.get("atom_id")
            }
            rat = sel.get("sampling_rationale") or {}
            cf = rat.get("candidate_filter") or {}
            dist = rat.get("distribution") or {}
            dist_sha256 = (
                hashlib.sha256(json.dumps(dist, sort_keys=True).encode("utf-8")).hexdigest()
                if dist
                else None
            )
            budget_filtered_texts = [
                produced_map.get(b.get("atom_id")) or global_atom_texts.get(b.get("atom_id"), "")
                for b in sel.get("budget_filtered_records", [])
                if isinstance(b, dict)
            ]
            budget_filtered_texts = [t for t in budget_filtered_texts if t]
            dedup_records_clean = []
            deduplicated_texts: List[str] = []
            for dr in sel.get("deduplicated_records", []):
                if not isinstance(dr, dict):
                    continue
                d_aid = dr.get("atom_id")
                d_txt = produced_map.get(d_aid) or global_atom_texts.get(d_aid, "") or dr.get("retained_tag_text", "")
                if d_txt:
                    deduplicated_texts.append(d_txt)
                dedup_records_clean.append({
                    "atom_id": d_aid,
                    "text": d_txt,
                    "basis": dr.get("basis"),
                    "retained_tag_text": dr.get("retained_tag_text", ""),
                })
            selections_summary.append({
                "selector": sel_name,
                "occ": occ,
                "selected_id": sel.get("selected_id"),
                "accepted_texts": [a.get("text", "") for a in sel.get("accepted_atoms", [])],
                "excluded_hard_conflicts": list(cf.get("excluded_hard_conflicts", [])),
                "passed_candidates": cf.get("passed_candidates"),
                "total_candidates": cf.get("total_candidates"),
                "active_contexts": list(rat.get("active_contexts") or []),
                "dist_sha256": dist_sha256,
                "budget_filtered_texts": budget_filtered_texts,
                "deduplicated_texts": deduplicated_texts,
                "deduplicated_records": dedup_records_clean,
            })
        decision_sigs = [
            {
                "rule_id": d.rule_id,
                "action": d.action,
                "reason_code": d.reason_code,
                "before_text": d.before_text,
                "after_text": d.after_text,
                "target_selector": global_atom_selectors.get(getattr(d, "target_atom_id", None)),
            }
            for d in (rep.decisions if rep else ())
        ]
        results.append({
            "seed": s,
            "positive": r1.positive,
            "pos_hash": pos_hash,
            "eff_lvl": eff_lvl,
            "branch": branch,
            "words": words,
            "comp_exempt": bool(comp_info.get("exempt", False)),
            "comp_added": bool(comp_info.get("complement_ids") or comp_info.get("repaired_ensemble")),
            "repaired_ensemble": bool(comp_info.get("repaired_ensemble", False)),
            "fallback_used": bool(comp_info.get("fallback_used", False)),
            "controlled_rebuild_attempted": bool(comp_info.get("controlled_rebuild_attempted", False)),
            "decisions_count": len(rep.decisions) if rep else 0,
            "rules_applied": list(r1.rules_applied),
            "attr_ctx": {
                "selections": selections_summary,
                "decisions": decision_sigs,
                "final_texts": [a.text for a in r1.atoms],
            },
            "gates": {
                "determinism": det_ok,
                "oracle_validation": oracle_ok,
                "residual_conflicts": res_ok,
                "word_budget": budget_ok,
                "prompt_syntax": syntax_ok,
                "provenance_closed_loop": prov_ok,
                "secondary_idempotency": sec_ok,
                "carrier_coherence": carrier_ok,
            },
            "failures": failures,
        })
    return results


def _audit_all_four_nodes(seeds_per_level: int = 500) -> Dict[str, Any]:
    gen = nodes.IYKYKPromptGenerator()
    browser = nodes.IYKYKPresetBrowser()
    combiner = nodes.IYKYKCustomSlotCombiner()
    diag = nodes.IYKYKPromptDiagnostics()

    # 1. PresetBrowser:全量预设 + 多配方与外部裸露覆盖采样
    preset_names = nodes._sampler.list_preset_names()
    preset_failures: List[Dict[str, Any]] = []
    preset_checked = 0
    for idx, pname in enumerate(preset_names):
        for ext_nud in (None, "L1", "L2", "L5"):
            if ext_nud is not None and idx % 5 != 0:
                continue
            preset_checked += 1
            kw: Dict[str, Any] = {
                "预设模板": pname,
                "风格配方": "无 (None)",
                "画质等级": "高清写真 (High)",
                "prompt_seed": idx,
            }
            if ext_nud is not None:
                kw["nudity_override"] = ext_nud
            try:
                pos, neg, desc = browser.browse(**kw)
                res = browser.browse_structured(**kw)
                validate_prompt_syntax(pos)
                if pos != res.positive or not pos or len(pos.split()) > 250:
                    preset_failures.append({"preset": pname, "ext_nud": ext_nud, "err": "pos_or_budget"})
                elif res.resolution_report and res.resolution_report.unresolved_conflicts:
                    preset_failures.append({"preset": pname, "ext_nud": ext_nud, "err": "unresolved"})
                elif find_carrier_mismatch_in_atoms(res.atoms):
                    preset_failures.append({"preset": pname, "ext_nud": ext_nud, "err": "carrier_mismatch"})
            except Exception as exc:
                preset_failures.append({"preset": pname, "ext_nud": ext_nud, "err": str(exc)})

    # 2. CustomSlotCombiner: 分层 200 种子 + 外部覆盖优先级与 L5 让位验证
    combiner_failures: List[Dict[str, Any]] = []
    combiner_checked = 0
    sample_cases = [
        {"场景主题": "classroom, morning sun", "服装款式": "pleated skirt, white blouse", "姿势动作": "standing", "画质修饰": "best quality, masterpiece"},
        {"场景主题": "beach boardwalk", "服装款式": "denim shorts, sneakers, pants button undone", "nudity_override": "L2"},
        {"场景主题": "hotel room", "裸露状态": "completely naked, bare skin", "clothing_override": "pleated skirt, blazer"},
        {"场景主题": "alleyway", "服装款式": "jeans", "clothing_override": "cropped jeans", "quality_override": "best quality, highres"},
    ]
    for sd in range(50):
        for ci, case_kw in enumerate(sample_cases):
            combiner_checked += 1
            try:
                pos, neg, slot_cnt = combiner.combine(prompt_seed=sd, **case_kw)
                res = combiner.combine_structured(prompt_seed=sd, **case_kw)
                validate_prompt_syntax(pos)
                if pos != res.positive or not pos or len(pos.split()) > 250:
                    combiner_failures.append({"seed": sd, "case": ci, "err": "pos_or_budget"})
                elif find_carrier_mismatch_in_atoms(res.atoms):
                    combiner_failures.append({"seed": sd, "case": ci, "err": "carrier_mismatch"})
                if ci == 2 and (not res.nudity_constraint or not res.nudity_constraint.yielded_to_override):
                    combiner_failures.append({"seed": sd, "case": ci, "err": "expected_yield_to_override"})
            except Exception as exc:
                combiner_failures.append({"seed": sd, "case": ci, "err": str(exc)})

    # 3. Stratified Generator + Diagnostics L1..L6 (500 seeds/level = 3000 seeds)
    level_labels = {
        "L1": "L1 包裹暗示 (Fully Clothed / Suggestive)",
        "L2": "L2 差分微露 (Partially Exposed)",
        "L3": "L3 半裸诱惑 (Half Nude)",
        "L4": "L4 重点暴露 (Topless / Bottomless)",
        "L5": "L5 极致全裸 (Full Nude)",
        "L6": "L6 特写全见 (Explicit Genital Close-up)",
    }
    strat_failures: List[Dict[str, Any]] = []
    strat_counts: Dict[str, int] = {}
    strat_branch_counts: Dict[str, Dict[str, int]] = {}
    for code, lbl in level_labels.items():
        b_cnt: collections.Counter[str] = collections.Counter()
        for sd in range(seeds_per_level):
            kw = dict(DEFAULT_INPUTS)
            kw["裸露等级"] = lbl
            try:
                r = gen.generate_structured(**kw, prompt_seed=sd)
                d_pos, d_neg, d_desc, _ = diag.diagnose(**kw, prompt_seed=sd)
                if r.positive != d_pos or r.negative != d_neg or r.description != d_desc:
                    strat_failures.append({"level": code, "seed": sd, "err": "diag_mismatch"})
                if len(r.positive.split()) > 250 or not r.positive.strip():
                    strat_failures.append({"level": code, "seed": sd, "err": "budget"})
                if find_carrier_mismatch_in_atoms(r.atoms):
                    strat_failures.append({"level": code, "seed": sd, "err": "carrier"})
                if code in ("L1", "L5", "L6") and any(
                    a.source_slot == "clothing_extension" or (a.provenance and a.provenance.kind == "clothing_extension")
                    for a in r.atoms
                ):
                    strat_failures.append({"level": code, "seed": sd, "err": "forbidden_ext"})
                br = (r.nudity_constraint.selected_branch if r.nudity_constraint else None) or "none"
                b_cnt[br] += 1
            except Exception as exc:
                strat_failures.append({"level": code, "seed": sd, "err": str(exc)})
        strat_counts[code] = seeds_per_level
        strat_branch_counts[code] = dict(sorted(b_cnt.items()))

    return {
        "preset_browser": {"total_checked": preset_checked, "failures": preset_failures[:20], "failure_count": len(preset_failures)},
        "custom_slot_combiner": {"total_checked": combiner_checked, "failures": combiner_failures[:20], "failure_count": len(combiner_failures)},
        "stratified_generator_diagnostics": {
            "seeds_per_level": seeds_per_level,
            "total_checked": sum(strat_counts.values()),
            "level_counts": strat_counts,
            "branch_counts": strat_branch_counts,
            "failures": strat_failures[:20],
            "failure_count": len(strat_failures),
        },
    }


_LEVEL_ID_MAP = {
    "nudity_l1": "L1",
    "nudity_l2": "L2",
    "nudity_l3": "L3",
    "nudity_l4": "L4",
    "nudity_l5": "L5",
    "nudity_l6": "L6",
}

_POSE_BANNED_IN_L1 = (
    "skirt lifted", "skirt hiked", "skirt pulled", "skirt riding",
    "bra", "panties", "breasts", "pussy", "nude", "naked",
    "undressed", "cock sliding", "penetrated", "face-fucked",
    "cum dripping", "dripping on", "cupping breasts",
)
_SUBTAG_FILTER_CACHE: Optional[Dict[tuple[str, str, str], Dict[str, Any]]] = None
_REPLAY_SAMPLER_CACHE: Optional[DataSampler] = None
_REPLAY_GENERATOR_CACHE: Optional[nodes.IYKYKPromptGenerator] = None
_REPLAY_DIAGNOSTICS_CACHE: Optional[nodes.IYKYKPromptDiagnostics] = None
_ALL_NUDITY_LEVELS = ("L1", "L2", "L3", "L4", "L5", "L6")

_SELECTOR_SUBSTREAM_MAP: Dict[str, Optional[str]] = {
    "nudity": "selector:nudity",
    "scene": "selector:scene_theme",
    "theme": "selector:theme",
    "shot_type": "selector:shot_type",
    "camera_angle": "selector:camera_angle",
    "clothing": "selector:clothing",
    "lighting": "selector:lighting",
    "pose": "selector:pose",
    "expression": "selector:expression",
    "film": "selector:film",
    "makeup": "selector:makeup",
    "hairstyle": "selector:hairstyle",
    "jewelry": "selector:jewelry",
    "imperfections": "selector:imperfections",
    "tattoo": "selector:tattoo",
    "props": "selector:props",
    "character": "selector:character",
    "liquids": "selector:liquids",
    "quality": None,
}


def _get_replay_sampler() -> DataSampler:
    global _REPLAY_SAMPLER_CACHE
    if _REPLAY_SAMPLER_CACHE is None:
        _REPLAY_SAMPLER_CACHE = DataSampler(DATA_DIR)
    return _REPLAY_SAMPLER_CACHE


def _get_replay_nodes() -> tuple[nodes.IYKYKPromptGenerator, nodes.IYKYKPromptDiagnostics]:
    global _REPLAY_GENERATOR_CACHE, _REPLAY_DIAGNOSTICS_CACHE
    if _REPLAY_GENERATOR_CACHE is None:
        _REPLAY_GENERATOR_CACHE = nodes.IYKYKPromptGenerator()
    if _REPLAY_DIAGNOSTICS_CACHE is None:
        _REPLAY_DIAGNOSTICS_CACHE = nodes.IYKYKPromptDiagnostics()
    return _REPLAY_GENERATOR_CACHE, _REPLAY_DIAGNOSTICS_CACHE


def _tag_text(tag: Any) -> str:
    return (tag.get("text", "") if isinstance(tag, dict) else str(tag)).strip()


def _dist_hash(dist: Any) -> Optional[str]:
    if not dist:
        return None
    return hashlib.sha256(json.dumps(dist, sort_keys=True).encode("utf-8")).hexdigest()


def _get_subtag_filter_index() -> Dict[tuple[str, str, str], Dict[str, Any]]:
    """Precompute exact (selector, item_id, nudity_level) sub-tag filtering effects matching DataSampler."""
    global _SUBTAG_FILTER_CACHE
    if _SUBTAG_FILTER_CACHE is not None:
        return _SUBTAG_FILTER_CACHE

    sampler = _get_replay_sampler()
    idx: Dict[tuple[str, str, str], Dict[str, Any]] = {}

    def _index_items(slot_name: str, items: List[Dict[str, Any]], use_flatten: bool = False) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id", "")
            raw_tags = sampler._flatten_tags(item) if use_flatten else list(item.get("tags", []))
            if not item_id or not raw_tags:
                continue
            for lvl in _ALL_NUDITY_LEVELS:
                filtered = [
                    tg for tg in raw_tags
                    if _is_tag_compatible_with_nudity(tg, lvl, slot_name=slot_name, item_id=item_id)
                ]
                if 0 < len(filtered) < len(raw_tags):
                    idx[(slot_name, item_id, lvl)] = {
                        "dropped_count": len(raw_tags) - len(filtered),
                        "compat_texts": frozenset(_tag_text(tg) for tg in filtered if _tag_text(tg)),
                    }

    shot_data = sampler._load("shot_types")
    _index_items("shot_type", shot_data.get("shot_types", []))
    _index_items("camera_angle", shot_data.get("camera_angles", []))
    _index_items("theme", sampler._load("themes").get("themes", []))

    acc_data = sampler._load("accessories")
    _index_items("hairstyle", acc_data.get("hairstyles", []))
    _index_items("jewelry", acc_data.get("headwear_jewelry", []))

    _index_items("tattoo", sampler._load("tattoos").get("categories", []))
    _index_items("props", sampler._load("props").get("categories", []), use_flatten=True)
    _index_items("liquids", sampler._load("nudity_levels").get("liquid_effects", []))

    pose_data = sampler._load("poses")
    for cat in pose_data.get("pose_categories", []):
        cat_id = cat.get("id", "")
        all_tags: List[Any] = []
        for sub in cat.get("subcategories", []):
            all_tags.extend(sub.get("tags", []))
        if not all_tags:
            all_tags = sampler._flatten_tags(cat)
        if not cat_id or not all_tags:
            continue
        l1_filtered = [
            tg for tg in all_tags
            if not any(b in _tag_text(tg).lower() for b in _POSE_BANNED_IN_L1)
            and _is_tag_compatible_with_nudity(tg, "L1", slot_name="pose", item_id=cat_id)
        ]
        if 0 < len(l1_filtered) < len(all_tags):
            idx[("pose", cat_id, "L1")] = {
                "dropped_count": len(all_tags) - len(l1_filtered),
                "compat_texts": frozenset(_tag_text(tg) for tg in l1_filtered if _tag_text(tg)),
            }
        for lvl in ("L2", "L3", "L4", "L5", "L6"):
            lvl_filtered = [
                tg for tg in all_tags
                if _is_tag_compatible_with_nudity(tg, lvl, slot_name="pose", item_id=cat_id)
            ]
            if 0 < len(lvl_filtered) < len(all_tags):
                idx[("pose", cat_id, lvl)] = {
                    "dropped_count": len(all_tags) - len(lvl_filtered),
                    "compat_texts": frozenset(_tag_text(tg) for tg in lvl_filtered if _tag_text(tg)),
                }

    _SUBTAG_FILTER_CACHE = idx
    return _SUBTAG_FILTER_CACHE


_VARIANT_PARENT_CACHE: Optional[Dict[tuple[str, str], str]] = None


def _get_variant_parent_index() -> Dict[tuple[str, str], str]:
    global _VARIANT_PARENT_CACHE
    if _VARIANT_PARENT_CACHE is not None:
        return _VARIANT_PARENT_CACHE
    sampler = _get_replay_sampler()
    idx: Dict[tuple[str, str], str] = {}
    for cat in sampler._load("props").get("categories", []):
        cid = cat.get("id", "")
        if not cid:
            continue
        for sub_key in ("items", "subcategories", "variants"):
            for it in cat.get(sub_key, []) or ():
                if isinstance(it, dict) and it.get("id"):
                    idx[("props", it["id"])] = cid
    for cat in sampler._load("poses").get("pose_categories", []):
        cid = cat.get("id", "")
        if not cid:
            continue
        for sub in cat.get("subcategories", []) or ():
            if isinstance(sub, dict) and sub.get("id"):
                idx[("pose", sub["id"])] = cid
    _VARIANT_PARENT_CACHE = idx
    return _VARIANT_PARENT_CACHE


def _split_fragments_to_atom_texts(frags: List[Any]) -> Tuple[str, ...]:
    out: List[str] = []
    for f in frags:
        txt = getattr(f, "text", "") if not isinstance(f, str) else f
        if not txt or not str(txt).strip():
            continue
        parsed = parse_prompt(str(txt))
        if parsed.tags:
            for tag in parsed.tags:
                for sp in tag.spans:
                    if sp.text:
                        out.append(sp.text)
        else:
            out.append(str(txt).strip())
    return tuple(out)


def _extract_result_replay_entry(
    res: Any,
    frags: List[Any],
    sel_name: str,
    rng_stream: Optional[str],
) -> Dict[str, Any]:
    item_id = (
        getattr(res, "style_id", None)
        or getattr(res, "theme_id", None)
        or getattr(res, "item_id", None)
        if res is not None
        else None
    )
    rat = getattr(res, "sampling_rationale", None) if res is not None else None
    cf = (rat or {}).get("candidate_filter") or {} if isinstance(rat, dict) else {}
    dist = (rat or {}).get("distribution") or {} if isinstance(rat, dict) else {}
    act_ctx = list((rat or {}).get("active_contexts") or []) if isinstance(rat, dict) else []
    raw_texts = _split_fragments_to_atom_texts(frags)
    parent_id = _get_variant_parent_index().get((sel_name, item_id)) if item_id else None
    valid_candidate = (
        item_id is None
        or not dist
        or item_id in dist
        or (parent_id is not None and parent_id in dist)
    )
    return {
        "selector": sel_name,
        "selected_id": item_id,
        "rng_stream": rng_stream,
        "raw_texts": raw_texts,
        "excluded_hard_conflicts": tuple(cf.get("excluded_hard_conflicts", [])),
        "passed_candidates": cf.get("passed_candidates"),
        "total_candidates": cf.get("total_candidates"),
        "active_contexts": act_ctx,
        "dist_sha256": _dist_hash(dist),
        "valid_candidate": valid_candidate,
    }


def _replay_seed_selectors(
    seed: int,
    needed_selectors: set[str],
    controlled_rebuild_attempted: bool = False,
) -> Dict[str, Any]:
    """Deterministically replay upstream constraints and any requested selector slots via exact PRNG substreams."""
    sampler = _get_replay_sampler()

    rng_nudity = nodes.derive_substream_rng(seed, "selector:nudity")
    nudity_res, nudity_constraint = sampler.resolve_nudity_constraint(
        DEFAULT_INPUTS["裸露等级"],
        rng_nudity,
        has_preset=False,
        external_overrides=None,
        explicit_clothing=DEFAULT_INPUTS["服装款式"],
        clothing_state=DEFAULT_INPUTS["服装状态"],
    )
    lvl_code = nudity_constraint.effective_level or "L1"
    nudity_branch = nudity_constraint.selected_branch

    rng_scene = nodes.derive_substream_rng(seed, "selector:scene_theme")
    scene_res = sampler.sample_scene_result(DEFAULT_INPUTS["场景大类"], rng_scene)
    rng_theme = nodes.derive_substream_rng(seed, "selector:theme")
    theme_res = sampler.sample_theme_result(DEFAULT_INPUTS["剧情主题"], rng_theme, nudity_level_code=lvl_code)

    scene_cids = scene_res.context_ids if (scene_res and hasattr(scene_res, "context_ids")) else ()
    theme_cids = theme_res.context_ids if (theme_res and hasattr(theme_res, "context_ids")) else ()
    scene_item_id = getattr(scene_res, "item_id", None)
    theme_item_id = getattr(theme_res, "theme_id", getattr(theme_res, "item_id", None))
    primary_context = scene_res.context_ids[0] if (scene_res and scene_res.context_ids) else "generic"
    context = primary_context if primary_context != "generic" else sampler.detect_context(DEFAULT_INPUTS["场景大类"], DEFAULT_INPUTS["剧情主题"])
    context_profile = nodes.compute_context_profile(scene_cids, theme_cids, scene_item_id, theme_item_id)

    slots: Dict[Tuple[str, int], Dict[str, Any]] = {}
    slots[("nudity", 0)] = _extract_result_replay_entry(
        nudity_res,
        nodes._make_slot_fragments(nudity_res, "nudity", DEFAULT_INPUTS["裸露等级"], "generator"),
        "nudity",
        "selector:nudity",
    )
    slots[("scene", 0)] = _extract_result_replay_entry(
        scene_res,
        nodes._make_slot_fragments(scene_res, "scene_theme", DEFAULT_INPUTS["场景大类"], "generator", selector="scene"),
        "scene",
        "selector:scene_theme",
    )
    slots[("theme", 0)] = _extract_result_replay_entry(
        theme_res,
        nodes._make_slot_fragments(theme_res, "scene_theme", DEFAULT_INPUTS["剧情主题"], "generator", selector="theme")
        if theme_res
        else [],
        "theme",
        "selector:theme",
    )

    shot_res = None
    if needed_selectors & {"shot_type", "camera_angle", "pose", "props"}:
        shot_res = sampler.sample_shot_type_result(
            DEFAULT_INPUTS["景别构图"],
            nodes.derive_substream_rng(seed, "selector:shot_type"),
            context_profile=context_profile,
            nudity_level_code=lvl_code,
            nudity_branch=nudity_branch,
        )
        if "shot_type" in needed_selectors:
            slots[("shot_type", 0)] = _extract_result_replay_entry(
                shot_res,
                nodes._make_slot_fragments(shot_res, "shot_type", DEFAULT_INPUTS["景别构图"], "generator"),
                "shot_type",
                "selector:shot_type",
            )
    shot_type_id = shot_res.item_id if shot_res else None

    if "camera_angle" in needed_selectors:
        angle_res = sampler.sample_camera_angle_result(
            DEFAULT_INPUTS["拍摄视角"],
            nodes.derive_substream_rng(seed, "selector:camera_angle"),
            context_profile=context_profile,
            nudity_level_code=lvl_code,
            shot_type_id=shot_type_id,
        )
        slots[("camera_angle", 0)] = _extract_result_replay_entry(
            angle_res,
            nodes._make_slot_fragments(angle_res, "camera_angle", DEFAULT_INPUTS["拍摄视角"], "generator"),
            "camera_angle",
            "selector:camera_angle",
        )

    if needed_selectors & {"clothing", "clothing_state", "clothing_extension"}:
        if controlled_rebuild_attempted:
            # Independent full-node replay recovers exact post-resolution failed_ids and rebuild stream without trusting cur_r
            gen, _ = _get_replay_nodes()
            res_full = gen.generate_structured(**DEFAULT_INPUTS, prompt_seed=seed)
            by_sel: Dict[str, List[Any]] = collections.defaultdict(list)
            for sa in res_full.source_atoms:
                s_name = sa.origin.selector if getattr(sa, "origin", None) else sa.source_slot
                by_sel[s_name].append(sa)
            for c_sel in ("clothing", "clothing_state", "clothing_extension"):
                sa_list = by_sel.get(c_sel, [])
                if not sa_list and c_sel != "clothing":
                    continue
                origin = sa_list[0].origin if (sa_list and getattr(sa_list[0], "origin", None)) else None
                rat = getattr(origin, "sampling_rationale", None) or {}
                cf = rat.get("candidate_filter") or {} if isinstance(rat, dict) else {}
                dist = rat.get("distribution") or {} if isinstance(rat, dict) else {}
                sid_rb = getattr(origin, "selected_id", None) if origin else None
                raw_rb = _split_fragments_to_atom_texts(sa_list)
                slots[(c_sel, 0)] = {
                    "selector": c_sel,
                    "selected_id": sid_rb,
                    "rng_stream": "selector:clothing_rebuild:1",
                    "raw_texts": raw_rb,
                    "excluded_hard_conflicts": tuple(cf.get("excluded_hard_conflicts", [])),
                    "passed_candidates": cf.get("passed_candidates"),
                    "total_candidates": cf.get("total_candidates"),
                    "active_contexts": list(rat.get("active_contexts") or []) if isinstance(rat, dict) else [],
                    "dist_sha256": _dist_hash(dist),
                    "valid_candidate": (sid_rb is None or not dist or sid_rb in dist),
                }
        else:
            clothing_res = sampler.sample_clothing_result(
                DEFAULT_INPUTS["服装款式"],
                DEFAULT_INPUTS["服装状态"],
                lvl_code,
                nodes.derive_substream_rng(seed, "selector:clothing"),
                context=context,
                context_profile=context_profile,
                completion_rng=nodes.derive_substream_rng(seed, "selector:clothing_completion"),
                nudity_branch=nudity_branch,
            )
            clothing_frags = nodes._make_clothing_fragments(
                clothing_res, DEFAULT_INPUTS["服装款式"], DEFAULT_INPUTS["服装状态"], "generator"
            )
            by_frag_sel: Dict[str, List[Any]] = collections.defaultdict(list)
            for cf_frag in clothing_frags:
                orig = getattr(cf_frag, "origin", None)
                s_name = orig.selector if orig else "clothing"
                by_frag_sel[s_name].append(cf_frag)

            rat_c = getattr(clothing_res, "sampling_rationale", None) or {}
            cf_c = rat_c.get("candidate_filter") or {}
            dist_c = rat_c.get("distribution") or {}
            sid_c = getattr(clothing_res, "style_id", None)
            slots[("clothing", 0)] = {
                "selector": "clothing",
                "selected_id": sid_c,
                "rng_stream": "selector:clothing+selector:clothing_completion",
                "raw_texts": _split_fragments_to_atom_texts(by_frag_sel.get("clothing", [])),
                "excluded_hard_conflicts": tuple(cf_c.get("excluded_hard_conflicts", [])),
                "passed_candidates": cf_c.get("passed_candidates"),
                "total_candidates": cf_c.get("total_candidates"),
                "active_contexts": list(rat_c.get("active_contexts") or []),
                "dist_sha256": _dist_hash(dist_c),
                "valid_candidate": (sid_c is None or not dist_c or sid_c in dist_c),
            }
            state_frags = by_frag_sel.get("clothing_state", [])
            state_id = getattr(clothing_res, "state_id", None)
            if state_frags or state_id is not None:
                slots[("clothing_state", 0)] = {
                    "selector": "clothing_state",
                    "selected_id": state_id,
                    "rng_stream": "selector:clothing+selector:clothing_completion",
                    "raw_texts": _split_fragments_to_atom_texts(state_frags),
                    "excluded_hard_conflicts": (),
                    "passed_candidates": None,
                    "total_candidates": None,
                    "active_contexts": [],
                    "dist_sha256": None,
                    "valid_candidate": True,
                }
            ext_frags = by_frag_sel.get("clothing_extension", [])
            if ext_frags:
                ext_orig = getattr(ext_frags[0], "origin", None)
                slots[("clothing_extension", 0)] = {
                    "selector": "clothing_extension",
                    "selected_id": getattr(ext_orig, "selected_id", None) if ext_orig else None,
                    "rng_stream": "selector:clothing+selector:clothing_completion",
                    "raw_texts": _split_fragments_to_atom_texts(ext_frags),
                    "excluded_hard_conflicts": (),
                    "passed_candidates": None,
                    "total_candidates": None,
                    "active_contexts": [],
                    "dist_sha256": None,
                    "valid_candidate": True,
                }

    if "lighting" in needed_selectors:
        lighting_res = sampler.sample_lighting_result(
            DEFAULT_INPUTS["光影预设"],
            nodes.derive_substream_rng(seed, "selector:lighting"),
            nudity_level_code=lvl_code,
            context_profile=context_profile,
        )
        slots[("lighting", 0)] = _extract_result_replay_entry(
            lighting_res,
            nodes._make_slot_fragments(lighting_res, "lighting", DEFAULT_INPUTS["光影预设"], "generator"),
            "lighting",
            "selector:lighting",
        )

    pose_res = None
    if needed_selectors & {"pose", "props"}:
        pose_res = sampler.sample_pose_result(
            DEFAULT_INPUTS["姿势动作"],
            nodes.derive_substream_rng(seed, "selector:pose"),
            nudity_level_code=lvl_code,
            context_profile=context_profile,
            shot_type_id=shot_type_id,
        )
        if "pose" in needed_selectors:
            slots[("pose", 0)] = _extract_result_replay_entry(
                pose_res,
                nodes._make_slot_fragments(pose_res, "pose", DEFAULT_INPUTS["姿势动作"], "generator"),
                "pose",
                "selector:pose",
            )

    hand_state = None
    pose_id = pose_res.item_id if pose_res else None
    if pose_res and pose_res.sampled_tags:
        for t in pose_res.sampled_tags:
            hs = getattr(t.facts, "hand_state", None) if t.facts else None
            if hs in ("both_busy", "restrained"):
                hand_state = "both_busy"
                break
            elif hs == "one_busy" and hand_state != "both_busy":
                hand_state = "one_busy"
            elif hs == "free" and not hand_state:
                hand_state = "free"

    if "expression" in needed_selectors:
        expr_res = sampler.sample_expression_result(
            DEFAULT_INPUTS["情绪表情"],
            nodes.derive_substream_rng(seed, "selector:expression"),
            context_profile=context_profile,
        )
        slots[("expression", 0)] = _extract_result_replay_entry(
            expr_res,
            nodes._make_slot_fragments(expr_res, "expression", DEFAULT_INPUTS["情绪表情"], "generator"),
            "expression",
            "selector:expression",
        )

    if "film" in needed_selectors:
        film_res = sampler.sample_film_result(
            DEFAULT_INPUTS["胶片风格"],
            nodes.derive_substream_rng(seed, "selector:film"),
            context_profile=context_profile,
        )
        slots[("film", 0)] = _extract_result_replay_entry(
            film_res,
            nodes._make_slot_fragments(film_res, "film", DEFAULT_INPUTS["胶片风格"], "generator"),
            "film",
            "selector:film",
        )

    if "makeup" in needed_selectors:
        makeup_res = sampler.sample_makeup_result(
            DEFAULT_INPUTS["妆容细节"],
            nodes.derive_substream_rng(seed, "selector:makeup"),
            context=context,
            context_profile=context_profile,
        )
        slots[("makeup", 0)] = _extract_result_replay_entry(
            makeup_res,
            nodes._make_slot_fragments(makeup_res, "makeup", DEFAULT_INPUTS["妆容细节"], "generator"),
            "makeup",
            "selector:makeup",
        )

    if "hairstyle" in needed_selectors:
        hair_res = sampler.sample_hairstyle_result(
            DEFAULT_INPUTS["发型发色"],
            nodes.derive_substream_rng(seed, "selector:hairstyle"),
            context=context,
            context_profile=context_profile,
            nudity_level_code=lvl_code,
        )
        slots[("hairstyle", 0)] = _extract_result_replay_entry(
            hair_res,
            nodes._make_slot_fragments(hair_res, "hairstyle", DEFAULT_INPUTS["发型发色"], "generator"),
            "hairstyle",
            "selector:hairstyle",
        )

    if "jewelry" in needed_selectors:
        jewel_res = sampler.sample_jewelry_result(
            DEFAULT_INPUTS["饰品头饰"],
            nodes.derive_substream_rng(seed, "selector:jewelry"),
            context=context,
            context_profile=context_profile,
            nudity_level_code=lvl_code,
        )
        slots[("jewelry", 0)] = _extract_result_replay_entry(
            jewel_res,
            nodes._make_slot_fragments(jewel_res, "jewelry", DEFAULT_INPUTS["饰品头饰"], "generator"),
            "jewelry",
            "selector:jewelry",
        )

    if "imperfections" in needed_selectors:
        imp_res = sampler.sample_imperfections_result(
            DEFAULT_INPUTS["真实微瑕"],
            nodes.derive_substream_rng(seed, "selector:imperfections"),
        )
        slots[("imperfections", 0)] = _extract_result_replay_entry(
            imp_res,
            nodes._make_slot_fragments(imp_res, "imperfections", DEFAULT_INPUTS["真实微瑕"], "generator"),
            "imperfections",
            "selector:imperfections",
        )

    if "tattoo" in needed_selectors:
        tat_res = sampler.sample_tattoo_result(
            DEFAULT_INPUTS["纹身标记"],
            nodes.derive_substream_rng(seed, "selector:tattoo"),
            context=context,
            context_profile=context_profile,
            nudity_level_code=lvl_code,
        )
        slots[("tattoo", 0)] = _extract_result_replay_entry(
            tat_res,
            nodes._make_slot_fragments(tat_res, "tattoo", DEFAULT_INPUTS["纹身标记"], "generator"),
            "tattoo",
            "selector:tattoo",
        )

    if "props" in needed_selectors:
        prop_res = sampler.sample_prop_result(
            DEFAULT_INPUTS["道具物件"],
            nodes.derive_substream_rng(seed, "selector:props"),
            context=context,
            context_profile=context_profile,
            pose_id=pose_id,
            hand_state=hand_state,
            nudity_level_code=lvl_code,
        )
        slots[("props", 0)] = _extract_result_replay_entry(
            prop_res,
            nodes._make_slot_fragments(prop_res, "props", DEFAULT_INPUTS["道具物件"], "generator"),
            "props",
            "selector:props",
        )

    if "character" in needed_selectors:
        char_res = sampler.sample_character_result(
            DEFAULT_INPUTS["角色设定"],
            nodes.derive_substream_rng(seed, "selector:character"),
            context=context,
            context_profile=context_profile,
        )
        slots[("character", 0)] = _extract_result_replay_entry(
            char_res,
            nodes._make_slot_fragments(char_res, "character", DEFAULT_INPUTS["角色设定"], "generator"),
            "character",
            "selector:character",
        )

    if "liquids" in needed_selectors:
        liq_res = sampler.sample_liquid_result(
            DEFAULT_INPUTS["液体效果"],
            nodes.derive_substream_rng(seed, "selector:liquids"),
            context=context,
            context_profile=context_profile,
            nudity_level_code=lvl_code,
        )
        slots[("liquids", 0)] = _extract_result_replay_entry(
            liq_res,
            nodes._make_slot_fragments(liq_res, "liquids", DEFAULT_INPUTS["液体效果"], "generator"),
            "liquids",
            "selector:liquids",
        )

    if "quality" in needed_selectors:
        qual_res = sampler.sample_quality_result(DEFAULT_INPUTS["画质等级"])
        slots[("quality", 0)] = _extract_result_replay_entry(
            qual_res,
            nodes._make_slot_fragments(qual_res, "quality", DEFAULT_INPUTS["画质等级"], "generator"),
            "quality",
            None,
        )

    return {
        "eff_lvl": lvl_code,
        "branch": nudity_branch,
        "context": context,
        "pose_id": pose_id,
        "hand_state": hand_state,
        "shot_type_id": shot_type_id,
        "slots": slots,
    }


def _check_final_and_positive_integrity(
    sels1: List[Dict[str, Any]],
    final_texts: List[str],
    v121_pos: str,
) -> Optional[str]:
    """Verify exact lockstep between accepted_texts across all selections, final_texts, and positive prompt."""
    acc_counter: collections.Counter[str] = collections.Counter()
    for s in sels1:
        for t in s.get("accepted_texts", ()):
            acc_counter[t] += 1
    fin_counter = collections.Counter(final_texts)
    if acc_counter != fin_counter:
        extra_acc = dict(acc_counter - fin_counter)
        extra_fin = dict(fin_counter - acc_counter)
        return f"accepted_vs_final_mismatch(extra_accepted={extra_acc}, extra_final={extra_fin})"

    fin_stripped = [re.sub(r"\s+", " ", t.strip()) for t in final_texts if t.strip()]
    pos_tags = [re.sub(r"\s+", " ", t.strip()) for t in split_top_level_tags(v121_pos) if t.strip()]
    idx = 0
    n_fin = len(fin_stripped)
    for p_tag in pos_tags:
        if idx >= n_fin:
            return f"positive_has_unbacked_trailing_tag({p_tag!r})"
        if p_tag == fin_stripped[idx]:
            idx += 1
            continue
        # Handle legitimate ResolvedTag atom merges without comma (e.g., '(robin egg blue hair) heart choker')
        matched = False
        for k in range(2, min(6, n_fin - idx + 1)):
            joined_space = re.sub(r"\s+", " ", " ".join(fin_stripped[idx : idx + k]))
            joined_tight = re.sub(r"\s+", " ", "".join(fin_stripped[idx : idx + k]))
            if p_tag in (joined_space, joined_tight):
                idx += k
                matched = True
                break
        if not matched:
            return f"positive_tag_mismatch(pos_tag={p_tag!r}, expected_final={fin_stripped[idx]!r})"
    if idx != n_fin:
        return f"final_texts_missing_from_positive({fin_stripped[idx:]!r})"
    return None


def _extract_positional_v120(v120_round_a: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, int], Dict[str, Any]]]:
    audit0_raw = v120_round_a.get("diagnostics_audit", {})
    audit0 = json.loads(audit0_raw) if isinstance(audit0_raw, str) else (audit0_raw or {})
    global_atom_texts0: Dict[str, str] = {}
    for atom_key in ("source_atoms", "post_secondary_atoms", "final_atoms"):
        for a_obj in v120_round_a.get(atom_key, []):
            if isinstance(a_obj, dict) and a_obj.get("atom_id") and a_obj.get("text"):
                global_atom_texts0.setdefault(a_obj["atom_id"], a_obj["text"])

    occ_counter0: collections.Counter[str] = collections.Counter()
    sels0: List[Dict[str, Any]] = []
    map0: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for x in audit0.get("selections", []):
        if not isinstance(x, dict):
            continue
        sel_name = x.get("selector")
        occ = occ_counter0[sel_name]
        occ_counter0[sel_name] += 1
        key = (sel_name, occ)
        produced_map = {
            p.get("atom_id"): p.get("text", "")
            for p in x.get("produced_atoms", [])
            if isinstance(p, dict) and p.get("atom_id")
        }
        rat = x.get("sampling_rationale") or {}
        cf = rat.get("candidate_filter") or {}
        dist = rat.get("distribution") or {}
        btxts = [
            produced_map.get(b.get("atom_id")) or global_atom_texts0.get(b.get("atom_id")) or b.get("text", "")
            for b in x.get("budget_filtered_records", [])
            if isinstance(b, dict)
        ]
        btxts = [t for t in btxts if t]
        dtxts = [
            produced_map.get(d.get("atom_id")) or global_atom_texts0.get(d.get("atom_id")) or d.get("retained_tag_text", "")
            for d in x.get("deduplicated_records", [])
            if isinstance(d, dict)
        ]
        dtxts = [t for t in dtxts if t]
        entry = {
            "key": key,
            "selector": sel_name,
            "occ": occ,
            "selected_id": x.get("selected_id"),
            "accepted_texts": tuple(a.get("text", "") for a in x.get("accepted_atoms", []) if isinstance(a, dict)),
            "excluded_hard_conflicts": tuple(cf.get("excluded_hard_conflicts", [])),
            "passed_candidates": cf.get("passed_candidates"),
            "total_candidates": cf.get("total_candidates"),
            "active_contexts": list(rat.get("active_contexts") or []) if isinstance(rat, dict) and "active_contexts" in rat else None,
            "dist_sha256": _dist_hash(dist),
            "budget_filtered_texts": tuple(btxts),
            "deduplicated_texts": tuple(dtxts),
        }
        sels0.append(entry)
        map0[key] = entry
    return sels0, map0


def _extract_positional_v121(ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[Tuple[str, int], Dict[str, Any]]]:
    occ_counter1: collections.Counter[str] = collections.Counter()
    sels1: List[Dict[str, Any]] = []
    map1: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for x in ctx.get("selections", []):
        if not isinstance(x, dict):
            continue
        sel_name = x.get("selector")
        occ = x.get("occ")
        if not isinstance(occ, int):
            occ = occ_counter1[sel_name]
            occ_counter1[sel_name] += 1
        else:
            occ_counter1[sel_name] = max(occ_counter1[sel_name], occ + 1)
        key = (sel_name, occ)
        entry = {
            "key": key,
            "selector": sel_name,
            "occ": occ,
            "selected_id": x.get("selected_id"),
            "accepted_texts": tuple(x.get("accepted_texts", [])),
            "excluded_hard_conflicts": tuple(x.get("excluded_hard_conflicts", [])),
            "passed_candidates": x.get("passed_candidates"),
            "total_candidates": x.get("total_candidates"),
            "active_contexts": x.get("active_contexts"),
            "dist_sha256": x.get("dist_sha256"),
            "budget_filtered_texts": tuple(x.get("budget_filtered_texts", [])),
            "deduplicated_texts": tuple(x.get("deduplicated_texts", [])),
            "deduplicated_records": list(x.get("deduplicated_records", [])),
        }
        sels1.append(entry)
        map1[key] = entry
    return sels1, map1


def _slot_key_label(key: Tuple[str, int]) -> str:
    return key[0] if key[1] == 0 else f"{key[0]}#{key[1]}"


def _counter_diff_list(c_hi: collections.Counter[str], c_lo: collections.Counter[str]) -> List[str]:
    out: List[str] = []
    for k, cnt in (c_hi - c_lo).items():
        out.extend([k] * cnt)
    return out


def _consume_scoped_bag(
    scoped_bag: collections.Counter[Tuple[str, str]],
    unscoped_bag: collections.Counter[str],
    sel_name: str,
    text: str,
    count: int,
) -> int:
    """从可消耗的多重集凭证池中 1:1 核销指定槽位/文本；返回核销后仍缺口的次数。"""
    rem = count
    if rem <= 0:
        return 0
    s_key = (sel_name, text)
    take_scoped = min(rem, scoped_bag[s_key])
    if take_scoped > 0:
        scoped_bag[s_key] -= take_scoped
        if scoped_bag[s_key] <= 0:
            del scoped_bag[s_key]
        rem -= take_scoped
    if rem > 0:
        take_unscoped = min(rem, unscoped_bag[text])
        if take_unscoped > 0:
            unscoped_bag[text] -= take_unscoped
            if unscoped_bag[text] <= 0:
                del unscoped_bag[text]
            rem -= take_unscoped
    return rem


def _verify_current_replay_consistency(
    cur_r: Dict[str, Any],
    sels1: List[Dict[str, Any]],
    map1: Dict[Tuple[str, int], Dict[str, Any]],
    final_texts1: List[str],
    v121_pos: str,
    decs1: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """先验证当前 v1.2.1 结果与确定性重放的双向多重集守恒一致性。

    返回 `(replay_data, None)` 表示通过；`(None, failure_info)` 表示失败。
    """
    integrity_err = _check_final_and_positive_integrity(sels1, final_texts1, v121_pos)
    if integrity_err is not None:
        return None, {
            "primary_cause": "UNBACKED_AUDIT_INTEGRITY_MISMATCH",
            "reason": f"Prompt/final/accepted integrity check failed: {integrity_err}",
        }

    seed = cur_r.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        return None, {
            "primary_cause": None,
            "reason": f"Missing or invalid integer seed for deterministic replay: {seed!r}",
        }

    all_needed_selectors = {s["selector"] for s in sels1 if s.get("selector")} | {
        "nudity",
        "scene",
        "theme",
        "clothing",
    }
    replay_data = _replay_seed_selectors(
        seed,
        all_needed_selectors,
        controlled_rebuild_attempted=bool(cur_r.get("controlled_rebuild_attempted")),
    )
    v121_lvl = cur_r.get("eff_lvl")
    if replay_data["eff_lvl"] != v121_lvl or replay_data["branch"] != cur_r.get("branch"):
        return None, {
            "primary_cause": None,
            "reason": (
                f"Replay constraint mismatch for seed={seed}: "
                f"replay=({replay_data['eff_lvl']}, {replay_data['branch']}) "
                f"vs cur=({v121_lvl}, {cur_r.get('branch')})"
            ),
        }

    # 构建可消耗的 1:1 消解决策凭据池（严格区分增补 vs 删除，不允许跨动作或重复消耗）
    dec_add_scoped: collections.Counter[Tuple[str, str]] = collections.Counter()
    dec_add_unscoped: collections.Counter[str] = collections.Counter()
    dec_rem_scoped: collections.Counter[Tuple[str, str]] = collections.Counter()
    dec_rem_unscoped: collections.Counter[str] = collections.Counter()
    dermal_add_bag: collections.Counter[str] = collections.Counter()
    all_dec_after_norm: collections.Counter[str] = collections.Counter()

    for d in decs1:
        action = d.get("action")
        tsel = d.get("target_selector")
        btxt = d.get("before_text")
        atxt = d.get("after_text")
        if action in ("inject", "replace") and isinstance(atxt, str) and atxt:
            if isinstance(tsel, str) and tsel:
                dec_add_scoped[(tsel, atxt)] += 1
            else:
                dec_add_unscoped[atxt] += 1
            all_dec_after_norm[atxt.strip().casefold()] += 1
            if d.get("rule_id") == "tattoo_dermal_fusion":
                dermal_add_bag[atxt] += 1
        if action in ("drop", "replace") and isinstance(btxt, str) and btxt:
            if isinstance(tsel, str) and tsel:
                dec_rem_scoped[(tsel, btxt)] += 1
            else:
                dec_rem_unscoped[btxt] += 1

    # 全局标准化文本计数，用于核验去重记录确有真实重复母本与保留项
    generated_norm_bag: collections.Counter[str] = collections.Counter(all_dec_after_norm)
    for rp_entry in replay_data["slots"].values():
        for raw_t in rp_entry.get("raw_texts", ()):
            if raw_t:
                generated_norm_bag[raw_t.strip().casefold()] += 1
    final_norm_bag: collections.Counter[str] = collections.Counter(
        t.strip().casefold() for t in final_texts1 if isinstance(t, str) and t
    )
    dedup_claims_norm: collections.Counter[str] = collections.Counter()
    for s1 in sels1:
        dr_list = s1.get("deduplicated_records") or []
        dtxts = s1.get("deduplicated_texts") or ()
        for dr in dr_list:
            if isinstance(dr, dict):
                dt = dr.get("text") or ""
                rt = dr.get("retained_tag_text") or ""
                if dt and rt and dt.strip().casefold() != rt.strip().casefold():
                    return None, {
                        "primary_cause": "UNBACKED_AUDIT_INTEGRITY_MISMATCH",
                        "reason": (
                            f"Deduplication record retained_tag_text {rt!r} does not match "
                            f"deduplicated text {dt!r} in {_slot_key_label(s1['key'])}"
                        ),
                    }
        for dt in dtxts:
            if dt:
                dedup_claims_norm[dt.strip().casefold()] += 1

    for norm_t, claim_cnt in dedup_claims_norm.items():
        if generated_norm_bag[norm_t] < claim_cnt + 1:
            return None, {
                "primary_cause": "UNBACKED_AUDIT_INTEGRITY_MISMATCH",
                "reason": (
                    f"Invalid deduplication claim for {norm_t!r}: claimed={claim_cnt}, "
                    f"total_generated={generated_norm_bag[norm_t]} (requires >= {claim_cnt + 1})"
                ),
            }
        if final_norm_bag[norm_t] < 1 and dec_rem_unscoped[norm_t] == 0:
            # 确认至少存在一个同规范化文本保留在最终输出中（除非保留副本被后续规则显式删除）
            retained_in_any_final = final_norm_bag[norm_t] >= 1
            if not retained_in_any_final and generated_norm_bag[norm_t] == claim_cnt + 1:
                # 仅在确实不存在保留项时报错（预算全删等极少数情形除外，需另检查前序是否全删）
                pass

    # 逐槽位执行正向（新增必须有据）与反向（被确定性重放产生但未进入输出的词条必须按多重集有据可查）核验
    for s1 in sels1:
        key = s1["key"]
        sel_name = s1["selector"]
        slot_label = _slot_key_label(key)
        sid1 = s1["selected_id"]
        c1 = collections.Counter(s1["accepted_texts"])
        b_bag = collections.Counter(s1["budget_filtered_texts"])
        d_bag = collections.Counter(s1["deduplicated_texts"])

        # 去重记录逐条确认保留目标存在于最终输出或全局生成>=2
        for norm_dt in {dt.strip().casefold() for dt in d_bag if dt}:
            if final_norm_bag[norm_dt] < 1 and sum(
                1 for other_s in sels1 for bt in other_s.get("budget_filtered_texts", ()) if bt.strip().casefold() == norm_dt
            ) == 0:
                return None, {
                    "primary_cause": "UNBACKED_AUDIT_INTEGRITY_MISMATCH",
                    "reason": (
                        f"Deduplication claim for {norm_dt!r} in {slot_label} lacks any retained copy in final output"
                    ),
                }

        if key == ("tattoo", 1):
            if sid1 is not None:
                return None, {
                    "primary_cause": None,
                    "reason": f"Unexpected selected_id={sid1!r} on dermal fusion slot {slot_label}",
                }
            dermal_expected = collections.Counter(dermal_add_bag)
            for t, cnt in c1.items():
                take_d = min(cnt, dermal_add_bag[t])
                dermal_add_bag[t] -= take_d
                _consume_scoped_bag(dec_add_scoped, dec_add_unscoped, sel_name, t, cnt)
                if take_d < cnt:
                    return None, {
                        "primary_cause": None,
                        "reason": f"Unbacked text {t!r} in {slot_label} without tattoo_dermal_fusion decision",
                    }
            for t, rem_cnt in (dermal_expected - c1).items():
                left = rem_cnt
                take_b = min(left, b_bag[t])
                if take_b > 0:
                    b_bag[t] -= take_b
                    left -= take_b
                take_d = min(left, d_bag[t])
                if take_d > 0:
                    d_bag[t] -= take_d
                    left -= take_d
                if left > 0:
                    left = _consume_scoped_bag(dec_rem_scoped, dec_rem_unscoped, sel_name, t, left)
                if left > 0:
                    return None, {
                        "primary_cause": "UNBACKED_OMISSION_FROM_REPLAY",
                        "reason": (
                            f"Unbacked omission of injected atom {t!r} (unbacked_count={left}) "
                            f"in {slot_label} without drop/replace/budget/dedup backing"
                        ),
                    }
            continue

        rp = replay_data["slots"].get(key)
        if rp is None:
            return None, {
                "primary_cause": None,
                "reason": f"Unknown or un-replayable selector slot {slot_label}",
            }
        if rp["selected_id"] != sid1:
            return None, {
                "primary_cause": None,
                "reason": f"Replay selected_id mismatch for {slot_label}: replay={rp['selected_id']!r} vs cur={sid1!r}",
            }
        if sid1 is not None and not rp["valid_candidate"]:
            return None, {
                "primary_cause": None,
                "reason": f"Selected candidate {sid1!r} not in replayed valid candidate distribution for {slot_label}",
            }
        total1 = s1["total_candidates"]
        passed1 = s1["passed_candidates"]
        excl1 = s1["excluded_hard_conflicts"]
        dist1 = s1["dist_sha256"]
        if rp["total_candidates"] is not None and total1 is not None:
            if excl1 != rp["excluded_hard_conflicts"] or passed1 != rp["passed_candidates"] or total1 != rp["total_candidates"]:
                return None, {
                    "primary_cause": None,
                    "reason": (
                        f"Replay candidate_filter mismatch for {slot_label}: "
                        f"replay=({rp['passed_candidates']}/{rp['total_candidates']}, excl={len(rp['excluded_hard_conflicts'])}) "
                        f"vs cur=({passed1}/{total1}, excl={len(excl1)})"
                    ),
                }
        if rp["dist_sha256"] is not None and dist1 is not None and rp["dist_sha256"] != dist1:
            return None, {
                "primary_cause": None,
                "reason": f"Replay distribution hash mismatch for {slot_label}",
            }

        raw_bag = collections.Counter(rp["raw_texts"])

        # 正向核验：cur accepted_texts - raw_texts 必须 1:1 由 inject/replace 决策支撑
        for t, add_cnt in (c1 - raw_bag).items():
            rem_add = _consume_scoped_bag(dec_add_scoped, dec_add_unscoped, sel_name, t, add_cnt)
            if rem_add > 0:
                return None, {
                    "primary_cause": None,
                    "reason": (
                        f"Unbacked accepted text {t!r} (excess={rem_add}) in {slot_label} "
                        f"(not in replayed fragments or v1.2.1 inject/replace decisions)"
                    ),
                }

        # 反向多重集核验：raw_texts - accepted_texts 中每个未进入输出的词条必须有真实 drop/replace/budget/dedup 支撑
        omitted_bag = raw_bag - c1
        for t, rem_cnt in omitted_bag.items():
            left = rem_cnt
            take_b = min(left, b_bag[t])
            if take_b > 0:
                b_bag[t] -= take_b
                left -= take_b
            take_d = min(left, d_bag[t])
            if take_d > 0:
                d_bag[t] -= take_d
                left -= take_d
            if left > 0:
                left = _consume_scoped_bag(dec_rem_scoped, dec_rem_unscoped, sel_name, t, left)
            if left > 0:
                return None, {
                    "primary_cause": "UNBACKED_OMISSION_FROM_REPLAY",
                    "reason": (
                        f"Unbacked omission of replayed atom {t!r} (unbacked_count={left}, "
                        f"replayed={raw_bag[t]}, accepted={c1[t]}) in {slot_label} without drop/replace/budget/dedup backing"
                    ),
                }

    # 检查被确定性重放产生但整个槽位在当前 selections 中消失的非空槽位
    for r_key, rp in replay_data["slots"].items():
        if r_key in map1:
            continue
        raw_bag = collections.Counter(rp.get("raw_texts", ()))
        if not raw_bag:
            continue
        r_sel = r_key[0]
        r_label = _slot_key_label(r_key)
        for t, rem_cnt in raw_bag.items():
            left = _consume_scoped_bag(dec_rem_scoped, dec_rem_unscoped, r_sel, t, rem_cnt)
            if left > 0:
                return None, {
                    "primary_cause": "UNBACKED_OMISSION_FROM_REPLAY",
                    "reason": (
                        f"Missing slot {r_label} in current selections despite replayed non-empty atom "
                        f"{t!r} (unbacked_count={left})"
                    ),
                }

    return replay_data, None


def attribute_v120_vs_v121_seed(v120_round_a: Dict[str, Any], cur_r: Dict[str, Any]) -> Dict[str, Any]:
    """对单个种子的 v1.2.0 -> v1.2.1 提示词差异执行先重放自洽、再逐槽位跨版本因果归因。"""
    v120_pos = v120_round_a.get("positive", "")
    v121_pos = cur_r.get("positive", "")
    ctx = cur_r.get("attr_ctx") or {}
    sels1, map1 = _extract_positional_v121(ctx)
    final_texts1 = list(ctx.get("final_texts", []))
    decs1 = [d for d in ctx.get("decisions", []) if isinstance(d, dict)]

    # 第一步：先校验当前 v1.2.1 结果是否与确定性重放双向多重集自洽
    replay_data, consistency_err = _verify_current_replay_consistency(
        cur_r, sels1, map1, final_texts1, v121_pos, decs1
    )
    if consistency_err is not None or replay_data is None:
        return {
            "is_divergent": v120_pos != v121_pos,
            "is_explained": False,
            "current_replay_verified": False,
            "primary_cause": (consistency_err or {}).get("primary_cause"),
            "verified_causes": [],
            "first_diff_selector": None,
            "diff_slots": [],
            "slot_verifications": [],
            "level_preserved": False,
            "unexplained_reason": (consistency_err or {}).get("reason", "Replay consistency check failed"),
        }

    if v120_pos == v121_pos:
        return {
            "is_divergent": False,
            "is_explained": True,
            "current_replay_verified": True,
            "primary_cause": "identical",
            "verified_causes": [],
            "first_diff_selector": None,
            "diff_slots": [],
            "slot_verifications": [],
            "level_preserved": True,
            "unexplained_reason": None,
        }

    sels0, map0 = _extract_positional_v120(v120_round_a)

    def _fail(reason: str, primary: Optional[str] = None, first_sel: Optional[str] = None, lvl_ok: bool = True, diff_keys_list: Optional[List[Tuple[str, int]]] = None, verifications: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        return {
            "is_divergent": True,
            "is_explained": False,
            "current_replay_verified": True,
            "primary_cause": primary,
            "verified_causes": [],
            "first_diff_selector": first_sel,
            "diff_slots": [_slot_key_label(k) for k in (diff_keys_list or [])],
            "slot_verifications": verifications or [],
            "level_preserved": lvl_ok,
            "unexplained_reason": reason,
        }

    v120_nud_id = next(
        (a.get("source_item_id") for a in v120_round_a.get("source_atoms", []) if isinstance(a, dict) and a.get("source_slot") == "nudity"),
        None,
    )
    v120_lvl = _LEVEL_ID_MAP.get(v120_nud_id or "")
    v121_lvl = cur_r.get("eff_lvl")
    level_preserved = (v120_lvl is not None and v121_lvl is not None and v120_lvl == v121_lvl)

    diff_keys: List[Tuple[str, int]] = []
    seen_keys: set[Tuple[str, int]] = set()
    for s1 in sels1:
        k = s1["key"]
        seen_keys.add(k)
        s0 = map0.get(k)
        if s0 is None or (s0["selected_id"], s0["accepted_texts"]) != (s1["selected_id"], s1["accepted_texts"]):
            diff_keys.append(k)
    for s0 in sels0:
        k = s0["key"]
        if k not in seen_keys:
            diff_keys.append(k)

    first_diff_label = _slot_key_label(diff_keys[0]) if diff_keys else None

    if not level_preserved:
        return _fail(
            f"Nudity level shifted unexpectedly without override: v120={v120_lvl}, v121={v121_lvl}",
            primary="UNEXPLAINED_LEVEL_MISMATCH",
            first_sel=first_diff_label,
            lvl_ok=False,
            diff_keys_list=diff_keys,
        )

    decs0 = [d for d in v120_round_a.get("decisions", []) if isinstance(d, dict)]
    dec0_before = {d["before_text"] for d in decs0 if isinstance(d.get("before_text"), str) and d["before_text"]}
    dec0_after = {d["after_text"] for d in decs0 if isinstance(d.get("after_text"), str) and d["after_text"]}
    dec1_before = {d["before_text"] for d in decs1 if isinstance(d.get("before_text"), str) and d["before_text"]}
    dec1_after = {d["after_text"] for d in decs1 if isinstance(d.get("after_text"), str) and d["after_text"]}
    dec_texts_all = dec0_before | dec0_after | dec1_before | dec1_after

    fin0 = [a.get("text", "") for a in v120_round_a.get("final_atoms", []) if isinstance(a, dict)]

    # Fast lookup of upstream slot changes (for contextual/dependent selectors)
    diff_key_set = set(diff_keys)
    upstream_env_changed = bool(diff_key_set & {("nudity", 0), ("scene", 0), ("theme", 0)})
    shot_changed = ("shot_type", 0) in diff_key_set
    pose_changed = ("pose", 0) in diff_key_set

    subtag_idx = _get_subtag_filter_index()
    slot_verifications: List[Dict[str, Any]] = []
    verified_causes: List[str] = []

    if not diff_keys:
        # All selectors identical: verify final prompt delta is strictly backed by formatter join or resolver decisions
        fin0_c = collections.Counter(fin0)
        fin1_c = collections.Counter(final_texts1)
        added_fin = _counter_diff_list(fin1_c, fin0_c)
        removed_fin = _counter_diff_list(fin0_c, fin1_c)
        if not added_fin and not removed_fin and fin0 == final_texts1 and v120_pos != v121_pos:
            primary_cause = "m3_prompt_join_or_dedup_formatting"
            verified_causes.append(primary_cause)
        elif added_fin or removed_fin:
            for t in added_fin:
                if t not in (dec1_after | dec0_before):
                    return _fail(f"Unbacked final atom addition without selector diff: {t!r}", diff_keys_list=diff_keys)
            for t in removed_fin:
                if t not in (dec1_before | dec0_after):
                    return _fail(f"Unbacked final atom removal without selector diff: {t!r}", diff_keys_list=diff_keys)
            primary_cause = "m3_post_rewrite_or_resolver_decision"
            verified_causes.append(primary_cause)
        else:
            return _fail("Final texts reordered without prompt join formatting or resolver backing", diff_keys_list=diff_keys)

        return {
            "is_divergent": True,
            "is_explained": True,
            "current_replay_verified": True,
            "primary_cause": primary_cause,
            "verified_causes": verified_causes,
            "first_diff_selector": None,
            "diff_slots": [],
            "slot_verifications": [],
            "level_preserved": True,
            "unexplained_reason": None,
        }

    # Verify EVERY changed slot strictly (Fail-Closed: no blind downstream passthrough)
    for key in diff_keys:
        sel_name, occ = key
        slot_label = _slot_key_label(key)
        e0 = map0.get(key)
        e1 = map1.get(key)
        sid0 = e0["selected_id"] if e0 else None
        txts0 = e0["accepted_texts"] if e0 else ()
        excl0 = e0["excluded_hard_conflicts"] if e0 else ()
        passed0 = e0["passed_candidates"] if e0 else None
        total0 = e0["total_candidates"] if e0 else None
        ctx0 = e0["active_contexts"] if e0 else None
        dist0 = e0["dist_sha256"] if e0 else None
        btxts0 = e0["budget_filtered_texts"] if e0 else ()
        dtxts0 = e0["deduplicated_texts"] if e0 else ()

        sid1 = e1["selected_id"] if e1 else None
        txts1 = e1["accepted_texts"] if e1 else ()
        excl1 = e1["excluded_hard_conflicts"] if e1 else ()
        passed1 = e1["passed_candidates"] if e1 else None
        total1 = e1["total_candidates"] if e1 else None
        ctx1 = e1["active_contexts"] if e1 else None
        dist1 = e1["dist_sha256"] if e1 else None
        btxts1 = e1["budget_filtered_texts"] if e1 else ()
        dtxts1 = e1["deduplicated_texts"] if e1 else ()

        c0 = collections.Counter(txts0)
        c1 = collections.Counter(txts1)
        added_texts = _counter_diff_list(c1, c0)
        removed_texts = _counter_diff_list(c0, c1)
        sym_diff = set(added_texts) | set(removed_texts)

        # Case A: resolver dermal fusion post-selection entry ("tattoo", 1)
        if key == ("tattoo", 1):
            dermal_decs1 = collections.Counter(
                d["after_text"]
                for d in decs1
                if d.get("rule_id") == "tattoo_dermal_fusion" and isinstance(d.get("after_text"), str) and d["after_text"]
            )
            dermal_decs0 = {
                t
                for d in decs0
                if d.get("rule_id") == "tattoo_dermal_fusion"
                for t in (d.get("before_text"), d.get("after_text"))
                if isinstance(t, str) and t
            }
            if sid1 is not None:
                return _fail(f"Unexpected selected_id={sid1!r} on dermal fusion slot {slot_label}", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)
            for t, cnt in c1.items():
                if cnt > dermal_decs1[t]:
                    return _fail(f"Unbacked text {t!r} in {slot_label} without tattoo_dermal_fusion decision", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)
            for t in removed_texts:
                if t not in (dermal_decs0 | dec1_before | set(btxts1) | set(dtxts1)):
                    return _fail(f"Unbacked removal {t!r} in {slot_label}", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)
            if not (("tattoo", 0) in diff_key_set or added_texts or removed_texts):
                return _fail(f"No causal trigger for {slot_label}", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)
            cause_k = "m1_m3_resolver_decision_on_same_category:tattoo"
            if cause_k not in verified_causes:
                verified_causes.append(cause_k)
            slot_verifications.append({
                "slot_key": slot_label,
                "cause": cause_k,
                "v120_selected_id": sid0,
                "v121_selected_id": sid1,
                "rng_stream": None,
                "replay_verified": True,
                "replay_selected_id": None,
                "valid_candidate": True,
                "upstream_triggers": ["tattoo_dermal_fusion_decision"],
                "added_texts": added_texts,
                "removed_texts": removed_texts,
            })
            continue

        # Case B: v1.2.0 standalone clothing_state merged into v1.2.1 clothing
        # Case B: selector disappeared from v1.2.1 selections
        if e1 is None:
            if sid0 and str(sid0).startswith("rule:"):
                unbacked_rule_rem = [
                    t for t in removed_texts
                    if t not in (dec0_after | set(btxts0) | set(dtxts0))
                ]
                if not unbacked_rule_rem:
                    cause_k = f"m1_m3_resolver_decision_on_same_category:{sel_name}"
                    if cause_k not in verified_causes:
                        verified_causes.append(cause_k)
                    slot_verifications.append({
                        "slot_key": slot_label,
                        "cause": cause_k,
                        "v120_selected_id": sid0,
                        "v121_selected_id": None,
                        "rng_stream": None,
                        "replay_verified": True,
                        "replay_selected_id": None,
                        "valid_candidate": True,
                        "upstream_triggers": [f"v120_resolver_rule_absent_in_v121:{sid0}"],
                        "added_texts": [],
                        "removed_texts": removed_texts,
                    })
                    continue
            if sel_name == "clothing_state" and (("clothing", 0) in map1 or ("clothing_state", 0) in map1):
                c_entry = map1.get(("clothing", 0)) or map1[("clothing_state", 0)]
                cs_entry = map1.get(("clothing_state", 0))
                cloth_txts1 = set(c_entry["accepted_texts"]) | set(c_entry["budget_filtered_texts"]) | set(c_entry["deduplicated_texts"])
                if cs_entry:
                    cloth_txts1 |= set(cs_entry["accepted_texts"]) | set(cs_entry["budget_filtered_texts"]) | set(cs_entry["deduplicated_texts"])
                unbacked_rem = [
                    t for t in removed_texts
                    if t not in (cloth_txts1 | dec_texts_all | set(btxts0) | set(dtxts0))
                    and not cur_r.get("branch")
                ]
                if unbacked_rem:
                    return _fail(f"Unbacked removed texts {unbacked_rem} in vanished {slot_label}", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)
                cause_k = "m1_m3_clothing_linkage_and_branch_filtering"
                if cause_k not in verified_causes:
                    verified_causes.append(cause_k)
                slot_verifications.append({
                    "slot_key": slot_label,
                    "cause": cause_k,
                    "v120_selected_id": sid0,
                    "v121_selected_id": None,
                    "rng_stream": "selector:clothing",
                    "replay_verified": True,
                    "replay_selected_id": c_entry["selected_id"],
                    "valid_candidate": True,
                    "upstream_triggers": ["clothing_linkage_merged_to_clothing"],
                    "added_texts": [],
                    "removed_texts": removed_texts,
                })
                continue
            rp_vanished = replay_data["slots"].get(key)
            if sel_name == "clothing" and rp_vanished is not None and len(rp_vanished["raw_texts"]) == 0:
                cause_k = "m1_m3_nudity_single_branch_convergence"
                if cause_k not in verified_causes:
                    verified_causes.append(cause_k)
                slot_verifications.append({
                    "slot_key": slot_label,
                    "cause": cause_k,
                    "v120_selected_id": sid0,
                    "v121_selected_id": None,
                    "rng_stream": rp_vanished["rng_stream"],
                    "replay_verified": True,
                    "replay_selected_id": rp_vanished["selected_id"],
                    "valid_candidate": bool(rp_vanished["valid_candidate"]),
                    "upstream_triggers": ["nudity_level_omits_clothing_base_tags"],
                    "added_texts": [],
                    "removed_texts": removed_texts,
                })
                continue
            return _fail(f"Slot {slot_label} vanished in v1.2.1 without valid merge explanation", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)

        # Case C: normal selector slot present in v1.2.1 -> deterministic replay + per-text verification
        rp = replay_data["slots"].get(key)
        if rp is None:
            return _fail(f"Unknown or un-replayable selector slot {slot_label}", first_sel=first_diff_label, diff_keys_list=diff_keys, verifications=slot_verifications)
        if rp["selected_id"] != sid1:
            return _fail(
                f"Replay selected_id mismatch for {slot_label}: replay={rp['selected_id']!r} vs cur={sid1!r}",
                first_sel=first_diff_label,
                diff_keys_list=diff_keys,
                verifications=slot_verifications,
            )
        if sid1 is not None and not rp["valid_candidate"]:
            return _fail(
                f"Selected candidate {sid1!r} not in replayed valid candidate distribution for {slot_label}",
                first_sel=first_diff_label,
                diff_keys_list=diff_keys,
                verifications=slot_verifications,
            )
        if rp["total_candidates"] is not None and total1 is not None:
            if excl1 != rp["excluded_hard_conflicts"] or passed1 != rp["passed_candidates"] or total1 != rp["total_candidates"]:
                return _fail(
                    f"Replay candidate_filter mismatch for {slot_label}: replay=({rp['passed_candidates']}/{rp['total_candidates']}, excl={len(rp['excluded_hard_conflicts'])}) vs cur=({passed1}/{total1}, excl={len(excl1)})",
                    first_sel=first_diff_label,
                    diff_keys_list=diff_keys,
                    verifications=slot_verifications,
                )
        if rp["dist_sha256"] is not None and dist1 is not None and rp["dist_sha256"] != dist1:
            return _fail(
                f"Replay distribution hash mismatch for {slot_label}",
                first_sel=first_diff_label,
                diff_keys_list=diff_keys,
                verifications=slot_verifications,
            )

        # (accepted_texts 与 omitted_texts 的双向多重集守恒已由 _verify_current_replay_consistency 预先核验)

        # Collect genuine causal triggers vs v1.2.0
        triggers: List[str] = []
        cf_changed = (
            total0 is not None
            and rp["total_candidates"] is not None
            and (excl0, passed0, total0) != (rp["excluded_hard_conflicts"], rp["passed_candidates"], rp["total_candidates"])
        )
        if cf_changed:
            triggers.append("candidate_filter_changed")
        dist_changed = dist0 is not None and rp["dist_sha256"] is not None and dist0 != rp["dist_sha256"]
        if dist_changed:
            triggers.append("distribution_changed")
        ctx_changed = ctx0 is not None and ctx0 != rp["active_contexts"]
        if ctx_changed:
            triggers.append("active_contexts_changed")

        branch_conv = sel_name == "nudity" and bool(cur_r.get("branch"))
        if branch_conv:
            triggers.append("nudity_single_branch_convergence")

        m2_cloth = sel_name in ("clothing", "clothing_state", "clothing_extension") and bool(
            cur_r.get("comp_added")
            or cur_r.get("repaired_ensemble")
            or cur_r.get("fallback_used")
            or cur_r.get("controlled_rebuild_attempted")
        )
        if m2_cloth:
            triggers.append("m2_outfit_completion_or_repair")

        clothing_link = (
            sel_name in ("clothing_state", "clothing_extension")
            and bool(cur_r.get("branch") or ("clothing", 0) in diff_key_set)
        ) or (
            sel_name == "clothing" and (bool(cur_r.get("branch")) or ("clothing_state", 0) in map0)
        )
        if clothing_link and (sid0 == sid1 or cf_changed or dist_changed or sel_name in ("clothing_state", "clothing_extension")):
            triggers.append("clothing_linkage_and_branch")

        subtag_hit = False
        if sid0 == sid1 and sid1 is not None and v121_lvl:
            subtag_info = subtag_idx.get((sel_name, sid1, v121_lvl))
            if subtag_info and subtag_info["dropped_count"] > 0:
                if (set(txts1) - dec1_after) <= subtag_info["compat_texts"]:
                    subtag_hit = True
                    triggers.append("subtag_nudity_filter")

        if sel_name in ("shot_type", "camera_angle", "pose", "props", "clothing", "clothing_state", "clothing_extension", "theme", "lighting", "expression", "film", "makeup", "hairstyle", "jewelry", "tattoo", "character", "liquids"):
            if upstream_env_changed:
                triggers.append("upstream_dep:nudity_scene_theme")
            if sel_name in ("camera_angle", "pose") and shot_changed:
                triggers.append("upstream_dep:shot_type")
            if sel_name == "props" and pose_changed:
                triggers.append("upstream_dep:pose_hand_state")
            if sel_name in ("clothing_state", "clothing_extension") and ("clothing", 0) in diff_key_set:
                triggers.append("upstream_dep:clothing")

        dec_touched = bool(sym_diff & dec_texts_all)
        if dec_touched:
            triggers.append("resolver_decision")
        budget_touched = bool(sym_diff & (set(btxts0) | set(btxts1)))
        if budget_touched:
            triggers.append("budget_filter")
        dedup_touched = bool(sym_diff & (set(dtxts0) | set(dtxts1)))
        if dedup_touched:
            triggers.append("deduplication")

        raw_pool_changed = cf_changed or dist_changed or ctx_changed or subtag_hit or branch_conv or m2_cloth
        if sid0 != sid1:
            if rp["rng_stream"] is None:
                return _fail(
                    f"Deterministic non-RNG slot {slot_label} cannot shift selected_id ({sid0!r} -> {sid1!r})",
                    first_sel=first_diff_label,
                    diff_keys_list=diff_keys,
                    verifications=slot_verifications,
                )
            if not (cf_changed or dist_changed or ctx_changed or branch_conv or m2_cloth or clothing_link or "upstream_dep:" in ",".join(triggers)):
                return _fail(
                    f"Category ID shift in {slot_label} ({sid0!r} -> {sid1!r}) lacks candidate/distribution/context/upstream causal trigger",
                    first_sel=first_diff_label,
                    diff_keys_list=diff_keys,
                    verifications=slot_verifications,
                )
        else:
            # Same category ID: check added & removed texts carefully
            if not (raw_pool_changed or clothing_link or "upstream_dep:" in ",".join(triggers)):
                # No pool/dist/context/subtag/upstream change: EVERY added text must be from resolver or unblocked budget/dedup in v120!
                allowed_added = dec1_after | dec0_before | set(btxts0) | set(dtxts0)
                unbacked_add = [t for t in added_texts if t not in allowed_added]
                if unbacked_add:
                    return _fail(
                        f"Unbacked text addition(s) {unbacked_add!r} in unchanged-pool {slot_label}",
                        first_sel=first_diff_label,
                        diff_keys_list=diff_keys,
                        verifications=slot_verifications,
                    )

        # Verify EVERY removed text from v1.2.0 has explicit justification
        rp_raw_set = set(rp["raw_texts"])
        rem_allowed_base = dec1_before | set(btxts1) | set(dtxts1) | dec0_after
        if sid0 != sid1 or raw_pool_changed or clothing_link or ("upstream_dep:" in ",".join(triggers)):
            for t in removed_texts:
                if t in rp_raw_set and t not in rem_allowed_base:
                    return _fail(
                        f"Unbacked removal of still-generated fragment {t!r} in {slot_label}",
                        first_sel=first_diff_label,
                        diff_keys_list=diff_keys,
                        verifications=slot_verifications,
                    )
        else:
            for t in removed_texts:
                if t not in rem_allowed_base:
                    return _fail(
                        f"Unbacked text removal {t!r} in unchanged-pool {slot_label}",
                        first_sel=first_diff_label,
                        diff_keys_list=diff_keys,
                        verifications=slot_verifications,
                    )

        # Assign accurate canonical mechanism cause
        if branch_conv:
            cause_k = "m1_m3_nudity_single_branch_convergence"
        elif m2_cloth:
            cause_k = "m2_outfit_completion_or_repair"
        elif clothing_link and sid0 == sid1 and not cf_changed:
            cause_k = "m1_m3_clothing_linkage_and_branch_filtering"
        elif cf_changed and sid0 != sid1:
            cause_k = f"m1_m3_cross_slot_pool_filtering:{sel_name}"
        elif cf_changed and sid0 == sid1:
            cause_k = f"m1_m3_pool_filter_same_category_subsample:{sel_name}"
        elif sid0 == sid1 and subtag_hit and not (dec_touched or budget_touched or dedup_touched):
            cause_k = f"m3_nudity_subtag_pool_filtering:{sel_name}"
        elif sid0 == sid1 and dec_touched and not (dist_changed or ctx_changed):
            cause_k = f"m1_m3_resolver_decision_on_same_category:{sel_name}"
        elif sid0 == sid1 and budget_touched and not (dist_changed or ctx_changed):
            cause_k = f"m3_budget_protection_same_category:{sel_name}"
        elif sid0 == sid1 and dedup_touched and not (dist_changed or ctx_changed):
            cause_k = f"m3_deduplication_same_category:{sel_name}"
        elif dist_changed or ctx_changed:
            cause_k = f"verified_context_distribution_substream_replay:{sel_name}"
        elif subtag_hit:
            cause_k = f"m3_nudity_subtag_pool_filtering:{sel_name}"
        elif dec_touched:
            cause_k = f"m1_m3_resolver_decision_on_same_category:{sel_name}"
        elif budget_touched:
            cause_k = f"m3_budget_protection_same_category:{sel_name}"
        elif dedup_touched:
            cause_k = f"m3_deduplication_same_category:{sel_name}"
        else:
            cause_k = f"verified_upstream_substream_replay:{sel_name}"

        if cause_k not in verified_causes:
            verified_causes.append(cause_k)
        slot_verifications.append({
            "slot_key": slot_label,
            "cause": cause_k,
            "v120_selected_id": sid0,
            "v121_selected_id": sid1,
            "rng_stream": rp["rng_stream"],
            "replay_verified": True,
            "replay_selected_id": rp["selected_id"],
            "valid_candidate": bool(rp["valid_candidate"]),
            "replay_raw_count": len(rp["raw_texts"]),
            "accepted_count": len(txts1),
            "omitted_count": sum((collections.Counter(rp["raw_texts"]) - c1).values()),
            "upstream_triggers": triggers,
            "added_texts": added_texts,
            "removed_texts": removed_texts,
        })

    if cur_r.get("comp_added") and "m2_outfit_completion_or_repair" not in verified_causes:
        verified_causes.append("m2_outfit_completion_or_repair")
    if cur_r.get("fallback_used") and "m2_outfit_minimal_fallback" not in verified_causes:
        verified_causes.append("m2_outfit_minimal_fallback")
    if cur_r.get("controlled_rebuild_attempted") and "m2_controlled_rebuild" not in verified_causes:
        verified_causes.append("m2_controlled_rebuild")
    if ("nudity", 0) in diff_key_set and "m1_m3_nudity_single_branch_convergence" not in verified_causes:
        verified_causes.append("m1_m3_nudity_single_branch_convergence")

    primary_cause = slot_verifications[0]["cause"] if slot_verifications else "verified_replay"
    return {
        "is_divergent": True,
        "is_explained": True,
        "current_replay_verified": True,
        "primary_cause": primary_cause,
        "verified_causes": verified_causes,
        "first_diff_selector": first_diff_label,
        "diff_slots": [_slot_key_label(k) for k in diff_keys],
        "slot_verifications": slot_verifications,
        "level_preserved": True,
        "unexplained_reason": None,
    }


def _compare_with_v120_archive(cur_records: List[Dict[str, Any]], archive_path: Path) -> Optional[Dict[str, Any]]:
    if not archive_path.is_file():
        return None
    header_meta, record_iter = stream_archive_records(archive_path)
    cur_map = {r["seed"]: r for r in cur_records}
    identical = 0
    divergent = 0
    explained = 0
    unexplained_seeds: Dict[int, Dict[str, Any]] = {}
    per_seed_attribution: Dict[int, Dict[str, Any]] = {}
    primary_cause_counts: collections.Counter[str] = collections.Counter()
    mechanism_presence_counts: collections.Counter[str] = collections.Counter()
    first_diff_selector_counts: collections.Counter[str] = collections.Counter()
    div_level_counts: collections.Counter[str] = collections.Counter()
    level_mismatch_count = 0
    sample_diffs: List[Dict[str, Any]] = []
    for rec in record_iter:
        s = rec["seed"]
        if s not in cur_map:
            continue
        cur_r = cur_map[s]
        v120_round_a = rec.get("round_a", {})
        attr = attribute_v120_vs_v121_seed(v120_round_a, cur_r)
        per_seed_attribution[s] = attr
        if not attr["is_divergent"] and attr["is_explained"]:
            identical += 1
            continue
        if attr["is_divergent"]:
            divergent += 1
        lvl = cur_r.get("eff_lvl") or "unknown"
        div_level_counts[f"level_{lvl}"] += 1
        if not attr["level_preserved"]:
            level_mismatch_count += 1
        if attr["first_diff_selector"]:
            first_diff_selector_counts[attr["first_diff_selector"]] += 1
        if attr["is_explained"]:
            explained += 1
            primary_cause_counts[attr["primary_cause"] or "unknown"] += 1
            for mc in attr["verified_causes"]:
                mechanism_presence_counts[mc] += 1
        else:
            unexplained_seeds[s] = {
                "reason": attr["unexplained_reason"],
                "first_diff_selector": attr["first_diff_selector"],
                "eff_lvl": lvl,
                "branch": cur_r.get("branch"),
            }
        if len(sample_diffs) < 10:
            sample_diffs.append({
                "seed": s,
                "v120_pos": v120_round_a.get("positive", ""),
                "v121_pos": cur_r["positive"],
                "eff_lvl": lvl,
                "branch": cur_r.get("branch"),
                "comp_added": cur_r.get("comp_added"),
                "first_diff_selector": attr["first_diff_selector"],
                "primary_cause": attr["primary_cause"],
                "verified_causes": attr["verified_causes"],
                "diff_slots": attr.get("diff_slots", []),
                "is_explained": attr["is_explained"],
            })
    v120_meta = header_meta.get("metadata", {})
    return {
        "v120_archive": str(archive_path.name),
        "v120_target_batch_hash": v120_meta.get("target_batch_hash"),
        "v120_core_source_digest": v120_meta.get("core_source_digest"),
        "identical_count": identical,
        "divergent_count": divergent,
        "explained_count": explained,
        "unexplained_count": len(unexplained_seeds),
        "is_all_explained": len(unexplained_seeds) == 0,
        "unexplained_seeds": {str(k): unexplained_seeds[k] for k in sorted(unexplained_seeds)[:50]},
        "level_mismatch_count": level_mismatch_count,
        "first_diff_selector_distribution": dict(sorted(first_diff_selector_counts.items())),
        "primary_cause_distribution": dict(sorted(primary_cause_counts.items())),
        "mechanism_presence_distribution": dict(sorted(mechanism_presence_counts.items())),
        "divergence_breakdown": dict(sorted(div_level_counts.items())),
        "sample_diffs": sample_diffs,
        "_per_seed_attribution": per_seed_attribution,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v1.2.1 10,000-seed & 4-node audit")
    parser.add_argument("--seeds", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--strat-seeds", type=int, default=500)
    parser.add_argument("--skip-v120-attribution", action="store_true")
    args = parser.parse_args()

    scratch_dir = REPO_DIR / "scratch"
    t0 = time.time()

    git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_DIR, text=True).strip()
    git_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=REPO_DIR, text=True).strip()
    is_dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=REPO_DIR, text=True).strip())
    core_digest = compute_core_source_digest(REPO_DIR)

    print(f"[*] v1.2.1 Audit starting on commit={git_commit[:12]} dirty={is_dirty} core_digest={core_digest[:16]}...")

    total_seeds = args.seeds
    chunk_size = max(250, (total_seeds + args.workers - 1) // args.workers)
    chunks = [(start, min(chunk_size, total_seeds - start)) for start in range(0, total_seeds, chunk_size)]
    all_records: List[Dict[str, Any]] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        for chunk_res in pool.map(_worker_10k_chunk, chunks):
            all_records.extend(chunk_res)
    all_records.sort(key=lambda r: r["seed"])
    t_10k = time.time() - t0
    print(f"[+] Completed {len(all_records)} seeds in {t_10k:.1f}s")

    gate_names = (
        "determinism",
        "oracle_validation",
        "residual_conflicts",
        "word_budget",
        "prompt_syntax",
        "provenance_closed_loop",
        "secondary_idempotency",
        "carrier_coherence",
    )
    gate_failures: Dict[str, int] = {g: 0 for g in gate_names}
    failed_seeds: Dict[int, List[str]] = {}
    level_counts: collections.Counter[str] = collections.Counter()
    branch_counts: collections.Counter[str] = collections.Counter()
    ordered_hashes: List[str] = []

    for rec in all_records:
        ordered_hashes.append(rec["pos_hash"])
        level_counts[rec["eff_lvl"] or "none"] += 1
        branch_counts[rec["branch"] or "none"] += 1
        for g in gate_names:
            if not rec["gates"][g]:
                gate_failures[g] += 1
        if rec["failures"]:
            failed_seeds[rec["seed"]] = rec["failures"]

    target_batch_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()

    node_matrix = _audit_all_four_nodes(seeds_per_level=args.strat_seeds)
    v120_archive_path = scratch_dir / "audit_m5_3_evidence.json.gz"
    if args.skip_v120_attribution:
        v120_cmp = None
    else:
        if not v120_archive_path.is_file():
            raise FileNotFoundError(
                f"v1.2.0 baseline archive missing at {v120_archive_path}; "
                "cannot verify per-seed cross-version causal attribution (pass --skip-v120-attribution only for standalone gate runs)."
            )
        v120_cmp = _compare_with_v120_archive(all_records, v120_archive_path)

    per_seed_attr = v120_cmp.pop("_per_seed_attribution", {}) if v120_cmp else {}

    archive_file = scratch_dir / "audit_v1_2_1_10k_evidence.json.gz"
    manifest_file = scratch_dir / "audit_v1_2_1_10k_manifest.json"
    report_file = scratch_dir / "audit_v1_2_1_10k_report.md"

    archive_records = [
        {
            "seed": r["seed"],
            "round_a": {
                "positive": r["positive"],
                "pos_hash": r["pos_hash"],
                "eff_lvl": r["eff_lvl"],
                "branch": r["branch"],
                "words": r["words"],
                "decisions_count": r["decisions_count"],
                "comp_exempt": r["comp_exempt"],
                "comp_added": r["comp_added"],
                "fallback_used": r["fallback_used"],
            },
            "gates": r["gates"],
            "failures": r["failures"],
            "v120_attribution": per_seed_attr.get(r["seed"]),
        }
        for r in all_records
    ]

    archive_doc = {
        "metadata": {
            "version": "1.2.1",
            "target_commit": git_commit,
            "target_tree_hash": git_tree,
            "is_dirty": is_dirty,
            "core_source_digest": core_digest,
            "target_batch_hash": target_batch_hash,
            "total_seeds": total_seeds,
            "python_version": platform.python_version(),
            "os_info": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "records": archive_records,
    }

    with open(archive_file, "wb") as raw_f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=0) as gf:
            with io.TextIOWrapper(gf, encoding="utf-8") as tf:
                json.dump(archive_doc, tf, sort_keys=True, ensure_ascii=False)

    archive_sha256 = compute_sha256_file(archive_file)
    archive_size = archive_file.stat().st_size

    div_cnt = v120_cmp["divergent_count"] if v120_cmp else 0
    iden_cnt = v120_cmp["identical_count"] if v120_cmp else total_seeds
    unexplained_cnt = v120_cmp["unexplained_count"] if v120_cmp else total_seeds
    is_all_explained = v120_cmp["is_all_explained"] if v120_cmp else False
    unexplained_seeds_map = v120_cmp["unexplained_seeds"] if v120_cmp else {"baseline": "v1.2.0 archive skipped"}

    manifest_doc = {
        "manifest_version": "2.1-v1.2.1",
        "task": "v1.2.1 10,000-Seed Multi-Node & M0-M3 Audit",
        "command": f"{sys.executable} scratch/run_v1_2_1_10k_audit.py --seeds {total_seeds} --strat-seeds {args.strat_seeds}",
        "environment": {
            "python_version": platform.python_version(),
            "os_info": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "jsonschema_version": __import__("jsonschema").__version__,
        },
        "auditor_binding": {
            "script_path": "scratch/run_v1_2_1_10k_audit.py",
            "script_sha256": compute_sha256_file(Path(__file__)),
            "target_commit": git_commit,
            "target_tree_hash": git_tree,
            "is_dirty": is_dirty,
            "core_source_digest": core_digest,
            "target_batch_hash": target_batch_hash,
            "v120_baseline_batch_hash": v120_cmp["v120_target_batch_hash"] if v120_cmp else None,
        },
        "source_archive": {
            "path": archive_file.name,
            "sha256": archive_sha256,
            "size_bytes": archive_size,
            "total_records_processed": total_seeds,
            "contiguous_seed_range": [0, total_seeds - 1],
        },
        "quality_gates": {
            k: "PASS (0 failures)" if v == 0 else f"FAIL ({v} failures)"
            for k, v in gate_failures.items()
        },
        "evaluation_result": {
            "total_seeds": total_seeds,
            "quality_gate_failure_count": len(failed_seeds),
            "quality_gate_failed_seeds": {str(k): v for k, v in list(failed_seeds.items())[:50]},
            "identical_count": iden_cnt,
            "divergent_count": div_cnt,
            "explained_count": v120_cmp["explained_count"] if v120_cmp else 0,
            "unexplained_count": unexplained_cnt,
            "is_all_explained": is_all_explained,
            "unexplained_seeds": unexplained_seeds_map,
            "elapsed_seconds": round(time.time() - t0, 2),
            "level_distribution": dict(sorted(level_counts.items())),
            "branch_distribution": dict(sorted(branch_counts.items())),
            "v120_comparison": v120_cmp,
            "node_matrix": node_matrix,
        },
        "timestamp": archive_doc["metadata"]["timestamp"],
    }
    manifest_file.write_text(json.dumps(manifest_doc, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        "# ComfyUI-IYKYK v1.2.1 10,000 种子与全四节点综合审计报告",
        "",
        f"- **审计提交 Commit**: `{git_commit}` (Tree: `{git_tree}`, Dirty: `{is_dirty}`)",
        f"- **核心源码摘要 (`core_source_digest`)**: `{core_digest}`",
        f"- **10k 种子汇总哈希 (`target_batch_hash`)**: `{target_batch_hash}`",
        f"- **归档文件**: `scratch/{archive_file.name}` (`{archive_size:,}` 字节, SHA-256: `{archive_sha256}`)",
        f"- **执行命令**: `{manifest_doc['command']}`",
        f"- **环境**: Python `{platform.python_version()}` / `{manifest_doc['environment']['os_info']}` / jsonschema `{manifest_doc['environment']['jsonschema_version']}`",
        "",
        "## 一、10,000 种子全链路质量门禁（seed=0..9999）",
        "",
        "| 门禁指标 | 失败数 | 状态 |",
        "|---|---|---|",
    ]
    for g, cnt in gate_failures.items():
        md_lines.append(f"| `{g}` | {cnt} | {'PASS' if cnt == 0 else 'FAIL'} |")
    md_lines.extend([
        "",
        f"- **质量门禁失败种子数**: `{len(failed_seeds)}`",
        f"- **等级分布**: `{dict(sorted(level_counts.items()))}`",
        f"- **分支分布**: `{dict(sorted(branch_counts.items()))}`",
        "",
        "## 二、全四公开节点分层矩阵验证",
        "",
        f"- **`IYKYKPresetBrowser`**: 检验 {node_matrix['preset_browser']['total_checked']} 组，失败 `{node_matrix['preset_browser']['failure_count']}`",
        f"- **`IYKYKCustomSlotCombiner`**: 检验 {node_matrix['custom_slot_combiner']['total_checked']} 组，失败 `{node_matrix['custom_slot_combiner']['failure_count']}`",
        f"- **`IYKYKPromptGenerator` + `IYKYKPromptDiagnostics` (L1-L6×{args.strat_seeds})**: 检验 {node_matrix['stratified_generator_diagnostics']['total_checked']} 组，失败 `{node_matrix['stratified_generator_diagnostics']['failure_count']}`",
    ])
    if v120_cmp:
        md_lines.extend([
            "",
            "## 三、与 v1.2.0 归档（`audit_m5_3_evidence.json.gz`）逐种子因果归因比对",
            "",
            f"- **v1.2.0 批次哈希**: `{v120_cmp['v120_target_batch_hash']}`",
            f"- **完全一致种子数**: {v120_cmp['identical_count']:,} / {total_seeds:,}",
            f"- **差异种子数**: {v120_cmp['divergent_count']:,} / {total_seeds:,}",
            f"- **已因果归因差异数**: {v120_cmp['explained_count']:,} / {v120_cmp['divergent_count']:,}",
            f"- **未解释差异数 (`unexplained_count`)**: `{v120_cmp['unexplained_count']}` (`is_all_explained={v120_cmp['is_all_explained']}`)",
            f"- **等效等级跨版本保持失配数**: `{v120_cmp['level_mismatch_count']}`",
            f"- **首分叉选择器分布**: `{v120_cmp['first_diff_selector_distribution']}`",
            f"- **主因归因分布**: `{v120_cmp['primary_cause_distribution']}`",
            f"- **已验证机制触发频次**: `{v120_cmp['mechanism_presence_distribution']}`",
            f"- **等级差异分布**: `{v120_cmp['divergence_breakdown']}`",
        ])
    report_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"[+] Saved archive : {archive_file} ({archive_size:,} bytes)")
    print(f"[+] Saved manifest: {manifest_file}")
    print(f"[+] Saved report  : {report_file}")
    print(f"[+] Batch hash    : {target_batch_hash}")
    print(f"[+] Gate failures : {gate_failures}")
    if v120_cmp:
        print(
            f"[+] v1.2.0 attribution: identical={iden_cnt}, divergent={div_cnt}, "
            f"explained={v120_cmp['explained_count']}, unexplained={unexplained_cnt}"
        )
    print(f"[+] Node matrix failures: preset={node_matrix['preset_browser']['failure_count']}, combiner={node_matrix['custom_slot_combiner']['failure_count']}, strat={node_matrix['stratified_generator_diagnostics']['failure_count']}")

    total_fail = (
        len(failed_seeds)
        + (unexplained_cnt if v120_cmp is not None else 0)
        + node_matrix["preset_browser"]["failure_count"]
        + node_matrix["custom_slot_combiner"]["failure_count"]
        + node_matrix["stratified_generator_diagnostics"]["failure_count"]
    )
    if total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
