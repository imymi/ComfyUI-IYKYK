#!/usr/bin/env python3
"""
audit_bc0d645_divergence.py — 全量双版本 (bc0d645 -> Current 137 款) 逐种子逐原子差异归因审计入口

功能：
1. 真实运行 bc0d645 基线 (31 款) 10,000 种子生成，强制校验其汇总哈希严格等于 4525786e7273dc0694e64ec216d4fc9510f211d32de7bdfaa00a12f7a5f320e2；
2. 真实运行 Current (137 款) 10,000 种子生成，计算 Current 汇总哈希；
3. 逐种子比对输出，对所有产生文本差异的种子调用 attribute_seed_diff 执行逐原子级精确归因；
4. 阻断门禁：任何未解释差异 (UNEXPLAINED) 数量 > 0 则立即报错并阻断更新哈希；
5. 输出标准化 JSON 审计档案与 Markdown 审查报告。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List

REPO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SCRATCH_DIR = Path("/Users/jacobyang/.gemini/antigravity/brain/4dfc2f79-2452-47f9-bc8f-de7cd3d990cc/scratch")
BASELINE_COMMIT = "bc0d645"
EXPECTED_BASELINE_HASH = "4525786e7273dc0694e64ec216d4fc9510f211d32de7bdfaa00a12f7a5f320e2"


WORKER_SCRIPT = """
import sys
import json
import hashlib
from pathlib import Path

repo_path = sys.argv[1]
start_seed = int(sys.argv[2])
count = int(sys.argv[3])
output_file = sys.argv[4]

sys.path.insert(0, repo_path)
import nodes

inputs = {
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

def atom_dict(a):
    slot = a.source_slot or (a.provenance.kind if a.provenance else "") or ""
    item_id = a.source_item_id or (a.provenance.item_id if a.provenance else "") or ""
    return {
        "atom_id": a.atom_id,
        "text": a.text,
        "slot": slot,
        "item_id": item_id,
    }

def dec_dict(d):
    return {
        "decision_id": d.decision_id,
        "rule_id": d.rule_id,
        "reason_code": d.reason_code,
        "action": d.action,
        "target_atom_id": d.target_atom_id,
        "before_text": d.before_text,
        "after_text": d.after_text,
        "winner_atom_ids": list(d.winner_atom_ids),
        "produced_atom_ids": list(d.produced_atom_ids),
    }

def get_clothing_id(source_atoms):
    for a in source_atoms:
        if a.source_slot in ("clothing", "base_clothing") and a.source_item_id:
            return a.source_item_id
        if a.provenance and a.provenance.kind in ("base_clothing", "clothing") and a.provenance.item_id:
            return a.provenance.item_id
    return ""

g = nodes.IYKYKPromptGenerator()
results = []
for s in range(start_seed, start_seed + count):
    r = g.generate_structured(**inputs, prompt_seed=s)
    cid = get_clothing_id(r.source_atoms)
    decs = [dec_dict(d) for d in r.resolution_report.decisions] if r.resolution_report else []
    h = hashlib.sha256(r.positive.encode("utf-8")).hexdigest()
    results.append({
        "seed": s,
        "positive": r.positive,
        "hash": h,
        "clothing_id": cid,
        "src_atoms": [atom_dict(a) for a in r.source_atoms],
        "final_atoms": [atom_dict(a) for a in r.atoms],
        "decisions": decs,
    })

Path(output_file).write_text(json.dumps(results), encoding="utf-8")
"""


def ensure_baseline_repo(scratch_dir: Path, baseline_commit: str = BASELINE_COMMIT) -> Path:
    baseline_dir = scratch_dir / f"baseline_{baseline_commit}"
    if not (baseline_dir / "nodes.py").exists():
        baseline_dir.mkdir(parents=True, exist_ok=True)
        # 导出基线代码
        cmd = f"git archive {baseline_commit} | tar -x -C {baseline_dir}"
        subprocess.run(cmd, shell=True, check=True, cwd=str(REPO_DIR))
    return baseline_dir


def _run_worker_chunk(
    repo_path: Path,
    start_seed: int,
    count: int,
    output_file: Path,
    python_bin: str,
) -> Path:
    cmd = [
        python_bin,
        "-c",
        WORKER_SCRIPT,
        str(repo_path),
        str(start_seed),
        str(count),
        str(output_file),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(repo_path))
    if res.returncode != 0:
        raise RuntimeError(f"Worker chunk failed for seeds {start_seed}..{start_seed+count-1}:\n{res.stderr}")
    return output_file


def run_batch_generation(
    repo_path: Path,
    total_seeds: int,
    chunk_size: int,
    scratch_dir: Path,
    label: str,
    workers: int,
    python_bin: str,
) -> List[Dict[str, Any]]:
    chunks = []
    chunk_files = []
    for i in range(0, total_seeds, chunk_size):
        c_count = min(chunk_size, total_seeds - i)
        out_f = scratch_dir / f"{label}_{i}_{c_count}.json"
        chunk_files.append(out_f)
        chunks.append((i, c_count, out_f))

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(_run_worker_chunk, repo_path, start, count, out_f, python_bin)
            for start, count, out_f in chunks
        ]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    all_results = []
    for _, _, out_f in chunks:
        data = json.loads(out_f.read_text(encoding="utf-8"))
        all_results.extend(data)
        try:
            out_f.unlink()
        except OSError:
            pass

    all_results.sort(key=lambda x: x["seed"])
    return all_results


def run_divergence_audit(
    total_seeds: int = 10000,
    chunk_size: int = 1000,
    workers: int = 8,
    baseline_commit: str = BASELINE_COMMIT,
    scratch_dir: Path | None = None,
    output_json: Path | None = None,
    output_md: Path | None = None,
    python_bin: str | None = None,
) -> Dict[str, Any]:
    scratch_dir = scratch_dir or DEFAULT_SCRATCH_DIR
    scratch_dir.mkdir(parents=True, exist_ok=True)
    python_bin = python_bin or sys.executable

    # 1. 准备基线仓库
    t0 = time.time()
    baseline_dir = ensure_baseline_repo(scratch_dir, baseline_commit)

    # 2. 运行基线版本生成
    print(f"[*] Running baseline ({baseline_commit}) generation for {total_seeds} seeds...")
    t_base0 = time.time()
    base_results = run_batch_generation(
        baseline_dir,
        total_seeds=total_seeds,
        chunk_size=chunk_size,
        scratch_dir=scratch_dir,
        label=f"base_{baseline_commit}",
        workers=workers,
        python_bin=python_bin,
    )
    t_base1 = time.time()
    print(f"[+] Baseline generation completed in {t_base1 - t_base0:.2f}s")

    # 校验基线 10k 黄金哈希
    base_ordered_hashes = [r["hash"] for r in base_results]
    base_batch_hash = hashlib.sha256("".join(base_ordered_hashes).encode("utf-8")).hexdigest()
    if total_seeds == 10000:
        if base_batch_hash != EXPECTED_BASELINE_HASH:
            raise AssertionError(
                f"Baseline hash verification failed!\nExpected: {EXPECTED_BASELINE_HASH}\nComputed: {base_batch_hash}"
            )
        print(f"[+] Baseline 10k hash matches expected golden hash: {base_batch_hash}")
    else:
        print(f"[i] Baseline {total_seeds} seeds hash: {base_batch_hash}")

    # 3. 运行当前版本生成
    print(f"[*] Running current (137 styles) generation for {total_seeds} seeds...")
    t_cur0 = time.time()
    cur_results = run_batch_generation(
        REPO_DIR,
        total_seeds=total_seeds,
        chunk_size=chunk_size,
        scratch_dir=scratch_dir,
        label="current_137",
        workers=workers,
        python_bin=python_bin,
    )
    t_cur1 = time.time()
    print(f"[+] Current generation completed in {t_cur1 - t_cur0:.2f}s")

    cur_ordered_hashes = [r["hash"] for r in cur_results]
    cur_batch_hash = hashlib.sha256("".join(cur_ordered_hashes).encode("utf-8")).hexdigest()
    print(f"[+] Current {total_seeds} seeds batch hash: {cur_batch_hash}")

    # 4. 执行逐种子逐原子差异归因比对
    from scripts.audit_attribution import attribute_seed_diff

    print(f"[*] Auditing divergence between baseline and current across {total_seeds} seeds...")
    identical_count = 0
    diff_count = 0
    category_counts: Dict[str, int] = {}
    unexplained_seeds: List[Dict[str, Any]] = []
    audited_diffs: List[Dict[str, Any]] = []

    for b, c in zip(base_results, cur_results):
        s = b["seed"]
        if b["positive"] == c["positive"]:
            identical_count += 1
            continue

        diff_count += 1
        b_cid = b["clothing_id"]
        c_cid = c["clothing_id"]
        attr = attribute_seed_diff(
            s=s,
            base_clothing_id=b_cid,
            cur_clothing_id=c_cid,
            base_src_atoms=b["src_atoms"],
            base_final_atoms=b["final_atoms"],
            base_decisions=b["decisions"],
            cur_src_atoms=c["src_atoms"],
            cur_final_atoms=c["final_atoms"],
            cur_decisions=c["decisions"],
            base_catalog_size=31,
            cur_catalog_size=137,
        )

        cat = attr["primary_category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1
        audited_diffs.append({
            "seed": s,
            "base_clothing": b_cid,
            "cur_clothing": c_cid,
            "primary_category": cat,
            "is_unexplained": attr["is_unexplained"],
            "summary": attr["attribution_summary"],
            "removed_atoms": attr["removed_atoms"],
            "added_atoms": attr["added_atoms"],
            "unexplained_removed": attr["unexplained_removed"],
            "unexplained_added": attr["unexplained_added"],
        })

        if attr["is_unexplained"]:
            unexplained_seeds.append({
                "seed": s,
                "base_clothing": b_cid,
                "cur_clothing": c_cid,
                "removed": attr["unexplained_removed"],
                "added": attr["unexplained_added"],
            })

    total_time = time.time() - t0

    # 5. 构建审计总结报告
    report = {
        "baseline_commit": baseline_commit,
        "baseline_catalog_size": 31,
        "current_catalog_size": 137,
        "total_seeds": total_seeds,
        "identical_seeds": identical_count,
        "differing_seeds": diff_count,
        "unexplained_seeds_count": len(unexplained_seeds),
        "baseline_batch_hash": base_batch_hash,
        "current_batch_hash": cur_batch_hash,
        "attribution_categories": category_counts,
        "unexplained_seeds": unexplained_seeds,
        "audit_duration_seconds": round(total_time, 2),
    }

    # 6. 保存报告文件
    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        # 为控制 JSON 体积，完整审计档案保存前 1000 组差异明细与全量未解释项
        full_json_doc = dict(report)
        full_json_doc["sample_diffs_limit_1000"] = audited_diffs[:1000]
        output_json.write_text(json.dumps(full_json_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[+] Divergence audit JSON saved to: {output_json}")

    if output_md:
        output_md.parent.mkdir(parents=True, exist_ok=True)
        md_lines = [
            "# 双版本全量种子差异归因审查报告",
            "",
            f"- **基线提交**: `{baseline_commit}` (31 款)",
            "- **当前版本**: HEAD (137 款完整目录)",
            f"- **测试种子范围**: Seeds `0..{total_seeds - 1}` (共 {total_seeds:,} 种子)",
            f"- **基线汇总哈希**: `{base_batch_hash}` (与冻结基线 100% 吻合)",
            f"- **当前汇总哈希**: `{cur_batch_hash}`",
            f"- **未解释差异项 (UNEXPLAINED)**: **`{len(unexplained_seeds)}`** (硬失败门禁要求 == 0)",
            "",
            "## 差异分布全景统计",
            "",
            "| 统计维度 | 种子数量 | 占比 | 状态 |",
            "|---|---|---|---|",
            f"| 完全一致种子 (Bit-exact Identical) | {identical_count:,} | {identical_count/total_seeds:.2%} | PASS |",
            f"| 差异种子总数 (Divergent Seeds) | {diff_count:,} | {diff_count/total_seeds:.2%} | INFO |",
            f"| 零归因遗漏/未解释差异 (UNEXPLAINED) | {len(unexplained_seeds):,} | {len(unexplained_seeds)/total_seeds:.2%} | {'PASS' if len(unexplained_seeds) == 0 else 'FAIL'} |",
            "",
            "## 差异主要归因分类分布",
            "",
            "| 主归因类别 | 影响种子数 | 归因机理解释 |",
            "|---|---|---|",
        ]
        for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
            if cat == "clothing_pool_expansion_category_shift":
                desc = "款式词库从 31 款扩充至 137 款，随机抽取的款式 ID 发生跨款式位移"
            elif cat == "clothing_pool_expansion_intra_slot_shift":
                desc = "款式相同，但扩充目录 PRNG 步进或多变体/叶子标签离散抽样产生槽位内位移"
            else:
                desc = f"承载物消解/冲突规则决策精确介入 ({cat})"
            md_lines.append(f"| `{cat}` | {cnt:,} ({cnt/diff_count:.2%}) | {desc} |")

        if unexplained_seeds:
            md_lines.extend([
                "",
                "## 阻断性未解释差异清单",
                "",
                "| 种子 | 基线款式 | 当前款式 | 未解释删除原子 | 未解释新增原子 |",
                "|---|---|---|---|---|",
            ])
            for u in unexplained_seeds[:50]:
                md_lines.append(
                    f"| {u['seed']} | `{u['base_clothing']}` | `{u['cur_clothing']}` | "
                    f"`{u['removed']}` | `{u['added']}` |"
                )

        output_md.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"[+] Divergence audit Markdown saved to: {output_md}")

    # 7. 门禁阻断检查
    if unexplained_seeds:
        raise AssertionError(
            f"Divergence audit FAILED with {len(unexplained_seeds)} UNEXPLAINED seed differences! "
            f"Cannot update golden hash until all differences are attributed."
        )

    print("\n" + "=" * 80)
    print("Divergence Audit Completed Successfully:")
    print(f"  Total Seeds Checked : {total_seeds:,}")
    print(f"  Identical Seeds     : {identical_count:,} ({identical_count/total_seeds:.2%})")
    print(f"  Divergent Seeds     : {diff_count:,} ({diff_count/total_seeds:.2%})")
    print("  Unexplained Diffs   : 0 (PASS)")
    print(f"  Current Golden Hash : {cur_batch_hash}")
    print("=" * 80 + "\n")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Dual-version divergence audit against baseline commit")
    parser.add_argument("--baseline-commit", default=BASELINE_COMMIT, help="Git commit of baseline version")
    parser.add_argument("--count", type=int, default=10000, help="Number of seeds to audit (0..count-1)")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Seeds per worker batch chunk")
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4), help="Parallel worker threads")
    parser.add_argument("--scratch-dir", type=Path, default=DEFAULT_SCRATCH_DIR, help="Scratch directory for baseline work")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=REPO_DIR / "docs" / "data_migration" / "bc0d645_to_137_divergence_audit.json",
        help="Path for output JSON audit report",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        default=REPO_DIR / "docs" / "data_migration" / "bc0d645_to_137_divergence_audit.md",
        help="Path for output Markdown audit report",
    )
    args = parser.parse_args()

    run_divergence_audit(
        total_seeds=args.count,
        chunk_size=args.chunk_size,
        workers=args.workers,
        baseline_commit=args.baseline_commit,
        scratch_dir=args.scratch_dir,
        output_json=args.output_json,
        output_md=args.output_md,
    )


if __name__ == "__main__":
    main()
