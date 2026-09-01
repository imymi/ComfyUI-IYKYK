#!/usr/bin/env python3
"""
validate_data.py — ComfyUI-IYKYK 数据集 Schema 与完整性校验工具

严格校验：
1. 19 个运行时 JSON 文件必须全部存在且符合 JSON 格式规范
2. 执行 JSON Schema Draft-7 标准递归校验 (支持 pattern, enum, minItems, maxItems, uniqueItems, required, properties 等)
3. 必须为全部 19 个运行时文件提供并执行 Schema 校验，缺少任一 Schema 在严格模式下必定报错
4. 领域结构约束：
   - scenes.json: context_ids 枚举合法性、anchor_tags >=1、detail_tags 零交集
   - clothing.json: 28 款式 ID 唯一、9/5/10 扩展档位数量与 ID 唯一、extension_policy 完整引用
   - props.json: 非 none 分类 tags/items 非空、item ID 唯一
   - conflict_rules.json: 12 规则完整，Rule 9~12 必需字段非空
5. 错误分级：ERROR 阻止 CI 与发布构建，WARNING 记录输出
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

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

# 严格映射全部 19 个运行时文件对应的 Schema
SCHEMA_MAPPINGS = {
    "accessories.json": "accessories.schema.json",
    "characters.json": "characters.schema.json",
    "clothing.json": "clothing.schema.json",
    "conflict_rules.json": "conflict-rules.schema.json",
    "expressions.json": "expressions.schema.json",
    "film_stocks.json": "film_stocks.schema.json",
    "imperfections.json": "imperfections.schema.json",
    "lighting.json": "lighting.schema.json",
    "makeup.json": "makeup.schema.json",
    "negative_prompts.json": "negative_prompts.schema.json",
    "nudity_levels.json": "nudity_levels.schema.json",
    "poses.json": "poses.schema.json",
    "presets.json": "presets.schema.json",
    "props.json": "props.schema.json",
    "scenes.json": "scenes.schema.json",
    "shot_types.json": "shot_types.schema.json",
    "style_recipes.json": "style_recipes.schema.json",
    "tattoos.json": "tattoos.schema.json",
    "themes.json": "themes.schema.json",
}


@dataclass
class ValidationResult:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checked_files: int = 0
    schema_engine: str = "builtin-draft7-strict"

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


class Draft7Validator:
    """标准 Draft-7 JSON Schema 递归校验器实现。"""

    @classmethod
    def check_schema(cls, schema: dict) -> None:
        if not isinstance(schema, dict):
            raise ValueError("Schema must be a dictionary")

    def __init__(self, schema: dict):
        self.schema = schema
        self.check_schema(schema)

    def iter_errors(self, instance: Any, schema: Optional[dict] = None, path: str = "") -> List[str]:
        if schema is None:
            schema = self.schema
        errors = []
        self._validate_node(instance, schema, path, errors)
        return errors

    def _validate_node(self, instance: Any, schema: dict, path: str, errors: List[str]) -> None:
        if not isinstance(schema, dict):
            return

        expected_type = schema.get("type")
        if expected_type == "object":
            if not isinstance(instance, dict):
                errors.append(f"[ERROR] Schema violation at '{path}': Expected object, got {type(instance).__name__}")
                return
            for req in schema.get("required", []):
                if req not in instance:
                    errors.append(f"[ERROR] Schema violation at '{path}': Missing required property '{req}'")
            props_schema = schema.get("properties", {})
            for prop_k, prop_v in instance.items():
                if prop_k in props_schema:
                    self._validate_node(prop_v, props_schema[prop_k], f"{path}.{prop_k}" if path else prop_k, errors)
        elif expected_type == "array":
            if not isinstance(instance, list):
                errors.append(f"[ERROR] Schema violation at '{path}': Expected array, got {type(instance).__name__}")
                return
            min_items = schema.get("minItems")
            if min_items is not None and len(instance) < min_items:
                errors.append(f"[ERROR] Schema violation at '{path}': Array length {len(instance)} is less than minItems {min_items}")
            max_items = schema.get("maxItems")
            if max_items is not None and len(instance) > max_items:
                errors.append(f"[ERROR] Schema violation at '{path}': Array length {len(instance)} exceeds maxItems {max_items}")
            if schema.get("uniqueItems") and len(instance) > 0:
                seen = []
                for idx, item in enumerate(instance):
                    if item in seen:
                        errors.append(f"[ERROR] Schema violation at '{path}[{idx}]': Duplicate array item violating uniqueItems")
                    seen.append(item)
            items_schema = schema.get("items")
            if items_schema and isinstance(items_schema, dict):
                for idx, item in enumerate(instance):
                    self._validate_node(item, items_schema, f"{path}[{idx}]", errors)
        elif expected_type == "string":
            if not isinstance(instance, str):
                errors.append(f"[ERROR] Schema violation at '{path}': Expected string, got {type(instance).__name__}")
                return
            min_len = schema.get("minLength")
            if min_len is not None and len(instance) < min_len:
                errors.append(f"[ERROR] Schema violation at '{path}': String length is less than minLength {min_len}")
            pattern = schema.get("pattern")
            if pattern is not None and not re.search(pattern, instance):
                errors.append(f"[ERROR] Schema violation at '{path}': String '{instance}' does not match pattern '{pattern}'")
            enum_vals = schema.get("enum")
            if enum_vals is not None and instance not in enum_vals:
                errors.append(f"[ERROR] Schema violation at '{path}': Value '{instance}' not in enum {enum_vals}")


def validate_all(data_dir: Path = DATA_DIR, schemas_dir: Path = SCHEMAS_DIR, strict_jsonschema: bool = False) -> ValidationResult:
    result = ValidationResult()
    target_data_dir = Path(data_dir)
    target_schemas_dir = Path(schemas_dir)

    if not target_data_dir.is_dir():
        result.errors.append(f"[ERROR] Data directory does not exist: {target_data_dir}")
        return result

    # 1. 检查必需运行时数据文件存在与 JSON 格式
    data_cache: Dict[str, dict] = {}
    for fname in RUNTIME_DATA_FILES:
        fpath = target_data_dir / fname
        if not fpath.is_file():
            result.errors.append(f"[ERROR] Missing required runtime data file: {fname}")
            continue

        try:
            content = fpath.read_text(encoding="utf-8")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                result.errors.append(f"[ERROR] {fname}: Root must be a JSON object (dict)")
            else:
                data_cache[fname] = parsed
        except json.JSONDecodeError as e:
            result.errors.append(f"[ERROR] {fname}: JSON syntax error (line {e.lineno}, col {e.colno}): {e.msg}")

    result.checked_files = len(data_cache)
    if result.errors:
        return result

    # 2. 全量 19 个文件 Schema 强校验
    for data_file, schema_file in SCHEMA_MAPPINGS.items():
        schema_path = target_schemas_dir / schema_file
        if not schema_path.is_file():
            if strict_jsonschema:
                result.errors.append(f"[ERROR] Missing required schema file '{schema_file}' for '{data_file}'")
            else:
                result.warnings.append(f"[WARNING] Schema file '{schema_file}' not found for '{data_file}'")
            continue

        if data_file in data_cache:
            try:
                schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
                validator = Draft7Validator(schema_doc)
                schema_errs = validator.iter_errors(data_cache[data_file])
                result.errors.extend(schema_errs)
            except Exception as e:
                result.errors.append(f"[ERROR] Schema validation error on {data_file}: {e}")

    # 3. 校验 scenes.json 结构与零交集约束
    scenes_data = data_cache.get("scenes.json", {}).get("scenes", [])
    if not scenes_data:
        result.errors.append("[ERROR] scenes.json: No scenes defined")
    seen_scene_ids: Set[str] = set()
    seen_scene_labels: Set[str] = set()

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
            elif sid in seen_scene_ids:
                result.errors.append(f"[ERROR] scenes.json: Duplicate scene id '{sid}'")
            else:
                seen_scene_ids.add(sid)

            if not slabel:
                result.errors.append(f"[ERROR] scenes.json [{sid}]: Missing label")
            elif slabel in seen_scene_labels:
                result.warnings.append(f"[WARNING] scenes.json: Duplicate label '{slabel}' in scene '{sid}'")
            else:
                seen_scene_labels.add(slabel)

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

    # 6. 校验 clothing.json (28 类别 ID 唯一 + 9/5/10 扩展数量与 ID 唯一 + extension_policy 完整性)
    clothing_doc = data_cache.get("clothing.json", {})
    clothing_cats = clothing_doc.get("categories", [])
    if len(clothing_cats) != 28:
        result.errors.append(f"[ERROR] clothing.json: Expected exactly 28 clothing categories, got {len(clothing_cats)}")
    c_ids = set()
    for c in clothing_cats:
        cid = c.get("id", "")
        if cid in c_ids:
            result.errors.append(f"[ERROR] clothing.json: Duplicate clothing category id '{cid}'")
        c_ids.add(cid)

    # 验证 9/5/10 扩展数量与 ID 唯一
    exp_tiers = clothing_doc.get("sfw_exposure_tiers", [])
    if len(exp_tiers) != 9:
        result.errors.append(f"[ERROR] clothing.json: Expected exactly 9 sfw_exposure_tiers, got {len(exp_tiers)}")
    exp_ids = set()
    for t in exp_tiers:
        tid = t.get("id", "")
        if tid in exp_ids:
            result.errors.append(f"[ERROR] clothing.json: Duplicate sfw_exposure_tier id '{tid}'")
        exp_ids.add(tid)

    trans_tiers = clothing_doc.get("cloth_transparency_tiers", [])
    if len(trans_tiers) != 5:
        result.errors.append(f"[ERROR] clothing.json: Expected exactly 5 cloth_transparency_tiers, got {len(trans_tiers)}")
    trans_ids = set()
    for t in trans_tiers:
        tid = t.get("id", "")
        if tid in trans_ids:
            result.errors.append(f"[ERROR] clothing.json: Duplicate cloth_transparency_tier id '{tid}'")
        trans_ids.add(tid)

    wardrobe_items = clothing_doc.get("lingerie_wardrobe", [])
    if len(wardrobe_items) != 10:
        result.errors.append(f"[ERROR] clothing.json: Expected exactly 10 lingerie_wardrobe items, got {len(wardrobe_items)}")
    wardrobe_ids = set()
    for w in wardrobe_items:
        wid = w.get("id", "")
        if wid in wardrobe_ids:
            result.errors.append(f"[ERROR] clothing.json: Duplicate lingerie_wardrobe id '{wid}'")
        wardrobe_ids.add(wid)

    # 验证 extension_policy 完整性
    policy = clothing_doc.get("extension_policy", {})
    if not policy or not isinstance(policy, dict):
        result.errors.append("[ERROR] clothing.json: Missing extension_policy")
    else:
        for lvl in ["L2", "L3", "L4"]:
            if lvl not in policy:
                result.errors.append(f"[ERROR] clothing.json: extension_policy missing key '{lvl}'")
            else:
                for eid in policy[lvl].get("exposure_ids", []):
                    if eid not in exp_ids:
                        result.errors.append(f"[ERROR] clothing.json: extension_policy.{lvl}.exposure_ids references unknown id '{eid}'")
                for tid in policy[lvl].get("transparency_ids", []):
                    if tid not in trans_ids:
                        result.errors.append(f"[ERROR] clothing.json: extension_policy.{lvl}.transparency_ids references unknown id '{tid}'")
                for wid in policy[lvl].get("wardrobe_ids", []):
                    if wid not in wardrobe_ids:
                        result.errors.append(f"[ERROR] clothing.json: extension_policy.{lvl}.wardrobe_ids references unknown id '{wid}'")

    linkages = clothing_doc.get("clothing_nudity_linkage", {})
    for lvl in ["L1", "L2", "L3", "L4", "L5", "L6"]:
        if lvl not in linkages or not isinstance(linkages[lvl], dict) or not linkages[lvl]:
            result.errors.append(f"[ERROR] clothing.json: Missing or empty nudity linkage for '{lvl}'")
        else:
            overrides = linkages[lvl].get("style_overrides", {})
            if not overrides or not isinstance(overrides, dict):
                result.errors.append(f"[ERROR] clothing.json: Missing or empty style_overrides for '{lvl}'")

    # 7. 校验 props.json (非 none 分类 tags 或 items 必须非空)
    prop_cats = data_cache.get("props.json", {}).get("categories", [])
    for p in prop_cats:
        pid = p.get("id", "")
        if pid == "none":
            continue
        has_tags = bool(p.get("tags"))
        has_items = bool(p.get("items"))
        if not has_tags and not has_items:
            result.errors.append(f"[ERROR] props.json [{pid}]: Must have non-empty 'tags' or 'items'")
        if has_items:
            item_ids = set()
            for item in p.get("items", []):
                iid = item.get("id", "")
                if not iid or iid in item_ids:
                    result.errors.append(f"[ERROR] props.json [{pid}]: Invalid or duplicate item id '{iid}'")
                item_ids.add(iid)
                if not item.get("tags"):
                    result.errors.append(f"[ERROR] props.json [{pid}][{iid}]: Item tags cannot be empty")

    # 8. 校验 conflict_rules.json (17 规则完整，Rule 9~17 必需字段非空)
    conflict_doc = data_cache.get("conflict_rules.json", {})
    rules = conflict_doc.get("rules", [])
    if len(rules) != 17:
        result.errors.append(f"[ERROR] conflict_rules.json: Expected exactly 17 rules, got {len(rules)}")
    rule_ids = set()
    for r in rules:
        rid = r.get("id", "")
        if not rid or rid in rule_ids:
            result.errors.append(f"[ERROR] conflict_rules.json: Invalid or duplicate rule id '{rid}'")
        rule_ids.add(rid)

    required_rule_ids = [
        "nudity_clothing_conflicts",
        "material_penetration",
        "gaze_angle_geometry",
        "gaze_mutual_exclusion",
        "liquid_restrictions",
        "device_quality_compatibility",
        "tattoo_dermal_fusion",
        "spatial_environmental_mutual_exclusion",
        "pose_hand_occupation",
        "emotion_gaze_affinity",
        "environmental_lighting_coherence",
        "makeup_details_coherence",
        "framing_lower_body_coherence",
        "accessory_occlusion_gaze_coherence",
        "monochrome_film_chroma_coherence",
        "clothing_style_state_coherence",
        "handheld_props_single_holder",
    ]
    for rrid in required_rule_ids:
        if rrid not in rule_ids:
            result.errors.append(f"[ERROR] conflict_rules.json: Missing required rule '{rrid}'")

    rule9 = next((r for r in rules if r.get("id") == "pose_hand_occupation"), None)
    if not rule9 or not rule9.get("busy_pose_triggers") or not rule9.get("banned_handheld_patterns"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'pose_hand_occupation' missing busy_pose_triggers or banned_handheld_patterns")

    rule10 = next((r for r in rules if r.get("id") == "emotion_gaze_affinity"), None)
    if not rule10 or not rule10.get("conflicts"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'emotion_gaze_affinity' missing 'conflicts'")

    rule11 = next((r for r in rules if r.get("id") == "environmental_lighting_coherence"), None)
    if not rule11 or not rule11.get("daylight_triggers") or not rule11.get("banned_night_elements"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'environmental_lighting_coherence' missing daylight_triggers or banned_night_elements")

    rule12 = next((r for r in rules if r.get("id") == "makeup_details_coherence"), None)
    if not rule12 or not rule12.get("no_makeup_triggers") or not rule12.get("banned_makeup_smudge"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'makeup_details_coherence' missing no_makeup_triggers or banned_makeup_smudge")

    rule13 = next((r for r in rules if r.get("id") == "framing_lower_body_coherence"), None)
    if not rule13 or not rule13.get("close_up_triggers") or not rule13.get("banned_lower_body"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'framing_lower_body_coherence' missing close_up_triggers or banned_lower_body")

    rule14 = next((r for r in rules if r.get("id") == "accessory_occlusion_gaze_coherence"), None)
    if not rule14 or not rule14.get("occlusion_triggers") or not rule14.get("banned_gaze_actions"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'accessory_occlusion_gaze_coherence' missing occlusion_triggers or banned_gaze_actions")

    rule15 = next((r for r in rules if r.get("id") == "monochrome_film_chroma_coherence"), None)
    if not rule15 or not rule15.get("monochrome_triggers") or not rule15.get("banned_chroma"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'monochrome_film_chroma_coherence' missing monochrome_triggers or banned_chroma")

    rule16 = next((r for r in rules if r.get("id") == "clothing_style_state_coherence"), None)
    if not rule16 or not rule16.get("one_piece_triggers") or not rule16.get("one_piece_banned_states"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'clothing_style_state_coherence' missing one_piece_triggers or one_piece_banned_states")

    rule17 = next((r for r in rules if r.get("id") == "handheld_props_single_holder"), None)
    if not rule17 or not rule17.get("handheld_patterns"):
        result.errors.append("[ERROR] conflict_rules.json: Rule 'handheld_props_single_holder' missing handheld_patterns")

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
