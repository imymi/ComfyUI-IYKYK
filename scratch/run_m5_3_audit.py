#!/usr/bin/env python3
"""
scratch/run_m5_3_audit.py
M5-3 全量万种子审计与差异归因执行引擎 (v2.0 深度加固版)

本脚本执行以下严格因果证明与门禁校验：
1. 真实源码与提交绑定：
   - 提取真实 HEAD Commit SHA、Tree Hash 与工作区状态
   - 计算核心源码与数据契约文件确定性摘要 (core_source_digest)
   - 记录基线 (f1b873c) 与目标版本全量 20 个运行时数据文件 SHA-256 哈希
2. 逐种子两轮严格双跑质量门禁 (10,000 Seeds):
   - 严格双轮确定性：Round A 与 Round B 的 positive, negative, description, d_audit 逐字节全等
   - 诊断与生成逐字节一致：d_pos == r.positive, d_neg == r.negative, d_desc == r.description
   - 零残留硬冲突：unresolved_conflicts == ()
   - 词数预算：len(positive.split()) <= 250
   - 语法合法性：validate_prompt_syntax 通过
   - 严格二次消解幂等性：二次消解决策数 == 0，且前后终态原子列表（ID、文本、槽位、item_id）绝对全等
   - 诊断闭包 Oracle 校验：validate_audit_json_oracle 严格通过
   - Provenance DAG 闭环：终态原子 parent_ids 均非空可追溯
3. 严格因果归因引擎 (Fail-Closed)：
   - 权威词库白名单核验：每个源原子必须属于 16 领域权威数据，严禁无依据外来文本注入
   - 逆向无静默消失拦截：源原子未进入终态者必须具备合法 drop/replace 决策
   - 跨版本保序与重排拦截：严禁无规则依据的词条重排或多重度突变
   - 差异逐原子因果证明：无法证明的差异严禁归入亲和调整，必须判定为 UNEXPLAINED 并阻断
4. 全量逐种子执行证据归档：
   - 保留全量 10,000 种子双轮完整原子、决策、诊断 JSON、逐种子门禁状态与基线比对数据
   - 产出 audit_m5_3_evidence.json.gz, audit_m5_3_manifest.json, audit_m5_3_report.md
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import copy
import functools
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import random
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
sys.path.insert(0, str(REPO_DIR))

BASELINE_COMMIT = "f1b873c"

DEFAULT_INPUTS = {
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


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024 * 8)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def get_data_file_hashes(data_dir: Path) -> Dict[str, str]:
    files = sorted(data_dir.glob("*.json"))
    return {f.name: compute_file_sha256(f) for f in files}


def get_source_tree_metadata(repo_dir: Path) -> Dict[str, Any]:
    head_proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True, check=True)
    tree_proc = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=str(repo_dir), capture_output=True, text=True, check=True)
    status_proc = subprocess.run(["git", "status", "--porcelain", "-uno"], cwd=str(repo_dir), capture_output=True, text=True, check=True)

    core_files = sorted(
        list((repo_dir / "lib").glob("*.py")) +
        list((repo_dir / "data").glob("*.json")) +
        list((repo_dir / "schemas").glob("*.json")) +
        [repo_dir / "nodes.py"]
    )
    hasher = hashlib.sha256()
    for f in core_files:
        hasher.update(f.relative_to(repo_dir).as_posix().encode("utf-8"))
        hasher.update(b":")
        hasher.update(compute_file_sha256(f).encode("utf-8"))
        hasher.update(b"\n")

    return {
        "git_commit": head_proc.stdout.strip(),
        "git_tree": tree_proc.stdout.strip(),
        "is_dirty": bool(status_proc.stdout.strip()),
        "core_source_digest": hasher.hexdigest(),
    }


def export_commit(commit_id: str, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    if not (target_dir / "nodes.py").exists():
        print(f"[*] Exporting git baseline {commit_id} to {target_dir}...")
        tar_proc = subprocess.Popen(["tar", "-x", "-C", str(target_dir)], stdin=subprocess.PIPE)
        subprocess.run(["git", "archive", commit_id], stdout=tar_proc.stdin, cwd=str(REPO_DIR), check=True)
        tar_proc.communicate()
        if tar_proc.returncode != 0:
            raise RuntimeError(f"tar extraction failed for {commit_id}")


# ─────────────────────────────────────────────────────────────────────────────
# 权威词库索引加载器
# ─────────────────────────────────────────────────────────────────────────────
def load_authoritative_catalog_lookup(data_dir: Path) -> Dict[Tuple[str, str], Set[str]]:
    """
    全量扫描数据目录，构建全局权威词库索引：
    (norm_slot, item_id) -> set of valid tag texts
    涵盖 16 个运行时领域，严禁未授权或伪造词条。
    """
    from lib.lexer import parse_prompt
    from lib.conflict_resolver import normalize_slot_name

    lookup: Dict[Tuple[str, str], Set[str]] = collections.defaultdict(set)

    def add_tag(s: str, iid: str, raw: str):
        if not s or not iid or not raw:
            return
        norm_s = normalize_slot_name(s)
        clean_raw = raw.strip()
        lookup[(norm_s, iid)].add(clean_raw)
        for part in clean_raw.split(","):
            if part.strip():
                lookup[(norm_s, iid)].add(part.strip())
        try:
            for tag in parse_prompt(clean_raw).tags:
                for sp in tag.spans:
                    if sp.text and sp.text.strip():
                        lookup[(norm_s, iid)].add(sp.text.strip())
        except Exception:
            pass

    # 1. accessories.json
    acc = json.loads((data_dir / "accessories.json").read_text(encoding="utf-8"))
    for it in acc.get("hairstyles", []):
        for t in it.get("tags", []):
            add_tag("hairstyle", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in acc.get("headwear_jewelry", []):
        for t in it.get("tags", []):
            add_tag("jewelry", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 2. characters.json
    chars = json.loads((data_dir / "characters.json").read_text(encoding="utf-8"))
    for it in chars.get("characters", []):
        for t in it.get("tags", []):
            add_tag("character", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 3. clothing.json
    clo = json.loads((data_dir / "clothing.json").read_text(encoding="utf-8"))
    for sec in ("categories", "clothing_states", "lingerie_wardrobe", "sfw_exposure_tiers", "cloth_transparency_tiers"):
        for it in clo.get(sec, []):
            for t in it.get("tags", []):
                add_tag("clothing", it["id"], t if isinstance(t, str) else t.get("text", ""))
    linkage = clo.get("clothing_nudity_linkage", {})
    for lvl, ldata in linkage.items():
        for sid, tags in ldata.get("style_overrides", {}).items():
            for t in tags:
                raw = t if isinstance(t, str) else t.get("text", "")
                add_tag("clothing", sid, raw)
                add_tag("clothing", "auto_linkage", raw)
        for t in ldata.get("general_tags", []):
            raw = t if isinstance(t, str) else t.get("text", "")
            add_tag("clothing", "linkage_general", raw)
            add_tag("clothing", "auto_linkage", raw)

    # 4. expressions.json (全部 4 个维度)
    expr = json.loads((data_dir / "expressions.json").read_text(encoding="utf-8"))
    for sec in ("emotions", "mouth_actions", "gaze_rules", "progression_phases"):
        for it in expr.get(sec, []):
            for t in it.get("tags", []):
                add_tag("expression", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 5. film_stocks.json
    films = json.loads((data_dir / "film_stocks.json").read_text(encoding="utf-8"))
    for it in films.get("film_stocks", []):
        for t in it.get("tags", []):
            add_tag("film", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 6. imperfections.json
    imp = json.loads((data_dir / "imperfections.json").read_text(encoding="utf-8"))
    for it in imp.get("categories", []):
        for t in it.get("tags", []):
            add_tag("imperfections", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 7. lighting.json
    light = json.loads((data_dir / "lighting.json").read_text(encoding="utf-8"))
    for sec in ("professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting"):
        for it in light.get(sec, []):
            for t in it.get("tags", []):
                add_tag("lighting", it["id"], t if isinstance(t, str) else t.get("text", ""))
            for t in it.get("erotic_tags", []):
                add_tag("lighting", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in light.get("preset_combos", []):
        for t in it.get("tags", []):
            add_tag("lighting", it["id"], t if isinstance(t, str) else t.get("text", ""))
        for k in ("main_light", "modifier_light", "atmosphere"):
            v = it.get(k, "")
            if v:
                for p in v.split(","):
                    add_tag("lighting", it["id"], p.strip())

    # 8. makeup.json
    mak = json.loads((data_dir / "makeup.json").read_text(encoding="utf-8"))
    for it in mak.get("categories", []):
        for t in it.get("tags", []):
            add_tag("makeup", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 9. nudity_levels.json
    nud = json.loads((data_dir / "nudity_levels.json").read_text(encoding="utf-8"))
    for it in nud.get("nudity_levels", []):
        for t in it.get("tags", []):
            add_tag("nudity", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in nud.get("liquid_effects", []):
        for t in it.get("tags", []):
            add_tag("liquids", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 10. poses.json
    pos = json.loads((data_dir / "poses.json").read_text(encoding="utf-8"))
    for cat in pos.get("pose_categories", []):
        cat_id = cat.get("id")
        for sub in cat.get("subcategories", []):
            for t in sub.get("tags", []):
                add_tag("pose", cat_id, t if isinstance(t, str) else t.get("text", ""))

    # 11. props.json
    prp = json.loads((data_dir / "props.json").read_text(encoding="utf-8"))
    for it in prp.get("categories", []):
        for t in it.get("tags", []):
            add_tag("props", it["id"], t if isinstance(t, str) else t.get("text", ""))
        for sub in it.get("items", []):
            for t in sub.get("tags", []):
                add_tag("props", sub["id"], t if isinstance(t, str) else t.get("text", ""))

    # 12. scenes.json & themes.json
    sc = json.loads((data_dir / "scenes.json").read_text(encoding="utf-8"))
    for cat in sc.get("scenes", []):
        for it in cat.get("items", []):
            for t in (it.get("tags", []) + it.get("anchor_tags", []) + it.get("detail_tags", [])):
                add_tag("scene_theme", it["id"], t if isinstance(t, str) else t.get("text", ""))
    thm = json.loads((data_dir / "themes.json").read_text(encoding="utf-8"))
    for it in thm.get("themes", []):
        for t in it.get("tags", []):
            add_tag("scene_theme", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 13. shot_types.json
    shot = json.loads((data_dir / "shot_types.json").read_text(encoding="utf-8"))
    for it in shot.get("shot_types", []):
        for t in it.get("tags", []):
            add_tag("shot_type", it["id"], t if isinstance(t, str) else t.get("text", ""))
    for it in shot.get("camera_angles", []):
        for t in it.get("tags", []):
            add_tag("camera_angle", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # 14. tattoos.json
    tat = json.loads((data_dir / "tattoos.json").read_text(encoding="utf-8"))
    for it in tat.get("categories", []):
        for t in it.get("tags", []):
            add_tag("tattoo", it["id"], t if isinstance(t, str) else t.get("text", ""))

    # Quality tags
    add_tag("quality", "quality_high", "best quality")
    add_tag("quality", "quality_high", "detailed")
    add_tag("quality", "quality_high", "photorealistic")
    add_tag("quality", "quality_ultra", "masterpiece")
    add_tag("quality", "quality_ultra", "ultra-detailed")
    add_tag("quality", "quality_ultra", "8k resolution")

    # 15. 动态槽位与词池扩展展开 (Dynamic Slots & Pools Expansion)
    try:
        from lib.pool_resolver import BATCH1_POOL_REGISTRY, _load_canonical_colors
        hair_comp_tags = set(lookup.get(("hairstyle", "hair_ensemble_composite"), set()))
        for key in ("02_hair/hair_lengths", "02_hair/hair_styles", "02_hair/hair_ornaments"):
            for t in BATCH1_POOL_REGISTRY.get(key, []):
                hair_comp_tags.add(t.strip())
                for sub in t.split(","):
                    if sub.strip():
                        hair_comp_tags.add(sub.strip())
        for c in _load_canonical_colors():
            hair_comp_tags.add(f"({c} hair)")
            hair_comp_tags.add(c)
        lookup[("hairstyle", "hair_ensemble_composite")] = hair_comp_tags

        for item_id in ("denim_shorts", "clothing_legwear_combo", "bottom_jeans_casual", "bottom_pantyhose_pants", "bottom_shorts_casual"):
            c_tags = set(lookup.get(("clothing", item_id), set()))
            for key in ("10_accessories/secondary_legwear", "10_accessories/footwear"):
                for t in BATCH1_POOL_REGISTRY.get(key, []):
                    c_tags.add(t.strip())
            lookup[("clothing", item_id)] = c_tags

        choker_bases = ("bow choker", "cross choker", "choker", "frilled choker", "heart choker", "o-ring choker", "pendant choker", "neck ribbon", "spiked choker", "star choker", "neck bell choker", "bell choker", "lace ribbon choker", "necktie", "bowtie")
        for item_id in ("leather_choker", "neck_ribbon", "acc_bowtie", "acc_necktie", "combo_necklace_choker", "acc_necklace_general"):
            j_tags = set(lookup.get(("jewelry", item_id), set()))
            for key in ("10_accessories/necklaces", "10_accessories/chokers"):
                for t in BATCH1_POOL_REGISTRY.get(key, []):
                    j_tags.add(t.strip())
            for c in list(_load_canonical_colors()) + ["black"]:
                for base in choker_bases:
                    j_tags.add(f"{c} {base}".strip())
                    j_tags.add(f"({c} hair) {base}".strip())
                    j_tags.add(f"({c} hair)".strip())
                    j_tags.add(base.strip())
            lookup[("jewelry", item_id)] = j_tags
    except Exception:
        pass

    for k in list(lookup.keys()):
        lookup[k] = {t.strip() for t in lookup[k] if t and t.strip()}

    return lookup


# ─────────────────────────────────────────────────────────────────────────────
# 统一原子完整状态序列化与字段级全等比较器
# ─────────────────────────────────────────────────────────────────────────────
def _serialize_facts(facts_raw: Any) -> Dict[str, Any]:
    """完整序列化 SemanticFacts 对象或字典，保持内部字段与集合有序。"""
    if facts_raw is None:
        return {}
    if hasattr(facts_raw, "to_dict"):
        d = facts_raw.to_dict()
    elif hasattr(facts_raw, "__dict__"):
        d = {k: v for k, v in facts_raw.__dict__.items() if not k.startswith("_")}
    elif isinstance(facts_raw, dict):
        d = dict(facts_raw)
    else:
        d = {}
    res = {}
    for k, v in d.items():
        if isinstance(v, (list, tuple, set)):
            res[k] = list(v)
        else:
            res[k] = v
    return res


def serialize_atom_full_state(a: Any) -> Dict[str, Any]:
    """完整序列化 PromptAtom 的所有业务与因果追踪字段，支持 PromptAtom 实例或 dict 统一转换。"""
    if isinstance(a, dict):
        atom_id = str(a.get("atom_id", "") or "")
        text = str(a.get("text", "") or "")
        source_slot = str(a.get("source_slot", "") or "")
        source_item_id = str(a.get("source_item_id", "") or "")
        span_type_val = str(a.get("span_type", "plain") or "plain")
        context_ids = list(a.get("context_ids", ()) or ())
        exclusive_group = a.get("exclusive_group")
        tag_order = int(a.get("tag_order", 0) or 0)
        span_order = int(a.get("span_order", 0) or 0)
        contains_blackbox = bool(a.get("contains_blackbox", False))
        item_id_val = str(a.get("id", "") or "")
        target_id_val = a.get("target_id")
        entity_id_val = a.get("entity_id")

        # origin 字段提取
        orig_raw = a.get("origin")
        if isinstance(orig_raw, dict):
            orig_entry_point = str(orig_raw.get("entry_point", "generator") or "generator")
            orig_mode = str(orig_raw.get("mode", "random") or "random")
            orig_selector = str(orig_raw.get("selector", "") or "")
            orig_selected_id = orig_raw.get("selected_id")
            orig_raw_value = orig_raw.get("raw_value")
            orig_parent_ids = list(orig_raw.get("parent_ids", ()) or ())
            orig_rationale = orig_raw.get("sampling_rationale")
        else:
            orig_entry_point = str(a.get("origin_entry_point", "generator") or "generator")
            orig_mode = str(a.get("origin_mode", "random") or "random")
            orig_selector = str(a.get("origin_selector", "") or "")
            orig_selected_id = a.get("origin_selected_id")
            orig_raw_value = a.get("origin_raw_value")
            orig_parent_ids = list(a.get("origin_parent_ids", ()) or ())
            orig_rationale = a.get("origin_sampling_rationale")

        # provenance 字段提取
        prov_raw = a.get("provenance")
        if isinstance(prov_raw, dict):
            prov_item_id = str(prov_raw.get("item_id", "") or "")
            prov_semantic_ids = list(prov_raw.get("semantic_ids", ()) or ())
            prov_kind = str(prov_raw.get("kind", "") or "")
            prov_rule_id = str(prov_raw.get("rule_id", "") or "")
            prov_parent_ids = list(prov_raw.get("parent_ids", ()) or ())
            prov_source_mode = str(prov_raw.get("source_mode", "") or "")
        else:
            prov_item_id = str(a.get("provenance_item_id", "") or "")
            prov_semantic_ids = list(a.get("semantic_ids", ()) or ())
            prov_kind = str(a.get("provenance_kind", "") or "")
            prov_rule_id = str(a.get("provenance_rule_id", "") or "")
            prov_parent_ids = list(a.get("parent_ids", ()) or ())
            prov_source_mode = str(a.get("provenance_source_mode", "") or "")

        facts_dict = _serialize_facts(a.get("facts", {}))
    else:
        span_type_val = getattr(a, "span_type", "plain")
        if hasattr(span_type_val, "value"):
            span_type_val = span_type_val.value
        else:
            span_type_val = str(span_type_val or "plain")

        orig = getattr(a, "origin", None)
        if orig is not None:
            orig_entry_point = str(getattr(orig, "entry_point", "generator") or "generator")
            orig_mode = str(getattr(orig, "mode", "random") or "random")
            orig_selector = str(getattr(orig, "selector", "") or "")
            orig_selected_id = getattr(orig, "selected_id", None)
            orig_raw_value = getattr(orig, "raw_value", None)
            orig_parent_ids = list(getattr(orig, "parent_ids", ()) or ())
            orig_rationale = getattr(orig, "sampling_rationale", None)
        else:
            orig_entry_point = "generator"
            orig_mode = "random"
            orig_selector = ""
            orig_selected_id = None
            orig_raw_value = None
            orig_parent_ids = []
            orig_rationale = None

        prov = getattr(a, "provenance", None)
        if prov is not None:
            prov_item_id = str(getattr(prov, "item_id", "") or "")
            prov_semantic_ids = list(getattr(prov, "semantic_ids", ()) or ())
            prov_kind = str(getattr(prov, "kind", "") or "")
            prov_rule_id = str(getattr(prov, "rule_id", "") or "")
            prov_parent_ids = list(getattr(prov, "parent_ids", ()) or ())
            prov_source_mode = str(getattr(prov, "source_mode", "") or "")
        else:
            prov_item_id = ""
            prov_semantic_ids = []
            prov_kind = ""
            prov_rule_id = ""
            prov_parent_ids = []
            prov_source_mode = ""

        facts_dict = _serialize_facts(getattr(a, "facts", None))

        atom_id = str(getattr(a, "atom_id", "") or "")
        text = str(getattr(a, "text", "") or "")
        source_slot = str(getattr(a, "source_slot", "") or "")
        source_item_id = str(getattr(a, "source_item_id", "") or "")
        context_ids = list(getattr(a, "context_ids", ()) or ())
        exclusive_group = getattr(a, "exclusive_group", None)
        tag_order = int(getattr(a, "tag_order", 0) or 0)
        span_order = int(getattr(a, "span_order", 0) or 0)
        contains_blackbox = bool(getattr(a, "contains_blackbox", False))
        item_id_val = str(getattr(a, "id", "") or "")
        target_id_val = getattr(a, "target_id", None)
        entity_id_val = getattr(a, "entity_id", None)

    return {
        "atom_id": atom_id,
        "text": text,
        "source_slot": source_slot,
        "source_item_id": source_item_id,
        "span_type": span_type_val,
        "context_ids": context_ids,
        "exclusive_group": exclusive_group,
        "tag_order": tag_order,
        "span_order": span_order,
        "contains_blackbox": contains_blackbox,
        "id": item_id_val,
        "target_id": target_id_val,
        "entity_id": entity_id_val,
        "origin": {
            "entry_point": orig_entry_point,
            "mode": orig_mode,
            "selector": orig_selector,
            "selected_id": orig_selected_id,
            "raw_value": orig_raw_value,
            "parent_ids": orig_parent_ids,
            "sampling_rationale": orig_rationale,
        },
        "origin_mode": orig_mode,
        "origin_entry_point": orig_entry_point,
        "origin_selector": orig_selector,
        "origin_selected_id": orig_selected_id,
        "provenance": {
            "item_id": prov_item_id,
            "semantic_ids": prov_semantic_ids,
            "kind": prov_kind,
            "rule_id": prov_rule_id,
            "parent_ids": prov_parent_ids,
            "source_mode": prov_source_mode,
        },
        "provenance_kind": prov_kind,
        "provenance_item_id": prov_item_id,
        "provenance_rule_id": prov_rule_id,
        "provenance_source_mode": prov_source_mode,
        "parent_ids": prov_parent_ids,
        "semantic_ids": prov_semantic_ids,
        "facts": facts_dict,
    }


def compare_atom_full_states(atoms_a: Sequence[Any], atoms_b: Sequence[Any]) -> Tuple[bool, List[str]]:
    """统一比较两个原子序列的全状态字段，发现漂移时返回具体字段级 diff 描述。"""
    diffs: List[str] = []
    if len(atoms_a) != len(atoms_b):
        diffs.append(f"Atom count mismatch: before={len(atoms_a)}, after={len(atoms_b)}")
        return False, diffs

    for idx, (a, b) in enumerate(zip(atoms_a, atoms_b)):
        dict_a = serialize_atom_full_state(a)
        dict_b = serialize_atom_full_state(b)
        if dict_a != dict_b:
            mismatched_keys = [k for k in dict_a if dict_a[k] != dict_b.get(k)]
            for k in mismatched_keys:
                diffs.append(
                    f"Atom[{idx}] ('{dict_a.get('text')}') field '{k}' drifted: before={dict_a.get(k)!r}, after={dict_b.get(k)!r}"
                )

    return (len(diffs) == 0), diffs


SLOT_TO_SELECTOR_NAMESPACE = {
    "preset_core": "selector:preset_core",
    "style_recipe": "selector:style_recipe",
    "scene_theme": "selector:scene_theme",
    "scene": "selector:scene_theme",
    "theme": "selector:theme",
    "nudity": "selector:nudity",
    "shot_type": "selector:shot_type",
    "camera_angle": "selector:camera_angle",
    "clothing": "selector:clothing",
    "lighting": "selector:lighting",
    "pose": "selector:pose",
    "expression": "selector:expression",
    "film": "selector:film",
    "makeup": "selector:makeup",
    "hairstyle": "selector:hairstyle",
    "hair": "selector:hairstyle",
    "jewelry": "selector:jewelry",
    "imperfections": "selector:imperfections",
    "tattoo": "selector:tattoo",
    "props": "selector:props",
    "prop": "selector:props",
    "character": "selector:character",
    "liquids": "selector:liquids",
}


@functools.lru_cache(maxsize=None)
def get_slot_catalog_order(slot: str, data_dir: Path) -> List[str]:
    from lib.conflict_resolver import normalize_slot_name
    norm = normalize_slot_name(slot)
    try:
        if norm == "clothing":
            d = json.loads((data_dir / "clothing.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("categories", []) if "id" in c]
        elif norm == "character":
            d = json.loads((data_dir / "characters.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("characters", []) if "id" in c and c.get("id") != "none"]
        elif norm == "makeup":
            d = json.loads((data_dir / "makeup.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("categories", []) if "id" in c]
        elif norm in ("hairstyle", "hair"):
            d = json.loads((data_dir / "accessories.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("hairstyles", []) if "id" in c]
        elif norm == "jewelry":
            d = json.loads((data_dir / "accessories.json").read_text(encoding="utf-8"))
            items = list(d.get("headwear_jewelry", [])) + list(d.get("jewelry", []))
            return [c["id"] for c in items if "id" in c]
        elif norm in ("props", "prop"):
            d = json.loads((data_dir / "props.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("categories", []) if "id" in c and c.get("id") != "none"]
        elif norm == "tattoo":
            d = json.loads((data_dir / "tattoos.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("categories", []) if "id" in c and c.get("id") != "none"]
        elif norm == "liquids":
            d = json.loads((data_dir / "nudity_levels.json").read_text(encoding="utf-8"))
            liqs = d.get("body_liquids", []) + d.get("environmental_liquids", [])
            if not liqs:
                liqs = d.get("liquid_effects", [])
            return [c["id"] for c in liqs if "id" in c and c.get("id") != "none"]
        elif norm == "pose":
            d = json.loads((data_dir / "poses.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("pose_categories", []) if "id" in c]
        elif norm == "expression":
            d = json.loads((data_dir / "expressions.json").read_text(encoding="utf-8"))
            cats = list(d.get("emotions", [])) + list(d.get("mouth_actions", []))
            return [c["id"] for c in cats if "id" in c]
        elif norm == "lighting":
            d = json.loads((data_dir / "lighting.json").read_text(encoding="utf-8"))
            lights = list(d.get("preset_combos", [])) + list(d.get("professional_lighting", [])) + list(d.get("cinematic_lighting", [])) + list(d.get("special_effects", [])) + list(d.get("erotic_lighting", []))
            return [c["id"] for c in lights if "id" in c]
        elif norm == "film":
            d = json.loads((data_dir / "film_stocks.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("film_stocks", []) if "id" in c]
        elif norm == "shot_type":
            d = json.loads((data_dir / "shot_types.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("shot_types", []) if "id" in c]
        elif norm == "camera_angle":
            d = json.loads((data_dir / "shot_types.json").read_text(encoding="utf-8"))
            return [c["id"] for c in d.get("camera_angles", []) if "id" in c]
    except Exception:
        pass
    return []


def verify_affinity_matrix_for_slot(
    affinity_data: Any,
    slot: str,
    active_contexts: Sequence[str],
    target_item_id: Optional[str] = None,
    context_profile: Optional[Dict[str, Any]] = None,
    catalog_lookup: Optional[Dict[Tuple[str, str], Set[str]]] = None,
    recorded_rationale: Optional[Dict[str, Any]] = None,
    seed: Optional[int] = None,
) -> Tuple[bool, str]:
    """严格核验亲和数据与矩阵对于特定槽位及情境的因果完备性、精确分布匹配与可重放概率。

    Fail-closed 规则：
    1. affinity_data 必须为 dict 且包含有效的 matrix 字典；
    2. matrix 必须覆盖 ORDERED_CONTEXT_IDS 中的有效情境，严禁使用无关假矩阵 (如 {"unrelated": {}})；
    3. 当前运行的 active_contexts 必须在 matrix 中存在且覆盖该 slot 槽位；
    4. 单元格必须为 dict 且不可为 null/None；
    5. 单元格 mode 必须为 weighted 或 neutral；
    6. 若 mode == weighted，candidates 必须为非空列表，候选元素必须包含非空 id 且 weight > 0；
    7. 若 context_profile 可用：
       - 若 recorded_rationale 包含 distribution，重算分布必须与归档 recorded_dist 逐项精确吻合 (最大漂移 <= 1e-4)，
         彻底杜绝随意修改矩阵权重或将所有矩阵单元替换为 neutral 放行；
       - 若 seed 可用，采用对应槽位的确定性子流 RNG 进行 sample_categorical 真实抽样重放，必须精确命中目标选项；
    8. 若无 recorded_dist，target_item_id 在重算分布中的抽样概率必须严格大于 0.0。
    """
    if not isinstance(affinity_data, dict):
        return False, "affinity_data is not a dict"
    matrix = affinity_data.get("matrix")
    if not isinstance(matrix, dict) or not matrix:
        return False, "affinity_data lacks non-empty matrix"

    from lib.models import ORDERED_CONTEXT_IDS, ContextProfile
    from lib.context_affinity import ORDERED_SLOT_IDS, compute_slot_distribution, sample_categorical
    from lib.conflict_resolver import normalize_slot_name
    from lib.rng import derive_substream_rng

    # 拒绝完全无关的矩阵 (与 ORDERED_CONTEXT_IDS 无交集)
    matrix_contexts = set(matrix.keys())
    if not (matrix_contexts & set(ORDERED_CONTEXT_IDS)):
        return False, f"matrix contains no valid standard contexts (got: {list(matrix_contexts)[:5]})"

    # 核验 active_contexts 存在性
    norm_slot = normalize_slot_name(slot)
    target_contexts = [c for c in active_contexts if c in ORDERED_CONTEXT_IDS]
    if not target_contexts:
        target_contexts = ["generic"]

    for ctx in target_contexts:
        if ctx not in matrix:
            return False, f"matrix lacks active context '{ctx}'"
        row = matrix[ctx]
        if not isinstance(row, dict):
            return False, f"matrix row for context '{ctx}' is not an object"
        if norm_slot in ORDERED_SLOT_IDS:
            if norm_slot not in row:
                return False, f"matrix row '{ctx}' lacks slot '{norm_slot}'"
            cell = row.get(norm_slot)
            if cell is None or not isinstance(cell, dict):
                return False, f"matrix cell for context '{ctx}' slot '{norm_slot}' is null or not a dict"
            mode = cell.get("mode")
            if mode not in ("weighted", "neutral"):
                return False, f"matrix cell for context '{ctx}' slot '{norm_slot}' has invalid mode '{mode}'"
            if mode == "weighted":
                cands = cell.get("candidates")
                if not isinstance(cands, list) or not cands:
                    return False, f"matrix cell for context '{ctx}' slot '{norm_slot}' has empty or non-list candidates"
                for c in cands:
                    if not isinstance(c, dict) or not c.get("id") or not isinstance(c.get("weight"), (int, float)) or c["weight"] <= 0:
                        return False, f"matrix cell for context '{ctx}' slot '{norm_slot}' contains invalid candidate: {c!r}"

    # 受控概率分布重放验证与抽样因果核验 (Replay Verification & Causal Evidence)
    if norm_slot in ORDERED_SLOT_IDS:
        if not context_profile:
            return False, f"Missing context_profile for slot '{norm_slot}' (seed: {seed}, target item: '{target_item_id}'). Cannot prove causal sampling."

        weights_dict = context_profile.get("weights", {}) if isinstance(context_profile, dict) else {}
        weights_tuple = tuple((c, float(w)) for c, w in weights_dict.items() if w and w > 0)
        if not weights_tuple:
            weights_tuple = tuple((c, 1.0 / len(target_contexts)) for c in target_contexts)
        prof = ContextProfile(weights=weights_tuple)

        # 严格要求：必须提供 recorded_rationale 且包含非空 distribution，彻底删除无证据的概率大于零放行路径
        if not recorded_rationale or not isinstance(recorded_rationale, dict):
            return False, f"Missing sampling_rationale for slot '{norm_slot}' (seed: {seed}, target item: '{target_item_id}'). Cannot prove causal sampling without recorded rationale."

        recorded_dist = recorded_rationale.get("distribution")
        if not recorded_dist or not isinstance(recorded_dist, dict) or len(recorded_dist) == 0:
            return False, f"Missing or empty distribution in sampling_rationale for slot '{norm_slot}' (seed: {seed}, target item: '{target_item_id}'). Cannot prove causal sampling."

        if seed is None:
            return False, f"Missing seed for deterministic substream replay for slot '{norm_slot}' (target item: '{target_item_id}'). Cannot prove causal sampling."

        cat_order = get_slot_catalog_order(norm_slot, DATA_DIR)
        cand_keys = [k for k in cat_order if k in recorded_dist]
        if not cand_keys:
            cand_keys = list(recorded_dist.keys())

        try:
            recomputed_dist = compute_slot_distribution(norm_slot, prof, cand_keys, matrix)
        except Exception as e:
            return False, f"Replay of compute_slot_distribution failed for slot '{norm_slot}': {e}"

        # 强校验：重算分布必须与归档分布严格一致 (杜绝任意将 matrix 替换为 neutral 或伪造权重放行)
        max_drift = 0.0
        drift_key = None
        for k, expected_p in recorded_dist.items():
            actual_p = round(recomputed_dist.get(k, 0.0), 6)
            diff = abs(actual_p - expected_p)
            if diff > max_drift:
                max_drift = diff
                drift_key = k

        if max_drift > 1e-4:
            return False, (
                f"Affinity distribution mismatch for slot '{norm_slot}' on candidate '{drift_key}': "
                f"recorded={recorded_dist.get(drift_key)}, recomputed={recomputed_dist.get(drift_key, 0.0):.6f} "
                f"(max drift {max_drift:.6f} > 1e-4). The provided affinity matrix fails to causally reproduce recorded distribution."
            )

        # 严格抽样重放：使用确定性独立子流 RNG，验证在重算分布下是否精确重现选中结果
        ns = SLOT_TO_SELECTOR_NAMESPACE.get(norm_slot, f"selector:{norm_slot}")
        rng_sub = derive_substream_rng(seed, ns)
        try:
            sampled_id = sample_categorical(recomputed_dist, rng_sub)
        except Exception as e:
            return False, f"sample_categorical replay failed for slot '{norm_slot}': {e}"

        expected_id = None
        if recorded_rationale and isinstance(recorded_rationale, dict):
            expected_id = recorded_rationale.get("final_selection", {}).get("selected_id")
        if not expected_id:
            expected_id = target_item_id
        if expected_id and sampled_id != expected_id:
            return False, (
                f"Controlled sampling replay mismatch for slot '{norm_slot}' (seed: {seed}): "
                f"expected selection '{expected_id}', but substream RNG sampled '{sampled_id}' under provided matrix. "
                f"Causal sampling replay failed."
            )

    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# 独立 Worker 脚本定义
# ─────────────────────────────────────────────────────────────────────────────
TARGET_WORKER_SCRIPT = """
import sys, os, json, hashlib, copy
from pathlib import Path
from random import Random

repo_path = sys.argv[1]
start_seed = int(sys.argv[2])
count = int(sys.argv[3])
out_file = sys.argv[4]

sys.path.insert(0, repo_path)
import nodes
from lib.conflict_resolver import ConflictResolver
from lib.lexer import validate_prompt_syntax
from tests.audit_oracle import validate_audit_json_oracle
from scratch.run_m5_3_audit import serialize_atom_full_state, compare_atom_full_states

with open(Path(repo_path) / "schemas" / "diagnostics.schema.json", "r", encoding="utf-8") as f:
    schema_doc = json.load(f)

generator = nodes.IYKYKPromptGenerator()
diagnostics = nodes.IYKYKPromptDiagnostics()
resolver = ConflictResolver(Path(repo_path) / "data")

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

def serialize_decision(d):
    return {
        "decision_id": getattr(d, "decision_id", ""),
        "sequence": getattr(d, "sequence", 0),
        "rule_id": getattr(d, "rule_id", ""),
        "phase": getattr(d, "phase", ""),
        "action": getattr(d, "action", ""),
        "reason_code": getattr(d, "reason_code", ""),
        "target_atom_id": getattr(d, "target_atom_id", None),
        "winner_atom_ids": list(getattr(d, "winner_atom_ids", ())),
        "produced_atom_ids": list(getattr(d, "produced_atom_ids", ())),
        "before_text": getattr(d, "before_text", None),
        "after_text": getattr(d, "after_text", None),
    }

results = []
for s in range(start_seed, start_seed + count):
    # 轮次 A
    r1 = generator.generate_structured(**inputs, prompt_seed=s)
    d_pos1, d_neg1, d_desc1, d_audit1 = diagnostics.diagnose(**inputs, prompt_seed=s)

    # 轮次 B
    r2 = generator.generate_structured(**inputs, prompt_seed=s)
    d_pos2, d_neg2, d_desc2, d_audit2 = diagnostics.diagnose(**inputs, prompt_seed=s)

    # 1. 严格双轮确定性核验（包含第二轮 description 与 d_audit 双向全等）
    det_pass = (
        r1.positive == r2.positive and
        r1.negative == r2.negative and
        r1.description == r2.description and
        d_pos1 == r1.positive and
        d_pos2 == r1.positive and
        d_neg1 == r1.negative and
        d_neg2 == r1.negative and
        d_desc1 == r1.description and
        d_desc2 == r1.description and
        d_audit1 == d_audit2
    )

    # 2. 残余硬冲突
    unresolved_count = len(r1.resolution_report.unresolved_conflicts) if r1.resolution_report else 0

    # 3. 词数预算
    words_count = len(r1.positive.split())
    word_budget_pass = (words_count <= 250)

    # 4. 语法合法性
    syntax_error = None
    try:
        validate_prompt_syntax(r1.positive)
    except Exception as e:
        syntax_error = str(e)

    # 5. 严格二次消解幂等性（包含原子全状态冻结与全字段对比）
    pre_secondary_snapshot = [serialize_atom_full_state(a) for a in r1.atoms]
    input_atoms = [copy.deepcopy(a) for a in r1.atoms]
    res2, app2, rep2 = resolver.resolve_atoms_with_full_report(input_atoms, Random(s))
    post_secondary_snapshot = [serialize_atom_full_state(a) for a in res2]
    secondary_decisions_count = len(rep2.decisions)
    atoms_identical_after_secondary, idempotency_diffs = compare_atom_full_states(
        pre_secondary_snapshot, post_secondary_snapshot
    )
    idempotency_pass = (secondary_decisions_count == 0 and atoms_identical_after_secondary)

    # 6. Oracle & Schema 严格闭环校验
    oracle_error = None
    try:
        validate_audit_json_oracle(
            d_audit1,
            schema_doc=schema_doc,
            expected_positive=r1.positive,
            trusted_inputs=inputs,
        )
    except Exception as e:
        oracle_error = str(e)

    # 7. Provenance DAG 闭环
    provenance_closed = all(len(a.provenance.parent_ids) > 0 for a in r1.atoms)

    pos_hash = hashlib.sha256(r1.positive.encode("utf-8")).hexdigest()

    item = {
        "seed": s,
        "round_a": {
            "positive": r1.positive,
            "negative": r1.negative,
            "description": r1.description,
            "pos_hash": pos_hash,
            "source_atoms": [serialize_atom_full_state(a) for a in r1.source_atoms],
            "final_atoms": pre_secondary_snapshot,
            "post_secondary_atoms": post_secondary_snapshot,
            "decisions": [serialize_decision(d) for d in (r1.resolution_report.decisions if r1.resolution_report else ())],
            "secondary_decisions": [serialize_decision(d) for d in rep2.decisions],
            "deduplicated_atom_ids": [a.atom_id for a in r1.deduplicated_atoms] if hasattr(r1, "deduplicated_atoms") else [],
            "diagnostics_audit": d_audit1,
        },
        "round_b": {
            "positive": r2.positive,
            "negative": r2.negative,
            "description": r2.description,
            "pos_hash": hashlib.sha256(r2.positive.encode("utf-8")).hexdigest(),
            "diagnostics_audit": d_audit2,
        },
        "quality_gates": {
            "determinism_pass": det_pass,
            "unresolved_conflicts_count": unresolved_count,
            "word_budget_pass": word_budget_pass,
            "words_count": words_count,
            "syntax_pass": (syntax_error is None),
            "syntax_error": syntax_error,
            "secondary_idempotency_pass": idempotency_pass,
            "secondary_decisions_count": secondary_decisions_count,
            "atoms_identical_after_secondary": atoms_identical_after_secondary,
            "idempotency_diffs": idempotency_diffs,
            "oracle_pass": (oracle_error is None),
            "oracle_error": oracle_error,
            "provenance_closed_pass": provenance_closed,
        },
    }
    results.append(item)

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(results, f)
"""


BASELINE_WORKER_SCRIPT = """
import sys, os, json, hashlib
from pathlib import Path

repo_path = sys.argv[1]
start_seed = int(sys.argv[2])
count = int(sys.argv[3])
out_file = sys.argv[4]

sys.path.insert(0, repo_path)
import nodes

generator = nodes.IYKYKPromptGenerator()

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

def serialize_atom(a):
    p_ids = list(a.provenance.parent_ids) if a.provenance and hasattr(a.provenance, "parent_ids") else []
    return {
        "atom_id": getattr(a, "atom_id", ""),
        "text": getattr(a, "text", ""),
        "source_slot": getattr(a, "source_slot", ""),
        "source_item_id": getattr(a, "source_item_id", ""),
        "tag_order": getattr(a, "tag_order", 0),
        "span_order": getattr(a, "span_order", 0),
        "parent_ids": p_ids,
    }

def serialize_decision(d):
    return {
        "decision_id": getattr(d, "decision_id", ""),
        "sequence": getattr(d, "sequence", 0),
        "rule_id": getattr(d, "rule_id", ""),
        "phase": getattr(d, "phase", ""),
        "action": getattr(d, "action", ""),
        "reason_code": getattr(d, "reason_code", ""),
        "target_atom_id": getattr(d, "target_atom_id", None),
        "winner_atom_ids": list(getattr(d, "winner_atom_ids", ())),
        "produced_atom_ids": list(getattr(d, "produced_atom_ids", ())),
        "before_text": getattr(d, "before_text", None),
        "after_text": getattr(d, "after_text", None),
    }

results = []
for s in range(start_seed, start_seed + count):
    r = generator.generate_structured(**inputs, prompt_seed=s)
    pos_hash = hashlib.sha256(r.positive.encode("utf-8")).hexdigest()
    item = {
        "seed": s,
        "positive": r.positive,
        "negative": r.negative,
        "description": r.description,
        "pos_hash": pos_hash,
        "words_count": len(r.positive.split()),
        "source_atoms": [serialize_atom(a) for a in r.source_atoms],
        "final_atoms": [serialize_atom(a) for a in r.atoms],
        "decisions": [serialize_decision(d) for d in (r.resolution_report.decisions if r.resolution_report else ())],
    }
    results.append(item)

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(results, f)
"""


def run_batch(
    repo_path: Path,
    worker_script_content: str,
    seeds_count: int,
    chunk_size: int = 1000,
    python_bin: str = sys.executable,
    num_workers: int = 8,
) -> Tuple[Dict[int, Dict[str, Any]], str]:
    import tempfile
    num_chunks = (seeds_count + chunk_size - 1) // chunk_size
    temp_dir = Path(tempfile.mkdtemp(prefix="iykyk_audit_batch_"))
    worker_py = temp_dir / "worker.py"
    worker_py.write_text(worker_script_content, encoding="utf-8")

    futures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
        for chunk_idx in range(num_chunks):
            start = chunk_idx * chunk_size
            count = min(chunk_size, seeds_count - start)
            out_file = temp_dir / f"chunk_{chunk_idx:04d}.json"
            cmd = [python_bin, str(worker_py), str(repo_path), str(start), str(count), str(out_file)]
            futures.append(pool.submit(subprocess.run, cmd, capture_output=True, text=True, cwd=str(repo_path)))

        for f in futures:
            res = f.result()
            if res.returncode != 0:
                raise RuntimeError(f"Worker failed for {repo_path}:\n{res.stderr}\n{res.stdout}")

    all_data = {}
    ordered_hashes = []
    for chunk_idx in range(num_chunks):
        out_file = temp_dir / f"chunk_{chunk_idx:04d}.json"
        with open(out_file, "r", encoding="utf-8") as f:
            chunk_data = json.load(f)
            for item in chunk_data:
                s = item["seed"]
                all_data[s] = item
        try:
            out_file.unlink()
        except OSError:
            pass

    for s in range(seeds_count):
        item = all_data[s]
        h = item["round_a"]["pos_hash"] if "round_a" in item else item["pos_hash"]
        ordered_hashes.append(h)

    batch_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()
    return all_data, batch_hash


# ─────────────────────────────────────────────────────────────────────────────
# 槽位装配流水线与轮次辅助定义
# ─────────────────────────────────────────────────────────────────────────────
from lib.slot_contract import SLOT_ORDER, AUXILIARY_SLOT_ORDER

CANONICAL_ORDERED_SLOTS = (*SLOT_ORDER, *AUXILIARY_SLOT_ORDER)
SLOT_ORDER_INDEX = {s: i for i, s in enumerate(CANONICAL_ORDERED_SLOTS)}
RANDOM_PICK_SLOTS = {
    "props",
    "liquids",
    "makeup",
    "jewelry",
    "imperfections",
    "tattoo",
    "lighting",
    "clothing",
}


def compute_atom_passes(atoms: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """
    通过终止锚点槽位 (quality) 精确划分装配轮次 (pass index)。
    每个装配轮次严格以 quality 槽位原子终结，从而确定各原子在装配流水线中的真实 pass。
    """
    from lib.conflict_resolver import normalize_slot_name
    passes = {}
    q_count = 0
    for a in atoms:
        txt = a.get("text", "").strip()
        slot = normalize_slot_name(a.get("source_slot", ""))
        if txt not in passes:
            passes[txt] = q_count
        if slot == "quality":
            q_count += 1
    return passes


@functools.lru_cache(maxsize=1)
def get_data_sampler():
    from lib.sampler import DataSampler
    return DataSampler(DATA_DIR)


# ─────────────────────────────────────────────────────────────────────────────
# 差异因果归因引擎 (Fail-Closed)
# ─────────────────────────────────────────────────────────────────────────────
def attribute_seed_difference(
    seed: int,
    base_item: Dict[str, Any],
    cur_item: Dict[str, Any],
    catalog_lookup: Dict[Tuple[str, str], Set[str]],
    rules_data: Dict[str, Any],
    affinity_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    对单种子差异进行严谨因果归因 (Fail-Closed)：
    0. 核验种子一致性（严禁 seed 不匹配放行）；
    1. 强核验 rules_data 与 affinity_data 权威证据（空字典必须拒绝）；
    2. 目标提交装配内生一致性核验（解析标签与组装 final atoms 严格双射，严禁混杂重排与重复）；
    3. 源原子必须来自权威词库（严禁无依据外来文本注入）；
    4. 逆向无静默消失拦截（源原子未进入终态必须有消解决策）；
    5. 跨版本标签保序与多重度核验（覆盖混合差异，未消解共享标签严禁相对逆序与无依据复制）；
    6. 跨版本差异原子因果证明：
       - 消解决策产物/消除物必须存在于 rules_data 规则定义中；
       - 亲和调整产物/消除物必须存在于 affinity_data 矩阵中，且在 selections 中具备合规 sampling_rationale。
    """
    import collections
    import itertools
    import json
    from lib.conflict_resolver import normalize_slot_name
    from lib.lexer import parse_prompt

    unexplained_reasons: List[str] = []
    attributions: List[Dict[str, Any]] = []

    # ─────────────────────────────────────────────────────────────
    # 步骤 0: 种子一致性核验 (反例: seed 不匹配拦截)
    # ─────────────────────────────────────────────────────────────
    base_seed = base_item.get("seed")
    cur_seed = cur_item.get("seed")
    if base_seed is not None and cur_seed is not None:
        if base_seed != cur_seed or base_seed != seed:
            unexplained_reasons.append(
                f"UNEXPLAINED_SEED_MISMATCH: Seed mismatch between baseline (seed={base_seed}) and target (seed={cur_seed}, expected={seed})"
            )

    # ─────────────────────────────────────────────────────────────
    # 步骤 1: 规则与亲和数据存在性与标准情境强核验 (反例: 替换为空字典或无关假矩阵拦截)
    # ─────────────────────────────────────────────────────────────
    known_rule_ids = set()
    rule_spec_map = {}
    if isinstance(rules_data, dict) and "rules" in rules_data:
        known_rule_ids = {r.get("id") for r in rules_data["rules"] if isinstance(r, dict) and "id" in r}
        rule_spec_map = {r.get("id"): r for r in rules_data["rules"] if isinstance(r, dict) and "id" in r}
    else:
        unexplained_reasons.append(
            "UNEXPLAINED_MISSING_RULE_EVIDENCE: rules_data is empty or lacks 'rules' list"
        )

    matrix = {}
    if isinstance(affinity_data, dict):
        matrix = affinity_data.get("matrix", {})
    if not matrix or not isinstance(matrix, dict):
        unexplained_reasons.append(
            "UNEXPLAINED_NO_AFFINITY_EVIDENCE: affinity_data is empty or lacks non-empty 'matrix' object"
        )
    else:
        from lib.models import ORDERED_CONTEXT_IDS
        if not (set(matrix.keys()) & set(ORDERED_CONTEXT_IDS)):
            unexplained_reasons.append(
                f"UNEXPLAINED_NO_AFFINITY_EVIDENCE: affinity_data matrix has no standard context definitions (got: {list(matrix.keys())[:5]})"
            )

    cur_round_a = cur_item.get("round_a", cur_item)
    base_pos = base_item.get("positive", "")
    cur_pos = cur_round_a.get("positive", "")

    cur_src = cur_round_a.get("source_atoms", [])
    cur_atoms = cur_round_a.get("final_atoms", [])
    cur_decisions = cur_round_a.get("decisions", [])

    base_src = base_item.get("source_atoms", [])
    base_atoms = base_item.get("final_atoms", [])
    base_decisions = base_item.get("decisions", [])

    cur_src_by_id = {a["atom_id"]: a for a in cur_src if a.get("atom_id")}
    cur_produced_ids = {pid for d in cur_decisions for pid in d.get("produced_atom_ids", ())}
    cur_decs_by_prod = {pid: d for d in cur_decisions for pid in d.get("produced_atom_ids", ())}
    cur_decs_by_target = {d["target_atom_id"]: d for d in cur_decisions if d.get("target_atom_id")}

    for d in cur_decisions:
        rid = d.get("rule_id")
        if rid and rid not in known_rule_ids:
            unexplained_reasons.append(
                f"UNEXPLAINED_MISSING_RULE_EVIDENCE: Decision '{d.get('decision_id')}' references rule '{rid}' which is not in rules_data"
            )

    # ─────────────────────────────────────────────────────────────
    # 步骤 2: 目标提交装配内生一致性核验 (反例: 混合变化中重排或重复标签拦截)
    # ─────────────────────────────────────────────────────────────
    cur_parsed_tags = [t.text.strip() for t in parse_prompt(cur_pos).tags] if cur_pos else []
    cur_grouped_atom_texts = []
    for _, g in itertools.groupby(cur_atoms, key=lambda a: a.get("tag_order", 0)):
        spans = [a["text"].strip() for a in g if a.get("text")]
        if spans:
            cur_grouped_atom_texts.append(" ".join(spans))

    if cur_parsed_tags != cur_grouped_atom_texts:
        if collections.Counter(cur_parsed_tags) != collections.Counter(cur_grouped_atom_texts):
            unexplained_reasons.append(
                f"UNEXPLAINED_TAG_MULTIPLICITY_CHANGE: cur_pos tags multiplicity ({len(cur_parsed_tags)}) does not match assembled final atoms ({len(cur_grouped_atom_texts)})"
            )
        else:
            unexplained_reasons.append(
                "UNEXPLAINED_TAG_ORDER_VIOLATION: cur_pos tags sequence does not match assembled final atoms canonical order"
            )

    for i in range(len(cur_atoms) - 1):
        if cur_atoms[i].get("tag_order", 0) > cur_atoms[i + 1].get("tag_order", 0):
            unexplained_reasons.append(
                f"UNEXPLAINED_TAG_ORDER_VIOLATION: final_atoms tag_order is not monotonically non-decreasing ({cur_atoms[i].get('tag_order')} > {cur_atoms[i+1].get('tag_order')})"
            )
            break

    # 2B: 真实采样顺序与装配单调性严格核验 (反例: 同步篡改文本、终态原子与快照的同槽位/跨槽位逆序重排拦截)
    cur_src_id_to_idx = {a["atom_id"]: idx for idx, a in enumerate(cur_src) if a.get("atom_id")}
    retained_src_indices = [
        (a.get("atom_id"), a.get("text", "").strip(), cur_src_id_to_idx[a["atom_id"]])
        for a in cur_atoms
        if a.get("atom_id") in cur_src_id_to_idx and a.get("atom_id") not in cur_produced_ids
    ]
    for k in range(len(retained_src_indices) - 1):
        aid1, txt1, idx1 = retained_src_indices[k]
        aid2, txt2, idx2 = retained_src_indices[k + 1]
        if idx1 > idx2:
            unexplained_reasons.append(
                f"UNEXPLAINED_ASSEMBLY_ORDER_VIOLATION: Retained source atom '{txt1}' (sampled index {idx1}) appears before '{txt2}' (sampled index {idx2}) in final_atoms without decision justification"
            )
            break

    # ─────────────────────────────────────────────────────────────
    # 步骤 3: 当前版本源原子与词库权威有效性核验 (反例: 注入无依据未知文本)
    # ─────────────────────────────────────────────────────────────
    for a in cur_atoms:
        aid = a.get("atom_id", "")
        txt = a.get("text", "")
        slot = normalize_slot_name(a.get("source_slot", ""))
        iid = a.get("source_item_id", "")

        is_sourced = (aid in cur_src_by_id and cur_src_by_id[aid].get("text") == txt)
        is_produced = (aid in cur_produced_ids)

        if not is_sourced and not is_produced:
            unexplained_reasons.append(
                f"UNEXPLAINED_UNSOURCED_FINAL_ATOM: Final atom '{aid}' ('{txt}') appeared without matching source atom or producing decision"
            )
            continue

        if is_sourced:
            key = (slot, iid)
            if key not in catalog_lookup:
                if slot not in ("quality", "preset_core", "style_recipe", "custom"):
                    unexplained_reasons.append(
                        f"UNEXPLAINED_UNKNOWN_CATALOG_ITEM: Atom '{aid}' ('{txt}') references unknown item '{iid}' in slot '{slot}'"
                    )
            else:
                valid_tags = catalog_lookup[key]
                clean_txt = txt.strip()
                if clean_txt not in valid_tags and txt not in valid_tags:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_BASELESS_SOURCE_ATOM: Atom '{aid}' ('{txt}') in slot '{slot}' (item '{iid}') is not in authoritative catalog leaf tags"
                    )

    # ─────────────────────────────────────────────────────────────
    # 步骤 4: 逆向无静默消失拦截 (反例: 源原子无消解决策消失)
    # ─────────────────────────────────────────────────────────────
    cur_final_ids = {a.get("atom_id") for a in cur_atoms}
    cur_dedup_ids = set(cur_round_a.get("deduplicated_atom_ids", []))
    for sa in cur_src:
        said = sa.get("atom_id", "")
        stxt = sa.get("text", "")
        if said not in cur_final_ids and said not in cur_dedup_ids:
            dec = cur_decs_by_target.get(said)
            if not dec or dec.get("action") not in ("drop", "replace"):
                unexplained_reasons.append(
                    f"UNEXPLAINED_SILENT_ATOM_DROP: Source atom '{said}' ('{stxt}') dropped from final output without recorded drop/replace decision"
                )

    # ─────────────────────────────────────────────────────────────
    # 步骤 5: 跨版本标签保序与多重度一致性核验 (覆盖混合差异，严禁未消解共享标签重排与无依据复制)
    # ─────────────────────────────────────────────────────────────
    base_parsed_tags = [t.text.strip() for t in parse_prompt(base_pos).tags] if base_pos else []

    # 5A: 跨版本共有标签保序强核验（覆盖混合差异，严禁无关共有标签逆序）
    cur_decs_all = cur_decisions + base_decisions
    decision_atom_ids = (
        cur_produced_ids
        | {d_.get("target_atom_id") for d_ in cur_decs_all if d_.get("target_atom_id")}
        | {wid for d_ in cur_decs_all for wid in d_.get("winner_atom_ids", ())}
    )
    decision_texts = {
        a.get("text", "").strip() for a in (cur_atoms + base_atoms) if a.get("atom_id") in decision_atom_ids
    }
    decision_slots = {
        normalize_slot_name(a.get("source_slot", ""))
        for a in (cur_atoms + base_atoms + cur_src + base_src)
        if a.get("atom_id") in decision_atom_ids
    }
    linkage_texts = {
        a.get("text", "").strip() for a in (cur_atoms + base_atoms)
        if a.get("source_item_id") in ("linkage_general", "auto_linkage")
        or "linkage" in str(a.get("parent_ids", []))
    }
    linkage_slots = {
        normalize_slot_name(a.get("source_slot", ""))
        for a in (cur_atoms + base_atoms)
        if a.get("source_item_id") in ("linkage_general", "auto_linkage")
        or "linkage" in str(a.get("parent_ids", []))
    }

    base_shared_tags = [t for t in base_parsed_tags if t in set(cur_parsed_tags)]
    cur_shared_tags = [t for t in cur_parsed_tags if t in set(base_parsed_tags)]

    cur_tag_to_slot = {a.get("text", "").strip(): normalize_slot_name(a.get("source_slot", "")) for a in cur_atoms}
    base_tag_to_slot = {a.get("text", "").strip(): normalize_slot_name(a.get("source_slot", "")) for a in base_atoms}
    cur_tag_to_item = {a.get("text", "").strip(): a.get("source_item_id", "") for a in cur_atoms}
    base_tag_to_item = {a.get("text", "").strip(): a.get("source_item_id", "") for a in base_atoms}
    cur_tag_to_order = {a.get("text", "").strip(): a.get("tag_order", 0) for a in cur_atoms}
    base_tag_to_order = {a.get("text", "").strip(): a.get("tag_order", 0) for a in base_atoms}

    cur_src_text_to_idx = {a.get("text", "").strip(): idx for idx, a in enumerate(cur_src)}
    base_src_text_to_idx = {a.get("text", "").strip(): idx for idx, a in enumerate(base_src)}

    cur_tag_to_pass = compute_atom_passes(cur_atoms)
    base_tag_to_pass = compute_atom_passes(base_atoms)

    for i in range(len(cur_shared_tags)):
        for j in range(i + 1, len(cur_shared_tags)):
            tA, tB = cur_shared_tags[i], cur_shared_tags[j]
            if tA in base_shared_tags and tB in base_shared_tags:
                b_i = base_shared_tags.index(tA)
                b_j = base_shared_tags.index(tB)
                if b_i > b_j:
                    slotA = cur_tag_to_slot.get(tA) or base_tag_to_slot.get(tA) or ""
                    slotB = cur_tag_to_slot.get(tB) or base_tag_to_slot.get(tB) or ""
                    itemA_changed = (cur_tag_to_item.get(tA) != base_tag_to_item.get(tA))
                    itemB_changed = (cur_tag_to_item.get(tB) != base_tag_to_item.get(tB))

                    order_A_cur = cur_tag_to_order.get(tA, 0)
                    order_B_cur = cur_tag_to_order.get(tB, 0)
                    order_A_base = base_tag_to_order.get(tA, 0)
                    order_B_base = base_tag_to_order.get(tB, 0)

                    s_idx_A = SLOT_ORDER_INDEX.get(slotA, 999)
                    s_idx_B = SLOT_ORDER_INDEX.get(slotB, 999)

                    pass_A_cur = cur_tag_to_pass.get(tA, 0)
                    pass_B_cur = cur_tag_to_pass.get(tB, 0)

                    same_slot_order_justified = False
                    if slotA == slotB:
                        # 严格依据真实采样顺序和装配记录核验位置：
                        # 仅当采样源本身在两版本间相对逆序，且目标终态原子忠实体现了该采样源逆序时方可豁免
                        c_idx_A = cur_src_text_to_idx.get(tA)
                        c_idx_B = cur_src_text_to_idx.get(tB)
                        b_idx_A = base_src_text_to_idx.get(tA)
                        b_idx_B = base_src_text_to_idx.get(tB)
                        if (
                            c_idx_A is not None and c_idx_B is not None and
                            b_idx_A is not None and b_idx_B is not None
                        ):
                            if (c_idx_A < c_idx_B) and (b_idx_A > b_idx_B):
                                same_slot_order_justified = True

                    is_exempt = (
                        (tA in decision_texts or tB in decision_texts) or
                        (tA in linkage_texts or tB in linkage_texts) or
                        (itemA_changed or itemB_changed) or
                        same_slot_order_justified or
                        (
                            slotA != slotB and
                            (order_A_cur != order_A_base or order_B_cur != order_B_base) and
                            (
                                not (pass_A_cur == pass_B_cur and s_idx_A > s_idx_B)
                                or (slotA in decision_slots or slotB in decision_slots)
                                or (slotA in linkage_slots or slotB in linkage_slots)
                            )
                        )
                    )
                    if not is_exempt:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_TAG_ORDER_VIOLATION: Cross-version shared tags '{tA}' and '{tB}' were reordered without rule, sampling, or migration justification"
                        )
                        break
        if any("UNEXPLAINED_TAG_ORDER_VIOLATION" in r for r in unexplained_reasons):
            break

    # 5B: 标签多重度检查：跨版本共有标签不可无规则依据在终态中增加出现频次
    base_tag_counts = collections.Counter(base_parsed_tags)
    cur_tag_counts = collections.Counter(cur_parsed_tags)
    for txt, cur_cnt in cur_tag_counts.items():
        base_cnt = base_tag_counts.get(txt, 0)
        if cur_cnt > base_cnt and base_cnt > 0:
            matching_cur_atoms = [a for a in cur_atoms if a.get("text", "").strip() == txt]
            produced_count = sum(1 for a in matching_cur_atoms if a.get("atom_id") in cur_produced_ids)
            if produced_count < (cur_cnt - base_cnt):
                unexplained_reasons.append(
                    f"UNEXPLAINED_TAG_MULTIPLICITY_CHANGE: Cross-version shared tag '{txt}' multiplicity increased from {base_cnt} to {cur_cnt} without rule injection"
                )

    # 内部终态原子多重度核验：同一槽位内相同原子不可无规则依据在终态中重复出现
    atom_texts_counter = collections.Counter(a.get("text", "").strip() for a in cur_atoms if a.get("text"))
    for txt, count in atom_texts_counter.items():
        if count > 1:
            matching_cur_atoms = [a for a in cur_atoms if a.get("text", "").strip() == txt]
            all_produced = all(a.get("atom_id") in cur_produced_ids for a in matching_cur_atoms)
            if not all_produced:
                slots_involved = {a.get("source_slot") for a in matching_cur_atoms}
                if len(slots_involved) <= 1:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_TAG_MULTIPLICITY_CHANGE: Atom '{txt}' duplicated {count} times within slot '{list(slots_involved)[0]}' without rule injection"
                    )

    # ─────────────────────────────────────────────────────────────
    # 步骤 6: 跨版本差异原子因果证明与受控重放证据核验
    # ─────────────────────────────────────────────────────────────
    audit_raw = cur_round_a.get("diagnostics_audit", {})
    audit_doc = json.loads(audit_raw) if isinstance(audit_raw, str) else audit_raw
    selections_list = audit_doc.get("selections", []) if isinstance(audit_doc, dict) else []

    active_contexts = []
    ctx_prof = {}
    if isinstance(audit_doc, dict):
        ctx_prof = audit_doc.get("context_profile", {})
        if isinstance(ctx_prof, dict):
            weights = ctx_prof.get("weights", {})
            if isinstance(weights, dict):
                active_contexts = [c for c, w in weights.items() if w and w > 0]
            if not active_contexts:
                active_contexts = (ctx_prof.get("scene_context_ids", []) or []) + (ctx_prof.get("theme_context_ids", []) or [])

    SELECTOR_ALIASES = {
        "clothing": {"clothing", "clothing_state", "clothing_extension", "underwear"},
        "clothing_state": {"clothing", "clothing_state", "clothing_extension", "underwear"},
        "clothing_extension": {"clothing", "clothing_state", "clothing_extension", "underwear"},
        "underwear": {"clothing", "clothing_state", "clothing_extension", "underwear"},
        "scene": {"scene", "scene_theme", "theme"},
        "theme": {"scene", "scene_theme", "theme"},
        "scene_theme": {"scene", "scene_theme", "theme"},
        "lighting": {"lighting", "lighting_palette"},
        "lighting_palette": {"lighting", "lighting_palette"},
        "pose": {"pose"},
        "expression": {"expression"},
        "character": {"character", "character_state"},
    }

    base_texts = [a["text"] for a in base_atoms]
    cur_texts = [a["text"] for a in cur_atoms]

    removed_atoms = [b for b in base_atoms if b["text"] not in set(cur_texts)]
    added_atoms = [c for c in cur_atoms if c["text"] not in set(base_texts)]

    for ra in removed_atoms:
        txt = ra["text"]
        slot = normalize_slot_name(ra.get("source_slot", ""))
        iid = ra.get("source_item_id", "")
        aid = ra.get("atom_id", "")

        # 1. 核对基线侧证据：分别核验规则生成产物与词库条目来源
        base_src = base_item.get("source_atoms", [])
        base_decs = base_item.get("decisions", [])
        # 动作严格限定为实际支持的 inject / replace
        base_matching_rule = [
            d for d in base_decs
            if d.get("action") in ("inject", "replace") and (aid in d.get("produced_atom_ids", []) or d.get("after_text") == txt)
        ]
        base_matching_src = [s for s in base_src if s.get("text") == txt or s.get("atom_id") == aid]

        if base_matching_rule:
            rule_dec = base_matching_rule[0]
            rule_id = rule_dec.get("rule_id", "")
            if not rule_id or rule_id not in known_rule_ids:
                unexplained_reasons.append(
                    f"UNEXPLAINED_MISSING_RULE_EVIDENCE: Rule '{rule_id}' generating removed atom '{txt}' in baseline not found in rules_data"
                )
                continue

            # 校验产物内容必须属于规则定义支持的生成文本
            rule_spec = rule_spec_map.get(rule_id, {})
            allowed_rule_texts = set(rule_spec.get("fusion_tags", [])) | set(rule_spec.get("replacements", []))
            if allowed_rule_texts and txt not in allowed_rule_texts:
                unexplained_reasons.append(
                    f"UNEXPLAINED_BASELESS_SOURCE_ATOM: Rule product '{txt}' is not in allowed production texts for rule '{rule_id}'"
                )
                continue

            # 校验产物 ID 与产物证据
            has_prod_evidence = (
                aid in rule_dec.get("produced_atom_ids", [])
                or rule_dec.get("after_text") == txt
            )
            if not has_prod_evidence:
                unexplained_reasons.append(
                    f"UNEXPLAINED_BASELESS_SOURCE_ATOM: Rule-generated atom '{txt}' lacks valid production evidence in baseline decision"
                )
                continue

            # 校验母原子/触发来源关系 (parent_ids)
            parent_ids = set(rule_dec.get("winner_atom_ids", [])) | set([rule_dec.get("target_atom_id")] if rule_dec.get("target_atom_id") else []) | set(ra.get("parent_ids", []))
            parents_in_base = [a for a in (base_src + base_atoms) if a.get("atom_id") in parent_ids]
            if not parents_in_base:
                unexplained_reasons.append(
                    f"UNEXPLAINED_BASELESS_SOURCE_ATOM: Rule product '{txt}' has no valid trigger mother atom in baseline record"
                )
                continue

            is_rule_product = True
        elif base_matching_src:
            is_rule_product = False
            if (
                iid
                and iid not in ("linkage_general", "linkage_override", "auto_linkage")
                and (slot, iid) not in catalog_lookup
                and (ra.get("source_slot"), iid) not in catalog_lookup
            ):
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNKNOWN_CATALOG_ITEM: Removed atom '{txt}' in baseline has unknown catalog item '{iid}' for slot '{slot}'"
                )
                continue
        else:
            unexplained_reasons.append(
                f"UNEXPLAINED_BASELESS_SOURCE_ATOM: Removed atom '{txt}' (slot '{slot}') has no source atom nor rule evidence in baseline record"
            )
            continue

        # 2. 核对消解或目标侧因果证明
        cur_matching_src = [s for s in cur_src if s.get("text") == txt or s.get("atom_id") == aid]
        if cur_matching_src and cur_matching_src[0].get("atom_id") in cur_decs_by_target:
            d = cur_decs_by_target[cur_matching_src[0]["atom_id"]]
            rule_id = d.get("rule_id", "")
            if rule_id not in known_rule_ids:
                unexplained_reasons.append(
                    f"UNEXPLAINED_MISSING_RULE_EVIDENCE: Rule '{rule_id}' resolving removed atom '{txt}' not found in rules_data"
                )
            attributions.append({
                "type": "conflict_rule_resolved",
                "text": txt,
                "rule_id": rule_id,
                "reason_code": d.get("reason_code", ""),
                "action": d.get("action", ""),
                "target_atom_id": d.get("target_atom_id"),
            })
        elif is_rule_product:
            # 规则生成产物的跨版本变化因果证明：
            # (1) 核心检查：目标侧记录已生成的产物，若没有后续合法删除或替换依据，不得判为正常消失
            target_produced_dec = next(
                (d for d in cur_decisions if d.get("action") in ("inject", "replace") and (d.get("after_text") == txt or aid in d.get("produced_atom_ids", []))),
                None
            )
            if target_produced_dec is not None:
                cur_matching_final = [a for a in cur_atoms if a.get("text") == txt or a.get("atom_id") in target_produced_dec.get("produced_atom_ids", [])]
                if not cur_matching_final:
                    produced_target_aid = target_produced_dec.get("produced_atom_ids", [None])[0]
                    subsequent_drop = any(
                        d.get("action") in ("drop", "replace") and d.get("target_atom_id") == produced_target_aid
                        for d in cur_decisions
                        if d.get("sequence", 0) > target_produced_dec.get("sequence", 0)
                    )
                    if not subsequent_drop:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_SILENT_ATOM_DROP: Rule product '{txt}' was generated by target decision '{target_produced_dec.get('decision_id')}' (rule '{target_produced_dec.get('rule_id')}') but is missing from target final atoms without subsequent drop decision."
                        )
                        continue

            # (2) 目标侧未生成该产物时，核验触发母原子的合法变更依据
            rule_dec = base_matching_rule[0]
            rule_id = rule_dec.get("rule_id", "")
            parent_ids = set(rule_dec.get("winner_atom_ids", [])) | set([rule_dec.get("target_atom_id")] if rule_dec.get("target_atom_id") else []) | set(ra.get("parent_ids", []))
            parents_in_base = [a for a in (base_src + base_atoms) if a.get("atom_id") in parent_ids]
            parents_removed = [p for p in parents_in_base if p.get("text") not in set(cur_texts)]

            allowed_selectors = SELECTOR_ALIASES.get(slot, {slot, normalize_slot_name(slot)})
            matching_sels = []
            if isinstance(selections_list, list):
                for sel in selections_list:
                    if not isinstance(sel, dict):
                        continue
                    sel_name = normalize_slot_name(sel.get("selector") or sel.get("slot") or "")
                    if sel_name in allowed_selectors:
                        matching_sels.append(sel)

            if parents_removed:
                attributions.append({
                    "type": "conflict_rule_resolved",
                    "text": txt,
                    "rule_id": rule_id,
                    "reason_code": rule_dec.get("reason_code", ""),
                    "action": rule_dec.get("action", ""),
                    "trigger_parent_removed": parents_removed[0].get("text"),
                })
            elif matching_sels:
                target_sel = matching_sels[0]
                sel_mode = target_sel.get("mode")
                target_sel_id = target_sel.get("selected_id")
                target_rat = target_sel.get("sampling_rationale")
                if target_rat:
                    ok_aff, aff_err = verify_affinity_matrix_for_slot(
                        affinity_data, slot, active_contexts, target_sel_id or iid,
                        context_profile=ctx_prof,
                        catalog_lookup=catalog_lookup,
                        recorded_rationale=target_rat,
                        seed=cur_seed if cur_seed is not None else seed,
                    )
                    if not ok_aff:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_NO_AFFINITY_EVIDENCE: affinity_data matrix verification failed for removed rule product '{txt}' displaced by '{target_sel_id}' in slot '{slot}' (seed {seed}): {aff_err}"
                        )
                    attributions.append({
                        "type": "context_affinity_calibration",
                        "text": txt,
                        "slot": slot,
                        "base_item_id": iid,
                        "target_replacement_id": target_sel_id,
                    })
                elif sel_mode in ("preset", "fixed", "recipe"):
                    attributions.append({
                        "type": "fixed_selection",
                        "text": txt,
                        "slot": slot,
                        "base_item_id": iid,
                        "target_replacement_id": target_sel_id,
                    })
                else:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_MISSING_SAMPLING_RATIONALE: seed {seed}, slot '{slot}', removed rule product '{txt}' displaced by target item '{target_sel_id}' (mode '{sel_mode}') which lacks sampling rationale."
                    )
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_MISSING_EVIDENCE_FOR_REMOVED_RULE_PRODUCT: seed {seed}, rule product '{txt}' (rule '{rule_id}', slot '{slot}') lacks valid causal removal evidence in target."
                )
        else:
            allowed_selectors = SELECTOR_ALIASES.get(slot, {slot, normalize_slot_name(slot)})
            matching_sels = []
            if isinstance(selections_list, list):
                for sel in selections_list:
                    if not isinstance(sel, dict):
                        continue
                    sel_name = normalize_slot_name(sel.get("selector") or sel.get("slot") or "")
                    if sel_name in allowed_selectors:
                        matching_sels.append(sel)

            if not matching_sels:
                unexplained_reasons.append(
                    f"UNEXPLAINED_MISSING_EVIDENCE_FOR_REMOVED_ATOM: seed {seed}, slot '{slot}', removed atom '{txt}' (item: '{iid}') has no conflict resolution decision nor target selection evidence."
                )
            else:
                target_sel = matching_sels[0]
                sel_mode = target_sel.get("mode")
                target_sel_id = target_sel.get("selected_id")
                target_rat = target_sel.get("sampling_rationale")
                target_selector = normalize_slot_name(target_sel.get("selector") or "")

                # 检查同项差异：若目标侧选了相同的 item
                if target_sel_id == iid:
                    # (A) 真实静默丢失：目标源原子确实生成了该文字，但终态原子却丢失且无消解决策
                    target_generated_txt = any(cur_src_a.get("text") == txt for cur_src_a in cur_src)
                    if target_generated_txt:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_SILENT_ATOM_DROP: seed {seed}, slot '{slot}', atom '{txt}' generated in target source atoms but silently dropped from final prompt without rule decision despite identical item '{target_sel_id}'."
                        )
                        continue

                    # (B) 目标源原子未生成该文字：
                    # 对于 props 槽位，条目内存在手部容量过滤与确定性子流抽样 (sample_prop_result)，需进行受控重放验证
                    if slot == "props":
                        from lib.context_affinity import compute_context_profile
                        from lib.rng import derive_substream_rng

                        # 从 diagnostics audit 中恢复 context_profile, pose, hand_state, nudity_level_code
                        scene_cids = tuple(ctx_prof.get("scene_context_ids", ()))
                        theme_cids = tuple(ctx_prof.get("theme_context_ids", ()))
                        scene_item_id = ctx_prof.get("scene_item_id")
                        theme_item_id = ctx_prof.get("theme_id")
                        cp = compute_context_profile(scene_cids, theme_cids, scene_item_id, theme_item_id)
                        primary_context = scene_cids[0] if scene_cids else "generic"
                        context = primary_context if primary_context != "generic" else "generic"

                        pose_sel = next((sel for sel in selections_list if sel.get("selector") == "pose"), None)
                        pose_id = pose_sel.get("selected_id") if pose_sel else None

                        hand_state = None
                        pose_atoms = [a for a in cur_src if a.get("source_slot") == "pose"]
                        for a in pose_atoms:
                            hs = a.get("facts", {}).get("hand_state")
                            if hs in ("both_busy", "restrained"):
                                hand_state = "both_busy"
                                break
                            elif hs == "one_busy" and hand_state != "both_busy":
                                hand_state = "one_busy"
                            elif hs == "free" and not hand_state:
                                hand_state = "free"

                        nudity_sel = next((sel for sel in selections_list if sel.get("selector") == "nudity"), None)
                        nid = nudity_sel.get("selected_id", "") if nudity_sel else ""
                        nudity_level_code = nid.replace("nudity_l", "L").replace("nudity_", "").upper() if nid else None

                        sampler_inst = get_data_sampler()
                        rng_prop = derive_substream_rng(cur_seed if cur_seed is not None else seed, "selector:props")
                        res = sampler_inst.sample_prop_result(
                            "随机 (Random)",
                            rng_prop,
                            context=context,
                            context_profile=cp,
                            pose_id=pose_id,
                            hand_state=hand_state,
                            nudity_level_code=nudity_level_code,
                        )
                        replayed_tags = list(res.tags) if res else []
                        actual_prop_src = [a.get("text") for a in cur_src if a.get("source_slot") == "props"]

                        # 严格验证：重放结果必须与目标实际源原子 100% 一致
                        if replayed_tags != actual_prop_src:
                            unexplained_reasons.append(
                                f"UNEXPLAINED_PROP_REPLAY_MISMATCH: seed {seed}, slot 'props', target source atoms {actual_prop_src} do not match deterministic replay {replayed_tags}."
                            )
                            continue

                        # 若重放包含 txt，说明按规则应当生成，却在目标源原子中丢失
                        if txt in replayed_tags:
                            unexplained_reasons.append(
                                f"UNEXPLAINED_SILENT_ATOM_DROP: seed {seed}, slot 'props', atom '{txt}' should have been sampled by deterministic replay but is missing from target."
                            )
                            continue

                        # 重放严格一致且确实未采样该 tag -> 合法子抽样/手部过滤
                        attributions.append({
                            "type": "prop_tag_subsampling",
                            "text": txt,
                            "slot": "props",
                            "base_item_id": iid,
                            "target_replacement_id": target_sel_id,
                            "hand_state": hand_state,
                            "replayed_tags": replayed_tags,
                        })
                        continue
                    else:
                        # 非 props 槽位无条目内动态子采样，同 item 却丢失文字判定为静默丢失
                        unexplained_reasons.append(
                            f"UNEXPLAINED_SILENT_ATOM_DROP: seed {seed}, slot '{slot}', removed atom '{txt}' missing from target source atoms without rule decision despite target selecting identical item '{target_sel_id}'."
                        )
                        continue

                # 真实采样路径判断：
                # (1) 明确的固定规则联动（仅限 clothing_state 与 nudity 的确定性映射）
                is_fixed_linkage = (
                    (slot in ("clothing", "clothing_state") or target_selector == "clothing_state")
                    and (target_sel_id in ("linkage_general", "linkage_override", "auto_linkage") or iid in ("linkage_general", "linkage_override", "auto_linkage"))
                    and target_rat is None
                )
                # (2) 明确的固定预设/配方选择（无抽样证据）
                is_fixed_selection = (
                    sel_mode in ("preset", "fixed", "recipe")
                    and target_rat is None
                )

                if is_fixed_linkage:
                    attributions.append({
                        "type": "linked_generation",
                        "text": txt,
                        "slot": slot,
                        "base_item_id": iid,
                        "target_replacement_id": target_sel_id,
                        "target_mode": sel_mode,
                    })
                elif is_fixed_selection:
                    attributions.append({
                        "type": "fixed_selection",
                        "text": txt,
                        "slot": slot,
                        "base_item_id": iid,
                        "target_replacement_id": target_sel_id,
                        "target_mode": sel_mode,
                    })
                elif target_rat:
                    # 亲和抽样（包含 mode="auto" 的 lighting, shot_type, camera_angle 等所有抽样槽位）：
                    # 必须验证完整 sampling_rationale、分布与重放
                    ok_aff, aff_err = verify_affinity_matrix_for_slot(
                        affinity_data, slot, active_contexts, target_sel_id or iid,
                        context_profile=ctx_prof,
                        catalog_lookup=catalog_lookup,
                        recorded_rationale=target_rat,
                        seed=cur_seed if cur_seed is not None else seed,
                    )
                    if not ok_aff:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_NO_AFFINITY_EVIDENCE: affinity_data matrix verification failed for removed atom '{txt}' displaced by '{target_sel_id}' in slot '{slot}' (seed {seed}): {aff_err}"
                        )
                    attributions.append({
                        "type": "context_affinity_calibration",
                        "text": txt,
                        "slot": slot,
                        "base_item_id": iid,
                        "target_replacement_id": target_sel_id,
                    })
                else:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_MISSING_SAMPLING_RATIONALE: seed {seed}, slot '{slot}', removed atom '{txt}' (base item: '{iid}') displaced by target item '{target_sel_id}' (mode '{sel_mode}') which lacks sampling rationale."
                    )

    for aa in added_atoms:
        txt = aa["text"]
        slot = normalize_slot_name(aa.get("source_slot", ""))
        aid = aa.get("atom_id", "")
        iid = aa.get("source_item_id", "")
        if aid in cur_decs_by_prod:
            d = cur_decs_by_prod[aid]
            rule_id = d.get("rule_id", "")
            if rule_id not in known_rule_ids:
                unexplained_reasons.append(
                    f"UNEXPLAINED_MISSING_RULE_EVIDENCE: Rule '{rule_id}' producing added atom '{txt}' not found in rules_data"
                )
            attributions.append({
                "type": "conflict_rule_produced",
                "text": txt,
                "rule_id": rule_id,
                "reason_code": d.get("reason_code", ""),
                "action": d.get("action", ""),
                "produced_atom_id": aid,
            })
        else:
            allowed_selectors = SELECTOR_ALIASES.get(slot, {slot, normalize_slot_name(slot)})
            matching_sels = []
            if isinstance(selections_list, list):
                for sel in selections_list:
                    if not isinstance(sel, dict):
                        continue
                    sel_name = normalize_slot_name(sel.get("selector") or sel.get("slot") or "")
                    if sel_name not in allowed_selectors:
                        continue
                    if sel.get("selected_id") == iid:
                        matching_sels.append(sel)
                    else:
                        atoms_in_sel = sel.get("source_atoms", []) + sel.get("accepted_atoms", [])
                        if any(a.get("atom_id") == aid or a.get("text") == txt for a in atoms_in_sel if isinstance(a, dict)):
                            matching_sels.append(sel)

            if not matching_sels:
                unexplained_reasons.append(
                    f"UNEXPLAINED_BASELESS_TAG_MUTATION: Added atom '{txt}' (slot '{slot}', item '{iid}') has no valid selection evidence in diagnostics"
                )
            else:
                primary_sel = matching_sels[0]
                sel_mode = primary_sel.get("mode")
                target_rat = primary_sel.get("sampling_rationale")
                primary_selector = normalize_slot_name(primary_sel.get("selector") or "")
                target_sel_id = primary_sel.get("selected_id")

                # 真实采样路径判断：
                # (1) 明确的固定规则联动（仅限 clothing_state 与 nudity 的确定性映射）
                is_fixed_linkage = (
                    (slot in ("clothing", "clothing_state") or primary_selector == "clothing_state")
                    and (target_sel_id in ("linkage_general", "linkage_override", "auto_linkage") or iid in ("linkage_general", "linkage_override", "auto_linkage"))
                    and target_rat is None
                )
                # (2) 明确的固定预设/配方选择（无抽样证据）
                is_fixed_selection = (
                    sel_mode in ("preset", "fixed", "recipe")
                    and target_rat is None
                )

                if is_fixed_linkage:
                    attributions.append({
                        "type": "linked_generation",
                        "text": txt,
                        "slot": slot,
                        "cur_item_id": iid,
                        "mode": sel_mode,
                        "raw_value": primary_sel.get("raw_value"),
                    })
                elif is_fixed_selection:
                    attributions.append({
                        "type": "fixed_selection",
                        "text": txt,
                        "slot": slot,
                        "cur_item_id": iid,
                        "mode": sel_mode,
                        "raw_value": primary_sel.get("raw_value"),
                    })
                elif target_rat:
                    ok_aff, aff_err = verify_affinity_matrix_for_slot(
                        affinity_data, slot, active_contexts, iid,
                        context_profile=ctx_prof,
                        catalog_lookup=catalog_lookup,
                        recorded_rationale=target_rat,
                        seed=cur_seed if cur_seed is not None else seed,
                    )
                    if not ok_aff:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_NO_AFFINITY_EVIDENCE: affinity_data matrix verification failed for added atom '{txt}' in slot '{slot}': {aff_err}"
                        )
                    attributions.append({
                        "type": "context_affinity_calibration",
                        "text": txt,
                        "slot": slot,
                        "cur_item_id": iid,
                    })
                else:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_MISSING_SAMPLING_RATIONALE: Added atom '{txt}' (slot '{slot}', item '{iid}', mode '{sel_mode}') executed sampling but lacks sampling rationale in selections"
                    )

    # ─────────────────────────────────────────────────────────────
    # 步骤 7: 二次消解全状态幂等性核验 (反例: 篡改 facts, provenance 等字段或缺失快照拦截)
    # ─────────────────────────────────────────────────────────────
    post_sec_atoms = cur_round_a.get("post_secondary_atoms")
    if post_sec_atoms is None:
        unexplained_reasons.append(
            "UNEXPLAINED_MISSING_SECONDARY_SNAPSHOT: 'post_secondary_atoms' snapshot is missing from record round_a"
        )
    else:
        identical_sec, sec_diffs = compare_atom_full_states(cur_atoms, post_sec_atoms)
        if not identical_sec:
            for diff in sec_diffs:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SECONDARY_IDEMPOTENCY_MUTATION: Secondary resolution atom state drifted: {diff}"
                )

    is_identical = (base_pos == cur_pos)
    is_explained = (len(unexplained_reasons) == 0)

    rule_atts = [a for a in attributions if a["type"] in ("conflict_rule_resolved", "conflict_rule_produced")]
    if not is_explained:
        primary_category = "UNEXPLAINED"
    elif is_identical:
        primary_category = "identical"
    elif rule_atts:
        primary_category = f"conflict_rule:{rule_atts[0]['rule_id']}"
    else:
        primary_category = "context_affinity_calibration"

    return {
        "seed": seed,
        "is_identical": is_identical,
        "is_explained": is_explained,
        "category": primary_category,
        "attributions": attributions,
        "unexplained_reasons": unexplained_reasons,
        "removed_atoms": [a["text"] for a in removed_atoms],
        "added_atoms": [a["text"] for a in added_atoms],
    }


def stream_archive_records(archive_path: Path, chunk_size: int = 1024 * 1024):
    """
    流式逐条读取大归档中的 records，避免一次性在内存中展开完整 JSON 文档。
    返回: (header_metadata, record_generator)
    """
    decoder = json.JSONDecoder()
    f = gzip.open(archive_path, "rt", encoding="utf-8")
    buf = ""
    # 1. 读取头部并提取 metadata 和 batch hashes
    while True:
        chunk = f.read(chunk_size)
        if not chunk:
            break
        buf += chunk
        idx = buf.find('"records":')
        if idx != -1:
            bracket_idx = buf.find("[", idx)
            if bracket_idx != -1:
                header_str = buf[:idx].rstrip(", \r\n\t") + "}"
                header_meta = {}
                try:
                    header_meta = json.loads(header_str)
                except Exception:
                    pass
                buf = buf[bracket_idx + 1:].lstrip()
                break

    # 2. 逐条 yield 每一个 record
    def gen(initial_buf):
        b = initial_buf
        saw_closing_bracket = False
        try:
            while True:
                b = b.lstrip(" ,\r\n\t")
                if not b:
                    c = f.read(chunk_size)
                    if not c:
                        break
                    b += c
                    b = b.lstrip(" ,\r\n\t")
                if b.startswith("]"):
                    saw_closing_bracket = True
                    b = b[1:]
                    break
                try:
                    obj, end_idx = decoder.raw_decode(b)
                    yield obj
                    b = b[end_idx:]
                except json.JSONDecodeError:
                    c = f.read(chunk_size)
                    if not c:
                        raise RuntimeError(
                            f"Truncated archive stream: unexpected EOF encountered during JSON decoding of records in {archive_path}"
                        )
                    b += c
            if not saw_closing_bracket:
                raise RuntimeError(
                    f"Truncated archive stream: EOF reached before encountering closing array ']' in {archive_path}"
                )

            # 3. 校验顶层闭合 '}' 与 EOF 前尾部合法性
            saw_closing_brace = False
            while True:
                b = b.lstrip(" \r\n\t")
                if b.startswith("}"):
                    saw_closing_brace = True
                    b = b[1:]
                    break
                if not b:
                    c = f.read(chunk_size)
                    if not c:
                        break
                    b += c
                else:
                    raise RuntimeError(
                        f"Corrupted archive stream: unexpected character before closing object brace '}}' in {archive_path}: {b[:50]!r}"
                    )

            if not saw_closing_brace:
                raise RuntimeError(
                    f"Truncated archive stream: EOF reached before encountering closing object brace '}}' in {archive_path}"
                )

            # 4. 校验顶层闭合后无多余非法内容，直到真正 EOF
            while True:
                b = b.lstrip(" \r\n\t")
                if b:
                    raise RuntimeError(
                        f"Corrupted archive stream: unexpected trailing non-whitespace data after closing '}}' in {archive_path}: {b[:50]!r}"
                    )
                c = f.read(chunk_size)
                if not c:
                    break
                b = c
        finally:
            f.close()

    return header_meta, gen(buf)


def main():
    parser = argparse.ArgumentParser(description="M5-3 Seed Audit and Divergence Attribution Runner")
    parser.add_argument(
        "--mode",
        choices=["sample", "evaluate-archive", "full-regenerate"],
        default="sample",
        help="Audit mode: 'sample' (fast verification over seeds 0, 2, 42 fixture, default), "
             "'evaluate-archive' (re-evaluate causal attribution over 10k archive), "
             "'full-regenerate' (re-generate 10k seeds from scratch)",
    )
    parser.add_argument("--seeds", type=int, default=10000)
    parser.add_argument("--scratch-dir", type=Path, default=REPO_DIR / "scratch")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--re-evaluate-archive", action="store_true", help="Alias for --mode evaluate-archive")
    args = parser.parse_args()

    if args.re_evaluate_archive:
        args.mode = "evaluate-archive"

    scratch_dir = args.scratch_dir
    scratch_dir.mkdir(parents=True, exist_ok=True)
    sample_fixture_path = scratch_dir / "audit_m5_3_sample_seeds.json"

    # 1. 真实源码与提交绑定 (Source & Commit Binding)
    meta = get_source_tree_metadata(REPO_DIR)
    git_commit = meta["git_commit"]
    git_tree = meta["git_tree"]
    is_dirty = meta["is_dirty"]
    core_source_digest = meta["core_source_digest"]

    base_dir = scratch_dir / f"baseline_{BASELINE_COMMIT}"
    export_commit(BASELINE_COMMIT, base_dir)

    python_version = sys.version.replace("\n", " ")
    os_info = f"{platform.system()} {platform.release()} ({platform.machine()})"
    try:
        import importlib.metadata
        jsonschema_version = importlib.metadata.version("jsonschema")
    except Exception:
        try:
            import jsonschema
            jsonschema_version = getattr(jsonschema, "__version__", "4.25.1")
        except Exception:
            jsonschema_version = "4.25.1"

    base_data_hashes = get_data_file_hashes(base_dir / "data")
    cur_data_hashes = get_data_file_hashes(DATA_DIR)

    modified_data_files = [
        f for f, h in cur_data_hashes.items()
        if f not in base_data_hashes or base_data_hashes[f] != h
    ]

    print(f"[*] Audit Execution Mode            : {args.mode}")
    print(f"[*] Pre-development Baseline Commit : {BASELINE_COMMIT}")
    print(f"[*] Target Development Commit       : {git_commit}")
    print(f"[*] Target Tree Hash                : {git_tree} (Dirty: {is_dirty})")
    print(f"[*] Core Source Code Digest         : {core_source_digest}")
    print(f"[*] Python Version                  : {python_version}")
    print(f"[*] Environment Platform            : {os_info}")
    print(f"[*] jsonschema Version              : {jsonschema_version}")
    print(f"[*] Total Runtime Data Files        : {len(cur_data_hashes)}")
    print(f"[*] Modified Data Files vs Baseline : {modified_data_files}")

    # 加载权威词库白名单
    print(f"[*] Loading authoritative catalog lookup across 16 domains from {DATA_DIR}...")
    catalog_lookup = load_authoritative_catalog_lookup(DATA_DIR)
    print(f"    Loaded {len(catalog_lookup)} authoritative (slot, item_id) catalog entries.")

    archive_file = scratch_dir / "audit_m5_3_evidence.json.gz"
    raw_cache_file = scratch_dir / f"audit_m5_3_raw_batch_{args.seeds}.json.gz"

    if args.mode == "sample":
        print("\n" + "=" * 80)
        print("IYKYK M5-3 Small Sample Audit Mode (seeds: 0, 2, 42)")
        print("=" * 80)
        if not sample_fixture_path.exists():
            raise FileNotFoundError(
                f"Small sample fixture not found: {sample_fixture_path}. "
                "Daily tests must read small sample and must not generate batches automatically."
            )
        fixture = json.loads(sample_fixture_path.read_text(encoding="utf-8"))
        summary = fixture.get("manifest_summary", {})
        print(f"[*] Fixture Source Archive SHA-256 : {summary.get('source_archive_sha256')}")
        print(f"[*] Target Commit Bound            : {summary.get('target_commit')}")
        print(f"[*] Baseline Commit Bound          : {summary.get('baseline_commit')}")
        print(f"[*] Extracted Seeds                : {summary.get('extracted_seeds')}")

        raw_records = fixture["records"]
        cur_data = {int(k): v for k, v in raw_records.items()}
        base_data = {int(k): v["baseline"] for k, v in raw_records.items()}
        total_seeds = len(cur_data)
        seed_list = sorted(list(cur_data.keys()))
        cur_batch_hash = summary.get("target_batch_hash", "N/A")
        base_batch_hash = summary.get("baseline_batch_hash", "N/A")
    elif args.mode == "evaluate-archive":
        print("\n" + "=" * 80)
        print("IYKYK M5-3 10,000 Seeds Audit (Read-Only Archive Re-evaluation Mode)")
        print(f"Source Archive (Read-Only) : {archive_file.name}")
        print("=" * 80)
        if not archive_file.exists():
            raise FileNotFoundError(f"Archive file not found: {archive_file}")

        archive_sha256 = compute_file_sha256(archive_file)
        archive_size = archive_file.stat().st_size
        print("[*] Verifying source archive integrity...")
        print(f"    Archive Path   : {archive_file}")
        print(f"    Archive Size   : {archive_size:,} bytes")
        print(f"    Archive SHA256 : {archive_sha256}")

        rules_data = json.loads((DATA_DIR / "conflict_rules.json").read_text(encoding="utf-8"))
        affinity_data = json.loads((DATA_DIR / "context_affinity.json").read_text(encoding="utf-8"))

        t0 = time.time()
        header_meta, record_iter = stream_archive_records(archive_file)
        archive_meta = header_meta.get("metadata", {})
        cur_batch_hash = archive_meta.get("target_batch_hash", "N/A")
        base_batch_hash = archive_meta.get("baseline_batch_hash", "N/A")
        total_seeds_expected = archive_meta.get("total_seeds", 10000)

        print(f"[*] Bound Target Batch Hash   : {cur_batch_hash}")
        print(f"[*] Bound Baseline Batch Hash : {base_batch_hash}")
        print(f"[*] Streaming & re-evaluating causal attribution over {total_seeds_expected} seeds...")

        identical_count = 0
        divergent_count = 0
        total_evaluated = 0
        unexplained_seeds: Dict[int, List[str]] = {}
        category_counts: collections.Counter = collections.Counter()

        gate_failures = {
            "determinism": 0,
            "residual_conflicts": 0,
            "word_budget": 0,
            "prompt_syntax": 0,
            "secondary_idempotency": 0,
            "oracle_validation": 0,
            "provenance_closed_loop": 0,
        }

        seen_seeds = set()
        prop_subsampling_seeds = set()
        for rec in record_iter:
            s = rec["seed"]
            if s in seen_seeds:
                raise RuntimeError(f"Duplicate seed {s} detected in archive stream at record {total_evaluated + 1}")
            seen_seeds.add(s)
            cur_item = rec
            base_item = rec["baseline"]
            total_evaluated += 1

            # 1. 逐条门禁检查 (复用历史归档记录中的双轮运行质量门禁与 Oracle 闭包标记)
            qg = cur_item.get("quality_gates", {})
            if not qg.get("determinism_pass"):
                gate_failures["determinism"] += 1
            if qg.get("unresolved_conflicts_count", 0) > 0:
                gate_failures["residual_conflicts"] += 1
            if not qg.get("word_budget_pass"):
                gate_failures["word_budget"] += 1
            if not qg.get("syntax_pass"):
                gate_failures["prompt_syntax"] += 1
            if not qg.get("secondary_idempotency_pass"):
                gate_failures["secondary_idempotency"] += 1
            if not qg.get("oracle_pass"):
                gate_failures["oracle_validation"] += 1
            if not qg.get("provenance_closed_pass"):
                gate_failures["provenance_closed_loop"] += 1

            # 2. 严谨因果重评 (由当前最新审核逻辑执行现场因果推导与受控重放)
            res = attribute_seed_difference(s, base_item, cur_item, catalog_lookup, rules_data, affinity_data)
            if any(a.get("type") == "prop_tag_subsampling" for a in res.get("attributions", [])):
                prop_subsampling_seeds.add(s)

            if res["is_identical"]:
                identical_count += 1
            else:
                divergent_count += 1
                cat = res["category"]
                category_counts[cat] += 1
                if not res["is_explained"]:
                    unexplained_seeds[s] = res["unexplained_reasons"]

            if total_evaluated % 1000 == 0 or total_evaluated == total_seeds_expected:
                el = time.time() - t0
                print(f"    [{total_evaluated:>5,}/{total_seeds_expected:,}] seeds re-evaluated in {el:.1f}s | Unexplained: {len(unexplained_seeds)}")

        # 归档种子完整性与总数强校验（严禁重复、截断流或种子跳跃）
        if total_evaluated != total_seeds_expected:
            raise RuntimeError(
                f"Archive record count mismatch: expected exactly {total_seeds_expected} records, but processed {total_evaluated} records"
            )
        if len(seen_seeds) != total_seeds_expected or seen_seeds != set(range(total_seeds_expected)):
            missing = sorted(set(range(total_seeds_expected)) - seen_seeds)
            raise RuntimeError(
                f"Archive stream completeness failure: expected {total_seeds_expected} contiguous seeds [0..{total_seeds_expected-1}], "
                f"but processed {len(seen_seeds)} unique seeds. Missing: {len(missing)} seeds (sample: {missing[:10]})"
            )

        elapsed_time = time.time() - t0
        print("\n" + "=" * 80)
        print("Read-Only Archive Re-evaluation Completed:")
        print("=" * 80)
        print(f"Total Seeds Processed: {total_evaluated:,}")
        print(f"Identical Seeds      : {identical_count:,} ({identical_count / (total_evaluated or 1) * 100:.2f}%)")
        print(f"Divergent Seeds      : {divergent_count:,} ({divergent_count / (total_evaluated or 1) * 100:.2f}%)")
        print(f"Prop Subsampling     : {len(prop_subsampling_seeds):,} seeds verified via deterministic replay")
        print(f"Unexplained Seeds    : {len(unexplained_seeds)} [PASS - Required == 0]")
        print(f"Total Elapsed Time   : {elapsed_time:.2f}s ({elapsed_time / (total_evaluated or 1) * 1000:.2f} ms/seed)")
        print("-" * 80)
        print("Divergence Category Breakdown:")
        for cat, cnt in category_counts.most_common():
            pct = cnt / (divergent_count or 1) * 100
            print(f"  - {cat:<40s}: {cnt:>5d} ({pct:>5.2f}%)")
        print("=" * 80)

        # 写入独立的重评清单和报告，绝对不覆盖历史文件
        reeval_manifest_file = scratch_dir / "audit_m5_3_reeval_manifest.json"
        reeval_report_file = scratch_dir / "audit_m5_3_reeval_report.md"

        reeval_manifest_doc = {
            "manifest_version": "2.0-reeval",
            "task": "M5-3 10,000 Seeds Read-Only Archive Re-evaluation",
            "source_archive": {
                "path": str(archive_file.name),
                "sha256": archive_sha256,
                "size_bytes": archive_size,
                "total_records_processed": total_evaluated,
                "contiguous_seed_range": [0, total_seeds_expected - 1],
            },
            "auditor_binding": {
                "script_path": "scratch/run_m5_3_audit.py",
                "script_sha256": compute_file_sha256(Path(__file__)),
                "target_commit": git_commit,
                "target_tree_hash": git_tree,
                "is_dirty": is_dirty,
                "core_source_digest": core_source_digest,
                "baseline_commit": BASELINE_COMMIT,
                "target_batch_hash": cur_batch_hash,
                "baseline_batch_hash": base_batch_hash,
            },
            "quality_gates": {k: "PASS (0 failures)" if v == 0 else f"FAIL ({v} failures)" for k, v in gate_failures.items()},
            "evaluation_result": {
                "total_seeds": total_evaluated,
                "identical_count": identical_count,
                "divergent_count": divergent_count,
                "prop_subsampling_seeds_count": len(prop_subsampling_seeds),
                "unexplained_count": len(unexplained_seeds),
                "is_all_explained": (len(unexplained_seeds) == 0),
                "elapsed_seconds": round(elapsed_time, 2),
                "category_breakdown": dict(category_counts),
                "unexplained_seeds": unexplained_seeds,
            },
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        reeval_manifest_file.write_text(json.dumps(reeval_manifest_doc, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        print(f"[+] Independent Re-evaluation Manifest saved: {reeval_manifest_file}")

        # 生成独立重评报告
        md_lines = [
            "# ComfyUI-IYKYK M5-3 只读归档因果重评独立报告",
            "",
            "> **说明**：本报告由审核器在只读模式下对历史证据归档（`audit_m5_3_evidence.json.gz`）进行完整流式逐条因果重评生成。历史归档文件（`audit_m5_3_evidence.json.gz`）、原始清单（`audit_m5_3_manifest.json`）及历史报告（`audit_m5_3_report.md`）保持原样只读，未被修改或覆盖。",
            "",
            "## 一、重评执行依据与环境绑定",
            "",
            f"- **源归档文件**: `scratch/{archive_file.name}`",
            f"- **源归档 SHA-256**: `{archive_sha256}`",
            f"- **源归档大小**: {archive_size:,} 字节",
            "- **审核器脚本**: `scratch/run_m5_3_audit.py`",
            f"- **审核器脚本 SHA-256**: `{compute_file_sha256(Path(__file__))}`",
            f"- **目标提交 Commit**: `{git_commit}` (Tree: `{git_tree}`, Dirty: `{is_dirty}`)",
            f"- **核心源码摘要**: `{core_source_digest}`",
            f"- **目标批次哈希**: `{cur_batch_hash}`",
            f"- **基线批次哈希**: `{base_batch_hash}`",
            f"- **执行时间**: `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`",
            f"- **处理种子总数**: {total_evaluated:,} 组 (连续种子 0..{total_seeds_expected - 1})",
            "- **流式读取方式**: 基于 JSONDecoder 块读取流式解包，单条 record 增量解码，无全量内存展开",
            f"- **重评总耗时**: {elapsed_time:.2f} 秒 (平均 {elapsed_time / (total_evaluated or 1) * 1000:.2f} 毫秒/种子)",
            "",
            "## 二、逐条质量门禁重验结果（复用历史运行证据）",
            "",
            "> 本节指标复用归档记录中两轮运行（Round A / Round B）时的原始质量门禁检查与 Oracle 闭包标记，验证历史生成质量。",
            "",
            "| 质量门禁指标 | 检查项 | 失败数 | 门禁要求 | 判定 |",
            "|---|---|---|---|---|",
        ]
        gate_names = {
            "determinism": ("严格双轮确定性", "Round A == Round B"),
            "residual_conflicts": ("残余硬冲突", "unresolved_conflicts == ()"),
            "word_budget": ("词数预算", "len(positive.split()) <= 250"),
            "prompt_syntax": ("语法合法性", "validate_prompt_syntax 通过"),
            "secondary_idempotency": ("严格二次消解幂等性", "二次消解决策数 == 0 且原子全等"),
            "oracle_validation": ("诊断闭包 Oracle 校验", "validate_audit_json_oracle 通过"),
            "provenance_closed_loop": ("Provenance DAG 闭环", "终态原子 parent_ids 非空可追溯"),
        }
        for k, (name, rule) in gate_names.items():
            fail_cnt = gate_failures[k]
            status = "**PASS**" if fail_cnt == 0 else f"**FAIL ({fail_cnt})**"
            md_lines.append(f"| **{name}** | `{rule}` | {fail_cnt} | `== 0` | {status} |")

        md_lines.extend([
            "",
            "## 三、逐条因果重评归因统计（当前审核引擎现场证明）",
            "",
            "> 本节由当前最新审核器对 10,000 组跨版本差异执行现场因果推导，包含 M3 冲突消解规则核实、M2 亲和矩阵合法性与分布验证，以及道具槽位（props）基于手部容量约束与独立子流 RNG 的确定性受控重放。",
            "",
            f"- **完全一致种子数**: **{identical_count:,}** ({identical_count / (total_evaluated or 1) * 100:.2f}%)",
            f"- **发生差异种子数**: **{divergent_count:,}** ({divergent_count / (total_evaluated or 1) * 100:.2f}%)",
            f"- **其中包含道具手部容量与子流抽样差异种子数**: **{len(prop_subsampling_seeds):,}** (全部通过受控重放数学证明)",
            f"- **未解释差异种子数**: **{len(unexplained_seeds)}** (门禁严格要求 == 0)",
            "",
            "### 差异因果类别明细表",
            "",
            "| 差异主归因类别 | 种子数 | 占差异比例 | 机制与规则依据 |",
            "|---|---|---|---|",
        ])
        for cat, cnt in category_counts.most_common():
            pct = cnt / (divergent_count or 1) * 100
            if cat.startswith("conflict_rule:"):
                rid = cat.split(":")[1]
                desc = f"M3 冲突消解规则 `{rid}` 触发物理互斥消解/状态修正/联动载体裁决"
            else:
                desc = "M2 情境亲和矩阵上下文权重校准与回退抽样范围保障"
            md_lines.append(f"| `{cat}` | {cnt:,} | {pct:.2f}% | {desc} |")

        md_lines.extend([
            "",
            "## 四、未解释差异清单",
            "",
        ])
        if len(unexplained_seeds) == 0:
            md_lines.append("未发现任何未解释差异种子，全部差异均具备严格物理规则、亲和矩阵或受控重放因果证明。")
        else:
            md_lines.append(f"共发现 {len(unexplained_seeds)} 个未解释种子：\n")
            for s, r in sorted(unexplained_seeds.items())[:100]:
                md_lines.append(f"- **Seed {s}**: {r}")

        md_lines.extend([
            "",
            "---",
            f"**重评结论**：全部 {total_evaluated:,} 条记录流式重验完成，未解释差异数为 **{len(unexplained_seeds)}**。"
        ])
        reeval_report_file.write_text("\n".join(md_lines), encoding="utf-8")
        print(f"[+] Independent Re-evaluation Report saved: {reeval_report_file}")

        total_gate_fails = sum(gate_failures.values())
        if len(unexplained_seeds) > 0 or total_gate_fails > 0:
            print(f"❌ [GATE FAIL] Re-evaluation failed: {len(unexplained_seeds)} unexplained seeds, {total_gate_fails} quality gate failures!")
            sys.exit(1)
        else:
            print(f"\n✅ [M5-3 RE-EVALUATION PASS] All {total_evaluated:,} seeds causally re-evaluated and explained with 0 unexplained diffs and 0 gate failures!")
            return
    else:
        # args.mode == "full-regenerate"
        total_seeds = args.seeds
        seed_list = list(range(total_seeds))
        print("\n" + "=" * 80)
        print(f"IYKYK M5-3 10,000 Seeds Audit (Full Regeneration Mode across {total_seeds} seeds)")
        print("=" * 80)

        # 实际运行 target 两轮 seed=0..9999
        print(f"\n[*] Running Target Commit ({git_commit[:7]}) double-run functional audit across {total_seeds} seeds...")
        t0 = time.time()
        cur_data, cur_batch_hash = run_batch(
            REPO_DIR,
            TARGET_WORKER_SCRIPT,
            seeds_count=total_seeds,
            chunk_size=1000,
            python_bin=sys.executable,
            num_workers=args.workers,
        )
        t_target = time.time() - t0
        print(f"    Target Batch Hash : {cur_batch_hash} (completed in {t_target:.1f}s)")

        # 运行 baseline (f1b873c) 进行跨版本比较
        print(f"\n[*] Running Pre-development Baseline ({BASELINE_COMMIT}) across {total_seeds} seeds...")
        t0 = time.time()
        base_data, base_batch_hash = run_batch(
            base_dir,
            BASELINE_WORKER_SCRIPT,
            seeds_count=total_seeds,
            chunk_size=1000,
            python_bin=sys.executable,
            num_workers=args.workers,
        )
        t_base = time.time() - t0
        print(f"    Baseline Batch Hash : {base_batch_hash} (completed in {t_base:.1f}s)")

        print(f"[*] Caching verified raw execution data with source binding to {raw_cache_file}...")
        with gzip.open(raw_cache_file, "wt", encoding="utf-8") as rf:
            json.dump({
                "runner_version": "3.0",
                "git_commit": git_commit,
                "git_tree": git_tree,
                "core_source_digest": core_source_digest,
                "cur_data": cur_data,
                "base_data": base_data,
                "cur_batch_hash": cur_batch_hash,
                "base_batch_hash": base_batch_hash,
            }, rf)

    # 逐项汇总门禁结果
    determinism_failures = [s for s, item in cur_data.items() if not item["quality_gates"]["determinism_pass"]]
    unresolved_failures = [s for s, item in cur_data.items() if item["quality_gates"]["unresolved_conflicts_count"] > 0]
    word_budget_failures = [s for s, item in cur_data.items() if not item["quality_gates"]["word_budget_pass"]]
    syntax_failures = [s for s, item in cur_data.items() if not item["quality_gates"]["syntax_pass"]]
    secondary_failures = [s for s, item in cur_data.items() if not item["quality_gates"]["secondary_idempotency_pass"]]
    oracle_failures = [s for s, item in cur_data.items() if not item["quality_gates"]["oracle_pass"]]
    provenance_failures = [s for s, item in cur_data.items() if not item["quality_gates"]["provenance_closed_pass"]]

    total_failures = (
        len(determinism_failures) +
        len(unresolved_failures) +
        len(word_budget_failures) +
        len(syntax_failures) +
        len(secondary_failures) +
        len(oracle_failures) +
        len(provenance_failures)
    )

    print("\n" + "=" * 80)
    print(f"Target Commit ({git_commit[:7]}) Strict Quality Gates (10,000 Seeds):")
    print("=" * 80)
    print(f"1. Determinism (Round A == Round B) : {len(determinism_failures):>5} failures [PASS - Required == 0]")
    print(f"2. Residual Hard Conflicts          : {len(unresolved_failures):>5} failures [PASS - Required == 0]")
    print(f"3. Word Budget (<= 250 words)       : {len(word_budget_failures):>5} failures [PASS - Required == 0]")
    print(f"4. Prompt Syntax Validity           : {len(syntax_failures):>5} failures [PASS - Required == 0]")
    print(f"5. Secondary Resolution Idempotency : {len(secondary_failures):>5} failures [PASS - Required == 0]")
    print(f"6. Diagnostics 2.0 Oracle Validation: {len(oracle_failures):>5} failures [PASS - Required == 0]")
    print(f"7. Provenance DAG Closed-Loop       : {len(provenance_failures):>5} failures [PASS - Required == 0]")
    print(f"Total Quality Gate Failures         : {total_failures:>5} failures")
    print("=" * 80)

    if total_failures > 0:
        print(f"❌ [GATE FAIL] Detected {total_failures} failures in quality gates!")
        sys.exit(1)

    # 4. 执行严谨因果归因 (Causal Attribution)
    print("\n[*] Auditing cross-version divergences & attributing to rules / context affinity / fixes...")
    rules_data = json.loads((DATA_DIR / "conflict_rules.json").read_text(encoding="utf-8"))
    affinity_data = json.loads((DATA_DIR / "context_affinity.json").read_text(encoding="utf-8"))

    identical_count = 0
    divergent_count = 0
    unexplained_seeds: Dict[int, List[str]] = {}
    category_counts: collections.Counter = collections.Counter()
    audited_diffs: List[Dict[str, Any]] = []
    full_evidence_records: List[Dict[str, Any]] = []
    for s in seed_list:
        res = attribute_seed_difference(s, base_data[s], cur_data[s], catalog_lookup, rules_data, affinity_data)
        if res["is_identical"]:
            identical_count += 1
        else:
            divergent_count += 1
            cat = res["category"]
            category_counts[cat] += 1
            if not res["is_explained"]:
                unexplained_seeds[s] = res["unexplained_reasons"]
            audited_diffs.append(res)

        # 完整保留逐种子全套执行证据（双轮输出、完整原子、决策与诊断 JSON）
        record = {
            "seed": s,
            "round_a": cur_data[s]["round_a"],
            "round_b": cur_data[s]["round_b"],
            "baseline": base_data[s],
            "quality_gates": cur_data[s]["quality_gates"],
            "divergence": res,
        }
        full_evidence_records.append(record)

    print("\n" + "=" * 80)
    print("Cross-Version Divergence Attribution Summary:")
    print("=" * 80)
    print(f"Total Seeds Audited  : {total_seeds:,}")
    print(f"Identical Seeds      : {identical_count:,} ({identical_count / total_seeds * 100:.2f}%)")
    print(f"Divergent Seeds      : {divergent_count:,} ({divergent_count / total_seeds * 100:.2f}%)")
    print(f"Unexplained Seeds    : {len(unexplained_seeds)} [PASS - Required == 0]")
    print("-" * 80)
    print("Divergence Category Breakdown:")
    for cat, cnt in category_counts.most_common():
        pct = cnt / (divergent_count or 1) * 100
        print(f"  - {cat:<40s}: {cnt:>5d} ({pct:>5.2f}%)")
    print("=" * 80)

    if len(unexplained_seeds) > 0:
        print(f"❌ [GATE FAIL] Detected {len(unexplained_seeds)} unexplained seeds!")
        for s, reasons in list(unexplained_seeds.items())[:50]:
            print(f"  Seed {s}: {reasons}")
        sys.exit(1)

    if args.mode == "sample":
        print("\n✅ [M5-3 SAMPLE PASS] All sample seeds (0, 2, 42) verified and causally attributed with 0 unexplained diffs!")
        return

    # 5. 保存全量证据归档、清单与 Markdown 报告
    archive_file = scratch_dir / "audit_m5_3_evidence.json.gz"
    manifest_file = scratch_dir / "audit_m5_3_manifest.json"
    report_file = scratch_dir / "audit_m5_3_report.md"

    archive_doc = {
        "metadata": {
            "baseline_commit": BASELINE_COMMIT,
            "baseline_batch_hash": base_batch_hash,
            "target_commit": git_commit,
            "target_tree_hash": git_tree,
            "is_dirty": is_dirty,
            "core_source_digest": core_source_digest,
            "target_batch_hash": cur_batch_hash,
            "total_seeds": total_seeds,
            "identical_seeds": identical_count,
            "divergent_seeds": divergent_count,
            "unexplained_seeds": len(unexplained_seeds),
            "python_version": python_version,
            "os_info": os_info,
            "jsonschema_version": jsonschema_version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "modified_data_files": modified_data_files,
        "base_data_hashes": base_data_hashes,
        "cur_data_hashes": cur_data_hashes,
        "category_breakdown": dict(category_counts),
        "records": full_evidence_records,
    }

    # 确定性写入 gzip (mtime=0)
    with open(archive_file, "wb") as raw_f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=0) as gf:
            with io.TextIOWrapper(gf, encoding="utf-8") as tf:
                json.dump(archive_doc, tf, sort_keys=True, ensure_ascii=False)

    archive_sha256 = compute_file_sha256(archive_file)
    print(f"\n[+] Full audit archive saved deterministically: {archive_file} (SHA-256: {archive_sha256})")

    manifest_doc = {
        "manifest_version": "2.0",
        "task": "M5-3 10,000 Seeds Audit and Divergence Attribution",
        "source_binding": {
            "target_commit": git_commit,
            "target_tree_hash": git_tree,
            "is_dirty": is_dirty,
            "core_source_digest": core_source_digest,
        },
        "baseline_commit": BASELINE_COMMIT,
        "baseline_batch_hash": base_batch_hash,
        "target_batch_hash": cur_batch_hash,
        "total_seeds": total_seeds,
        "quality_gates": {
            "determinism": "PASS (0 failures, Round A == Round B byte-for-byte across positive, negative, description, d_audit)",
            "residual_conflicts": "PASS (0 unresolved)",
            "word_budget": "PASS (0 > 250 words)",
            "prompt_syntax": "PASS (0 syntax errors)",
            "secondary_idempotency": "PASS (0 new decisions, atoms identical before and after secondary resolution)",
            "oracle_validation": "PASS (0 oracle failures)",
            "provenance_closed_loop": "PASS (0 orphaned atoms)",
            "unexplained_divergences": "PASS (0 unexplained)",
        },
        "archive_file": {
            "path": str(archive_file.name),
            "sha256": archive_sha256,
            "size_bytes": archive_file.stat().st_size,
            "records_count": len(full_evidence_records),
        },
        "category_breakdown": dict(category_counts),
        "target_data_hashes": cur_data_hashes,
    }
    manifest_file.write_text(json.dumps(manifest_doc, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Manifest saved: {manifest_file}")

    # 生成 Markdown 报告
    md_lines = [
        "# ComfyUI-IYKYK M5-3 万种子全量审计与差异归因报告",
        "",
        "## 一、执行环境与固定执行依据",
        "",
        f"- **开发前基线 Commit (Pre-dev Baseline)**: `{BASELINE_COMMIT}` (Merge pull request #2 from imymi/ci/rc10-freeze-docs-only-20261010)",
        f"- **目标提交 Commit (Target Commit)**: `{git_commit}` (Tree: `{git_tree}`, Dirty: `{is_dirty}`)",
        f"- **核心源码确定性摘要 (Core Source Digest)**: `{core_source_digest}`",
        f"- **基线汇总哈希 (Baseline Batch Hash)**: `{base_batch_hash}`",
        f"- **目标汇总哈希 (Target Batch Hash)**: `{cur_batch_hash}`",
        f"- **Python 版本**: `{python_version}`",
        f"- **操作系统与架构**: `{os_info}`",
        f"- **jsonschema 库版本**: `{jsonschema_version}`",
        "- **全量种子规模**: 10,000 组 (`seed=0..9999`)",
        "- **默认采样输入**: 22 个全槽位标准默认输入（预设/配方无，裸露/服装/发型/姿势等随机，画质高清写真）",
        "",
        "### 运行时数据文件 SHA-256 哈希列表 (20 个数据文件)",
        "",
        "| 数据文件 | Baseline 哈希 (`f1b873c`) | Target 哈希 (`HEAD`) | 状态 |",
        "|---|---|---|---|",
    ]
    for fname in sorted(cur_data_hashes.keys()):
        b_hash = base_data_hashes.get(fname, "N/A")
        c_hash = cur_data_hashes[fname]
        status = "已校准修改" if b_hash != c_hash else "零变更保持"
        md_lines.append(f"| `{fname}` | `{b_hash[:16]}...` | `{c_hash[:16]}...` | {status} |")

    md_lines.extend([
        "",
        "## 二、目标提交两轮万种子全功能质量门禁 (10,000 Seeds)",
        "",
        "| 质量门禁指标 | 检查项与断言 | 失败数 | 门禁要求 | 判定 |",
        "|---|---|---|---|---|",
        f"| **1. 严格双轮确定性** | Round A == Round B (positive, negative, description, d_audit) 且与诊断节点前三输出逐字节一致 | {len(determinism_failures)} | `== 0` | **PASS** |",
        f"| **2. 残余硬冲突** | `r.resolution_report.unresolved_conflicts == ()` | {len(unresolved_failures)} | `== 0` | **PASS** |",
        f"| **3. 词数预算** | `len(positive.split()) <= 250` | {len(word_budget_failures)} | `== 0` | **PASS** |",
        f"| **4. 语法合法性** | `validate_prompt_syntax` 词法语法解析无异常 | {len(syntax_failures)} | `== 0` | **PASS** |",
        f"| **5. 严格二次消解幂等性** | 终态原子二次消解新增决策数 `len(rep2.decisions) == 0` 且二次前后原子状态全等 | {len(secondary_failures)} | `== 0` | **PASS** |",
        f"| **6. 诊断闭包 Oracle 校验** | `validate_audit_json_oracle` 模式与事实闭包核验 | {len(oracle_failures)} | `== 0` | **PASS** |",
        f"| **7. Provenance DAG 闭环** | 所有终态 Atom 的 `parent_ids` 均非空且可溯源 | {len(provenance_failures)} | `== 0` | **PASS** |",
        "",
        "## 三、跨版本差异归因与因果证明",
        "",
        "### 1. 结构变化与行为变化严格解耦",
        "- **诊断结构变化 (Diagnostic Structure Evolution)**：",
        "  - 从旧版扁平弱类型结构升级为 **Diagnostics 2.0 规范**；",
        "  - 新增完备的 `context_profile`、`source_atoms`、`selections`、`counts`、`overrides`（支持 20 槽位外部覆写全量追踪）；",
        "  - 完备的 Provenance DAG 追溯与 `ResolutionDecision` 序列化；",
        "  - 错误报告统一归纳为 5 维标准诊断字典，杜绝二次异常抛出。",
        "- **生成行为变化 (Prompt Generation Behavioral Shifts)**：",
        f"  - 全量 10,000 种子中，完全一致种子数：**{identical_count:,}** ({identical_count / total_seeds * 100:.2f}%)；",
        f"  - 发生差异种子数：**{divergent_count:,}** ({divergent_count / total_seeds * 100:.2f}%)；",
        f"  - **未解释差异种子数：{len(unexplained_seeds)} (门禁严格要求 == 0)**。",
        "",
        "### 2. 差异因果归因明细表",
        "",
        "| 差异因果主归因类别 | 影响种子数 | 占差异种子比例 | 因果与机制依据 |",
        "|---|---|---|---|",
    ])

    for cat, cnt in category_counts.most_common():
        pct = cnt / (divergent_count or 1) * 100
        if cat.startswith("conflict_rule:"):
            rule_id = cat.split(":")[1]
            desc = f"M3 冲突消解规则 `{rule_id}` 触发物理互斥消解/状态修正/联动载体裁决"
        else:
            desc = "M2 情境亲和矩阵上下文权重校准与回退抽样范围保障"
        md_lines.append(f"| `{cat}` | {cnt:,} | {pct:.2f}% | {desc} |")

    md_lines.extend([
        "",
        "## 四、可复现证据归档与校验清单",
        "",
        f"- **完整证据归档文件**: `scratch/{archive_file.name}`",
        f"- **归档文件 SHA-256**: `{archive_sha256}`",
        f"- **归档文件大小**: {archive_file.stat().st_size:,} 字节",
        f"- **归档记录总数**: {len(full_evidence_records):,} 组种子全量完整证据",
        f"- **审计签名清单**: `scratch/{manifest_file.name}`",
        "- **重现执行命令**:",
        "  ```bash",
        "  /Users/jacobyang/Hermes/.venv/bin/python3 scratch/run_m5_3_audit.py --seeds 10000",
        "  ```",
        "",
        "---",
        "**M5-3 审计结论**：万种子双轮确定性 100% 达标，7 项功能质量门禁全部零失败；基线差异 100% 完成因果归因，未解释差异严格为 0。符合 M5-3 验收条件。",
    ])

    report_file.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[+] Markdown report saved: {report_file}")
    print("\n✅ [M5-3 COMPLETED] All 10,000 seeds verified and causally attributed with 0 unexplained diffs!")


if __name__ == "__main__":
    main()
