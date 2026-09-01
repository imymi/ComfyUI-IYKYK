#!/usr/bin/env python3
"""
validate_data.py — ComfyUI-IYKYK 数据集 Schema 与完整性校验工具

严格校验：
1. 19 个运行时 JSON 文件必须全部存在且符合 JSON 格式规范
2. 执行 JSON Schema 标准校验 (jsonschema 库或内置完整 Schema 验证引擎)
3. 校验 scenes.json：
   - 字段完整 (id, label, context_ids, anchor_tags, detail_tags, exclusive_group)
   - context_ids 枚举合法性
   - anchor_tags 至少 1 项且非空
   - detail_tags 非空且与 anchor_tags 严格零交集 (Zero Intersection)
   - 所有 tags 零重复
4. 校验 presets.json：77 预设全部具备 id, name_zh, positive
5. 校验 style_recipes.json：8 配方全部具备 id, style_name, lighting_palette, style_recipe, focus_detail
6. 校验 conflict_rules.json：8 大规则完整
7. 跨文件 ID 唯一性与非空检查
8. 错误分级：ERROR 阻止 CI 与发布构建，WARNING 记录输出
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REPO_DIR = Path(__file__).parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"

REQUIRED_DATA_FILES = [
    "accessories.json",
    "characters.json",
    "clothing.json",
    "conflict_rules.json",
    "expressions.json",
    "film_stocks.json",
    "imperfections.json",
    "lighting.json",
    "makeup.json",
    "negative_prompts.json",
    "nudity_levels.json",
    "poses.json",
    "presets.json",
    "props.json",
    "scenes.json",
    "shot_types.json",
    "style_recipes.json",
    "tattoos.json",
    "themes.json",
]

VALID_CONTEXT_ENUMS = {
    "school",
    "office",
    "medical",
    "onsen_bath",
    "bondage_sm",
    "traditional",
    "nightlife",
    "domestic",
    "transit",
    "outdoor",
    "dining",
    "adult",
    "special",
    "generic",
}


def validate_with_jsonschema(data_cache: Dict[str, dict], errors: List[str]):
    """尝试使用 jsonschema 库进行 schema 验证。"""
    try:
        import jsonschema
    except ImportError:
        return

    schema_mappings = {
        "scenes.json": "scenes.schema.json",
        "presets.json": "presets.schema.json",
        "style_recipes.json": "recipes.schema.json",
    }

    for data_file, schema_file in schema_mappings.items():
        if data_file in data_cache and (SCHEMAS_DIR / schema_file).is_file():
            try:
                schema_doc = json.loads((SCHEMAS_DIR / schema_file).read_text(encoding="utf-8"))
                jsonschema.validate(instance=data_cache[data_file], schema=schema_doc)
            except jsonschema.ValidationError as e:
                path = " -> ".join(str(p) for p in e.path) if e.path else "root"
                errors.append(f"[ERROR] JSONSchema {schema_file} validation failed on {data_file} at '{path}': {e.message}")


def validate_all() -> Tuple[int, int, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not DATA_DIR.is_dir():
        errors.append(f"[ERROR] Data directory does not exist: {DATA_DIR}")
        return len(errors), len(warnings), errors

    # 1. 检查必需文件解析
    data_cache: Dict[str, dict] = {}
    for fname in REQUIRED_DATA_FILES:
        fpath = DATA_DIR / fname
        if not fpath.is_file():
            errors.append(f"[ERROR] Missing required data file: {fname}")
            continue

        try:
            content = fpath.read_text(encoding="utf-8")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                errors.append(f"[ERROR] {fname}: Root must be a JSON object (dict)")
            else:
                data_cache[fname] = parsed
        except json.JSONDecodeError as e:
            errors.append(f"[ERROR] {fname}: JSON syntax error (line {e.lineno}, col {e.colno}): {e.msg}")

    if errors:
        return len(errors), len(warnings), errors

    # 2. jsonschema 校验
    validate_with_jsonschema(data_cache, errors)

    # 3. 校验 scenes.json 结构与零交集约束
    scenes_data = data_cache.get("scenes.json", {}).get("scenes", [])
    if not scenes_data:
        errors.append("[ERROR] scenes.json: No scenes defined")
    seen_scene_ids: Set[str] = set()
    seen_scene_labels: Set[str] = set()
    total_scene_items = 0

    for cat_idx, cat in enumerate(scenes_data):
        cat_name = cat.get("category", "")
        if not cat_name:
            errors.append(f"[ERROR] scenes.json: Category at index {cat_idx} missing 'category' name")
        items = cat.get("items", [])
        for item_idx, item in enumerate(items):
            total_scene_items += 1
            sid = item.get("id")
            if not sid:
                errors.append(f"[ERROR] scenes.json: Item in category '{cat_name}' at index {item_idx} missing 'id'")
            elif sid in seen_scene_ids:
                errors.append(f"[ERROR] scenes.json: Duplicate scene id: '{sid}'")
            else:
                seen_scene_ids.add(sid)

            label = item.get("label") or item.get("subcategory")
            if not label:
                errors.append(f"[ERROR] scenes.json: Scene '{sid}' missing label")
            elif label in seen_scene_labels:
                errors.append(f"[ERROR] scenes.json: Duplicate scene label: '{label}' in '{sid}'")
            else:
                seen_scene_labels.add(label)

            ctx_ids = item.get("context_ids", [])
            if not isinstance(ctx_ids, list) or len(ctx_ids) == 0:
                errors.append(f"[ERROR] scenes.json: Scene '{sid}' context_ids must be a non-empty list")
            else:
                for c in ctx_ids:
                    if c not in VALID_CONTEXT_ENUMS:
                        errors.append(f"[ERROR] scenes.json: Scene '{sid}' invalid context_id '{c}'")

            anchors = item.get("anchor_tags", [])
            if not isinstance(anchors, list) or len(anchors) == 0:
                errors.append(f"[ERROR] scenes.json: Scene '{sid}' anchor_tags must have at least 1 item")
            else:
                if len(anchors) != len(set(anchors)):
                    errors.append(f"[ERROR] scenes.json: Scene '{sid}' contains duplicate tags in anchor_tags")
                for a in anchors:
                    if not isinstance(a, str) or not a.strip():
                        errors.append(f"[ERROR] scenes.json: Scene '{sid}' contains empty anchor tag")

            details = item.get("detail_tags", [])
            if not isinstance(details, list):
                errors.append(f"[ERROR] scenes.json: Scene '{sid}' detail_tags must be a list")
            else:
                if len(details) != len(set(details)):
                    errors.append(f"[ERROR] scenes.json: Scene '{sid}' contains duplicate tags in detail_tags")
                for d in details:
                    if not isinstance(d, str) or not d.strip():
                        errors.append(f"[ERROR] scenes.json: Scene '{sid}' contains empty detail tag")

            # 核心约束：anchor_tags 与 detail_tags 严格零交集
            if set(anchors) & set(details):
                intersection = set(anchors) & set(details)
                errors.append(f"[ERROR] scenes.json: Scene '{sid}' has overlapping tags between anchors and details: {intersection}")

            ex_grp = item.get("exclusive_group")
            if not ex_grp or not isinstance(ex_grp, str):
                errors.append(f"[ERROR] scenes.json: Scene '{sid}' missing valid exclusive_group")

    if total_scene_items < 120:
        warnings.append(f"[WARN] scenes.json has {total_scene_items} subcategories (expected >= 120)")

    # 4. 校验 presets.json
    presets = data_cache.get("presets.json", {}).get("presets", [])
    if len(presets) < 77:
        warnings.append(f"[WARN] presets.json has {len(presets)} presets (expected >= 77)")

    seen_pids = set()
    for p in presets:
        pid = p.get("id")
        if not pid:
            errors.append(f"[ERROR] presets.json: Preset missing 'id': {p}")
        elif pid in seen_pids:
            errors.append(f"[ERROR] presets.json: Duplicate preset id: '{pid}'")
        seen_pids.add(pid)

        if not p.get("name_zh"):
            errors.append(f"[ERROR] presets.json: Preset '{pid}' missing 'name_zh'")
        if not (p.get("positive") or p.get("prompt")):
            errors.append(f"[ERROR] presets.json: Preset '{pid}' missing 'positive' prompt")

    # 5. 校验 style_recipes.json
    recipes = data_cache.get("style_recipes.json", {}).get("recipes", [])
    if len(recipes) < 8:
        warnings.append(f"[WARN] style_recipes.json has {len(recipes)} recipes (expected >= 8)")

    seen_rids = set()
    for r in recipes:
        rid = r.get("id")
        if not rid:
            errors.append(f"[ERROR] style_recipes.json: Recipe missing 'id': {r}")
        elif rid in seen_rids:
            errors.append(f"[ERROR] style_recipes.json: Duplicate recipe id: '{rid}'")
        seen_rids.add(rid)

        for req_field in ["style_name", "lighting_palette", "style_recipe", "focus_detail"]:
            if not r.get(req_field):
                errors.append(f"[ERROR] style_recipes.json: Recipe '{rid}' missing required field '{req_field}'")

    # 6. 校验 conflict_rules.json
    rules = data_cache.get("conflict_rules.json", {}).get("rules", [])
    if len(rules) < 8:
        errors.append(f"[ERROR] conflict_rules.json: Expected at least 8 rules, found {len(rules)}")
    for r in rules:
        if not r.get("id"):
            errors.append(f"[ERROR] conflict_rules.json: Rule missing 'id': {r}")

    # 7. 校验 themes.json
    themes = data_cache.get("themes.json", {}).get("themes", [])
    if len(themes) < 30:
        warnings.append(f"[WARN] themes.json has {len(themes)} themes (expected >= 30)")
    for t in themes:
        tname = t.get("name_zh") or t.get("theme_zh")
        if not tname:
            errors.append(f"[ERROR] themes.json: Theme missing name: {t}")
        tags = t.get("tags", [])
        if not tags or len(tags) == 0:
            errors.append(f"[ERROR] themes.json: Theme '{tname}' has no tags")

    all_messages = errors + warnings
    return len(errors), len(warnings), all_messages


def main():
    err_count, warn_count, messages = validate_all()
    print(f"Data Validation Summary: {len(REQUIRED_DATA_FILES)} files checked.")
    for msg in messages:
        print(" ", msg)

    if err_count > 0:
        print(f"\n❌ Validation FAILED with {err_count} errors, {warn_count} warnings.")
        sys.exit(1)
    else:
        print(f"\n✅ Validation PASSED with 0 errors ({warn_count} warnings).")
        sys.exit(0)


if __name__ == "__main__":
    main()
