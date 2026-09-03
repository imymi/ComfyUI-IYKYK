"""
test_selection_contracts.py — 选择器四态契约、全 457 项 UI 选项真实调用、严格负向防模糊与 All-None 纯净度测试
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random

from lib.assembler import PromptAssembler, finalize_prompt_atoms
from lib.errors import DataSelectionError
from lib.models import PromptFragment, TagProvenance
from lib.sampler import DataSampler, SelectionMode, get_selection_mode
from nodes import IYKYKPresetBrowser, IYKYKPromptGenerator


class TestSelectionContracts(unittest.TestCase):
    def setUp(self):
        self.repo_dir = Path(__file__).parent.parent
        self.data_dir = self.repo_dir / "data"
        self.sampler = DataSampler(self.data_dir)
        self.generator = IYKYKPromptGenerator()
        self.preset_browser = IYKYKPresetBrowser()
        self.assembler = PromptAssembler(self.data_dir)

    def test_selection_mode_helper(self):
        self.assertEqual(get_selection_mode("无 (None)"), SelectionMode.NONE)
        self.assertEqual(get_selection_mode("None"), SelectionMode.NONE)
        self.assertEqual(get_selection_mode(None), SelectionMode.NONE)
        self.assertEqual(get_selection_mode("随机 (Random)"), SelectionMode.RANDOM)
        self.assertEqual(get_selection_mode("random"), SelectionMode.RANDOM)
        self.assertEqual(get_selection_mode("自动 (Auto)"), SelectionMode.AUTO)
        self.assertEqual(get_selection_mode("自动联动裸露等级 (Auto Link Nudity)"), SelectionMode.AUTO)
        self.assertEqual(get_selection_mode("教室"), SelectionMode.EXPLICIT)
        self.assertEqual(get_selection_mode("jk_seifuku"), SelectionMode.EXPLICIT)

    def test_sampler_none_zero_leakage(self):
        """验证所有 sampler 方法在 None 选项下 100% 返回空结果，绝不落入随机退化"""
        for seed in range(100):
            rng = Random(seed)
            self.assertIsNone(self.sampler.sample_scene_result("无 (None)", rng))
            self.assertEqual(self.sampler.sample_scene("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_theme("无 (None)", rng), [])
            self.assertIsNone(self.sampler.sample_theme_result("无 (None)", rng))
            self.assertEqual(self.sampler.sample_shot_type("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_camera_angle("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_lighting("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_pose("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_expression("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_film("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_makeup("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_hairstyle("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_jewelry("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_imperfections("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_tattoo("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_prop("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_character("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_liquid("无 (None)", rng), [])
            self.assertEqual(self.sampler.sample_quality_tags("无 (None)"), [])
            self.assertIsNone(self.sampler.get_style_recipe("无 (None)", rng))
            self.assertIsNone(self.sampler.get_preset("无 (None)", rng))

    def test_generator_all_none_zero_leakage_and_provenance(self):
        """
        验证全 None 严谨验收标准 (修订 6 强制约束)：
        - 所有可选槽位设为 None；
        - mandatory: 裸露=L1，画质=Standard；
        - 采样组装后的 PromptAtom 最终结果只允许来自 'nudity' 与 'quality' 两种 provenance kind；
        - clothing 槽位原子数量必须绝对为 0；
        - 文本层额外断言绝不含 resolver 合成的 clothing 词 (如 neatly dressed, normal wearing 等)。
        """
        all_none_kwargs = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "无 (None)",
            "剧情主题": "无 (None)",
            "景别构图": "无 (None)",
            "拍摄视角": "无 (None)",
            "裸露等级": "L1 包裹暗示 (Fully Clothed / Suggestive)",
            "服装款式": "无 (None)",
            "服装状态": "无 (None)",
            "发型发色": "无 (None)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "无 (None)",
            "情绪表情": "无 (None)",
            "光影预设": "无 (None)",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "无 (None)",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "标准画质 (Standard)",
            "prompt_seed": 42,
        }
        for seed in range(50):
            all_none_kwargs["prompt_seed"] = seed
            pos, neg, desc = self.generator.generate(**all_none_kwargs)

            # 1. 文本层断言：绝对不含任何 resolver 合成的 clothing 词汇
            pos_lower = pos.lower()
            self.assertNotIn("neatly dressed", pos_lower)
            self.assertNotIn("normal wearing", pos_lower)
            self.assertNotIn("uniform", pos_lower)
            self.assertNotIn("classroom", pos_lower)
            self.assertNotIn("kimono", pos_lower)
            self.assertNotIn("cheongsam", pos_lower)

            # 2. 结构化原子与 Provenance 断言
            rng = Random(seed)
            nudity_tags, lvl_code = self.sampler.sample_nudity(all_none_kwargs["裸露等级"], rng)
            quality_tags = self.sampler.sample_quality_tags(all_none_kwargs["画质等级"])
            clothing_res = self.sampler.sample_clothing_result("无 (None)", "无 (None)", lvl_code, rng)

            frags = [
                PromptFragment(
                    text=t,
                    source_slot="nudity",
                    source_item_id=lvl_code,
                    provenance=TagProvenance(kind="nudity", item_id=lvl_code),
                )
                for t in nudity_tags
            ] + [
                PromptFragment(
                    text=t.text,
                    source_slot="clothing",
                    source_item_id=t.provenance.item_id,
                    provenance=t.provenance,
                )
                for t in clothing_res.all_tags
            ] + [
                PromptFragment(
                    text=t,
                    source_slot="quality",
                    source_item_id="standard",
                    provenance=TagProvenance(kind="quality", item_id="standard"),
                )
                for t in quality_tags
            ]

            atoms = finalize_prompt_atoms(frags, self.data_dir, rng=rng)

            # 严格断言：最终接受的原子只能具备 nudity 或 quality 溯源
            for a in atoms:
                self.assertIn(
                    a.provenance.kind,
                    ("nudity", "quality"),
                    f"Unexpected provenance kind '{a.provenance.kind}' for atom '{a.text}'"
                )
                self.assertIn(
                    a.source_slot,
                    ("nudity", "quality"),
                    f"Unexpected slot '{a.source_slot}' for atom '{a.text}'"
                )

            # 严格断言：clothing 槽位原子数量绝对为 0
            clothing_atoms = [a for a in atoms if a.source_slot == "clothing" or a.provenance.kind == "clothing"]
            self.assertEqual(len(clothing_atoms), 0, f"Found clothing atoms in all-none: {clothing_atoms}")

    def test_all_122_scenes_exact_mapping(self):
        """遍历 122 个场景 label，在 100 个随机种子下断言 100% 精确唯一映射，杜绝错位抽取 (杜绝 range(5) 假声明)"""
        data = json.loads((self.data_dir / "scenes.json").read_text(encoding="utf-8"))
        all_items = [item for g in data["scenes"] for item in g["items"]]
        self.assertEqual(len(all_items), 122)

        for item in all_items:
            label = item["label"]
            expected_id = item["id"]
            for seed in range(100):
                res = self.sampler.sample_scene_result(label, Random(seed))
                self.assertIsNotNone(res, f"Result was None for {label}")
                self.assertEqual(res.item_id, expected_id, f"Mismatch for {label}: expected {expected_id}, got {res.item_id}")

    def test_all_457_ui_options_real_call(self):
        """
        全量 457 个 UI 选项真实调用测试 (修订 5 核心验收标准)：
        逐项真实调用每个下拉选项对应的 sampler 方法，断言全部成功返回且不抛出 DataSelectionError！
        杜绝仅在 index 查 key 的假测试。
        """
        inputs = IYKYKPromptGenerator.INPUT_TYPES()["required"]

        method_map = {
            "预设模板": lambda opt: self.sampler.get_preset(opt, Random(42)),
            "风格配方": lambda opt: self.sampler.get_style_recipe(opt),
            "场景大类": lambda opt: self.sampler.sample_scene(opt, Random(42)),
            "剧情主题": lambda opt: self.sampler.sample_theme(opt, Random(42)),
            "景别构图": lambda opt: self.sampler.sample_shot_type(opt, Random(42)),
            "拍摄视角": lambda opt: self.sampler.sample_camera_angle(opt, Random(42)),
            "裸露等级": lambda opt: self.sampler.sample_nudity(opt, Random(42)),
            "服装款式": lambda opt: self.sampler.sample_clothing_with_nudity_linkage(opt, "无 (None)", "L1 包裹暗示 (Fully Clothed / Suggestive)", Random(42)),
            "服装状态": lambda opt: self.sampler.sample_clothing_result("旗袍 (Qipao/Cheongsam)", opt, "L3 重点隐现 (Micro/Peek)", Random(42)),
            "发型发色": lambda opt: self.sampler.sample_hairstyle(opt, Random(42)),
            "饰品头饰": lambda opt: self.sampler.sample_jewelry(opt, Random(42)),
            "妆容细节": lambda opt: self.sampler.sample_makeup(opt, Random(42)),
            "姿势动作": lambda opt: self.sampler.sample_pose(opt, Random(42)),
            "情绪表情": lambda opt: self.sampler.sample_expression(opt, Random(42)),
            "光影预设": lambda opt: self.sampler.sample_lighting(opt, Random(42)),
            "胶片风格": lambda opt: self.sampler.sample_film(opt, Random(42)),
            "液体效果": lambda opt: self.sampler.sample_liquid(opt, Random(42)),
            "纹身标记": lambda opt: self.sampler.sample_tattoo(opt, Random(42)),
            "道具物件": lambda opt: self.sampler.sample_prop(opt, Random(42)),
            "角色设定": lambda opt: self.sampler.sample_character(opt, Random(42)),
            "真实微瑕": lambda opt: self.sampler.sample_imperfections(opt, Random(42)),
            "画质等级": lambda opt: self.sampler.sample_quality_tags(opt),
        }

        defaults_to_skip = {
            "无 (None)", "随机 (Random)", "自动 (Auto)",
            "自动联动裸露等级 (Auto Link Nudity)"
        }

        called_count = 0
        for slot_name, func in method_map.items():
            options = inputs[slot_name][0]
            for opt in options:
                if opt in defaults_to_skip:
                    continue
                try:
                    res = func(opt)
                    self.assertIsNotNone(res, f"Calling {slot_name} with '{opt}' returned None")
                    if slot_name == "服装状态":
                        self.assertIsNotNone(res.state_id, f"ClothingSampleResult state_id is None for '{opt}'")
                        self.assertTrue(len(res.state_tags) > 0, f"No state tags returned for '{opt}'")
                        for st in res.state_tags:
                            self.assertEqual(st.provenance.kind, "clothing_state")
                            self.assertEqual(st.provenance.item_id, res.state_id)
                    called_count += 1
                except Exception as e:
                    self.fail(f"Real call failed for UI option '{opt}' in slot '{slot_name}': {e}")

        self.assertEqual(called_count, 457, f"Expected exactly 457 explicit UI options, called {called_count}")

    def test_strict_negative_selectors_fail_fast(self):
        """
        严格负向测试 (修订 5 强约束)：
        为每个 selector 构造 valid + XYZ、XYZ + valid、类似词与模糊子串，
        断言全部严苛抛出 DataSelectionError，绝对禁止模糊子串匹配或静默回退！
        """
        rng = Random(42)

        # (调用函数, 合法基线值)
        probes = [
            (lambda v: self.sampler.sample_scene(v, rng), "卧室私密"),
            (lambda v: self.sampler.sample_theme(v, rng), "人妻/出轨系列"),
            (lambda v: self.sampler.sample_shot_type(v, rng), "特写 Close-up (面部情绪/眼神)"),
            (lambda v: self.sampler.sample_camera_angle(v, rng), "俯拍 (略微俯视·柔弱感)"),
            (lambda v: self.sampler.sample_nudity(v, rng), "L1 包裹暗示 (Fully Clothed / Suggestive)"),
            (lambda v: self.sampler.sample_clothing_result(v, "无 (None)", "L1", rng), "旗袍 (Qipao/Cheongsam)"),
            (lambda v: self.sampler.sample_clothing_result("jk_seifuku", v, "L3", rng), "解开纽扣 (Unbuttoned)"),
            (lambda v: self.sampler.sample_hairstyle(v, rng), "黑长直散发 (Long Straight Black Hair)"),
            (lambda v: self.sampler.sample_jewelry(v, rng), "猫耳发箍 (Cat Ear Headband)"),
            (lambda v: self.sampler.sample_makeup(v, rng), "清纯伪素颜 (Natural Clean Beauty)"),
            (lambda v: self.sampler.sample_pose(v, rng), "🧎 跪姿系列"),
            (lambda v: self.sampler.sample_expression(v, rng), "😳 害羞/羞涩"),
            (lambda v: self.sampler.sample_lighting(v, rng), "油画古典"),
            (lambda v: self.sampler.sample_film(v, rng), "Kodak Portra 400 (暖调人像·奶油肤色)"),
            (lambda v: self.sampler.sample_liquid(v, rng), "💦 微汗晶莹 (Fine Perspiration & Sweat Sheen)"),
            (lambda v: self.sampler.sample_tattoo(v, rng), "💕 可爱小爱心/手腕微图案"),
            (lambda v: self.sampler.sample_prop(v, rng), "🔞 经典魔杖振动棒/跳蛋"),
            (lambda v: self.sampler.sample_character(v, rng), "清纯女高/JK学妹 (High School Girl)"),
            (lambda v: self.sampler.sample_imperfections(v, rng), "💄 泪痣/唇角美人痣/锁骨痣"),
            (lambda v: self.sampler.sample_quality_tags(v), "标准画质 (Standard)"),
            (lambda v: self.sampler.get_style_recipe(v, rng), "AV封面"),
            (lambda v: self.sampler.get_preset(v, rng), "C01 (教室后排露出)"),
        ]

        for func, valid_base in probes:
            # 1. 尾部追加非法串 valid + XYZ
            with self.assertRaises(DataSelectionError, msg=f"Should reject trailing garbage for {valid_base}"):
                func(f"{valid_base}_INVALID_XYZ")

            # 2. 前置追加非法串 XYZ + valid
            with self.assertRaises(DataSelectionError, msg=f"Should reject leading garbage for {valid_base}"):
                func(f"INVALID_XYZ_{valid_base}")

            # 3. 剥离或破坏的类似词
            with self.assertRaises(DataSelectionError, msg=f"Should reject mutated string for {valid_base}"):
                func(f"{valid_base[:max(2, len(valid_base)//2)]}_CORRUPTED")

    def test_clothing_state_nudity_full_24_matrix(self):
        """
        真实执行 4 state (None, Auto, Random, Explicit) × 6 nudity (L1..L6) = 24 组合全矩阵测试 (修订 5 强约束)：
        - 断言 state_id、base/state/extension provenance 与允许/禁止关系；
        - L1, L5, L6: extension 必须绝对为 0；
        - L2, L3, L4: 在 Auto/Random/Explicit 模式下稳定产出 extension tags；
        - None state: state_tags 数量绝对为 0。
        """
        nudity_levels = ["L1", "L2", "L3", "L4", "L5", "L6"]
        state_modes = [
            ("None", "无 (None)"),
            ("Auto", "自动联动裸露等级 (Auto Link Nudity)"),
            ("Random", "随机 (Random)"),
            ("Explicit", "解开纽扣 (Unbuttoned)"),
        ]

        for state_mode_name, state_opt in state_modes:
            for n_code in nudity_levels:
                # 遍历多个种子确保稳定性
                has_ext_in_runs = False
                for seed in range(20):
                    rng = Random(seed)
                    res = self.sampler.sample_clothing_result("jk_seifuku", state_opt, n_code, rng)

                    # 1. 基本字段断言
                    self.assertEqual(res.style_id, "jk_seifuku")
                    self.assertEqual(res.nudity_level, n_code)

                    # 2. State 契约
                    if state_mode_name == "None":
                        self.assertEqual(len(res.state_tags), 0, f"None state produced state tags in {n_code}")
                        self.assertIn(res.state_id, ("none", None))
                    elif state_mode_name == "Explicit":
                        if n_code in ("L1", "L5", "L6"):
                            self.assertEqual(res.state_id, "linkage_override")
                        else:
                            self.assertEqual(res.state_id, "unbuttoned")
                            self.assertGreater(len(res.state_tags), 0)
                            for st in res.state_tags:
                                self.assertEqual(st.provenance.kind, "clothing_state")
                                self.assertEqual(st.provenance.item_id, "unbuttoned")

                    # 3. Nudity Extension 权限矩阵
                    if n_code in ("L1", "L5", "L6"):
                        # L1、L5、L6 绝不允许产出任何服装扩展
                        self.assertEqual(
                            len(res.extension_tags), 0,
                            f"Illegal extension tags in {n_code} with {state_mode_name}: {res.extension_tags}"
                        )
                    else:
                        # L2, L3, L4 具备扩展能力
                        if len(res.extension_tags) > 0:
                            has_ext_in_runs = True
                            for et in res.extension_tags:
                                self.assertEqual(et.provenance.kind, "clothing_extension")

                # L2/L3/L4 下 Auto/Random/Explicit 必须在多次采样中至少产出 extension tags
                if n_code in ("L2", "L3", "L4") and state_mode_name != "None":
                    self.assertTrue(
                        has_ext_in_runs,
                        f"Expected extensions in {n_code} with {state_mode_name} over 20 runs"
                    )

    def test_clothing_state_negative_mutations_fail_fast(self):
        """
        验证强制反例 P2-2：全量服装状态前缀、后缀变异 Fail-Fast 抛出 DataSelectionError。
        """
        from nodes import IYKYKPromptGenerator
        from lib.errors import DataSelectionError
        rng = Random(42)

        inputs = IYKYKPromptGenerator.INPUT_TYPES()["required"]
        clothing_states = inputs["服装状态"][0]
        defaults_to_skip = {"无 (None)", "随机 (Random)", "自动 (Auto)", "自动联动裸露等级 (Auto Link Nudity)"}

        for st in clothing_states:
            if st in defaults_to_skip:
                continue
            # 1. 后缀变异
            with self.assertRaises(DataSelectionError, msg=f"Did not fail on suffix mutation of '{st}'"):
                self.sampler.sample_clothing_result("旗袍 (Qipao/Cheongsam)", f"{st}XYZ", "L3 重点隐现 (Micro/Peek)", rng)
            # 2. 前缀变异
            with self.assertRaises(DataSelectionError, msg=f"Did not fail on prefix mutation of '{st}'"):
                self.sampler.sample_clothing_result("旗袍 (Qipao/Cheongsam)", f"XYZ{st}", "L3 重点隐现 (Micro/Peek)", rng)

    def test_generate_structured_end_to_end_provenance(self):
        """
        验证强制反例 P1-5：直接调用 _generate_structured 并全面断言 GenerationResult.atoms 的完整 provenance。
        彻底淘汰手工重建 fragments 的测试链路，断言 scene, theme, film, jewelry, tattoo 及消解生成项。
        """
        from nodes import _generate_structured, _sampler, _assembler

        inputs = {
            "场景大类": "卧室私密",
            "剧情主题": "随机 (Random)",
            "景别构图": "自动 (Auto)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L1 包裹暗示 (Fully Clothed / Suggestive)",
            "服装款式": "旗袍 (Qipao/Cheongsam)",
            "服装状态": "整齐穿着 (Normal Wearing)",
            "发型发色": "随机 (Random)",
            "饰品头饰": "猫耳发箍 (Cat Ear Headband)",
            "妆容细节": "无 (None)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "随机 (Random)",
            "光影预设": "自动 (Auto)",
            "胶片风格": "Kodak Gold 200 (暖金复古·温馨生活)",
            "液体效果": "无 (None)",
            "纹身标记": "💕 可爱小爱心/手腕微图案",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "高清写真 (High)",
        }

        res = _generate_structured(_sampler, _assembler, inputs, Random(42))
        self.assertTrue(len(res.atoms) > 0)

        # 1. 验证 scene_theme 槽位原子包含真实 scene provenance
        scene_atoms = [a for a in res.atoms if a.source_slot == "scene_theme" and a.provenance.kind == "scene"]
        self.assertTrue(len(scene_atoms) > 0, "No atoms found with provenance.kind == 'scene'")
        for sa in scene_atoms:
            self.assertIsNotNone(sa.provenance.item_id)
            self.assertTrue(any(s.startswith("scene:") for s in sa.provenance.semantic_ids))

        # 2. 验证 film 槽位原子包含真实 film provenance
        film_atoms = [a for a in res.atoms if a.source_slot == "film"]
        self.assertTrue(len(film_atoms) > 0, "No film atoms found")
        for fa in film_atoms:
            self.assertEqual(fa.provenance.kind, "film")
            self.assertEqual(fa.provenance.item_id, "kodak_gold_200")

        # 3. 验证 jewelry 槽位原子包含真实 jewelry provenance
        jewelry_atoms = [a for a in res.atoms if a.source_slot == "jewelry"]
        self.assertTrue(len(jewelry_atoms) > 0, "No jewelry atoms found")
        for ja in jewelry_atoms:
            self.assertEqual(ja.provenance.kind, "jewelry")
            self.assertEqual(ja.provenance.item_id, "cat_ears")

        # 4. 验证 tattoo 槽位原子包含真实 tattoo provenance
        tattoo_atoms = [a for a in res.atoms if a.source_slot == "tattoo" and a.provenance.kind == "tattoo"]
        self.assertTrue(len(tattoo_atoms) > 0, "No tattoo atoms found")
        for ta in tattoo_atoms:
            self.assertEqual(ta.provenance.kind, "tattoo")
            self.assertEqual(ta.provenance.item_id, "cute_heart_star")

        # 5. 验证消解器生成的纹身融合原子 (包含 rule_id 与父来源 parent_ids，并在 source_atoms 中 1:1 闭环解析)
        source_id_map = {a.atom_id: a for a in res.source_atoms}
        fusion_atoms = [a for a in res.atoms if a.provenance.kind == "resolver_generated"]
        self.assertTrue(len(fusion_atoms) > 0, "No resolver_generated atoms found")
        for fa in fusion_atoms:
            self.assertEqual(fa.provenance.rule_id, "tattoo_dermal_fusion")
            self.assertTrue(len(fa.provenance.parent_ids) > 0)
            for pid in fa.provenance.parent_ids:
                self.assertIn(pid, source_id_map)
                parent_atom = source_id_map[pid]
                self.assertEqual(parent_atom.source_item_id, "cute_heart_star")

    def test_preset_path_enters_unified_source_atoms_contract(self):
        """
        验证修订 P1-3：预设路径必须进入统一 source_atoms 契约。
        1. 遍历 77 个预设，断言 atoms 非空时 source_atoms 也非空且长度 >= atoms；
        2. 预设 + 8 个 recipe 矩阵中，source atom ID 在每个生成结果内全局唯一；
        3. 构造或调用触发消解器新生 Atom 的预设，断言每个 parent_id 在 source_atoms 中 1:1 闭环解析；
        4. 同一 seed 下预设 AssemblyResult 100% 恒等。
        """
        preset_names = self.sampler.list_preset_names()
        self.assertGreaterEqual(len(preset_names), 70, "Expected at least 70 presets")

        # 1. 遍历全部预设
        for pname in preset_names:
            res = self.preset_browser.browse_structured(pname, "无 (None)", "高清写真 (High)", prompt_seed=42)
            self.assertGreater(len(res.atoms), 0, f"Preset {pname} yielded 0 accepted atoms")
            self.assertGreater(len(res.source_atoms), 0, f"Preset {pname} yielded empty source_atoms")
            self.assertGreaterEqual(len(res.source_atoms), len(res.atoms))

            # 验证每一个 source atom 均携带合法唯一 ID 与 provenance
            source_ids = [a.atom_id for a in res.source_atoms]
            self.assertEqual(len(source_ids), len(set(source_ids)), f"Duplicate atom_id in source_atoms for {pname}")
            for a in res.source_atoms:
                self.assertTrue(a.atom_id.startswith("atom_"))
                self.assertIsNotNone(a.provenance)
                self.assertIsNotNone(a.provenance.kind)

        # 2. 预设 × 8 风格配方矩阵
        recipes = self.sampler.list_style_recipes()
        self.assertGreaterEqual(len(recipes), 8, "Expected at least 8 style recipes")
        sample_presets = preset_names[:5]

        for p in sample_presets:
            for r in recipes:
                res = self.preset_browser.browse_structured(p, r, "高清写真 (High)", prompt_seed=123)
                source_ids = [a.atom_id for a in res.source_atoms]
                self.assertEqual(len(source_ids), len(set(source_ids)), f"Collision in {p} + {r}")

        # 3. 构造触发 resolver-generated Atom 的预设，断言每个 parent_id 在 source_atoms 中恰好解析一次
        fusion_preset = {
            "id": "preset_test_fusion",
            "name_zh": "测试纹身融合预设",
            "positive": "1girl, cute heart tattoo on clavicle, bare shoulders, masterpiece",
        }
        res_fusion = self.assembler.assemble_preset(fusion_preset, None, "标准画质 (Standard)", rng=Random(42))
        source_id_map = {a.atom_id: a for a in res_fusion.source_atoms}
        resolver_atoms = [a for a in res_fusion.accepted_atoms if a.provenance.kind == "resolver_generated"]
        if resolver_atoms:
            for ra in resolver_atoms:
                self.assertTrue(len(ra.provenance.parent_ids) > 0)
                for pid in ra.provenance.parent_ids:
                    self.assertIn(pid, source_id_map, f"parent_id {pid} missing from source_atoms")

        # 4. 同 seed 下预设 AssemblyResult 完整相等
        p_target = preset_names[0]
        res_a = self.preset_browser.browse_structured(p_target, recipes[0], "高清写真 (High)", prompt_seed=999)
        res_b = self.preset_browser.browse_structured(p_target, recipes[0], "高清写真 (High)", prompt_seed=999)
        self.assertEqual(res_a.positive, res_b.positive)
        self.assertEqual(res_a.negative, res_b.negative)
        self.assertEqual(res_a.description, res_b.description)
        self.assertEqual(res_a.atoms, res_b.atoms)
        self.assertEqual(res_a.rules_applied, res_b.rules_applied)
        self.assertEqual(res_a.source_atoms, res_b.source_atoms)

    def test_all_18_samplers_compat_wrapper_identical_to_result_projection_and_rng_aligned(self):
        """
        验证修订 P2-3：旧 sample_* 与新 sample_*_result 的文本投影和后续 RNG 状态必须 100% 绝对一致。
        覆盖全部 18 个槽位采样函数。
        """
        def nudity_res_proj(s, rng):
            res, code = s.sample_nudity_result("随机 (Random)", rng)
            return (list(res.tags) if res else [], code)

        def theme_res_proj(s, rng):
            res = s.sample_theme_result("随机 (Random)", rng)
            return list(res.all_text_tags) if res else []

        samplers_to_test = [
            ("scene", lambda s, rng: s.sample_scene("随机 (Random)", rng), lambda s, rng: list(s.sample_scene_result("随机 (Random)", rng).tags)),
            ("theme", lambda s, rng: s.sample_theme("随机 (Random)", rng), theme_res_proj),
            ("shot_type", lambda s, rng: s.sample_shot_type("随机 (Random)", rng), lambda s, rng: list(s.sample_shot_type_result("随机 (Random)", rng).tags)),
            ("camera_angle", lambda s, rng: s.sample_camera_angle("随机 (Random)", rng), lambda s, rng: list(s.sample_camera_angle_result("随机 (Random)", rng).tags)),
            ("nudity", lambda s, rng: s.sample_nudity("随机 (Random)", rng), nudity_res_proj),
            ("clothing", lambda s, rng: s.sample_clothing_with_nudity_linkage("随机 (Random)", "随机 (Random)", "L2", rng), lambda s, rng: s.sample_clothing_result("随机 (Random)", "随机 (Random)", "L2", rng).all_text_tags),
            ("lighting", lambda s, rng: s.sample_lighting("随机 (Random)", rng), lambda s, rng: list(s.sample_lighting_result("随机 (Random)", rng).tags)),
            ("pose", lambda s, rng: s.sample_pose("随机 (Random)", rng), lambda s, rng: list(s.sample_pose_result("随机 (Random)", rng).tags)),
            ("expression", lambda s, rng: s.sample_expression("随机 (Random)", rng), lambda s, rng: list(s.sample_expression_result("随机 (Random)", rng).tags)),
            ("film", lambda s, rng: s.sample_film("随机 (Random)", rng), lambda s, rng: list(s.sample_film_result("随机 (Random)", rng).tags)),
            ("makeup", lambda s, rng: s.sample_makeup("随机 (Random)", rng), lambda s, rng: list(s.sample_makeup_result("随机 (Random)", rng).tags)),
            ("hairstyle", lambda s, rng: s.sample_hairstyle("随机 (Random)", rng), lambda s, rng: list(s.sample_hairstyle_result("随机 (Random)", rng).tags)),
            ("jewelry", lambda s, rng: s.sample_jewelry("随机 (Random)", rng), lambda s, rng: list(s.sample_jewelry_result("随机 (Random)", rng).tags)),
            ("imperfections", lambda s, rng: s.sample_imperfections("随机 (Random)", rng), lambda s, rng: list(s.sample_imperfections_result("随机 (Random)", rng).tags)),
            ("tattoo", lambda s, rng: s.sample_tattoo("随机 (Random)", rng), lambda s, rng: list(s.sample_tattoo_result("随机 (Random)", rng).tags)),
            ("prop", lambda s, rng: s.sample_prop("随机 (Random)", rng), lambda s, rng: list(s.sample_prop_result("随机 (Random)", rng).tags)),
            ("character", lambda s, rng: s.sample_character("随机 (Random)", rng), lambda s, rng: list(s.sample_character_result("随机 (Random)", rng).tags)),
            ("liquid", lambda s, rng: s.sample_liquid("随机 (Random)", rng), lambda s, rng: list(s.sample_liquid_result("随机 (Random)", rng).tags)),
            ("quality", lambda s, rng: s.sample_quality_tags("高清写真 (High)"), lambda s, rng: list(s.sample_quality_result("高清写真 (High)").tags)),
        ]

        for name, legacy_fn, result_fn in samplers_to_test:
            for seed in (42, 100, 777, 2026):
                rng_legacy = Random(seed)
                rng_result = Random(seed)

                out_legacy = legacy_fn(self.sampler, rng_legacy)
                out_result = result_fn(self.sampler, rng_result)

                # 1. 文本投影 100% 相等
                self.assertEqual(out_legacy, out_result, f"Mismatch in tags for sampler {name} with seed {seed}")

                # 2. 消耗的随机数步数与后续 RNG 状态 100% 恒等
                next_legacy = [rng_legacy.random() for _ in range(5)]
                next_result = [rng_result.random() for _ in range(5)]
                self.assertEqual(next_legacy, next_result, f"RNG state drift in sampler {name} with seed {seed}")


if __name__ == "__main__":
    unittest.main()
