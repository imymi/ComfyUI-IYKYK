#!/usr/bin/env python3
"""
scratch/audit_dba5861_vs_current.py
全量种子 (0..9999) dba5861 (HEAD 冻结基线) 与当前工作区逐种子、逐原子结构化因果归因审计器。

核心规范：
1. 全量 10,000 种子结构化比对：直接比较 PromptAtom 序列签名 (atom_id, text, source_slot, source_item_id, tag_order, span_order)，杜绝仅比较 positive 文本或使用逗号切片；
2. 防复用旧 ID 篡改文本：严格比对 cur 来源原子、dba 来源原子、dba_drop before_text 与 cur 终态文本，四者必须绝对 1:1 一致；
3. 严格载体级联证明链：对 state_lacks_carrier 恢复，必须证明“全景误删载体 -> 当前载体恢复 -> 状态成功绑定该载体”；
4. 序列保序与无重复 ID 门禁：检测相对序列漂移 (sequence order mutation)、绝对 tag_order 乱序与重复 atom_id；
5. 零未解释项门禁：任何无法提供完整因果证据的增删改，必须触发 UNEXPLAINED 并返回非零退出码 (sys.exit(1))。
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DBA_COMMIT = "dba5861"

WORKER_SCRIPT = """
import sys
import json
from pathlib import Path

repo_path = sys.argv[1]
start_seed = int(sys.argv[2])
count = int(sys.argv[3])
out_file = sys.argv[4]

sys.path.insert(0, repo_path)
import nodes
from lib.conflict_resolver import extract_garment_entities, find_bound_carrier, is_garment_modifier_atom

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

g = nodes.NODE_CLASS_MAPPINGS["IYKYKPromptGenerator"]()

def atom_dict(a):
    return {
        "atom_id": a.atom_id,
        "text": a.text,
        "source_slot": a.source_slot,
        "source_item_id": a.source_item_id or "",
        "tag_order": a.tag_order,
        "span_order": a.span_order,
        "parent_ids": list(a.provenance.parent_ids) if a.provenance else [],
        "visible_regions": list(a.facts.visible_regions) if a.facts else [],
    }

def dec_dict(d):
    return {
        "decision_id": d.decision_id,
        "sequence": d.sequence,
        "rule_id": d.rule_id,
        "reason_code": d.reason_code,
        "action": d.action,
        "target_atom_id": d.target_atom_id,
        "winner_atom_ids": list(d.winner_atom_ids),
        "produced_atom_ids": list(d.produced_atom_ids),
        "parent_source_ids": list(d.parent_source_ids),
        "before_text": d.before_text,
        "after_text": d.after_text,
    }

res = []
for s in range(start_seed, start_seed + count):
    r = g.generate_structured(**inputs, prompt_seed=s)

    # 提取在穿实体与状态绑定证据
    entities = extract_garment_entities(r.atoms)
    worn = [e for e in entities.values() if e.is_worn and not e.is_ambient]
    carrier_bindings = {}
    for a in r.atoms:
        if is_garment_modifier_atom(a):
            b = find_bound_carrier(a, worn)
            if b.status.name == "BOUND" and b.target_entity:
                carrier_bindings[a.atom_id] = {
                    "carrier_entity_id": b.target_entity.entity_id,
                    "carrier_selected_id": b.target_entity.selected_id,
                    "carrier_member_atom_ids": [m.atom_id for m in b.target_entity.member_atoms],
                    "carrier_member_texts": [m.text for m in b.target_entity.member_atoms],
                }

    res.append({
        "seed": s,
        "positive": r.positive,
        "final_atoms": [atom_dict(a) for a in r.atoms],
        "source_atoms": [atom_dict(a) for a in r.source_atoms],
        "decisions": [dec_dict(d) for d in r.resolution_report.decisions] if r.resolution_report else [],
        "carrier_bindings": carrier_bindings,
    })

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(res, f)
"""


def export_commit(commit_id: str, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    cmd = f"git archive {commit_id} | tar -x -C {target_dir}"
    subprocess.run(cmd, shell=True, check=True, cwd=REPO_DIR)


def run_batch_parallel(repo_path: Path, seeds_count: int, chunk_size: int = 1000) -> Tuple[Dict[int, Dict[str, Any]], str]:
    num_chunks = (seeds_count + chunk_size - 1) // chunk_size
    temp_dir = Path(tempfile.mkdtemp(prefix="iykyk_audit_batch_"))
    worker_py = temp_dir / "worker.py"
    worker_py.write_text(WORKER_SCRIPT, encoding="utf-8")

    futures = []
    num_workers = min(8, os.cpu_count() or 4)
    with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as pool:
        for chunk_idx in range(num_chunks):
            start = chunk_idx * chunk_size
            count = min(chunk_size, seeds_count - start)
            out_file = temp_dir / f"chunk_{chunk_idx:04d}.json"
            cmd = [sys.executable, str(worker_py), str(repo_path), str(start), str(count), str(out_file)]
            futures.append(pool.submit(subprocess.run, cmd, capture_output=True, text=True, cwd=str(repo_path)))

        for f in futures:
            res = f.result()
            if res.returncode != 0:
                raise RuntimeError(f"Worker failed: {res.stderr}\n{res.stdout}")

    all_data = {}
    ordered_hashes = []
    for chunk_idx in range(num_chunks):
        out_file = temp_dir / f"chunk_{chunk_idx:04d}.json"
        with open(out_file, "r", encoding="utf-8") as f:
            chunk_data = json.load(f)
            for item in chunk_data:
                s = item["seed"]
                all_data[s] = item

    for s in range(seeds_count):
        item = all_data[s]
        h = hashlib.sha256(item["positive"].encode("utf-8")).hexdigest()
        ordered_hashes.append(h)

    batch_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()
    return all_data, batch_hash


def atom_signature(a: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        a["atom_id"],
        a["text"],
        a["source_slot"],
        a.get("source_item_id", ""),
        a.get("tag_order", 0),
        a.get("span_order", 0),
    )


def attribute_atom_divergence(
    item_dba: Dict[str, Any],
    item_cur: Dict[str, Any],
) -> Dict[str, Any]:
    """
    对单个种子的跨版本输出执行逐原子结构化因果归因。

    严禁使用裸逗号 split(",")，严格核验来源一致性、删除原因一致性、载体绑定证据链与相对顺序保序。
    """
    seed = item_dba["seed"]
    p_dba = item_dba["positive"]
    p_cur = item_cur["positive"]

    cur_final = item_cur["final_atoms"]
    dba_final = item_dba["final_atoms"]
    cur_src = {a["atom_id"]: a for a in item_cur["source_atoms"]}
    dba_src = {a["atom_id"]: a for a in item_dba["source_atoms"]}

    cur_final_map = {a["atom_id"]: a for a in cur_final}
    dba_final_map = {a["atom_id"]: a for a in dba_final}

    unexplained_reasons: List[str] = []
    explained_attributions: List[Dict[str, Any]] = []

    # 1. 重复 atom_id 严格核验
    cur_final_ids = [a["atom_id"] for a in cur_final]
    if len(cur_final_ids) != len(set(cur_final_ids)):
        unexplained_reasons.append(f"DUPLICATE_ATOM_ID_IN_CURRENT: Duplicate atom IDs detected in Current final atoms: {cur_final_ids}")

    dba_final_ids = [a["atom_id"] for a in dba_final]
    if len(dba_final_ids) != len(set(dba_final_ids)):
        unexplained_reasons.append(f"DUPLICATE_ATOM_ID_IN_BASELINE: Duplicate atom IDs detected in Baseline final atoms: {dba_final_ids}")

    # 2. 相对顺序严格保序检查：共存原子在双版本中的相对出现顺序必须严格一致
    common_in_dba = [a["atom_id"] for a in dba_final if a["atom_id"] in cur_final_map]
    common_in_cur = [a["atom_id"] for a in cur_final if a["atom_id"] in dba_final_map]
    if common_in_dba != common_in_cur:
        unexplained_reasons.append(
            f"UNEXPLAINED_SEQUENCE_ORDER_MUTATION: Common retained atoms relative order drifted: "
            f"dba={common_in_dba} vs cur={common_in_cur}"
        )

    # 3. Current 终态原子的绝对 tag_order / span_order 排序单调性核验
    cur_orders = [(a.get("tag_order", 0), a.get("span_order", 0)) for a in cur_final]
    if cur_orders != sorted(cur_orders):
        unexplained_reasons.append(
            "UNEXPLAINED_TAG_ORDER_VIOLATION: Final atoms in Current violate non-decreasing tag_order / span_order sorting"
        )

    # 4. 共存原子的字段绝对不变性核验 (槽位、条目ID、文本)
    for aid in common_in_dba:
        ad = dba_final_map[aid]
        ac = cur_final_map[aid]
        if ad["text"] != ac["text"]:
            unexplained_reasons.append(
                f"UNEXPLAINED_ALTERED_ATOM: Atom '{aid}' text changed without decision: '{ad['text']}' -> '{ac['text']}'"
            )
        if ad.get("source_slot") != ac.get("source_slot"):
            unexplained_reasons.append(
                f"UNEXPLAINED_FIELD_DRIFT: Atom '{aid}' slot drifted: '{ad.get('source_slot')}' -> '{ac.get('source_slot')}'"
            )
        if ad.get("source_item_id") != ac.get("source_item_id"):
            unexplained_reasons.append(
                f"UNEXPLAINED_FIELD_DRIFT: Atom '{aid}' source_item_id drifted: '{ad.get('source_item_id')}' -> '{ac.get('source_item_id')}'"
            )
        if ad.get("span_order") != ac.get("span_order"):
            unexplained_reasons.append(
                f"UNEXPLAINED_ORDER_FIELD_DRIFT: Atom '{aid}' span_order drifted between versions"
            )
        elif ad.get("tag_order") != ac.get("tag_order"):
            # 仅允许在消解器规则中由 inject 决策动态追加到末尾的原子（其 tag_order 取决于 max(active tag_orders) + 1）
            dba_inject = next((dec for dec in item_dba.get("decisions", []) if dec.get("action") == "inject" and aid in dec.get("produced_atom_ids", [])), None)
            cur_inject = next((dec for dec in item_cur.get("decisions", []) if dec.get("action") == "inject" and aid in dec.get("produced_atom_ids", [])), None)
            dba_expected = max((a.get("tag_order", 0) for a in dba_final if a["atom_id"] != aid), default=0) + 1
            cur_expected = max((a.get("tag_order", 0) for a in cur_final if a["atom_id"] != aid), default=0) + 1
            if not (dba_inject and cur_inject and dba_inject.get("rule_id") == cur_inject.get("rule_id") and ad.get("tag_order") == dba_expected and ac.get("tag_order") == cur_expected):
                unexplained_reasons.append(
                    f"UNEXPLAINED_ORDER_FIELD_DRIFT: Atom '{aid}' tag_order drifted between versions ('{ad.get('tag_order')}' -> '{ac.get('tag_order')}')"
                )

    # 5. 跨版本增删原子因果证据链核验
    cur_decs_by_prod: Dict[str, Dict[str, Any]] = {}
    for d in item_cur["decisions"]:
        for pid in d.get("produced_atom_ids", []):
            cur_decs_by_prod[pid] = d

    dba_drops_by_target = {d["target_atom_id"]: d for d in item_dba["decisions"] if d["action"] == "drop"}

    added_ids = [aid for aid in cur_final_map if aid not in dba_final_map]
    removed_ids = [aid for aid in dba_final_map if aid not in cur_final_map]

    # 收集基线中所有由于 full_body_shot 构图原子误删的目标 ID 集合
    dba_fbs_dropped_targets: Set[str] = set()
    for d in item_dba["decisions"]:
        if d["rule_id"] == "nudity_clothing_conflicts" and d["action"] == "drop":
            winners = [dba_src.get(wid) for wid in d.get("winner_atom_ids", [])]
            if any(w and "full_body" in w.get("visible_regions", []) and w.get("source_slot") != "nudity" for w in winners):
                dba_fbs_dropped_targets.add(d["target_atom_id"])

    cur_carrier_bindings = item_cur.get("carrier_bindings", {})

    # A: 核验每一个新增原子 (added_ids)
    for aid in added_ids:
        atom = cur_final_map[aid]

        # A1: 衍生原子 (由 Current 生产决策产生，如 material_penetration)
        if aid in cur_decs_by_prod:
            prod_dec = cur_decs_by_prod[aid]
            # 严格核对生产决策的 after_text 与当前终态文本
            if atom["text"] != prod_dec.get("after_text"):
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_ATOM_TEXT_MISMATCH: Atom '{aid}' text '{atom['text']}' "
                    f"does not match decision after_text '{prod_dec.get('after_text')}'"
                )
                continue

            parent_id = prod_dec.get("target_atom_id")
            if not parent_id or parent_id not in cur_src or parent_id not in dba_src:
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_PARENT_NOT_IN_SOURCE: Parent '{parent_id}' of produced atom '{aid}' "
                    f"is missing from source atoms"
                )
                continue

            if cur_src[parent_id]["text"] != dba_src[parent_id]["text"] or cur_src[parent_id]["text"] != prod_dec.get("before_text"):
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_PARENT_TEXT_MISMATCH: Parent '{parent_id}' text mismatch: "
                    f"cur_src='{cur_src[parent_id]['text']}' vs dba_src='{dba_src[parent_id]['text']}' vs dec_before='{prod_dec.get('before_text')}'"
                )
                continue

            if parent_id not in dba_drops_by_target:
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_PARENT_NOT_DROPPED_IN_DBA: Parent '{parent_id}' was not dropped in dba5861"
                )
                continue

            if parent_id not in dba_fbs_dropped_targets:
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_PARENT_NOT_FBS_DROPPED: Parent '{parent_id}' drop was not caused by full_body_shot framing"
                )
                continue

            explained_attributions.append({
                "atom_id": aid,
                "text": atom["text"],
                "source_slot": atom["source_slot"],
                "category": "produced_atom_from_restored_target",
                "parent_id": parent_id,
                "rule_id": prod_dec["rule_id"],
                "producer_decision": prod_dec,
            })

        # A2: 原生来源原子直接恢复
        elif aid in dba_drops_by_target:
            dba_drop = dba_drops_by_target[aid]

            if aid not in cur_src or aid not in dba_src:
                unexplained_reasons.append(
                    f"UNEXPLAINED_REUSED_ID_NOT_IN_SOURCE: Restored atom '{aid}' was not found in source atoms"
                )
                continue

            src_dba = dba_src[aid]
            src_cur = cur_src[aid]

            # 统一来源签名检查 (Unified Source Signature Check)
            # 原生恢复原子的 ID、文本、槽位、条目 ID、tag/span 序数，必须在基线来源、当前来源、当前终态之间 100% 严格一致
            sig_dba = atom_signature(src_dba)
            sig_cur = atom_signature(src_cur)
            sig_fin = atom_signature(atom)

            if sig_dba != sig_cur or sig_cur != sig_fin or dba_drop.get("before_text") != atom["text"]:
                if atom["text"] != src_dba["text"] or atom["text"] != src_cur["text"] or dba_drop.get("before_text") != atom["text"]:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_TAMPERED_RESTORED_ATOM: Atom '{aid}' text '{atom['text']}' does not match "
                        f"cur_src='{src_cur['text']}', dba_src='{src_dba['text']}', or drop before_text='{dba_drop.get('before_text')}'"
                    )
                if atom.get("source_slot") != src_dba.get("source_slot") or atom.get("source_slot") != src_cur.get("source_slot"):
                    unexplained_reasons.append(
                        f"UNEXPLAINED_FIELD_DRIFT: Restored atom '{aid}' source_slot drifted: "
                        f"cur_final='{atom.get('source_slot')}', cur_src='{src_cur.get('source_slot')}', dba_src='{src_dba.get('source_slot')}'"
                    )
                if atom.get("source_item_id") != src_dba.get("source_item_id") or atom.get("source_item_id") != src_cur.get("source_item_id"):
                    unexplained_reasons.append(
                        f"UNEXPLAINED_FIELD_DRIFT: Restored atom '{aid}' source_item_id drifted: "
                        f"cur_final='{atom.get('source_item_id')}', cur_src='{src_cur.get('source_item_id')}', dba_src='{src_dba.get('source_item_id')}'"
                    )
                if atom.get("tag_order") != src_dba.get("tag_order") or atom.get("tag_order") != src_cur.get("tag_order"):
                    unexplained_reasons.append(
                        f"UNEXPLAINED_ORDER_FIELD_DRIFT: Restored atom '{aid}' tag_order drifted: "
                        f"cur_final={atom.get('tag_order')}, cur_src={src_cur.get('tag_order')}, dba_src={src_dba.get('tag_order')}"
                    )
                if atom.get("span_order") != src_dba.get("span_order") or atom.get("span_order") != src_cur.get("span_order"):
                    unexplained_reasons.append(
                        f"UNEXPLAINED_ORDER_FIELD_DRIFT: Restored atom '{aid}' span_order drifted: "
                        f"cur_final={atom.get('span_order')}, cur_src={src_cur.get('span_order')}, dba_src={src_dba.get('span_order')}"
                    )
                continue

            # A2a: full_body_shot 构图误判直接恢复
            if aid in dba_fbs_dropped_targets:
                explained_attributions.append({
                    "atom_id": aid,
                    "text": atom["text"],
                    "source_slot": atom["source_slot"],
                    "category": "full_body_shot_nudity_misjudgment_fixed",
                    "dba_drop_decision": dba_drop,
                })

            # A2b: 级联恢复（严格建立“全景误删载体 -> 当前载体恢复 -> 状态成功绑定该载体”证据链）
            elif (
                dba_drop["rule_id"] == "clothing_style_state_coherence"
                and dba_drop.get("reason_code") == "state_lacks_carrier"
            ):
                cb = cur_carrier_bindings.get(aid)
                if not cb:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_CARRIER_CASCADE_FAILURE: State atom '{aid}' ('{atom['text']}') "
                        f"has NO bound carrier entity in Current final output"
                    )
                    continue

                carrier_members = set(cb.get("carrier_member_atom_ids", []))
                overlap = carrier_members & dba_fbs_dropped_targets
                if not overlap:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_CARRIER_CASCADE_FAILURE: Bound carrier '{cb.get('carrier_selected_id')}' "
                        f"member atoms {carrier_members} were NOT dropped by full_body_shot in dba5861"
                    )
                    continue

                explained_attributions.append({
                    "atom_id": aid,
                    "text": atom["text"],
                    "source_slot": atom["source_slot"],
                    "category": "carrier_cascade_restored",
                    "carrier_selected_id": cb.get("carrier_selected_id"),
                    "restored_carrier_member_atom_ids": list(overlap),
                    "dba_drop_decision": dba_drop,
                })

            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNKNOWN_DBA_DROP: Atom '{aid}' was dropped in dba5861 by unexpected rule: "
                    f"{dba_drop.get('rule_id')} ({dba_drop.get('reason_code')})"
                )

        else:
            unexplained_reasons.append(
                f"UNEXPLAINED_ADDED_ATOM: Atom '{aid}' ('{atom['text']}') appeared in Current but was NEVER dropped in dba5861"
            )

    # B: 核验每一个缺失原子 (removed_ids)
    for aid in removed_ids:
        unexplained_reasons.append(
            f"UNEXPLAINED_REMOVED_ATOM: Atom '{aid}' ('{dba_final_map[aid]['text']}') was in dba5861 final atoms but missing in Current"
        )

    is_fully_explained = len(unexplained_reasons) == 0

    return {
        "seed": seed,
        "is_fully_explained": is_fully_explained,
        "unexplained_reasons": unexplained_reasons,
        "explained_attributions": explained_attributions,
        "added_count": len(added_ids),
        "removed_count": len(removed_ids),
        "dba_positive": p_dba,
        "cur_positive": p_cur,
        "dba_final_atoms": dba_final,
        "cur_final_atoms": cur_final,
        "dba_source_atoms": item_dba.get("source_atoms", []),
        "cur_source_atoms": item_cur.get("source_atoms", []),
        "dba_decisions": item_dba["decisions"],
        "cur_decisions": item_cur["decisions"],
        "carrier_bindings": cur_carrier_bindings,
    }


def main():
    seeds_count = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    print(f"[*] Starting strict atom-by-atom structural divergence audit: {DBA_COMMIT} (Baseline) vs Current Workspace...")

    dba_dir = Path(tempfile.mkdtemp(prefix="iykyk_dba5861_export_"))
    print(f"[*] Exporting git commit {DBA_COMMIT} to {dba_dir}...")
    export_commit(DBA_COMMIT, dba_dir)

    t0 = time.time()
    print(f"[*] Generating {seeds_count} seeds for baseline {DBA_COMMIT}...")
    dba_data, dba_hash = run_batch_parallel(dba_dir, seeds_count)
    t_dba = time.time() - t0
    print(f"[+] Baseline {DBA_COMMIT} batch hash: {dba_hash} (took {t_dba:.1f}s)")

    t0 = time.time()
    print(f"[*] Generating {seeds_count} seeds for Current Workspace...")
    cur_data, cur_hash = run_batch_parallel(REPO_DIR, seeds_count)
    t_cur = time.time() - t0
    print(f"[+] Current Workspace batch hash: {cur_hash} (took {t_cur:.1f}s)")

    diff_seeds: List[Dict[str, Any]] = []
    unexplained_seeds: List[Dict[str, Any]] = []
    atom_category_counts: Dict[str, int] = {}
    identical_count = 0

    # 全量 10,000 种子结构化签名与序列核验，绝不放过正向文本相同但原子顺序/属性漂移的暗箱差异
    for s in range(seeds_count):
        item_dba = dba_data[s]
        item_cur = cur_data[s]

        dba_sigs = [atom_signature(a) for a in item_dba["final_atoms"]]
        cur_sigs = [atom_signature(a) for a in item_cur["final_atoms"]]

        cur_ids = [a["atom_id"] for a in item_cur["final_atoms"]]
        dba_ids = [a["atom_id"] for a in item_dba["final_atoms"]]
        has_dup = (len(cur_ids) != len(set(cur_ids))) or (len(dba_ids) != len(set(dba_ids)))

        is_structurally_identical = (
            dba_sigs == cur_sigs
            and item_dba["positive"] == item_cur["positive"]
            and not has_dup
        )

        if is_structurally_identical:
            identical_count += 1
        else:
            audit_item = attribute_atom_divergence(item_dba, item_cur)
            diff_seeds.append(audit_item)
            if not audit_item["is_fully_explained"]:
                unexplained_seeds.append(audit_item)
            for attr in audit_item["explained_attributions"]:
                cat = attr["category"]
                atom_category_counts[cat] = atom_category_counts.get(cat, 0) + 1

    divergent_count = len(diff_seeds)
    unexplained_count = len(unexplained_seeds)

    print("\n" + "=" * 80)
    print("STRICT STRUCTURAL ATOM-BY-ATOM DIVERGENCE AUDIT REPORT (dba5861 -> Current Workspace)")
    print(f"Total Seeds Checked    : {seeds_count}")
    print(f"Identical Seeds        : {identical_count} ({identical_count / seeds_count * 100:.2f}%) [Structurally & Serially Invariant]")
    print(f"Divergent Seeds        : {divergent_count} ({divergent_count / seeds_count * 100:.2f}%)")
    print(f"Unexplained Seeds      : {unexplained_count}")
    print(f"Baseline dba5861 Hash  : {dba_hash}")
    print(f"Current Workspace Hash : {cur_hash}")
    print("-" * 80)
    print("Atom-Level Causal Attribution Breakdown:")
    for cat, cnt in sorted(atom_category_counts.items(), key=lambda x: -x[1]):
        print(f"  - {cat}: {cnt} atoms")
    print("=" * 80)

    # 导出可复核结构化证据 JSON 报告
    out_json = REPO_DIR / "scratch" / "dba5861_to_current_audit.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({
            "seeds_count": seeds_count,
            "baseline_commit": DBA_COMMIT,
            "baseline_batch_hash": dba_hash,
            "current_batch_hash": cur_hash,
            "identical_seeds_count": identical_count,
            "divergent_seeds_count": divergent_count,
            "unexplained_seeds_count": unexplained_count,
            "atom_category_summary": atom_category_counts,
            "divergent_seeds": diff_seeds,
        }, f, indent=2, ensure_ascii=False)
    print(f"[+] Full audit report written to: {out_json}")

    if unexplained_count > 0:
        print(f"\n❌ AUDIT FAILED: {unexplained_count} unexplained seeds detected!")
        for u in unexplained_seeds[:5]:
            print(f"  [Seed {u['seed']}] Unexplained reasons:")
            for r in u["unexplained_reasons"]:
                print(f"    - {r}")
        sys.exit(1)
    else:
        print("\n✅ AUDIT PASSED: 100% of differences causally proven at atom level, 0 unexplained diffs.")
        sys.exit(0)


if __name__ == "__main__":
    main()
