#!/usr/bin/env python3
"""
scratch/audit_step3_cross_catalog.py
第 3 步增量双版本差异因果归因审计器：以 2df289a 为增量基线，逐种子、逐原子因果归因

核心规范：
1. 以 2df289a（第 2 步收尾提交，黄金哈希 3e1291ae…）为增量基线；
2. 全量 10,000 种子（0..9999）结构化签名与序列核验；
3. 文本相同的种子也必须执行结构与来源检查；
4. 统一来源签名核验入口：比较 ID、文本、槽位、条目 ID、tag/span 序数；
5. 统一决策合法性核验入口：严格核验证券，目标必须为缺席状态，winner 必须为在穿内衣主体，绝不允许构图原子被误删或配饰充当内衣 winner；
6. 逐原子差异因果证明：每个增删终态原子必须拥有独立证明，严禁全种子的模糊分类兜底；
7. 权威叶子词条校验与逆向无决策消失（silent drop）拦截；
8. 未变更槽位零突变：除允许变更槽位外，其余 15 个槽位完整有序原子签名列表 100% 严格一致；
9. 归档保存全部差异种子的双版本 source_atoms, final_atoms, decisions, carrier_bindings 及逐原子证明链至 scratch/audit_step3_evidence.json.gz。
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import gzip
import hashlib
import json
import os
from pathlib import Path
from random import Random
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
sys.path.insert(0, str(REPO_DIR))

from lib.models import (  # noqa: E402  # REPO_DIR inserted into sys.path before importing local project modules
    ContextProfile,
    PromptAtom, SpanType, TagProvenance, SelectionOrigin, SemanticFacts,
    VALID_GARMENT_TOPOLOGIES, VALID_GARMENT_STATES, VALID_VISIBLE_REGIONS, VALID_PROP_USAGES,
)
from lib.conflict_resolver import (  # noqa: E402  # REPO_DIR inserted into sys.path before importing local project modules
    ConflictResolver,
    normalize_slot_name,
    build_garment_entity_key,
    find_bound_carrier,
    is_garment_modifier_atom,
    BindingStatus,
    GarmentCarrierEntity,
)

BASELINE_COMMIT = "2df289a"
EXPECTED_BASELINE_HASH = "3e1291ae60af887ebde1869a8fb60f7937424198c1db6a4856bb90aa954725fc"

NEW_JEWELRY_IDS = frozenset({
    "neck_ribbon", "hooded_cloak", "cloak", "poncho", "waist_belt", "winter_scarf", "fur_shawl"
})
OUTERWEAR_JEWELRY_IDS = frozenset({
    "hooded_cloak", "cloak", "poncho", "fur_shawl"
})
NEW_LINGERIE_IDS = frozenset({
    "crotchless_panties", "basic_underwear"
})
NEW_IMPERFECTION_IDS = frozenset({
    "tan_lines"
})

ALLOWED_DIFF_SLOTS = frozenset({
    "jewelry", "imperfections", "clothing", "clothing_state", "clothing_extension", "linkage"
})

WORKER_SCRIPT = """
import sys
import json
import hashlib
from pathlib import Path

repo_path = sys.argv[1]
start_seed = int(sys.argv[2])
count = int(sys.argv[3])
out_file = sys.argv[4]

sys.path.insert(0, repo_path)
from dataclasses import asdict
import nodes
from lib.conflict_resolver import extract_garment_entities, find_bound_carrier, is_garment_modifier_atom

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

g = nodes.NODE_CLASS_MAPPINGS["IYKYKPromptGenerator"]()

def atom_dict(a):
    return {
        "atom_id": a.atom_id,
        "text": a.text,
        "span_type": a.span_type.value if hasattr(a.span_type, "value") else str(a.span_type),
        "source_slot": a.source_slot,
        "source_item_id": a.source_item_id or (a.provenance.item_id if a.provenance else "") or "",
        "tag_order": a.tag_order,
        "span_order": a.span_order,
        "target_id": a.target_id,
        "provenance": asdict(a.provenance) if a.provenance else None,
        "facts": asdict(a.facts) if a.facts else None,
        "origin": asdict(a.origin) if a.origin else None,
        "id": a.id,
        "is_worn": getattr(a, "is_worn", True),
        "is_ambient": getattr(a, "is_ambient", False),
        "provenance_kind": a.provenance.kind if a.provenance else None,
        "parent_ids": list(a.provenance.parent_ids) if a.provenance else [],
        "visible_regions": list(a.facts.visible_regions) if a.facts else [],
        "garment_topologies": list(a.facts.garment_topologies) if a.facts else [],
        "garment_states": list(a.facts.garment_states) if a.facts else [],
        "prop_usage": a.facts.prop_usage if a.facts else None,
    }

def dec_dict(d):
    return {
        "decision_id": d.decision_id,
        "sequence": d.sequence,
        "rule_id": d.rule_id,
        "reason_code": d.reason_code,
        "action": d.action,
        "target_atom_id": d.target_atom_id,
        "winner_atom_ids": list(d.winner_atom_ids),
        "produced_atom_ids": list(d.produced_atom_ids),
        "parent_source_ids": list(d.parent_source_ids),
        "before_text": d.before_text,
        "after_text": d.after_text,
    }

res = []
for s in range(start_seed, start_seed + count):
    r = g.generate_structured(**inputs, prompt_seed=s)
    h = hashlib.sha256(r.positive.encode("utf-8")).hexdigest()

    entities = extract_garment_entities(r.atoms)
    worn = [e for e in entities.values() if e.is_worn and not e.is_ambient]
    carrier_bindings = {}
    for a in r.atoms:
        if is_garment_modifier_atom(a):
            b = find_bound_carrier(a, worn)
            if b.status.name == "BOUND" and b.target_entity:
                carrier_bindings[a.atom_id] = {
                    "carrier_entity_id": b.target_entity.entity_id,
                    "carrier_selected_id": b.target_entity.selected_id,
                    "carrier_member_atom_ids": [m.atom_id for m in b.target_entity.member_atoms],
                    "carrier_member_texts": [m.text for m in b.target_entity.member_atoms],
                }

    dedup_recs = [
        {
            "atom_id": rec.atom_id,
            "retained_atom_id": rec.retained_atom_id,
            "retained_tag_text": rec.retained_tag_text,
            "basis": rec.basis,
        }
        for rec in (r.deduplication_records or ())
    ]

    budget_recs = [
        {
            "atom_id": rec.atom_id,
            "reason": rec.reason,
        }
        for rec in (r.budget_filter_records or ())
    ]

    res.append({
        "seed": s,
        "positive": r.positive,
        "hash": h,
        "final_atoms": [atom_dict(a) for a in r.atoms],
        "source_atoms": [atom_dict(a) for a in r.source_atoms],
        "decisions": [dec_dict(d) for d in r.resolution_report.decisions] if r.resolution_report else [],
        "carrier_bindings": carrier_bindings,
        "dedup_records": dedup_recs,
        "budget_records": budget_recs,
    })

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(res, f)
"""


def export_commit(commit_id: str, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    if not (target_dir / "nodes.py").exists():
        print(f"[*] Exporting git baseline {commit_id} to {target_dir}...")
        tar_proc = subprocess.Popen(["tar", "-x", "-C", str(target_dir)], stdin=subprocess.PIPE)
        subprocess.run(["git", "archive", commit_id], stdout=tar_proc.stdin, cwd=str(REPO_DIR), check=True)
        tar_proc.communicate()
        if tar_proc.returncode != 0:
            raise RuntimeError(f"tar extraction failed for {commit_id}")


def run_batch_parallel(
    repo_path: Path, seeds_count: int, chunk_size: int = 1000, python_bin: str = sys.executable
) -> Tuple[Dict[int, Dict[str, Any]], str]:
    num_chunks = (seeds_count + chunk_size - 1) // chunk_size
    temp_dir = Path(tempfile.mkdtemp(prefix="iykyk_step3_batch_"))
    worker_py = temp_dir / "worker.py"
    worker_py.write_text(WORKER_SCRIPT, encoding="utf-8")

    futures = []
    num_workers = min(8, os.cpu_count() or 4)
    with concurrent.futures.ProcessPoolExecutor(max_workers=num_workers) as pool:
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
        ordered_hashes.append(item["hash"])

    batch_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()
    return all_data, batch_hash


def atom_signature(a: Dict[str, Any]) -> Tuple[Any, ...]:
    return (
        a["atom_id"],
        a["text"],
        a["source_slot"],
        a.get("source_item_id", ""),
        a.get("tag_order", 0),
        a.get("span_order", 0),
    )


def slot_atom_signature(a: Dict[str, Any]) -> Tuple[Any, ...]:
    """槽位内原子的语义签名（不受跨槽位全局 tag_order 偏移影响）"""
    return (
        a["source_slot"],
        a.get("source_item_id", ""),
        a["text"],
        a.get("span_order", 0),
    )


def load_authoritative_catalog_leaf_tags(data_dir: Path) -> Dict[str, Dict[str, Set[str]]]:
    """
    加载权威词库的合法叶子词条文本集合（逐原子精确匹配）。
    """
    from lib.lexer import parse_prompt

    acc_doc = json.loads((data_dir / "accessories.json").read_text(encoding="utf-8"))
    jewelry_tags: Dict[str, Set[str]] = {}
    for item in acc_doc.get("headwear_jewelry", []):
        iid = item["id"]
        jewelry_tags[iid] = set()
        for t in item.get("tags", []):
            raw = t if isinstance(t, str) else (t.get("text", "") if isinstance(t, dict) else "")
            if not raw:
                continue
            jewelry_tags[iid].add(raw)
            for tag in parse_prompt(raw).tags:
                for sp in tag.spans:
                    if sp.text:
                        jewelry_tags[iid].add(sp.text)

    imp_doc = json.loads((data_dir / "imperfections.json").read_text(encoding="utf-8"))
    imp_tags: Dict[str, Set[str]] = {}
    for item in imp_doc.get("categories", []):
        iid = item["id"]
        imp_tags[iid] = set()
        for t in item.get("tags", []):
            raw = t if isinstance(t, str) else (t.get("text", "") if isinstance(t, dict) else "")
            if not raw:
                continue
            imp_tags[iid].add(raw)
            for tag in parse_prompt(raw).tags:
                for sp in tag.spans:
                    if sp.text:
                        imp_tags[iid].add(sp.text)

    clo_doc = json.loads((data_dir / "clothing.json").read_text(encoding="utf-8"))
    clothing_tags: Dict[str, Set[str]] = {}

    def add_clothing_tags(item_id: str, tag_list: Any):
        if not item_id:
            return
        if item_id not in clothing_tags:
            clothing_tags[item_id] = set()
        for t in tag_list:
            raw = t if isinstance(t, str) else (t.get("text", "") if isinstance(t, dict) else "")
            if not raw:
                continue
            clothing_tags[item_id].add(raw)
            for tag in parse_prompt(raw).tags:
                for sp in tag.spans:
                    if sp.text:
                        clothing_tags[item_id].add(sp.text)

    for sec in ("categories", "clothing_states", "lingerie_wardrobe", "sfw_exposure_tiers", "cloth_transparency_tiers"):
        for it in clo_doc.get(sec, []):
            add_clothing_tags(it.get("id"), it.get("tags", []))

    linkage = clo_doc.get("clothing_nudity_linkage", {})
    for lvl, ldata in linkage.items():
        so = ldata.get("style_overrides", {})
        for sid, tags in so.items():
            add_clothing_tags(sid, tags)
            add_clothing_tags("auto_linkage", tags)
        gt = ldata.get("general_tags", [])
        add_clothing_tags("linkage_general", gt)
        add_clothing_tags("auto_linkage", gt)

    return {
        "jewelry": jewelry_tags,
        "imperfections": imp_tags,
        "clothing": clothing_tags,
    }


def load_authoritative_resolver_rules(data_dir: Path) -> Set[str]:
    """加载权威冲突消解规则 ID 白名单集合"""
    rules_doc = json.loads((data_dir / "conflict_rules.json").read_text(encoding="utf-8"))
    rules = {r["id"] for r in rules_doc.get("rules", [])}
    rules.add("absence_state_conflict")
    rules.add("full_body_framing_clothing_conflict")
    rules.add("state_lacks_carrier")
    rules.add("garment_carrier_binding")
    return rules


# ─────────────────────────────────────────────────────────────
# 统一来源签名核验入口 (Unified Atom Source Signature Verification)
# ─────────────────────────────────────────────────────────────
def verify_atom_source_signature(
    atom: Dict[str, Any],
    source_atom: Dict[str, Any],
) -> List[str]:
    """
    统一来源签名核验入口：比较 ID、文本、槽位、条目 ID、tag/span 序数。
    任何字段发生漂移立即返回具体原因。
    """
    errors: List[str] = []
    aid = atom.get("atom_id")
    if atom.get("atom_id") != source_atom.get("atom_id"):
        errors.append(
            f"UNEXPLAINED_FIELD_DRIFT(atom_id): final='{atom.get('atom_id')}' != src='{source_atom.get('atom_id')}'"
        )
    if atom.get("text") != source_atom.get("text"):
        errors.append(
            f"UNEXPLAINED_TAMPERED_RESTORED_ATOM(text): Atom '{aid}' final text '{atom.get('text')}' != src text '{source_atom.get('text')}'"
        )
    if atom.get("source_slot") != source_atom.get("source_slot"):
        errors.append(
            f"UNEXPLAINED_FIELD_DRIFT(source_slot): Atom '{aid}' final slot '{atom.get('source_slot')}' != src slot '{source_atom.get('source_slot')}'"
        )
    if atom.get("source_item_id") != source_atom.get("source_item_id"):
        errors.append(
            f"UNEXPLAINED_FIELD_DRIFT(source_item_id): Atom '{aid}' final item_id '{atom.get('source_item_id')}' != src item_id '{source_atom.get('source_item_id')}'"
        )
    if atom.get("tag_order") != source_atom.get("tag_order"):
        errors.append(
            f"UNEXPLAINED_ORDER_FIELD_DRIFT(tag_order): Atom '{aid}' final tag_order={atom.get('tag_order')} != src tag_order={source_atom.get('tag_order')}"
        )
    if atom.get("span_order") != source_atom.get("span_order"):
        errors.append(
            f"UNEXPLAINED_ORDER_FIELD_DRIFT(span_order): Atom '{aid}' final span_order={atom.get('span_order')} != src span_order={source_atom.get('span_order')}"
        )
    return errors


# ─────────────────────────────────────────────────────────────
# 权威领域实体与在穿状态判定定义 (Authoritative Entity & In-Wear Definitions)
# ─────────────────────────────────────────────────────────────
NON_GARMENT_DOMAINS: Set[str] = {
    "hairstyle", "hair", "makeup", "accessory", "accessories",
    "pose", "emotion", "expression", "lighting", "shot", "shot_type",
    "composition", "camera", "camera_angle", "context",
    "scene_category", "scene_theme", "theme", "shot_framing",
    "film_grain", "liquid", "tattoo", "props", "character", "imperfections",
    "quality", "preset", "recipe", "nudity", "nudity_level", "jewelry",
}

CANONICAL_ABSENCE_STATES: Set[str] = {"braless", "underwearless"}

BRA_ENTITIES: Set[str] = {
    "basic_underwear", "lingerie_lace", "bikini_classic", "bikini_strappy", "bikini_micro"
}

PANTIES_ENTITIES: Set[str] = {
    "basic_underwear", "crotchless_panties", "bikini_classic", "bikini_strappy", "bikini_micro"
}

RULE_ALLOWED_CONTRACTS: Dict[str, Dict[str, Set[str]]] = {
    "spatial_environmental_mutual_exclusion": {
        "drop": {"indoor_outdoor_mutex", "venue_cluster_mutex"},
        "replace": {"deprecated_tag_replaced"},
    },
    "nudity_clothing_conflicts": {
        "drop": {"nudity_removes_clothing", "nudity_removes_underwear"},
    },
    "framing_lower_body_coherence": {
        "drop": {"close_up_removes_lower_body"},
    },
    "pose_hand_occupation": {
        "drop": {"busy_hands_remove_props"},
    },
    "handheld_props_single_holder": {
        "drop": {"single_handheld_prop_limit"},
    },
    "clothing_style_state_coherence": {
        "drop": {
            "one_piece_state_conflict",
            "pants_state_conflict",
            "state_lacks_carrier",
            "layering_mismatch",
            "absence_state_conflict",
            "no_compatible_carrier",
            "target_incompatible",
        },
    },
    "material_penetration": {
        "replace": {"material_penetration_replaced"},
        "drop": {"material_penetration_removed"},
    },
    "device_quality_compatibility": {
        "drop": {"device_removes_conflicting_quality"},
    },
    "environmental_lighting_coherence": {
        "drop": {"night_scene_removes_daylight", "daylight_removes_night"},
    },
    "monochrome_film_chroma_coherence": {
        "drop": {"monochrome_film_removes_chroma"},
    },
    "makeup_details_coherence": {
        "drop": {"clean_base_removes_heavy_makeup"},
    },
    "gaze_angle_geometry": {
        "drop": {"camera_angle_removes_impossible_gaze"},
    },
    "accessory_occlusion_gaze_coherence": {
        "drop": {"eye_occlusion_removes_gaze"},
    },
    "emotion_gaze_affinity": {
        "drop": {"emotion_removes_conflicting_gaze"},
    },
    "gaze_mutual_exclusion": {
        "drop": {"gaze_mutual_exclusion_preserved_first"},
    },
    "liquid_restrictions": {
        "replace": {"liquid_combo_replaced", "liquid_quantifier_added"},
    },
    "tattoo_dermal_fusion": {
        "inject": {"tattoo_dermal_fusion_injected"},
    },
    # 兼容直接使用子规则 / 别名 ID
    "absence_state_conflict": {
        "drop": {"absence_state_conflict"},
    },
    "state_lacks_carrier": {
        "drop": {"state_lacks_carrier", "no_compatible_carrier", "target_incompatible"},
    },
    "garment_carrier_binding": {
        "drop": {"state_lacks_carrier", "no_compatible_carrier", "target_incompatible", "absence_state_conflict"},
    },
    "full_body_framing_clothing_conflict": {
        "drop": {"full_body_framing_clothing_conflict"},
    },
}

RULE_TARGET_DOMAINS: Dict[str, Set[str]] = {
    "spatial_environmental_mutual_exclusion": {
        "scene_category", "scene_theme", "theme", "context", "spatial", "environment", "venue",
    },
    # nudity_clothing_conflicts 属于锚点层跨全域冲突消解，可作用于违反裸露等级的各槽位原子
    "framing_lower_body_coherence": {
        "clothing", "clothing_state", "clothing_extension", "lingerie", "base_clothing", "shoes", "socks", "legs", "lower_body",
    },
    "pose_hand_occupation": {
        "props", "handheld_props", "prop",
    },
    "handheld_props_single_holder": {
        "props", "handheld_props", "prop",
    },
    "clothing_style_state_coherence": {
        "clothing_state", "clothing", "clothing_extension", "lingerie",
    },
    "material_penetration": {
        "clothing", "clothing_state", "clothing_extension", "lingerie",
    },
    "device_quality_compatibility": {
        "quality", "quality_level", "device",
    },
    "environmental_lighting_coherence": {
        "lighting", "environmental_lighting", "lighting_preset", "lighting_palette",
    },
    "monochrome_film_chroma_coherence": {
        "film_style", "film_grain", "chroma", "color",
    },
    "makeup_details_coherence": {
        "makeup", "makeup_details",
    },
    "gaze_angle_geometry": {
        "gaze", "expression",
    },
    "accessory_occlusion_gaze_coherence": {
        "gaze", "expression",
    },
    "emotion_gaze_affinity": {
        "gaze", "expression",
    },
    "gaze_mutual_exclusion": {
        "gaze", "expression",
    },
    "liquid_restrictions": {
        "liquid", "liquid_effect",
    },
    "tattoo_dermal_fusion": {
        "tattoo", "clothing_extension",
    },
    "absence_state_conflict": {
        "clothing_state", "clothing",
    },
    "state_lacks_carrier": {
        "clothing_state", "clothing", "clothing_extension",
    },
    "garment_carrier_binding": {
        "clothing_state", "clothing", "clothing_extension",
    },
    "full_body_framing_clothing_conflict": {
        "clothing", "shot", "shot_type", "shot_framing",
    },
}


_CATALOG_TEXT_LOOKUP: Optional[Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]]] = None

def get_catalog_text_lookup(data_dir: Path | str = DATA_DIR) -> Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]]:
    """单次缓存构建 (norm_slot, lower_text) -> (leaf_id, facts_dict) 权威词条逆向索引"""
    global _CATALOG_TEXT_LOOKUP
    if _CATALOG_TEXT_LOOKUP is not None:
        return _CATALOG_TEXT_LOOKUP
    lookup: Dict[Tuple[str, str], Tuple[str, Dict[str, Any]]] = {}
    data_dir = Path(data_dir)
    file_map = {
        "accessories.json": ["jewelry", "accessories"],
        "characters.json": ["character"],
        "clothing.json": ["clothing", "clothing_state", "clothing_extension", "underwear"],
        "expressions.json": ["expression", "expressions"],
        "film_stocks.json": ["film", "film_stock"],
        "imperfections.json": ["imperfections"],
        "lighting.json": ["lighting", "lighting_palette"],
        "makeup.json": ["makeup"],
        "nudity_levels.json": ["nudity", "liquids", "liquid"],
        "poses.json": ["pose", "poses"],
        "props.json": ["props", "prop"],
        "scenes.json": ["scene_theme", "scene", "theme"],
        "shot_types.json": ["shot_type", "shot", "camera_angle", "camera", "angle", "view"],
        "style_recipes.json": ["style_recipe", "recipe"],
        "tattoos.json": ["tattoo", "tattoos"],
        "themes.json": ["scene_theme", "scene", "theme"],
    }
    for fname, slots in file_map.items():
        p = data_dir / fname
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        def walk(obj, current_slot=None):
            if isinstance(obj, dict):
                s_override = obj.get("slot", current_slot)
                if "text" in obj and "id" in obj and isinstance(obj["text"], str) and isinstance(obj["id"], str):
                    txt = obj["text"].strip().lower()
                    target_slots = [s_override] if s_override else slots
                    for s in target_slots:
                        norm_s = normalize_slot_name(s)
                        lookup[(norm_s, txt)] = (obj["id"], obj.get("facts", {}))
                for v in obj.values():
                    walk(v, s_override)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item, current_slot)
        walk(data)
    _CATALOG_TEXT_LOOKUP = lookup
    return _CATALOG_TEXT_LOOKUP


def dict_to_prompt_atom(d: Dict[str, Any] | PromptAtom) -> PromptAtom:
    """将字典格式原子准确还原为强类型 PromptAtom 实例以复用生产级消解流水线与谓词"""
    if isinstance(d, PromptAtom):
        return d
    aid = d.get("atom_id") or d.get("id") or ""
    text = d.get("text") or ""
    slot = (d.get("source_slot") or "").strip()
    item_id = d.get("source_item_id") or ""
    tag_order = int(d.get("tag_order", 0))
    span_order = int(d.get("span_order", 0))
    target_id = d.get("target_id")

    f_d = d.get("facts")
    if isinstance(f_d, dict):
        facts = SemanticFacts(**{k: tuple(v) if isinstance(v, list) else v for k, v in f_d.items()})
    else:
        topos = tuple(t for t in (d.get("garment_topologies") or ()) if t in VALID_GARMENT_TOPOLOGIES)
        states = tuple(s for s in (d.get("garment_states") or ()) if s in VALID_GARMENT_STATES)
        regions = tuple(r for r in (d.get("visible_regions") or ()) if r in VALID_VISIBLE_REGIONS)
        pu = d.get("prop_usage")
        prop_usage = pu if pu in VALID_PROP_USAGES else None
        facts = SemanticFacts(
            visible_regions=regions,
            garment_topologies=topos,
            garment_states=states,
            prop_usage=prop_usage,
        )

    p_d = d.get("provenance")
    if isinstance(p_d, dict):
        prov = TagProvenance(**{k: tuple(v) if isinstance(v, list) else v for k, v in p_d.items()})
    else:
        parent_ids = tuple(d.get("parent_ids") or ())
        prov_kind = d.get("provenance_kind") or d.get("kind")
        if not prov_kind:
            if item_id in ("auto_linkage", "linkage_general") or any("linkage" in p for p in parent_ids):
                prov_kind = "clothing_state"
            else:
                prov_kind = slot
        prov = TagProvenance(
            parent_ids=parent_ids,
            item_id=item_id,
            kind=prov_kind,
        )

    o_d = d.get("origin")
    if isinstance(o_d, dict):
        origin = SelectionOrigin(**{k: tuple(v) if isinstance(v, list) else v for k, v in o_d.items()})
    else:
        parent_ids = tuple(d.get("parent_ids") or ())
        prov_kind = prov.kind or slot
        origin_selector = "clothing_state" if prov_kind == "clothing_state" else slot
        origin = SelectionOrigin(
            selector=origin_selector,
            selected_id=item_id,
            parent_ids=parent_ids,
            mode="explicit",
        )

    st = d.get("span_type", SpanType.PLAIN)
    if isinstance(st, str):
        try:
            st = SpanType(st)
        except ValueError:
            st = SpanType.PLAIN

    raw_id = d.get("id", "")
    if raw_id == aid:
        raw_id = ""

    # 如果缺少 id 或 facts.explicit_fields 为空，通过权威词库快速逆向补全
    if (not raw_id or not facts.explicit_fields) and text:
        text_lookup = get_catalog_text_lookup(DATA_DIR)
        norm_s = normalize_slot_name(slot)
        txt = text.strip().lower()
        found = text_lookup.get((norm_s, txt)) or text_lookup.get((slot, txt))
        if found:
            tag_id, facts_dict = found
            if not raw_id:
                raw_id = tag_id
            if not facts.explicit_fields and facts_dict:
                facts = SemanticFacts.from_dict(facts_dict)

    if not facts.explicit_fields:
        facts = SemanticFacts(
            semantic_role="selector",
            visible_regions=facts.visible_regions,
            garment_topologies=facts.garment_topologies,
            garment_states=facts.garment_states,
            prop_usage=facts.prop_usage,
        )

    atom = PromptAtom(
        text=text,
        span_type=st,
        source_slot=slot,
        source_item_id=item_id,
        tag_order=tag_order,
        span_order=span_order,
        provenance=prov,
        atom_id=aid,
        id=raw_id,
        facts=facts,
        origin=origin,
        target_id=target_id,
    )
    if "is_worn" in d:
        object.__setattr__(atom, "is_worn", d["is_worn"])
    if "is_ambient" in d:
        object.__setattr__(atom, "is_ambient", d["is_ambient"])
    return atom


def build_replay_entity_key(atom: PromptAtom) -> Optional[str]:
    """复用生产 build_garment_entity_key 并对测试构造的已注销在穿资格原子实施隔离"""
    if atom.source_item_id in ("auto_linkage", "linkage_general"):
        return None
    ekey = build_garment_entity_key(atom)
    if not ekey:
        return None
    if getattr(atom, "is_worn", True) is False or (atom.facts and any(s in ("discarded", "removed") for s in atom.facts.garment_states)):
        return f"{ekey}:discarded:{atom.atom_id}"
    return ekey


def get_active_worn_entities(active_atoms: Sequence[PromptAtom]) -> List[GarmentCarrierEntity]:
    """从当前活跃 PromptAtom 序列恢复实体，并精确应用 discarded 状态判定 (复用生产 conflict_resolver.py:2221-2237)"""
    entities_map: Dict[str, GarmentCarrierEntity] = {}
    for a in active_atoms:
        ekey = build_replay_entity_key(a)
        if not ekey:
            continue
        slot = a.origin.selector if a.origin else (a.source_slot or "")
        item_id = a.source_item_id or (a.origin.selected_id if a.origin else "") or a.id
        is_w = getattr(a, "is_worn", True)
        if a.facts and any(s in ("discarded", "removed") for s in a.facts.garment_states):
            is_w = False
        if a.facts and a.facts.prop_usage in ("ambient", "discarded"):
            is_w = False
        is_amb = getattr(a, "is_ambient", False) or not is_w

        if ekey not in entities_map:
            entities_map[ekey] = GarmentCarrierEntity(
                entity_id=ekey,
                selector=slot,
                selected_id=item_id,
                member_atoms=[a],
                is_worn=is_w,
                is_ambient=is_amb,
            )
        else:
            entities_map[ekey].member_atoms.append(a)
            if not is_w:
                entities_map[ekey].is_worn = False
                entities_map[ekey].is_ambient = True

    # 处理关联 discarded 修饰原子的注销 (生产 conflict_resolver.py:2221-2237)
    discarded_atoms = [
        a for a in active_atoms
        if (
            (a.origin and a.origin.selected_id == "discarded")
            or (a.facts and "discarded" in a.facts.garment_states)
            or a.source_item_id == "discarded"
        )
    ]
    for da in discarded_atoms:
        binding = find_bound_carrier(da, list(entities_map.values()))
        if binding.status == BindingStatus.BOUND and binding.target_entity:
            binding.target_entity.is_worn = False
            binding.target_entity.is_ambient = True
            binding.target_entity.discarded_by = da

    return [e for e in entities_map.values() if e.is_worn and not e.is_ambient]


def is_bra_qualified_entity(e: GarmentCarrierEntity) -> bool:
    """复用生产 conflict_resolver.py:2425-2429 判定是否具备胸罩资格"""
    if e.selected_id in BRA_ENTITIES:
        return True
    return any(
        ma.facts and "underwear" in ma.facts.garment_topologies and "top" in ma.facts.garment_topologies
        for ma in e.member_atoms
    )


def is_panties_qualified_entity(e: GarmentCarrierEntity) -> bool:
    """复用生产 conflict_resolver.py:2441-2445 判定是否具备内裤资格"""
    if e.selected_id in PANTIES_ENTITIES:
        return True
    return any(
        ma.facts and "underwear" in ma.facts.garment_topologies and (
            "bottom" in ma.facts.garment_topologies or "bottom_pants" in ma.facts.garment_topologies
        )
        for ma in e.member_atoms
    )


_CACHED_RESOLVER: Optional[ConflictResolver] = None

def get_conflict_resolver(data_dir: Path | str = DATA_DIR) -> ConflictResolver:
    global _CACHED_RESOLVER
    if _CACHED_RESOLVER is None:
        _CACHED_RESOLVER = ConflictResolver(str(data_dir))
    return _CACHED_RESOLVER


def is_decision_match(dec_dict: Dict[str, Any], actual_dec: Any) -> bool:
    """对比外部声明决策与生产消解实际决策是否等价（全语义字段核验，包含 after_text 与 before_text）"""
    r_match = (
        dec_dict.get("rule_id") == actual_dec.rule_id
        or (dec_dict.get("rule_id") in ("absence_state_conflict", "clothing_style_state_coherence")
            and actual_dec.rule_id in ("absence_state_conflict", "clothing_style_state_coherence"))
    )
    if not r_match:
        return False
    if dec_dict.get("reason_code") != actual_dec.reason_code:
        return False
    if dec_dict.get("action") != actual_dec.action:
        return False
    if dec_dict.get("target_atom_id") != actual_dec.target_atom_id:
        return False
    if "winner_atom_ids" in dec_dict and tuple(sorted(dec_dict.get("winner_atom_ids") or ())) != tuple(sorted(actual_dec.winner_atom_ids or ())):
        return False
    if "produced_atom_ids" in dec_dict and tuple(sorted(dec_dict.get("produced_atom_ids") or ())) != tuple(sorted(actual_dec.produced_atom_ids or ())):
        return False
    dec_before = (dec_dict.get("before_text") or "").strip()
    act_before = (getattr(actual_dec, "before_text", None) or "").strip()
    if dec_before != act_before:
        return False
    dec_after = (dec_dict.get("after_text") or "").strip()
    act_after = (getattr(actual_dec, "after_text", None) or "").strip()
    if dec_after != act_after:
        return False
    return True


def verify_single_decision_legality(
    dec: Dict[str, Any],
    cur_src_map: Dict[str, Any] | Sequence[Dict[str, Any] | PromptAtom],
    valid_rules: Set[str],
    cur_bindings: Dict[str, Any] | None = None,
    seed: Optional[int] = None,
    rng: Optional[Random] = None,
    context_profile: Optional[ContextProfile] = None,
    data_dir: Optional[Path | str] = None,
) -> List[str]:
    """
    单条决策合法性与因果查询接口：核验单项变化决策在当前输入原子下的生产触发依据与语义合法性。
    """
    errors: List[str] = []
    source_atoms = list(cur_src_map.values()) if isinstance(cur_src_map, dict) else list(cur_src_map)
    source_atom_objs: List[PromptAtom] = []
    active_map: Dict[str, PromptAtom] = {}
    for a in source_atoms:
        atom_obj = dict_to_prompt_atom(a)
        if atom_obj.id == atom_obj.atom_id:
            object.__setattr__(atom_obj, "id", "")
        source_atom_objs.append(atom_obj)
        active_map[atom_obj.atom_id] = atom_obj

    resolver = get_conflict_resolver(data_dir or DATA_DIR)
    actual_decisions: List[Any] = []
    try:
        _, _, rep = resolver.resolve_atoms_with_full_report(
            source_atom_objs, rng=rng, context_profile=context_profile, effective_seed=seed
        )
        actual_decisions = list(rep.decisions)
    except Exception as e:
        errors.append(
            f"UNEXPECTED_RESOLVER_EXCEPTION: Production resolver failed on source atoms with {type(e).__name__}: {e}"
        )
        return errors

    rule_id = dec.get("rule_id")
    reason_code = dec.get("reason_code")
    action = dec.get("action")
    tid = dec.get("target_atom_id")
    wids = dec.get("winner_atom_ids", [])
    before_text = dec.get("before_text")

    # 1. 生产消解产出因果一致性核验 (含 after_text / before_text)
    matching_actual = next((ad for ad in actual_decisions if is_decision_match(dec, ad)), None)
    if not matching_actual:
        errors.append(
            f"UNGROUNDED_DECISION: Decision '{dec.get('decision_id')}' ({rule_id} / {reason_code}, "
            f"target={tid}, winners={wids}) has no legal basis or trigger condition in production conflict resolution "
            f"for the provided input atoms."
        )

    # 2. 规则有效性
    if rule_id not in valid_rules:
        errors.append(f"UNKNOWN_DECISION_RULE: Unexpected rule_id '{rule_id}' in resolution decisions")
        return errors

    # 3. 动作与原因码契约
    contract = RULE_ALLOWED_CONTRACTS.get(rule_id)
    if contract is not None:
        if action not in contract:
            errors.append(
                f"ILLEGAL_DECISION_ACTION: rule '{rule_id}' does not support action '{action}' "
                f"(allowed actions: {sorted(contract.keys())})"
            )
        elif reason_code not in contract[action]:
            errors.append(
                f"ILLEGAL_REASON_CODE: reason_code '{reason_code}' is not valid for rule '{rule_id}' with action '{action}' "
                f"(allowed: {sorted(contract[action])})"
            )

    # 4. 目标存在性与槽位域
    if tid is not None:
        if tid not in active_map:
            errors.append(f"TARGET_NOT_ACTIVE: target atom '{tid}' is not active at decision time (already dropped or missing)")
        else:
            target_atom = active_map[tid]
            if before_text and before_text != target_atom.text:
                errors.append(f"DECISION_BEFORE_TEXT_MISMATCH: before_text '{before_text}' != target text '{target_atom.text}'")

            allowed_slots = RULE_TARGET_DOMAINS.get(rule_id)
            tslot = (target_atom.source_slot or "").lower()
            if allowed_slots is not None and tslot not in allowed_slots:
                errors.append(
                    f"ILLEGAL_RULE_TARGET_DOMAIN: rule '{rule_id}' cannot target slot '{tslot}' "
                    f"(atom='{tid}', text='{target_atom.text}')"
                )

    # 5. Winner 活跃性
    for wid in wids:
        if wid not in active_map:
            errors.append(f"WINNER_NOT_ACTIVE: winner atom '{wid}' is not active at decision time (already dropped or missing)")

    # 6. 生产谓词核验（基于活跃实体）
    worn_entities = get_active_worn_entities(list(active_map.values()))

    # 6A. 缺席互斥规则
    if rule_id == "absence_state_conflict" or reason_code == "absence_state_conflict":
        if action != "drop":
            errors.append(f"ILLEGAL_DECISION_ACTION: absence_state_conflict action must be 'drop', got '{action}'")
        if tid in active_map:
            target_atom = active_map[tid]
            t_iid = target_atom.source_item_id
            t_slot = target_atom.source_slot
            if t_iid not in CANONICAL_ABSENCE_STATES or t_slot not in ("clothing_state", "clothing"):
                errors.append(
                    f"ILLEGAL_ABSENCE_CONFLICT_TARGET: absence conflict targeted non-absence atom "
                    f"'{target_atom.text}' (slot={t_slot}, id={t_iid})"
                )

        if not wids:
            errors.append("MISSING_DECISION_WINNERS: absence_state_conflict has no winner atoms")
        for wid in wids:
            if wid in active_map:
                winner_atom = active_map[wid]
                w_entity = next((e for e in worn_entities if any(ma.atom_id == wid for ma in e.member_atoms)), None)
                if not w_entity:
                    errors.append(
                        f"ILLEGAL_ABSENCE_CONFLICT_WINNER_NOT_WORN: winner '{wid}' ('{winner_atom.source_item_id}') "
                        f"is not actively worn (discarded/ambient/removed) and cannot trigger absence conflict"
                    )
                    continue

                if tid in active_map:
                    target_atom = active_map[tid]
                    t_iid = target_atom.source_item_id
                    if t_iid == "braless":
                        if not is_bra_qualified_entity(w_entity):
                            errors.append(
                                f"ILLEGAL_ABSENCE_CONFLICT_WINNER: absence_state_conflict winner '{wid}' "
                                f"('{winner_atom.text}', id={winner_atom.source_item_id}) cannot drop braless (requires active bra entity)"
                            )
                    elif t_iid == "underwearless":
                        if not is_panties_qualified_entity(w_entity):
                            errors.append(
                                f"ILLEGAL_ABSENCE_CONFLICT_WINNER: absence_state_conflict winner '{wid}' "
                                f"('{winner_atom.text}', id={winner_atom.source_item_id}) cannot drop underwearless (requires active panties entity)"
                            )

    # 6B. 承载主体检查
    elif rule_id in ("state_lacks_carrier", "garment_carrier_binding") or reason_code in (
        "state_lacks_carrier", "no_compatible_carrier", "target_incompatible"
    ):
        if tid in active_map:
            target_atom = active_map[tid]
            tslot = (target_atom.source_slot or "").lower()
            if tslot in NON_GARMENT_DOMAINS or not is_garment_modifier_atom(target_atom):
                errors.append(
                    f"ILLEGAL_CARRIER_BINDING_TARGET: carrier check rule '{rule_id}' targeted non-modifier atom "
                    f"'{target_atom.text}' (slot='{tslot}', id='{target_atom.source_item_id}')"
                )
            else:
                binding = find_bound_carrier(target_atom, worn_entities)
                if action == "drop":
                    if binding.status == BindingStatus.BOUND:
                        cid = binding.target_entity.selected_id if binding.target_entity else "unknown"
                        errors.append(
                            f"ILLEGAL_CARRIER_DROP_WHEN_BOUND: Atom '{tid}' ('{target_atom.text}') was dropped for lacking carrier, "
                            f"but has active bound carrier '{cid}' at decision time"
                        )
                    elif binding.status == BindingStatus.AMBIGUOUS_MULTIPLE_CANDIDATES:
                        errors.append(
                            f"ILLEGAL_CARRIER_DROP_WHEN_BOUND: Atom '{tid}' ('{target_atom.text}') was dropped for lacking carrier, "
                            f"but has multiple active carrier candidates at decision time (ambiguity preserves atom)"
                        )
                    elif cur_bindings and tid in cur_bindings:
                        cid = cur_bindings[tid].get("carrier_selected_id", "unknown")
                        errors.append(
                            f"ILLEGAL_CARRIER_DROP_WHEN_BOUND: Atom '{tid}' was dropped for lacking carrier, "
                            f"but has recorded active bound carrier '{cid}'"
                        )

    return errors


def verify_decision_legality(
    dec: Dict[str, Any],
    cur_src_map: Dict[str, Any],
    valid_rules: Set[str],
    cur_bindings: Dict[str, Any] | None = None,
    seed: Optional[int] = None,
) -> List[str]:
    """向后兼容单条决策合法性核验入口"""
    return verify_single_decision_legality(dec, cur_src_map, valid_rules, cur_bindings=cur_bindings, seed=seed)


def verify_full_resolution_decisions(
    source_atoms: Sequence[Dict[str, Any] | PromptAtom],
    decisions: Sequence[Dict[str, Any]],
    valid_rules: Set[str],
    cur_bindings: Dict[str, Any] | None = None,
    seed: Optional[int] = None,
    rng: Optional[Random] = None,
    context_profile: Optional[ContextProfile] = None,
    data_dir: Optional[Path | str] = None,
) -> List[str]:
    """
    完整归档校验：规范化后的有序决策列表全等比较与时序重放：
    1. 还原输入原子为强类型 PromptAtom 序列；
    2. 调用生产 ConflictResolver.resolve_atoms_with_full_report 权威执行全规则求解；
    3. 异常严格抛出报错（UNEXPECTED_RESOLVER_EXCEPTION），严禁静默吞掉异常；
    4. 对归档决策与生产实际产出的决策列表执行数量、顺序及全语义字段（含 after_text / before_text）逐项全等比对；
    5. 逐项核验动作契约、原因码契约、目标槽位域及 Winner 活跃时序；
    6. 实时核销活跃原子池，杜绝先删后用的时序漏洞。
    """
    errors: List[str] = []
    source_atom_objs: List[PromptAtom] = []
    active_map: Dict[str, PromptAtom] = {}
    for a in source_atoms:
        atom_obj = dict_to_prompt_atom(a)
        if atom_obj.id == atom_obj.atom_id:
            object.__setattr__(atom_obj, "id", "")
        source_atom_objs.append(atom_obj)
        active_map[atom_obj.atom_id] = atom_obj

    resolver = get_conflict_resolver(data_dir or DATA_DIR)
    actual_decisions: List[Any] = []
    try:
        _, _, rep = resolver.resolve_atoms_with_full_report(
            source_atom_objs, rng=rng, context_profile=context_profile, effective_seed=seed
        )
        actual_decisions = list(rep.decisions)
    except Exception as e:
        errors.append(
            f"UNEXPECTED_RESOLVER_EXCEPTION: Production resolver failed on source atoms with {type(e).__name__}: {e}"
        )
        return errors

    # 1. 数量严格比对
    if len(decisions) != len(actual_decisions):
        errors.append(
            f"DECISION_COUNT_MISMATCH: Claimed {len(decisions)} decisions, but production resolver produced {len(actual_decisions)} decisions "
            f"(claimed_ids={[d.get('decision_id') for d in decisions]}, actual_ids={[ad.decision_id for ad in actual_decisions]})"
        )

    # 2. 规范化有序决策列表逐项字段全等比较 (覆盖 after_text / before_text)
    max_len = max(len(decisions), len(actual_decisions))
    for idx in range(max_len):
        if idx >= len(decisions):
            act = actual_decisions[idx]
            errors.append(
                f"OMITTED_PRODUCTION_DECISION: Production decision at index {idx} '{act.decision_id}' "
                f"({act.rule_id} / {act.reason_code}, target={act.target_atom_id}) was produced by resolver but omitted from decisions."
            )
            continue
        if idx >= len(actual_decisions):
            claimed = decisions[idx]
            errors.append(
                f"UNGROUNDED_DECISION: Claimed decision at index {idx} '{claimed.get('decision_id')}' "
                f"({claimed.get('rule_id')} / {claimed.get('reason_code')}, target={claimed.get('target_atom_id')}) "
                f"has no corresponding production decision produced by resolver."
            )
            continue

        claimed = decisions[idx]
        act = actual_decisions[idx]

        c_rule = claimed.get("rule_id")
        a_rule = act.rule_id
        r_match = (
            c_rule == a_rule
            or (c_rule in ("absence_state_conflict", "clothing_style_state_coherence")
                and a_rule in ("absence_state_conflict", "clothing_style_state_coherence"))
        )
        if not r_match:
            errors.append(
                f"DECISION_ORDER_OR_RULE_MISMATCH: Decision at index {idx}: claimed rule '{c_rule}' != actual rule '{a_rule}'"
            )

        if claimed.get("reason_code") != act.reason_code:
            errors.append(
                f"DECISION_FIELD_MISMATCH(reason_code): Decision at index {idx} ({c_rule}): "
                f"claimed reason '{claimed.get('reason_code')}' != actual reason '{act.reason_code}'"
            )

        if claimed.get("action") != act.action:
            errors.append(
                f"DECISION_FIELD_MISMATCH(action): Decision at index {idx} ({c_rule}): "
                f"claimed action '{claimed.get('action')}' != actual action '{act.action}'"
            )

        if claimed.get("target_atom_id") != act.target_atom_id:
            errors.append(
                f"DECISION_FIELD_MISMATCH(target_atom_id): Decision at index {idx} ({c_rule}): "
                f"claimed target '{claimed.get('target_atom_id')}' != actual target '{act.target_atom_id}'"
            )

        c_winners = tuple(sorted(claimed.get("winner_atom_ids") or ()))
        a_winners = tuple(sorted(act.winner_atom_ids or ()))
        if c_winners != a_winners:
            errors.append(
                f"DECISION_FIELD_MISMATCH(winner_atom_ids): Decision at index {idx} ({c_rule}): "
                f"claimed winners {c_winners} != actual winners {a_winners}"
            )

        c_prods = tuple(sorted(claimed.get("produced_atom_ids") or ()))
        a_prods = tuple(sorted(act.produced_atom_ids or ()))
        if c_prods != a_prods:
            errors.append(
                f"DECISION_FIELD_MISMATCH(produced_atom_ids): Decision at index {idx} ({c_rule}): "
                f"claimed produced {c_prods} != actual produced {a_prods}"
            )

        c_before = (claimed.get("before_text") or "").strip()
        a_before = (getattr(act, "before_text", None) or "").strip()
        if c_before != a_before:
            errors.append(
                f"DECISION_FIELD_MISMATCH(before_text): Decision at index {idx} ({c_rule}): "
                f"claimed before_text {c_before!r} != actual before_text {a_before!r}"
            )

        c_after = (claimed.get("after_text") or "").strip()
        a_after = (getattr(act, "after_text", None) or "").strip()
        if c_after != a_after:
            errors.append(
                f"DECISION_FIELD_MISMATCH(after_text): Decision at index {idx} ({c_rule}): "
                f"claimed after_text {c_after!r} != actual after_text {a_after!r}"
            )

    # 3. 逐项核验动作契约、原因码契约、目标槽位域及 Winner 活跃时序
    for dec in decisions:
        rule_id = dec.get("rule_id")
        reason_code = dec.get("reason_code")
        action = dec.get("action")
        tid = dec.get("target_atom_id")
        wids = dec.get("winner_atom_ids", [])
        before_text = dec.get("before_text")

        if rule_id not in valid_rules:
            errors.append(f"UNKNOWN_DECISION_RULE: Unexpected rule_id '{rule_id}' in resolution decisions")
            continue

        contract = RULE_ALLOWED_CONTRACTS.get(rule_id)
        if contract is not None:
            if action not in contract:
                errors.append(
                    f"ILLEGAL_DECISION_ACTION: rule '{rule_id}' does not support action '{action}' "
                    f"(allowed actions: {sorted(contract.keys())})"
                )
            elif reason_code not in contract[action]:
                errors.append(
                    f"ILLEGAL_REASON_CODE: reason_code '{reason_code}' is not valid for rule '{rule_id}' with action '{action}' "
                    f"(allowed: {sorted(contract[action])})"
                )

        if tid is not None:
            if tid not in active_map:
                errors.append(f"TARGET_NOT_ACTIVE: target atom '{tid}' is not active at decision time (already dropped or missing)")
            else:
                target_atom = active_map[tid]
                if before_text and before_text != target_atom.text:
                    errors.append(f"DECISION_BEFORE_TEXT_MISMATCH: before_text '{before_text}' != target text '{target_atom.text}'")

                allowed_slots = RULE_TARGET_DOMAINS.get(rule_id)
                tslot = (target_atom.source_slot or "").lower()
                if allowed_slots is not None and tslot not in allowed_slots:
                    errors.append(
                        f"ILLEGAL_RULE_TARGET_DOMAIN: rule '{rule_id}' cannot target slot '{tslot}' "
                        f"(atom='{tid}', text='{target_atom.text}')"
                    )

        for wid in wids:
            if wid not in active_map:
                errors.append(f"WINNER_NOT_ACTIVE: winner atom '{wid}' is not active at decision time (already dropped or missing)")

        worn_entities = get_active_worn_entities(list(active_map.values()))

        # 3B. 缺席互斥规则
        if rule_id == "absence_state_conflict" or reason_code == "absence_state_conflict":
            if action != "drop":
                errors.append(f"ILLEGAL_DECISION_ACTION: absence_state_conflict action must be 'drop', got '{action}'")
            if tid in active_map:
                target_atom = active_map[tid]
                t_iid = target_atom.source_item_id
                t_slot = target_atom.source_slot
                if t_iid not in CANONICAL_ABSENCE_STATES or t_slot not in ("clothing_state", "clothing"):
                    errors.append(
                        f"ILLEGAL_ABSENCE_CONFLICT_TARGET: absence conflict targeted non-absence atom "
                        f"'{target_atom.text}' (slot={t_slot}, id={t_iid})"
                    )

            if not wids:
                errors.append("MISSING_DECISION_WINNERS: absence_state_conflict has no winner atoms")
            for wid in wids:
                if wid in active_map:
                    winner_atom = active_map[wid]
                    w_entity = next((e for e in worn_entities if any(ma.atom_id == wid for ma in e.member_atoms)), None)
                    if not w_entity:
                        errors.append(
                            f"ILLEGAL_ABSENCE_CONFLICT_WINNER_NOT_WORN: winner '{wid}' ('{winner_atom.source_item_id}') "
                            f"is not actively worn (discarded/ambient/removed) and cannot trigger absence conflict"
                        )
                        continue

                    if tid in active_map:
                        target_atom = active_map[tid]
                        t_iid = target_atom.source_item_id
                        if t_iid == "braless":
                            if not is_bra_qualified_entity(w_entity):
                                errors.append(
                                    f"ILLEGAL_ABSENCE_CONFLICT_WINNER: absence_state_conflict winner '{wid}' "
                                    f"('{winner_atom.text}', id={winner_atom.source_item_id}) cannot drop braless (requires active bra entity)"
                                )
                        elif t_iid == "underwearless":
                            if not is_panties_qualified_entity(w_entity):
                                errors.append(
                                    f"ILLEGAL_ABSENCE_CONFLICT_WINNER: absence_state_conflict winner '{wid}' "
                                    f"('{winner_atom.text}', id={winner_atom.source_item_id}) cannot drop underwearless (requires active panties entity)"
                                )

        # 3C. 承载主体检查
        elif rule_id in ("state_lacks_carrier", "garment_carrier_binding") or reason_code in (
            "state_lacks_carrier", "no_compatible_carrier", "target_incompatible"
        ):
            if tid in active_map:
                target_atom = active_map[tid]
                tslot = (target_atom.source_slot or "").lower()
                if tslot in NON_GARMENT_DOMAINS or not is_garment_modifier_atom(target_atom):
                    errors.append(
                        f"ILLEGAL_CARRIER_BINDING_TARGET: carrier check rule '{rule_id}' targeted non-modifier atom "
                        f"'{target_atom.text}' (slot='{tslot}', id='{target_atom.source_item_id}')"
                    )
                else:
                    binding = find_bound_carrier(target_atom, worn_entities)
                    if action == "drop":
                        if binding.status == BindingStatus.BOUND:
                            cid = binding.target_entity.selected_id if binding.target_entity else "unknown"
                            errors.append(
                                f"ILLEGAL_CARRIER_DROP_WHEN_BOUND: Atom '{tid}' ('{target_atom.text}') was dropped for lacking carrier, "
                                f"but has active bound carrier '{cid}' at decision time"
                            )
                        elif binding.status == BindingStatus.AMBIGUOUS_MULTIPLE_CANDIDATES:
                            errors.append(
                                f"ILLEGAL_CARRIER_DROP_WHEN_BOUND: Atom '{tid}' ('{target_atom.text}') was dropped for lacking carrier, "
                                f"but has multiple active carrier candidates at decision time (ambiguity preserves atom)"
                            )
                        elif cur_bindings and tid in cur_bindings:
                            cid = cur_bindings[tid].get("carrier_selected_id", "unknown")
                            errors.append(
                                f"ILLEGAL_CARRIER_DROP_WHEN_BOUND: Atom '{tid}' was dropped for lacking carrier, "
                                f"but has recorded active bound carrier '{cid}'"
                            )

        if action == "drop":
            if tid in active_map:
                del active_map[tid]
        elif action == "replace":
            old = active_map.pop(tid, None)
            for pid in dec.get("produced_atom_ids", []):
                slot = old.source_slot if old else "clothing"
                item_id = old.source_item_id if old else ""
                active_map[pid] = PromptAtom(
                    text=dec.get("after_text") or "",
                    span_type=SpanType.PLAIN,
                    source_slot=slot,
                    source_item_id=item_id,
                    atom_id=pid,
                    id=pid,
                    provenance=TagProvenance(item_id=item_id, kind=slot, rule_id=rule_id, parent_ids=(tid,) if tid else ()),
                )
        elif action == "inject":
            for pid in dec.get("produced_atom_ids", []):
                active_map[pid] = PromptAtom(
                    text=dec.get("after_text") or "",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing_extension",
                    atom_id=pid,
                    id=pid,
                )

    return errors


def replay_and_verify_decisions(
    source_atoms: Sequence[Dict[str, Any] | PromptAtom],
    decisions: Sequence[Dict[str, Any]],
    valid_rules: Set[str],
    cur_bindings: Dict[str, Any] | None = None,
    seed: Optional[int] = None,
    rng: Optional[Random] = None,
    context_profile: Optional[ContextProfile] = None,
    data_dir: Optional[Path | str] = None,
    enforce_full_completeness: bool = True,
) -> List[str]:
    """
    消解决策流水线重放与核验：
    - enforce_full_completeness=True 时执行完整归档校验 verify_full_resolution_decisions；
    - enforce_full_completeness=False 时执行时序局部决策流校验。
    """
    if enforce_full_completeness:
        return verify_full_resolution_decisions(
            source_atoms,
            decisions,
            valid_rules,
            cur_bindings=cur_bindings,
            seed=seed,
            rng=rng,
            context_profile=context_profile,
            data_dir=data_dir,
        )

    errors: List[str] = []
    source_atom_objs: List[PromptAtom] = []
    active_map: Dict[str, PromptAtom] = {}
    for a in source_atoms:
        atom_obj = dict_to_prompt_atom(a)
        if atom_obj.id == atom_obj.atom_id:
            object.__setattr__(atom_obj, "id", "")
        source_atom_objs.append(atom_obj)
        active_map[atom_obj.atom_id] = atom_obj

    for dec in decisions:
        action = dec.get("action")
        tid = dec.get("target_atom_id")
        wids = dec.get("winner_atom_ids", [])

        if tid is not None and tid not in active_map:
            errors.append(f"TARGET_NOT_ACTIVE: target atom '{tid}' is not active at decision time")
        for wid in wids:
            if wid not in active_map:
                errors.append(f"WINNER_NOT_ACTIVE: winner atom '{wid}' is not active at decision time (already dropped or missing)")

        if action == "drop":
            if tid in active_map:
                del active_map[tid]
        elif action == "replace":
            old = active_map.pop(tid, None)
            for pid in dec.get("produced_atom_ids", []):
                slot = old.source_slot if old else "clothing"
                item_id = old.source_item_id if old else ""
                active_map[pid] = PromptAtom(
                    text=dec.get("after_text") or "",
                    span_type=SpanType.PLAIN,
                    source_slot=slot,
                    source_item_id=item_id,
                    atom_id=pid,
                    id=pid,
                    provenance=TagProvenance(item_id=item_id, kind=slot, rule_id=dec.get("rule_id"), parent_ids=(tid,) if tid else ()),
                )
        elif action == "inject":
            for pid in dec.get("produced_atom_ids", []):
                active_map[pid] = PromptAtom(
                    text=dec.get("after_text") or "",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing_extension",
                    atom_id=pid,
                    id=pid,
                )

    return errors


def attribute_step3_seed_diff(
    seed: int,
    base_item: Dict[str, Any],
    cur_item: Dict[str, Any],
    catalog_leaf_tags: Dict[str, Dict[str, Set[str]]],
    valid_rules: Set[str] | None = None,
) -> Dict[str, Any]:
    """
    对单个种子执行严格结构化逐原子跨版本差异因果归因。
    """
    if valid_rules is None:
        valid_rules = load_authoritative_resolver_rules(REPO_DIR / "data")

    base_src = base_item["source_atoms"]
    cur_src = cur_item["source_atoms"]
    base_final = base_item["final_atoms"]
    cur_final = cur_item["final_atoms"]
    cur_decs = cur_item["decisions"]
    base_decs = base_item["decisions"]
    cur_bindings = cur_item.get("carrier_bindings", {})
    cur_dedup_records = cur_item.get("dedup_records", [])
    cur_budget_records = cur_item.get("budget_records", [])

    unexplained_reasons: List[str] = []
    explained_attributions: List[Dict[str, Any]] = []

    # ─────────────────────────────────────────────────────────────
    # 1. 重复 atom_id 严格核验
    # ─────────────────────────────────────────────────────────────
    for name, atom_list in [
        ("cur_final", cur_final),
        ("cur_src", cur_src),
        ("base_final", base_final),
        ("base_src", base_src),
    ]:
        ids = [a["atom_id"] for a in atom_list]
        if len(ids) != len(set(ids)):
            unexplained_reasons.append(
                f"DUPLICATE_ATOM_ID_IN_{name.upper()}: Duplicate atom IDs detected: {ids}"
            )

    # ─────────────────────────────────────────────────────────────
    # 2. 未变更槽位零突变核验 (完整有序原子签名列表比对)
    # ─────────────────────────────────────────────────────────────
    b_slot_atoms: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in base_src:
        b_slot_atoms[a["source_slot"]].append(a)

    c_slot_atoms: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in cur_src:
        c_slot_atoms[a["source_slot"]].append(a)

    all_slots = set(b_slot_atoms.keys()) | set(c_slot_atoms.keys())
    for slot in all_slots:
        if slot not in ALLOWED_DIFF_SLOTS:
            b_sigs = [slot_atom_signature(a) for a in b_slot_atoms[slot]]
            c_sigs = [slot_atom_signature(a) for a in c_slot_atoms[slot]]
            if b_sigs != c_sigs:
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNEXPECTED_SLOT_MUTATION({slot}): Multi-atom signature mismatch in unchanged slot: "
                    f"base={b_sigs} vs cur={c_sigs}"
                )

    # ─────────────────────────────────────────────────────────────
    # 3. 词库合法性与具体叶子词条严格核验
    # ─────────────────────────────────────────────────────────────
    for a in cur_src:
        slot = a.get("source_slot")
        iid = a.get("source_item_id", "")
        txt = a.get("text", "")
        aid = a.get("atom_id")

        if slot == "jewelry":
            if iid not in catalog_leaf_tags["jewelry"]:
                unexplained_reasons.append(f"UNEXPLAINED_UNKNOWN_JEWELRY_ID: '{iid}'")
            elif txt not in catalog_leaf_tags["jewelry"][iid]:
                unexplained_reasons.append(
                    f"UNEXPLAINED_TAMPERED_CATALOG_TAG(jewelry): Atom '{aid}' under id '{iid}' "
                    f"has unauthorized leaf tag text '{txt}'"
                )
        elif slot == "imperfections":
            if iid not in catalog_leaf_tags["imperfections"]:
                unexplained_reasons.append(f"UNEXPLAINED_UNKNOWN_IMPERFECTION_ID: '{iid}'")
            elif txt not in catalog_leaf_tags["imperfections"][iid]:
                unexplained_reasons.append(
                    f"UNEXPLAINED_TAMPERED_CATALOG_TAG(imperfections): Atom '{aid}' under id '{iid}' "
                    f"has unauthorized leaf tag text '{txt}'"
                )
        elif slot in ("clothing", "clothing_state", "clothing_extension", "linkage"):
            if iid not in catalog_leaf_tags["clothing"]:
                unexplained_reasons.append(f"UNEXPLAINED_UNKNOWN_CLOTHING_ID: '{iid}'")
            elif txt not in catalog_leaf_tags["clothing"][iid]:
                unexplained_reasons.append(
                    f"UNEXPLAINED_TAMPERED_CATALOG_TAG(clothing): Atom '{aid}' under id '{iid}' "
                    f"has unauthorized leaf tag text '{txt}'"
                )

    # ─────────────────────────────────────────────────────────────
    # 4. 当前版本内部来源核验 (统一来源签名) 与逆向无决策消失核验
    # ─────────────────────────────────────────────────────────────
    cur_src_map = {a["atom_id"]: a for a in cur_src}
    cur_final_map = {a["atom_id"]: a for a in cur_final}
    cur_decs_by_prod = {pid: d for d in cur_decs for pid in d.get("produced_atom_ids", [])}
    cur_consumed_by_target = {
        d["target_atom_id"]: d
        for d in cur_decs
        if d.get("action") in ("drop", "replace")
    }
    cur_dedup_by_id = {rec["atom_id"]: rec for rec in cur_dedup_records}
    cur_budget_by_id = {rec["atom_id"]: rec for rec in cur_budget_records}

    # 4A. 正向统一来源签名核验：每一个终态原子必须与源原子签名 100% 吻合（或由决策合法生产）
    for a in cur_final:
        aid = a["atom_id"]
        if aid in cur_src_map:
            src_a = cur_src_map[aid]
            sig_errors = verify_atom_source_signature(a, src_a)
            if sig_errors:
                unexplained_reasons.extend(sig_errors)
        elif aid in cur_decs_by_prod:
            prod_dec = cur_decs_by_prod[aid]
            if a["text"] != prod_dec.get("after_text"):
                unexplained_reasons.append(
                    f"UNEXPLAINED_PRODUCED_ATOM_TEXT_MISMATCH: Final atom '{aid}' text '{a['text']}' "
                    f"does not match producing decision after_text '{prod_dec.get('after_text')}'"
                )
        else:
            unexplained_reasons.append(
                f"UNEXPLAINED_UNSOURCED_FINAL_ATOM: Final atom '{aid}' ('{a['text']}') has neither source atom nor producing decision"
            )

    # 4B. 统一决策合法性核验：按决策顺序重放并复用生产谓词核验完整决策流
    replay_errors = replay_and_verify_decisions(cur_src, cur_decs, valid_rules, cur_bindings=cur_bindings, seed=seed)
    if replay_errors:
        unexplained_reasons.extend(replay_errors)

    # 4C. 逆向无决策消失拦截：每一个源原子必须在终态中保留、或有明确合规的 drop/replace 决策、或在去重/截断记录中
    for a in cur_src:
        aid = a["atom_id"]
        if aid not in cur_final_map:
            if aid in cur_consumed_by_target:
                consumed_dec = cur_consumed_by_target[aid]
                if consumed_dec.get("before_text") and consumed_dec.get("before_text") != a["text"]:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_CONSUMED_BEFORE_TEXT_MISMATCH: Decision before_text '{consumed_dec.get('before_text')}' "
                        f"does not match source text '{a['text']}'"
                    )
            elif aid in cur_dedup_by_id:
                # 具备去重证明
                pass
            elif aid in cur_budget_by_id:
                # 具备预算截断证明
                pass
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SILENT_ATOM_DROP: Source atom '{aid}' ('{a['text']}') missing from final atoms without recorded drop/replace/dedup decision"
                )

    # ─────────────────────────────────────────────────────────────
    # 5. 跨版本保序与未变更槽位相对顺序不变性核验
    # ─────────────────────────────────────────────────────────────
    cur_orders = [(a.get("tag_order", 0), a.get("span_order", 0)) for a in cur_final]
    if cur_orders != sorted(cur_orders):
        unexplained_reasons.append(
            "UNEXPLAINED_TAG_ORDER_VIOLATION: Final atoms in Current violate non-decreasing order"
        )

    b_unchanged_sigs = [
        slot_atom_signature(a) for a in base_final if a["source_slot"] not in ALLOWED_DIFF_SLOTS
    ]
    c_unchanged_sigs = [
        slot_atom_signature(a) for a in cur_final if a["source_slot"] not in ALLOWED_DIFF_SLOTS
    ]
    c_unchanged_set = set(c_unchanged_sigs)
    b_unchanged_set = set(b_unchanged_sigs)
    common_unchanged_b = [x for x in b_unchanged_sigs if x in c_unchanged_set]
    common_unchanged_c = [x for x in c_unchanged_sigs if x in b_unchanged_set]
    if common_unchanged_b != common_unchanged_c:
        unexplained_reasons.append(
            f"UNEXPLAINED_SEQUENCE_ORDER_MUTATION: Common retained unchanged-slot atoms order drifted: "
            f"base={common_unchanged_b} vs cur={common_unchanged_c}"
        )

    # ─────────────────────────────────────────────────────────────
    # 6. 逐原子差异因果证明 (Atom-by-atom Proof for Every Divergence)
    # ─────────────────────────────────────────────────────────────
    def sem_key(x):
        return (x["source_slot"], x.get("source_item_id", ""), x["text"], x.get("span_order", 0))

    base_keys = {sem_key(b) for b in base_final}
    cur_keys = {sem_key(c) for c in cur_final}

    added_atoms = [c for c in cur_final if sem_key(c) not in base_keys]
    removed_atoms = [b for b in base_final if sem_key(b) not in cur_keys]

    cur_src_ids = {a.get("source_item_id") for a in cur_src if a.get("source_item_id")}
    cur_sampled_new_jewelry = cur_src_ids & NEW_JEWELRY_IDS
    cur_sampled_new_lingerie = cur_src_ids & NEW_LINGERIE_IDS
    cur_sampled_new_imperfection = cur_src_ids & NEW_IMPERFECTION_IDS

    # 6A. 核验每一个新增终态原子的独立证明
    for a in added_atoms:
        slot = a["source_slot"]
        iid = a.get("source_item_id", "")
        aid = a["atom_id"]
        is_proven = False

        if slot == "jewelry" and iid in NEW_JEWELRY_IDS:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": a["text"],
                "category": f"NEW_JEWELRY_SAMPLED({iid})",
            })
        elif slot in ("clothing", "clothing_extension") and iid in NEW_LINGERIE_IDS:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": a["text"],
                "category": f"NEW_LINGERIE_SAMPLED({iid})",
            })
        elif slot == "imperfections" and iid in NEW_IMPERFECTION_IDS:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": a["text"],
                "category": "NEW_IMPERFECTION_SAMPLED(tan_lines)",
            })
        elif aid in cur_decs_by_prod and not verify_decision_legality(cur_decs_by_prod[aid], cur_src_map, valid_rules, cur_bindings):
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": a["text"],
                "category": f"PRODUCED_BY_DECISION({cur_decs_by_prod[aid]['rule_id']})",
            })
        elif slot in ALLOWED_DIFF_SLOTS and aid in cur_src_map:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": a["text"],
                "category": f"PRNG_CANDIDATE_SHIFT({slot})",
            })

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_ADDED_ATOM: Atom '{aid}' ('{a['text']}', slot={slot}, id={iid}) was added without valid causal proof"
            )

    claimed_absence_dec_ids: Set[str] = set()

    # 6B. 核验每一个缺失原子的独立证明
    for b in removed_atoms:
        slot = b["source_slot"]
        iid = b.get("source_item_id", "")
        aid = b["atom_id"]
        is_proven = False

        # 1. 缺席状态原子：必须证明在 cur 中确实存在针对该特定缺席状态的合法 absence_state_conflict 裁决，禁止跨状态冒领或一裁多销
        if iid in CANONICAL_ABSENCE_STATES:
            matching_decs = []
            for d in cur_decs:
                if d.get("decision_id") in claimed_absence_dec_ids:
                    continue
                if not (d.get("rule_id") == "absence_state_conflict" or d.get("reason_code") == "absence_state_conflict"):
                    continue
                dtid = d.get("target_atom_id")
                # 精确核对目标
                if dtid and dtid in cur_src_map:
                    t_atom = cur_src_map[dtid]
                    if t_atom.get("source_item_id") != iid:
                        continue
                    if aid in cur_src_map and dtid != aid:
                        continue
                else:
                    if dtid != aid and d.get("before_text") != b.get("text"):
                        continue

                # 核验该裁决本身的合法性
                if not verify_decision_legality(d, cur_src_map, valid_rules, cur_bindings):
                    matching_decs.append(d)

            if matching_decs:
                matched_dec = matching_decs[0]
                claimed_absence_dec_ids.add(matched_dec["decision_id"])
                is_proven = True
                explained_attributions.append({
                    "atom_id": aid,
                    "text": b["text"],
                    "category": f"ABSENCE_MUTUAL_EXCLUSION_DROP({iid})",
                    "decision": matched_dec,
                })
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_ABSENCE_DROP: Absence state atom '{b['text']}' (id={iid}) was dropped without valid, unclaimed matching absence conflict decision"
                )

        # 2. 有裁决记录的 drop / replace（裁决本身必须合法）
        elif any(
            d.get("target_atom_id") == aid
            and not verify_decision_legality(d, cur_src_map, valid_rules, cur_bindings)
            for d in cur_decs
        ):
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": b["text"],
                "category": "DECISION_TARGET_DROPPED_OR_REPLACED",
            })

        # 3. 有去重记录
        elif aid in cur_dedup_by_id:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": b["text"],
                "category": "DEDUPLICATED",
            })

        # 4. 有预算截断记录
        elif aid in cur_budget_by_id:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": b["text"],
                "category": "BUDGET_FILTERED",
            })

        # 5. 允许变更槽位在两版间未抽中该候选（PRNG 候选偏移）
        elif slot in ALLOWED_DIFF_SLOTS and aid not in cur_src_map:
            is_proven = True
            explained_attributions.append({
                "atom_id": aid,
                "text": b["text"],
                "category": f"PRNG_CANDIDATE_SHIFT({slot})",
            })

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_REMOVED_ATOM: Baseline atom '{aid}' ('{b['text']}', slot={slot}, id={iid}) was removed without valid causal proof"
            )

    # 缺席状态互斥与外穿配饰承载总结
    absence_conflict_decs = [
        d for d in cur_decs
        if (d.get("rule_id") == "absence_state_conflict" or d.get("reason_code") == "absence_state_conflict")
        and d.get("action") == "drop"
    ]

    outerwear_carrier_bound = False
    if cur_sampled_new_jewelry & OUTERWEAR_JEWELRY_IDS:
        for aid, cb in cur_bindings.items():
            if cb.get("carrier_selected_id") in OUTERWEAR_JEWELRY_IDS:
                outerwear_carrier_bound = True
                explained_attributions.append({
                    "category": "OUTERWEAR_CARRIER_BOUND",
                    "carrier_selected_id": cb.get("carrier_selected_id"),
                    "state_atom_id": aid,
                })

    categories = []
    if cur_sampled_new_jewelry:
        categories.append(f"NEW_JEWELRY_SAMPLED({','.join(sorted(cur_sampled_new_jewelry))})")
    if cur_sampled_new_lingerie:
        categories.append(f"NEW_LINGERIE_SAMPLED({','.join(sorted(cur_sampled_new_lingerie))})")
    if cur_sampled_new_imperfection:
        categories.append("NEW_IMPERFECTION_SAMPLED(tan_lines)")
    if absence_conflict_decs:
        categories.append("ABSENCE_MUTUAL_EXCLUSION_DROP")
    if outerwear_carrier_bound:
        categories.append("OUTERWEAR_CARRIER_BOUND")

    diff_slot_names = []
    for sl in ALLOWED_DIFF_SLOTS:
        b_sigs = [slot_atom_signature(a) for a in b_slot_atoms[sl]]
        c_sigs = [slot_atom_signature(a) for a in c_slot_atoms[sl]]
        if b_sigs != c_sigs:
            diff_slot_names.append(sl)

    if diff_slot_names:
        categories.append(f"PRNG_CANDIDATE_SHIFT({','.join(sorted(diff_slot_names))})")

    has_structural_diff = (
        base_item["positive"] != cur_item["positive"]
        or [slot_atom_signature(a) for a in base_final] != [slot_atom_signature(a) for a in cur_final]
    )

    if has_structural_diff and not categories:
        unexplained_reasons.append("UNEXPLAINED_DIFFERENCE_WITHOUT_CAUSAL_ORIGIN")

    primary_category = categories[0] if categories else "IDENTICAL_STRUCTURAL"
    is_unexplained = len(unexplained_reasons) > 0

    return {
        "seed": seed,
        "is_unexplained": is_unexplained,
        "unexplained_reasons": unexplained_reasons,
        "category": primary_category,
        "all_categories": categories,
        "explained_attributions": explained_attributions,
        "base_positive": base_item["positive"],
        "cur_positive": cur_item["positive"],
        "base_final_atoms": base_final,
        "cur_final_atoms": cur_final,
        "base_source_atoms": base_src,
        "cur_source_atoms": cur_src,
        "base_decisions": base_decs,
        "cur_decisions": cur_decs,
        "carrier_bindings": cur_bindings,
        "details": {
            "cur_sampled_new_jewelry": list(cur_sampled_new_jewelry),
            "cur_sampled_new_lingerie": list(cur_sampled_new_lingerie),
            "cur_sampled_new_imperfection": list(cur_sampled_new_imperfection),
            "absence_decs_count": len(absence_conflict_decs),
            "base_hash": base_item["hash"],
            "cur_hash": cur_item["hash"],
        },
    }


def run_audit(
    total_seeds: int = 10000,
    scratch_dir: Path | None = None,
    output_archive: Path | None = None,
    output_report_md: Path | None = None,
    python_bin: str | None = None,
) -> Dict[str, Any]:
    scratch_dir = scratch_dir or (REPO_DIR / "scratch")
    scratch_dir.mkdir(parents=True, exist_ok=True)
    python_bin = python_bin or sys.executable

    # 1. 准备基线仓库
    baseline_dir = scratch_dir / f"baseline_{BASELINE_COMMIT}"
    export_commit(BASELINE_COMMIT, baseline_dir)

    # 2. 收集当前词库权威叶子词条集合与合法规则集合
    catalog_leaf_tags = load_authoritative_catalog_leaf_tags(REPO_DIR / "data")
    valid_rules = load_authoritative_resolver_rules(REPO_DIR / "data")
    print(
        f"[*] Loaded authoritative catalog leaf tags: "
        f"jewelry={len(catalog_leaf_tags['jewelry'])}, "
        f"lingerie={len(catalog_leaf_tags['clothing'])}, "
        f"imperfections={len(catalog_leaf_tags['imperfections'])}"
    )
    print(f"[*] Loaded authoritative resolver rules: {len(valid_rules)} rules")

    # 3. 运行双版本批处理
    t0 = time.time()
    print(f"[*] Running baseline ({BASELINE_COMMIT}) generation for {total_seeds} seeds...")
    base_data, base_hash = run_batch_parallel(baseline_dir, total_seeds, python_bin=python_bin)
    print(f"    Baseline batch hash: {base_hash}")
    if total_seeds == 10000:
        if base_hash != EXPECTED_BASELINE_HASH:
            raise RuntimeError(
                f"Baseline hash mismatch! Expected {EXPECTED_BASELINE_HASH}, got {base_hash}"
            )
        print("    ✅ Baseline hash strictly matches frozen 2df289a baseline!")

    print(f"[*] Running current working tree generation for {total_seeds} seeds...")
    cur_data, cur_hash = run_batch_parallel(REPO_DIR, total_seeds, python_bin=python_bin)
    print(f"    Current batch hash:  {cur_hash}")

    # 4. 比对与逐种子逐原子因果归因
    print("[*] Auditing seed divergences with atomic causal verification...")
    identical_count = 0
    divergent_count = 0
    category_counts: Dict[str, int] = {}
    audited_diffs: List[Dict[str, Any]] = []
    unexplained_seeds: List[Dict[str, Any]] = []

    for s in range(total_seeds):
        b = base_data[s]
        c = cur_data[s]

        b_sigs = [atom_signature(a) for a in b["final_atoms"]]
        c_sigs = [atom_signature(a) for a in c["final_atoms"]]

        c_ids = [a["atom_id"] for a in c["final_atoms"]]
        b_ids = [a["atom_id"] for a in b["final_atoms"]]
        has_dup = (len(c_ids) != len(set(c_ids))) or (len(b_ids) != len(set(b_ids)))

        # 结构化相同且 positive 相同的种子
        if b_sigs == c_sigs and b["positive"] == c["positive"] and not has_dup:
            attr = attribute_step3_seed_diff(s, b, c, catalog_leaf_tags, valid_rules)
            if not attr["is_unexplained"]:
                identical_count += 1
                continue
            # 内部校验失败
            divergent_count += 1
            unexplained_seeds.append(attr)
            audited_diffs.append(attr)
            cat = attr["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1
            continue

        divergent_count += 1
        attr = attribute_step3_seed_diff(s, b, c, catalog_leaf_tags, valid_rules)
        cat = attr["category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1
        audited_diffs.append(attr)

        if attr["is_unexplained"]:
            unexplained_seeds.append(attr)

    duration = time.time() - t0
    print(f"[*] Audit complete in {duration:.1f}s.")
    print(f"    Total seeds:       {total_seeds}")
    print(f"    Identical seeds:   {identical_count} ({identical_count/total_seeds:.2%})")
    print(f"    Divergent seeds:   {divergent_count} ({divergent_count/total_seeds:.2%})")
    print(f"    Unexplained seeds: {len(unexplained_seeds)} (Gate required: == 0)")
    print("    Category Breakdown:")
    for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
        print(f"      - {cat}: {cnt} ({cnt/divergent_count:.2%})")

    # 5. 保存完整归档 (含双版本完整原子、决策与逐原子证明)
    archive_path = output_archive or (scratch_dir / "audit_step3_evidence.json.gz")
    with gzip.open(archive_path, "wt", encoding="utf-8") as gf:
        json.dump({
            "baseline_commit": BASELINE_COMMIT,
            "baseline_hash": base_hash,
            "current_hash": cur_hash,
            "total_seeds": total_seeds,
            "identical_count": identical_count,
            "divergent_count": divergent_count,
            "unexplained_count": len(unexplained_seeds),
            "category_counts": category_counts,
            "unexplained_seeds": unexplained_seeds,
            "diffs": audited_diffs,
        }, gf, ensure_ascii=False)
    print(f"[+] Full audit archive saved to: {archive_path}")

    # 6. 保存 Markdown 报告
    report_md_path = output_report_md or (scratch_dir / "audit_step3_report.md")
    md_content = f"""# 第 3 步增量双版本差异因果归因审计报告

- **增量基线**: `{BASELINE_COMMIT}` (第 2 步收尾提交)
- **受测版本**: 工作区当前状态 (HEAD: `{BASELINE_COMMIT}` + 第 3 步改动)
- **种子范围**: Seeds `0..{total_seeds - 1}` (共 {total_seeds:,} 种子)
- **基线汇总哈希**: `{base_hash}` ({'✅ 100% 吻合' if base_hash == EXPECTED_BASELINE_HASH else '⚠️ 漂移'})
- **当前汇总哈希**: `{cur_hash}`
- **未解释差异项 (UNEXPLAINED)**: **`{len(unexplained_seeds)}`** ({'PASS (硬门禁通过)' if len(unexplained_seeds) == 0 else 'FAIL (阻断)'})
- **完整因果归档**: `{archive_path.name}` (包含全部 {divergent_count:,} 组差异种子的双版本 source_atoms, final_atoms, decisions 及逐原子证明)

## 差异统计

| 统计指标 | 种子数 | 占比 | 门禁状态 |
|---|---|---|---|
| 完全一致种子 (Bit-exact Identical) | {identical_count:,} | {identical_count/total_seeds:.2%} | PASS |
| 差异种子总数 (Divergent Seeds) | {divergent_count:,} | {divergent_count/total_seeds:.2%} | INFO |
| 未解释差异 (UNEXPLAINED) | {len(unexplained_seeds):,} | {len(unexplained_seeds)/total_seeds:.2%} | {'PASS' if len(unexplained_seeds) == 0 else 'FAIL'} |

## 归因分类明细

| 归因分类 | 差异种子数 | 占差异比例 |
|---|---|---|
"""
    for cat, cnt in sorted(category_counts.items(), key=lambda x: -x[1]):
        pct = cnt / divergent_count if divergent_count > 0 else 0
        md_content += f"| `{cat}` | {cnt:,} | {pct:.2%} |\n"

    report_md_path.write_text(md_content, encoding="utf-8")
    print(f"[+] Markdown report saved to: {report_md_path}")

    summary_res = {
        "baseline_commit": BASELINE_COMMIT,
        "baseline_hash": base_hash,
        "current_hash": cur_hash,
        "total_seeds": total_seeds,
        "identical_count": identical_count,
        "divergent_count": divergent_count,
        "unexplained_count": len(unexplained_seeds),
        "category_counts": category_counts,
        "duration": duration,
    }

    if len(unexplained_seeds) > 0:
        print(f"\n❌ [GATE FAIL] {len(unexplained_seeds)} unexplained seeds detected! Blocking.")
        return summary_res

    print("\n✅ [GATE PASS] Zero unexplained differences detected! Safe to proceed.")
    return summary_res


def main():
    parser = argparse.ArgumentParser(description="Step 3 Cross-Catalog Divergence Audit")
    parser.add_argument("--seeds", type=int, default=10000, help="Number of seeds to audit (default: 10000)")
    parser.add_argument("--output-archive", type=Path, default=None, help="Output archive path (.json.gz)")
    parser.add_argument("--output-report", type=Path, default=None, help="Output markdown report path (.md)")
    args = parser.parse_args()

    python_bin = str(Path(sys.executable))
    res = run_audit(
        total_seeds=args.seeds,
        output_archive=args.output_archive,
        output_report_md=args.output_report,
        python_bin=python_bin,
    )
    if res["unexplained_count"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
