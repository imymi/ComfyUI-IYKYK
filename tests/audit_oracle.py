"""
tests/audit_oracle.py — 可复用的 Fail-Closed 审计 JSON 校验 Oracle (R3-P2-001)

用于验证 ComfyUI-IYKYK 诊断节点输出的确定性审计报告 JSON：
1. 8 大顶层字段白名单与 Draft-7 Schema 结构强校验；
2. 数学守恒公式精确闭环：
   source_atoms + produced == accepted_atoms + dropped + replaced + deduplicated + budget_filtered
3. 集合分区推导验证与 Atom 完整生命周期去向跟踪；
4. 全局 ID 唯一性、无碰撞与前后快照强一致性；
5. 决策状态机单次合法消费重放 (无重复消费、无死者复活、时序有效)；
6. before/after 文本快照 1:1 比对；
7. 溯源父关系合法性校验 (无孤儿、无自环、无未来引用)；
8. rules_applied 精确等于首次真实决策规则有序去重；
9. 数组业务顺序校验 (tag_order/span_order 非递减)；
10. 正常成功报告 unresolved_conflicts 强断言为空；
11. 正向提示词精确还原比对 (按 tag/span 组装协议，严禁子串包含模糊匹配)。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

_COMPILED_VALIDATORS: Dict[int, Any] = {}

EXPECTED_TOP_LEVEL_KEYS = {
    "schema_version",
    "effective_seed",
    "context_profile",
    "selections",
    "decisions",
    "rules_applied",
    "unresolved_conflicts",
    "counts",
}

EXPECTED_COUNT_KEYS = {
    "accepted_atoms",
    "budget_filtered",
    "deduplicated",
    "dropped",
    "injected",
    "produced",
    "replaced",
    "source_atoms",
}

EXPECTED_SELECTION_KEYS = {
    "accepted_atoms",
    "budget_filtered_records",
    "deduplicated_records",
    "entry_point",
    "mode",
    "parent_ids",
    "produced_atoms",
    "raw_value",
    "selected_id",
    "selector",
    "source_atoms",
}

SELECTOR_TO_UI_PARAM: Dict[str, str] = {
    "scene": "场景大类",
    "scene_theme": "场景主题",
    "theme": "剧情主题",
    "shot_type": "景别构图",
    "camera_angle": "拍摄视角",
    "nudity": "裸露等级",
    "clothing": "服装款式",
    "clothing_style": "服装款式",
    "clothing_state": "服装状态",
    "lighting": "光影预设",
    "pose": "姿势动作",
    "expression": "情绪表情",
    "film": "胶片风格",
    "makeup": "妆容细节",
    "hairstyle": "发型发色",
    "jewelry": "饰品头饰",
    "imperfections": "真实微瑕",
    "tattoo": "纹身标记",
    "props": "道具物件",
    "liquids": "液体效果",
    "character": "角色设定",
    "persona": "人格角色",
    "quality": "画质等级",
    "custom": "自定义追加",
    "preset": "预设模板",
    "preset_core": "预设模板",
    "recipe": "风格配方",
    "style_recipe": "风格配方",
}

_CATALOG_ITEM_IDS: Optional[Set[str]] = None
_RUNTIME_ITEM_IDS = {
    "auto_linkage",
    "quality_cctv",
    "quality_default",
    "quality_high",
    "quality_masterpiece",
    "quality_phone",
    "quality_standard",
}
_SEMANTIC_PREFIXES = {
    "camera_angle", "character", "clothing", "expression", "extension_family",
    "extension_tier", "film", "hairstyle", "imperfections", "jewelry", "lighting",
    "liquid", "liquids", "makeup", "nudity", "override", "pose", "prop", "props", "quality",
    "preset", "recipe", "scene", "shot_type", "slot", "state", "tattoo", "theme",
}
_RUNTIME_SEMANTIC_IDS = {
    "extension_family:cloth_transparency",
    "extension_family:lingerie_wardrobe",
    "extension_family:sfw_exposure",
    "override:linkage",
}


def _get_catalog_item_ids() -> Set[str]:
    global _CATALOG_ITEM_IDS
    if _CATALOG_ITEM_IDS is not None:
        return _CATALOG_ITEM_IDS
    from pathlib import Path
    data_dir = Path(__file__).resolve().parent.parent / "data"
    ids: Set[str] = set()
    if data_dir.is_dir():
        for p in data_dir.glob("*.json"):
            try:
                doc = json.loads(p.read_text("utf-8"))

                def collect(value: Any) -> None:
                    if isinstance(value, dict):
                        for key, child in value.items():
                            if (
                                isinstance(child, str)
                                and child
                                and (key == "id" or key == "code" or key.endswith("_id"))
                            ):
                                ids.add(child)
                            collect(child)
                    elif isinstance(value, list):
                        for child in value:
                            collect(child)

                collect(doc)
            except Exception as exc:
                raise AssertionError(f"Cannot load authoritative catalog '{p.name}': {exc}") from exc
    ids.update(_RUNTIME_ITEM_IDS)
    _CATALOG_ITEM_IDS = ids
    return _CATALOG_ITEM_IDS


def validate_audit_json_oracle(
    audit_data: str | Dict[str, Any],
    schema_doc: Optional[Dict[str, Any]] = None,
    expected_positive: Optional[str] = None,
    trusted_inputs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """执行可复用的 Fail-Closed 审计 JSON 强断言。任一不符合立即抛出 AssertionError。"""
    if isinstance(audit_data, str):
        try:
            audit = json.loads(audit_data)
        except Exception as e:
            raise AssertionError(f"Invalid JSON string in audit data: {e}") from e
    elif isinstance(audit_data, dict):
        audit = audit_data
    else:
        raise AssertionError(f"audit_data must be str or dict, got {type(audit_data).__name__}")

    # 1. 顶层键严格 8 个，无多余、无缺失
    actual_keys = set(audit.keys())
    if actual_keys != EXPECTED_TOP_LEVEL_KEYS:
        missing = EXPECTED_TOP_LEVEL_KEYS - actual_keys
        extra = actual_keys - EXPECTED_TOP_LEVEL_KEYS
        raise AssertionError(
            f"Top-level keys mismatch: missing={sorted(missing)}, extra={sorted(extra)}"
        )

    if audit.get("schema_version") != "1.0":
        raise AssertionError(f"schema_version must be '1.0', got {audit.get('schema_version')!r}")

    eff_seed = audit.get("effective_seed")
    if not isinstance(eff_seed, int) or isinstance(eff_seed, bool) or eff_seed < 0:
        raise AssertionError(f"effective_seed must be non-negative int, got {eff_seed!r}")

    # 2. Draft-7 Schema 校验 (Fail-Closed: 若传了 schema_doc 则必须具备 jsonschema 并且校验通过)
    if schema_doc is not None:
        if not HAS_JSONSCHEMA:
            raise AssertionError("jsonschema is required for schema validation but is not installed")
        doc_id = id(schema_doc)
        if doc_id not in _COMPILED_VALIDATORS:
            _COMPILED_VALIDATORS[doc_id] = jsonschema.Draft7Validator(schema_doc)
        validator = _COMPILED_VALIDATORS[doc_id]
        errors = list(validator.iter_errors(audit))
        if errors:
            msg_list = [f"{list(e.path)}: {e.message}" for e in errors]
            raise AssertionError(f"Draft-7 Schema validation failed with {len(errors)} error(s): {msg_list}")

    # 3. 正常成功报告 unresolved_conflicts 强断言为空 (R3-P2-001)
    unresolved = audit.get("unresolved_conflicts", [])
    if unresolved != [] and unresolved != ():
        raise AssertionError(
            f"Successful generation report must have empty unresolved_conflicts, got {unresolved!r}"
        )

    # 4. 提取 selections 并校验选择项契约与业务顺序 (R3-P1-001, R3-P2-001)
    selections = audit.get("selections", [])
    seen_selector_values: Set[Tuple[str, Optional[str]]] = set()

    for s_idx, sel in enumerate(selections):
        sel_keys = set(sel.keys())
        if sel_keys != EXPECTED_SELECTION_KEYS:
            missing_sel = EXPECTED_SELECTION_KEYS - sel_keys
            extra_sel = sel_keys - EXPECTED_SELECTION_KEYS
            raise AssertionError(
                f"Selection[{s_idx}] keys mismatch: missing={sorted(missing_sel)}, extra={sorted(extra_sel)}"
            )

        selector = sel.get("selector", "")
        raw_val = sel.get("raw_value")
        mode = sel.get("mode")
        entry_point = sel.get("entry_point")

        from lib.models import CANONICAL_SELECTORS_BY_ENTRY_POINT
        if selector not in CANONICAL_SELECTORS_BY_ENTRY_POINT.get(entry_point, ()):
            raise AssertionError(
                f"Selection[{s_idx}] selector '{selector}' is invalid for entry point '{entry_point}'"
            )

        if mode != "resolver":
            if raw_val is None:
                raise AssertionError(f"Selection[{s_idx}] for selector '{selector}' has null raw_value")
            if trusted_inputs is None:
                raise AssertionError(
                    "trusted_inputs is required to validate non-resolver selection raw values"
                )
            expected_raw = trusted_inputs.get(selector)
            if expected_raw is None and selector in SELECTOR_TO_UI_PARAM:
                expected_raw = trusted_inputs.get(SELECTOR_TO_UI_PARAM[selector])
            if expected_raw is None:
                raise AssertionError(
                    f"No trusted input supplied for non-resolver selector '{selector}'"
                )
            if raw_val != str(expected_raw):
                raise AssertionError(
                    f"Selection raw_value {raw_val!r} does not match trusted input {expected_raw!r} for selector '{selector}'"
                )

            pair = (selector, raw_val)
            if pair in seen_selector_values:
                raise AssertionError(
                    f"Duplicate selection record detected for selector '{selector}' and raw_value {raw_val!r}. "
                    f"A single selection must group multi-leaf atoms into one record."
                )
            seen_selector_values.add(pair)

        # 校验选择项内部数组业务顺序 (tag_order / span_order 非递减)
        for atom_list_key in ("source_atoms", "produced_atoms", "accepted_atoms"):
            atom_list = sel.get(atom_list_key, [])
            last_order = (-1, -1)
            for a in atom_list:
                cur_order = (a.get("tag_order", 0), a.get("span_order", 0))
                if cur_order < last_order:
                    raise AssertionError(
                        f"Selection '{selector}' {atom_list_key} ordering violated: "
                        f"order {cur_order} < previous order {last_order}"
                    )
                last_order = cur_order

    # 5. 构建全局原子字典与 ID 唯一性检验
    all_source_atoms = [a for sel in selections for a in sel.get("source_atoms", [])]
    all_produced_atoms = [a for sel in selections for a in sel.get("produced_atoms", [])]
    all_accepted_atoms = [a for sel in selections for a in sel.get("accepted_atoms", [])]
    all_dedup_records = [r for sel in selections for r in sel.get("deduplicated_records", [])]
    all_budget_records = [r for sel in selections for r in sel.get("budget_filtered_records", [])]

    source_owners = {
        a["atom_id"]: idx for idx, sel in enumerate(selections) for a in sel.get("source_atoms", [])
    }
    produced_owners = {
        a["atom_id"]: idx for idx, sel in enumerate(selections) for a in sel.get("produced_atoms", [])
    }
    accepted_owners = {
        a["atom_id"]: idx for idx, sel in enumerate(selections) for a in sel.get("accepted_atoms", [])
    }

    source_atom_ids = [a["atom_id"] for a in all_source_atoms]
    if len(source_atom_ids) != len(set(source_atom_ids)):
        duplicates = [x for x in source_atom_ids if source_atom_ids.count(x) > 1]
        raise AssertionError(f"Duplicate source atom IDs detected: {set(duplicates)}")

    produced_atom_ids = [a["atom_id"] for a in all_produced_atoms]
    if len(produced_atom_ids) != len(set(produced_atom_ids)):
        duplicates = [x for x in produced_atom_ids if produced_atom_ids.count(x) > 1]
        raise AssertionError(f"Duplicate produced atom IDs detected: {set(duplicates)}")

    collision = set(source_atom_ids) & set(produced_atom_ids)
    if collision:
        raise AssertionError(f"Collision between source atom IDs and produced atom IDs: {collision}")

    accepted_atom_ids = [a["atom_id"] for a in all_accepted_atoms]
    if len(accepted_atom_ids) != len(set(accepted_atom_ids)):
        duplicates = [x for x in accepted_atom_ids if accepted_atom_ids.count(x) > 1]
        raise AssertionError(f"Duplicate accepted atom IDs detected: {set(duplicates)}")

    source_map: Dict[str, Dict[str, Any]] = {a["atom_id"]: a for a in all_source_atoms}
    produced_map: Dict[str, Dict[str, Any]] = {a["atom_id"]: a for a in all_produced_atoms}
    all_registered_atoms: Dict[str, Dict[str, Any]] = {**source_map, **produced_map}
    accepted_map: Dict[str, Dict[str, Any]] = {a["atom_id"]: a for a in all_accepted_atoms}

    # 6. accepted 与 source/produced 载荷强一致性及 is_accepted 标记一致性
    for aid, acc_atom in accepted_map.items():
        if not acc_atom.get("is_accepted"):
            raise AssertionError(f"Accepted atom '{aid}' must have is_accepted=True")
        if aid not in all_registered_atoms:
            raise AssertionError(f"Accepted atom '{aid}' not found in registered source or produced atoms")
        orig_atom = all_registered_atoms[aid]
        for fld in (
            "id",
            "text",
            "source_slot",
            "source_item_id",
            "parent_ids",
            "semantic_ids",
            "tag_order",
            "span_order",
        ):
            if acc_atom.get(fld) != orig_atom.get(fld):
                raise AssertionError(
                    f"Accepted atom '{aid}' payload mismatch with registered snapshot for field '{fld}': "
                    f"accepted has {acc_atom.get(fld)!r}, snapshot has {orig_atom.get(fld)!r}"
                )

        registered_owner = source_owners.get(aid, produced_owners.get(aid))
        if accepted_owners.get(aid) != registered_owner:
            raise AssertionError(
                f"Accepted atom '{aid}' moved across selection ownership: "
                f"accepted owner={accepted_owners.get(aid)}, registered owner={registered_owner}"
            )

    for aid, src_atom in source_map.items():
        expected_flag = aid in accepted_map
        if src_atom.get("is_accepted") != expected_flag:
            raise AssertionError(
                f"Source atom '{aid}' is_accepted flag mismatch: atom says {src_atom.get('is_accepted')}, "
                f"but in accepted_atoms: {expected_flag}"
            )

    for aid, prod_atom in produced_map.items():
        expected_flag = aid in accepted_map
        if prod_atom.get("is_accepted") != expected_flag:
            raise AssertionError(
                f"Produced atom '{aid}' is_accepted flag mismatch: atom says {prod_atom.get('is_accepted')}, "
                f"but in accepted_atoms: {expected_flag}"
            )

    # 7. 溯源父关系图合法性校验 (无孤儿、无自环)
    catalog_ids = _get_catalog_item_ids()
    valid_parent_pool: Set[str] = set(all_registered_atoms.keys())
    valid_parent_pool.update(catalog_ids)
    for sel in selections:
        selected_id = sel.get("selected_id")
        if selected_id and sel.get("mode") != "resolver":
            if selected_id not in catalog_ids:
                raise AssertionError(
                    f"Selection '{sel.get('selector')}' has non-authoritative selected_id '{selected_id}'"
                )
            valid_parent_pool.add(selected_id)
        for p in sel.get("parent_ids", []):
            if sel.get("mode") == "resolver":
                if p not in all_registered_atoms:
                    raise AssertionError(
                        f"Resolver selection has unknown parent_id '{p}'"
                    )
            elif p not in catalog_ids:
                raise AssertionError(
                    f"Selection '{sel.get('selector')}' has non-authoritative parent_id '{p}'"
                )
            valid_parent_pool.add(p)

    for a in all_source_atoms:
        source_item_id = a.get("source_item_id")
        leaf_id = a.get("id")
        if source_item_id and source_item_id not in catalog_ids:
            raise AssertionError(
                f"Source atom '{a['atom_id']}' has non-authoritative source_item_id '{source_item_id}'"
            )
        if leaf_id and leaf_id not in catalog_ids:
            if not source_item_id or not leaf_id.startswith(f"{source_item_id}__"):
                raise AssertionError(
                    f"Source atom '{a['atom_id']}' has non-authoritative leaf id '{leaf_id}'"
                )
        for parent_id in a.get("parent_ids", []):
            if parent_id not in catalog_ids:
                raise AssertionError(
                    f"Source atom '{a['atom_id']}' has non-authoritative parent_id '{parent_id}'"
                )
        for semantic_id in a.get("semantic_ids", []):
            if ":" not in semantic_id:
                raise AssertionError(
                    f"Source atom '{a['atom_id']}' has malformed semantic_id '{semantic_id}'"
                )
            prefix, suffix = semantic_id.split(":", 1)
            if prefix not in _SEMANTIC_PREFIXES or not suffix:
                raise AssertionError(
                    f"Source atom '{a['atom_id']}' has non-authoritative semantic_id '{semantic_id}'"
                )
            is_slot_semantic = prefix == "slot" and suffix in SELECTOR_TO_UI_PARAM
            if (
                semantic_id not in _RUNTIME_SEMANTIC_IDS
                and not is_slot_semantic
                and suffix not in catalog_ids
            ):
                raise AssertionError(
                    f"Source atom '{a['atom_id']}' has non-authoritative semantic_id '{semantic_id}'"
                )

    for aid, atom in all_registered_atoms.items():
        for p in atom.get("parent_ids", []):
            if p == aid:
                raise AssertionError(f"Atom '{aid}' has self-referential parent_id: {p}")
            if p not in valid_parent_pool:
                raise AssertionError(f"Atom '{aid}' has invalid parent_id '{p}' not in valid parent scope")

    # 8. 消解决策状态机单次消费与时序合法性重放
    active_atoms: Dict[str, Dict[str, Any]] = dict(source_map)
    known_at_time: Set[str] = set(source_map) | set(catalog_ids)
    consumed_atoms: Set[str] = set()
    dropped_atoms: Set[str] = set()
    replaced_atoms: Set[str] = set()
    rules_in_decisions: List[str] = []

    decisions = audit.get("decisions", [])
    decision_ids = [d["decision_id"] for d in decisions]
    if len(decision_ids) != len(set(decision_ids)):
        duplicates = [x for x in decision_ids if decision_ids.count(x) > 1]
        raise AssertionError(f"Duplicate decision IDs detected: {set(duplicates)}")

    for idx, d in enumerate(decisions):
        if d.get("sequence") != idx:
            raise AssertionError(f"Decision sequence mismatch at index {idx}: got {d.get('sequence')}")

        r_id = d.get("rule_id")
        if r_id not in rules_in_decisions:
            rules_in_decisions.append(r_id)

        action = d.get("action")
        target_id = d.get("target_atom_id")
        before_t = d.get("before_text")
        after_t = d.get("after_text")
        prod_ids = d.get("produced_atom_ids", [])

        for wid in d.get("winner_atom_ids", []):
            if wid not in active_atoms:
                raise AssertionError(
                    f"Winner atom ID '{wid}' in decision '{d.get('decision_id')}' "
                    "was not active before the decision"
                )

        for psid in d.get("parent_source_ids", []):
            if psid not in known_at_time:
                raise AssertionError(
                    f"Parent source ID '{psid}' in decision '{d.get('decision_id')}' "
                    "was not known before the decision"
                )

        if action == "drop":
            if not target_id or target_id not in active_atoms:
                if target_id in consumed_atoms:
                    raise AssertionError(
                        f"Drop decision {d['decision_id']} target_atom_id '{target_id}' was already consumed"
                    )
                raise AssertionError(
                    f"Drop decision {d['decision_id']} target_atom_id '{target_id}' not in active atoms"
                )
            target_snap = active_atoms[target_id]
            if before_t != target_snap["text"]:
                raise AssertionError(
                    f"Drop decision {d['decision_id']} before_text {before_t!r} != target atom text {target_snap['text']!r}"
                )
            if after_t is not None:
                raise AssertionError(f"Drop decision after_text must be null, got {after_t!r}")
            if prod_ids != []:
                raise AssertionError(f"Drop decision produced_atom_ids must be empty, got {prod_ids}")
            active_atoms.pop(target_id)
            consumed_atoms.add(target_id)
            dropped_atoms.add(target_id)

        elif action == "replace":
            if not target_id or target_id not in active_atoms:
                if target_id in consumed_atoms:
                    raise AssertionError(
                        f"Replace decision {d['decision_id']} target_atom_id '{target_id}' was already consumed"
                    )
                raise AssertionError(
                    f"Replace decision {d['decision_id']} target_atom_id '{target_id}' not in active atoms"
                )
            target_snap = active_atoms[target_id]
            if before_t != target_snap["text"]:
                raise AssertionError(
                    f"Replace decision {d['decision_id']} before_text {before_t!r} != target atom text {target_snap['text']!r}"
                )
            if not after_t:
                raise AssertionError("Replace decision after_text cannot be empty")
            if not prod_ids:
                raise AssertionError("Replace decision produced_atom_ids cannot be empty")
            for pid in prod_ids:
                if pid not in produced_map:
                    raise AssertionError(f"Produced atom ID '{pid}' not found in produced_atoms")
                if pid in consumed_atoms or pid in active_atoms:
                    raise AssertionError(f"Produced atom ID '{pid}' already exists in active/consumed atoms")
                if target_id not in produced_map[pid].get("parent_ids", []):
                    raise AssertionError(
                        f"Replacement atom '{pid}' does not link to target_atom_id '{target_id}'"
                    )
                for identity_field in (
                    "id", "source_item_id", "source_slot", "tag_order", "span_order"
                ):
                    if produced_map[pid].get(identity_field) != target_snap.get(identity_field):
                        raise AssertionError(
                            f"Replacement atom '{pid}' changed immutable field '{identity_field}'"
                        )
                for parent_id in produced_map[pid].get("parent_ids", []):
                    if parent_id not in known_at_time:
                        raise AssertionError(
                            f"Produced atom '{pid}' has future/unknown parent_id '{parent_id}'"
                        )
                active_atoms[pid] = produced_map[pid]
                known_at_time.add(pid)
            expected_after = ", ".join(produced_map[pid]["text"] for pid in prod_ids)
            if after_t != expected_after:
                raise AssertionError(
                    f"Replace decision after_text {after_t!r} != produced atom text {expected_after!r}"
                )
            active_atoms.pop(target_id)
            consumed_atoms.add(target_id)
            replaced_atoms.add(target_id)

        elif action == "inject":
            if target_id is not None:
                raise AssertionError(f"Inject decision target_atom_id must be null, got {target_id!r}")
            if before_t is not None:
                raise AssertionError(f"Inject decision before_text must be null, got {before_t!r}")
            if not after_t:
                raise AssertionError("Inject decision after_text cannot be empty")
            if not prod_ids:
                raise AssertionError("Inject decision produced_atom_ids cannot be empty")
            for pid in prod_ids:
                if pid not in produced_map:
                    raise AssertionError(f"Produced atom ID '{pid}' not found in produced_atoms")
                if pid in consumed_atoms or pid in active_atoms:
                    raise AssertionError(f"Produced atom ID '{pid}' already exists in active/consumed atoms")
                for parent_id in produced_map[pid].get("parent_ids", []):
                    if parent_id not in known_at_time:
                        raise AssertionError(
                            f"Produced atom '{pid}' has future/unknown parent_id '{parent_id}'"
                        )
                if not set(d.get("winner_atom_ids", [])) <= set(produced_map[pid].get("parent_ids", [])):
                    raise AssertionError(
                        f"Injected atom '{pid}' does not link every winner as a parent"
                    )
                active_atoms[pid] = produced_map[pid]
                known_at_time.add(pid)
            expected_after = ", ".join(produced_map[pid]["text"] for pid in prod_ids)
            if after_t != expected_after:
                raise AssertionError(
                    f"Inject decision after_text {after_t!r} != produced atom text {expected_after!r}"
                )
        else:
            raise AssertionError(f"Invalid decision action '{action}'")

    # 验证 rules_applied 精确等于按 DAG 顺序实际产生决策的规则清单 (R3-P2-001)
    if list(audit.get("rules_applied", [])) != rules_in_decisions:
        raise AssertionError(
            f"rules_applied mismatch: got {audit.get('rules_applied', [])}, expected {rules_in_decisions}"
        )

    # 验证已消费 Atom 绝不复活 (R3-P2-001: 解决 C2-N12)
    for cid in consumed_atoms:
        if cid in accepted_map:
            raise AssertionError(f"Consumed atom '{cid}' was replaced or dropped and cannot appear in accepted_atoms")

    # 9. 重放去重与预算超限过滤记录
    for r in all_dedup_records:
        aid = r["atom_id"]
        if aid not in active_atoms:
            raise AssertionError(f"Deduplicated atom '{aid}' not found in active atoms during replay")
        if aid in accepted_map:
            raise AssertionError(f"Deduplicated atom '{aid}' cannot appear in accepted_atoms")
        retained_id = r["retained_atom_id"]
        if retained_id not in all_registered_atoms:
            raise AssertionError(f"Deduplication retained_atom_id '{retained_id}' not found in registered atoms")
        if retained_id in consumed_atoms:
            raise AssertionError(f"Deduplication retained_atom_id '{retained_id}' was consumed by a decision")
        retained_snap = all_registered_atoms[retained_id]
        if r["basis"] != "normalized_exact_tag_duplicate":
            raise AssertionError(
                f"Deduplication basis must be 'normalized_exact_tag_duplicate', got {r['basis']!r}"
            )
        if r["retained_tag_text"] != retained_snap["text"]:
            raise AssertionError(
                f"Deduplication retained_tag_text mismatch: {r['retained_tag_text']!r} != {retained_snap['text']!r}"
            )
        active_atoms.pop(aid)

    if all_budget_records:
        budgets = {r["word_budget"] for r in all_budget_records}
        if len(budgets) != 1:
            raise AssertionError(f"Budget records disagree on word_budget: {sorted(budgets)}")
        word_budget = next(iter(budgets))
        records_by_atom = {r["atom_id"]: r for r in all_budget_records}
        if len(records_by_atom) != len(all_budget_records):
            raise AssertionError("Duplicate budget filter record for the same atom")

        tags: Dict[int, List[Dict[str, Any]]] = {}
        for atom in active_atoms.values():
            tags.setdefault(atom["tag_order"], []).append(atom)

        used_words = 0
        for _, tag_atoms in sorted(tags.items()):
            tag_atoms.sort(key=lambda atom: atom["span_order"])
            tag_text = "".join(atom["text"] for atom in tag_atoms)
            candidate_words = len(tag_text.split())
            record_atoms = [atom for atom in tag_atoms if atom["atom_id"] in records_by_atom]
            if record_atoms:
                if len(record_atoms) != len(tag_atoms):
                    raise AssertionError("Budget filtering must consume a complete tag, never a partial span")
                if used_words + candidate_words <= word_budget:
                    raise AssertionError(
                        f"Budget-filtered tag would fit: used={used_words}, candidate={candidate_words}, budget={word_budget}"
                    )
                for atom in record_atoms:
                    record = records_by_atom.pop(atom["atom_id"])
                    if record["reason"] != "word_budget_exceeded":
                        raise AssertionError(
                            f"Budget record for atom '{atom['atom_id']}' has invalid reason {record['reason']!r}"
                        )
                    if record["used_words"] != used_words or record["candidate_words"] != candidate_words:
                        raise AssertionError(
                            f"Budget replay mismatch for atom '{atom['atom_id']}': "
                            f"reported used/candidate={record['used_words']}/{record['candidate_words']}, "
                            f"expected {used_words}/{candidate_words}"
                        )
                    if atom["atom_id"] in accepted_map:
                        raise AssertionError(
                            f"Budget filtered atom '{atom['atom_id']}' cannot appear in accepted_atoms"
                        )
                    active_atoms.pop(atom["atom_id"])
            else:
                if used_words + candidate_words > word_budget:
                    raise AssertionError(
                        f"Accepted tag exceeds replayed budget: used={used_words}, candidate={candidate_words}, budget={word_budget}"
                    )
                used_words += candidate_words

        if records_by_atom:
            raise AssertionError(f"Budget records reference non-active atoms: {sorted(records_by_atom)}")

    # 10. 最终集合完备性与数学守恒推导验证
    if set(active_atoms.keys()) != set(accepted_map.keys()):
        diff_missing = set(accepted_map.keys()) - set(active_atoms.keys())
        diff_extra = set(active_atoms.keys()) - set(accepted_map.keys())
        raise AssertionError(
            f"Final active atoms do not match accepted_atoms: missing={diff_missing}, extra={diff_extra}"
        )

    counts = audit.get("counts", {})
    counts_keys = set(counts.keys())
    if counts_keys != EXPECTED_COUNT_KEYS:
        raise AssertionError(
            f"counts dictionary keys mismatch: missing={sorted(EXPECTED_COUNT_KEYS - counts_keys)}, "
            f"extra={sorted(counts_keys - EXPECTED_COUNT_KEYS)}"
        )

    for k, val in counts.items():
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise AssertionError(f"counts[{k!r}] must be a non-negative int, got {val!r}")

    if counts["source_atoms"] != len(all_source_atoms):
        raise AssertionError(
            f"counts.source_atoms ({counts['source_atoms']}) != actual source atoms ({len(all_source_atoms)})"
        )
    if counts["produced"] != len(all_produced_atoms):
        raise AssertionError(
            f"counts.produced ({counts['produced']}) != actual produced atoms ({len(all_produced_atoms)})"
        )
    if counts["accepted_atoms"] != len(all_accepted_atoms):
        raise AssertionError(
            f"counts.accepted_atoms ({counts['accepted_atoms']}) != actual accepted atoms ({len(all_accepted_atoms)})"
        )
    if counts["dropped"] != len(dropped_atoms):
        raise AssertionError(
            f"counts.dropped ({counts['dropped']}) != actual drop decisions ({len(dropped_atoms)})"
        )
    if counts["replaced"] != len(replaced_atoms):
        raise AssertionError(
            f"counts.replaced ({counts['replaced']}) != actual replace decisions ({len(replaced_atoms)})"
        )
    actual_injected = sum(1 for d in decisions if d.get("action") == "inject")
    if counts["injected"] != actual_injected:
        raise AssertionError(
            f"counts.injected ({counts['injected']}) != actual inject decisions ({actual_injected})"
        )
    if counts["deduplicated"] != len(all_dedup_records):
        raise AssertionError(
            f"counts.deduplicated ({counts['deduplicated']}) != actual deduplicated records ({len(all_dedup_records)})"
        )
    if counts["budget_filtered"] != len(all_budget_records):
        raise AssertionError(
            f"counts.budget_filtered ({counts['budget_filtered']}) != actual budget filtered records ({len(all_budget_records)})"
        )

    # 核心守恒方程：
    # source_atoms + produced == accepted_atoms + dropped + replaced + deduplicated + budget_filtered
    left_hand = counts["source_atoms"] + counts["produced"]
    right_hand = (
        counts["accepted_atoms"]
        + counts["dropped"]
        + counts["replaced"]
        + counts["deduplicated"]
        + counts["budget_filtered"]
    )
    if left_hand != right_hand:
        raise AssertionError(
            f"Mathematical count conservation violated: "
            f"source ({counts['source_atoms']}) + produced ({counts['produced']}) = {left_hand} != "
            f"accepted ({counts['accepted_atoms']}) + dropped ({counts['dropped']}) + "
            f"replaced ({counts['replaced']}) + deduplicated ({counts['deduplicated']}) + "
            f"budget_filtered ({counts['budget_filtered']}) = {right_hand}"
        )

    # 11. 正向提示词精确还原比对 (禁止子串包含，按 tag/span 组装协议精确还原)
    if expected_positive is not None:
        sorted_accepted = sorted(
            all_accepted_atoms, key=lambda a: (a.get("tag_order", 0), a.get("span_order", 0))
        )
        tag_groups: Dict[int, List[str]] = {}
        for a in sorted_accepted:
            tag_groups.setdefault(a.get("tag_order", 0), []).append(a["text"])
        reconstructed_tags = ["".join(spans) for _, spans in sorted(tag_groups.items())]
        reconstructed_positive = ", ".join(t for t in reconstructed_tags if t)
        if reconstructed_positive != expected_positive:
            raise AssertionError(
                f"Reconstructed positive prompt does not match expected positive:\n"
                f"  Reconstructed: {reconstructed_positive!r}\n"
                f"  Expected:      {expected_positive!r}"
            )

    return audit
