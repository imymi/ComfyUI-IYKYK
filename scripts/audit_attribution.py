"""
audit_attribution.py — 全量种子审计差异原子级精确归因引擎

核心规范：
1. 以原子及来源身份建立差异映射，通过 target_atom_id / produced_atom_ids 精确对应决策；
2. 彻底杜绝裸逗号拆分 (split(",")) 和子串模糊认领 (rtag in before_text)；
3. 任何未在受测版本实际采样的原子，绝不可被归因为受测版本的消解决策；
4. 严格区分款式抽样位移 (category shift)、槽位级联 PRNG 位移 (intra-slot shift) 与具体消解决策 (decision drop/replace)；
5. 任何无法证明来源的增删直接标记为 UNEXPLAINED 并触发硬失败。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


CLOTHING_SLOTS = frozenset({
    "clothing",
    "base_clothing",
    "clothing_state",
    "clothing_extension",
    "linkage",
})


def attribute_seed_diff(
    s: int,
    rc8_clothing_id: Optional[str] = None,
    cur_clothing_id: str = "",
    rc8_src_atoms: Optional[List[Dict[str, Any]]] = None,
    rc8_final_atoms: Optional[List[Dict[str, Any]]] = None,
    rc8_decisions: Optional[List[Dict[str, Any]]] = None,
    cur_src_atoms: Optional[List[Dict[str, Any]]] = None,
    cur_final_atoms: Optional[List[Dict[str, Any]]] = None,
    cur_decisions: Optional[List[Dict[str, Any]]] = None,
    *,
    base_clothing_id: Optional[str] = None,
    base_src_atoms: Optional[List[Dict[str, Any]]] = None,
    base_final_atoms: Optional[List[Dict[str, Any]]] = None,
    base_decisions: Optional[List[Dict[str, Any]]] = None,
    base_catalog_size: Optional[int] = None,
    cur_catalog_size: Optional[int] = None,
) -> Dict[str, Any]:
    """以原子及来源身份建立差异映射，通过 target_atom_id / produced_atom_ids 对应决策，绝无裸逗号拆分和子串认领。"""
    effective_base_clothing_id = base_clothing_id if base_clothing_id is not None else (rc8_clothing_id or "")
    effective_base_src_atoms = base_src_atoms if base_src_atoms is not None else (rc8_src_atoms or [])
    effective_base_final_atoms = base_final_atoms if base_final_atoms is not None else (rc8_final_atoms or [])
    effective_base_decisions = base_decisions if base_decisions is not None else (rc8_decisions or [])
    cur_src_atoms = cur_src_atoms or []
    cur_final_atoms = cur_final_atoms or []
    cur_decisions = cur_decisions or []

    sampling_delta = (effective_base_clothing_id != cur_clothing_id)

    # 源原子按 ID 与文本索引
    cur_src_by_id = {a["atom_id"]: a for a in cur_src_atoms if a.get("atom_id")}
    cur_src_by_text: Dict[str, List[Dict[str, Any]]] = {}
    for a in cur_src_atoms:
        cur_src_by_text.setdefault(a["text"], []).append(a)

    base_src_by_text: Dict[str, List[Dict[str, Any]]] = {}
    for a in effective_base_src_atoms:
        base_src_by_text.setdefault(a["text"], []).append(a)

    # 决议索引：精确按 target_atom_id 与 produced_atom_ids 索引
    cur_decs_by_target = {d["target_atom_id"]: d for d in cur_decisions if d.get("target_atom_id")}
    cur_produced_dec: Dict[str, Dict[str, Any]] = {}
    cur_produced_ids: set[str] = set()
    for d in cur_decisions:
        for pid in d.get("produced_atom_ids", []):
            cur_produced_ids.add(pid)
            cur_produced_dec[pid] = d

    base_decs_by_target = {d["target_atom_id"]: d for d in effective_base_decisions if d.get("target_atom_id")}

    # ─── 阶段 1: Current 完整最终原子集合的版本内来源身份独立核验 ───
    # 必须在跨版本比较之前执行：保留原子必须严格匹配源原子的 ID 与内容；新生成原子必须匹配对应生成决策。
    # 来源错误独立汇总为阻断，绝不依赖是否存在跨版本文本增删。
    unsourced_atoms: List[Dict[str, Any]] = []
    for atom in cur_final_atoms:
        atom_id = atom.get("atom_id", "")
        text = atom.get("text", "")
        slot = (atom.get("slot") or "").lower()

        is_in_cur_src = bool(atom_id and atom_id in cur_src_by_id and cur_src_by_id[atom_id].get("text") == text)
        is_produced_by_cur = bool(atom_id and atom_id in cur_produced_ids)

        if not is_in_cur_src and not is_produced_by_cur:
            unsourced_atoms.append({
                "text": text,
                "category": "UNEXPLAINED_UNSOURCED_ATOM",
                "slot": slot,
                "item_id": atom.get("item_id", ""),
                "atom_id": atom_id,
                "error": f"Atom '{text}' ({atom_id}) appeared in Current output but was neither registered in cur_src_atoms with matching ID and text nor produced by any cur_decision",
            })
    unsourced_ids = {a["atom_id"] for a in unsourced_atoms}

    # ─── 阶段 2: 跨版本差异计算与归因 ───
    cur_final_texts = {a["text"] for a in cur_final_atoms}
    base_final_texts = {a["text"] for a in effective_base_final_atoms}

    # 原子级比较：从最终产出原子中找出差异（杜绝按逗号拆分字符串破坏复合词条）
    removed_atoms = [a for a in effective_base_final_atoms if a["text"] not in cur_final_texts]
    added_atoms = [b for b in cur_final_atoms if b["text"] not in base_final_texts]

    # 文本与文案参数格式化
    if base_catalog_size is not None and cur_catalog_size is not None:
        expansion_text = f"due to {base_catalog_size} -> {cur_catalog_size} styles in catalog"
        intra_text = f"PRNG draw on {cur_catalog_size} styles shifted intra-slot tag sampling"
    else:
        expansion_text = "due to clothing catalog expansion"
        intra_text = "PRNG draw on expanded catalog shifted intra-slot tag sampling"

    removed_attributions = []
    for ra in removed_atoms:
        text = ra["text"]
        slot = (ra.get("slot") or "").lower()
        # 严格检查：Current 是否实际采样了该完整原子？
        cur_matches = cur_src_by_text.get(text, [])
        if cur_matches:
            # Current 实际采样了该原子：必须通过 target_atom_id 精确匹配删除决策，文本必须 100% 绝对一致（严禁子串匹配）
            cur_atom = cur_matches[0]
            cur_atom_id = cur_atom["atom_id"]
            drop_dec = cur_decs_by_target.get(cur_atom_id)
            if drop_dec and drop_dec.get("before_text") == text:
                removed_attributions.append({
                    "text": text,
                    "category": "current_decision",
                    "rule_id": drop_dec["rule_id"],
                    "reason_code": drop_dec["reason_code"],
                    "action": drop_dec["action"],
                    "decision_id": drop_dec["decision_id"],
                    "target_atom_id": cur_atom_id,
                    "before_text": drop_dec["before_text"],
                })
            else:
                removed_attributions.append({
                    "text": text,
                    "category": "UNEXPLAINED_SILENT_DROP",
                    "error": f"Atom '{text}' sampled in Current as {cur_atom_id} but missing in output without matching drop decision",
                })
        else:
            # Current 从未采样该原子！绝不可被任何 Current 决策认领，归因为抽样池扩充产生的位移
            if slot not in CLOTHING_SLOTS:
                removed_attributions.append({
                    "text": text,
                    "category": "UNEXPLAINED_NON_CLOTHING_SAMPLING_SHIFT",
                    "slot": slot,
                    "item_id": ra.get("item_id", ""),
                    "error": f"Non-clothing slot '{slot}' atom '{text}' missing in Current, cannot be attributed to clothing pool expansion",
                })
            elif sampling_delta:
                removed_attributions.append({
                    "text": text,
                    "category": "clothing_pool_expansion_category_shift",
                    "slot": slot,
                    "item_id": ra.get("item_id", ""),
                    "base_clothing_id": effective_base_clothing_id,
                    "rc8_clothing_id": effective_base_clothing_id,
                    "cur_clothing_id": cur_clothing_id,
                    "rationale": f"Sampled clothing shifted '{effective_base_clothing_id}' -> '{cur_clothing_id}' {expansion_text}",
                })
            else:
                removed_attributions.append({
                    "text": text,
                    "category": "clothing_pool_expansion_intra_slot_shift",
                    "slot": slot,
                    "item_id": ra.get("item_id", ""),
                    "base_clothing_id": effective_base_clothing_id,
                    "rc8_clothing_id": effective_base_clothing_id,
                    "cur_clothing_id": cur_clothing_id,
                    "rationale": f"Same clothing '{cur_clothing_id}', {intra_text}",
                })

    added_attributions = list(unsourced_atoms)
    for aa in added_atoms:
        text = aa["text"]
        atom_id = aa.get("atom_id", "")
        slot = (aa.get("slot") or "").lower()

        # 若已在阶段 1 判定为无来源原子，跳过跨版本属性分类
        if atom_id in unsourced_ids:
            continue

        # 1. 是否由 Current 决策生成？精确匹配 produced_atom_ids
        prod_dec = cur_produced_dec.get(atom_id)
        if prod_dec:
            added_attributions.append({
                "text": text,
                "category": "current_replacement" if prod_dec.get("action") == "replace" else "current_decision_produced",
                "rule_id": prod_dec["rule_id"],
                "reason_code": prod_dec["reason_code"],
                "action": prod_dec["action"],
                "decision_id": prod_dec["decision_id"],
                "produced_atom_ids": prod_dec.get("produced_atom_ids", []),
                "after_text": prod_dec.get("after_text", ""),
            })
        else:
            # 2. 属于普通采样原子：必须核查槽位是否归属衣物领域
            if slot not in CLOTHING_SLOTS:
                added_attributions.append({
                    "text": text,
                    "category": "UNEXPLAINED_NON_CLOTHING_SAMPLING_SHIFT",
                    "slot": slot,
                    "item_id": aa.get("item_id", ""),
                    "atom_id": atom_id,
                    "error": f"Non-clothing slot '{slot}' atom '{text}' cannot be attributed to clothing pool expansion",
                })
                continue

            # 3. 检查 Base 是否采样过
            base_matches = base_src_by_text.get(text, [])
            if not base_matches:
                # Base 未采样：属于 Current 新采样的原子
                if sampling_delta:
                    added_attributions.append({
                        "text": text,
                        "category": "clothing_pool_expansion_category_shift",
                        "slot": slot,
                        "item_id": aa.get("item_id", ""),
                        "base_clothing_id": effective_base_clothing_id,
                        "rc8_clothing_id": effective_base_clothing_id,
                        "cur_clothing_id": cur_clothing_id,
                        "rationale": f"Newly sampled in Current due to clothing shift '{effective_base_clothing_id}' -> '{cur_clothing_id}'",
                    })
                else:
                    added_attributions.append({
                        "text": text,
                        "category": "clothing_pool_expansion_intra_slot_shift",
                        "slot": slot,
                        "item_id": aa.get("item_id", ""),
                        "base_clothing_id": effective_base_clothing_id,
                        "rc8_clothing_id": effective_base_clothing_id,
                        "cur_clothing_id": cur_clothing_id,
                        "rationale": f"Newly sampled in Current due to intra-slot PRNG shift on '{cur_clothing_id}'",
                    })
            else:
                # Base 采样过该原子，核查是否在 Base 中被删除决策剔除而在 Current 中得以幸存
                base_atom = base_matches[0]
                base_atom_id = base_atom["atom_id"]
                base_drop = base_decs_by_target.get(base_atom_id)
                if base_drop and base_drop.get("before_text") == text:
                    added_attributions.append({
                        "text": text,
                        "category": "rc8_decision_dropped_now_survived",
                        "base_category": "base_decision_dropped_now_survived",
                        "slot": slot,
                        "item_id": aa.get("item_id", ""),
                        "base_rule_id": base_drop["rule_id"],
                        "rc8_rule_id": base_drop["rule_id"],
                        "base_reason_code": base_drop["reason_code"],
                        "rc8_reason_code": base_drop["reason_code"],
                        "base_target_atom_id": base_atom_id,
                        "rc8_target_atom_id": base_atom_id,
                    })
                else:
                    added_attributions.append({
                        "text": text,
                        "category": "UNEXPLAINED_RC8_SILENT_DROP",
                        "error": f"Atom '{text}' was in base source as {base_atom_id} but absent in base final without drop decision",
                    })

    unexplained_rem = [a for a in removed_attributions if a["category"].startswith("UNEXPLAINED")]
    unexplained_add = [a for a in added_attributions if a["category"].startswith("UNEXPLAINED")]
    is_seed_unexplained = bool(unexplained_rem or unexplained_add)

    coherence_decisions = [
        d for d in cur_decisions
        if d["rule_id"] == "clothing_style_state_coherence"
    ]

    # 确定种子级别主归因
    if is_seed_unexplained:
        primary_category = "unexplained"
        summary = f"UNEXPLAINED: {len(unexplained_rem)} removed, {len(unexplained_add)} added unexplained"
    elif sampling_delta:
        primary_category = "clothing_pool_expansion_category_shift"
        summary = f"clothing_pool_expansion_category_shift: sampled clothing shifted '{effective_base_clothing_id}' -> '{cur_clothing_id}' {expansion_text}"
    elif coherence_decisions:
        primary_category = coherence_decisions[0]["reason_code"]
        summary = f"{primary_category}: same clothing '{cur_clothing_id}', dangling/incompatible modifiers pruned per carrier coherence"
    else:
        primary_category = "clothing_pool_expansion_intra_slot_shift"
        summary = f"clothing_pool_expansion_intra_slot_shift: same clothing '{cur_clothing_id}', {intra_text}"

    return {
        "seed": s,
        "base_clothing_id": effective_base_clothing_id,
        "rc8_clothing_id": effective_base_clothing_id,
        "cur_clothing_id": cur_clothing_id,
        "sampling_delta": sampling_delta,
        "removed_atoms": [a["text"] for a in removed_atoms],
        "added_atoms": [a["text"] for a in added_atoms],
        "removed_attributions": removed_attributions,
        "added_attributions": added_attributions,
        "is_unexplained": is_seed_unexplained,
        "unexplained_removed": unexplained_rem,
        "unexplained_added": unexplained_add,
        "coherence_decisions": coherence_decisions,
        "primary_category": primary_category,
        "attribution_summary": summary,
    }
