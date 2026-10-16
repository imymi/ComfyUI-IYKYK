"""
tests/test_m2_matrix_and_sampling.py
ComfyUI-IYKYK M2 阶段专项验收测试：情境亲和矩阵运行时装载、中性/梯度采样及硬冲突空交集平滑回退契约

覆盖验收范围：
1. 196 单元运行时矩阵装载与结构自闭环（18 中性单元 + 178 加权单元，4/2/1 梯度权重，0 幽灵 ID）；
2. neutral 模式中性采样语义验证（C(c,s) = G_s，14 generic + 4 tattoo 规范情境无偏置）；
3. weighted 模式 4/2/1 梯度概率比与公式级差分精确度（P(w=4) > P(w=2) > P(w=1) > P(unlisted)）；
4. 多情境信号精确合成（scene:1.0 / theme:0.6 归一化与跨情境分布叠加）；
5. 运行时候选预过滤与空交集平滑回退至 G_s（不抛异常，优雅降级）；
6. 孤立子流 RNG 确定性复现（固定种子输出稳定，无关槽位互不干扰）。
"""
from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Set
from unittest.mock import patch

from lib.context_affinity import (
    ORDERED_SLOT_IDS,
    ContextAffinityRegistry,
    compute_context_profile,
    compute_slot_distribution,
    load_slot_id_registries,
    sample_categorical,
)
from lib.errors import AffinityConfigurationError
from lib.models import ORDERED_CONTEXT_IDS, ContextProfile
from lib.rng import derive_substream_rng
from lib.sampler import DataSampler

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class TestM2MatrixAndSampling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ContextAffinityRegistry(DATA_DIR)
        cls.sampler = DataSampler(DATA_DIR)
        cls.slot_registries = load_slot_id_registries(DATA_DIR)

    # ─────────────────────────────────────────────────────────────────
    # 一、矩阵运行时装载与结构自闭环
    # ─────────────────────────────────────────────────────────────────

    def test_01_matrix_runtime_structure_and_counts(self):
        """[M2-01] 运行时矩阵严格满足 196 单元，且恰好 18 个 neutral 单元与 178 个 weighted 单元。"""
        matrix = self.registry.matrix
        self.assertEqual(len(matrix), 14, "Must have exactly 14 contexts")

        neutral_cells: List[Tuple[str, str]] = []
        weighted_cells: List[Tuple[str, str]] = []

        for ctx in ORDERED_CONTEXT_IDS:
            self.assertIn(ctx, matrix, f"Context {ctx} missing from matrix")
            row = matrix[ctx]
            self.assertEqual(len(row), 14, f"Context {ctx} must have 14 slots")
            for slot in ORDERED_SLOT_IDS:
                self.assertIn(slot, row, f"Slot {slot} missing from context {ctx}")
                cell = row[slot]
                mode = cell.get("mode")
                if mode == "neutral":
                    neutral_cells.append((ctx, slot))
                    self.assertEqual(set(cell.keys()), {"mode"}, f"Neutral cell ({ctx}, {slot}) has extra keys")
                elif mode == "weighted":
                    weighted_cells.append((ctx, slot))
                    self.assertEqual(set(cell.keys()), {"mode", "candidates"}, f"Weighted cell ({ctx}, {slot}) keys mismatch")
                    self.assertGreater(len(cell["candidates"]), 0, f"Weighted cell ({ctx}, {slot}) candidates empty")
                else:
                    self.fail(f"Invalid mode {mode!r} in cell ({ctx}, {slot})")

        self.assertEqual(len(neutral_cells), 18, f"Expected 18 neutral cells, got {len(neutral_cells)}")
        self.assertEqual(len(weighted_cells), 178, f"Expected 178 weighted cells, got {len(weighted_cells)}")

        # 检查 18 个 neutral 单元构成：14 generic + 4 tattoo (school, office, medical, dining)
        expected_neutral = {( "generic", s) for s in ORDERED_SLOT_IDS} | {
            ("school", "tattoo"),
            ("office", "tattoo"),
            ("medical", "tattoo"),
            ("dining", "tattoo"),
        }
        self.assertEqual(set(neutral_cells), expected_neutral)

    def test_02_zero_phantom_ids_in_runtime_matrix(self):
        """[M2-02] 运行时矩阵加权候选引用的全部选择器 100% 存在于槽位专属白名单，0 幽灵 ID。"""
        for ctx in ORDERED_CONTEXT_IDS:
            for slot in ORDERED_SLOT_IDS:
                cell = self.registry.matrix[ctx][slot]
                if cell["mode"] == "weighted":
                    valid_catalog = self.slot_registries[slot]
                    for cand in cell["candidates"]:
                        cid = cand["id"]
                        self.assertIn(
                            cid,
                            valid_catalog,
                            f"PHANTOM ID in matrix [{ctx}][{slot}]: '{cid}' not registered in slot catalog!",
                        )

    def test_03_weights_strictly_adhere_to_4_2_1_gradient(self):
        """[M2-03] 全量加权单元的候选权重严格限定在 {1.0, 2.0, 4.0} 三档梯度设计中。"""
        allowed_weights = {1.0, 2.0, 4.0}
        seen_weights: Set[float] = set()

        for ctx in ORDERED_CONTEXT_IDS:
            for slot in ORDERED_SLOT_IDS:
                cell = self.registry.matrix[ctx][slot]
                if cell["mode"] == "weighted":
                    for cand in cell["candidates"]:
                        wt = cand["weight"]
                        self.assertIn(
                            wt,
                            allowed_weights,
                            f"Non-standard weight {wt} in cell [{ctx}][{slot}] for candidate {cand['id']}",
                        )
                        seen_weights.add(wt)

        self.assertEqual(seen_weights, allowed_weights, "Must observe all three weight tiers: 1.0, 2.0, 4.0")

    def test_04_fail_closed_on_invalid_cell_mode(self):
        """[M2-04] 负向：向 compute_slot_distribution 传入非法 mode 时必须抛出 AffinityConfigurationError。"""
        profile = ContextProfile(
            weights=(("school", 1.0),),
            scene_item_id="dummy",
            theme_id="dummy",
            scene_context_ids=("school",),
            theme_context_ids=("school",),
        )
        bad_matrix = {
            "school": {
                "clothing": {
                    "mode": "invalid_mode_xyz"
                }
            }
        }
        with self.assertRaises(AffinityConfigurationError) as ctx:
            compute_slot_distribution(
                slot_name="clothing",
                profile=profile,
                catalog_candidates=["c1", "c2"],
                matrix=bad_matrix,
            )
        self.assertIn("Unknown cell mode: invalid_mode_xyz", str(ctx.exception))

    # ─────────────────────────────────────────────────────────────────
    # 二、中性无偏置采样 (neutral 模式与 G_s 对齐)
    # ─────────────────────────────────────────────────────────────────

    def test_05_neutral_cells_sample_exactly_as_global_distribution(self):
        """[M2-05] 18 个 neutral 单元严格满足 C(c,s) = G_s，差分精度 < 1e-9。"""
        # 测试 14 个 generic 单元 + 4 个 tattoo 单元
        test_cells = [("generic", s) for s in ORDERED_SLOT_IDS] + [
            ("school", "tattoo"),
            ("office", "tattoo"),
            ("medical", "tattoo"),
            ("dining", "tattoo"),
        ]

        cands = ["opt_a", "opt_b", "opt_c", "opt_d"]
        base_weights = {"opt_a": 1.0, "opt_b": 2.0, "opt_c": 3.0, "opt_d": 4.0}
        total_w = sum(base_weights.values())
        expected_g = {k: v / total_w for k, v in base_weights.items()}

        for ctx, slot in test_cells:
            profile = ContextProfile(
                weights=((ctx, 1.0),),
                scene_item_id="dummy",
                theme_id="dummy",
                scene_context_ids=(ctx,),
                theme_context_ids=(ctx,),
            )
            # 1. 带非均匀 base_weights 测试：严格 P_s = G_s
            dist = compute_slot_distribution(
                slot_name=slot,
                profile=profile,
                catalog_candidates=cands,
                matrix=self.registry.matrix,
                base_weights=base_weights,
            )
            for k in cands:
                self.assertAlmostEqual(
                    dist[k],
                    expected_g[k],
                    places=9,
                    msg=f"Neutral cell ({ctx}, {slot}) diverged from G_s!",
                )

            # 2. 均匀 base_weights 测试：严格 1/N
            uniform_dist = compute_slot_distribution(
                slot_name=slot,
                profile=profile,
                catalog_candidates=cands,
                matrix=self.registry.matrix,
                base_weights=None,
            )
            for k in cands:
                self.assertAlmostEqual(
                    uniform_dist[k],
                    0.25,
                    places=9,
                    msg=f"Neutral cell ({ctx}, {slot}) diverged from uniform G_s!",
                )

    def test_06_tattoo_neutral_allows_all_candidates_without_bias(self):
        """[M2-06] 规范情境 (school/office/medical/dining) 下 tattoo 槽位不新增偏置，全部合法候选均匀抽样。"""
        all_tattoos = list(self.slot_registries["tattoo"])
        n_tattoos = len(all_tattoos)
        expected_uniform_p = 1.0 / float(n_tattoos)

        for ctx in ("school", "office", "medical", "dining"):
            profile = ContextProfile(
                weights=((ctx, 1.0),),
                scene_item_id="dummy",
                theme_id="dummy",
                scene_context_ids=(ctx,),
                theme_context_ids=(ctx,),
            )
            dist = compute_slot_distribution(
                slot_name="tattoo",
                profile=profile,
                catalog_candidates=all_tattoos,
                matrix=self.registry.matrix,
            )
            for tid in all_tattoos:
                self.assertAlmostEqual(
                    dist[tid],
                    expected_uniform_p,
                    places=9,
                    msg=f"Context {ctx} tattoo slot biased for candidate {tid}",
                )

    # ─────────────────────────────────────────────────────────────────
    # 三、加权梯度采样与 4/2/1 概率单调性
    # ─────────────────────────────────────────────────────────────────

    def test_07_weighted_sampling_monotonic_order_and_exact_formula(self):
        """[M2-07] weighted 单元严格满足公式 P(cid) = 0.15*G_s + 0.85*(w/sum(w))，且 P(w=4) > P(w=2) > P(w=1) > P(unlisted)。"""
        # 1. 真实矩阵 4.0 vs 2.0 vs unlisted 单元：outdoor x clothing
        cell = self.registry.matrix["outdoor"]["clothing"]
        self.assertEqual(cell["mode"], "weighted")

        w4_ids = [c["id"] for c in cell["candidates"] if c["weight"] == 4.0]
        w2_ids = [c["id"] for c in cell["candidates"] if c["weight"] == 2.0]
        self.assertTrue(len(w4_ids) > 0)
        self.assertTrue(len(w2_ids) > 0)

        all_pool = [c["id"] for c in cell["candidates"]] + ["unlisted_outdoor_dress"]
        n_cands = len(all_pool)
        g_val = 1.0 / float(n_cands)

        sum_w = sum(c["weight"] for c in cell["candidates"])
        profile = ContextProfile(
            weights=(("outdoor", 1.0),),
            scene_item_id="dummy",
            theme_id="dummy",
            scene_context_ids=("outdoor",),
            theme_context_ids=("outdoor",),
        )

        dist = compute_slot_distribution(
            slot_name="clothing",
            profile=profile,
            catalog_candidates=all_pool,
            matrix=self.registry.matrix,
        )

        p_w4 = dist[w4_ids[0]]
        p_w2 = dist[w2_ids[0]]
        p_unlisted = dist["unlisted_outdoor_dress"]

        self.assertGreater(p_w4, p_w2, "Weight 4.0 probability must exceed weight 2.0")
        self.assertGreater(p_w2, p_unlisted, "Weight 2.0 probability must exceed unlisted (G_s only)")

        expected_w4 = 0.15 * g_val + 0.85 * (4.0 / sum_w)
        expected_w2 = 0.15 * g_val + 0.85 * (2.0 / sum_w)
        expected_unlisted = 0.15 * g_val + 0.85 * 0.0

        self.assertAlmostEqual(dist[w4_ids[0]], expected_w4, places=9)
        self.assertAlmostEqual(dist[w2_ids[0]], expected_w2, places=9)
        self.assertAlmostEqual(dist["unlisted_outdoor_dress"], expected_unlisted, places=9)

        # 2. 真实矩阵含权重 1.0 单元：transit x tattoo
        transit_cell = self.registry.matrix["transit"]["tattoo"]
        t_w1_ids = [c["id"] for c in transit_cell["candidates"] if c["weight"] == 1.0]
        t_w2_ids = [c["id"] for c in transit_cell["candidates"] if c["weight"] == 2.0]
        self.assertTrue(len(t_w1_ids) > 0)
        self.assertTrue(len(t_w2_ids) > 0)
        t_pool = [c["id"] for c in transit_cell["candidates"]] + ["unlisted_tattoo"]
        t_prof = ContextProfile(
            weights=(("transit", 1.0),),
            scene_item_id="dummy",
            theme_id="dummy",
            scene_context_ids=("transit",),
            theme_context_ids=("transit",),
        )
        t_dist = compute_slot_distribution(
            slot_name="tattoo",
            profile=t_prof,
            catalog_candidates=t_pool,
            matrix=self.registry.matrix,
        )
        self.assertGreater(t_dist[t_w2_ids[0]], t_dist[t_w1_ids[0]], "Weight 2.0 must exceed weight 1.0 in transit tattoo")
        self.assertGreater(t_dist[t_w1_ids[0]], t_dist["unlisted_tattoo"], "Weight 1.0 must exceed unlisted in transit tattoo")

        t_sum_w = sum(c["weight"] for c in transit_cell["candidates"])
        t_g = 1.0 / float(len(t_pool))
        self.assertAlmostEqual(t_dist[t_w2_ids[0]], 0.15 * t_g + 0.85 * (2.0 / t_sum_w), places=9)
        self.assertAlmostEqual(t_dist[t_w1_ids[0]], 0.15 * t_g + 0.85 * (1.0 / t_sum_w), places=9)
        self.assertAlmostEqual(t_dist["unlisted_tattoo"], 0.15 * t_g, places=9)

        # 3. 合成 4/2/1 全梯度单元精确公式断言: P(w=4) > P(w=2) > P(w=1) > P(unlisted)
        synthetic_matrix = {
            "special": {
                "clothing": {
                    "mode": "weighted",
                    "candidates": [
                        {"id": "cand_w4", "weight": 4.0},
                        {"id": "cand_w2", "weight": 2.0},
                        {"id": "cand_w1", "weight": 1.0},
                    ]
                }
            }
        }
        synthetic_pool = ["cand_w4", "cand_w2", "cand_w1", "cand_unlisted"]
        synthetic_prof = ContextProfile(
            weights=(("special", 1.0),),
            scene_item_id="dummy",
            theme_id="dummy",
            scene_context_ids=("special",),
            theme_context_ids=("special",),
        )
        synth_dist = compute_slot_distribution(
            slot_name="clothing",
            profile=synthetic_prof,
            catalog_candidates=synthetic_pool,
            matrix=synthetic_matrix,
        )
        p_4 = synth_dist["cand_w4"]
        p_2 = synth_dist["cand_w2"]
        p_1 = synth_dist["cand_w1"]
        p_un = synth_dist["cand_unlisted"]

        self.assertGreater(p_4, p_2, "P(w=4) must strictly exceed P(w=2)")
        self.assertGreater(p_2, p_1, "P(w=2) must strictly exceed P(w=1)")
        self.assertGreater(p_1, p_un, "P(w=1) must strictly exceed P(unlisted)")

        synth_sum_w = 4.0 + 2.0 + 1.0
        synth_g = 1.0 / 4.0
        self.assertAlmostEqual(p_4, 0.15 * synth_g + 0.85 * (4.0 / synth_sum_w), places=9)
        self.assertAlmostEqual(p_2, 0.15 * synth_g + 0.85 * (2.0 / synth_sum_w), places=9)
        self.assertAlmostEqual(p_1, 0.15 * synth_g + 0.85 * (1.0 / synth_sum_w), places=9)
        self.assertAlmostEqual(p_un, 0.15 * synth_g + 0.85 * 0.0, places=9)

    # ─────────────────────────────────────────────────────────────────
    # 四、多情境信号精确合成 (Multi-Context Profile Blending)
    # ─────────────────────────────────────────────────────────────────

    def test_08_multi_context_synthesis_blending(self):
        """[M2-08] 场景 (1.0) 与主题 (0.6) 混合情境下，alpha(c) 正确归一化，各情境亲和度按比例线性加权叠加。"""
        # scene=school, theme=nightlife -> school: 1.0/1.6 = 0.625, nightlife: 0.6/1.6 = 0.375
        prof = compute_context_profile(
            scene_context_ids=["school"],
            theme_context_ids=["nightlife"],
        )
        self.assertEqual(len(prof.weights), 2)
        w_dict = dict(prof.weights)
        self.assertAlmostEqual(w_dict["school"], 1.0 / 1.6, places=9)
        self.assertAlmostEqual(w_dict["nightlife"], 0.6 / 1.6, places=9)

        # 测试在 lighting 槽位上的混合分布
        light_cands = list(self.slot_registries["lighting"])
        dist_mixed = compute_slot_distribution(
            slot_name="lighting",
            profile=prof,
            catalog_candidates=light_cands,
            matrix=self.registry.matrix,
        )

        # 理论分布：P_s = 0.15*G_s + 0.85 * [ 0.625 * C(school, light) + 0.375 * C(nightlife, light) ]
        prof_school = compute_context_profile(["school"], [])
        prof_nightlife = compute_context_profile(["nightlife"], [])

        dist_school = compute_slot_distribution("lighting", prof_school, light_cands, self.registry.matrix)
        dist_nightlife = compute_slot_distribution("lighting", prof_nightlife, light_cands, self.registry.matrix)

        for lid in light_cands:
            expected_p = w_dict["school"] * dist_school[lid] + w_dict["nightlife"] * dist_nightlife[lid]
            self.assertAlmostEqual(dist_mixed[lid], expected_p, places=7)

    # ─────────────────────────────────────────────────────────────────
    # 五、运行时候选预过滤与空交集平滑回退至 G_s (M2 核心交付)
    # ─────────────────────────────────────────────────────────────────

    def test_09_empty_intersection_fallbacks_smoothly_to_global_pool(self):
        """[M2-09] 核心：当运行时候选经过冲突预过滤后与加权单元交集为空时，不抛异常，平滑回退至 G_s。"""
        profile = ContextProfile(
            weights=(("school", 1.0),),
            scene_item_id="dummy",
            theme_id="dummy",
            scene_context_ids=("school",),
            theme_context_ids=("school",),
        )
        # 获取 school x clothing 的所有亲和候选
        cell = self.registry.matrix["school"]["clothing"]
        school_cands = {c["id"] for c in cell["candidates"]}

        # 构造一个与 school_cands 完全互斥的合法候选子集 (模拟所有学校服装被冲突规则过滤)
        all_clothing = self.slot_registries["clothing"]
        non_school_cands = sorted(all_clothing - school_cands)
        self.assertGreater(len(non_school_cands), 5)

        sub_pool = non_school_cands[:5]
        # 必须不抛出 AffinityConfigurationError，而是平滑回退至 sub_pool 上的 G_s (均匀 1/5 = 0.2)
        dist = compute_slot_distribution(
            slot_name="clothing",
            profile=profile,
            catalog_candidates=sub_pool,
            matrix=self.registry.matrix,
        )

        self.assertEqual(len(dist), 5)
        for cid in sub_pool:
            self.assertAlmostEqual(dist[cid], 0.2, places=9, msg="Empty intersection must degrade smoothly to G_s (1/N)")

    def test_10_partial_intersection_renormalizes_surviving_candidates(self):
        """[M2-10] 当预过滤后部分候选留存时，留存候选在交集上重新归一化，仍保持高亲和度抽样。"""
        profile = ContextProfile(
            weights=(("school", 1.0),),
            scene_item_id="dummy",
            theme_id="dummy",
            scene_context_ids=("school",),
            theme_context_ids=("school",),
        )
        cell = self.registry.matrix["school"]["clothing"]
        cands = cell["candidates"]
        self.assertGreater(len(cands), 2)

        # 仅保留前两个矩阵候选，再混入两个非矩阵候选
        surviving_cands = [cands[0]["id"], cands[1]["id"]]
        outside_cands = ["outside_item_1", "outside_item_2"]
        pool = surviving_cands + outside_cands

        w_sum = cands[0]["weight"] + cands[1]["weight"]
        g_val = 0.25

        expected_dist = {
            surviving_cands[0]: 0.15 * g_val + 0.85 * (cands[0]["weight"] / w_sum),
            surviving_cands[1]: 0.15 * g_val + 0.85 * (cands[1]["weight"] / w_sum),
            outside_cands[0]: 0.15 * g_val,
            outside_cands[1]: 0.15 * g_val,
        }

        actual_dist = compute_slot_distribution(
            slot_name="clothing",
            profile=profile,
            catalog_candidates=pool,
            matrix=self.registry.matrix,
        )

        for cid in pool:
            self.assertAlmostEqual(actual_dist[cid], expected_dist[cid], places=9)

    def test_11_sampler_pre_filtering_with_excluded_ids(self):
        """[M2-11] 验证 DataSampler._sample_from_candidates 支持 excluded_ids 预先剔除已知硬冲突。"""
        data = self.sampler._load("clothing")
        styles = data.get("categories", [])
        self.assertGreater(len(styles), 10)

        # 选定 school 情境，并将 school 情境矩阵里的所有候选加入 excluded_ids
        cell = self.registry.matrix["school"]["clothing"]
        school_ids = {c["id"] for c in cell["candidates"]}

        rng = Random(42)
        # 预过滤排除所有 school_ids，此时矩阵候选交集为空，但抽样器平滑回退，抽取到非 school 的合法服装
        chosen = self.sampler._sample_from_candidates(
            slot_name="clothing",
            candidates=styles,
            rng=rng,
            context="school",
            excluded_ids=school_ids,
        )

        self.assertIsNotNone(chosen)
        self.assertNotIn(chosen["id"], school_ids, "Excluded IDs must never be sampled!")

    # ─────────────────────────────────────────────────────────────────
    # 六、固定种子确定性与独立子流验证 (Determinism & Seed Isolation)
    # ─────────────────────────────────────────────────────────────────

    def test_12_fixed_seed_determinism_across_generations(self):
        """[M2-12] 相同 prompt_seed 下，多次生成的结果完全一致 (字节级确定性)。"""
        from nodes import IYKYKPromptGenerator

        gen = IYKYKPromptGenerator()

        res1 = gen.generate_structured(
            预设模板="无 (None)", 风格配方="无 (None)",
            场景大类="教室", 剧情主题="教师/学生系列",
            景别构图="自动 (Auto)", 拍摄视角="自动 (Auto)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)", 服装款式="随机 (Random)",
            服装状态="正常穿着 (Normal)", 发型发色="随机 (Random)",
            饰品头饰="无 (None)", 妆容细节="无 (None)",
            姿势动作="随机 (Random)", 情绪表情="随机 (Random)",
            光影预设="自动 (Auto)", 胶片风格="无 (None)",
            液体效果="无 (None)", 纹身标记="无 (None)",
            道具物件="无 (None)", 角色设定="无 (None)",
            真实微瑕="无 (None)", 画质等级="高清写真 (High)",
            prompt_seed=123456,
        )

        res2 = gen.generate_structured(
            预设模板="无 (None)", 风格配方="无 (None)",
            场景大类="教室", 剧情主题="教师/学生系列",
            景别构图="自动 (Auto)", 拍摄视角="自动 (Auto)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)", 服装款式="随机 (Random)",
            服装状态="正常穿着 (Normal)", 发型发色="随机 (Random)",
            饰品头饰="无 (None)", 妆容细节="无 (None)",
            姿势动作="随机 (Random)", 情绪表情="随机 (Random)",
            光影预设="自动 (Auto)", 胶片风格="无 (None)",
            液体效果="无 (None)", 纹身标记="无 (None)",
            道具物件="无 (None)", 角色设定="无 (None)",
            真实微瑕="无 (None)", 画质等级="高清写真 (High)",
            prompt_seed=123456,
        )

        self.assertEqual(res1.positive, res2.positive, "Identical seed must yield identical positive prompt")
        self.assertEqual(res1.negative, res2.negative, "Identical seed must yield identical negative prompt")
        self.assertEqual([a.text for a in res1.atoms], [a.text for a in res2.atoms], "Identical seed must yield identical atoms")

    # ─────────────────────────────────────────────────────────────────
    # 七、光影候选全域可达性与公共入口硬冲突预过滤 (Expanded Lighting & Pre-filtering)
    # ─────────────────────────────────────────────────────────────────

    def test_13_lighting_auto_random_reaches_all_59_items_and_sections(self):
        """[M2-13] sample_lighting_result 在 auto/random 模式下打通全部 59 个光影候选项（覆盖 5 大分区），且 Provenance/Tag 结构完整。"""
        lighting_data = self.sampler._load("lighting")
        combos = lighting_data.get("preset_combos", [])
        prof = lighting_data.get("professional_lighting", [])
        cine = lighting_data.get("cinematic_lighting", [])
        spec = lighting_data.get("special_effects", [])
        erot = lighting_data.get("erotic_lighting", [])
        all_59_ids = {x["id"] for x in (combos + prof + cine + spec + erot)}
        self.assertEqual(len(all_59_ids), 59, "Lighting total IDs must be 59")

        # 验证在大量采样中，各个分区的代表候选项均可被自动/随机抽样命中（非仅限于 preset_combos）
        hit_ids = set()
        for seed in range(300):
            res = self.sampler.sample_lighting_result("随机 (Random)", Random(seed))
            self.assertIsNotNone(res)
            self.assertIn(res.item_id, all_59_ids)
            self.assertEqual(res.provenance.kind, "lighting")
            self.assertGreater(len(res.tags), 0)
            self.assertGreater(len(res.sampled_tags), 0)
            hit_ids.add(res.item_id)

        # 验证 professional_lighting / cinematic_lighting / preset_combos 分区均有条目被命中
        prof_ids = {x["id"] for x in prof}
        cine_ids = {x["id"] for x in cine}
        combo_ids = {x["id"] for x in combos}
        self.assertTrue(len(hit_ids & prof_ids) > 0, "Professional lighting items must be reachable in random mode")
        self.assertTrue(len(hit_ids & cine_ids) > 0, "Cinematic lighting items must be reachable in random mode")
        self.assertTrue(len(hit_ids & combo_ids) > 0, "Preset combos must be reachable in random mode")

        # 明确核验重点扩充候选：professional_lighting_14 (树影日光), professional_lighting_15 (落日侧光), ext_aw_light_warm_golden_hour
        outdoor_prof = compute_context_profile(["outdoor"], [])
        outdoor_hits = set()
        for seed in range(150):
            r = self.sampler.sample_lighting_result("随机 (Random)", Random(seed), context_profile=outdoor_prof)
            if r:
                outdoor_hits.add(r.item_id)
        self.assertTrue(
            "professional_lighting_14" in outdoor_hits or "professional_lighting_15" in outdoor_hits or "ext_aw_light_warm_golden_hour" in outdoor_hits,
            "Key expanded lighting candidates must be reachable under affinity contexts"
        )

        # 验证显式/直接抽样对 single preset 与 combo preset 的格式一致性
        res_prof = self.sampler.sample_lighting_result("professional_lighting_14", Random(42))
        self.assertEqual(res_prof.item_id, "professional_lighting_14")
        self.assertEqual(res_prof.provenance.item_id, "professional_lighting_14")
        self.assertTrue(all(t.provenance.item_id == "professional_lighting_14" for t in res_prof.sampled_tags))

        res_combo = self.sampler.sample_lighting_result("lighting_studio_commercial", Random(42))
        self.assertEqual(res_combo.item_id, "lighting_studio_commercial")
        self.assertEqual(res_combo.provenance.item_id, "lighting_studio_commercial")
        self.assertTrue(all(t.provenance.item_id == "lighting_studio_commercial" for t in res_combo.sampled_tags))

    def test_14_public_entry_point_hard_conflict_pre_filtering(self):
        """[M2-14] 验证公共采样入口在采样前精确执行硬冲突预过滤（物理硬约束），并保留构图偏好与穿戴/空闲合法空间。"""
        # 1. 视角预过滤：L1 下排除 between_legs_angle
        for seed in range(50):
            res_l1 = self.sampler.sample_camera_angle_result("随机 (Random)", Random(seed), nudity_level_code="L1")
            self.assertIsNotNone(res_l1)
            self.assertNotEqual(res_l1.item_id, "between_legs_angle")

        # 2. 光影预过滤：L1 下排除情色光影 (erotic_lighting_00..03)
        erotic_light_ids = {"erotic_lighting_00", "erotic_lighting_01", "erotic_lighting_02", "erotic_lighting_03"}
        for seed in range(50):
            res_lgt_l1 = self.sampler.sample_lighting_result("随机 (Random)", Random(seed), nudity_level_code="L1")
            self.assertIsNotNone(res_lgt_l1)
            self.assertNotIn(res_lgt_l1.item_id, erotic_light_ids)

        # 3. 姿势与景别软偏好 (规则 13 治理契约)：
        # 特写构图下画外体态具有电影叙事合法性，绝不硬排除 kneeling, all_fours, limb_posture
        excluded_cu_poses = self.sampler.get_excluded_candidate_ids("pose", shot_type_id="close_up")
        self.assertNotIn("kneeling", excluded_cu_poses, "Kneeling must not be hard-excluded under close_up")
        self.assertNotIn("all_fours", excluded_cu_poses, "All fours must not be hard-excluded under close_up")
        self.assertNotIn("limb_posture", excluded_cu_poses, "Limb posture must not be hard-excluded under close_up")

        # 正向反例 3.1：特写构图下抽样跪姿，公共入口合法返回
        res_kneel = self.sampler.sample_pose_result("kneeling", Random(42), shot_type_id="close_up")
        self.assertIsNotNone(res_kneel)
        self.assertEqual(res_kneel.item_id, "kneeling")

        # 4. 道具手部占用物理硬约束 (规则 9)：
        # 当双手被占用 (hand_state='both_busy') 时，排除手持道具 (需用手持握者)
        strictly_handheld_props = {
            "wine_glass_bottle", "smartphone_recording", "digital_camera_record",
            "oriental_fan_umbrella", "game_controller", "fashion_clutch_bag",
            "flower_bouquet_petals", "plush_doll_teddy", "pillow_clutching",
        }
        sampled_prop_ids = set()
        for seed in range(50):
            res_prop_busy = self.sampler.sample_prop_result("随机 (Random)", Random(seed), hand_state="both_busy")
            if res_prop_busy:
                self.assertNotIn(res_prop_busy.item_id, strictly_handheld_props, f"Handheld prop {res_prop_busy.item_id} sampled when hands busy!")
                sampled_prop_ids.add(res_prop_busy.item_id)
        # 证明非手持道具（穿戴/环境/接触类）仍可正常被采样抽取
        self.assertTrue(len(sampled_prop_ids) > 0, "Non-handheld props must remain sampleable")

        # 正向反例 4.1：fashion_baguette_bag (法棍包) 为 worn (hands_required=0)，双手被占用时绝不排除！
        excluded_busy_props = self.sampler.get_excluded_candidate_ids("props", hand_state="both_busy")
        self.assertNotIn("fashion_baguette_bag", excluded_busy_props, "Baguette bag (worn, hands=0) must not be excluded when hands are busy")
        res_baguette = self.sampler.sample_prop_result("fashion_baguette_bag", Random(42), hand_state="both_busy")
        self.assertIsNotNone(res_baguette)
        self.assertEqual(res_baguette.item_id, "fashion_baguette_bag")

        # 正向反例 4.2：combat__fighting_stance (格斗站姿) 事实 hand_state='free'，占用 0 手，必须允许手持道具
        excluded_combat = self.sampler.get_excluded_candidate_ids("props", pose_id="combat__fighting_stance")
        self.assertNotIn("wine_glass_bottle", excluded_combat, "Wine glass must not be excluded with fighting stance")
        self.assertNotIn("digital_camera_record", excluded_combat, "Camera must not be excluded with fighting stance")
        res_combat_prop = self.sampler.sample_prop_result("wine_glass_bottle", Random(42), pose_id="combat__fighting_stance")
        self.assertIsNotNone(res_combat_prop)
        self.assertEqual(res_combat_prop.item_id, "wine_glass_bottle")

        # 正向反例 4.3：restraints__bound_legs (双腿受限) 事实 hand_state='free'，占用 0 手，必须允许手持道具
        excluded_legs = self.sampler.get_excluded_candidate_ids("props", pose_id="restraints__bound_legs")
        self.assertNotIn("wine_glass_bottle", excluded_legs, "Wine glass must not be excluded when only legs are bound")
        self.assertNotIn("digital_camera_record", excluded_legs, "Camera must not be excluded when only legs are bound")
        res_legs_prop = self.sampler.sample_prop_result("digital_camera_record", Random(42), pose_id="restraints__bound_legs")
        self.assertIsNotNone(res_legs_prop)
        self.assertIn(res_legs_prop.item_id, ("digital_camera_record", "digital_camera_in_hand"))

        # 正向反例 4.4：单手占用 (hand_state='one_busy') 时，依据 Rule 9.2 容量模型，单手道具 (1+1=2) 完全合法
        excluded_one = self.sampler.get_excluded_candidate_ids("props", hand_state="one_busy")
        self.assertNotIn("digital_camera_record", excluded_one, "Single-hand camera must be allowed when one hand is busy (1+1<=2)")
        self.assertNotIn("wine_glass_bottle", excluded_one, "Single-hand wine glass must be allowed when one hand is busy (1+1<=2)")

        # 5. 服装形制载体物理预过滤：
        # 解扣状态下排除无开合结构款式；掀裙状态下必须核验款式具备裙装形制载体
        for seed in range(50):
            res_c_unbuttoned = self.sampler.sample_clothing_result(
                "随机 (Random)", "解开纽扣 (Unbuttoned)", "L3", Random(seed)
            )
            self.assertNotIn(res_c_unbuttoned.style_id, {"one_piece_swimsuit", "latex_catsuit", "sweater_casual"})

        # 负向反例 5.1：hosiery_legwear (袜类/丝袜, 拓扑 underwear) 与 sailor_shirt (上衣, 拓扑 top) 无裙摆载体，掀裙状态下严格排除
        excluded_lifted = self.sampler.get_excluded_candidate_ids("clothing", clothing_state="lifted_up")
        self.assertIn("hosiery_legwear", excluded_lifted, "hosiery_legwear lacks skirt carrier and must be excluded under lifted_up")
        self.assertIn("sailor_shirt", excluded_lifted, "sailor_shirt lacks skirt carrier and must be excluded under lifted_up")
        self.assertIn("denim_shorts", excluded_lifted, "denim_shorts lacks skirt carrier and must be excluded under lifted_up")
        self.assertIn("one_piece_swimsuit", excluded_lifted, "one_piece_swimsuit in NON_SKIRT_ONE_PIECE must be excluded under lifted_up")

        # 正向反例 5.2：jk_seifuku, summer_sundress, pleated_skirt 具备裙装载体，绝不排除
        self.assertNotIn("jk_seifuku", excluded_lifted, "jk_seifuku possesses skirt carrier and must remain legal")
        self.assertNotIn("summer_sundress", excluded_lifted, "summer_sundress possesses skirt carrier and must remain legal")
        self.assertNotIn("pleated_skirt", excluded_lifted, "pleated_skirt possesses skirt carrier and must remain legal")

        for seed in range(50):
            res_c_lifted = self.sampler.sample_clothing_result(
                "随机 (Random)", "裙摆掀起 (Skirt Lifted Up)", "L3", Random(seed)
            )
            self.assertNotIn(res_c_lifted.style_id, {"one_piece_swimsuit", "denim_shorts", "latex_catsuit", "hosiery_legwear", "sailor_shirt"})

    def test_15_empty_legal_candidate_pool_graceful_handling(self):
        """[M2-15] 验证当所有候选均被硬冲突排除（合法池为空）时，各公共入口干净返回 None/空结果，不抛未捕获异常。"""
        rng = Random(42)

        # 1. camera_angle：公共入口 sample_camera_angle_result 在全量排除时干净返回 None
        all_angle_ids = {a["id"] for a in self.sampler._load("shot_types").get("camera_angles", [])}
        with patch.object(self.sampler, "get_excluded_candidate_ids", return_value=all_angle_ids):
            res_angle = self.sampler.sample_camera_angle_result("随机 (Random)", rng)
            self.assertIsNone(res_angle, "sample_camera_angle_result must return None when pool is fully excluded")

        # 2. lighting：公共入口 sample_lighting_result 在全量排除时干净返回 None
        all_lgt = self.sampler._load("lighting")
        all_lgt_items = (
            all_lgt.get("preset_combos", []) +
            all_lgt.get("professional_lighting", []) +
            all_lgt.get("cinematic_lighting", []) +
            all_lgt.get("special_effects", []) +
            all_lgt.get("erotic_lighting", [])
        )
        all_lgt_ids = {x["id"] for x in all_lgt_items}
        with patch.object(self.sampler, "get_excluded_candidate_ids", return_value=all_lgt_ids):
            res_lgt = self.sampler.sample_lighting_result("随机 (Random)", rng)
            self.assertIsNone(res_lgt, "sample_lighting_result must return None when pool is fully excluded")

        # 3. pose：公共入口 sample_pose_result 在全量排除时干净返回 None
        all_poses = self.sampler._load("poses").get("pose_categories", [])
        all_pose_ids = {p["id"] for p in all_poses}
        with patch.object(self.sampler, "get_excluded_candidate_ids", return_value=all_pose_ids):
            res_pose = self.sampler.sample_pose_result("随机 (Random)", rng)
            self.assertIsNone(res_pose, "sample_pose_result must return None when pool is fully excluded")

        # 4. props：公共入口 sample_prop_result 在全量排除时干净返回 None
        all_props = [p for p in self.sampler._load("props").get("categories", []) if p.get("id") != "none"]
        all_prop_ids = {p["id"] for p in all_props}
        with patch.object(self.sampler, "get_excluded_candidate_ids", return_value=all_prop_ids):
            res_prop = self.sampler.sample_prop_result("随机 (Random)", rng)
            self.assertIsNone(res_prop, "sample_prop_result must return None when pool is fully excluded")

        # 5. clothing：公共入口 sample_clothing_result 在全量排除时干净返回空 ClothingSampleResult
        all_cloth_styles = self.sampler._load("clothing").get("categories", [])
        all_cloth_ids = {c["id"] for c in all_cloth_styles}
        with patch.object(self.sampler, "get_excluded_candidate_ids", return_value=all_cloth_ids):
            res_cloth = self.sampler.sample_clothing_result("随机 (Random)", "正常穿着 (Normal)", "L1", rng)
            self.assertEqual(res_cloth.base_tags, (), "sample_clothing_result must return empty base_tags when fully excluded")
            self.assertFalse(res_cloth.style_id, "style_id must be empty when fully excluded")

        # 验证公共调用在显式指定无款式时干净返回
        empty_cloth_res = self.sampler.sample_clothing_result("无 (None)", "无 (None)", "L1", rng)
        self.assertEqual(empty_cloth_res.base_tags, ())

    def test_16_end_to_end_generator_pre_filtering_in_structured_generation(self):
        """[M2-16] 验证在 IYKYKPromptGenerator 端到端生成中，硬冲突候选在抽样前被自动排除。"""
        from nodes import IYKYKPromptGenerator

        gen = IYKYKPromptGenerator()

        # 1. L1 级别下端到端生成：验证视角绝非 between_legs_angle，光影绝非 erotic_lighting，道具绝非成人情趣
        for seed in range(20):
            res_l1 = gen.generate_structured(
                预设模板="无 (None)", 风格配方="无 (None)",
                场景大类="教室", 剧情主题="教师/学生系列",
                景别构图="自动 (Auto)", 拍摄视角="自动 (Auto)",
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)", 服装款式="随机 (Random)",
                服装状态="正常穿着 (Normal)", 发型发色="随机 (Random)",
                饰品头饰="无 (None)", 妆容细节="无 (None)",
                姿势动作="随机 (Random)", 情绪表情="随机 (Random)",
                光影预设="自动 (Auto)", 胶片风格="无 (None)",
                液体效果="无 (None)", 纹身标记="无 (None)",
                道具物件="随机 (Random)", 角色设定="无 (None)",
                真实微瑕="无 (None)", 画质等级="高清写真 (High)",
                prompt_seed=seed,
            )
            active_origins = [s.selected_id for s in res_l1.selections]
            self.assertNotIn("between_legs_angle", active_origins)
            self.assertFalse(any(o and o.startswith("erotic_lighting") for o in active_origins))
            self.assertNotIn("sex_toy_vibrator", active_origins)
            self.assertNotIn("bondage_rope_collar", active_origins)

        # 2. 姿态为双手约束体态 (restraints__bound_arms) 时：道具物件在抽样前已排除手持道具
        handheld_props = {
            "wine_glass_bottle", "smartphone_recording", "digital_camera_record",
            "oriental_fan_umbrella", "game_controller", "fashion_clutch_bag",
            "flower_bouquet_petals", "plush_doll_teddy", "pillow_clutching",
        }
        for seed in range(20):
            res_restrained = gen.generate_structured(
                预设模板="无 (None)", 风格配方="无 (None)",
                场景大类="教室", 剧情主题="教师/学生系列",
                景别构图="自动 (Auto)", 拍摄视角="自动 (Auto)",
                裸露等级="L3 半裸诱惑 (Half Nude)", 服装款式="随机 (Random)",
                服装状态="正常穿着 (Normal)", 发型发色="随机 (Random)",
                饰品头饰="无 (None)", 妆容细节="无 (None)",
                姿势动作="restraints__bound_arms", 情绪表情="随机 (Random)",
                光影预设="自动 (Auto)", 胶片风格="无 (None)",
                液体效果="无 (None)", 纹身标记="无 (None)",
                道具物件="随机 (Random)", 角色设定="无 (None)",
                真实微瑕="无 (None)", 画质等级="高清写真 (High)",
                prompt_seed=seed,
            )
            prop_origins = [s.selected_id for s in res_restrained.selections if s.selector == "props"]
            for pid in prop_origins:
                self.assertNotIn(pid, handheld_props, f"Handheld prop {pid} generated while pose is restraints__bound_arms!")


if __name__ == "__main__":
    unittest.main()
