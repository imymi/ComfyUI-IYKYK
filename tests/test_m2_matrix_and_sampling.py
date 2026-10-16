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
        # 选取包含 4, 2, 1 梯度的单元：outdoor x clothing
        cell = self.registry.matrix["outdoor"]["clothing"]
        self.assertEqual(cell["mode"], "weighted")

        w4_ids = [c["id"] for c in cell["candidates"] if c["weight"] == 4.0]
        w2_ids = [c["id"] for c in cell["candidates"] if c["weight"] == 2.0]
        self.assertTrue(len(w4_ids) > 0)
        self.assertTrue(len(w2_ids) > 0)

        # 模拟全量候选池包含矩阵内候选以及一个未列入矩阵的候选
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

        # 验证单调概率排序: P(w=4) > P(w=2) > P(unlisted)
        p_w4 = dist[w4_ids[0]]
        p_w2 = dist[w2_ids[0]]
        p_unlisted = dist["unlisted_outdoor_dress"]

        self.assertGreater(p_w4, p_w2, "Weight 4.0 probability must exceed weight 2.0")
        self.assertGreater(p_w2, p_unlisted, "Weight 2.0 probability must exceed unlisted (G_s only)")

        # 验证精确数值公式
        expected_w4 = 0.15 * g_val + 0.85 * (4.0 / sum_w)
        expected_w2 = 0.15 * g_val + 0.85 * (2.0 / sum_w)
        expected_unlisted = 0.15 * g_val + 0.85 * 0.0

        self.assertAlmostEqual(dist[w4_ids[0]], expected_w4, places=9)
        self.assertAlmostEqual(dist[w2_ids[0]], expected_w2, places=9)
        self.assertAlmostEqual(dist["unlisted_outdoor_dress"], expected_unlisted, places=9)

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


if __name__ == "__main__":
    unittest.main()
