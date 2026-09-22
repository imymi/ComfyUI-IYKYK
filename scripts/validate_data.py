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
import re
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

from lib.context_affinity import ContextAffinityRegistry
from lib.lexer import split_top_level_tags
from lib.models import CANONICAL_RECIPE_SELECTORS, SelectionOrigin, SemanticFacts
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
    id_regex = re.compile(r'^[a-z][a-z0-9_]{2,95}$')
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

            anchor_texts = {a.get("text", a) if isinstance(a, dict) else str(a) for a in anchors}
            detail_texts = {d.get("text", d) if isinstance(d, dict) else str(d) for d in details}
            overlap = anchor_texts & detail_texts
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

    # 4. 校验 presets.json 结构与逐 Tag 逐字节等价性
    preset_id_regex = re.compile(r'^[A-Za-z0-9_]{2,95}$')
    presets = data_cache.get("presets.json", {}).get("presets", [])
    if len(presets) < 70:
        result.errors.append(f"[ERROR] presets.json: Expected >=70 presets, got {len(presets)}")
    preset_catalog_registry: Dict[str, Tuple[str, str]] = {}
    for p_idx, p in enumerate(presets):
        pid = p.get("id", "")
        if not pid or not isinstance(pid, str) or not preset_id_regex.match(pid):
            result.errors.append(f"[ERROR] presets.json: Invalid preset id '{pid}'")
        elif pid in preset_catalog_registry:
            prev_kind, prev_path = preset_catalog_registry[pid]
            result.errors.append(f"[ERROR] presets.json at presets[{p_idx}]: duplicate ID '{pid}' collides with {prev_kind} at {prev_path}")
        else:
            preset_catalog_registry[pid] = ("preset", f"presets[{p_idx}]")

        if not p.get("name_zh") or not p.get("positive"):
            result.errors.append(f"[ERROR] presets.json [{pid}]: Missing name_zh or positive prompt")

        frags = p.get("fragments")
        if not frags or not isinstance(frags, list):
            result.errors.append(f"[ERROR] presets.json [{pid}]: Missing or invalid fragments")
            continue
        expected_tags = split_top_level_tags(p.get("positive", ""))
        actual_tags = [f.get("text", "") for f in frags]
        if expected_tags != actual_tags:
            result.errors.append(f"[ERROR] presets.json [{pid}]: fragments text mismatch with positive prompt: expected {expected_tags} vs actual {actual_tags}")
        for idx, f in enumerate(frags):
            actual_frag_keys = set(f.keys())
            expected_frag_keys = {"id", "slot", "text", "facts", "origin"}
            if actual_frag_keys != expected_frag_keys:
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: invalid fragment keys: {sorted(actual_frag_keys)}")
            fid = f.get("id")
            frag_path = f"presets[{p_idx}][{pid}].fragments[{idx}]"
            if not fid or not isinstance(fid, str) or not id_regex.match(fid):
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: invalid fragment ID '{fid}'")
            elif fid in preset_catalog_registry:
                prev_kind, prev_path = preset_catalog_registry[fid]
                result.errors.append(f"[ERROR] presets.json at {frag_path}: duplicate ID '{fid}' collides with {prev_kind} at {prev_path}")
            else:
                preset_catalog_registry[fid] = ("fragment", frag_path)

            if not isinstance(f.get("text"), str) or len(f.get("text", "").strip()) == 0:
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: fragment text must be non-empty str")
            if not isinstance(f.get("facts"), dict):
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: fragment facts must be dict")
            else:
                try:
                    sf = SemanticFacts.from_dict(f.get("facts"))
                    sf.validate()
                except Exception as err:
                    result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: invalid fragment facts: {err}")
            orig = f.get("origin", {})
            if not isinstance(orig, dict):
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: origin must be dict")
                continue
            try:
                SelectionOrigin.from_dict(orig)
            except Exception as err:
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: invalid fragment origin: {err}")
            if orig.get("entry_point") != "preset_browser":
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: invalid entry_point '{orig.get('entry_point')}'")
            if orig.get("mode") != "preset":
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: invalid mode '{orig.get('mode')}'")
            if orig.get("selected_id") != pid:
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: selected_id '{orig.get('selected_id')}' != '{pid}'")
            if orig.get("selector") != "preset_core":
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: origin selector must be 'preset_core', got '{orig.get('selector')}'")
            pids = orig.get("parent_ids", [])
            if pids != [pid] and pids != (pid,):
                result.errors.append(f"[ERROR] presets.json [{pid}] fragment[{idx}]: parent_ids must be exactly ['{pid}'], got {pids}")

    # 5. 校验 style_recipes.json 结构与逐字段逐字节等价性
    recipes = data_cache.get("style_recipes.json", {}).get("recipes", [])
    if len(recipes) < 8:
        result.errors.append(f"[ERROR] style_recipes.json: Expected >=8 recipes, got {len(recipes)}")
    recipe_catalog_registry: Dict[str, Tuple[str, str]] = {}
    for r_idx, r in enumerate(recipes):
        rid = r.get("id", "")
        if not rid or not isinstance(rid, str) or not id_regex.match(rid):
            result.errors.append(f"[ERROR] style_recipes.json: Invalid recipe id '{rid}'")
        elif rid in recipe_catalog_registry:
            prev_kind, prev_path = recipe_catalog_registry[rid]
            result.errors.append(f"[ERROR] style_recipes.json at recipes[{r_idx}]: duplicate ID '{rid}' collides with {prev_kind} at {prev_path}")
        else:
            recipe_catalog_registry[rid] = ("recipe", f"recipes[{r_idx}]")

        if not r.get("style_name") or not r.get("style_recipe"):
            result.errors.append(f"[ERROR] style_recipes.json [{rid}]: Missing required recipe fields")
        frags = r.get("fragments")
        if not frags or not isinstance(frags, list):
            result.errors.append(f"[ERROR] style_recipes.json [{rid}]: Missing or invalid fragments")
            continue

        # 逐字段等价性校验：配方文本字段与 fragments 逐 Tag 逐字节等价
        ignore_keys = {"id", "style_name", "name_zh", "description", "fragments"}
        for k, v in r.items():
            if k in ignore_keys or not isinstance(v, str):
                continue
            expected_field_tags = split_top_level_tags(v)
            actual_field_tags = [f.get("text", "") for f in frags if f.get("origin", {}).get("selector") == k]
            if expected_field_tags != actual_field_tags:
                result.errors.append(
                    f"[ERROR] style_recipes.json [{rid}]: field '{k}' text mismatch with fragments: "
                    f"expected {expected_field_tags} vs actual {actual_field_tags}"
                )

        # 校验 Fragment origin 闭环与强类型
        for idx, f in enumerate(frags):
            actual_frag_keys = set(f.keys())
            expected_frag_keys = {"id", "slot", "text", "facts", "origin"}
            if actual_frag_keys != expected_frag_keys:
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid fragment keys: {sorted(actual_frag_keys)}")
            fid = f.get("id")
            frag_path = f"recipes[{r_idx}][{rid}].fragments[{idx}]"
            if not fid or not isinstance(fid, str) or not id_regex.match(fid):
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid fragment ID '{fid}'")
            elif fid in recipe_catalog_registry:
                prev_kind, prev_path = recipe_catalog_registry[fid]
                result.errors.append(f"[ERROR] style_recipes.json at {frag_path}: duplicate ID '{fid}' collides with {prev_kind} at {prev_path}")
            else:
                recipe_catalog_registry[fid] = ("fragment", frag_path)
            if not isinstance(f.get("text"), str) or len(f.get("text", "").strip()) == 0:
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: fragment text must be non-empty str")
            if not isinstance(f.get("facts"), dict):
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: fragment facts must be dict")
            else:
                try:
                    sf = SemanticFacts.from_dict(f.get("facts"))
                    sf.validate()
                except Exception as err:
                    result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid fragment facts: {err}")
            orig = f.get("origin", {})
            if not isinstance(orig, dict):
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: origin must be dict")
                continue
            try:
                SelectionOrigin.from_dict(orig)
            except Exception as err:
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid fragment origin: {err}")
            if orig.get("entry_point") != "preset_browser":
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid entry_point '{orig.get('entry_point')}'")
            if orig.get("mode") != "recipe":
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid mode '{orig.get('mode')}'")
            if orig.get("selected_id") != rid:
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: selected_id '{orig.get('selected_id')}' != '{rid}'")
            if orig.get("selector") not in CANONICAL_RECIPE_SELECTORS:
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: invalid selector '{orig.get('selector')}', expected one of {sorted(CANONICAL_RECIPE_SELECTORS)}")
            pids = orig.get("parent_ids", [])
            if pids != [rid] and pids != (rid,):
                result.errors.append(f"[ERROR] style_recipes.json [{rid}] fragment[{idx}]: parent_ids must be exactly ['{rid}'], got {pids}")

    # 6. 校验 clothing.json 扩展策略与跨目录引用图闭环
    clothing_data = data_cache.get("clothing.json", {})
    policy = clothing_data.get("extension_policy", {})
    exp_ids = {t.get("id") for t in clothing_data.get("sfw_exposure_tiers", [])}
    trans_ids = {t.get("id") for t in clothing_data.get("cloth_transparency_tiers", [])}
    ward_ids = {t.get("id") for t in clothing_data.get("lingerie_wardrobe", [])}
    clothing_style_ids = {s.get("id") for s in clothing_data.get("categories", [])}

    for lvl in ("L2", "L3", "L4"):
        if lvl not in policy:
            result.errors.append(f"[ERROR] clothing.json: extension_policy missing nudity level '{lvl}'")
        else:
            lvl_policy = policy[lvl]
            eids = lvl_policy.get("exposure_ids", [])
            if not eids:
                result.errors.append(f"[ERROR] clothing.json: extension_policy[{lvl}] missing or empty 'exposure_ids'")
            for eid in eids:
                if eid not in exp_ids:
                    result.errors.append(f"[ERROR] clothing.json: extension_policy[{lvl}] dangling exposure_id '{eid}'")

            tids = lvl_policy.get("transparency_ids", [])
            if not tids:
                result.errors.append(f"[ERROR] clothing.json: extension_policy[{lvl}] missing or empty 'transparency_ids'")
            for tid in tids:
                if tid not in trans_ids:
                    result.errors.append(f"[ERROR] clothing.json: extension_policy[{lvl}] dangling transparency_id '{tid}'")

            if lvl == "L4":
                wids = lvl_policy.get("wardrobe_ids", [])
                if not wids:
                    result.errors.append("[ERROR] clothing.json: extension_policy[L4] missing or empty 'wardrobe_ids'")
                for wid in wids:
                    if wid not in ward_ids:
                        result.errors.append(f"[ERROR] clothing.json: extension_policy[L4] dangling wardrobe_id '{wid}'")

    for lvl, ldata in clothing_data.get("clothing_nudity_linkage", {}).items():
        for sid in ldata.get("style_overrides", {}).keys():
            if sid not in clothing_style_ids:
                result.errors.append(f"[ERROR] clothing.json: clothing_nudity_linkage[{lvl}] dangling style_override '{sid}'")

    # 7. 校验 themes.json ID 规范与跨文件 context_ids
    themes_data = data_cache.get("themes.json", {}).get("themes", [])
    if len(themes_data) < 39:
        result.errors.append(f"[ERROR] themes.json: Expected >=39 themes, got {len(themes_data)}")
    seen_theme_ids: Set[str] = set()
    for t in themes_data:
        tid = t.get("id", "")
        if not tid or not re.match(r'^[a-z][a-z0-9_]{2,95}$', tid):
            result.errors.append(f"[ERROR] themes.json: Invalid theme id '{tid}'")
        if tid in seen_theme_ids:
            result.errors.append(f"[ERROR] themes.json: Duplicate theme id '{tid}'")
        seen_theme_ids.add(tid)
        cids = t.get("context_ids", [])
        if not cids:
            result.errors.append(f"[ERROR] themes.json [{tid}]: context_ids cannot be empty")
        for c in cids:
            if c not in VALID_CONTEXT_ENUMS:
                result.errors.append(f"[ERROR] themes.json [{tid}]: invalid context_id '{c}'")

    # 8. 校验全量数据文件中的可采样叶子节点 (Fail-Closed: 拒绝字符串、拒绝缺失ID/text/facts、拒绝额外字段、强类型)
    id_regex = re.compile(r'^[a-z][a-z0-9_]{2,95}$')
    tag_containers = {"tags", "anchor_tags", "detail_tags", "general_tags", "erotic_tags"}
    known_facts_fields = set(SemanticFacts.__dataclass_fields__.keys())

    for fname, fcontent in data_cache.items():
        if fname in ("conflict_rules.json", "context_affinity.json", "negative_prompts.json", "presets.json", "style_recipes.json"):
            continue
        catalog_id_registry: Dict[str, Tuple[str, str]] = {}

        def _check_leaf_tag(item: Any, item_path: str):
            if not isinstance(item, dict):
                result.errors.append(
                    f"[ERROR] {fname} at {item_path}: expected object for leaf tag, got {type(item).__name__}"
                )
                return
            actual_keys = set(item.keys())
            required_keys = {"id", "text", "facts"}
            allowed_optional_keys = {"role", "mutex_group", "raw_lines", "derivation", "derivation_note"}
            if not required_keys.issubset(actual_keys) or not actual_keys.issubset(required_keys | allowed_optional_keys):
                result.errors.append(
                    f"[ERROR] {fname} at {item_path}: invalid leaf tag keys: {sorted(actual_keys)} (required {sorted(required_keys)}, allowed optional {sorted(allowed_optional_keys)})"
                )
                return
            lid = item["id"]
            if not isinstance(lid, str) or not id_regex.match(lid):
                result.errors.append(f"[ERROR] {fname} at {item_path}: invalid leaf ID {lid!r}")
            elif lid in catalog_id_registry:
                prev_kind, prev_path = catalog_id_registry[lid]
                if prev_kind == "leaf_tag":
                    result.errors.append(
                        f"[ERROR] {fname} at {item_path}: duplicate leaf ID '{lid}' collides with {prev_kind} at {prev_path}"
                    )
                else:
                    result.errors.append(
                        f"[ERROR] {fname} at {item_path}: duplicate ID '{lid}' (leaf_tag) collides with {prev_kind} at {prev_path}"
                    )
            else:
                catalog_id_registry[lid] = ("leaf_tag", item_path)

            txt = item["text"]
            if not isinstance(txt, str) or len(txt.strip()) == 0:
                result.errors.append(f"[ERROR] {fname} at {item_path}: leaf tag text must be non-empty str, got {txt!r}")

            fcts = item["facts"]
            if not isinstance(fcts, dict):
                result.errors.append(f"[ERROR] {fname} at {item_path}: facts must be dict, got {type(fcts).__name__}")
                return
            unexpected = set(fcts.keys()) - known_facts_fields
            if unexpected:
                result.errors.append(f"[ERROR] {fname} at {item_path}: unexpected facts fields {sorted(unexpected)}")
            if "hands_required" in fcts:
                hr = fcts["hands_required"]
                if not isinstance(hr, int) or isinstance(hr, bool) or hr not in (0, 1, 2):
                    result.errors.append(
                        f"[ERROR] {fname} at {item_path}: hands_required must be int in 0..2, got {hr!r}"
                    )
            try:
                sf = SemanticFacts.from_dict(fcts)
                sf.validate()
            except Exception as err:
                result.errors.append(f"[ERROR] {fname} at {item_path} ({lid}): invalid SemanticFacts: {err}")

        def _traverse_leaves(obj: Any, path: str = ""):
            if isinstance(obj, dict):
                # 校验选择器/条目 ID 文件内唯一性并注册到统一碰撞域
                if "id" in obj and isinstance(obj["id"], str) and not ("text" in obj and "facts" in obj):
                    sid = obj["id"]
                    if not id_regex.match(sid):
                        result.errors.append(f"[ERROR] {fname} at {path}: invalid selector/item ID '{sid}'")
                    elif sid in catalog_id_registry:
                        prev_kind, prev_path = catalog_id_registry[sid]
                        result.errors.append(
                            f"[ERROR] {fname} at {path}: duplicate ID '{sid}' (selector_or_item) collides with {prev_kind} at {prev_path}"
                        )
                    else:
                        catalog_id_registry[sid] = ("selector_or_item", path)

                # 校验同一选择器内 tags 的事实互斥性
                tags_list = None
                for k_t in tag_containers:
                    if k_t in obj and isinstance(obj[k_t], list):
                        tags_list = obj[k_t]
                        break
                if tags_list:
                    sid = obj.get("id", path)
                    all_cm: Set[str] = set()
                    all_lk: Set[str] = set()
                    for itm in tags_list:
                        if isinstance(itm, dict):
                            fcts = itm.get("facts", {})
                            for cm in fcts.get("color_modes", ()):
                                all_cm.add(cm)
                            lk = fcts.get("liquid_kind")
                            if lk and lk != "none":
                                all_lk.add(lk)
                    if "monochrome" in all_cm and any(c in ("color", "high_saturation") for c in all_cm):
                        result.errors.append(
                            f"[ERROR] {fname} [{sid}]: selector tags declare mutually contradictory color_modes: {sorted(all_cm)}"
                        )
                    if len(all_lk) > 1:
                        result.errors.append(
                            f"[ERROR] {fname} [{sid}]: selector tags declare mutually contradictory liquid_kinds: {sorted(all_lk)}"
                        )

                for k, v in obj.items():
                    curr_path = f"{path}.{k}" if path else k
                    if k in tag_containers and isinstance(v, list):
                        for idx, item in enumerate(v):
                            _check_leaf_tag(item, f"{curr_path}[{idx}]")
                    elif k == "attributes" and isinstance(v, dict):
                        for attr_cat, attr_tags in v.items():
                            if isinstance(attr_tags, list):
                                for idx, item in enumerate(attr_tags):
                                    _check_leaf_tag(item, f"{curr_path}.{attr_cat}[{idx}]")
                            else:
                                _traverse_leaves(attr_tags, f"{curr_path}.{attr_cat}")
                    else:
                        _traverse_leaves(v, curr_path)
            elif isinstance(obj, list):
                for i, elem in enumerate(obj):
                    _traverse_leaves(elem, f"{path}[{i}]")

        _traverse_leaves(fcontent)

    # 9. 校验 conflict_rules.json 17 规则完整性与强类型契约 (单源验证)
    conflict_doc = data_cache.get("conflict_rules.json")
    if conflict_doc:
        try:
            validate_rule_document(conflict_doc)
        except Exception as e:
            result.errors.append(f"[ERROR] conflict_rules.json: {e}")
    else:
        result.errors.append("[ERROR] conflict_rules.json: File not found in data directory")

    # 10. 校验 context_affinity.json 196 单元与跨目录选择器引用 (自主 Fail-Closed 校验)
    if "context_affinity.json" in data_cache:
        try:
            ContextAffinityRegistry(data_dir)
        except Exception as e:
            result.errors.append(f"[ERROR] context_affinity.json: {e}")
    else:
        result.errors.append("[ERROR] context_affinity.json: File not found in data directory")

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
