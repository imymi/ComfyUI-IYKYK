"""
test_context_affinity_rc8.py — ComfyUI-IYKYK v1.1.0-rc8 多信号情境亲和度模型全维度门禁测试
"""
from __future__ import annotations

import copy
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from random import Random
from typing import Any, Dict

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
from lib.rng import derive_substream_rng, derive_substream_seed
from lib.sampler import DataSampler

DATA_DIR = Path(__file__).parent.parent / "data"


class TestContextAffinityRC8(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ContextAffinityRegistry(DATA_DIR)
        cls.sampler = DataSampler(DATA_DIR)

    def test_01_matrix_196_cells_structure(self):
        """验证恰好 196 个单元格 (14 情境 × 14 槽位) 均存在且格式符合契约。"""
        self.assertEqual(len(ORDERED_CONTEXT_IDS), 14)
        self.assertEqual(len(ORDERED_SLOT_IDS), 14)

        matrix = self.registry.matrix
        self.assertEqual(len(matrix), 14)

        for ctx in ORDERED_CONTEXT_IDS:
            self.assertIn(ctx, matrix)
            row = matrix[ctx]
            self.assertEqual(len(row), 14, f"Row {ctx} must have 14 slots")
            for slot in ORDERED_SLOT_IDS:
                self.assertIn(slot, row)
                cell = row[slot]
                self.assertIn(cell["mode"], ("weighted", "neutral"))
                if cell["mode"] == "weighted":
                    cands = cell["candidates"]
                    self.assertIsInstance(cands, list)
                    self.assertGreater(len(cands), 0)
                    for c in cands:
                        self.assertIsInstance(c["id"], str)
                        self.assertIsInstance(c["weight"], (int, float))
                        self.assertTrue(math.isfinite(c["weight"]))
                        self.assertGreater(c["weight"], 0)

    def test_02_fail_closed_missing_cell(self):
        """负向：缺失任意一个单元格必须触发 AffinityConfigurationError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for f in DATA_DIR.glob("*.json"):
                shutil.copy(f, tmppath / f.name)

            aff_file = tmppath / "context_affinity.json"
            data = json.loads(aff_file.read_text(encoding="utf-8"))
            del data["matrix"]["school"]["clothing"]
            aff_file.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(AffinityConfigurationError):
                ContextAffinityRegistry(tmppath)

    def test_03_fail_closed_extra_field(self):
        """负向：单元格或顶层存在额外未知字段必须触发 AffinityConfigurationError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for f in DATA_DIR.glob("*.json"):
                shutil.copy(f, tmppath / f.name)

            aff_file = tmppath / "context_affinity.json"
            data = json.loads(aff_file.read_text(encoding="utf-8"))
            data["unknown_top_level"] = 123
            aff_file.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(AffinityConfigurationError):
                ContextAffinityRegistry(tmppath)

    def test_04_fail_closed_unknown_candidate_id(self):
        """负向：加权槽位引用未在槽位专属白名单注册的 ID 必须被拦截。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for f in DATA_DIR.glob("*.json"):
                shutil.copy(f, tmppath / f.name)

            aff_file = tmppath / "context_affinity.json"
            data = json.loads(aff_file.read_text(encoding="utf-8"))
            data["matrix"]["office"]["clothing"]["candidates"].append({
                "id": "non_existent_suit_id_xyz",
                "weight": 2.0,
            })
            aff_file.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(AffinityConfigurationError) as ctx:
                ContextAffinityRegistry(tmppath)
            self.assertIn("not found in valid catalog", str(ctx.exception))

    def test_05_fail_closed_non_finite_or_negative_weight(self):
        """负向：非有限浮点数 (NaN, inf) 或非正权重必须被拦截。"""
        bad_weights = [0.0, -1.5, float("inf"), float("nan"), True, False]
        for bad_wt in bad_weights:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmppath = Path(tmpdir)
                for f in DATA_DIR.glob("*.json"):
                    shutil.copy(f, tmppath / f.name)

                aff_file = tmppath / "context_affinity.json"
                data = json.loads(aff_file.read_text(encoding="utf-8"))
                data["matrix"]["school"]["clothing"]["candidates"][0]["weight"] = bad_wt
                aff_file.write_text(json.dumps(data), encoding="utf-8")

                with self.assertRaises(AffinityConfigurationError):
                    ContextAffinityRegistry(tmppath)

    def test_06_slot_isolation_negative(self):
        """负向：跨槽位 ID 越界引用必须被拦截 (例如把发型 ID 填入首饰槽位)。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for f in DATA_DIR.glob("*.json"):
                shutil.copy(f, tmppath / f.name)

            aff_file = tmppath / "context_affinity.json"
            data = json.loads(aff_file.read_text(encoding="utf-8"))
            data["matrix"]["school"]["jewelry"] = {
                "mode": "weighted",
                "candidates": [{"id": "twin_tails", "weight": 2.0}]
            }
            aff_file.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(AffinityConfigurationError) as ctx:
                ContextAffinityRegistry(tmppath)
            self.assertIn("not found in valid catalog for slot 'jewelry'", str(ctx.exception))

    def test_06b_fail_closed_missing_catalog_file(self):
        """负向：缺失必需 catalog 文件 (如 clothing.json) 时必须 Fail-Closed 触发 AffinityConfigurationError。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for f in DATA_DIR.glob("*.json"):
                if f.name != "clothing.json":
                    shutil.copy(f, tmppath / f.name)

            with self.assertRaises(AffinityConfigurationError) as ctx:
                ContextAffinityRegistry(tmppath)
            self.assertIn("clothing.json", str(ctx.exception))

    def test_06c_fail_closed_unknown_context_id(self):
        """负向：向 compute_context_profile 传入未知情境 ID 必须 Fail-Closed 抛出 AffinityConfigurationError。"""
        with self.assertRaises(AffinityConfigurationError) as ctx:
            compute_context_profile(("unknown_ctx_123",), ("school",))
        self.assertIn("unknown_ctx_123", str(ctx.exception))

        with self.assertRaises(AffinityConfigurationError) as ctx:
            compute_context_profile(("school",), ("unknown_theme_456",))
        self.assertIn("unknown_theme_456", str(ctx.exception))

    def test_06d_context_deduplication_no_bias(self):
        """负向/边界：重复情境输入在权重计算前稳定去重，绝不形成 2:1 异常偏置。"""
        prof_single = compute_context_profile(("school",), ("office",))
        prof_dup = compute_context_profile(("school", "school"), ("office",))
        self.assertEqual(prof_single.weights, prof_dup.weights)

    def test_07_all_14_contexts_reachable(self):
        """正向：验证真实生成器路径 (IYKYKPromptGenerator) 能够触达全部 14 个情境 (包括 special)。"""
        from nodes import IYKYKPromptGenerator
        gen = IYKYKPromptGenerator()

        scene_map = {}
        for cat in self.sampler.list_scene_categories():
            res = self.sampler.sample_scene_result(cat, Random(42))
            if res and res.context_ids:
                for c in res.context_ids:
                    scene_map.setdefault(c, []).append(cat)

        theme_map = {}
        for th in self.sampler.list_themes():
            res = self.sampler.sample_theme_result(th, Random(42))
            if res and res.context_ids:
                for c in res.context_ids:
                    theme_map.setdefault(c, []).append(th)

        reached_contexts = set()
        for ctx in ORDERED_CONTEXT_IDS:
            scene_cat = scene_map.get(ctx, ["随机 (Random)"])[0]
            theme_val = theme_map.get(ctx, ["随机 (Random)"])[0]
            res = gen.generate_structured(
                预设模板="无 (None)", 风格配方="无 (None)",
                场景大类=scene_cat, 剧情主题=theme_val,
                景别构图="自动 (Auto)", 拍摄视角="自动 (Auto)",
                裸露等级="随机 (Random)", 服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)", 发型发色="随机 (Random)",
                饰品头饰="无 (None)", 妆容细节="无 (None)",
                姿势动作="随机 (Random)", 情绪表情="随机 (Random)",
                光影预设="自动 (Auto)", 胶片风格="无 (None)",
                液体效果="无 (None)", 纹身标记="无 (None)",
                道具物件="无 (None)", 角色设定="无 (None)",
                真实微瑕="无 (None)", 画质等级="高清写真 (High)",
                prompt_seed=42,
            )
            if res.context_profile:
                for c, w in res.context_profile.weights:
                    if w > 0:
                        reached_contexts.add(c)

        for ctx in ORDERED_CONTEXT_IDS:
            self.assertIn(ctx, reached_contexts, f"Context {ctx} not reached via generator path!")

    def test_07b_all_14_slots_sample_affinity_in_generator(self):
        """正向：一次全槽位真实生成中，14/14 个亲和槽位均调用统一亲和分布计算入口。"""
        from nodes import IYKYKPromptGenerator
        from unittest.mock import patch
        import lib.context_affinity as ca

        gen = IYKYKPromptGenerator()
        called_slots = set()

        orig_compute = ca.compute_slot_distribution
        def mock_compute(slot_name, *args, **kwargs):
            called_slots.add(slot_name)
            return orig_compute(slot_name, *args, **kwargs)

        with patch("lib.sampler.compute_slot_distribution", side_effect=mock_compute):
            gen.generate_structured(
                预设模板="无 (None)", 风格配方="无 (None)",
                场景大类="随机 (Random)", 剧情主题="随机 (Random)",
                景别构图="自动 (Auto)", 拍摄视角="自动 (Auto)",
                裸露等级="随机 (Random)", 服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)", 发型发色="随机 (Random)",
                饰品头饰="随机 (Random)", 妆容细节="随机 (Random)",
                姿势动作="随机 (Random)", 情绪表情="随机 (Random)",
                光影预设="自动 (Auto)", 胶片风格="随机 (Random)",
                液体效果="随机 (Random)", 纹身标记="随机 (Random)",
                道具物件="随机 (Random)", 角色设定="随机 (Random)",
                真实微瑕="随机 (Random)", 画质等级="高清写真 (High)",
                prompt_seed=42,
            )

        self.assertEqual(len(called_slots), 14)
        for s in ORDERED_SLOT_IDS:
            self.assertIn(s, called_slots)

    def test_08_exact_formula_differential_neutral(self):
        """公式级差分：neutral 模式下严格 P_s = G_s (误差 < 1e-9)。"""
        profile = ContextProfile(
            weights=(("school", 1.0),),
            scene_item_id="classroom",
            theme_id="teacher_student",
            scene_context_ids=("school",),
            theme_context_ids=("school",),
        )
        neutral_slot = None
        for s in ORDERED_SLOT_IDS:
            if self.registry.matrix["school"][s]["mode"] == "neutral":
                neutral_slot = s
                break

        self.assertIsNotNone(neutral_slot, "At least one slot in school should be neutral")

        cands = ["item_a", "item_b", "item_c"]
        base_w = {"item_a": 1.0, "item_b": 2.0, "item_c": 1.0}
        total_b = 4.0
        expected_g = {k: v / total_b for k, v in base_w.items()}

        dist = compute_slot_distribution(
            slot_name=neutral_slot,
            profile=profile,
            catalog_candidates=cands,
            matrix=self.registry.matrix,
            base_weights=base_w,
        )

        for k in cands:
            self.assertAlmostEqual(dist[k], expected_g[k], places=9)

    def test_09_exact_formula_differential_weighted(self):
        """公式级差分：weighted 模式严格满足 0.15*G_s + 0.85*(w/sum(w))。"""
        profile = ContextProfile(
            weights=(("school", 1.0),),
            scene_item_id="classroom",
            theme_id="teacher_student",
            scene_context_ids=("school",),
            theme_context_ids=("school",),
        )
        cell = self.registry.matrix["school"]["clothing"]
        self.assertEqual(cell["mode"], "weighted")

        cand_map = {c["id"]: c["weight"] for c in cell["candidates"]}
        all_pool = list(cand_map.keys()) + ["other_suit_outside_cell"]
        n_pool = len(all_pool)
        g_val = 1.0 / float(n_pool)

        w_sum = sum(cand_map.values())
        expected_p = {}
        for cid in all_pool:
            c_affinity = (cand_map[cid] / w_sum) if cid in cand_map else 0.0
            expected_p[cid] = 0.15 * g_val + 0.85 * c_affinity

        sum_expected = sum(expected_p.values())
        for cid in all_pool:
            expected_p[cid] /= sum_expected

        actual_dist = compute_slot_distribution(
            slot_name="clothing",
            profile=profile,
            catalog_candidates=all_pool,
            matrix=self.registry.matrix,
            base_weights=None,
        )

        for cid in all_pool:
            self.assertAlmostEqual(actual_dist[cid], expected_p[cid], places=9)

    def test_09b_exact_formula_subset_intersection(self):
        """公式级差分：当合法词库是亲和候选集真子集时，亲和权重在交集上重新归一化。"""
        profile = ContextProfile(
            weights=(("school", 1.0),),
            scene_item_id="classroom",
            theme_id="teacher_student",
            scene_context_ids=("school",),
            theme_context_ids=("school",),
        )
        cell = self.registry.matrix["school"]["clothing"]
        cand_items = cell["candidates"]
        self.assertGreater(len(cand_items), 1)

        # 仅取前两个候选作为合法词库子集
        subset_cands = [cand_items[0]["id"], cand_items[1]["id"]]
        sub_w_sum = cand_items[0]["weight"] + cand_items[1]["weight"]
        g_val = 0.5  # 两个候选，uniform G_s = 0.5

        expected_p = {
            subset_cands[0]: 0.15 * g_val + 0.85 * (cand_items[0]["weight"] / sub_w_sum),
            subset_cands[1]: 0.15 * g_val + 0.85 * (cand_items[1]["weight"] / sub_w_sum),
        }

        actual_dist = compute_slot_distribution(
            slot_name="clothing",
            profile=profile,
            catalog_candidates=subset_cands,
            matrix=self.registry.matrix,
            base_weights=None,
        )

        for cid in subset_cands:
            self.assertAlmostEqual(actual_dist[cid], expected_p[cid], places=9)

    def test_10_multi_signal_context_profile_weights(self):
        """情境多信号混合权重严格匹配 1.0 / 0.6 归一化公式。"""
        prof = compute_context_profile(
            scene_context_ids=["school"],
            theme_context_ids=["bondage_sm"],
            scene_item_id="classroom",
            theme_id="sm_series",
        )
        w_dict = dict(prof.weights)
        expected_school = 1.0 / 1.6
        expected_sm = 0.6 / 1.6

        self.assertAlmostEqual(w_dict["school"], expected_school, places=9)
        self.assertAlmostEqual(w_dict["bondage_sm"], expected_sm, places=9)
        self.assertAlmostEqual(sum(w_dict.values()), 1.0, places=9)

    def test_11_four_state_sampling_contract(self):
        """四态采样契约完整性：None/Random/Auto/Explicit 行为严谨隔离。"""
        rng = Random(42)

        # 1. None: 明确返回 None
        shot_none = self.sampler.sample_shot_type_result("无 (None)", rng)
        self.assertIsNone(shot_none)

        # 2. Explicit: 命中指定特定项
        shot_exp = self.sampler.sample_shot_type_result("近景 CU (面部表情/眼神)", rng)
        self.assertEqual(shot_exp.item_id, "close_up")

        # 3. Random: 不带 context_profile
        shot_rand = self.sampler.sample_shot_type_result("随机 (Random)", rng)
        self.assertIsNotNone(shot_rand.item_id)

        # 4. Auto: 随情境 profile 引导选择
        prof_medical = compute_context_profile(["medical"], ["medical"])
        shot_auto = self.sampler.sample_shot_type_result("自动 (Auto)", rng, context_profile=prof_medical)
        self.assertIsNotNone(shot_auto.item_id)

        # 5. Clothing: Explicit & Context-profile weighted random
        cloth_exp = self.sampler.sample_clothing_result("水手服 (JK Sailor Suit)", "整齐穿着 (Normal Wearing)", "L1", rng)
        self.assertEqual(cloth_exp.style_id, "jk_seifuku")
        self.assertTrue(len(cloth_exp.all_tags) > 0)
        self.assertIn("sailor", cloth_exp.all_tags[0].text.lower())

    def test_12_independent_rng_substreams(self):
        """独立子流验证：前置槽位的抽样次数变化绝不扰动下游独立槽位的随机序列。"""
        effective_seed = 12345
        rng_pose_1 = derive_substream_rng(effective_seed, "pose")
        rng_lighting_1 = derive_substream_rng(effective_seed, "lighting")

        expected_lighting_sequence = [rng_lighting_1.random() for _ in range(5)]

        rng_pose_2 = derive_substream_rng(effective_seed, "pose")
        for _ in range(100):
            rng_pose_2.random()

        rng_lighting_2 = derive_substream_rng(effective_seed, "lighting")
        actual_lighting_sequence = [rng_lighting_2.random() for _ in range(5)]

        self.assertEqual(expected_lighting_sequence, actual_lighting_sequence)

    def test_13_tvd_statistical_sampling(self):
        """统计采样契约：全量 196 单元格 (14情境 × 14槽位) 各 N=20,000 抽取检验 TVD <= 0.03。"""
        n_samples = 20000
        rng = Random(20260904)
        slots_map = self.registry.slot_registries

        worst_tvd = 0.0
        worst_cell = None

        for ctx in ORDERED_CONTEXT_IDS:
            profile = ContextProfile(
                weights=((ctx, 1.0),),
                scene_item_id="dummy",
                theme_id="dummy",
                scene_context_ids=(ctx,),
                theme_context_ids=(ctx,),
            )
            for slot in ORDERED_SLOT_IDS:
                cands = slots_map[slot]
                theory_dist = compute_slot_distribution(
                    slot_name=slot,
                    profile=profile,
                    catalog_candidates=cands,
                    matrix=self.registry.matrix,
                )
                items = list(theory_dist.keys())
                counts: Dict[str, int] = {k: 0 for k in items}
                for _ in range(n_samples):
                    chosen_id = sample_categorical(theory_dist, rng)
                    counts[chosen_id] += 1

                tvd = 0.5 * sum(abs(counts[k] / float(n_samples) - theory_dist[k]) for k in items)
                if tvd > worst_tvd:
                    worst_tvd = tvd
                    worst_cell = (ctx, slot)

                self.assertLessEqual(
                    tvd,
                    0.03,
                    f"TVD {tvd:.5f} exceeds gate limit 0.03 for cell ({ctx}, {slot}) (N={n_samples})"
                )

        self.assertLessEqual(worst_tvd, 0.03, f"Worst TVD {worst_tvd:.5f} at cell {worst_cell} exceeded 0.03")


if __name__ == "__main__":
    unittest.main()
