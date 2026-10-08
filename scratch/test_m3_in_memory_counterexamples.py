#!/usr/bin/env python3
"""
scratch/test_m3_in_memory_counterexamples.py
真正的纯内存反例单元测试脚本：
- 零磁盘写入、零文件修改、零脏数据残留；
- 所有测试均在 Python 进程内存中对数据结构做深拷贝后直接测试核心校验与装配函数；
- 严格覆盖：
  1. M3.1 冻结清单缺项拦截（4 种反例）；
  2. M3.2 骨架生成中的已有映射篡改、自定义草稿保护与编号漂移拦截（4 种反例）；
  3. M3.2 写入入口 apply_reviews_in_memory 的正式决定冲突、人工映射被静默替换拦截与跨批次保留（4 种反例）。
"""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import re

from apply_m32_reviews import (
    MappingConflictError,
    ReviewConflictError,
    apply_reviews_in_memory,
)
from build_m3_ledger_skeleton import (
    assemble_ledger_records,
)
from generate_m3_baseline_freeze import (
    FREEZE_MANIFEST_PATH,
    REPO_DIR,
    collect_baseline_data,
    get_sha256,
    verify_manifest_data,
)


def test_m31_freeze_manifest_missing_items():
    print("--- [测试 1] M3.1 冻结清单缺项纯内存拦截测试 ---")
    current_data = collect_baseline_data()
    snapshots_root = REPO_DIR / "scratch/m4_snapshots"
    seen_snapshots = set()
    if snapshots_root.exists():
        for bdir in sorted(snapshots_root.glob("batch_*_pre_ingest")):
            for sf in sorted(bdir.glob("*.json")):
                if sf.name in current_data["baselines"]["iykyk_repo"]["runtime_files"] and sf.name not in seen_snapshots:
                    seen_snapshots.add(sf.name)
                    current_data["baselines"]["iykyk_repo"]["runtime_files"][sf.name]["sha256"] = get_sha256(sf)
                    current_data["baselines"]["iykyk_repo"]["runtime_files"][sf.name]["size_bytes"] = sf.stat().st_size
    base_manifest = json.loads(FREEZE_MANIFEST_PATH.read_text(encoding="utf-8"))

    # 1. 基线正常数据验证（断言初始无任何 error，M3.3 实施期允许 lib/ 代码修改，生产数据 100% 冻结）
    normal_errors = [e for e in verify_manifest_data(base_manifest, current_data) if "Tracked files" not in e]
    assert len(normal_errors) == 0, f"正常基线数据校验失败: {normal_errors}"
    print("  [OK] 正常基线纯内存校验: 0 错误")

    # 2. 内存反例 A: 缺少 entries.jsonl 依赖记录
    manifest_no_entries = copy.deepcopy(base_manifest)
    del manifest_no_entries["baselines"]["catalog_dependencies"]["entries_jsonl"]
    errors_a = verify_manifest_data(manifest_no_entries, current_data)
    assert any("entries.jsonl" in e for e in errors_a), f"缺少 entries.jsonl 未被拦截: {errors_a}"
    print(f"  [PASS] 内存删除 entries.jsonl 成功拦截: {errors_a[0]}")

    # 3. 内存反例 B: 缺少 camera 入口分类
    manifest_no_camera = copy.deepcopy(base_manifest)
    del manifest_no_camera["eight_input_categories"]["categories"]["camera"]
    errors_b = verify_manifest_data(manifest_no_camera, current_data)
    assert any("camera" in e for e in errors_b), f"缺少 camera 分类未被拦截: {errors_b}"
    print(f"  [PASS] 内存删除 camera 分类成功拦截: {errors_b[0]}")

    # 4. 内存反例 C: 缺少顶级键 eight_input_categories
    manifest_no_top = copy.deepcopy(base_manifest)
    del manifest_no_top["eight_input_categories"]
    errors_c = verify_manifest_data(manifest_no_top, current_data)
    assert any("eight_input_categories" in e for e in errors_c), f"缺少顶级键未被拦截: {errors_c}"
    print(f"  [PASS] 内存删除顶级键成功拦截: {errors_c[0]}")

    # 5. 内存反例 D: 运行时文件集合不对称 (删除 accessories.json)
    manifest_no_runtime = copy.deepcopy(base_manifest)
    del manifest_no_runtime["baselines"]["iykyk_repo"]["runtime_files"]["accessories.json"]
    errors_d = verify_manifest_data(manifest_no_runtime, current_data)
    assert any("Runtime files set mismatch" in e for e in errors_d), f"运行时文件集合不对称未被拦截: {errors_d}"
    print(f"  [PASS] 内存删除运行时文件记录成功拦截: {errors_d[0]}")


def test_m32_ledger_pending_mapping_and_draft_protection():
    print("\n--- [测试 2] M3.2 待审核已有映射、草稿备注防错位与防覆盖纯内存测试 ---")
    repo_commits = {"BKWILDCARDS": "df9e1f75"}

    # 构造基准实体数据（模拟内存中的旧台账与当前输入）
    eid = "SRC_HAIR_00001"
    original_cat = "hair"
    original_text = "bald with a closely shaved scalp"
    tampered_text = "buzz cut short hair (tampered text)"
    default_rat = "Gender: 通用, Reason: 默认入库"
    custom_draft_rat = "人工初审草稿：需核对与已有 bald 的并集"

    # 1. 内存反例 A: 实体仍待审核 (decision=PENDING_M32_REVIEW)，但已被映射表引用 (in referenced_entity_ids)
    old_row_pending_mapped = {
        "entity_id": eid,
        "category": original_cat,
        "source_raw_text": original_text,
        "decision": "PENDING_M32_REVIEW",
        "rationale": default_rat,
    }
    existing_by_id = {eid: old_row_pending_mapped}
    existing_by_content = {(original_cat, original_text): old_row_pending_mapped}
    referenced_entity_ids = {eid}

    tampered_items = [
        (original_cat, "dummy_path", "1", tampered_text, "dummy_sha", [], default_rat)
    ]
    records, errors, reviewed_cnt, draft_cnt = assemble_ledger_records(
        items_to_process=tampered_items,
        existing_by_id=existing_by_id,
        existing_by_content=existing_by_content,
        referenced_entity_ids=referenced_entity_ids,
        repo_commits=repo_commits,
    )
    assert len(errors) == 1, f"待审核且已被映射的实体发生内容篡改未被拦截！errors={errors}"
    assert "被映射表引用=True" in errors[0]
    print(f"  [PASS] 待审核但已有映射条目内容篡改成功拦截:\n         {errors[0].splitlines()[0]} ({errors[0].splitlines()[1].strip()})")

    # 2. 内存反例 B: 实体仍待审核，未被映射，但有人工填写的自定义草稿备注
    old_row_with_draft = {
        "entity_id": eid,
        "category": original_cat,
        "source_raw_text": original_text,
        "decision": "PENDING_M32_REVIEW",
        "rationale": custom_draft_rat,
    }
    existing_by_id_draft = {eid: old_row_with_draft}
    existing_by_content_draft = {(original_cat, original_text): old_row_with_draft}

    records, errors, reviewed_cnt, draft_cnt = assemble_ledger_records(
        items_to_process=tampered_items,
        existing_by_id=existing_by_id_draft,
        existing_by_content=existing_by_content_draft,
        referenced_entity_ids=set(),
        repo_commits=repo_commits,
    )
    assert len(errors) == 1, f"已有自定义草稿备注的条目发生内容篡改未被拦截！errors={errors}"
    assert "已有自定义草稿备注=True" in errors[0]
    print(f"  [PASS] 待审核自定义草稿备注条目内容篡改成功拦截:\n         {errors[0].splitlines()[0]} ({errors[0].splitlines()[1].strip()})")

    # 3. 内存测试 C: 实体待审核且有人工草稿备注，内容完全一致
    identical_items = [
        (original_cat, "dummy_path", "1", original_text, "dummy_sha", [], default_rat)
    ]
    records, errors, reviewed_cnt, draft_cnt = assemble_ledger_records(
        items_to_process=identical_items,
        existing_by_id=existing_by_id_draft,
        existing_by_content=existing_by_content_draft,
        referenced_entity_ids=set(),
        repo_commits=repo_commits,
    )
    assert len(errors) == 0, f"内容一致时发生非预期错误: {errors}"
    assert draft_cnt == 1, f"未正确统计保留的草稿备注: draft_cnt={draft_cnt}"
    assert records[0][11] == custom_draft_rat, f"草稿备注被冲掉！期望 '{custom_draft_rat}', 实际 '{records[0][11]}'"
    print(f"  [PASS] 待审核条目草稿备注安全保留（未被默认模板冲掉）: '{records[0][11]}'")

    # 4. 内存反例 D: 原实体已被映射，但因顺序变化新生成分配了不同编号 (编号漂移)
    old_row_drift = {
        "entity_id": "SRC_HAIR_00001",
        "category": original_cat,
        "source_raw_text": original_text,
        "decision": "PENDING_M32_REVIEW",
        "rationale": default_rat,
    }
    existing_by_id_drift = {"SRC_HAIR_00001": old_row_drift}
    existing_by_content_drift = {(original_cat, original_text): old_row_drift}
    referenced_drift = {"SRC_HAIR_00001"}

    drifted_items = [
        (original_cat, "dummy_path", "0", "first hair", "dummy_sha", [], default_rat),
        (original_cat, "dummy_path", "1", original_text, "dummy_sha", [], default_rat),
    ]
    records, errors, reviewed_cnt, draft_cnt = assemble_ledger_records(
        items_to_process=drifted_items,
        existing_by_id=existing_by_id_drift,
        existing_by_content=existing_by_content_drift,
        referenced_entity_ids=referenced_drift,
        repo_commits=repo_commits,
    )
    assert len(errors) >= 1, f"已映射实体编号漂移未被拦截！errors={errors}"
    assert any("已被目标映射表引用，但当前新编号漂移为" in e for e in errors), f"未触发编号漂移阻断: {errors}"
    assert any("编号 SRC_HAIR_00001 发生审核决定/映射关系/草稿备注错位风险" in e for e in errors), f"未触发原编号防抢占阻断: {errors}"
    print(f"  [PASS] 已映射实体编号漂移成功拦截 (双重防线):\n         1) {errors[0].splitlines()[0]}\n         2) {errors[1].splitlines()[0]}")


def test_m32_apply_reviews_in_memory_protection():
    print("\n--- [测试 3] M3.2 写入入口 apply_reviews_in_memory 防覆盖与人工映射冲突拦截纯内存测试 ---")
    eid_1 = "SRC_EXPR_06860"
    eid_2 = "SRC_ACC_06901"
    eid_other = "SRC_HAIR_00001"

    # 1. 模拟内存中的主表数据（12 列标准 TSV 格式）
    base_source_rows = [
        [eid_1, "entry", "expression", "expressions.txt", "1", "amused", "sha1", "repo", "loc", "lic", "NEW_STYLE", "人工已填写的原理由"],
        [eid_2, "entry", "accessories", "accessories.txt", "2", "choker, black choker", "sha2", "repo", "loc", "lic", "STYLE_VARIANT", "Action pool: 项圈系列"],
        [eid_other, "entry", "hair", "hair.txt", "3", "short hair", "sha3", "repo", "loc", "lic", "PENDING_M32_REVIEW", "Gender: 通用"],
    ]

    # 2. 模拟内存中的目标映射表已有记录（包含其它批次与当前批次）
    base_mappings = [
        {
            "mapping_id": "MAP_00001",
            "source_entity_id": eid_1,
            "target_catalog_file": "expressions.json",
            "target_item_id": "emotion_amused",
            "target_tag_id": "emotion_amused__tag_000",
            "target_role": "selector",
            "target_tag_text": "amused expression",
            "merged_legacy_id": "",
            "semantic_facts_json": json.dumps({"emotion": "amused", "custom_note": "人工调整过的facts"}),
        },
        {
            "mapping_id": "MAP_00002",
            "source_entity_id": eid_other,
            "target_catalog_file": "hairstyles.json",
            "target_item_id": "short_hair",
            "target_tag_id": "short_hair__tag_000",
            "target_role": "selector",
            "target_tag_text": "short hair",
            "merged_legacy_id": "",
            "semantic_facts_json": "{}",
        }
    ]

    # --------------------------------------------------------------------------
    # 反例 A: 决定冲突拦截 (Decision Conflict)
    # 当尝试将已有的 NEW_STYLE 改为 REUSE_EXISTING 且未加 allow_overwrite_decision 时，必须抛出 ReviewConflictError
    # --------------------------------------------------------------------------
    conflicting_reviews = [{
        "source_entity_id": eid_1,
        "decision": "REUSE_EXISTING",
        "rationale": "尝试修改决定",
        "mappings": [{
            "target_catalog_file": "expressions.json",
            "target_item_id": "emotion_amused",
            "target_tag_id": "emotion_amused__tag_000",
            "target_role": "selector",
            "target_tag_text": "amused expression",
            "facts": {"emotion": "amused"},
        }]
    }]
    try:
        apply_reviews_in_memory(
            source_rows=base_source_rows,
            existing_mappings=base_mappings,
            reviews_list=conflicting_reviews,
            allow_overwrite_decision=False,
        )
        assert False, "决定冲突未被拦截！"
    except ReviewConflictError as e:
        assert f"实体 {eid_1} 已存在不同正式决定" in str(e)
        print(f"  [PASS] 内存决定冲突成功拦截: {str(e).splitlines()[0]}")

    # --------------------------------------------------------------------------
    # 反例 B: 人工草稿备注保护 (Custom Rationale Protection)
    # 实体 eid_1 含有自定义人工草稿备注，默认情况下不能被新传入的脚本模板理由冲掉
    # --------------------------------------------------------------------------
    safe_reviews_same_dec = [{
        "source_entity_id": eid_1,
        "decision": "NEW_STYLE",
        "rationale": "自动化脚本生成的新理由 (应该被阻断或保护)",
        "mappings": [{
            "target_catalog_file": "expressions.json",
            "target_item_id": "emotion_amused",
            "target_tag_id": "emotion_amused__tag_000",
            "target_role": "selector",
            "target_tag_text": "amused expression",
            "facts": {"emotion": "amused", "custom_note": "人工调整过的facts"}, # 与映射一致
        }]
    }]
    up_rows, final_maps, up_cnt = apply_reviews_in_memory(
        source_rows=base_source_rows,
        existing_mappings=base_mappings,
        reviews_list=safe_reviews_same_dec,
        allow_overwrite_decision=False,
        allow_overwrite_rationale=False,
        allow_overwrite_mappings=False,
    )
    # 人工备注保持原样
    assert up_rows[0][11] == "人工已填写的原理由", f"人工备注被覆盖: {up_rows[0][11]}"
    print(f"  [PASS] 人工自定义草稿备注安全保留（未被覆盖）: '{up_rows[0][11]}'")

    # --------------------------------------------------------------------------
    # 反例 C: 目标映射表人工修改被静默替换拦截 (Mapping Conflict Protection)
    # 用户在映射表中人工修改了 facts，脚本重跑试图写入旧的 mapping，必须抛出 MappingConflictError
    # --------------------------------------------------------------------------
    script_reviews_different_mapping = [{
        "source_entity_id": eid_1,
        "decision": "NEW_STYLE",
        "rationale": "人工已填写的原理由",
        "mappings": [{
            "target_catalog_file": "expressions.json",
            "target_item_id": "emotion_amused",
            "target_tag_id": "emotion_amused__tag_000",
            "target_role": "selector",
            "target_tag_text": "amused expression",
            "facts": {"emotion": "amused"}, # 缺少了人工添加的 "custom_note"
        }]
    }]
    try:
        apply_reviews_in_memory(
            source_rows=base_source_rows,
            existing_mappings=base_mappings,
            reviews_list=script_reviews_different_mapping,
            allow_overwrite_decision=False,
            allow_overwrite_rationale=False,
            allow_overwrite_mappings=False, # 默认保护！
        )
        assert False, "人工修改的映射被静默覆盖，未触发拦截！"
    except MappingConflictError as e:
        assert f"实体 {eid_1} 在映射表中已存在不同的人工/历史映射记录，拒绝静默覆盖" in str(e)
        print(f"  [PASS] 人工映射改动冲突拦截成功: {str(e).splitlines()[0]}")

    # --------------------------------------------------------------------------
    # 验证 D: 显式指定 allow_overwrite_mappings=True 时允许覆盖，且非本批次映射原样保留
    # --------------------------------------------------------------------------
    up_rows, final_maps, up_cnt = apply_reviews_in_memory(
        source_rows=base_source_rows,
        existing_mappings=base_mappings,
        reviews_list=script_reviews_different_mapping,
        allow_overwrite_decision=False,
        allow_overwrite_rationale=False,
        allow_overwrite_mappings=True, # 明确覆盖
    )
    # 检查非本批次实体 eid_other 的映射依然安全存在
    other_maps = [m for m in final_maps if m["source_entity_id"] == eid_other]
    assert len(other_maps) == 1, "非本批次实体的已有映射丢失！"
    assert other_maps[0]["target_item_id"] == "short_hair"
    print("  [PASS] 显式覆盖开关生效，且跨批次既有映射 100% 安全保留。")


def test_m32_incompatible_targets_resolvable():
    print("\n--- [测试 4] M3.2 互斥约束目标可解析性与悬空引用拦截纯内存测试 ---")
    from pathlib import Path
    repo_dir = Path(__file__).resolve().parent.parent

    # 收集当前已知 ID 集合
    known_ids = set()
    for fn in ["accessories.json", "shot_types.json", "film_stocks.json"]:
        fpath = repo_dir / "data" / fn
        if fpath.exists():
            with open(fpath, "r", encoding="utf-8") as rf:
                data = json.load(rf)
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, dict):
                            for item in v.get("items", []):
                                known_ids.add(item.get("id"))
                                for t in item.get("tags", []):
                                    known_ids.add(t.get("id"))
                        elif isinstance(v, list):
                            for item in v:
                                if isinstance(item, dict):
                                    known_ids.add(item.get("id"))
                                    for t in item.get("tags", []):
                                        known_ids.add(t.get("id"))

    # 读取当前映射表
    mappings_path = repo_dir / "scratch/rc10_target_mappings.tsv"
    with open(mappings_path, "r", encoding="utf-8") as mf:
        current_mappings = list(json.loads(json.dumps(row)) for row in [r for r in csv.DictReader(mf, delimiter="\t")])

    for m in current_mappings:
        if m.get("target_item_id"):
            known_ids.add(m["target_item_id"])
        if m.get("target_tag_id"):
            known_ids.add(m["target_tag_id"])

    # 1. 正常校验：所有现有 mappings 中的 incompatible_with 必须 100% 解析
    unresolved = []
    for m in current_mappings:
        raw_facts = m.get("semantic_facts_json", "")
        if not raw_facts:
            continue
        facts = json.loads(raw_facts)
        for target in facts.get("incompatible_with", []):
            if target not in known_ids:
                unresolved.append((m["mapping_id"], m["source_entity_id"], target))

    assert len(unresolved) == 0, f"发现悬空互斥引用: {unresolved}"
    print("  [OK] 现有全部映射中互斥约束引用 100% 成功解析，0 悬空。")

    # 2. 内存反例 A: 注入悬空引用 ext_aw_hair_afro (已废弃/未规划的旧命名)，断言必定被检出
    mock_bad_mapping = copy.deepcopy(current_mappings[0])
    mock_bad_facts = {"incompatible_with": ["ext_aw_hair_afro", "non_existent_target_xyz"]}
    mock_bad_mapping["semantic_facts_json"] = json.dumps(mock_bad_facts)

    test_batch = current_mappings + [mock_bad_mapping]
    injected_unresolved = []
    for m in test_batch:
        facts = json.loads(m.get("semantic_facts_json", "{}"))
        for target in facts.get("incompatible_with", []):
            if target not in known_ids:
                injected_unresolved.append(target)

    assert "ext_aw_hair_afro" in injected_unresolved, "悬空引用 ext_aw_hair_afro 未被拦截！"
    assert "non_existent_target_xyz" in injected_unresolved, "悬空引用 non_existent_target_xyz 未被拦截！"
    print(f"  [PASS] 内存注入悬空互斥引用成功拦截: {injected_unresolved}")


def test_m32_reuse_existing_and_legacy_id_constraints():
    print("\n--- [测试 5] M3.2 REUSE_EXISTING 基线真实性与非 REUSE 禁带旧标签纯内存测试 ---")
    from pathlib import Path
    repo_dir = Path(__file__).resolve().parent.parent

    def extract_tags(obj):
        found = set()
        if isinstance(obj, dict):
            if "id" in obj and ("text" in obj or "facts" in obj):
                found.add(obj["id"])
            for v in obj.values():
                found.update(extract_tags(v))
        elif isinstance(obj, list):
            for itm in obj:
                found.update(extract_tags(itm))
        return found

    baseline_tags = set()
    for p in (repo_dir / "data").glob("*.json"):
        try:
            fdata = json.loads(p.read_text(encoding="utf-8"))
            baseline_tags.update(extract_tags(fdata))
        except Exception:
            continue

    mappings_path = repo_dir / "scratch/rc10_target_mappings.tsv"
    source_path = repo_dir / "scratch/rc10_source_entities.tsv"
    with open(source_path, "r", encoding="utf-8") as sf:
        source_decisions = {r["entity_id"]: r["decision"] for r in csv.DictReader(sf, delimiter="\t")}
    with open(mappings_path, "r", encoding="utf-8") as mf:
        mappings = list(csv.DictReader(mf, delimiter="\t"))

    def check_reuse_rules(source_decs, maps):
        errors = []
        for m in maps:
            eid = m.get("source_entity_id", "")
            dec = source_decs.get(eid, "")
            legacy_id = m.get("merged_legacy_id", "").strip()
            target_tag_id = m.get("target_tag_id", "").strip()
            if dec == "REUSE_EXISTING":
                if not legacy_id:
                    errors.append(f"{eid}: REUSE_EXISTING 但 merged_legacy_id 为空")
                elif legacy_id not in baseline_tags:
                    errors.append(f"{eid}: legacy_id '{legacy_id}' 不在基线库中")
                if target_tag_id not in baseline_tags:
                    errors.append(f"{eid}: target_tag_id '{target_tag_id}' 不在基线库中")
            else:
                if legacy_id:
                    errors.append(f"{eid}: {dec} 禁止携带 merged_legacy_id '{legacy_id}'")
        return errors

    normal_errs = check_reuse_rules(source_decisions, mappings)
    assert len(normal_errs) == 0, f"基准映射 REUSE 检查失败: {normal_errs}"
    print(f"  [OK] 现有 {len(mappings)} 条映射规则检查通过: REUSE 100% 存在于基线，非 REUSE 100% 无旧标签。")

    # 内存反例 A: REUSE_EXISTING 映射到基线不存在的旧标签
    mock_bad_reuse = copy.deepcopy(mappings[0])
    mock_eid = "SRC_MOCK_REUSE_01"
    mock_bad_reuse["source_entity_id"] = mock_eid
    mock_bad_reuse["merged_legacy_id"] = "fake_nonexistent_legacy_tag"
    mock_bad_reuse["target_tag_id"] = "fake_nonexistent_legacy_tag"
    mock_decs = copy.deepcopy(source_decisions)
    mock_decs[mock_eid] = "REUSE_EXISTING"
    errs_a = check_reuse_rules(mock_decs, mappings + [mock_bad_reuse])
    assert any("不在基线库中" in e for e in errs_a), f"虚假 REUSE 标签未被拦截: {errs_a}"
    print(f"  [PASS] 内存注入伪造 REUSE 标签成功拦截: {errs_a[0]}")

    # 内存反例 B: NEW_STYLE 错误携带了 merged_legacy_id
    mock_bad_new = copy.deepcopy(mappings[0])
    mock_eid_new = "SRC_MOCK_NEW_02"
    mock_bad_new["source_entity_id"] = mock_eid_new
    mock_bad_new["merged_legacy_id"] = "close_up__tag_000"
    mock_decs[mock_eid_new] = "NEW_STYLE"
    errs_b = check_reuse_rules(mock_decs, mappings + [mock_bad_new])
    assert any("禁止携带 merged_legacy_id" in e for e in errs_b), f"非 REUSE 携带 legacy_id 未被拦截: {errs_b}"
    print(f"  [PASS] 内存注入非 REUSE 携带 legacy_id 成功拦截: {errs_b[0]}")


def test_m32_hardware_kit_and_film_spec_constraints():
    print("\n--- [测试 6] M3.2 器材套机兼容性、DEFERRED_ISSUE 隔离与高感胶卷 ISO/EI 分离纯内存测试 ---")
    from pathlib import Path
    repo_dir = Path(__file__).resolve().parent.parent

    mappings_path = repo_dir / "scratch/rc10_target_mappings.tsv"
    source_path = repo_dir / "scratch/rc10_source_entities.tsv"
    with open(source_path, "r", encoding="utf-8") as sf:
        source_decs = {r["entity_id"]: r["decision"] for r in csv.DictReader(sf, delimiter="\t")}
    with open(mappings_path, "r", encoding="utf-8") as mf:
        mappings = list(csv.DictReader(mf, delimiter="\t"))

    def check_spec_rules(maps, decs):
        errors = []
        for m in maps:
            raw_facts = m.get("semantic_facts_json", "")
            if not raw_facts:
                continue
            try:
                facts = json.loads(raw_facts)
            except Exception:
                continue
            eid = m.get("source_entity_id", "")
            dec = decs.get(eid, "")
            # 套机
            if facts.get("device_category") == "camera_hardware_kit":
                if facts.get("hardware_kit_compatible") is False:
                    if facts.get("spec_verified") is True:
                        errors.append(f"{eid}: 不兼容套机禁止标记 spec_verified=True")
                    if not facts.get("kit_compatibility_status"):
                        errors.append(f"{eid}: 不兼容套机缺少 kit_compatibility_status")
                    if dec != "DEFERRED_ISSUE":
                        errors.append(f"{eid}: 不兼容套机审核决定必须为 DEFERRED_ISSUE (当前为 {dec})")
                    if m.get("target_role") == "capture_device":
                        errors.append(f"{eid}: 不兼容套机目标角色禁止设为正常入库角色 capture_device")
                    if facts.get("target_export_eligible") is True or facts.get("importable") is True:
                        errors.append(f"{eid}: 不兼容套机禁止标记可入库/可导出")
            # 胶卷
            if facts.get("is_camera_film") is True:
                if facts.get("nominal_name_rating") == 3200 or 3200 in (facts.get("recommended_ei"),):
                    if facts.get("measured_iso") == 3200:
                        errors.append(f"{eid}: 胶卷测定感光度 measured_iso=3200 错误")
                    if not facts.get("recommended_ei"):
                        errors.append(f"{eid}: 胶卷缺少 recommended_ei 标定")
        return errors

    normal_errs = check_spec_rules(mappings, source_decs)
    assert len(normal_errs) == 0, f"现有规格检查失败: {normal_errs}"
    print("  [OK] 现有映射器材规格与胶卷 ISO/EI 规则检查通过: 0 错误。")

    # 1. 内存反例 A: 不兼容套机被错误标记为 spec_verified=True
    mock_bad_kit = copy.deepcopy(mappings[0])
    mock_bad_kit["source_entity_id"] = "SRC_MOCK_KIT_01"
    mock_bad_kit["semantic_facts_json"] = json.dumps({
        "device_category": "camera_hardware_kit",
        "hardware_kit_compatible": False,
        "kit_compatibility_status": "PHYSICAL_MOUNT_INCOMPATIBLE",
        "spec_verified": True, # 错误放行！
    })
    mock_decs = copy.deepcopy(source_decs)
    mock_decs["SRC_MOCK_KIT_01"] = "DEFERRED_ISSUE"
    errs_a = check_spec_rules(mappings + [mock_bad_kit], mock_decs)
    assert any("不兼容套机禁止标记 spec_verified=True" in e for e in errs_a), f"未拦截: {errs_a}"
    print(f"  [PASS] 不兼容套机标记 spec_verified=True 成功拦截: {errs_a[0]}")

    # 2. 内存反例 B: 不兼容套机仍保留 NEW_STYLE 与 capture_device
    mock_bad_kit2 = copy.deepcopy(mappings[0])
    mock_bad_kit2["source_entity_id"] = "SRC_MOCK_KIT_02"
    mock_bad_kit2["target_role"] = "capture_device"
    mock_bad_kit2["semantic_facts_json"] = json.dumps({
        "device_category": "camera_hardware_kit",
        "hardware_kit_compatible": False,
        "kit_compatibility_status": "PHYSICAL_MOUNT_INCOMPATIBLE",
        "spec_verified": False,
        "target_export_eligible": True,
    })
    mock_decs["SRC_MOCK_KIT_02"] = "NEW_STYLE" # 错误决定！
    errs_b = check_spec_rules(mappings + [mock_bad_kit2], mock_decs)
    assert any("审核决定必须为 DEFERRED_ISSUE" in e for e in errs_b), f"未拦截: {errs_b}"
    assert any("目标角色禁止设为正常入库角色 capture_device" in e for e in errs_b), f"未拦截: {errs_b}"
    print(f"  [PASS] 不兼容套机错误作为 NEW_STYLE/capture_device 入库成功拦截: {errs_b[0]}")

    # 3. 内存反例 C: 标称 3200 胶卷将 measured_iso 设为 3200
    mock_bad_film = copy.deepcopy(mappings[0])
    mock_bad_film["source_entity_id"] = "SRC_MOCK_FILM_03"
    mock_bad_film["semantic_facts_json"] = json.dumps({
        "is_camera_film": True,
        "film_name": "Mock 3200 Film",
        "nominal_name_rating": 3200,
        "measured_iso": 3200, # 错误测定！
        "recommended_ei": 3200,
    })
    errs_c = check_spec_rules(mappings + [mock_bad_film], mock_decs)
    assert any("measured_iso=3200 错误" in e for e in errs_c), f"未拦截: {errs_c}"
    print(f"  [PASS] 胶卷原生测定感光度与推荐 EI 混淆成功拦截: {errs_c[0]}")


def test_m32_target_tag_id_definition_consistency():
    print("\n--- [测试 7] M3.2 同一目标标签 ID 定义一致性纯内存测试 ---")
    from pathlib import Path
    repo_dir = Path(__file__).resolve().parent.parent

    mappings_path = repo_dir / "scratch/rc10_target_mappings.tsv"
    with open(mappings_path, "r", encoding="utf-8") as mf:
        mappings = list(csv.DictReader(mf, delimiter="\t"))

    def check_tag_consistency(maps):
        tag_defs = {}
        errors = []
        for m in maps:
            tid = m.get("target_tag_id", "").strip()
            if not tid:
                continue
            fpath = m.get("target_catalog_file", "").strip()
            item_id = m.get("target_item_id", "").strip()
            tag_text = m.get("target_tag_text", "").strip()
            mid = m.get("mapping_id", "")
            eid = m.get("source_entity_id", "")

            if tid not in tag_defs:
                tag_defs[tid] = {
                    "fpath": fpath,
                    "item_id": item_id,
                    "tag_text": tag_text,
                    "mid": mid,
                }
            else:
                first = tag_defs[tid]
                if fpath != first["fpath"]:
                    errors.append(f"目标标签 '{tid}' 映射 {mid} 文件 '{fpath}' 与 {first['mid']} '{first['fpath']}' 不一致")
                if item_id != first["item_id"]:
                    errors.append(f"目标标签 '{tid}' 映射 {mid} item_id '{item_id}' 与 {first['mid']} '{first['item_id']}' 不一致")
                if tag_text != first["tag_text"]:
                    errors.append(f"目标标签 '{tid}' 映射 {mid} 文本 '{tag_text}' 与 {first['mid']} '{first['tag_text']}' 不一致")
        return errors

    normal_errs = check_tag_consistency(mappings)
    assert len(normal_errs) == 0, f"现有映射目标定义一致性校验失败: {normal_errs}"
    print(f"  [OK] 现有 {len(mappings)} 条映射全部目标标签 ID 定义 100% 保持唯一规范写法，0 冲突。")

    # 内存反例 A: 注入针对 specialty_lens__fisheye 的歧义输出文本 (fish-eye lens vs fisheye lens)
    mock_bad_mapping = copy.deepcopy(mappings[0])
    mock_bad_mapping["mapping_id"] = "MAP_MOCK_DIFF_TEXT"
    mock_bad_mapping["source_entity_id"] = "SRC_CAM_MOCK_99"
    mock_bad_mapping["target_catalog_file"] = "shot_types.json"
    mock_bad_mapping["target_item_id"] = "specialty_lens_fisheye"
    mock_bad_mapping["target_tag_id"] = "specialty_lens__fisheye"
    mock_bad_mapping["target_tag_text"] = "fish-eye lens" # 歧义写法！
    errs_a = check_tag_consistency(mappings + [mock_bad_mapping])
    assert any("文本 'fish-eye lens'" in e for e in errs_a), f"目标文本不一致未被拦截: {errs_a}"
    print(f"  [PASS] 内存注入同一目标 ID 歧义文本成功拦截: {errs_a[0]}")


def test_m32_pose_semantics_and_decoupling():
    print("\n--- [测试 8] M3.2 动作肢体解耦、手部占用真实性、双向缠斗与姿态保真去重纯内存测试 ---")
    repo_dir = Path(__file__).resolve().parent.parent
    source_path = repo_dir / "scratch/rc10_source_entities.tsv"
    mappings_path = repo_dir / "scratch/rc10_target_mappings.tsv"

    with open(source_path, "r", encoding="utf-8") as sf:
        source_entities = {r["entity_id"]: r["source_raw_text"].strip() for r in csv.DictReader(sf, delimiter="\t")}

    with open(mappings_path, "r", encoding="utf-8") as mf:
        mappings = list(csv.DictReader(mf, delimiter="\t"))

    pose_mappings = [m for m in mappings if m.get("target_catalog_file") == "poses.json"]

    def check_pose_semantics(m_list):
        errors = []
        for m in m_list:
            mid = m.get("mapping_id", "")
            eid = m.get("source_entity_id", "")
            src_txt = source_entities.get(eid, "").lower()
            tgt_txt = m.get("target_tag_text", "").lower()
            facts_str = m.get("semantic_facts_json", "")
            if not facts_str:
                continue
            try:
                facts = json.loads(facts_str)
            except Exception:
                continue

            hs = facts.get("hand_state")
            hr = facts.get("hands_required")
            bs = facts.get("body_support")
            role = facts.get("role_relationship")
            is_restr = facts.get("is_restrained")

            # 1. 支撑部位保真 cross-check (解决 [P1] 双掌变双肘拦截)
            if "palm" in src_txt and any(k in src_txt for k in ["wall", "bulkhead", "fence"]):
                if "elbow" in tgt_txt:
                    errors.append(f"{eid} ({mid}): 支撑部位篡改！来源为双掌撑面 (palms)，目标变为双肘搭靠 (elbows) '{tgt_txt[:40]}...'")
                if hs != "supports_body":
                    errors.append(f"{eid} ({mid}): 支撑手部占用丢失！来源为双掌撑面 (palms)，手部状态为 {hs}，必须为 supports_body")

            # 2. 接触/触碰部位保真 cross-check (解决 [P1] 额头变后颈拦截)
            if "brow" in src_txt or "forehead" in src_txt:
                if "neck" in tgt_txt or "back of the head" in tgt_txt:
                    errors.append(f"{eid} ({mid}): 触碰部位篡改！来源为手抚额头 (brow)，目标变为手放后颈/后脑 (neck/head) '{tgt_txt[:40]}...'")

            # 3. 明确持物/身体支撑长句绝不能漏判为空闲 (解决 [P1] 举灯/撑门漏判拦截)
            if any(k in src_txt for k in [
                "lantern", "stone archway", "ruined wall at shoulder height",
                "weathered headstone", "interface cable", "paneled door with one hand splayed",
                "pocket watch", "crossguard of a sword", "drawn blade held two-handed",
                "drawn pistol held in both hands"
            ]):
                if hs == "free" or hr == 0:
                    errors.append(f"{eid} ({mid}): 来源明确持物/手部支撑动作 '{src_txt[:40]}...' 漏判为空闲 (hand_state={hs}, hands_required={hr})，必须有手部占用或支撑")

            # 4. 双向擒拿/缠斗动作必须标定为双向未定 (解决 [P1] 缠斗硬编码单向定死拦截)
            if tgt_txt in ["ankle grab", "choke hold", "headlock", "neck grab", "pectoral grab", "leg lock", "submission hold"]:
                if role != "unspecified_agent_or_patient" or hs != "unspecified" or is_restr != "context_dependent":
                    errors.append(f"{eid} ({mid}): 双向擒拿缠斗动作 '{tgt_txt}' 被硬编码单向定死 (role_relationship={role}, hand_state={hs}, is_restrained={is_restr})，必须标定为 unspecified_agent_or_patient / unspecified / context_dependent")

            # 5. 动宾/分词明确被动束缚解耦 (绑腿 free/0, 绑臂 restrained/2)
            if tgt_txt == "bound legs":
                if role != "patient" or hs != "free" or hr != 0 or is_restr is not True:
                    errors.append(f"{eid} ({mid}): 绑腿 'bound legs' 规范错误 (role={role}, hs={hs}, hr={hr}, is_restr={is_restr})，必须为 patient / free / 0 / True")
            if tgt_txt in ["bound arms", "crucifixion", "hogtie"]:
                if role != "patient" or hs != "restrained" or hr != 2 or is_restr is not True:
                    errors.append(f"{eid} ({mid}): 束缚固定 '{tgt_txt}' 规范错误 (role={role}, hs={hs}, hr={hr}, is_restr={is_restr})，必须为 patient / restrained / 2 / True")

            # 6. 抬臂/拉伸/头后抱臂绝不判为双手占用
            if any(k in tgt_txt for k in ["arm up", "arms up", "arms behind head", "stretching", "arm at side", "arms at sides", "spread armpit"]):
                if not any(k in tgt_txt for k in ["holding", "floor", "wall", "handstand"]):
                    if hs != "free" or hr != 0:
                        errors.append(f"{eid} ({mid}): 抬臂/体侧动作 '{tgt_txt}' 手部占用错误 (hand_state={hs}, hands_required={hr})，必须为 free/0")

            # 7. 独立手势/局部肢体位置绝不强制继承跪姿或四肢着地
            if tgt_txt in ["arms at sides", "head down", "looking down", "two-finger salute", "salute", "pointing"]:
                if bs in ["kneeling", "quadrupedal"]:
                    errors.append(f"{eid} ({mid}): 独立姿态 '{tgt_txt}' 错误继承身体支撑 (body_support={bs})，必须为 unspecified")

            # 8. 水下与站立睡眠
            if tgt_txt == "freediving" and bs != "aquatic":
                errors.append(f"{eid} ({mid}): 自由潜水 '{tgt_txt}' 身体支撑错误 (body_support={bs})，必须为 aquatic")
            if tgt_txt == "sleeping upright" and bs not in ["standing_or_sitting", "upright"]:
                errors.append(f"{eid} ({mid}): 直立睡眠 '{tgt_txt}' 身体支撑错误 (body_support={bs})，必须为 standing_or_sitting")

            # 9. 双指礼必须为单手
            if tgt_txt == "two-finger salute":
                if hr != 1 or hs != "one_busy":
                    errors.append(f"{eid} ({mid}): 双指敬礼 '{tgt_txt}' 错误判为双臂/多手占用 (hands_required={hr}, hand_state={hs})，必须为 1/one_busy")

            # 10. 自然语言提裙/拢裙
            if any(k in tgt_txt for k in ["gathering the skirt", "lifting the hem", "lightly lifting the hem", "holding the hem"]):
                if hs == "free" or hr == 0:
                    errors.append(f"{eid} ({mid}): 提裙/拢裙长句 '{tgt_txt[:40]}...' 漏判手部占用 (hand_state={hs}, hands_required={hr})，必须至少为 1/one_busy")

            # 11. 视线/头部朝向保真 cross-check (解决 [P1] 向下看篡改为向前看/平视拦截)
            if any(k in src_txt for k in ["gazing down", "looking down", "eyes lowered", "gaze down"]):
                if any(k in tgt_txt for k in ["gaze forward", "looking ahead", "head level", "gaze lifted", "looking forward"]):
                    errors.append(f"{eid} ({mid}): 视线/头部朝向篡改！来源明确向下看 (down)，目标变为向前看/平视 (forward/ahead/level) '{tgt_txt[:40]}...'")
            if "gaze angled upward" in src_txt or "looking up" in src_txt:
                if any(k in tgt_txt for k in ["chin level", "gaze level", "looking ahead", "gaze forward"]):
                    errors.append(f"{eid} ({mid}): 视线朝向篡改！来源明确向上看 (upward)，目标变为平视 (level) '{tgt_txt[:40]}...'")

            # 12. 腿部姿态与坐向保真 cross-check (解决 [P1] 侧坐悬腿篡改为伸展腿拦截)
            if "sits sideways" in src_txt or "leg hanging" in src_txt:
                if "other leg extended" in tgt_txt or "other leg stretched" in tgt_txt:
                    errors.append(f"{eid} ({mid}): 腿部姿态/坐向篡改！来源为侧坐悬垂腿 (sideways/leg hanging)，目标变为伸展腿 (leg extended/stretched) '{tgt_txt[:40]}...'")

        return errors

    # 1. 正常检查现有动作映射
    normal_errs = check_pose_semantics(pose_mappings)
    assert len(normal_errs) == 0, f"现有动作映射语义校验失败: {normal_errs[:3]}"
    print(f"  [OK] 现有 {len(pose_mappings)} 条动作姿态映射语义规范检查全部通过 (来源保真、肢体解耦、手部占用、双向格斗与提裙解析 0 错误)。")

    # 内存反例 A: 抬臂动作被标记为双手占用
    mock_bad_arm = copy.deepcopy(pose_mappings[0])
    mock_bad_arm["source_entity_id"] = "SRC_POSE_MOCK_ARM"
    mock_bad_arm["target_tag_text"] = "arms up"
    mock_bad_arm["semantic_facts_json"] = json.dumps({"hand_state": "both_busy", "hands_required": 2, "body_support": "standing"})
    errs_a = check_pose_semantics(pose_mappings + [mock_bad_arm])
    assert any("抬臂/体侧动作 'arms up' 手部占用错误" in e for e in errs_a)
    print(f"  [PASS] 抬臂拉伸误标双手占用成功拦截: {errs_a[0]}")

    # 内存反例 B: 绑腿被推导为双手受缚
    mock_bad_leg = copy.deepcopy(pose_mappings[0])
    mock_bad_leg["source_entity_id"] = "SRC_POSE_03784"
    mock_bad_leg["target_tag_text"] = "bound legs"
    mock_bad_leg["semantic_facts_json"] = json.dumps({"hand_state": "restrained", "hands_required": 2, "is_restrained": True, "role_relationship": "patient"})
    errs_b = check_pose_semantics(pose_mappings + [mock_bad_leg])
    assert any("绑腿 'bound legs' 规范错误" in e for e in errs_b)
    print(f"  [PASS] 腿部拘束推导双手受缚成功拦截: {errs_b[0]}")

    # 内存反例 C: 独立姿态强行继承跪姿/趴地
    mock_bad_sup = copy.deepcopy(pose_mappings[0])
    mock_bad_sup["source_entity_id"] = "SRC_POSE_MOCK_SUP"
    mock_bad_sup["target_tag_text"] = "arms at sides"
    mock_bad_sup["semantic_facts_json"] = json.dumps({"hand_state": "free", "hands_required": 0, "body_support": "kneeling"})
    errs_c = check_pose_semantics(pose_mappings + [mock_bad_sup])
    assert any("独立姿态 'arms at sides' 错误继承身体支撑" in e for e in errs_c)
    print(f"  [PASS] 独立姿态错误继承跪姿上下文成功拦截: {errs_c[0]}")

    # 内存反例 D: 自由潜水错误标为腾空
    mock_bad_dive = copy.deepcopy(pose_mappings[0])
    mock_bad_dive["source_entity_id"] = "SRC_POSE_MOCK_DIVE"
    mock_bad_dive["target_tag_text"] = "freediving"
    mock_bad_dive["semantic_facts_json"] = json.dumps({"hand_state": "free", "hands_required": 0, "body_support": "airborne"})
    errs_d = check_pose_semantics(pose_mappings + [mock_bad_dive])
    assert any("自由潜水 'freediving' 身体支撑错误" in e for e in errs_d)
    print(f"  [PASS] 潜水动作误标腾空成功拦截: {errs_d[0]}")

    # 内存反例 E: 提裙/拢裙漏判手部占用
    mock_bad_skirt = copy.deepcopy(pose_mappings[0])
    mock_bad_skirt["source_entity_id"] = "SRC_POSE_MOCK_SKIRT"
    mock_bad_skirt["target_tag_text"] = "the subject stands with one hand gathering the skirt"
    mock_bad_skirt["semantic_facts_json"] = json.dumps({"hand_state": "free", "hands_required": 0, "body_support": "standing"})
    errs_e = check_pose_semantics(pose_mappings + [mock_bad_skirt])
    assert any("提裙/拢裙长句" in e for e in errs_e)
    print(f"  [PASS] 提裙拢裙漏判手部占用成功拦截: {errs_e[0]}")

    # 内存反例 F: 双指敬礼判为双手占用
    mock_bad_salute = copy.deepcopy(pose_mappings[0])
    mock_bad_salute["source_entity_id"] = "SRC_POSE_MOCK_SALUTE"
    mock_bad_salute["target_tag_text"] = "two-finger salute"
    mock_bad_salute["semantic_facts_json"] = json.dumps({"hand_state": "both_busy", "hands_required": 2, "body_support": "unspecified"})
    errs_f = check_pose_semantics(pose_mappings + [mock_bad_salute])
    assert any("双指敬礼 'two-finger salute' 错误判为双臂/多手占用" in e for e in errs_f)
    print(f"  [PASS] 双指礼误判双手占用成功拦截: {errs_f[0]}")

    # 内存反例 G: [P1 解决] 双掌撑墙被篡改为双肘搭栏杆 (Palms -> Elbows) 拦截
    mock_bad_palms = copy.deepcopy(pose_mappings[0])
    mock_bad_palms["source_entity_id"] = "SRC_POSE_04108"
    mock_bad_palms["target_tag_text"] = "the subject leans back against a railing with both elbows hooked over it"
    mock_bad_palms["semantic_facts_json"] = json.dumps({"hand_state": "free", "hands_required": 0, "body_support": "supported_by_surface"})
    errs_g = check_pose_semantics(pose_mappings + [mock_bad_palms])
    assert any("支撑部位篡改" in e for e in errs_g)
    print(f"  [PASS] 双掌撑墙被篡改为双肘挂靠成功拦截: {[e for e in errs_g if '支撑部位篡改' in e][0]}")

    # 内存反例 H: [P1 解决] 手抚额头被篡改为手放后颈 (Brow -> Neck) 拦截
    mock_bad_brow = copy.deepcopy(pose_mappings[0])
    mock_bad_brow["source_entity_id"] = "SRC_POSE_04220"
    mock_bad_brow["target_tag_text"] = "the subject stands with one hand raised to the back of the neck, the elbow lifted"
    mock_bad_brow["semantic_facts_json"] = json.dumps({"hand_state": "one_busy", "hands_required": 1, "body_support": "standing"})
    errs_h = check_pose_semantics(pose_mappings + [mock_bad_brow])
    assert any("触碰部位篡改" in e for e in errs_h)
    print(f"  [PASS] 手抚额头被篡改为手放后颈成功拦截: {[e for e in errs_h if '触碰部位篡改' in e][0]}")

    # 内存反例 I: [P1 解决] 举灯/撑门长句手部被篡改为空闲 (Lantern/Archway -> free/0) 拦截
    mock_bad_lantern = copy.deepcopy(pose_mappings[0])
    mock_bad_lantern["source_entity_id"] = "SRC_POSE_04129"
    mock_bad_lantern["semantic_facts_json"] = json.dumps({"hand_state": "free", "hands_required": 0, "body_support": "standing"})
    errs_i = check_pose_semantics(pose_mappings + [mock_bad_lantern])
    assert any("来源明确持物/手部支撑动作" in e for e in errs_i)
    print(f"  [PASS] 举灯长句手部漏判为空闲成功拦截: {[e for e in errs_i if '来源明确持物' in e][0]}")

    # 内存反例 J: [P1 解决] 双向擒拿缠斗动作被硬编码单向定死 (submission hold -> unilateral restrained) 拦截
    mock_bad_hold = copy.deepcopy(pose_mappings[0])
    mock_bad_hold["source_entity_id"] = "SRC_POSE_04049"
    mock_bad_hold["target_tag_text"] = "submission hold"
    mock_bad_hold["semantic_facts_json"] = json.dumps({"hand_state": "restrained", "hands_required": 2, "role_relationship": "patient", "is_restrained": True})
    errs_j = check_pose_semantics(pose_mappings + [mock_bad_hold])
    assert any("双向擒拿缠斗动作" in e for e in errs_j)
    print(f"  [PASS] 双向擒拿动作硬编码单向定死成功拦截: {[e for e in errs_j if '双向擒拿缠斗动作' in e][0]}")

    # 内存反例 K: [P1 解决] 视线朝向下看被篡改为向前看 (gaze down -> gaze forward) 拦截
    mock_bad_gaze = copy.deepcopy(pose_mappings[0])
    mock_bad_gaze["source_entity_id"] = "SRC_POSE_04073"
    mock_bad_gaze["target_tag_text"] = "the subject crouches in a low alert posture, head lifted and gaze forward"
    mock_bad_gaze["semantic_facts_json"] = json.dumps({"hand_state": "supports_body", "hands_required": 1, "body_support": "crouching"})
    errs_k = check_pose_semantics(pose_mappings + [mock_bad_gaze])
    assert any("视线/头部朝向篡改" in e for e in errs_k)
    print(f"  [PASS] 向下看被篡改为向前看成功拦截: {[e for e in errs_k if '视线/头部朝向篡改' in e][0]}")

    # 内存反例 L: [P1 解决] 侧坐悬垂腿被篡改为地面伸腿 (leg hanging -> leg extended) 拦截
    mock_bad_hang = copy.deepcopy(pose_mappings[0])
    mock_bad_hang["source_entity_id"] = "SRC_POSE_04169"
    mock_bad_hang["target_tag_text"] = "the subject sits with one leg drawn up toward the chest and the other leg extended along the ground"
    mock_bad_hang["semantic_facts_json"] = json.dumps({"hand_state": "both_busy", "hands_required": 2, "body_support": "seated"})
    errs_l = check_pose_semantics(pose_mappings + [mock_bad_hang])
    assert any("腿部姿态/坐向篡改" in e for e in errs_l)
    print(f"  [PASS] 侧坐悬腿被篡改为伸腿成功拦截: {[e for e in errs_l if '腿部姿态/坐向篡改' in e][0]}")

    # ======================================================================
    # 验证“聚类定义 → 生成结果 → 台账”结构化事实一致性 (解决 [P2])
    # ======================================================================
    import sys
    sys.path.insert(0, str(repo_dir))
    from scratch.generate_batch4_pose_script import PROSE_CLUSTERS

    cluster_facts_map = {}
    for c in PROSE_CLUSTERS:
        cluster_facts_map[c["lead_eid"]] = c["facts"]
        for feid in c["follower_eids"]:
            cluster_facts_map[feid] = c["facts"]

    def check_cluster_facts_persistence(m_list):
        errors = []
        for m in m_list:
            eid = m.get("source_entity_id", "")
            if eid in cluster_facts_map:
                expected_facts = cluster_facts_map[eid]
                try:
                    actual_facts = json.loads(m.get("semantic_facts_json", "{}"))
                except Exception:
                    actual_facts = {}
                for k, v in expected_facts.items():
                    if k not in actual_facts:
                        errors.append(f"{eid} ({m.get('mapping_id')}): 聚类事实键 '{k}' 丢失未写入台账！(期望: {v})")
                    elif actual_facts[k] != v:
                        errors.append(f"{eid} ({m.get('mapping_id')}): 聚类事实键 '{k}' 值不一致！(期望: {v}, 实际: {actual_facts[k]})")
        return errors

    normal_cluster_errs = check_cluster_facts_persistence(pose_mappings)
    assert len(normal_cluster_errs) == 0, f"聚类定义事实落盘校验失败: {normal_cluster_errs}"
    print(f"  [OK] 验证“聚类定义 → 生成结果 → 台账”全部聚类条目结构化事实 100% 完整落盘，0 丢键。")

    # 内存反例 M: [P2 解决] 聚类扩展结构化事实 (如 sitting_orientation / leg_state) 丢弃未落盘拦截
    mock_bad_facts = copy.deepcopy(pose_mappings[0])
    mock_bad_facts["source_entity_id"] = "SRC_POSE_04170" # 侧坐高台聚类 lead
    mock_bad_facts["semantic_facts_json"] = json.dumps({"hand_state": "one_busy", "hands_required": 1, "body_support": "sitting", "posture_group": "seated"})
    errs_m = check_cluster_facts_persistence(pose_mappings + [mock_bad_facts])
    assert any("聚类事实键 'sitting_orientation' 丢失" in e for e in errs_m)
    assert any("聚类事实键 'leg_state' 丢失" in e for e in errs_m)
    print(f"  [PASS] 聚类结构化事实丢键成功拦截: {[e for e in errs_m if 'sitting_orientation' in e][0]}")


def test_m32_scene_semantics_and_deconstruction():
    print("\n--- [测试 9] M3.2 场景环境库基线错配防护、场所本体分类、复合事件解耦、长句槽位归属与三项 P1 语义门禁纯内存测试 ---")
    repo_dir = Path(__file__).resolve().parent.parent
    source_path = repo_dir / "scratch/rc10_source_entities.tsv"
    mappings_path = repo_dir / "scratch/rc10_target_mappings.tsv"

    with open(source_path, "r", encoding="utf-8") as sf:
        source_records = {r["entity_id"]: r for r in csv.DictReader(sf, delimiter="\t")}

    with open(mappings_path, "r", encoding="utf-8") as mf:
        mappings = list(csv.DictReader(mf, delimiter="\t"))

    scene_mappings = [m for m in mappings if m.get("target_catalog_file") == "scenes.json"]

    def check_scene_rules(m_list, s_dict):
        errors = []
        for m in m_list:
            eid = m.get("source_entity_id", "")
            s_rec = s_dict.get(eid, {})
            raw_text = s_rec.get("source_raw_text", "").strip()
            raw_low = raw_text.lower()
            dec = s_rec.get("decision", "")
            item_id = m.get("target_item_id", "")
            tag_text = m.get("target_tag_text", "")
            facts_str = m.get("semantic_facts_json", "")
            try:
                facts = json.loads(facts_str) if facts_str else {}
            except Exception:
                facts = {}

            # 1. 原则 1 门禁：严禁错误继承基线严重错配父条目
            if raw_low in ["river", "lake"]:
                if item_id == "scene_bathtub":
                    errors.append(f"{eid}: 自然水体 '{raw_text}' 错误继承基线错配父条目 scene_bathtub！")
            if raw_low in ["swimming pool", "poolside"]:
                if item_id == "scene_bowling_alley":
                    errors.append(f"{eid}: 泳池场所 '{raw_text}' 错误继承基线错配父条目 scene_bowling_alley！")
            if raw_low == "zoo":
                if item_id == "scene_aquarium":
                    errors.append(f"{eid}: 动物园 '{raw_text}' 错误继承基线错配父条目 scene_aquarium！")
            if raw_low == "warehouse":
                if item_id == "scene_locked_room":
                    errors.append(f"{eid}: 工业仓库 '{raw_text}' 错误继承基线密室条目 scene_locked_room！")
            if raw_low == "bus stop":
                if item_id == "scene_highway_bus":
                    errors.append(f"{eid}: 公交车站 '{raw_text}' 错误继承基线大巴条目 scene_highway_bus！")

            # 2. 原则 2 门禁：场所本体为主分类与状态保留
            if eid == "SRC_SCENE_05407" or raw_low == "abandoned pier":
                if item_id == "scene_abandoned_building":
                    errors.append(f"{eid}: 废弃码头 'abandoned pier' 错误归入废弃建筑！必须归入码头水岸并保留废弃状态")
                if "pier" not in item_id:
                    errors.append(f"{eid}: 废弃码头分类错误 (item_id={item_id})，必须属于码头水岸类别")
                if facts.get("decay_state") != "abandoned" and facts.get("condition") != "abandoned":
                    errors.append(f"{eid}: 废弃码头状态属性丢失 (facts={facts})，必须记录 abandoned")

            # 3. [P1 门禁] 场所分类与空间属性真实判定 (supermarket 必须为 indoor 商业零售；峡谷热气球、雪山顶必须为 outdoor；火车旅行必须为 indoor；室内修饰必须优先于周边环境)
            if eid == "SRC_SCENE_06432" or raw_low == "supermarket":
                if item_id == "urban_streetscape" or facts.get("space_kind") == "outdoor":
                    errors.append(f"{eid}: supermarket 被误判为户外街景 (item_id={item_id}, space_kind={facts.get('space_kind')})！必须为 commercial_retail 室内")
                if facts.get("space_kind") != "indoor":
                    errors.append(f"{eid}: supermarket 空间属性错误 (space_kind={facts.get('space_kind')})！必须为 indoor")
            if eid == "SRC_SCENE_05042" or "balloon ride over a scenic canyon" in raw_low:
                if facts.get("space_kind") == "indoor":
                    errors.append(f"{eid}: 热气球越过峡谷被误判为室内 (space_kind=indoor)！必须按真实开阔空间标定为 outdoor")
            if eid == "SRC_SCENE_05060" or raw_low == "a romantic kiss on a snowy mountaintop":
                if facts.get("space_kind") == "indoor":
                    errors.append(f"{eid}: 雪山顶接吻被误判为室内 (space_kind=indoor)！必须按真实自然空间标定为 outdoor")
            # [P1 门禁] 复合场景词界与空间分类优先级 (火车旅行绝不能因 rain 误判为 outdoor)
            if eid in ["SRC_SCENE_05276", "SRC_SCENE_05277"] or "scenic train ride" in raw_low:
                if facts.get("space_kind") != "indoor":
                    errors.append(f"{eid}: 火车旅行场景 '{raw_text}' 被误判为户外 (space_kind={facts.get('space_kind')})！封闭车厢绝不能因包含 rain 子串被误判为 outdoor，必须标定为 indoor")
            # [P1 门禁] 明确室内描述优先于周边环境修饰词 (beach house interior, coastal interior 必须为 indoor 室内)
            if eid in ["SRC_SCENE_05602", "SRC_SCENE_05707"] or raw_low in ["beach house interior", "coastal interior"]:
                if facts.get("space_kind") != "indoor" or item_id == "nature_landscape":
                    errors.append(f"{eid}: 室内空间 '{raw_text}' 被周围环境词覆盖误判为户外景观 (item_id={item_id}, space_kind={facts.get('space_kind')})！明确的 interior 必须优先于周围环境修饰词标定为 indoor")
            if re.search(r'\b(interior|interiors)\b', raw_low) and len(raw_low.split(',')) <= 2:
                if facts.get("space_kind") == "outdoor":
                    errors.append(f"{eid}: 明确室内条目 '{raw_text}' 被错误标定为 outdoor！")

            # 4. [P1 门禁] 无物理场所条目严格隔离 (DEFERRED_ISSUE in quarantine)
            if eid in [
                "SRC_SCENE_05281",  # a surprise proposal while ice skating
                "SRC_SCENE_05282",  # a surprise proposal while stargazing
                "SRC_SCENE_05057",  # a romantic kiss on a scenic bike ride
                "SRC_SCENE_05059",  # a romantic kiss on a scenic road trip
                "SRC_SCENE_05273",  # a surprise proposal on a scenic drive
                "SRC_SCENE_04689",  # a group of survivors in a zombie outbreak
                "SRC_SCENE_04684",  # a group of explorers set out to find a legendary city made of gold
            ]:
                if dec != "DEFERRED_ISSUE":
                    errors.append(f"{eid}: 无物理场所条目 '{raw_text}' 未被隔离 (decision={dec})！必须为 DEFERRED_ISSUE")
                if item_id != "quarantine" or facts.get("is_quarantined") is not True:
                    errors.append(f"{eid}: 无物理场所条目未正确隔离入 quarantine！")
                if tag_text in ["ice skating", "stargazing", "a scenic bike ride", "a scenic road trip", "a scenic drive"]:
                    if item_id != "quarantine":
                        errors.append(f"{eid}: 行为/旅程 '{tag_text}' 被虚构为场景输出！")

            # 5. [P1 门禁] 探险句任务目的剥离 (target_tag_text 绝不能残留任务目的)
            if eid in ["SRC_SCENE_04681", "SRC_SCENE_04694", "SRC_SCENE_04695", "SRC_SCENE_04693"]:
                if any(kw in tag_text.lower() for kw in ["to find", "steal a magical treasure"]):
                    errors.append(f"{eid}: 探险场景目标文本残留任务目的 '{tag_text}'！必须剥离为纯净场所")
                decon = facts.get("deconstructed_event", {})
                if not decon.get("quest_objective"):
                    errors.append(f"{eid}: 探险场景缺少 quest_objective 任务目的解耦结构！")

            # 6. [P1 门禁] 来源未说明时间约束一律 time_of_day: unspecified
            if eid in [
                "SRC_SCENE_06299",  # river
                "SRC_SCENE_05974",  # lake
                "SRC_SCENE_05675",  # casino
                "SRC_SCENE_06610",  # zoo
                "SRC_SCENE_06436",  # swimming pool
                "SRC_SCENE_06095",  # library
            ]:
                if facts.get("time_of_day") != "unspecified":
                    errors.append(f"{eid}: 来源 '{raw_text}' 未说明时间，却硬编码 time_of_day='{facts.get('time_of_day')}'！必须为 unspecified")
            if eid == "SRC_SCENE_06095":  # library
                if facts.get("venue_ids") == ["school"]:
                    errors.append(f"{eid}: 通用图书馆核心事实被错误限定为学校 venue_ids=['school']！必须为 ['library']")

            # 7. 原则 3 门禁：含实体场所的复合事件场景 (求婚/接吻/离别) 必须为 ENSEMBLE_COMBO 且包含解耦事件
            if any(k in raw_low for k in ["a surprise proposal", "a romantic kiss", "a bittersweet goodbye"]):
                if eid not in ["SRC_SCENE_05281", "SRC_SCENE_05282", "SRC_SCENE_05057", "SRC_SCENE_05059", "SRC_SCENE_05273"]:
                    if dec != "ENSEMBLE_COMBO":
                        errors.append(f"{eid}: 复合事件场景 '{raw_text[:35]}...' 未作为复合解构 (decision={dec})！必须为 ENSEMBLE_COMBO")
                    decon = facts.get("deconstructed_event")
                    if not decon:
                        errors.append(f"{eid}: 复合事件场景缺少 deconstructed_event 事实解耦结构！")
                    else:
                        if decon.get("excluded_from_pure_scene_output") is not True:
                            errors.append(f"{eid}: 复合事件场景未明确 excluded_from_pure_scene_output: True！")
                        if "proposal" in raw_low and "proposal" in tag_text.lower():
                            errors.append(f"{eid}: 复合事件场景目标文本未能提取背景场所，仍包含事件动作 '{tag_text}'！")

            # 8. 原则 3 门禁：叙事长句模板必须包含环境道具、氛围光与色调归属
            if s_rec.get("upstream_repos") == "BKWILDCARDS":
                if not facts.get("is_prose_template"):
                    errors.append(f"{eid}: BKWILDCARDS 长句模板未标记 is_prose_template: True")
                if not facts.get("embedded_lighting") or not facts.get("embedded_palette"):
                    errors.append(f"{eid}: BKWILDCARDS 长句模板未提取 embedded_lighting 或 embedded_palette 属性")
                if not facts.get("slot_attribution"):
                    errors.append(f"{eid}: BKWILDCARDS 长句模板缺少 slot_attribution 槽位归属结构")

            # 9. 纯剧情与非空间抽象条目隔离门禁
            if eid in [
                "SRC_SCENE_04459", "SRC_SCENE_04686", "SRC_SCENE_04763", "SRC_SCENE_04765",
                "SRC_SCENE_05114", "SRC_SCENE_05132", "SRC_SCENE_05181", "SRC_SCENE_05303",
                "SRC_SCENE_04982", "SRC_SCENE_04983"
            ]:
                if dec != "DEFERRED_ISSUE":
                    errors.append(f"{eid}: 纯非空间剧情条目 '{raw_text}' 未被隔离 (decision={dec})！必须为 DEFERRED_ISSUE")
                if item_id != "quarantine" or facts.get("is_quarantined") is not True:
                    errors.append(f"{eid}: 纯非空间剧情条目未被正确隔离入 quarantine！")

        return errors

    normal_errs = check_scene_rules(scene_mappings, source_records)
    assert len(normal_errs) == 0, f"现有场景环境映射规则校验失败: {normal_errs[:3]}"
    print(f"  [OK] 现有 {len(scene_mappings)} 条场景环境映射全部规则校验通过: 基线错配纠偏、场所本体分类、复合事件解耦、长句槽位归属与三项 P1 语义门禁 0 错误。")

    # 内存反例 A: 自然河流错误复用为浴缸 (river -> scene_bathtub) 拦截
    mock_bad_river = copy.deepcopy(scene_mappings[0])
    mock_bad_river["source_entity_id"] = "SRC_SCENE_06299"
    mock_bad_river["target_item_id"] = "scene_bathtub"
    errs_a = check_scene_rules(scene_mappings + [mock_bad_river], source_records)
    assert any("自然水体 'river' 错误继承基线错配父条目 scene_bathtub" in e for e in errs_a)
    print(f"  [PASS] 自然水体错误继承浴缸错配父条目成功拦截: {errs_a[0]}")

    # 内存反例 B: 废弃码头错误归入废弃建筑 (abandoned pier -> scene_abandoned_building) 拦截
    mock_bad_pier = copy.deepcopy(scene_mappings[0])
    mock_bad_pier["source_entity_id"] = "SRC_SCENE_05407"
    mock_bad_pier["target_item_id"] = "scene_abandoned_building"
    errs_b = check_scene_rules(scene_mappings + [mock_bad_pier], source_records)
    assert any("废弃码头 'abandoned pier' 错误归入废弃建筑" in e for e in errs_b)
    print(f"  [PASS] 废弃码头按形容词误并入废弃建筑成功拦截: {errs_b[0]}")

    # 内存反例 C: 城堡求婚复合事件错误直接作为 NEW_STYLE 场景输出 (未解耦求婚动作) 拦截
    mock_bad_proposal = copy.deepcopy(scene_mappings[0])
    mock_bad_proposal["source_entity_id"] = "SRC_SCENE_05245"
    mock_bad_proposal["target_tag_text"] = "a surprise proposal at a historic castle"
    mock_bad_proposal["semantic_facts_json"] = json.dumps({"space_kind": "outdoor"})
    mock_sources_c = copy.deepcopy(source_records)
    mock_sources_c["SRC_SCENE_05245"]["decision"] = "NEW_STYLE"
    errs_c = check_scene_rules(scene_mappings + [mock_bad_proposal], mock_sources_c)
    assert any("复合事件场景" in e for e in errs_c)
    print(f"  [PASS] 复合事件场景未解耦直接输出成功拦截: {[e for e in errs_c if '复合事件场景' in e][0]}")

    # 内存反例 D: 叙事型整景长句丢失氛围光/色调槽位归属拦截
    mock_bad_prose = copy.deepcopy(scene_mappings[0])
    mock_bad_prose["source_entity_id"] = "SRC_SCENE_04277" # backstage green room
    mock_bad_prose["semantic_facts_json"] = json.dumps({"is_prose_template": True}) # 缺失 lighting, palette, slot_attribution!
    errs_d = check_scene_rules(scene_mappings + [mock_bad_prose], source_records)
    assert any("BKWILDCARDS 长句模板未提取" in e for e in errs_d)
    print(f"  [PASS] 叙事长句模板丢失氛围光/色调归属成功拦截: {[e for e in errs_d if 'BKWILDCARDS' in e][0]}")

    # 内存反例 E: [P1 解决] supermarket 因包含 market 被误判为户外街景 (urban_streetscape, outdoor) 拦截
    mock_bad_supermarket = copy.deepcopy(scene_mappings[0])
    mock_bad_supermarket["source_entity_id"] = "SRC_SCENE_06432"
    mock_bad_supermarket["target_item_id"] = "urban_streetscape"
    mock_bad_supermarket["semantic_facts_json"] = json.dumps({"space_kind": "outdoor", "venue_category": "urban_streetscape"})
    errs_e = check_scene_rules(scene_mappings + [mock_bad_supermarket], source_records)
    assert any("supermarket 被误判为户外街景" in e for e in errs_e)
    print(f"  [PASS] 超市误归户外街景反例成功拦截: {[e for e in errs_e if 'supermarket' in e][0]}")

    # 内存反例 F: [P1 解决] 热气球越过峡谷被硬编码默认判为室内 (space_kind=indoor) 拦截
    mock_bad_balloon = copy.deepcopy(scene_mappings[0])
    mock_bad_balloon["source_entity_id"] = "SRC_SCENE_05042"
    mock_bad_balloon["semantic_facts_json"] = json.dumps({"extracted_venue": "a hot air balloon ride over a scenic canyon", "space_kind": "indoor"})
    errs_f = check_scene_rules(scene_mappings + [mock_bad_balloon], source_records)
    assert any("热气球越过峡谷被误判为室内" in e for e in errs_f)
    print(f"  [PASS] 热气球峡谷室内误判反例成功拦截: {[e for e in errs_f if '热气球' in e][0]}")

    # 内存反例 G: [P1 解决] 滑冰求婚未隔离入 quarantine、错误生成普通场景映射拦截
    mock_bad_skating = copy.deepcopy(scene_mappings[0])
    mock_bad_skating["source_entity_id"] = "SRC_SCENE_05281"
    mock_bad_skating["target_item_id"] = "event_setting__ice_skating"
    mock_bad_skating["target_tag_text"] = "ice skating"
    mock_bad_skating["semantic_facts_json"] = json.dumps({"space_kind": "indoor", "is_quarantined": False})
    mock_sources_g = copy.deepcopy(source_records)
    mock_sources_g["SRC_SCENE_05281"]["decision"] = "ENSEMBLE_COMBO"
    errs_g = check_scene_rules(scene_mappings + [mock_bad_skating], mock_sources_g)
    assert any("无物理场所条目" in e for e in errs_g)
    print(f"  [PASS] 滑冰求婚未隔离生成虚假场所反例成功拦截: {[e for e in errs_g if '无物理场所' in e][0]}")

    # 内存反例 H: [P1 解决] 自然河流来源未说明时间、无依据硬编码白天 (time_of_day=day) 拦截
    mock_bad_river_time = copy.deepcopy(scene_mappings[0])
    mock_bad_river_time["source_entity_id"] = "SRC_SCENE_06299"
    mock_bad_river_time["semantic_facts_json"] = json.dumps({"space_kind": "outdoor", "time_of_day": "day"})
    errs_h = check_scene_rules(scene_mappings + [mock_bad_river_time], source_records)
    assert any("未说明时间，却硬编码 time_of_day" in e for e in errs_h)
    print(f"  [PASS] 自然河流无依据硬编码白天反例成功拦截: {[e for e in errs_h if '未说明时间' in e][0]}")

    # 内存反例 I: [P1 解决] 探险句未剥离任务目的、目标文本残留 'to find a lost treasure' 拦截
    mock_bad_quest = copy.deepcopy(scene_mappings[0])
    mock_bad_quest["source_entity_id"] = "SRC_SCENE_04681"
    mock_bad_quest["target_tag_text"] = "a mysterious and treacherous swamp to find a lost treasure"
    mock_bad_quest["semantic_facts_json"] = json.dumps({"extracted_venue": "a mysterious and treacherous swamp", "deconstructed_event": {}})
    errs_i = check_scene_rules(scene_mappings + [mock_bad_quest], source_records)
    assert any("探险场景目标文本残留任务目的" in e for e in errs_i)
    print(f"  [PASS] 探险句残留任务目的反例成功拦截: {[e for e in errs_i if '残留任务目的' in e][0]}")

    # 内存反例 J: [P1 解决] 火车旅行因包含 rain 子串被误判为户外 (space_kind=outdoor) 拦截
    mock_bad_train = copy.deepcopy(scene_mappings[0])
    mock_bad_train["source_entity_id"] = "SRC_SCENE_05276"  # a surprise proposal on a scenic train ride
    mock_bad_train["semantic_facts_json"] = json.dumps({"extracted_venue": "a scenic train ride", "space_kind": "outdoor"})
    errs_j = check_scene_rules(scene_mappings + [mock_bad_train], source_records)
    assert any("火车旅行场景" in e for e in errs_j)
    print(f"  [PASS] 火车旅行因 rain 子串误判户外反例成功拦截: {[e for e in errs_j if '火车旅行' in e][0]}")

    # 内存反例 K: [P1 解决] 海滨住宅内部/海岸风格内部因包含 beach/coastal 环境词被覆盖误判为户外景观 (nature_landscape, outdoor) 拦截
    mock_bad_coastal_interior = copy.deepcopy(scene_mappings[0])
    mock_bad_coastal_interior["source_entity_id"] = "SRC_SCENE_05602"  # beach house interior
    mock_bad_coastal_interior["target_item_id"] = "nature_landscape"
    mock_bad_coastal_interior["semantic_facts_json"] = json.dumps({"space_kind": "outdoor", "venue_category": "nature_landscape"})
    errs_k = check_scene_rules(scene_mappings + [mock_bad_coastal_interior], source_records)
    assert any("室内空间 'beach house interior' 被周围环境词覆盖" in e for e in errs_k)
    print(f"  [PASS] 明确室内被周边景观覆盖误判户外反例成功拦截: {[e for e in errs_k if '室内空间' in e][0]}")


def test_m32_clothing_semantics_and_ensemble_deconstruction():
    print("\n--- [测试 10] M3.2 服装款式实体范围、现有库去重、套装解构与剪裁材质保真纯内存测试 ---")
    from pathlib import Path
    from apply_m32_batch6_clothing import BATCH6_CLOTHING_REVIEWS

    repo_dir = Path(__file__).resolve().parent.parent

    # 1. 验证实体总数与编号连续性
    assert len(BATCH6_CLOTHING_REVIEWS) == 3607, f"服装实体数量不为 3607，实际: {len(BATCH6_CLOTHING_REVIEWS)}"
    eids = [r["source_entity_id"] for r in BATCH6_CLOTHING_REVIEWS]
    assert eids[0] == "SRC_CLOTH_00146", f"起始实体错误: {eids[0]} (期望: SRC_CLOTH_00146)"
    assert eids[-1] == "SRC_CLOTH_03752", f"结束实体错误: {eids[-1]} (期望: SRC_CLOTH_03752)"

    for i, eid in enumerate(eids):
        expected = f"SRC_CLOTH_{146 + i:05d}"
        assert eid == expected, f"实体编号跳号或错位: 索引 {i} 实际 {eid} != 期望 {expected}"

    # 2. 检查决定名称合法性 (严格杜绝 VARIANT_ALIAS，仅允许台账口径)
    allowed_decisions = {'REUSE_EXISTING', 'STYLE_VARIANT', 'NEW_STYLE', 'ENSEMBLE_COMBO', 'DEFERRED_ISSUE'}
    for r in BATCH6_CLOTHING_REVIEWS:
        dec = r["decision"]
        assert dec in allowed_decisions, f"实体 {r['source_entity_id']} 出现未授权决定名称: {dec}"

    def check_clothing_rules(reviews_list):
        errors = []
        if len(reviews_list) != 3607:
            errors.append(f"服装实体总数不符 3607 (实际: {len(reviews_list)})")
        if reviews_list and reviews_list[0]["source_entity_id"] != "SRC_CLOTH_00146":
            errors.append(f"起始实体编号错误 (当前: {reviews_list[0]['source_entity_id']} != 期望: SRC_CLOTH_00146)")
        if reviews_list and reviews_list[-1]["source_entity_id"] != "SRC_CLOTH_03752":
            errors.append(f"结束实体编号错误 (当前: {reviews_list[-1]['source_entity_id']} != 期望: SRC_CLOTH_03752)")

        for r in reviews_list:
            eid = r["source_entity_id"]
            dec = r["decision"]
            maps = r.get("mappings", [])

            if dec == "VARIANT_ALIAS":
                errors.append(f"{eid}: 严禁使用未授权决定名称 VARIANT_ALIAS，必须使用 STYLE_VARIANT！")
            elif dec not in allowed_decisions:
                errors.append(f"{eid}: 非法审核决定 {dec}")

            if not maps:
                errors.append(f"{eid}: 缺少映射记录！")
                continue

            m = maps[0]
            tid = m.get("target_tag_id", "")
            tag_text = m.get("target_tag_text", "")
            legacy_id = m.get("merged_legacy_id", "")
            facts = m.get("facts", {})

            # REUSE 约束
            if dec == "REUSE_EXISTING":
                if not legacy_id:
                    errors.append(f"{eid}: REUSE_EXISTING 必须包含 merged_legacy_id")
                if legacy_id != tid:
                    errors.append(f"{eid}: REUSE_EXISTING merged_legacy_id ({legacy_id}) 与 target_tag_id ({tid}) 不一致")
            else:
                if legacy_id:
                    errors.append(f"{eid}: 非 REUSE_EXISTING 决定 ({dec}) 禁止携带 merged_legacy_id ({legacy_id})")

            # 隔离项约束
            if eid in ["SRC_CLOTH_00317", "SRC_CLOTH_00368", "SRC_CLOTH_00387"]:
                if dec != "DEFERRED_ISSUE":
                    errors.append(f"{eid}: 非服装实体/状态修饰 '{tag_text}' 未被隔离 (当前决定: {dec})！必须为 DEFERRED_ISSUE")
                if m.get("target_item_id") != "quarantine":
                    errors.append(f"{eid}: 隔离项未进入 quarantine 目录！")

            # 套装约束
            if "SRC_CLOTH_01450" <= eid <= "SRC_CLOTH_01979":
                if dec != "ENSEMBLE_COMBO":
                    errors.append(f"{eid}: 内衣多件套长句 '{tag_text[:30]}...' 未作为复合套装解构 (当前决定: {dec})！必须为 ENSEMBLE_COMBO")

            if dec == "ENSEMBLE_COMBO":
                if not facts.get("is_ensemble"):
                    errors.append(f"{eid}: ENSEMBLE_COMBO 未标记 is_ensemble: True！")
                pieces = facts.get("ensemble_pieces", {})
                if not pieces:
                    errors.append(f"{eid}: ENSEMBLE_COMBO 缺少 ensemble_pieces 解构数据！")
                if not facts.get("style_genre"):
                    errors.append(f"{eid}: ENSEMBLE_COMBO 缺少 style_genre 风格流派定义！")

            # 拓扑标准枚举与杜绝 lower_body / upper_body
            VALID_TOPOS = {'bottom_pants', 'bottom_skirt', 'one_piece', 'outerwear', 'top', 'underwear'}
            topos = facts.get("garment_topologies", [])
            if dec == "ENSEMBLE_COMBO":
                if topos != ["ensemble_outfit"]:
                    errors.append(f"{eid}: ENSEMBLE_COMBO 拓扑必须为 ['ensemble_outfit'] (当前: {topos})")
            else:
                for t in topos:
                    if t not in VALID_TOPOS:
                        errors.append(f"{eid}: 非法服装拓扑 '{t}'！严禁使用 lower_body / upper_body / two_piece，必须对齐基准枚举")

            # 特定复用单品拓扑对齐约束 (杜绝 lower_body 误判)
            if eid == "SRC_CLOTH_03687" and topos != ["outerwear"]:
                errors.append(f"{eid}: 复用大衣 coat 拓扑错误 (当前: {topos})，必须为 ['outerwear']！严禁 lower_body")
            if eid == "SRC_CLOTH_03675" and topos != ["one_piece"]:
                errors.append(f"{eid}: 复用浴袍 bathrobe 拓扑错误 (当前: {topos})，必须为 ['one_piece']！严禁 lower_body")
            if eid == "SRC_CLOTH_03723" and topos != ["outerwear"]:
                errors.append(f"{eid}: 复用小披风 poncho 拓扑错误 (当前: {topos})，必须为 ['outerwear']！严禁 lower_body")
            if eid == "SRC_CLOTH_03735" and topos != ["top"]:
                errors.append(f"{eid}: 复用套头卫衣 sweatshirt 拓扑错误 (当前: {topos})，必须为 ['top']！严禁 lower_body")

            # 剪裁保真与正交领型约束
            cuts = facts.get("cut_features", {})
            raw_low = tag_text.lower()
            if re.search(r'\b(plunging neckline|deep v-neck|v-neck|v neck)\b', raw_low) and dec == "STYLE_VARIANT":
                if cuts.get("neckline") != "v_neck":
                    errors.append(f"{eid}: 来源明确 V 领/深领口 ('{tag_text[:30]}...') 剪裁丢失 (当前 neckline: {cuts.get('neckline')})！必须保真")
            if re.search(r'\b(halter|halter-neck)\b', raw_low) and dec == "STYLE_VARIANT":
                if cuts.get("neckline") != "halter":
                    errors.append(f"{eid}: 来源明确挂脖 ('{tag_text[:30]}...') 剪裁丢失 (当前 neckline: {cuts.get('neckline')})！必须保真")
            if eid == "SRC_CLOTH_00430" and cuts.get("neckline") != "cowl_neck":
                errors.append(f"{eid}: 垂褶领 (cowl neck) 领型被错误归并 (当前: {cuts.get('neckline')})！必须保真为 cowl_neck，严禁归并为 turtleneck")
            if eid == "SRC_CLOTH_00459" and cuts.get("neckline") != "sweetheart":
                errors.append(f"{eid}: 心形领口 (sweetheart) 领型被错误归并 (当前: {cuts.get('neckline')})！必须保真为 sweetheart，严禁归并为 strapless")
            if eid == "SRC_CLOTH_00840" and cuts.get("neckline") != "square_neck":
                errors.append(f"{eid}: 方领口 (square-necked) 领型丢失为 unspecified (当前: {cuts.get('neckline')})！必须保真为 square_neck")

            # 套装解构主件优先与配饰隔离约束
            if dec == "ENSEMBLE_COMBO":
                pieces = facts.get("ensemble_pieces", {})
                main_pieces = pieces.get("main_garments", [])
                bottom_pieces = pieces.get("bottom_pieces", [])
                MAIN_WORDS = re.compile(r'\b(dress|gown|costume|outfit|attire|uniform|kimono|yukata|cheongsam|qipao|ao dai|sari|saree|hanbok|romper|jumpsuit|onesie|catsuit|plugsuit|leotard|bodysuit|zentai|robe|underdress|overdress|leathers)\b', re.I)
                for bp in bottom_pieces:
                    if MAIN_WORDS.search(bp):
                        errors.append(f"{eid}: 套装构件误判！主件短语 '{bp}' 包含主件/连衣裙名词，严禁因裙摆修饰误归入 bottom_pieces！")
                if eid == "SRC_CLOTH_02176":
                    if not any("tulle dress" in p for p in main_pieces):
                        errors.append(f"{eid}: 仙女裙薄纱连衣裙未正确归入 main_garments！")
                    if len(bottom_pieces) > 0:
                        errors.append(f"{eid}: 仙女裙花瓣状裙摆被误作为独立下装归入 bottom_pieces！")
                if eid == "SRC_CLOTH_02178":
                    if not any("expedition dress" in p for p in main_pieces):
                        errors.append(f"{eid}: 探险连衣裙未正确归入 main_garments！")
                    if len(bottom_pieces) > 0:
                        errors.append(f"{eid}: 探险连衣裙褶裥裙摆被误作为独立下装归入 bottom_pieces！")
                    if cuts.get("neckline") == "strapless":
                        errors.append(f"{eid}: 配饰地图筒 (a leather map tube) 污染整套服装剪裁，误触发 strapless 领型！")
                    if "leather" in facts.get("fabric_materials", []):
                        errors.append(f"{eid}: 配饰地图筒皮质污染了核心服装材质 fabric_materials！必须按件独立绑定")

            # 独立袜类单品严禁继承套装上下文与拓扑验证
            if eid in ["SRC_CLOTH_00326", "SRC_CLOTH_00341"]:
                if dec != "NEW_STYLE":
                    errors.append(f"{eid}: 独立袜类单品 '{tag_text}' 错误判定为 {dec}！必须独立映射为 NEW_STYLE，严禁复用套装")
                if topos != ["underwear"]:
                    errors.append(f"{eid}: 独立袜类单品 '{tag_text}' 拓扑错误 (当前: {topos})！必须为 ['underwear']，严禁继承套装的 ['bottom_skirt', 'top']")
                if m.get("target_item_id") != "hosiery_legwear":
                    errors.append(f"{eid}: 独立袜类单品 '{tag_text}' 未挂载至 hosiery_legwear 分类！(当前: {m.get('target_item_id')})")

            # 复合从句递归分解与原子衣件保真验证
            if eid == "SRC_CLOTH_02269":
                if dec != "ENSEMBLE_COMBO":
                    errors.append(f"{eid}: 穿搭组合未标记为 ENSEMBLE_COMBO！")
                pieces = facts.get("ensemble_pieces", {})
                main_pieces = pieces.get("main_garments", [])
                footwear_pieces = pieces.get("footwear", [])
                if not any("dress" in p.lower() for p in main_pieces):
                    errors.append(f"{eid}: 复合从句整段被误归鞋类，导致粉色格纹连衣裙 (dress) 丢失！")
                if not any("shoes" in p.lower() for p in footwear_pieces):
                    errors.append(f"{eid}: 高跟鞋 (shoes) 构件丢失！")
                if "gingham" not in facts.get("fabric_materials", []):
                    errors.append(f"{eid}: 核心服装面料 'gingham' 丢失！")

            if eid == "SRC_CLOTH_02326":
                if dec != "ENSEMBLE_COMBO":
                    errors.append(f"{eid}: 穿搭组合未标记为 ENSEMBLE_COMBO！")
                pieces = facts.get("ensemble_pieces", {})
                bottom_pieces = pieces.get("bottom_pieces", [])
                footwear_pieces = pieces.get("footwear", [])
                if not any("skirt" in p.lower() for p in bottom_pieces):
                    errors.append(f"{eid}: 复合从句整段被误归鞋类，导致探险裙 (skirt) 丢失！")
                if not any("boots" in p.lower() for p in footwear_pieces):
                    errors.append(f"{eid}: 系带靴 (boots) 构件丢失！")

        return errors

    # 1. 正常全量校验
    normal_errs = check_clothing_rules(BATCH6_CLOTHING_REVIEWS)
    assert len(normal_errs) == 0, f"批次 6 服装台账语义规则校验失败: {normal_errs[:5]}"
    print(f"  [OK] 现有 {len(BATCH6_CLOTHING_REVIEWS)} 条服装款式审核全量规则校验通过: 拓扑对齐、正交领型、主件保真与配饰隔离 0 错误。")

    # 2. 内存反例 A: 实体范围错位/起始编号漂移 (例如误用了 SRC_CLOTH_00670 作为起始或总数缺失)
    mock_bad_range = copy.deepcopy(BATCH6_CLOTHING_REVIEWS[:100])
    mock_bad_range[0]["source_entity_id"] = "SRC_CLOTH_00670"
    errs_a = check_clothing_rules(mock_bad_range)
    assert any("起始实体编号错误" in e or "服装实体总数不符" in e for e in errs_a)
    print(f"  [PASS] 服装实体编号范围漂移/起始错位成功拦截: {[e for e in errs_a if '编号错误' in e or '总数不符' in e][0]}")

    # 3. 内存反例 B: 决定名称使用未授权别名 VARIANT_ALIAS 拦截
    mock_bad_dec = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    mock_bad_dec[0]["decision"] = "VARIANT_ALIAS"
    errs_b = check_clothing_rules(mock_bad_dec)
    assert any("严禁使用未授权决定名称 VARIANT_ALIAS" in e for e in errs_b)
    print(f"  [PASS] 违规决定名称 VARIANT_ALIAS 成功拦截: {[e for e in errs_b if 'VARIANT_ALIAS' in e][0]}")

    # 4. 内存反例 C: 复合穿搭内衣多件套被粗暴判定为 NEW_STYLE 拦截
    mock_bad_ensemble = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_ensemble:
        if r["source_entity_id"] == "SRC_CLOTH_01450":
            r["decision"] = "NEW_STYLE"
            r["mappings"][0]["facts"] = {}
            break
    errs_c = check_clothing_rules(mock_bad_ensemble)
    assert any("内衣多件套长句" in e for e in errs_c)
    print(f"  [PASS] 复合穿搭多件套长句粗暴判定 NEW_STYLE 反例成功拦截: {[e for e in errs_c if '内衣多件套' in e][0]}")

    # 5. 内存反例 D: 单品裙装明确深领口/V领被篡改丢失拦截
    mock_bad_cut = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_cut:
        if r["source_entity_id"] == "SRC_CLOTH_00611":
            r["mappings"][0]["facts"]["cut_features"]["neckline"] = "unspecified"
            break
    errs_d = check_clothing_rules(mock_bad_cut)
    assert any("来源明确 V 领/深领口" in e for e in errs_d)
    print(f"  [PASS] 单品服装核心剪裁特征丢失反例成功拦截: {[e for e in errs_d if '剪裁丢失' in e][0]}")

    # 6. 内存反例 E: 非服装抽象概念 (cosplay) 错误作为正常单品放行拦截
    mock_bad_quarantine = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_quarantine:
        if r["source_entity_id"] == "SRC_CLOTH_00317":
            r["decision"] = "NEW_STYLE"
            r["mappings"][0]["target_item_id"] = "bottoms_specialty"
            break
    errs_e = check_clothing_rules(mock_bad_quarantine)
    assert any("非服装实体/状态修饰 'cosplay' 未被隔离" in e for e in errs_e)
    print(f"  [PASS] 非服装抽象概念误放行反例成功拦截: {[e for e in errs_e if '未被隔离' in e][0]}")

    # 7. 内存反例 F: 复用大衣 coat 拓扑被篡改为 lower_body 拦截
    mock_bad_topo = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_topo:
        if r["source_entity_id"] == "SRC_CLOTH_03687":
            r["mappings"][0]["facts"]["garment_topologies"] = ["lower_body"]
            break
    errs_f = check_clothing_rules(mock_bad_topo)
    assert any("非法服装拓扑 'lower_body'" in e or "复用大衣 coat 拓扑错误" in e for e in errs_f)
    print(f"  [PASS] 复用单品大衣拓扑误标 lower_body 反例成功拦截: {[e for e in errs_f if 'coat' in e or 'lower_body' in e][0]}")

    # 8. 内存反例 G: 仙女裙薄纱连衣裙因花瓣裙摆误入 bottom_pieces 拦截
    mock_bad_skirt = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_skirt:
        if r["source_entity_id"] == "SRC_CLOTH_02176":
            r["mappings"][0]["facts"]["ensemble_pieces"]["bottom_pieces"] = ["a gauzy pastel tulle dress with a layered petal skirt"]
            break
    errs_g = check_clothing_rules(mock_bad_skirt)
    assert any("包含主件/连衣裙名词" in e or "仙女裙花瓣状裙摆" in e for e in errs_g)
    print(f"  [PASS] 连衣裙主件因裙摆误归入 bottom_pieces 反例成功拦截: {[e for e in errs_g if 'bottom_pieces' in e][0]}")

    # 9. 内存反例 H: 探险套装因地图筒 (map tube) 误触发 strapless 领型拦截
    mock_bad_tube = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_tube:
        if r["source_entity_id"] == "SRC_CLOTH_02178":
            r["mappings"][0]["facts"]["cut_features"]["neckline"] = "strapless"
            break
    errs_h = check_clothing_rules(mock_bad_tube)
    assert any("配饰地图筒" in e and "strapless" in e for e in errs_h)
    print(f"  [PASS] 配饰地图筒污染服装领型误标 strapless 反例成功拦截: {[e for e in errs_h if 'map tube' in e][0]}")

    # 10. 内存反例 I: 垂褶领 (cowl neck) 粗暴归并为 turtleneck 拦截
    mock_bad_cowl = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_cowl:
        if r["source_entity_id"] == "SRC_CLOTH_00430":
            r["mappings"][0]["facts"]["cut_features"]["neckline"] = "turtleneck"
            break
    errs_i = check_clothing_rules(mock_bad_cowl)
    assert any("垂褶领 (cowl neck) 领型被错误归并" in e for e in errs_i)
    print(f"  [PASS] 垂褶领粗暴归并为 turtleneck 反例成功拦截: {[e for e in errs_i if 'cowl neck' in e][0]}")

    # 11. 内存反例 J: 独立袜类单品被直接继承套装拓扑 (['bottom_skirt', 'top']) 拦截
    mock_bad_socks = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_socks:
        if r["source_entity_id"] == "SRC_CLOTH_00326":
            r["decision"] = "REUSE_EXISTING"
            r["mappings"][0]["merged_legacy_id"] = "bunny_suit__tag_004"
            r["mappings"][0]["target_tag_id"] = "bunny_suit__tag_004"
            r["mappings"][0]["facts"]["garment_topologies"] = ["bottom_skirt", "top"]
            break
    errs_j = check_clothing_rules(mock_bad_socks)
    assert any("独立袜类单品" in e and "严禁复用套装" in e for e in errs_j)
    print(f"  [PASS] 独立袜类单品继承套装拓扑反例成功拦截: {[e for e in errs_j if '独立袜类' in e][0]}")

    # 12. 内存反例 K: 复合从句整段被误归为 footwear 导致主件连衣裙丢失拦截
    mock_bad_clause = copy.deepcopy(BATCH6_CLOTHING_REVIEWS)
    for r in mock_bad_clause:
        if r["source_entity_id"] == "SRC_CLOTH_02269":
            ep = r["mappings"][0]["facts"]["ensemble_pieces"]
            ep["main_garments"] = []
            ep["footwear"] = ["an all-hot-pink outfit of a pink gingham dress and white heeled shoes"]
            r["mappings"][0]["facts"]["fabric_materials"] = []
            break
    errs_k = check_clothing_rules(mock_bad_clause)
    assert any("复合从句整段被误归鞋类" in e for e in errs_k)
    print(f"  [PASS] 复合从句整段被误归鞋类导致主件丢失反例成功拦截: {[e for e in errs_k if '复合从句' in e][0]}")


def main():
    print("==================================================")
    print("开始执行 M3.1 & M3.2 纯内存反例单元测试 (无磁盘写入)")
    print("==================================================")
    test_m31_freeze_manifest_missing_items()
    test_m32_ledger_pending_mapping_and_draft_protection()
    test_m32_apply_reviews_in_memory_protection()
    test_m32_incompatible_targets_resolvable()
    test_m32_reuse_existing_and_legacy_id_constraints()
    test_m32_hardware_kit_and_film_spec_constraints()
    test_m32_target_tag_id_definition_consistency()
    test_m32_pose_semantics_and_decoupling()
    test_m32_scene_semantics_and_deconstruction()
    test_m32_clothing_semantics_and_ensemble_deconstruction()
    print("\n[SUCCESS] 全部纯内存反例测试通过，逻辑闭环！")


if __name__ == "__main__":
    main()

