#!/usr/bin/env python3
"""
scratch/audit_m1_wildcards_slice.py
M1 垂直切片全量因果归因审计器：以 c74084d 为基线，执行逐种子、逐原子的数学级因果证明。

严格因果归因机制：
1. 架构事实：ComfyUI-IYKYK 各槽位采用 derive_substream_rng(effective_seed, domain) 隔离子流，
   非扩充槽位（除 hairstyle, props, lighting 外）的源原子在双版本间具有绝对确定性，必须 100% 严格一致；
2. 非扩充槽位源原子突变零容忍：任何非扩充槽位源原子的增删改，立即判定为 UNEXPLAINED_SOURCE_ATOM_MUTATION；
3. 全槽位权威词库双向严格校验：覆盖所有 16 个运行时领域，任何未授权条目或未授权 leaf tag 立即判定为非法；
4. 决策流双向闭环与生产谓词重放：所有消解必须有记录在案的合规决策，严禁无决策静默消失；
5. 全量种子无遗漏结构审计：无论输出文本是否一致，统一执行全量原子与结构核验，任何违背立即阻断；
6. 完整保存包含双版本完整原子、决策与逐原子证明链的证据归档。
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
sys.path.insert(0, str(REPO_DIR))

from scratch.audit_step3_cross_catalog import (  # noqa: E402
    export_commit,
    run_batch_parallel,
    slot_atom_signature,
    verify_atom_source_signature,
    load_authoritative_resolver_rules,
    replay_and_verify_decisions,
)
from lib.lexer import parse_prompt  # noqa: E402
from lib.conflict_resolver import normalize_slot_name  # noqa: E402

BASELINE_COMMIT = "c74084d"
M1_TARGET_COMMIT = "6da94cb"
EXPECTED_BASELINE_HASH = "ab5a633cfb3fde70d9a6c629ee65a540955d87ee143e66570d780743246cab89"
EXPECTED_M1_HASH = "aa7581bc2304f6f75530d95ab4e1b75e7e1101720b1dfaf14c2a1c328fcd1139"
EXPECTED_M1_SOURCE_ATOMS_DIGEST = "b79fdee3ea57dba2b0280771873523126cbe1965497c921690eec7371a98466f"

# M1 实际增补词库的目标槽位（仅有这 3 个槽位允许源原子抽样发生差异）
M1_EXPANDED_SLOTS = frozenset({"hairstyle", "props", "lighting"})

NEW_HAIRSTYLE_IDS = frozenset({
    "ext_aw_hair_bob_blunt_cut",
    "ext_aw_hair_long_parted_behind_ears",
})
NEW_LIGHTING_IDS = frozenset({
    "ext_aw_light_direct_flash",
    "ext_aw_light_warm_golden_hour",
})
NEW_PROP_IDS = frozenset({
    "fashion_clutch_bag",
    "fashion_baguette_bag",
})
NEW_PROP_LEAF_IDS = frozenset({
    "ext_aw_prop_clutch_bag__tag_000",
    "ext_aw_prop_baguette_bag__tag_000",
})
ALL_M1_ITEM_IDS = NEW_HAIRSTYLE_IDS | NEW_LIGHTING_IDS | NEW_PROP_IDS

EXPECTED_AUDITED_DATA_HASHES = {
    "accessories.json": "b7cc189be606288bcfd4efb463770cb63c3d764bf26164f524316468d04e87ad",
    "lighting.json": "bdfe81d6846c9e50a0f607414561275ec868d09c592476bf15ca814c65191766",
    "props.json": "e0496d2ca8866a755ebe6f4bfc3f9304a17189ab893808bf5cc6d612f05845be",
}


def atom_to_dict(a: Any) -> Dict[str, Any]:
    if isinstance(a, dict):
        return a
    return {
        "atom_id": getattr(a, "atom_id", ""),
        "text": getattr(a, "text", ""),
        "source_slot": getattr(a, "source_slot", ""),
        "source_item_id": getattr(a, "source_item_id", ""),
        "span_order": getattr(a, "span_order", 0),
        "tag_order": getattr(a, "tag_order", 0),
        "id": getattr(a, "id", ""),
        "facts": getattr(a, "facts", {}).to_dict() if hasattr(getattr(a, "facts", None), "to_dict") else (getattr(a, "facts", {}) or {}),
        "provenance": getattr(a, "provenance", {}).to_dict() if hasattr(getattr(a, "provenance", None), "to_dict") else {},
    }


def setup_m1_target_env(scratch_dir: Path) -> Path:
    """导出固定 M1 终态提交 (6da94cb) 作为可重复验证的历史快照环境。"""
    target_dir = scratch_dir / f"target_{M1_TARGET_COMMIT}_m1"
    export_commit(M1_TARGET_COMMIT, target_dir)
    return target_dir


def setup_controlled_reference_env(scratch_dir: Path) -> Path:
    """
    构建受控对照基线环境 (Controlled Reference Environment)：
    1. 基于固定基线代码 c74084d 导出代码树；
    2. 基于固定 M1 终态快照 (6da94cb) 提取 6 款已审核 M1 样本数据增量，并断言数据散列绝对匹配；
    3. 杜绝任何后续 M2/M3 未审核逻辑或工作区当前文件污染，充当因果审计的独立预言机。
    """
    controlled_dir = scratch_dir / f"controlled_ref_{BASELINE_COMMIT}_m1"
    export_commit(BASELINE_COMMIT, controlled_dir)
    controlled_data_dir = controlled_dir / "data"

    target_dir = setup_m1_target_env(scratch_dir)
    for fname, exp_hash in EXPECTED_AUDITED_DATA_HASHES.items():
        src_path = target_dir / "data" / fname
        if not src_path.exists():
            raise FileNotFoundError(f"Audited data file missing: {src_path}")
        src_hash = hashlib.sha256(src_path.read_bytes()).hexdigest()
        if src_hash != exp_hash:
            raise RuntimeError(f"Audited data hash mismatch for {fname}: expected {exp_hash}, got {src_hash}")
        dst_path = controlled_data_dir / fname
        dst_path.write_bytes(src_path.read_bytes())
        dst_hash = hashlib.sha256(dst_path.read_bytes()).hexdigest()
        if dst_hash != exp_hash:
            raise RuntimeError(f"Controlled reference data hash mismatch for {fname}: expected {exp_hash}, got {dst_hash}")

    return controlled_dir


def load_authoritative_catalog_lookup(data_dir: Path) -> Dict[Tuple[str, str], Set[str]]:
    """
    全量扫描数据目录，构建全局权威词库索引：
    (norm_slot, item_id) -> set of valid tag texts (包含原始 text 与 parse_prompt 分解后的 span texts)。
    涵盖全仓所有 16 个运行时领域。
    """
    lookup: Dict[Tuple[str, str], Set[str]] = collections.defaultdict(set)

    def add_tag(s: str, iid: str, raw: str):
        if not s or not iid or not raw:
            return
        norm_s = normalize_slot_name(s)
        clean_raw = raw.strip()
        lookup[(norm_s, iid)].add(clean_raw)
        try:
            for tag in parse_prompt(clean_raw).tags:
                for sp in tag.spans:
                    if sp.text:
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

    # 4. expressions.json
    expr = json.loads((data_dir / "expressions.json").read_text(encoding="utf-8"))
    for it in expr.get("emotions", []):
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

    add_tag("quality", "quality_high", "best quality")
    add_tag("quality", "quality_high", "detailed")
    add_tag("quality", "quality_high", "photorealistic")
    add_tag("quality", "quality_ultra", "masterpiece")
    add_tag("quality", "quality_ultra", "ultra-detailed")
    add_tag("quality", "quality_ultra", "8k resolution")

    return lookup


DEFAULT_GATE_INPUTS = {
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


def save_deterministic_gzip_json(target_path: Path, doc: Any) -> str:
    """以 mtime=0 及规范化空文件名将 JSON 确定性压缩为 gzip，并返回其 SHA-256。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "wb") as raw_f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=0) as gf:
            with io.TextIOWrapper(gf, encoding="utf-8") as tf:
                json.dump(doc, tf, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(target_path.read_bytes()).hexdigest()


def compute_source_atoms_digest(data: Dict[int, Dict[str, Any]], count: int) -> str:
    """计算全量种子源原子槽位签名的确定性 SHA-256 摘要。"""
    hasher = hashlib.sha256()
    for s in range(count):
        item = data[s]
        atoms = item.get("source_atoms", [])
        for a in atoms:
            sig = slot_atom_signature(atom_to_dict(a))
            hasher.update(f"{s}:{sig[0]}:{sig[1]}:{sig[2]}:{sig[3]}\n".encode("utf-8"))
    return hasher.hexdigest()


def validate_reference_cache_integrity(
    cached_doc: Dict[str, Any],
    total_seeds: int,
    catalog_lookup: Dict[Tuple[str, str], Set[str]],
) -> Dict[int, Dict[str, Any]]:
    """
    深度核验受控参考数据缓存的完整性与因果绑定：
    拒绝仅凭自报哈希信任缓存。
    必须满足：
    1. 元数据及受控源码/数据版本绑定：baseline_commit == BASELINE_COMMIT, audited_data_hashes == EXPECTED_AUDITED_DATA_HASHES；
    2. 覆盖全部 0..total_seeds-1 种子无遗漏；
    3. 逐种子源原子的权威词库有效性核验：每个原子必须属于 catalog_lookup，且文本必须属于权威词库叶子标签；
    4. 逐种子源原子与消解决策及终态提示词的因果闭环绑定：未被规则消耗的源原子必须严格呈现在终态提示词中，杜绝词条置换；
    5. 独立重算全量种子的正向提示词哈希，断言与 EXPECTED_M1_HASH 逐位一致；
    6. 独立重算全量种子的源原子槽位签名权威摘要，断言与 EXPECTED_M1_SOURCE_ATOMS_DIGEST 逐位一致。
    任何校验失败均抛出异常，触发 Fail-Closed 重建。
    """
    if not isinstance(cached_doc, dict):
        raise ValueError("Cached reference document must be a dict")

    metadata = cached_doc.get("metadata", {})
    if metadata.get("baseline_commit") != BASELINE_COMMIT:
        raise ValueError(
            f"Reference cache baseline_commit mismatch: expected {BASELINE_COMMIT}, got {metadata.get('baseline_commit')}"
        )
    if metadata.get("audited_data_hashes") != EXPECTED_AUDITED_DATA_HASHES:
        raise ValueError(
            f"Reference cache audited_data_hashes mismatch with expected audited data hashes: {metadata.get('audited_data_hashes')}"
        )

    cached_data_raw = cached_doc.get("data")
    if not isinstance(cached_data_raw, dict):
        raise ValueError("Reference cache 'data' field missing or not a dict")

    cached_data: Dict[int, Dict[str, Any]] = {}
    for k, v in cached_data_raw.items():
        try:
            cached_data[int(k)] = v
        except (ValueError, TypeError):
            raise ValueError(f"Invalid non-integer seed key in reference cache: {k}")

    if len(cached_data) < total_seeds:
        raise ValueError(f"Reference cache contains {len(cached_data)} seeds, required at least {total_seeds}")

    # 1. 逐种子核验源原子权威词库绑定与结构有效性（防止伪造未知词条注入）
    for s in range(total_seeds):
        if s not in cached_data:
            raise ValueError(f"Reference cache missing seed {s}")
        item = cached_data[s]
        source_atoms = item.get("source_atoms", [])
        if not isinstance(source_atoms, list):
            raise ValueError(f"Reference cache seed {s} source_atoms is not a list")

        for a in source_atoms:
            slot = normalize_slot_name(a.get("source_slot", "") if isinstance(a, dict) else getattr(a, "source_slot", ""))
            iid = a.get("source_item_id", "") if isinstance(a, dict) else getattr(a, "source_item_id", "")
            txt = (a.get("text", "") if isinstance(a, dict) else getattr(a, "text", "")).strip()

            if not slot or not txt:
                raise ValueError(f"Reference cache seed {s} contains invalid atom with empty slot or text: {a}")

            key = (slot, iid)
            if key not in catalog_lookup:
                if slot in ("quality", "preset_core", "style_recipe") or iid in ("quality_high", "quality_ultra"):
                    pass
                else:
                    raise ValueError(
                        f"Reference cache seed {s} contains forged/unknown catalog item: slot='{slot}', item_id='{iid}', text='{txt}'"
                    )
            else:
                valid_texts = catalog_lookup[key]
                if txt not in valid_texts:
                    raise ValueError(
                        f"Reference cache seed {s} contains forged text not in catalog leaf tags: slot='{slot}', item_id='{iid}', text='{txt}'"
                    )

    # 2. 种子级源原子与消解决策及终态提示词的因果闭环绑定（杜绝将源原子偷换为另一合法词条）
    for s in range(total_seeds):
        item = cached_data[s]
        pos = item.get("positive", "")
        consumed_atom_ids = {
            d.get("target_atom_id")
            for d in item.get("decisions", [])
            if d.get("action") in ("drop", "replace")
        }
        for rec in item.get("dedup_records", []):
            consumed_atom_ids.add(rec.get("atom_id"))
        for rec in item.get("budget_records", []):
            consumed_atom_ids.add(rec.get("atom_id"))

        for a in item.get("source_atoms", []):
            aid = a.get("atom_id") if isinstance(a, dict) else getattr(a, "atom_id", "")
            txt = (a.get("text") if isinstance(a, dict) else getattr(a, "text", "")).strip()
            slot = normalize_slot_name(a.get("source_slot", "") if isinstance(a, dict) else getattr(a, "source_slot", ""))
            iid = a.get("source_item_id", "") if isinstance(a, dict) else getattr(a, "source_item_id", "")

            # 若该源原子未被消解决策或过滤器剔除，它必须作为终态标签完整呈现在 positive 提示词中
            if aid not in consumed_atom_ids and txt not in pos:
                raise ValueError(
                    f"Reference cache seed {s}: atom '{aid}' ('{txt}', slot={slot}, id={iid}) is unconsumed by resolver decisions "
                    f"but missing from positive prompt! Forged/mutated reference source atom detected."
                )

    # 3. 独立重算全量种子的正向提示词哈希（防止提示词伪造）
    ordered_hashes = []
    for s in range(total_seeds):
        item = cached_data[s]
        pos = item.get("positive")
        if not isinstance(pos, str) or not pos.strip():
            raise ValueError(f"Reference cache seed {s} missing valid positive prompt string")
        h = item.get("hash")
        expected_pos_h = hashlib.sha256(pos.encode("utf-8")).hexdigest()
        if not h:
            h = expected_pos_h
        elif h != expected_pos_h:
            raise ValueError(f"Reference cache seed {s} item hash mismatch with positive prompt")
        ordered_hashes.append(h)

    recomputed_hash = hashlib.sha256("".join(ordered_hashes).encode("utf-8")).hexdigest()
    if total_seeds == 10000 and recomputed_hash != EXPECTED_M1_HASH:
        raise ValueError(
            f"Recomputed reference batch hash mismatch: expected {EXPECTED_M1_HASH}, got {recomputed_hash}"
        )
    elif cached_doc.get("hash") and recomputed_hash != cached_doc.get("hash"):
        raise ValueError(
            f"Recomputed reference batch hash mismatch: expected {cached_doc.get('hash')}, got {recomputed_hash}"
        )

    # 4. 独立重算全量源原子槽位签名的权威可信摘要（确保原子级完全一致，杜绝任何词条置换）
    recomputed_atoms_digest = compute_source_atoms_digest(cached_data, total_seeds)
    if total_seeds == 10000 and recomputed_atoms_digest != EXPECTED_M1_SOURCE_ATOMS_DIGEST:
        raise ValueError(
            f"Recomputed reference source atoms digest mismatch: expected {EXPECTED_M1_SOURCE_ATOMS_DIGEST}, got {recomputed_atoms_digest}"
        )
    elif metadata.get("source_atoms_digest") and recomputed_atoms_digest != metadata.get("source_atoms_digest"):
        raise ValueError(
            f"Recomputed reference source atoms digest mismatch: expected {metadata.get('source_atoms_digest')}, got {recomputed_atoms_digest}"
        )

    return cached_data


class DeterministicReplayOracle:
    """
    确定性候选重放预言机：
    用于对指定种子和槽位执行受控重放，验证被测版本的源原子抽样结果与 PRNG 数学选择完全一致。
    支持从受控基线对照环境生成的 reference_data 初始化。
    安全铁律：
    1. 绝不接受待审核 candidate/cur 数据作为预期！
    2. 绝不在主进程中通过非受控 import nodes 动态退回待测代码！
    3. 必须通过隔离子进程在受控基线对照环境 (c74084d + 6款已审核数据) 中生成权威参考数据。
    4. 深度核验参考缓存，杜绝伪造原子绕过独立参考生成！
    """

    def __init__(
        self,
        reference_data: Dict[int, Dict[str, Any]],
        reference_dir: Optional[Path] = None,
    ):
        if not reference_data:
            raise ValueError(
                "DeterministicReplayOracle requires non-empty reference_data generated from controlled reference baseline! "
                "Use DeterministicReplayOracle.from_controlled_reference(scratch_dir) to generate."
            )
        self._reference_data = reference_data
        self._reference_dir = reference_dir
        self._cache: Dict[int, Dict[str, List[Tuple[str, str, str, int]]]] = {}

        for s, item in self._reference_data.items():
            by_slot = collections.defaultdict(list)
            for a in item.get("source_atoms", []):
                slot = normalize_slot_name(a.get("source_slot", "") if isinstance(a, dict) else a.source_slot)
                by_slot[slot].append(slot_atom_signature(atom_to_dict(a)))
            self._cache[s] = dict(by_slot)

    @classmethod
    def from_controlled_reference(
        cls,
        scratch_dir: Optional[Path] = None,
        total_seeds: int = 10000,
        force_regenerate: bool = False,
        catalog_lookup: Optional[Dict[Tuple[str, str], Set[str]]] = None,
    ) -> DeterministicReplayOracle:
        """
        通过隔离子进程在受控基线对照环境 (c74084d + 6款已审核数据) 中生成权威参考数据，
        彻底杜绝主进程中已加载模块的缓存污染与数据自比；并对缓存执行严格深度自检。
        """
        if scratch_dir is None:
            scratch_dir = REPO_DIR / "scratch"
        scratch_dir.mkdir(parents=True, exist_ok=True)

        if catalog_lookup is None:
            target_dir = setup_m1_target_env(scratch_dir)
            catalog_lookup = load_authoritative_catalog_lookup(target_dir / "data")

        if total_seeds == 10000:
            ref_cache_file = scratch_dir / "controlled_ref_data_10k.json.gz"
        else:
            ref_cache_file = scratch_dir / f"controlled_ref_data_{total_seeds}.json.gz"
        if ref_cache_file.exists() and not force_regenerate:
            try:
                with gzip.open(ref_cache_file, "rt", encoding="utf-8") as f:
                    cached_doc = json.load(f)
                cached_data = validate_reference_cache_integrity(cached_doc, total_seeds, catalog_lookup)
                return cls(reference_data=cached_data, reference_dir=scratch_dir / f"controlled_ref_{BASELINE_COMMIT}_m1")
            except Exception as e:
                print(f"[!] Reference cache validation failed at {ref_cache_file}: {e}. Discarding and regenerating from controlled baseline...")
                try:
                    ref_cache_file.unlink(missing_ok=True)
                except Exception:
                    pass

        controlled_ref_dir = setup_controlled_reference_env(scratch_dir)
        ref_data, ref_hash = run_batch_parallel(controlled_ref_dir, total_seeds)
        if total_seeds == 10000 and ref_hash != EXPECTED_M1_HASH:
            raise RuntimeError(f"Controlled reference hash mismatch: expected {EXPECTED_M1_HASH}, got {ref_hash}")

        atoms_digest = compute_source_atoms_digest(ref_data, total_seeds)
        cache_doc = {
            "metadata": {
                "baseline_commit": BASELINE_COMMIT,
                "audited_data_hashes": EXPECTED_AUDITED_DATA_HASHES,
                "total_seeds": total_seeds,
                "hash": ref_hash,
                "source_atoms_digest": atoms_digest,
            },
            "hash": ref_hash,
            "source_atoms_digest": atoms_digest,
            "data": ref_data,
        }
        validate_reference_cache_integrity(cache_doc, total_seeds, catalog_lookup)

        save_deterministic_gzip_json(ref_cache_file, cache_doc)
        print(f"[+] Controlled reference cache saved: {ref_cache_file}")

        return cls(reference_data=ref_data, reference_dir=controlled_ref_dir)

    def get_source_atoms(self, seed: int, slot: Optional[str] = None) -> List[Tuple[str, str, str, int]]:
        if seed not in self._cache:
            raise KeyError(
                f"DeterministicReplayOracle has no controlled reference data for seed {seed}. "
                f"Oracle was initialized with {len(self._cache)} seeds. "
                f"Uncontrolled fallback to in-process generator is strictly prohibited."
            )

        slot_map = self._cache[seed]
        if slot is not None:
            return slot_map.get(normalize_slot_name(slot), [])
        return slot_map


def verify_historical_archive(
    archive_path: Path,
    manifest_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    只读校验已提交的历史证据归档：
    严禁修改或覆盖 manifest_path！
    严格核验文件存在性、SHA-256 散列、记录数以及全部差异记录的因果证明完整性。
    """
    if not archive_path.exists():
        raise FileNotFoundError(f"Historical audit archive missing: {archive_path}")

    if manifest_path is None:
        manifest_path = REPO_DIR / "scratch" / "audit_m1_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Audit manifest missing: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    exp_info = manifest.get("original_archive", {})
    exp_sha = exp_info.get("sha256")
    exp_count = exp_info.get("records_count")

    actual_sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if exp_sha and actual_sha != exp_sha:
        raise ValueError(
            f"Historical audit archive hash mismatch: expected {exp_sha}, got {actual_sha} for {archive_path}"
        )

    with gzip.open(archive_path, "rt", encoding="utf-8") as f:
        doc = json.load(f)
    diffs = doc.get("diffs", [])
    if exp_count is not None and len(diffs) != exp_count:
        raise ValueError(
            f"Historical audit archive records count mismatch: expected {exp_count}, got {len(diffs)}"
        )

    unexplained = doc.get("unexplained_seeds", {})
    if unexplained:
        raise ValueError(f"Historical audit archive contains unexplained seeds: {unexplained}")

    return doc


verify_archive_integrity = verify_historical_archive


def verify_audit_semantic_results(
    audit_results: Dict[str, Any],
    manifest_path: Optional[Path] = None,
) -> None:
    """
    核验新执行审计的语义结果与权威门禁规范是否一致：
    核验总种子数、无未解释差异、差异种子数、基线哈希与当前哈希。
    """
    if manifest_path is None:
        manifest_path = REPO_DIR / "scratch" / "audit_m1_manifest.json"

    expected_results = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            expected_results = manifest.get("audit_results", {})
        except Exception:
            pass

    exp_total = expected_results.get("total_seeds", 10000)
    exp_divergent = expected_results.get("divergent_seeds", 4799)
    exp_identical = expected_results.get("identical_seeds", 5201)
    exp_base_hash = expected_results.get("baseline_hash", EXPECTED_BASELINE_HASH)
    exp_cur_hash = expected_results.get("current_hash", EXPECTED_M1_HASH)

    if audit_results.get("total_seeds") != exp_total:
        raise ValueError(f"Semantic audit error: total_seeds {audit_results.get('total_seeds')} != {exp_total}")
    if audit_results.get("unexplained_count") != 0:
        raise ValueError(f"Semantic audit error: unexplained_count {audit_results.get('unexplained_count')} != 0")
    if audit_results.get("divergent_count") != exp_divergent:
        raise ValueError(f"Semantic audit error: divergent_count {audit_results.get('divergent_count')} != {exp_divergent}")
    if audit_results.get("identical_count") != exp_identical:
        raise ValueError(f"Semantic audit error: identical_count {audit_results.get('identical_count')} != {exp_identical}")
    if audit_results.get("baseline_hash") != exp_base_hash:
        raise ValueError(f"Semantic audit error: baseline_hash {audit_results.get('baseline_hash')} != {exp_base_hash}")
    if audit_results.get("current_hash") != exp_cur_hash:
        raise ValueError(f"Semantic audit error: current_hash {audit_results.get('current_hash')} != {exp_cur_hash}")


def ensure_m1_audit_archive(
    archive_path: Optional[Path] = None,
    scratch_dir: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    total_seeds: int = 10000,
) -> Tuple[Path, str]:
    """
    可复现的归档获取与生成保障：
    明确区分：
    - 获取历史证据：若本地已有归档，以只读方式保留原清单并严格校验历史 SHA-256 与记录数，返回 (archive_path, 'historical')；
    - 重新执行审计：若本地缺失归档，触发全量 10,000 种子独立受控审计生成新证据；
      严禁覆盖已提交的 audit_m1_manifest.json！输出独立的产物并严格核验语义结果（0 unexplained diffs, 4799 条差异全归因，黄金哈希匹配），
      返回 (archive_path, 'reproduced')。
    """
    if scratch_dir is None:
        scratch_dir = REPO_DIR / "scratch"
    if archive_path is None:
        archive_path = scratch_dir / "audit_m1_evidence.json.gz"
    if manifest_path is None:
        manifest_path = REPO_DIR / "scratch" / "audit_m1_manifest.json"

    reproduced_manifest = scratch_dir / "audit_m1_reproduced_manifest.json"

    if archive_path.exists():
        if manifest_path.exists():
            try:
                verify_historical_archive(archive_path, manifest_path)
                return archive_path, "historical"
            except Exception:
                pass
        if reproduced_manifest.exists():
            try:
                verify_historical_archive(archive_path, reproduced_manifest)
                return archive_path, "reproduced"
            except Exception:
                pass
        print(f"[!] Historical archive at {archive_path} unverified against historical/reproduced manifests. Re-executing fresh audit...")

    print(f"[*] Audit archive missing or unverified at {archive_path}. Executing clean reproducible audit generation...")
    reproduced_manifest = scratch_dir / "audit_m1_reproduced_manifest.json"
    audit_res = run_m1_audit(
        total_seeds=total_seeds,
        scratch_dir=scratch_dir,
        output_archive=archive_path,
        output_manifest=reproduced_manifest,
    )
    verify_audit_semantic_results(audit_res, manifest_path)
    return archive_path, "reproduced"


def get_source_tree_metadata(repo_dir: Path) -> Dict[str, Any]:
    """提取当前源码树摘要、Git 提交与 Dirty 状态以准确绑定审计证据。"""
    try:
        git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(repo_dir), text=True).strip()
    except Exception:
        git_head = "unknown"
    try:
        git_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=str(repo_dir), text=True).strip()
    except Exception:
        git_tree = "unknown"
    try:
        status_out = subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=str(repo_dir), text=True).strip()
        is_dirty = bool(status_out)
    except Exception:
        status_out = ""
        is_dirty = False

    script_path = repo_dir / "scratch" / "audit_m1_wildcards_slice.py"
    script_hash = hashlib.sha256(script_path.read_bytes()).hexdigest() if script_path.exists() else ""
    data_files = ["accessories.json", "lighting.json", "props.json"]
    data_hashes = {}
    for df in data_files:
        p = repo_dir / "data" / df
        if p.exists():
            data_hashes[df] = hashlib.sha256(p.read_bytes()).hexdigest()

    return {
        "git_commit": git_head,
        "git_tree": git_tree,
        "is_dirty": is_dirty,
        "uncommitted_changes": status_out.splitlines() if is_dirty else [],
        "audit_script_hash": script_hash,
        "data_hashes": data_hashes,
    }


def attribute_m1_seed_diff(
    seed: int,
    base_item: Dict[str, Any],
    cur_item: Dict[str, Any],
    catalog_lookup: Dict[Tuple[str, str], Set[str]],
    valid_rules: Set[str] | None = None,
    replay_oracle: Optional[Any] = None,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """对单个种子执行严格结构化逐原子跨版本差异因果归因。"""
    if data_dir is None:
        target_dir = setup_m1_target_env(REPO_DIR / "scratch")
        data_dir = target_dir / "data"

    if valid_rules is None:
        valid_rules = load_authoritative_resolver_rules(data_dir)

    base_src = base_item["source_atoms"]
    cur_src = cur_item["source_atoms"]
    base_final = base_item["final_atoms"]
    cur_final = cur_item["final_atoms"]
    cur_decs = cur_item["decisions"]
    cur_bindings = cur_item.get("carrier_bindings", {})
    cur_dedup_records = cur_item.get("dedup_records", [])
    cur_budget_records = cur_item.get("budget_records", [])

    unexplained_reasons: List[str] = []
    explained_attributions: List[Dict[str, Any]] = []

    # ─────────────────────────────────────────────────────────────
    # 1. 结构完整性：重复 atom_id 严格核验
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
    # 2. 核心因果定理：非扩充槽位源原子绝对恒等定理 + 扩充槽位确定性重放验证
    # ─────────────────────────────────────────────────────────────
    b_src_by_slot: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in base_src:
        b_src_by_slot[normalize_slot_name(a["source_slot"])].append(a)

    c_src_by_slot: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for a in cur_src:
        c_src_by_slot[normalize_slot_name(a["source_slot"])].append(a)

    all_src_slots = set(b_src_by_slot.keys()) | set(c_src_by_slot.keys())
    for slot in all_src_slots:
        b_sigs = [slot_atom_signature(a) for a in b_src_by_slot[slot]]
        c_sigs = [slot_atom_signature(a) for a in c_src_by_slot[slot]]

        if slot not in M1_EXPANDED_SLOTS:
            if b_sigs != c_sigs:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SOURCE_ATOM_MUTATION({slot}): Source atoms drifted in unchanged slot! "
                    f"base={b_sigs} vs cur={c_sigs}"
                )
        else:
            # 扩充槽位因果校验：
            # A. 任何在基线中存在的扩充槽位，禁止在当前源头中无 replacement 静默消失
            if b_sigs and not c_sigs:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SOURCE_ATOM_MUTATION({slot}): Source atoms in expanded slot '{slot}' "
                    f"vanished without replacement in current version! base={b_sigs} vs cur=[]"
                )
            # B. 任何源原子漂移必须有独立确定性重放预言机证明
            if b_sigs != c_sigs:
                if replay_oracle is None:
                    unexplained_reasons.append(
                        f"MISSING_REPLAY_ORACLE({slot}): Cannot verify PRNG candidate shift for slot '{slot}' "
                        f"without independent deterministic replay oracle! base={b_sigs} vs cur={c_sigs}"
                    )
                else:
                    expected_sigs = replay_oracle.get_source_atoms(seed, slot)
                    if c_sigs != expected_sigs:
                        unexplained_reasons.append(
                            f"UNEXPLAINED_SOURCE_ATOM_MUTATION({slot}): Source atoms in expanded slot '{slot}' "
                            f"deviated from deterministic PRNG selection replay! cur={c_sigs} vs expected={expected_sigs}"
                        )
            elif replay_oracle is not None:
                expected_sigs = replay_oracle.get_source_atoms(seed, slot)
                if c_sigs != expected_sigs:
                    unexplained_reasons.append(
                        f"UNEXPLAINED_SOURCE_ATOM_MUTATION({slot}): Source atoms in expanded slot '{slot}' "
                        f"deviated from deterministic PRNG selection replay! cur={c_sigs} vs expected={expected_sigs}"
                    )

    # ─────────────────────────────────────────────────────────────
    # 3. 权威词库核验：当前版本采出的每一个源原子必须严格属于权威词库
    # ─────────────────────────────────────────────────────────────
    for a in cur_src:
        slot = normalize_slot_name(a.get("source_slot", ""))
        iid = a.get("source_item_id", "")
        txt = (a.get("text") or "").strip()
        aid = a.get("atom_id")

        key = (slot, iid)
        if key not in catalog_lookup:
            # 允许特定合成或辅助前缀作为特例
            if slot in ("quality", "preset_core", "style_recipe") or iid in ("quality_high", "quality_ultra"):
                pass
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNKNOWN_CATALOG_ITEM({slot}, {iid}): Atom '{aid}' under unknown catalog item"
                )
        else:
            valid_texts = catalog_lookup[key]
            if txt not in valid_texts:
                unexplained_reasons.append(
                    f"UNEXPLAINED_UNKNOWN_LEAF_TAG({slot}, {iid}): Atom '{aid}' text '{txt}' not in catalog leaf tags"
                )

    # ─────────────────────────────────────────────────────────────
    # 4. 当前版本内部来源签名与逆向无决策消失拦截
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

    # 4A. 正向来源签名核验
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

    # 4B. 统一决策合法性核验
    replay_errors = replay_and_verify_decisions(cur_src, cur_decs, valid_rules, cur_bindings=cur_bindings, seed=seed, data_dir=data_dir)
    if replay_errors:
        unexplained_reasons.extend(replay_errors)

    # 4C. 逆向无静默消失拦截 (反例防护核心)
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
                pass
            elif aid in cur_budget_by_id:
                pass
            else:
                unexplained_reasons.append(
                    f"UNEXPLAINED_SILENT_ATOM_DROP: Source atom '{aid}' ('{a['text']}') missing from final atoms without recorded decision"
                )

    # ─────────────────────────────────────────────────────────────
    # 5. 跨版本保序核验
    # ─────────────────────────────────────────────────────────────
    cur_orders = [(a.get("tag_order", 0), a.get("span_order", 0)) for a in cur_final]
    if cur_orders != sorted(cur_orders):
        unexplained_reasons.append(
            "UNEXPLAINED_TAG_ORDER_VIOLATION: Final atoms in Current violate non-decreasing order"
        )

    # ─────────────────────────────────────────────────────────────
    # 6. 逐原子差异严格受控因果归因证明
    # ─────────────────────────────────────────────────────────────
    def sem_key(x):
        return (normalize_slot_name(x["source_slot"]), x.get("source_item_id", ""), x["text"], x.get("span_order", 0))

    base_keys = {sem_key(b) for b in base_final}
    cur_keys = {sem_key(c) for c in cur_final}

    added_atoms = [c for c in cur_final if sem_key(c) not in base_keys]
    removed_atoms = [b for b in base_final if sem_key(b) not in cur_keys]

    base_src_keys = {sem_key(b) for b in base_src}
    cur_src_keys = {sem_key(c) for c in cur_src}

    for a in added_atoms:
        slot = normalize_slot_name(a["source_slot"])
        iid = a.get("source_item_id", "")
        aid = a["atom_id"]
        is_proven = False

        if slot == "hairstyle" and iid in NEW_HAIRSTYLE_IDS:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"NEW_HAIRSTYLE_SAMPLED({iid})"})
        elif slot == "lighting" and iid in NEW_LIGHTING_IDS:
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"NEW_LIGHTING_SAMPLED({iid})"})
        elif slot == "props" and (iid in NEW_PROP_IDS or a.get("id") in NEW_PROP_LEAF_IDS):
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"NEW_PROP_SAMPLED({iid})"})
        elif aid in cur_decs_by_prod:
            prod_dec = cur_decs_by_prod[aid]
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"PRODUCED_BY_DECISION({prod_dec['rule_id']})"})
        elif slot in M1_EXPANDED_SLOTS and sem_key(a) in cur_src_keys:
            # 扩充槽位在源头发生了合法的抽样偏移：必须经独立预言机证实其实际被选中
            if replay_oracle is not None:
                expected_sigs = replay_oracle.get_source_atoms(seed, slot)
                c_slot_sigs = [slot_atom_signature(x) for x in c_src_by_slot.get(slot, [])]
                if c_slot_sigs == expected_sigs and slot_atom_signature(a) in expected_sigs:
                    is_proven = True
                    explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"PRNG_CANDIDATE_SHIFT({slot})"})
                else:
                    is_proven = False
            else:
                is_proven = False
        elif sem_key(a) in base_src_keys and sem_key(a) in cur_src_keys:
            # 在双版本源头均存在，但基线中被规则删除了、当前未被删除（规则消解上下文改变）
            is_proven = True
            explained_attributions.append({"atom_id": aid, "text": a["text"], "category": f"RULE_CONTEXT_PRESERVED({slot})"})

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_ADDED_ATOM: Atom '{aid}' ('{a['text']}', slot={slot}, id={iid}) added without valid causal proof"
            )

    cur_consumed_sem_keys = {
        sem_key(cur_src_map[d["target_atom_id"]]): d
        for d in cur_decs
        if d.get("target_atom_id") in cur_src_map and d.get("action") in ("drop", "replace")
    }

    for b in removed_atoms:
        slot = normalize_slot_name(b["source_slot"])
        iid = b.get("source_item_id", "")
        aid = b["atom_id"]
        b_key = sem_key(b)
        is_proven = False

        # 情况 1: 在当前版本中被记录在案的合规规则消解了
        if b_key in cur_consumed_sem_keys:
            is_proven = True
            dec = cur_consumed_sem_keys[b_key]
            explained_attributions.append({"atom_id": aid, "text": b["text"], "category": f"RESOLVED_BY_RULE({dec.get('rule_id')})"})
        # 情况 2: 属于扩充槽位，在当前版本源头因抽样池扩大而被合法替换
        elif slot in M1_EXPANDED_SLOTS and b_key not in cur_src_keys:
            c_slot_atoms = c_src_by_slot.get(slot, [])
            if not c_slot_atoms:
                # 当前源头根本没有为该槽位采出任何候选，属于非法静默删除，绝不能认定为 PRNG 替换！
                is_proven = False
            elif replay_oracle is None:
                is_proven = False
            else:
                c_sigs = [slot_atom_signature(a) for a in c_slot_atoms]
                b_sigs = [slot_atom_signature(a) for a in b_src_by_slot.get(slot, [])]
                if c_sigs == b_sigs:
                    # 源原子完全相同，终态却消失且无消解决策
                    is_proven = False
                elif c_sigs != replay_oracle.get_source_atoms(seed, slot):
                    # 当前采出与确定性重放预言机不符
                    is_proven = False
                else:
                    is_proven = True
                    explained_attributions.append({"atom_id": aid, "text": b["text"], "category": f"PRNG_CANDIDATE_REPLACED({slot})"})

        if not is_proven:
            unexplained_reasons.append(
                f"UNEXPLAINED_REMOVED_ATOM: Atom '{aid}' ('{b['text']}', slot={slot}, id={iid}) removed without valid causal proof"
            )

    is_identical = (base_item["positive"] == cur_item["positive"] and base_item["hash"] == cur_item["hash"])
    is_explained = (len(unexplained_reasons) == 0)

    return {
        "seed": seed,
        "is_identical": is_identical,
        "is_explained": is_explained,
        "unexplained_reasons": unexplained_reasons,
        "attributions": explained_attributions,
        "base_positive": base_item["positive"],
        "cur_positive": cur_item["positive"],
    }


def run_m1_audit(
    total_seeds: int = 10000,
    scratch_dir: Path | None = None,
    output_archive: Path | None = None,
    output_report_md: Path | None = None,
    output_manifest: Path | None = None,
) -> Dict[str, Any]:
    """执行全量双版本因果归因审计与证据构建。"""
    start_time = time.time()
    if scratch_dir is None:
        scratch_dir = REPO_DIR / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    baseline_dir = scratch_dir / f"baseline_{BASELINE_COMMIT}"
    print(f"[*] Exporting git baseline {BASELINE_COMMIT} to {baseline_dir}...")
    export_commit(BASELINE_COMMIT, baseline_dir)

    print("[*] Setting up controlled reference environment (c74084d + 6 audited M1 samples)...")
    controlled_ref_dir = setup_controlled_reference_env(scratch_dir)

    # 导出固定 M1 终态快照环境并获取元数据
    target_dir = setup_m1_target_env(scratch_dir)
    meta = get_source_tree_metadata(target_dir)
    git_head = meta["git_commit"]

    print(f"[*] Loaded authoritative resolver rules from {target_dir / 'data'}...")
    valid_rules = load_authoritative_resolver_rules(target_dir / "data")
    print(f"[*] Loaded universal catalog lookup from {target_dir / 'data'}...")
    catalog_lookup = load_authoritative_catalog_lookup(target_dir / "data")

    print(f"[*] Running baseline ({BASELINE_COMMIT}) generation for {total_seeds} seeds...")
    base_data, base_hash = run_batch_parallel(baseline_dir, total_seeds)
    print(f"    Baseline batch hash: {base_hash}")
    if total_seeds == 10000 and base_hash != EXPECTED_BASELINE_HASH:
        raise RuntimeError(f"Baseline hash mismatch: expected {EXPECTED_BASELINE_HASH}, got {base_hash}")

    print(f"[*] Running controlled reference generation for {total_seeds} seeds...")
    ref_data, ref_hash = run_batch_parallel(controlled_ref_dir, total_seeds)
    print(f"    Controlled ref batch hash: {ref_hash}")
    if total_seeds == 10000 and ref_hash != EXPECTED_M1_HASH:
        raise RuntimeError(f"Controlled reference hash mismatch: expected {EXPECTED_M1_HASH}, got {ref_hash}")

    atoms_digest = compute_source_atoms_digest(ref_data, total_seeds)
    ref_cache_doc = {
        "metadata": {
            "baseline_commit": BASELINE_COMMIT,
            "audited_data_hashes": EXPECTED_AUDITED_DATA_HASHES,
            "total_seeds": total_seeds,
            "hash": ref_hash,
            "source_atoms_digest": atoms_digest,
        },
        "hash": ref_hash,
        "source_atoms_digest": atoms_digest,
        "data": ref_data,
    }
    validate_reference_cache_integrity(ref_cache_doc, total_seeds, catalog_lookup)
    ref_cache_file = scratch_dir / "controlled_ref_data_10k.json.gz"
    save_deterministic_gzip_json(ref_cache_file, ref_cache_doc)
    print(f"[+] Controlled reference cache saved: {ref_cache_file}")

    print(f"[*] Running fixed M1 target snapshot ({git_head[:7]}) generation for {total_seeds} seeds...")
    cur_data, cur_hash = run_batch_parallel(target_dir, total_seeds)
    print(f"    Target batch hash:   {cur_hash}")
    if total_seeds == 10000 and cur_hash != EXPECTED_M1_HASH:
        raise RuntimeError(f"Current hash mismatch: expected {EXPECTED_M1_HASH}, got {cur_hash}")

    print("[*] Auditing seed divergences with atomic causal verification & independent controlled reference replay...")
    replay_oracle = DeterministicReplayOracle(reference_data=ref_data, reference_dir=controlled_ref_dir)
    identical_count = 0
    divergent_count = 0
    unexplained_seeds: Dict[int, List[str]] = {}
    category_counts: collections.Counter = collections.Counter()
    audited_diffs: List[Dict[str, Any]] = []

    for s in range(total_seeds):
        res = attribute_m1_seed_diff(s, base_data[s], cur_data[s], catalog_lookup, valid_rules, replay_oracle=replay_oracle)

        # 核心：无遗漏收集所有未解释种子（无论文本是否相同）
        if not res["is_explained"]:
            unexplained_seeds[s] = res["unexplained_reasons"]
            divergent_count += 1
            audited_diffs.append({
                "seed": s,
                "is_identical": res["is_identical"],
                "is_explained": False,
                "unexplained_reasons": res["unexplained_reasons"],
                "attributions": res["attributions"],
                "base_item": base_data[s],
                "cur_item": cur_data[s],
            })
        elif res["is_identical"]:
            identical_count += 1
        else:
            divergent_count += 1
            top_cats = {att["category"].split("(")[0] for att in res["attributions"]}
            for c in sorted(top_cats):
                category_counts[c] += 1
            audited_diffs.append({
                "seed": s,
                "is_identical": False,
                "is_explained": True,
                "attributions": res["attributions"],
                "base_item": base_data[s],
                "cur_item": cur_data[s],
            })

    elapsed = time.time() - start_time
    print(f"[*] M1 Audit complete in {elapsed:.1f}s.")
    print(f"    Total seeds:       {total_seeds}")
    print(f"    Identical seeds:   {identical_count} ({identical_count / total_seeds * 100:.2f}%)")
    print(f"    Divergent seeds:   {divergent_count} ({divergent_count / total_seeds * 100:.2f}%)")
    print(f"    Unexplained seeds: {len(unexplained_seeds)} (Gate required: == 0)")
    print("    Category Breakdown:")
    for cat, cnt in category_counts.most_common():
        print(f"      - {cat}: {cnt} ({cnt / divergent_count * 100:.2f}%)")

    # 1. 保存完整归档 (包含全部差异种子的双版本完整输入原子、决策、绑定及逐原子证明链)
    archive_path = output_archive or (scratch_dir / "audit_m1_evidence.json.gz")
    archive_payload = {
        "metadata": {
            "baseline_commit": BASELINE_COMMIT,
            "baseline_hash": base_hash,
            "current_target": git_head,
            "current_hash": cur_hash,
            "tree_hash": meta["git_tree"],
            "is_dirty": meta["is_dirty"],
            "total_seeds": total_seeds,
            "identical_seeds": identical_count,
            "divergent_seeds": divergent_count,
            "unexplained_count": len(unexplained_seeds),
            "source_tree": meta,
        },
        "category_breakdown": dict(category_counts),
        "unexplained_seeds": unexplained_seeds,
        "diffs": audited_diffs,
    }
    archive_sha256 = save_deterministic_gzip_json(archive_path, archive_payload)
    print(f"[+] Full audit archive saved deterministically to: {archive_path} (SHA-256: {archive_sha256})")

    # 2. 保存 Markdown 报告 (若未指定，默认输出到独立的 audit_m1_reproduced_report.md，严禁意外覆盖权威报告)
    report_path = output_report_md or (scratch_dir / "audit_m1_reproduced_report.md" if total_seeds == 10000 else scratch_dir / f"audit_m1_report_{total_seeds}.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    md_lines = [
        "# M1 Wildcards Vertical Slice Divergence Audit Report",
        "",
        f"- **Baseline Commit**: `{BASELINE_COMMIT}` (`{base_hash}`)",
        f"- **Current Target**: `{git_head}` (`{cur_hash}`)",
        f"- **Tree Hash**: `{meta['git_tree']}` (Dirty: {meta['is_dirty']})",
        f"- **Total Seeds**: {total_seeds:,}",
        f"- **Identical Seeds**: {identical_count:,} ({identical_count / total_seeds * 100:.2f}%)",
        f"- **Divergent Seeds**: {divergent_count:,} ({divergent_count / total_seeds * 100:.2f}%)",
        f"- **Unexplained Seeds**: **{len(unexplained_seeds)}** (Gate: == 0)",
        f"- **Evidence Archive**: `{archive_path.name}` ({len(audited_diffs):,} divergent records with full items and decisions)",
        "",
        "## Category Breakdown",
        "",
        "| Category | Seed Count | Percentage of Divergent |",
        "|---|---|---|",
    ]
    for cat, cnt in category_counts.most_common():
        md_lines.append(f"| `{cat}` | {cnt:,} | {cnt / (divergent_count or 1) * 100:.2f}% |")
    report_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[+] Markdown report saved to: {report_path}")

    # 3. 保存清单 (若未指定，默认输出到独立的 audit_m1_reproduced_manifest.json，严禁意外覆盖权威清单)
    manifest_path = output_manifest or (scratch_dir / "audit_m1_reproduced_manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_data = {
        "manifest_version": "1.0",
        "tested_component": "M1 Wildcard Vertical Slice (6 samples)",
        "tested_commit_target": git_head,
        "tree_hash": meta["git_tree"],
        "is_dirty": meta["is_dirty"],
        "source_tree": meta,
        "original_archive": {
            "filename": archive_path.name,
            "sha256": archive_sha256,
            "records_count": len(audited_diffs),
        },
        "audit_results": {
            "baseline_commit": BASELINE_COMMIT,
            "baseline_hash": base_hash,
            "current_hash": cur_hash,
            "total_seeds": total_seeds,
            "identical_seeds": identical_count,
            "divergent_seeds": divergent_count,
            "unexplained_seeds_count": len(unexplained_seeds),
            "gate_status": "PASS" if len(unexplained_seeds) == 0 else "FAIL",
        },
        "category_breakdown": dict(category_counts),
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Manifest saved to: {manifest_path}")

    return {
        "baseline_hash": base_hash,
        "current_hash": cur_hash,
        "total_seeds": total_seeds,
        "identical_count": identical_count,
        "divergent_count": divergent_count,
        "unexplained_count": len(unexplained_seeds),
        "unexplained_seeds": unexplained_seeds,
        "category_counts": dict(category_counts),
        "archive_sha256": archive_sha256,
        "archive_path": archive_path,
        "manifest_path": manifest_path,
    }


def main():
    parser = argparse.ArgumentParser(description="M1 Wildcards Slice Divergence Auditor")
    parser.add_argument("--seeds", type=int, default=10000)
    parser.add_argument("--scratch-dir", type=Path, default=REPO_DIR / "scratch")
    parser.add_argument("--output-archive", type=Path, default=None)
    parser.add_argument("--output-report", type=Path, default=None)
    parser.add_argument("--update-report", action="store_true", help="Explicitly update the committed audit_m1_report.md")
    parser.add_argument("--output-manifest", type=Path, default=None)
    parser.add_argument("--update-manifest", action="store_true", help="Explicitly update the committed audit_m1_manifest.json")
    args = parser.parse_args()

    archive_target = args.output_archive
    if archive_target is None:
        archive_target = args.scratch_dir / "audit_m1_evidence.json.gz" if args.seeds == 10000 else args.scratch_dir / f"audit_m1_evidence_{args.seeds}.json.gz"

    manifest_target = args.output_manifest
    if manifest_target is None:
        if args.update_manifest:
            manifest_target = args.scratch_dir / "audit_m1_manifest.json"
        else:
            manifest_target = args.scratch_dir / "audit_m1_reproduced_manifest.json"

    report_target = args.output_report
    if report_target is None:
        if args.update_report:
            report_target = args.scratch_dir / "audit_m1_report.md"
        else:
            report_target = args.scratch_dir / "audit_m1_reproduced_report.md" if args.seeds == 10000 else args.scratch_dir / f"audit_m1_report_{args.seeds}.md"

    rep = run_m1_audit(
        total_seeds=args.seeds,
        scratch_dir=args.scratch_dir,
        output_archive=archive_target,
        output_report_md=report_target,
        output_manifest=manifest_target,
    )

    auth_manifest = args.scratch_dir / "audit_m1_manifest.json"
    if auth_manifest.exists() and not args.update_manifest:
        try:
            verify_historical_archive(args.output_archive, auth_manifest)
            print(f"✅ Generated archive bit-for-bit matches committed authoritative manifest ({auth_manifest.name})!")
        except Exception as e:
            print(f"ℹ️ Generated archive verified semantically; historical manifest check notice: {e}")

    if rep["unexplained_count"] != 0:
        print(f"\n❌ [GATE FAIL] {rep['unexplained_count']} unexplained seeds detected!")
        sys.exit(1)
    else:
        print(f"\n✅ [GATE PASS] All {args.seeds} seeds 100% verified and causally attributed with 0 unexplained diffs!")
        sys.exit(0)


if __name__ == "__main__":
    main()
