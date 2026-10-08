#!/usr/bin/env python3
"""
scratch/build_m3_ledger_skeleton.py
M3.2 双层关系型去重台账生成器与严格内容指纹保护器（完整修复版）：
1. 严格内容指纹校验与全维度防错位：
   - 检查已审核决定 (decision != PENDING_M32_REVIEW)；
   - 检查目标映射表引用 (无论是否待审核，凡被引用的实体强绑定原始内容)；
   - 检查人工草稿备注 (rationale != default_rat)；
   - 一旦检测到上述任何一项关联实体的内容发生顺序漂移，立即终止退出，杜绝映射或决定套给不同词条；
2. 草稿备注增量保留：在待审核状态下，人工填写的 rationale 完整保留，不被模板覆盖；
3. 完整保留 JSON 定位信息与上游 Commit 绑定：
   - 支持 BooruPromptGallery 的 json_pointer (/slots/pose:gesture/48/0)，格式化为 #{json_pointer}；
   - 杜绝残缺的 #L；绑定 upstream_repo 对应的真实 Commit 哈希；
4. 目标映射表外键约束与数据保护：
   - 默认禁止清空已有映射表；校验外键 entity_id 必须与主表原始内容严格一致；
5. 准入校验：生成前自动调用 M3.1 冻结校验器，确保输入完全符合冻结基准。
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

REPO_DIR = Path(__file__).resolve().parent.parent
HERMES_DIR = REPO_DIR.parent
WILDCARDS_DIR = HERMES_DIR / "ai-image-wildcards"

ENTRIES_JSONL = WILDCARDS_DIR / "catalog/entries.jsonl"
SOURCES_JSON = WILDCARDS_DIR / "SOURCES.json"

LICENSE_MAP = {
    "BKWILDCARDS": "CC BY-SA 4.0 (bkidderz, sources/BKWILDCARDS/LICENSE-CONTENT)",
    "mattjaybe": "CC0 1.0 Universal (sources/mattjaybe/LICENSE)",
    "BooruPromptGallery": "AGPL-3.0 (sources/BooruPromptGallery/LICENSE)",
    "DuoUmiWild": "MIT (sources/DuoUmiWild/LICENSE)",
    "SkyyySi": "待核验 (无根目录许可文件，遵循原始上游声明)",
    "Avaray": "待核验 (无根目录许可文件，遵循原始上游声明)",
}


def get_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_upstream_commits() -> dict[str, str]:
    if not SOURCES_JSON.exists():
        return {}
    data = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    return {r["name"]: r.get("commit", "") for r in data.get("repositories", [])}


def load_entries_origins_index() -> dict[tuple[str, str], list[dict]]:
    """加载 entries.jsonl 并建立 (file, prompt) -> origins 列表的索引。"""
    print(f"Loading entries index from {ENTRIES_JSONL}...")
    index = defaultdict(list)
    if not ENTRIES_JSONL.exists():
        print(f"[WARN] {ENTRIES_JSONL} not found, origins lookup will be empty.")
        return index

    with open(ENTRIES_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            fpath = obj.get("file", "").strip()
            prompt = obj.get("prompt", "").strip()
            origins = obj.get("origins", [])
            for orig in origins:
                index[(fpath, prompt)].append(orig)
                fname = Path(fpath).name
                index[(fname, prompt)].append(orig)

    print(f"Indexed {len(index)} file-prompt keys.")
    return index


def format_origins_and_licenses(origins: list[dict], repo_commits: dict[str, str]) -> tuple[str, str, str]:
    """返回 (upstream_repos, upstream_locations, licenses) 完整保留 json_pointer 与 commit"""
    if not origins:
        return "UNKNOWN", "UNKNOWN", "待核验 (未检索到 origins 索引)"

    repos = []
    locations = []
    licenses = []
    seen_locs = set()

    for orig in origins:
        repo = orig.get("repo", "UNKNOWN")
        path = orig.get("path", "")
        line = orig.get("line")
        json_ptr = orig.get("json_pointer")
        sec = orig.get("section", "")
        commit = repo_commits.get(repo, "")
        commit_short = commit[:8] if commit else ""

        # 精确构建定位锚点：优先 json_pointer，其次 line，绝不输出残缺 #L
        loc_anchor = ""
        if json_ptr:
            loc_anchor = f"#{json_ptr}"
        elif line is not None and str(line).strip() and str(line) != "0":
            loc_anchor = f"#L{line}"

        repo_part = f"{repo}@{commit_short}" if commit_short else repo
        loc_str = f"{repo_part}:{path}{loc_anchor}"
        if sec:
            loc_str += f" ({sec})"

        if loc_str not in seen_locs:
            seen_locs.add(loc_str)
            repos.append(repo)
            locations.append(loc_str)
            lic = LICENSE_MAP.get(repo, "待核验 (未声明/存疑)")
            licenses.append(f"{repo}: {lic}")

    return " | ".join(sorted(set(repos))), " ; ".join(locations), " ; ".join(sorted(set(licenses)))


def assemble_ledger_records(
    items_to_process: list[tuple[str, str, str, str, str, list[dict], str]],
    existing_by_id: dict[str, dict],
    existing_by_content: dict[tuple[str, str], dict],
    referenced_entity_ids: set[str],
    repo_commits: dict[str, str],
    force_rebuild: bool = False,
) -> tuple[list[list[str]], list[str], int, int]:
    """纯内存装配与指纹校验函数。
    返回 (records, disorder_errors, preserved_reviewed_count, preserved_draft_rat_count)
    """
    records = []
    sources_count = 0
    preserved_reviewed_count = 0
    preserved_draft_rat_count = 0
    disorder_errors = []

    prefix_map = {
        "hair": "SRC_HAIR_",
        "clothing": "SRC_CLOTH_",
        "pose_action": "SRC_POSE_",
        "scene": "SRC_SCENE_",
        "camera": "SRC_CAM_",
        "lighting": "SRC_LIGHT_",
        "expression": "SRC_EXPR_",
        "accessories": "SRC_ACC_",
    }

    for cat, file_rel, line_num, raw_text, file_sha, origs, default_rat in items_to_process:
        sources_count += 1
        eid = f"{prefix_map[cat]}{sources_count:05d}"
        repos, locs, lics = format_origins_and_licenses(origs, repo_commits)

        dec = "PENDING_M32_REVIEW"
        rat = default_rat

        if eid in existing_by_id and not force_rebuild:
            old_row = existing_by_id[eid]
            old_text = old_row.get("source_raw_text", "").strip()
            old_cat = old_row.get("category", "").strip()
            old_dec = old_row.get("decision", "PENDING_M32_REVIEW")
            old_rat = old_row.get("rationale", "").strip()

            has_formal_review = (old_dec != "PENDING_M32_REVIEW")
            is_referenced_in_mappings = (eid in referenced_entity_ids)
            has_custom_rationale = (bool(old_rat) and old_rat != default_rat.strip())

            # 校验指纹一致性
            if (old_cat, old_text) != (cat, raw_text):
                if has_formal_review or is_referenced_in_mappings or has_custom_rationale:
                    # 发生了致命的内容错位！
                    disorder_errors.append(
                        f"编号 {eid} 发生审核决定/映射关系/草稿备注错位风险！\n"
                        f"  - 触发保护原因: [已有正式决定={has_formal_review}, 被映射表引用={is_referenced_in_mappings}, 已有自定义草稿备注={has_custom_rationale}]\n"
                        f"  - 原实体内容: [{old_cat}] '{old_text}'\n"
                        f"  - 当前新输入: [{cat}] '{raw_text}'\n"
                        f"  - 阻断原因: 拒绝将已关联资产或草稿劳动的编号套给完全不同的新词条！"
                    )
            else:
                # 内容完全一致：安全继承正式审核决定与人工草稿备注
                if has_formal_review:
                    dec = old_dec
                    preserved_reviewed_count += 1
                if has_custom_rationale:
                    rat = old_rat
                    preserved_draft_rat_count += 1
        elif (cat, raw_text) in existing_by_content and not force_rebuild:
            # 相同内容出现在不同编号：安全基于内容继承
            old_row = existing_by_content[(cat, raw_text)]
            old_eid = old_row.get("entity_id", "")
            if old_eid in referenced_entity_ids and old_eid != eid:
                disorder_errors.append(
                    f"词条 [{cat}] '{raw_text}' 原编号 {old_eid} 已被目标映射表引用，但当前新编号漂移为 {eid}！\n"
                    f"  - 阻断原因: 目标映射表外键强绑定旧编号 {old_eid}，编号变动将导致外键错位，拒绝静默重新编号！"
                )
            if old_row.get("decision") != "PENDING_M32_REVIEW":
                dec = old_row["decision"]
                preserved_reviewed_count += 1
            old_rat = old_row.get("rationale", "").strip()
            if old_rat and old_rat != default_rat.strip():
                rat = old_rat
                preserved_draft_rat_count += 1

        records.append([
            eid, "EXTERNAL_SOURCE", cat, file_rel, line_num,
            raw_text, file_sha, repos, locs, lics, dec, rat
        ])

    return records, disorder_errors, preserved_reviewed_count, preserved_draft_rat_count


def main():
    parser = argparse.ArgumentParser(description="M3.2 去重台账骨架与真实来源回填工具 (带内容指纹与防覆盖保护)")
    parser.add_argument("--force-rebuild", action="store_true", help="强制重建来源实体表 (重置所有 decision 与草稿)")
    parser.add_argument("--force-empty-mappings", action="store_true", help="强制清空目标映射表")
    parser.add_argument("--skip-baseline-verify", action="store_true", help="跳过前置 M3.1 冻结基线校验 (仅调试)")
    args = parser.parse_args()

    print("=== M3.2 双层去重台账真实来源回填与骨架生成 ===")

    # 1. 前置门禁：校验 M3.1 冻结基线
    if not args.skip_baseline_verify:
        print("执行前置 M3.1 冻结清单严格校验...")
        verify_script = REPO_DIR / "scratch" / "generate_m3_baseline_freeze.py"
        res = subprocess.run([sys.executable, str(verify_script)], capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[FAIL] 前置 M3.1 冻结清单校验失败，拒绝生成台账！\n{res.stdout}\n{res.stderr}")
            sys.exit(1)
        print("[PASS] 前置 M3.1 冻结清单校验通过。")

    source_entities_path = REPO_DIR / "scratch" / "rc10_source_entities.tsv"
    target_mappings_path = REPO_DIR / "scratch" / "rc10_target_mappings.tsv"

    # 2. 检查现有目标映射表，收集被引用的实体 ID 与内容指纹
    referenced_entity_ids = set()
    referenced_entity_fingerprints: dict[str, tuple[str, str]] = {}
    target_protected = False
    if target_mappings_path.exists() and not args.force_empty_mappings:
        with open(target_mappings_path, "r", encoding="utf-8") as f:
            mapping_lines = [map_line for map_line in f if map_line.strip()]
        if len(mapping_lines) > 1:
            reader = csv.DictReader(mapping_lines, delimiter="\t")
            for row in reader:
                fk = row.get("source_entity_id", "").strip()
                if fk:
                    referenced_entity_ids.add(fk)
            print(f"[PROTECTION] 目标映射表包含 {len(mapping_lines)-1} 条已有数据 (覆盖 {len(referenced_entity_ids)} 个实体引用)，跳过清空并受强指纹保护！")
            target_protected = True

    # 3. 检查现有主台账的内容指纹与审核记录
    existing_by_id: dict[str, dict] = {}
    existing_by_content: dict[tuple[str, str], dict] = {}

    if source_entities_path.exists():
        with open(source_entities_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                eid = row.get("entity_id", "")
                cat = row.get("category", "")
                raw_text = row.get("source_raw_text", "").strip()
                existing_by_id[eid] = row
                existing_by_content[(cat, raw_text)] = row
                if eid in referenced_entity_ids:
                    referenced_entity_fingerprints[eid] = (cat, raw_text)

    repo_commits = load_upstream_commits()
    origins_index = load_entries_origins_index()

    source_headers = [
        "entity_id",
        "entity_type",
        "category",
        "source_file_relpath",
        "source_line_num",
        "source_raw_text",
        "source_file_sha256",
        "upstream_repos",
        "upstream_locations",
        "licenses",
        "decision",
        "rationale",
    ]

    target_headers = [
        "mapping_id",
        "source_entity_id",
        "target_catalog_file",
        "target_item_id",
        "target_tag_id",
        "target_role",
        "target_tag_text",
        "merged_legacy_id",
        "semantic_facts_json",
    ]

    # 4. 收集输入数据
    hair_csv = WILDCARDS_DIR / "catalog/gender-analysis/hair-gender.csv"
    hair_sha = get_sha256(hair_csv)
    with open(hair_csv, "r", encoding="utf-8") as f:
        hair_entries = [r for r in csv.DictReader(f) if r.get("标签") in ("女", "通用")]

    clothing_csv = WILDCARDS_DIR / "catalog/gender-analysis/clothing-gender.csv"
    clothing_sha = get_sha256(clothing_csv)
    with open(clothing_csv, "r", encoding="utf-8") as f:
        clothing_entries = [r for r in csv.DictReader(f) if r.get("标签") in ("女", "通用")]

    pose_txt = WILDCARDS_DIR / "wildcards/04_pose/action.txt"
    pose_sha = get_sha256(pose_txt)
    pose_lines = [(idx, line.strip()) for idx, line in enumerate(open(pose_txt, encoding="utf-8"), 1) if line.strip() and not line.startswith("#")]

    scene_txt = WILDCARDS_DIR / "wildcards/05_scene/scene.txt"
    scene_sha = get_sha256(scene_txt)
    scene_lines = [(idx, line.strip()) for idx, line in enumerate(open(scene_txt, encoding="utf-8"), 1) if line.strip() and not line.startswith("#")]

    camera_dir = WILDCARDS_DIR / "wildcards/06_camera"
    camera_entries = []
    seen_camera = set()
    for cf in sorted(camera_dir.glob("*.txt")):
        c_sha = get_sha256(cf)
        rel_f = f"wildcards/06_camera/{cf.name}"
        with open(cf, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                item_text = line.strip()
                if item_text and not item_text.startswith("#") and item_text not in seen_camera:
                    seen_camera.add(item_text)
                    camera_entries.append((rel_f, cf.name, str(idx), item_text, c_sha))

    lighting_txt = WILDCARDS_DIR / "wildcards/07_lighting/lighting.txt"
    light_sha = get_sha256(lighting_txt)
    light_lines = [(idx, line.strip()) for idx, line in enumerate(open(lighting_txt, encoding="utf-8"), 1) if line.strip() and not line.startswith("#")]

    expr_txt = WILDCARDS_DIR / "wildcards/08_expression/expression.txt"
    expr_sha = get_sha256(expr_txt)
    expr_lines = [(idx, line.strip()) for idx, line in enumerate(open(expr_txt, encoding="utf-8"), 1) if line.strip() and not line.startswith("#")]

    acc_dir = WILDCARDS_DIR / "wildcards/10_accessories"
    acc_entries = []
    seen_acc = set()
    for af in sorted(acc_dir.glob("*.txt")):
        a_sha = get_sha256(af)
        rel_f = f"wildcards/10_accessories/{af.name}"
        with open(af, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                item_text = line.strip()
                if item_text and not item_text.startswith("#") and item_text not in seen_acc:
                    seen_acc.add(item_text)
                    acc_entries.append((rel_f, af.name, str(idx), item_text, a_sha))

    # 组装扁平待装配条目列表
    items_to_process = []
    for r in hair_entries:
        text = r.get("词条", "").strip()
        subf = r.get("所属文件", "")
        origs = origins_index.get((f"wildcards/02_hair/{subf}.txt", text)) or origins_index.get((f"{subf}.txt", text)) or []
        items_to_process.append(("hair", "catalog/gender-analysis/hair-gender.csv", r.get("行号", ""), text, hair_sha, origs, f"Gender: {r.get('标签')}, Reason: {r.get('理由', '')}"))

    for r in clothing_entries:
        text = r.get("词条", "").strip()
        subfiles = [sf.strip() for sf in r.get("所属文件", "").split(",") if sf.strip()]
        origs = []
        for sf in subfiles:
            found = origins_index.get((f"wildcards/03_clothing/{sf}.txt", text)) or origins_index.get((f"{sf}.txt", text))
            if found:
                origs.extend(found)
        items_to_process.append(("clothing", "catalog/gender-analysis/clothing-gender.csv", r.get("行号", ""), text, clothing_sha, origs, f"Gender: {r.get('标签')}, Reason: {r.get('理由', '')}"))

    for idx, text_entry in pose_lines:
        origs = origins_index.get(("wildcards/04_pose/action.txt", text_entry)) or []
        items_to_process.append(("pose_action", "wildcards/04_pose/action.txt", str(idx), text_entry, pose_sha, origs, "Action pool entry"))

    for idx, text_entry in scene_lines:
        origs = origins_index.get(("wildcards/05_scene/scene.txt", text_entry)) or []
        items_to_process.append(("scene", "wildcards/05_scene/scene.txt", str(idx), text_entry, scene_sha, origs, "Scene location entry"))

    for rel_f, cf_name, idx_str, text_entry, c_sha in camera_entries:
        origs = origins_index.get((rel_f, text_entry)) or origins_index.get((cf_name, text_entry)) or []
        items_to_process.append(("camera", rel_f, idx_str, text_entry, c_sha, origs, f"Camera subtype from {cf_name}"))

    for idx, text_entry in light_lines:
        origs = origins_index.get(("wildcards/07_lighting/lighting.txt", text_entry)) or []
        items_to_process.append(("lighting", "wildcards/07_lighting/lighting.txt", str(idx), text_entry, light_sha, origs, "Lighting preset entry"))

    for idx, text_entry in expr_lines:
        origs = origins_index.get(("wildcards/08_expression/expression.txt", text_entry)) or []
        items_to_process.append(("expression", "wildcards/08_expression/expression.txt", str(idx), text_entry, expr_sha, origs, "Expression mood entry"))

    for rel_f, af_name, idx_str, text_entry, a_sha in acc_entries:
        origs = origins_index.get((rel_f, text_entry)) or origins_index.get((af_name, text_entry)) or []
        items_to_process.append(("accessories", rel_f, idx_str, text_entry, a_sha, origs, f"Accessory item from {af_name}"))

    # 5. 执行纯内存装配与指纹校验
    records, disorder_errors, preserved_reviewed_count, preserved_draft_rat_count = assemble_ledger_records(
        items_to_process=items_to_process,
        existing_by_id=existing_by_id,
        existing_by_content=existing_by_content,
        referenced_entity_ids=referenced_entity_ids,
        repo_commits=repo_commits,
        force_rebuild=args.force_rebuild,
    )

    if disorder_errors:
        print(f"\n[FAIL] 检测到 {len(disorder_errors)} 处审核决定/映射关系/草稿备注错位风险，构建终止！")
        for err in disorder_errors[:5]:
            print(f"----------------------------------------\n{err}")
        if len(disorder_errors) > 5:
            print(f"... 剩余 {len(disorder_errors)-5} 处省略。")
        print("\n提示：请检查输入数据是否有乱序、重排序或删除。若确认需要强制重建，请传 --force-rebuild。")
        sys.exit(1)

    assert len(records) == 6939, f"Expected 6939, got {len(records)}"

    # 6. 最终验证映射表外键约束与实体内容身份
    if target_protected:
        valid_eids = {r[0] for r in records}
        records_fingerprints = {r[0]: (r[2], r[5]) for r in records}
        with open(target_mappings_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            fk_errors = []
            for row in reader:
                fk = row.get("source_entity_id", "").strip()
                mid = row.get("mapping_id", "")
                if not fk:
                    continue
                if fk not in valid_eids:
                    fk_errors.append(f"Mapping ID {mid} 外键 {fk} 在主表中不存在！")
                elif fk in referenced_entity_fingerprints:
                    expected_fp = referenced_entity_fingerprints[fk]
                    curr_fp = records_fingerprints.get(fk)
                    if expected_fp != curr_fp:
                        fk_errors.append(
                            f"Mapping ID {mid} 引用的外键实体 {fk} 内容发生漂移！\n"
                            f"  - 映射绑定时内容: [{expected_fp[0]}] '{expected_fp[1]}'\n"
                            f"  - 当前新生成内容: [{curr_fp[0]}] '{curr_fp[1]}'\n"
                            f"  - 阻断原因: 拒绝将映射关系指向被篡改的不同实体！"
                        )
            if fk_errors:
                print(f"[FAIL] 映射表外键约束与实体内容身份核验失败：{len(fk_errors)} 条异常！\n" + "\n".join(fk_errors[:5]))
                sys.exit(1)

        # 6.2 强校验：目标映射表中所有 incompatible_with 声明的目标 ID 均可被现有运行时库或本台账已规划目标解析
        with open(target_mappings_path, "r", encoding="utf-8") as f:
            all_mappings = list(csv.DictReader(f, delimiter="\t"))

        known_ids = set()
        for fn in ["accessories.json", "shot_types.json", "film_stocks.json"]:
            fpath = REPO_DIR / "data" / fn
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
        for m in all_mappings:
            if m.get("target_item_id"):
                known_ids.add(m["target_item_id"])
            if m.get("target_tag_id"):
                known_ids.add(m["target_tag_id"])

        incompat_errors = []
        for m in all_mappings:
            mid = m.get("mapping_id", "")
            eid = m.get("source_entity_id", "")
            raw_facts = m.get("semantic_facts_json", "")
            if not raw_facts:
                continue
            try:
                facts = json.loads(raw_facts)
            except Exception:
                continue
            for target in facts.get("incompatible_with", []):
                if target not in known_ids:
                    incompat_errors.append(f"映射 {mid} (实体 {eid}) 的互斥约束 incompatible_with 引用了不存在的目标 ID: '{target}'！")

        if incompat_errors:
            print(f"[FAIL] 映射表互斥约束悬空引用检查失败：{len(incompat_errors)} 处未解析！\n" + "\n".join(incompat_errors[:5]))
            sys.exit(1)

        # 6.3 强校验：REUSE_EXISTING 实体对应的 mapping 必须在运行时基线库中真实存在，且 merged_legacy_id 必须匹配；
        #            非 REUSE_EXISTING 实体禁止携带 merged_legacy_id
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

        baseline_tag_ids = set()
        for p in (REPO_DIR / "data").glob("*.json"):
            try:
                fdata = json.loads(p.read_text(encoding="utf-8"))
                baseline_tag_ids.update(extract_tags(fdata))
            except Exception:
                continue

        decisions_by_eid = {r[0]: r[10] for r in records}
        reuse_errors = []
        for m in all_mappings:
            mid = m.get("mapping_id", "")
            eid = m.get("source_entity_id", "")
            dec = decisions_by_eid.get(eid, "")
            legacy_id = m.get("merged_legacy_id", "").strip()
            target_tag_id = m.get("target_tag_id", "").strip()

            if dec == "REUSE_EXISTING":
                if not legacy_id:
                    reuse_errors.append(f"映射 {mid} (实体 {eid}) 判定为 REUSE_EXISTING，但 merged_legacy_id 为空！")
                elif legacy_id not in baseline_tag_ids:
                    reuse_errors.append(f"映射 {mid} (实体 {eid}) 判定为 REUSE_EXISTING，但 legacy ID '{legacy_id}' 在基线运行时库中不存在！")
                if target_tag_id not in baseline_tag_ids:
                    reuse_errors.append(f"映射 {mid} (实体 {eid}) 判定为 REUSE_EXISTING，但目标标签 '{target_tag_id}' 在基线运行时库中不存在！")
            else:
                if legacy_id:
                    reuse_errors.append(f"映射 {mid} (实体 {eid}) 判定为 {dec} (非 REUSE_EXISTING)，禁止携带 merged_legacy_id ('{legacy_id}')！")

        if reuse_errors:
            print(f"[FAIL] REUSE_EXISTING 基线真实性与旧标签引用检查失败：{len(reuse_errors)} 处异常！\n" + "\n".join(reuse_errors[:5]))
            sys.exit(1)

        # 6.4 强校验：器材套机硬件兼容性、DEFERRED_ISSUE 隔离与胶卷感光度规范
        spec_errors = []
        for m in all_mappings:
            mid = m.get("mapping_id", "")
            eid = m.get("source_entity_id", "")
            dec = decisions_by_eid.get(eid, "")
            raw_facts = m.get("semantic_facts_json", "")
            if not raw_facts:
                continue
            try:
                facts = json.loads(raw_facts)
            except Exception:
                continue
            # 套机兼容性检查与隔离门禁
            if facts.get("device_category") == "camera_hardware_kit":
                if facts.get("hardware_kit_compatible") is False:
                    if facts.get("spec_verified") is True:
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 为物理不兼容套机 (hardware_kit_compatible=False)，禁止标记 spec_verified=True！")
                    if not facts.get("kit_compatibility_status"):
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 为不兼容套机，必须提供 kit_compatibility_status 状态代码！")
                    if dec != "DEFERRED_ISSUE":
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 为物理不兼容套机，来源审核决定必须为 DEFERRED_ISSUE (当前为 {dec})！")
                    if m.get("target_role") == "capture_device":
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 为物理不兼容套机，目标角色禁止设为正常入库角色 capture_device (应为 quarantine 等隔离角色)！")
                    if facts.get("target_export_eligible") is True or facts.get("importable") is True:
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 为物理不兼容套机，禁止标记可入库/可导出 (target_export_eligible/importable 应为 False)！")
            # 胶卷 ISO/EI 分离检查
            if facts.get("is_camera_film") is True:
                if facts.get("nominal_name_rating") == 3200 or 3200 in (facts.get("recommended_ei"),):
                    if facts.get("measured_iso") == 3200:
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 胶卷标定测定感光度 measured_iso=3200 错误 (官方测定通常为 800-1000，3200 为推荐 EI)！")
                    if not facts.get("recommended_ei"):
                        spec_errors.append(f"映射 {mid} (实体 {eid}) 胶卷缺少 recommended_ei (推荐曝光指数) 标定！")

        if spec_errors:
            print(f"[FAIL] 器材套机兼容性与胶卷感光度规范检查失败：{len(spec_errors)} 处异常！\n" + "\n".join(spec_errors[:5]))
            sys.exit(1)

        # 6.5 强校验：同一目标标签 ID (target_tag_id) 的定义必须完全一致 (目标文件、所属项 ID、目标输出文本)
        tag_definitions: dict[str, dict] = {}
        target_def_errors = []
        for m in all_mappings:
            tid = m.get("target_tag_id", "").strip()
            if not tid:
                continue
            fpath = m.get("target_catalog_file", "").strip()
            item_id = m.get("target_item_id", "").strip()
            tag_text = m.get("target_tag_text", "").strip()
            mid = m.get("mapping_id", "")
            eid = m.get("source_entity_id", "")

            if tid not in tag_definitions:
                tag_definitions[tid] = {
                    "target_catalog_file": fpath,
                    "target_item_id": item_id,
                    "target_tag_text": tag_text,
                    "first_mapping": mid,
                    "first_entity": eid,
                }
            else:
                first = tag_definitions[tid]
                if fpath != first["target_catalog_file"]:
                    target_def_errors.append(
                        f"目标标签 ID '{tid}' 在映射 {mid} (实体 {eid}) 中归属文件 '{fpath}' 与映射 {first['first_mapping']} 中 '{first['target_catalog_file']}' 不一致！"
                    )
                if item_id != first["target_item_id"]:
                    target_def_errors.append(
                        f"目标标签 ID '{tid}' 在映射 {mid} (实体 {eid}) 中归属 item_id '{item_id}' 与映射 {first['first_mapping']} 中 '{first['target_item_id']}' 不一致！"
                    )
                if tag_text != first["target_tag_text"]:
                    target_def_errors.append(
                        f"目标标签 ID '{tid}' 在映射 {mid} (实体 {eid}) 中输出文本 '{tag_text}' 与映射 {first['first_mapping']} 中 '{first['target_tag_text']}' 不一致！"
                    )

        if target_def_errors:
            print(f"[FAIL] 同一目标标签 ID 定义一致性检查失败：{len(target_def_errors)} 处冲突！\n" + "\n".join(target_def_errors[:5]))
            sys.exit(1)

    # 写入主表
    with open(source_entities_path, "w", encoding="utf-8", newline="") as sf:
        swriter = csv.writer(sf, delimiter="\t")
        swriter.writerow(source_headers)
        swriter.writerows(records)

    # 初始化空映射表（仅当未被保护时）
    if not target_protected:
        with open(target_mappings_path, "w", encoding="utf-8", newline="") as mf:
            mwriter = csv.writer(mf, delimiter="\t")
            mwriter.writerow(target_headers)

    print(f"[OK] M3.2 双层台账构建成功: {source_entities_path}")
    print(f"     已继承保留正式审核决定: {preserved_reviewed_count} 条")
    print(f"     已继承保留人工草稿备注: {preserved_draft_rat_count} 条")
    print(f"     目标映射表状态: {'[PROTECTED 已保留并验证外键及内容身份]' if target_protected else '[INITIALIZED 已初始化]'}")


if __name__ == "__main__":
    main()
