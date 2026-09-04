"""
context_affinity.py — 多信号情境自洽亲和度模型与统一采样计算核心 (rc8 权威实现)

实现规范：
1. 槽位专属 ID Registry 与严格跨目录/跨槽位引用强校验 (Fail-Closed)；
2. 上下文多信号合并算法：scene(1.0) 与 theme(0.6) 质量加权并归一化；
3. 全情境概率混合公式：P_s = 0.15 * G_s + 0.85 * Σ(alpha(c) * C(c,s))；
4. 单次归一化 categorical draw 直接抽样；
5. 内部全浮点精度保持，避免提前量化造成归一化漂移。
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

if __package__:
    from .errors import AffinityConfigurationError
    from .models import ORDERED_CONTEXT_IDS, ContextProfile
else:
    from lib.errors import AffinityConfigurationError
    from lib.models import ORDERED_CONTEXT_IDS, ContextProfile


ORDERED_SLOT_IDS: Tuple[str, ...] = (
    "clothing",
    "character",
    "makeup",
    "hairstyle",
    "jewelry",
    "props",
    "tattoo",
    "liquids",
    "pose",
    "expression",
    "lighting",
    "film",
    "shot_type",
    "camera_angle",
)


def load_slot_id_registries(data_dir: Path) -> Dict[str, Set[str]]:
    """加载并构建各槽位专属的选择器 ID 白名单 (槽位间严格隔离)。"""
    slot_ids: Dict[str, Set[str]] = {}

    # 1. clothing
    clothing_path = data_dir / "clothing.json"
    if not clothing_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {clothing_path.name}")
    c_doc = json.loads(clothing_path.read_text(encoding="utf-8"))
    slot_ids["clothing"] = {c["id"] for c in c_doc.get("categories", []) if "id" in c}
    if not slot_ids["clothing"]:
        raise AffinityConfigurationError(f"Slot registry for 'clothing' is empty in {clothing_path.name}")

    # 2. character
    char_path = data_dir / "characters.json"
    if not char_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {char_path.name}")
    ch_doc = json.loads(char_path.read_text(encoding="utf-8"))
    slot_ids["character"] = {c["id"] for c in ch_doc.get("characters", []) if "id" in c}
    if not slot_ids["character"]:
        raise AffinityConfigurationError(f"Slot registry for 'character' is empty in {char_path.name}")

    # 3. makeup
    makeup_path = data_dir / "makeup.json"
    if not makeup_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {makeup_path.name}")
    m_doc = json.loads(makeup_path.read_text(encoding="utf-8"))
    slot_ids["makeup"] = {m["id"] for m in m_doc.get("categories", []) if "id" in m}
    if not slot_ids["makeup"]:
        raise AffinityConfigurationError(f"Slot registry for 'makeup' is empty in {makeup_path.name}")

    # 4 & 5. accessories: hairstyle 与 jewelry 严格隔离
    acc_path = data_dir / "accessories.json"
    if not acc_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {acc_path.name}")
    acc_doc = json.loads(acc_path.read_text(encoding="utf-8"))
    slot_ids["hairstyle"] = {h["id"] for h in acc_doc.get("hairstyles", []) if "id" in h}
    slot_ids["jewelry"] = {j["id"] for j in acc_doc.get("headwear_jewelry", []) if "id" in j}
    if not slot_ids["hairstyle"]:
        raise AffinityConfigurationError(f"Slot registry for 'hairstyle' is empty in {acc_path.name}")
    if not slot_ids["jewelry"]:
        raise AffinityConfigurationError(f"Slot registry for 'jewelry' is empty in {acc_path.name}")

    # 6. props
    props_path = data_dir / "props.json"
    if not props_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {props_path.name}")
    p_doc = json.loads(props_path.read_text(encoding="utf-8"))
    p_ids = {p["id"] for p in p_doc.get("categories", []) if "id" in p}
    for cat in p_doc.get("categories", []):
        if "items" in cat:
            for it in cat["items"]:
                if "id" in it:
                    p_ids.add(it["id"])
    slot_ids["props"] = p_ids
    if not slot_ids["props"]:
        raise AffinityConfigurationError(f"Slot registry for 'props' is empty in {props_path.name}")

    # 7. tattoo
    tat_path = data_dir / "tattoos.json"
    if not tat_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {tat_path.name}")
    tat_doc = json.loads(tat_path.read_text(encoding="utf-8"))
    slot_ids["tattoo"] = {t["id"] for t in tat_doc.get("categories", []) if "id" in t}
    if not slot_ids["tattoo"]:
        raise AffinityConfigurationError(f"Slot registry for 'tattoo' is empty in {tat_path.name}")

    # 8. liquids: liquid_effects + none
    nud_path = data_dir / "nudity_levels.json"
    if not nud_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {nud_path.name}")
    nud_doc = json.loads(nud_path.read_text(encoding="utf-8"))
    l_ids = {liq["id"] for liq in nud_doc.get("liquid_effects", []) if "id" in liq}
    l_ids.add("none")
    slot_ids["liquids"] = l_ids

    # 9. pose
    pose_path = data_dir / "poses.json"
    if not pose_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {pose_path.name}")
    pose_doc = json.loads(pose_path.read_text(encoding="utf-8"))
    slot_ids["pose"] = {p["id"] for p in pose_doc.get("pose_categories", []) if "id" in p}
    if not slot_ids["pose"]:
        raise AffinityConfigurationError(f"Slot registry for 'pose' is empty in {pose_path.name}")

    # 10. expression
    expr_path = data_dir / "expressions.json"
    if not expr_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {expr_path.name}")
    expr_doc = json.loads(expr_path.read_text(encoding="utf-8"))
    slot_ids["expression"] = {e["id"] for e in expr_doc.get("emotions", []) if "id" in e}
    if not slot_ids["expression"]:
        raise AffinityConfigurationError(f"Slot registry for 'expression' is empty in {expr_path.name}")

    # 11. lighting: professional, cinematic, special, erotic, preset_combos
    light_path = data_dir / "lighting.json"
    if not light_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {light_path.name}")
    light_doc = json.loads(light_path.read_text(encoding="utf-8"))
    lt_ids = set()
    for sec in ["professional_lighting", "cinematic_lighting", "special_effects", "erotic_lighting", "preset_combos"]:
        for item in light_doc.get(sec, []):
            if isinstance(item, dict) and "id" in item:
                lt_ids.add(item["id"])
    slot_ids["lighting"] = lt_ids
    if not slot_ids["lighting"]:
        raise AffinityConfigurationError(f"Slot registry for 'lighting' is empty in {light_path.name}")

    # 12. film
    film_path = data_dir / "film_stocks.json"
    if not film_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {film_path.name}")
    film_doc = json.loads(film_path.read_text(encoding="utf-8"))
    slot_ids["film"] = {f["id"] for f in film_doc.get("film_stocks", []) if "id" in f}
    if not slot_ids["film"]:
        raise AffinityConfigurationError(f"Slot registry for 'film' is empty in {film_path.name}")

    # 13 & 14. shot_types: shot_type 与 camera_angle 严格隔离
    shot_path = data_dir / "shot_types.json"
    if not shot_path.is_file():
        raise AffinityConfigurationError(f"Required catalog file missing: {shot_path.name}")
    shot_doc = json.loads(shot_path.read_text(encoding="utf-8"))
    slot_ids["shot_type"] = {s["id"] for s in shot_doc.get("shot_types", []) if "id" in s}
    slot_ids["camera_angle"] = {a["id"] for a in shot_doc.get("camera_angles", []) if "id" in a}
    if not slot_ids["shot_type"]:
        raise AffinityConfigurationError(f"Slot registry for 'shot_type' is empty in {shot_path.name}")
    if not slot_ids["camera_angle"]:
        raise AffinityConfigurationError(f"Slot registry for 'camera_angle' is empty in {shot_path.name}")

    return slot_ids


class ContextAffinityRegistry:
    """情境亲和度配置单例与自主 Fail-Closed 校验注册中心。"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.slot_registries = load_slot_id_registries(self.data_dir)
        self.raw_doc = self._load_and_validate()
        self.matrix: Dict[str, Dict[str, Dict[str, Any]]] = self.raw_doc["matrix"]
        self.signal_weights: Dict[str, float] = self.raw_doc["signal_weights"]
        self.mixture: Dict[str, float] = self.raw_doc["mixture"]

    def _load_and_validate(self) -> Dict[str, Any]:
        affinity_path = self.data_dir / "context_affinity.json"
        if not affinity_path.is_file():
            raise AffinityConfigurationError(f"context_affinity.json not found at: {affinity_path}")

        try:
            doc = json.loads(affinity_path.read_text(encoding="utf-8"))
        except Exception as e:
            raise AffinityConfigurationError(f"Failed to parse context_affinity.json: {e}") from e

        if not isinstance(doc, dict):
            raise AffinityConfigurationError(f"context_affinity.json root must be object, got {type(doc).__name__}")

        # 1. 顶层键集校验
        expected_top_keys = {"schema_version", "contexts", "slots", "signal_weights", "mixture", "matrix"}
        actual_top_keys = set(doc.keys())
        if actual_top_keys != expected_top_keys:
            extra = actual_top_keys - expected_top_keys
            missing = expected_top_keys - actual_top_keys
            raise AffinityConfigurationError(
                f"context_affinity.json top-level keys mismatch: extra={extra}, missing={missing}"
            )

        if doc.get("schema_version") != "1.0":
            raise AffinityConfigurationError(f"Invalid schema_version: {doc.get('schema_version')}")

        contexts = doc.get("contexts")
        if not isinstance(contexts, list) or tuple(contexts) != ORDERED_CONTEXT_IDS:
            raise AffinityConfigurationError(f"Contexts must strictly match ORDERED_CONTEXT_IDS: got {contexts}")

        slots = doc.get("slots")
        if not isinstance(slots, list) or tuple(slots) != ORDERED_SLOT_IDS:
            raise AffinityConfigurationError(f"Slots must strictly match ORDERED_SLOT_IDS: got {slots}")

        sig_w = doc.get("signal_weights", {})
        if not isinstance(sig_w, dict) or set(sig_w.keys()) != {"scene", "theme"} or sig_w["scene"] != 1.0 or sig_w["theme"] != 0.6:
            raise AffinityConfigurationError(f"Invalid signal_weights: {sig_w}")

        mix = doc.get("mixture", {})
        if not isinstance(mix, dict) or set(mix.keys()) != {"global", "affinity"} or mix["global"] != 0.15 or mix["affinity"] != 0.85:
            raise AffinityConfigurationError(f"Invalid mixture: {mix}")

        # 2. 14×14 恰好 196 个单元深度封闭联合与跨目录校验
        matrix = doc.get("matrix")
        if not isinstance(matrix, dict):
            raise AffinityConfigurationError("matrix must be a dict")

        if set(matrix.keys()) != set(ORDERED_CONTEXT_IDS):
            raise AffinityConfigurationError(f"matrix context keys mismatch: {set(matrix.keys()) ^ set(ORDERED_CONTEXT_IDS)}")

        for ctx in ORDERED_CONTEXT_IDS:
            row = matrix.get(ctx)
            if not isinstance(row, dict):
                raise AffinityConfigurationError(f"matrix row for context '{ctx}' must be a dict")
            if set(row.keys()) != set(ORDERED_SLOT_IDS):
                raise AffinityConfigurationError(f"matrix row '{ctx}' slot keys mismatch: {set(row.keys()) ^ set(ORDERED_SLOT_IDS)}")

            for slot in ORDERED_SLOT_IDS:
                cell = row.get(slot)
                if not isinstance(cell, dict):
                    raise AffinityConfigurationError(f"Cell [{ctx}][{slot}] must be an object")

                mode = cell.get("mode")
                valid_slot_catalog = self.slot_registries.get(slot)
                if not valid_slot_catalog:
                    raise AffinityConfigurationError(f"Slot registry for '{slot}' is missing or empty")

                if mode == "neutral":
                    if set(cell.keys()) != {"mode"}:
                        raise AffinityConfigurationError(f"Neutral cell [{ctx}][{slot}] contains extra keys: {set(cell.keys()) - {'mode'}}")
                elif mode == "weighted":
                    if set(cell.keys()) != {"mode", "candidates"}:
                        raise AffinityConfigurationError(f"Weighted cell [{ctx}][{slot}] keys mismatch: {set(cell.keys())}")
                    cands = cell.get("candidates")
                    if not isinstance(cands, list) or len(cands) == 0:
                        raise AffinityConfigurationError(f"Weighted cell [{ctx}][{slot}] candidates must be non-empty list")

                    seen_ids: Set[str] = set()
                    for idx, c in enumerate(cands):
                        if not isinstance(c, dict) or set(c.keys()) != {"id", "weight"}:
                            raise AffinityConfigurationError(f"Candidate [{ctx}][{slot}][{idx}] must have 'id' and 'weight'")
                        cid = c["id"]
                        wt = c["weight"]

                        if not isinstance(cid, str) or not cid:
                            raise AffinityConfigurationError(f"Candidate [{ctx}][{slot}][{idx}] id must be non-empty str")
                        if cid in seen_ids:
                            raise AffinityConfigurationError(f"Duplicate candidate id '{cid}' in cell [{ctx}][{slot}]")
                        seen_ids.add(cid)

                        if isinstance(wt, bool) or not isinstance(wt, (int, float)) or not math.isfinite(wt) or wt <= 0:
                            raise AffinityConfigurationError(f"Candidate [{ctx}][{slot}][{cid}] weight must be finite positive number, got {wt}")

                        # 槽位专属 ID 校验 (拦截跨槽位混用，如 hairstyle 混入 jewelry)
                        if cid not in valid_slot_catalog:
                            raise AffinityConfigurationError(
                                f"Candidate id '{cid}' in cell [{ctx}][{slot}] not found in valid catalog for slot '{slot}'"
                            )

                    # 加权候选集与合法槽位词库交集非空断言
                    if not (seen_ids & valid_slot_catalog):
                        raise AffinityConfigurationError(
                            f"Weighted cell [{ctx}][{slot}] has empty intersection with valid catalog for slot '{slot}'"
                        )
                else:
                    raise AffinityConfigurationError(f"Invalid mode '{mode}' in cell [{ctx}][{slot}], must be 'weighted' or 'neutral'")

        return doc


def compute_context_profile(
    scene_context_ids: Sequence[str],
    theme_context_ids: Sequence[str],
    scene_item_id: Optional[str] = None,
    theme_id: Optional[str] = None,
) -> ContextProfile:
    """根据实际采样的场景与主题 context_ids 执行精确多信号合并。

    契约算法：
    raw(c) = 1[c ∈ S] * 1.0 / |S| + 1[c ∈ T] * 0.6 / |T|
    alpha(c) = raw(c) / sum(raw(k))

    不变量：
    - 若 S 与 T 均为空，显式回退为 generic=1.0；
    - 未知 context ID 必须 Fail-Closed 抛出 AffinityConfigurationError；
    - S 与 T 必须先稳定去重，再按实际集合基数计算贡献，杜绝重复 context 偏置；
    - 仅消费 scene_res.context_ids 与 theme_res.context_ids；
    - 内部全精度浮点计算，权重归一化和严格为 1.0。
    """
    for c in scene_context_ids:
        if c not in ORDERED_CONTEXT_IDS:
            raise AffinityConfigurationError(f"Unknown context ID '{c}' in scene_context_ids")
    for c in theme_context_ids:
        if c not in ORDERED_CONTEXT_IDS:
            raise AffinityConfigurationError(f"Unknown context ID '{c}' in theme_context_ids")

    def _stable_dedup(items: Sequence[str]) -> Tuple[str, ...]:
        seen = set()
        res = []
        for it in items:
            if it not in seen:
                seen.add(it)
                res.append(it)
        return tuple(res)

    s_dedup = _stable_dedup(scene_context_ids)
    t_dedup = _stable_dedup(theme_context_ids)

    if not s_dedup and not t_dedup:
        weights_tuple = (("generic", 1.0),)
        return ContextProfile(
            weights=weights_tuple,
            scene_item_id=scene_item_id,
            theme_id=theme_id,
            scene_context_ids=(),
            theme_context_ids=(),
        )

    raw_weights: Dict[str, float] = {}
    if s_dedup:
        s_contrib = 1.0 / float(len(s_dedup))
        for c in s_dedup:
            raw_weights[c] = raw_weights.get(c, 0.0) + s_contrib

    if t_dedup:
        t_contrib = 0.6 / float(len(t_dedup))
        for c in t_dedup:
            raw_weights[c] = raw_weights.get(c, 0.0) + t_contrib

    total_raw = sum(raw_weights.values())
    assert total_raw > 0.0

    # 规范按 ORDERED_CONTEXT_IDS 保序输出并归一化
    norm_weights: List[Tuple[str, float]] = []
    accum = 0.0
    active_keys = [c for c in ORDERED_CONTEXT_IDS if c in raw_weights]
    for idx, c in enumerate(active_keys):
        if idx == len(active_keys) - 1:
            # 最后一项消除浮点截断误差，确保 sum == 1.0
            wt = 1.0 - accum
        else:
            wt = raw_weights[c] / total_raw
            accum += wt
        norm_weights.append((c, wt))

    return ContextProfile(
        weights=tuple(norm_weights),
        scene_item_id=scene_item_id,
        theme_id=theme_id,
        scene_context_ids=s_dedup,
        theme_context_ids=t_dedup,
    )


def compute_slot_distribution(
    slot_name: str,
    profile: ContextProfile,
    catalog_candidates: Sequence[str],
    matrix: Mapping[str, Mapping[str, Dict[str, Any]]],
    base_weights: Optional[Mapping[str, float]] = None,
) -> Dict[str, float]:
    """计算槽位最终概率分布 P_s。

    契约公式：
    P_s = 0.15 * G_s + 0.85 * Σ_c (alpha(c) * C(c, s))
    """
    if not catalog_candidates:
        return {}

    candidates_list = list(catalog_candidates)
    n_cands = len(candidates_list)

    # 1. 全局合法分布 G_s
    g_dist: Dict[str, float] = {}
    if base_weights:
        b_sum = sum(base_weights.get(cid, 1.0) for cid in candidates_list)
        for cid in candidates_list:
            g_dist[cid] = base_weights.get(cid, 1.0) / b_sum
    else:
        uniform_p = 1.0 / float(n_cands)
        for cid in candidates_list:
            g_dist[cid] = uniform_p

    # 2. 亲和混合分布 Σ_c (alpha(c) * C(c, s))
    affinity_dist: Dict[str, float] = {cid: 0.0 for cid in candidates_list}

    for ctx_id, alpha in profile.weights:
        if alpha <= 0.0:
            continue
        cell = matrix[ctx_id][slot_name]
        mode = cell["mode"]

        if mode == "neutral":
            # neutral 单元严格定义为 C(c,s) = G_s
            for cid in candidates_list:
                affinity_dist[cid] += alpha * g_dist[cid]
        elif mode == "weighted":
            cand_items = cell["candidates"]
            # 计算矩阵候选与当前已知合法集合 catalog_candidates 的交集
            valid_set = set(candidates_list)
            intersection = [c for c in cand_items if c["id"] in valid_set]

            if not intersection:
                raise AffinityConfigurationError(
                    f"Weighted cell [{ctx_id}][{slot_name}] candidate intersection with catalog pool is empty!"
                )

            cand_wt_sum = sum(c["weight"] for c in intersection)
            for c in intersection:
                c_id = c["id"]
                c_p = c["weight"] / cand_wt_sum
                affinity_dist[c_id] += alpha * c_p
        else:
            raise AffinityConfigurationError(f"Unknown cell mode: {mode}")

    # 3. 最终混合 P_s = 0.15 * G_s + 0.85 * Affinity
    final_p: Dict[str, float] = {}
    total_p = 0.0
    for cid in candidates_list:
        p_val = 0.15 * g_dist[cid] + 0.85 * affinity_dist[cid]
        final_p[cid] = p_val
        total_p += p_val

    # 归一化保障
    for cid in candidates_list:
        final_p[cid] /= total_p

    return final_p


def sample_categorical(distribution: Mapping[str, float], rng: Random) -> str:
    """使用单次归一化 categorical draw 直接从分布抽样。"""
    if not distribution:
        raise ValueError("Cannot sample from empty distribution")

    items = list(distribution.items())
    u = rng.random()
    accum = 0.0
    for key, p in items:
        accum += p
        if u <= accum:
            return key
    return items[-1][0]
