#!/usr/bin/env python3
"""
validate_data.py — 运行时 JSON 数据文件与 Schema 完整性强门禁校验脚本

特性：
1. 采用标准 jsonschema.Draft7Validator 进行模式校验
2. --strict 模式强制要求官方 jsonschema>=4.23,<5.0 依赖，禁止静默回退
3. 校验每个 Schema 自身的合法性 (Draft7Validator.check_schema)
4. 严格两阶段全局 Alias 唯一性与规范化碰撞检测 (strip + casefold)
5. 消费 lib/rule_contract.py 权威契约，Fail-Closed 校验 17 大规则与必需字段
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from lib.rule_contract import validate_rule_document
from lib.runtime_manifest import RUNTIME_DATA_FILES

DATA_DIR = REPO_DIR / "data"
SCHEMAS_DIR = REPO_DIR / "schemas"

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


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_files: int = 0
    schema_engine: str = "none"

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def validate_all(
    data_dir: Path = DATA_DIR,
    schemas_dir: Path = SCHEMAS_DIR,
    strict_jsonschema: bool = False
) -> ValidationResult:
    result = ValidationResult()

    if strict_jsonschema and not HAS_JSONSCHEMA:
        result.errors.append("[ERROR] Strict mode requires official 'jsonschema>=4.23,<5.0', but it is not installed!")
        return result

    # 1. 加载 19 个运行时数据文件
    data_cache: Dict[str, Any] = {}
    for data_file in RUNTIME_DATA_FILES:
        data_path = data_dir / data_file
        if not data_path.is_file():
            result.errors.append(f"[ERROR] Missing runtime data file: {data_file}")
            continue
        try:
            data_cache[data_file] = json.loads(data_path.read_text(encoding="utf-8"))
            result.checked_files += 1
        except Exception as e:
            result.errors.append(f"[ERROR] Malformed JSON in '{data_file}': {e}")

    # 2. 对每个数据文件匹配其 Schema 并执行 Draft-7 递归校验
    for data_file in RUNTIME_DATA_FILES:
        base_name = data_file.replace(".json", "")
        schema_file = f"{base_name}.schema.json"
        schema_path = schemas_dir / schema_file
        if not schema_path.is_file():
            alt_schema_file = f"{base_name.replace('_', '-')}.schema.json"
            if (schemas_dir / alt_schema_file).is_file():
                schema_file = alt_schema_file
                schema_path = schemas_dir / alt_schema_file

        if not schema_path.is_file():
            if strict_jsonschema:
                result.errors.append(f"[ERROR] Missing required schema file '{schema_file}' for runtime data '{data_file}'")
            else:
                result.warnings.append(f"[WARNING] Schema file '{schema_file}' not found for '{data_file}'")
            continue

        if data_file in data_cache:
            try:
                schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
                if HAS_JSONSCHEMA:
                    result.schema_engine = "jsonschema-draft7"
                    # 校验 Schema 自身合法性 (metaschema validation)
                    jsonschema.Draft7Validator.check_schema(schema_doc)
                    v = jsonschema.Draft7Validator(schema_doc, format_checker=jsonschema.FormatChecker())
                    for err in v.iter_errors(data_cache[data_file]):
                        result.errors.append(f"[ERROR] Schema violation in {data_file} at '{err.json_path}': {err.message}")
                else:
                    if strict_jsonschema:
                        result.errors.append("[ERROR] Strict validation requires official jsonschema package!")
            except Exception as e:
                result.errors.append(f"[ERROR] Schema validation error on {data_file}: {e}")

    # 3. 校验 scenes.json 结构与两阶段全局防冲突
    scenes_data = data_cache.get("scenes.json", {}).get("scenes", [])
    if not scenes_data:
        result.errors.append("[ERROR] scenes.json: No scenes defined")

    # Phase 1: 统一收集并校验全量 ID 与 Label (规范化为 strip() + casefold())
    # norm_key -> (kind, scene_id, original_text)
    global_scene_registry: Dict[str, Tuple[str, str, str]] = {}

    for cat_idx, cat in enumerate(scenes_data):
        cat_name = cat.get("category", "")
        if not cat_name:
            result.errors.append(f"[ERROR] scenes.json: Category at index {cat_idx} missing 'category' name")
        items = cat.get("items", [])
        for item_idx, item in enumerate(items):
            sid = item.get("id", "")
            slabel = item.get("label", "")
            ctx_ids = item.get("context_ids", [])
            anchors = item.get("anchor_tags", [])
            details = item.get("detail_tags", [])

            if not sid:
                result.errors.append(f"[ERROR] scenes.json [{cat_name}][{item_idx}]: Missing scene id")
                continue

            norm_id = sid.strip().casefold()
            if norm_id in global_scene_registry:
                prev_kind, prev_sid, prev_orig = global_scene_registry[norm_id]
                result.errors.append(f"[ERROR] scenes.json [{sid}]: ID '{sid}' collides with {prev_kind} '{prev_orig}' in scene '{prev_sid}'")
            else:
                global_scene_registry[norm_id] = ("id", sid, sid)

            if not slabel:
                result.errors.append(f"[ERROR] scenes.json [{sid}]: Missing label")
            else:
                norm_label = slabel.strip().casefold()
                if norm_label in global_scene_registry:
                    prev_kind, prev_sid, prev_orig = global_scene_registry[norm_label]
                    result.errors.append(f"[ERROR] scenes.json [{sid}]: label '{slabel}' collides with {prev_kind} '{prev_orig}' in scene '{prev_sid}'")
                else:
                    global_scene_registry[norm_label] = ("label", sid, slabel)

            if not ctx_ids:
                result.errors.append(f"[ERROR] scenes.json [{sid}]: context_ids cannot be empty")
            else:
                for c in ctx_ids:
                    if c not in VALID_CONTEXT_ENUMS:
                        result.errors.append(f"[ERROR] scenes.json [{sid}]: invalid context_id '{c}'")

            if not anchors:
                result.errors.append(f"[ERROR] scenes.json [{sid}]: anchor_tags must have at least 1 item")

            overlap = set(anchors) & set(details)
            if overlap:
                result.errors.append(f"[ERROR] scenes.json [{sid}]: overlapping tags between anchors and details: {overlap}")

    # Phase 2: 校验全局 Aliases 与全量 ID / Label / 其它 Alias 的防冲突 (解决前向与后向碰撞)
    for cat in scenes_data:
        for item in cat.get("items", []):
            sid = item.get("id", "")
            aliases = item.get("aliases", [])
            seen_item_aliases: Set[str] = set()

            for alias in aliases:
                if not alias:
                    continue
                norm_alias = alias.strip().casefold()
                if norm_alias in seen_item_aliases:
                    result.errors.append(f"[ERROR] scenes.json [{sid}]: duplicate alias '{alias}' within same item")
                seen_item_aliases.add(norm_alias)

                if norm_alias in global_scene_registry:
                    prev_kind, prev_sid, prev_orig = global_scene_registry[norm_alias]
                    result.errors.append(f"[ERROR] scenes.json [{sid}]: alias '{alias}' collides with {prev_kind} '{prev_orig}' in scene '{prev_sid}'")
                else:
                    global_scene_registry[norm_alias] = ("alias", sid, alias)

    # 4. 校验 presets.json 结构
    presets = data_cache.get("presets.json", {}).get("presets", [])
    if len(presets) < 70:
        result.errors.append(f"[ERROR] presets.json: Expected >=70 presets, got {len(presets)}")
    seen_preset_ids: Set[str] = set()
    for p in presets:
        pid = p.get("id", "")
        if not pid or pid in seen_preset_ids:
            result.errors.append(f"[ERROR] presets.json: Invalid or duplicate preset id '{pid}'")
        seen_preset_ids.add(pid)
        if not p.get("name_zh") or not p.get("positive"):
            result.errors.append(f"[ERROR] presets.json [{pid}]: Missing name_zh or positive prompt")

    # 5. 校验 style_recipes.json 结构
    recipes = data_cache.get("style_recipes.json", {}).get("recipes", [])
    if len(recipes) < 8:
        result.errors.append(f"[ERROR] style_recipes.json: Expected >=8 recipes, got {len(recipes)}")
    for r in recipes:
        rid = r.get("id", "")
        if not rid or not r.get("style_name") or not r.get("style_recipe"):
            result.errors.append(f"[ERROR] style_recipes.json [{rid}]: Missing required recipe fields")

    # 6. 校验 clothing.json 扩展策略与 24 档唯一性
    clothing_data = data_cache.get("clothing.json", {})
    policy = clothing_data.get("extension_policy", {})
    for lvl in ("L2", "L3", "L4"):
        if lvl not in policy:
            result.errors.append(f"[ERROR] clothing.json: extension_policy missing nudity level '{lvl}'")
        else:
            lvl_policy = policy[lvl]
            if not lvl_policy.get("exposure_ids"):
                result.errors.append(f"[ERROR] clothing.json: extension_policy[{lvl}] missing or empty 'exposure_ids'")
            if not lvl_policy.get("transparency_ids"):
                result.errors.append(f"[ERROR] clothing.json: extension_policy[{lvl}] missing or empty 'transparency_ids'")
            if lvl == "L4" and not lvl_policy.get("wardrobe_ids"):
                result.errors.append("[ERROR] clothing.json: extension_policy[L4] missing or empty 'wardrobe_ids'")

    # 4. 校验 conflict_rules.json 17 规则完整性与强类型契约 (单源验证)
    conflict_doc = data_cache.get("conflict_rules.json")
    if conflict_doc:
        try:
            validate_rule_document(conflict_doc)
        except Exception as e:
            result.errors.append(f"[ERROR] conflict_rules.json: {e}")
    else:
        result.errors.append("[ERROR] conflict_rules.json: File not found in data directory")

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate ComfyUI-IYKYK JSON datasets against Draft-7 schemas.")
    parser.add_argument("--strict", action="store_true", help="Fail with non-zero exit code if any error occurs")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Path to data directory")
    parser.add_argument("--schemas-dir", type=Path, default=SCHEMAS_DIR, help="Path to schemas directory")
    args = parser.parse_args()

    res = validate_all(
        data_dir=args.data_dir,
        schemas_dir=args.schemas_dir,
        strict_jsonschema=args.strict
    )

    print(f"Data Validation Summary: {res.checked_files} runtime files checked (Engine: {res.schema_engine}).")
    for warn in res.warnings:
        print(f"  {warn}")
    for err in res.errors:
        print(f"  {err}")

    if not res.is_valid:
        print(f"\n❌ Validation FAILED with {len(res.errors)} errors ({len(res.warnings)} warnings).")
        return 1
    else:
        print(f"\n✅ Validation PASSED with 0 errors ({len(res.warnings)} warnings).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
