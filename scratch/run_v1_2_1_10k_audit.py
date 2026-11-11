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
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from lib.conflict_resolver import ConflictResolver, find_carrier_mismatch_in_atoms  # noqa: E402
from lib.lexer import validate_prompt_syntax  # noqa: E402
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
        results.append({
            "seed": s,
            "positive": r1.positive,
            "pos_hash": pos_hash,
            "eff_lvl": eff_lvl,
            "branch": branch,
            "words": words,
            "comp_exempt": bool(comp_info.get("exempt", False)),
            "comp_added": bool(comp_info.get("complement_ids") or comp_info.get("repaired_ensemble")),
            "fallback_used": bool(comp_info.get("fallback_used", False)),
            "controlled_rebuild_attempted": bool(comp_info.get("controlled_rebuild_attempted", False)),
            "decisions_count": len(rep.decisions) if rep else 0,
            "rules_applied": list(r1.rules_applied),
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


def _compare_with_v120_archive(cur_records: List[Dict[str, Any]], archive_path: Path) -> Optional[Dict[str, Any]]:
    if not archive_path.is_file():
        return None
    header_meta, record_iter = stream_archive_records(archive_path)
    cur_map = {r["seed"]: r for r in cur_records}
    identical = 0
    divergent = 0
    div_reasons: collections.Counter[str] = collections.Counter()
    sample_diffs: List[Dict[str, Any]] = []
    for rec in record_iter:
        s = rec["seed"]
        v120_pos = rec.get("round_a", {}).get("positive", "")
        cur_r = cur_map[s]
        if v120_pos == cur_r["positive"]:
            identical += 1
        else:
            divergent += 1
            lvl = cur_r.get("eff_lvl") or "unknown"
            div_reasons[f"level_{lvl}"] += 1
            if cur_r.get("comp_added"):
                div_reasons["outfit_completion_applied"] += 1
            if len(sample_diffs) < 10:
                sample_diffs.append({
                    "seed": s,
                    "v120_pos": v120_pos,
                    "v121_pos": cur_r["positive"],
                    "eff_lvl": lvl,
                    "branch": cur_r.get("branch"),
                    "comp_added": cur_r.get("comp_added"),
                })
    v120_meta = header_meta.get("metadata", {})
    return {
        "v120_archive": str(archive_path.name),
        "v120_target_batch_hash": v120_meta.get("target_batch_hash"),
        "v120_core_source_digest": v120_meta.get("core_source_digest"),
        "identical_count": identical,
        "divergent_count": divergent,
        "divergence_breakdown": dict(sorted(div_reasons.items())),
        "sample_diffs": sample_diffs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run v1.2.1 10,000-seed & 4-node audit")
    parser.add_argument("--seeds", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    parser.add_argument("--strat-seeds", type=int, default=500)
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
    v120_cmp = _compare_with_v120_archive(all_records, scratch_dir / "audit_m5_3_evidence.json.gz")

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
            "identical_count": iden_cnt,
            "divergent_count": div_cnt,
            "unexplained_count": len(failed_seeds),
            "is_all_explained": len(failed_seeds) == 0,
            "unexplained_seeds": {str(k): v for k, v in list(failed_seeds.items())[:50]},
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
        f"- **总失败种子数**: `{len(failed_seeds)}`",
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
            "## 三、与 v1.2.0 归档（`audit_m5_3_evidence.json.gz`）逐种子比对",
            "",
            f"- **v1.2.0 批次哈希**: `{v120_cmp['v120_target_batch_hash']}`",
            f"- **完全一致种子数**: {v120_cmp['identical_count']:,} / {total_seeds:,}",
            f"- **差异种子数**: {v120_cmp['divergent_count']:,} / {total_seeds:,}",
            f"- **差异归因分布**: `{v120_cmp['divergence_breakdown']}`",
        ])
    report_file.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(f"[+] Saved archive : {archive_file} ({archive_size:,} bytes)")
    print(f"[+] Saved manifest: {manifest_file}")
    print(f"[+] Saved report  : {report_file}")
    print(f"[+] Batch hash    : {target_batch_hash}")
    print(f"[+] Gate failures : {gate_failures}")
    print(f"[+] Node matrix failures: preset={node_matrix['preset_browser']['failure_count']}, combiner={node_matrix['custom_slot_combiner']['failure_count']}, strat={node_matrix['stratified_generator_diagnostics']['failure_count']}")

    total_fail = (
        len(failed_seeds)
        + node_matrix["preset_browser"]["failure_count"]
        + node_matrix["custom_slot_combiner"]["failure_count"]
        + node_matrix["stratified_generator_diagnostics"]["failure_count"]
    )
    if total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
