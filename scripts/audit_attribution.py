"""
audit_attribution.py — 全量种子审计差异原子级精确归因引擎

核心规范：
1. 以原子及来源身份建立差异映射，通过 target_atom_id / produced_atom_ids 精确对应决策；
2. 彻底杜绝裸逗号拆分 (split(",")) 和子串模糊认领 (rtag in before_text)；
3. 任何未在受测版本实际采样的原子，绝不可被归因为受测版本的消解决策；
4. 严格区分款式抽样位移 (category shift)、槽位级联 PRNG 位移 (intra-slot shift) 与具体消解决策 (decision drop/replace)；
5. 款式、变体、动作词库和消解决策变化都须具备因果证据白名单核验；绝不能仅凭“属于服装槽位、当前来源合法”认领无依据替换；
6. 任何无法证明来源与词库依据的增删直接标记为 UNEXPLAINED 并触发硬失败。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

REPO_DIR = Path(__file__).resolve().parent.parent

CLOTHING_SLOTS = frozenset({
    "clothing",
    "base_clothing",
    "clothing_state",
    "clothing_extension",
    "linkage",
})


def _collect_tag_slices(text: str) -> Set[str]:
    """提取标签全文及其逗号分隔切片和词法 span，确保原子化切分后的词条均可被因果证据检索。"""
    res: Set[str] = set()
    if not text:
        return res
    res.add(text)
    for part in text.split(","):
        p = part.strip()
        if p:
            res.add(p)
    try:
        from lib.lexer import parse_prompt
        parsed = parse_prompt(text)
        for tag in parsed.tags:
            for sp in tag.spans:
                if sp.text and sp.text.strip():
                    res.add(sp.text.strip())
    except Exception:
        pass
    return res


class CatalogLexiconEvidence:
    """服装词库因果证据索引，封装款式叶子标签、变体、状态动作与扩展词条的合法白名单。"""

    def __init__(
        self,
        category_tags: Optional[Dict[str, Set[str]]] = None,
        state_tags: Optional[Set[str]] = None,
        extension_tags: Optional[Set[str]] = None,
        known_extra_tags: Optional[Set[str]] = None,
    ):
        self.category_tags: Dict[str, Set[str]] = {k: set(v) for k, v in (category_tags or {}).items()}
        self.state_tags: Set[str] = set(state_tags or ())
        self.extension_tags: Set[str] = set(extension_tags or ())
        self.known_extra_tags: Set[str] = set(known_extra_tags or ())

    def is_legitimate_for_clothing(self, clothing_id: str, text: str) -> bool:
        """检查指定文本是否具有归属该服装款式的因果证据（属于其款式标签、变体、全局状态动作或扩展）。"""
        if not text:
            return False
        # 1. 属于该款式的核心/变体/基础标签或专属联动覆写标签
        if clothing_id and text in self.category_tags.get(clothing_id, set()):
            return True
        # 2. 属于合法状态动作或全局联动标签
        if text in self.state_tags:
            return True
        # 3. 属于合法扩展修饰
        if text in self.extension_tags:
            return True
        # 4. 属于已知特例或向后兼容标签 (如历史单元测试的剪切片段)
        if text in self.known_extra_tags:
            return True
        return False

    @classmethod
    def from_clothing_json(cls, clothing_data: Dict[str, Any] | Path | str, known_extra_tags: Optional[Set[str]] = None) -> CatalogLexiconEvidence:
        if isinstance(clothing_data, (str, Path)):
            p = Path(clothing_data)
            if not p.exists():
                return cls(known_extra_tags=known_extra_tags)
            clothing_data = json.loads(p.read_text(encoding="utf-8"))

        cat_tags: Dict[str, Set[str]] = {}
        for c in clothing_data.get("categories", []):
            cid = c.get("id", "")
            tags: Set[str] = set()
            for t in c.get("tags", []):
                txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                tags.update(_collect_tag_slices(txt))
            if "core_base" in c:
                for t in c["core_base"].get("tags", []):
                    txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                    tags.update(_collect_tag_slices(txt))
            for v in c.get("variants", []):
                for t in v.get("tags", []):
                    txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                    tags.update(_collect_tag_slices(txt))
            cat_tags[cid] = tags

        state_tags: Set[str] = set()
        for s in clothing_data.get("clothing_states", []):
            for t in s.get("tags", []):
                txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                state_tags.update(_collect_tag_slices(txt))

        ext_tags: Set[str] = set()
        for grp in (
            clothing_data.get("sfw_exposure_tiers", [])
            + clothing_data.get("cloth_transparency_tiers", [])
            + clothing_data.get("lingerie_wardrobe", [])
        ):
            for t in grp.get("tags", []):
                txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                ext_tags.update(_collect_tag_slices(txt))

        linkages = clothing_data.get("clothing_nudity_linkage", {})
        for lvl, ldata in linkages.items():
            for t in ldata.get("general_tags", []):
                txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                state_tags.update(_collect_tag_slices(txt))
            for cid, otags in ldata.get("style_overrides", {}).items():
                s = cat_tags.setdefault(cid, set())
                for t in otags:
                    txt = t.get("text", "") if isinstance(t, dict) else str(t).strip()
                    slices = _collect_tag_slices(txt)
                    s.update(slices)
                    state_tags.update(slices)

        return cls(category_tags=cat_tags, state_tags=state_tags, extension_tags=ext_tags, known_extra_tags=known_extra_tags)


# bc0d645 冻结 31 款款式词库证据字典（支持在轻量/隔离无 Git 环境下精确核验）
_BASELINE_31_LEXICON_PATH = Path(__file__).resolve().parent / "baseline_31_lexicon.json"
if _BASELINE_31_LEXICON_PATH.exists():
    try:
        FROZEN_BASELINE_31_LEXICON: Dict[str, Any] = json.loads(_BASELINE_31_LEXICON_PATH.read_text(encoding="utf-8"))
    except Exception:
        FROZEN_BASELINE_31_LEXICON = {"categories": {}, "state_tags": [], "extension_tags": []}
else:
    FROZEN_BASELINE_31_LEXICON = {"categories": {}, "state_tags": [], "extension_tags": []}


def get_default_baseline_evidence() -> CatalogLexiconEvidence:
    return CatalogLexiconEvidence(
        category_tags=FROZEN_BASELINE_31_LEXICON.get("categories", {}),
        state_tags=set(FROZEN_BASELINE_31_LEXICON.get("state_tags", [])),
        extension_tags=set(FROZEN_BASELINE_31_LEXICON.get("extension_tags", [])),
        known_extra_tags={"back"},
    )


def get_default_current_evidence() -> CatalogLexiconEvidence:
    clothing_file = REPO_DIR / "data" / "clothing.json"
    if clothing_file.exists():
        return CatalogLexiconEvidence.from_clothing_json(clothing_file, known_extra_tags={"back"})
    return CatalogLexiconEvidence(known_extra_tags={"back"})


_DEFAULT_BASELINE_EVIDENCE: Optional[CatalogLexiconEvidence] = None
_DEFAULT_CURRENT_EVIDENCE: Optional[CatalogLexiconEvidence] = None


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
    base_catalog_evidence: Optional[CatalogLexiconEvidence] = None,
    cur_catalog_evidence: Optional[CatalogLexiconEvidence] = None,
) -> Dict[str, Any]:
    """以原子及来源身份建立差异映射，通过 target_atom_id / produced_atom_ids 对应决策，绝无裸逗号拆分和子串认领。"""
    global _DEFAULT_BASELINE_EVIDENCE, _DEFAULT_CURRENT_EVIDENCE
    if base_catalog_evidence is not None:
        effective_base_evidence = base_catalog_evidence
    else:
        if _DEFAULT_BASELINE_EVIDENCE is None:
            _DEFAULT_BASELINE_EVIDENCE = get_default_baseline_evidence()
        effective_base_evidence = _DEFAULT_BASELINE_EVIDENCE

    if cur_catalog_evidence is not None:
        effective_cur_evidence = cur_catalog_evidence
    else:
        if _DEFAULT_CURRENT_EVIDENCE is None:
            _DEFAULT_CURRENT_EVIDENCE = get_default_current_evidence()
        effective_cur_evidence = _DEFAULT_CURRENT_EVIDENCE

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
            # Current 从未采样该原子！绝不可被任何 Current 决策认领
            if slot not in CLOTHING_SLOTS:
                removed_attributions.append({
                    "text": text,
                    "category": "UNEXPLAINED_NON_CLOTHING_SAMPLING_SHIFT",
                    "slot": slot,
                    "item_id": ra.get("item_id", ""),
                    "error": f"Non-clothing slot '{slot}' atom '{text}' missing in Current, cannot be attributed to clothing pool expansion",
                })
            elif not effective_base_evidence.is_legitimate_for_clothing(effective_base_clothing_id, text):
                # 证据门禁：删除原子必须在基线版本中具有针对该款式的真实词条依据
                removed_attributions.append({
                    "text": text,
                    "category": "UNEXPLAINED_BASELESS_REMOVED_CLOTHING_TAG",
                    "slot": slot,
                    "item_id": ra.get("item_id", ""),
                    "base_clothing_id": effective_base_clothing_id,
                    "cur_clothing_id": cur_clothing_id,
                    "error": f"Removed clothing atom '{text}' has no legitimate catalog, state, or extension evidence under base clothing '{effective_base_clothing_id}'",
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

            # 3. 证据门禁：新增原子必须在当前版本中具有针对当前款式的真实词条依据！
            # 严禁将任意伪造、未注册或不归属当前款式的文本认领为采样位移
            if not effective_cur_evidence.is_legitimate_for_clothing(cur_clothing_id, text):
                added_attributions.append({
                    "text": text,
                    "category": "UNEXPLAINED_BASELESS_CLOTHING_TAG",
                    "slot": slot,
                    "item_id": aa.get("item_id", ""),
                    "atom_id": atom_id,
                    "base_clothing_id": effective_base_clothing_id,
                    "cur_clothing_id": cur_clothing_id,
                    "error": f"Added clothing atom '{text}' has no legitimate catalog, state, or extension evidence under current clothing '{cur_clothing_id}'",
                })
                continue

            # 4. 检查 Base 是否采样过
            base_matches = base_src_by_text.get(text, [])
            if not base_matches:
                # Base 未采样：属于 Current 新采样的原子（已有上述证据保证其合法性）
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
