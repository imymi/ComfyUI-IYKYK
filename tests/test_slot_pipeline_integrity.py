"""
test_slot_pipeline_integrity.py — 18 槽位流水线契约、无循环依赖、中性哨兵与 Tag 级去重测试
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from random import Random

from lib.assembler import PromptAssembler, assemble_prompt, assemble_prompt_atoms
from lib.conflict_resolver import ConflictResolver
from lib.errors import PromptValidationError
from lib.models import PromptFragment
from lib.slot_contract import ALLOWED_SLOTS, SLOT_ALIASES, SLOT_ORDER, normalize_slot_name, validate_slots_subset
from nodes import IYKYKPromptGenerator

DATA_DIR = Path(__file__).parent.parent / "data"


class TestSlotPipelineIntegrity(unittest.TestCase):
    def setUp(self):
        self.assembler = PromptAssembler(DATA_DIR)
        self.resolver = ConflictResolver(DATA_DIR)
        self.generator = IYKYKPromptGenerator()

    def test_canonical_18_slot_order_snapshot(self):
        """快照测试：断言内部装配流水线严格按既有 18 槽位顺序执行，防止优先级漂移"""
        expected_order = (
            "scene_theme",
            "shot_type",
            "camera_angle",
            "character",
            "nudity",
            "clothing",
            "lighting",
            "pose",
            "expression",
            "makeup",
            "hairstyle",
            "jewelry",
            "imperfections",
            "tattoo",
            "props",
            "liquids",
            "film",
            "quality",
        )
        self.assertEqual(SLOT_ORDER, expected_order)
        self.assertEqual(len(SLOT_ORDER), 18)

    def test_slot_alias_normalization(self):
        """测试历史别名与调用方别名规范化映射 (P2-3 修复验证)"""
        alias_expected = {
            "poses": "pose",
            "lighting_palette": "lighting",
            "scene": "scene_theme",
            "theme": "scene_theme",
            "hairstyles": "hairstyle",
            "expressions": "expression",
            "film_stock": "film",
            "accessories": "jewelry",
            "tattoos": "tattoo",
            "prop": "props",
            "liquid": "liquids",
            "recipe": "style_recipe",
            "scene_theme": "scene_theme",
            "quality": "quality",
        }
        for alias, expected in alias_expected.items():
            self.assertEqual(normalize_slot_name(alias), expected, f"Alias '{alias}' did not normalize to '{expected}'")

    def test_slot_alias_collision_fails_closed(self):
        """
        验证必须修订 8：槽位别名冲突 Fail-Closed。
        若同时提供两个非空 raw keys 归一化到同一规范槽位（如 scene 与 theme，或 scene_theme 与 scene），
        必须抛出 PromptValidationError 并明确指出冲突键。
        """
        # 1. 两个不同别名碰撞 (scene 与 theme 均映射到 scene_theme)
        with self.assertRaises(PromptValidationError) as ctx1:
            self.assembler.assemble({"scene": ["cyberpunk"], "theme": ["investigation"]})
        self.assertIn("Conflicting slot inputs normalized to canonical slot 'scene_theme'", str(ctx1.exception))
        self.assertIn("scene", str(ctx1.exception))
        self.assertIn("theme", str(ctx1.exception))

        # 2. 规范名与别名碰撞 (scene_theme 与 scene)
        with self.assertRaises(PromptValidationError) as ctx2:
            self.assembler.assemble({"scene_theme": ["cyberpunk"], "scene": ["cyberpunk"]})
        self.assertIn("Conflicting slot inputs normalized to canonical slot 'scene_theme'", str(ctx2.exception))

        # 3. 允许其中一个为空
        out = self.assembler.assemble({"scene": ["cyberpunk"], "theme": []})
        self.assertIn("cyberpunk", out)

    def test_atomizer_isolation_no_circular_dependency(self):
        """验证 lib.atomizer 模块为纯净单向依赖，可独立导入而不触发循环导入"""
        # 测试在独立命名空间中导入 lib.atomizer
        import importlib
        atomizer_mod = importlib.import_module("lib.atomizer")
        self.assertTrue(hasattr(atomizer_mod, "fragments_to_atoms"))
        self.assertTrue(hasattr(atomizer_mod, "atoms_to_tags"))
        self.assertTrue(hasattr(atomizer_mod, "atoms_to_fragments"))
        self.assertTrue(hasattr(atomizer_mod, "deduplicate_tags"))
        self.assertTrue(hasattr(atomizer_mod, "PromptTag"))

    def test_assembler_neutral_sentinel_preserves_all_18_slots(self):
        """
        中性哨兵测试：向 18 个槽位分别传入唯一中性 tag，
        断言 assembler 绝不丢失任何槽位，且严格按 SLOT_ORDER 顺序输出。
        """
        sentinel_slots = {}
        for slot in SLOT_ORDER:
            sentinel_slots[slot] = [f"sentinel_{slot}_tag"]

        output = self.assembler.assemble(sentinel_slots, rng=Random(42))
        tokens = [t.strip() for t in output.split(",")]

        # 断言所有 18 个哨兵均在输出中
        for slot in SLOT_ORDER:
            expected_tag = f"sentinel_{slot}_tag"
            self.assertIn(expected_tag, tokens, f"Slot {slot} sentinel was dropped by assembler!")

        # 断言 18 个哨兵的相对顺序完全符合 SLOT_ORDER
        indices = [tokens.index(f"sentinel_{slot}_tag") for slot in SLOT_ORDER]
        self.assertEqual(indices, sorted(indices), f"Slot order in output violated SLOT_ORDER: {tokens}")

    def test_assembler_fails_closed_on_unknown_non_empty_slot(self):
        """验证向 assembler 传入未知非空槽位时，必须 Fail-Closed 抛出 PromptValidationError"""
        bad_slots = {
            "scene_theme": ["street corner"],
            "totally_unknown_custom_slot": ["rogue_value"],
        }
        with self.assertRaises(PromptValidationError):
            self.assembler.assemble(bad_slots)

        # 允许空的未知槽位通过
        empty_bad_slots = {
            "scene_theme": ["street corner"],
            "totally_unknown_custom_slot": [],
        }
        out = self.assembler.assemble(empty_bad_slots)
        self.assertIn("street corner", out)

    def test_end_to_end_film_jewelry_tattoo_reachability(self):
        """
        真实端到端生成测试 (P1-1 生产回归修复验证)：
        分别显式设置胶片、饰品、纹身，断言生成文本与 All-None 截然不同，且对应 tag 真实入选！
        """
        base_kwargs = {
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

        # 1. 基准：All-None 输出
        none_pos, _, _ = self.generator.generate(**base_kwargs)

        # 2. 切换 胶片风格
        film_kwargs = dict(base_kwargs)
        film_kwargs["胶片风格"] = "Kodak Portra 400 (暖调人像·奶油肤色)"
        film_pos, _, _ = self.generator.generate(**film_kwargs)
        self.assertNotEqual(film_pos, none_pos, "Film slot failed to affect generator output!")
        self.assertTrue(
            any(w in film_pos.lower() for w in ["kodak", "portra", "warm tones", "skin tone"]),
            f"Film tags missing in output: {film_pos}"
        )

        # 3. 切换 饰品头饰
        jewelry_kwargs = dict(base_kwargs)
        jewelry_kwargs["饰品头饰"] = "猫耳发箍 (Cat Ear Headband)"
        jewelry_pos, _, _ = self.generator.generate(**jewelry_kwargs)
        self.assertNotEqual(jewelry_pos, none_pos, "Jewelry slot failed to affect generator output!")
        self.assertTrue(
            any(w in jewelry_pos.lower() for w in ["cat ear", "headband"]),
            f"Jewelry tags missing in output: {jewelry_pos}"
        )

        # 4. 切换 纹身标记
        tattoo_kwargs = dict(base_kwargs)
        tattoo_kwargs["纹身标记"] = "💕 可爱小爱心/手腕微图案"
        tattoo_pos, _, _ = self.generator.generate(**tattoo_kwargs)
        self.assertNotEqual(tattoo_pos, none_pos, "Tattoo slot failed to affect generator output!")
        self.assertTrue(
            any(w in tattoo_pos.lower() for w in ["heart", "tattoo", "wrist"]),
            f"Tattoo tags missing in output: {tattoo_pos}"
        )

    def test_tag_level_deduplication_blackbox_immunity(self):
        """
        完整 Tag 级去重黑盒免疫测试 (P1-2 修复验证)：
        - 重复 LoRA、带不同 plain 后缀的重复 LoRA 绝对不能被删！
        - 重复 Quoted 短语绝对不能被删！
        - 普通 Plain 标签全局首次出现保留、后续去重！
        """
        # 1. 相同 LoRA + 不同 Plain 后缀：两者均保留
        frags = [
            PromptFragment(text="<lora:x:1> foo", source_slot="custom"),
            PromptFragment(text="<lora:x:1> bar", source_slot="custom"),
        ]
        out = assemble_prompt(frags, DATA_DIR, rng=Random(42))
        self.assertIn("<lora:x:1> foo", out)
        self.assertIn("<lora:x:1> bar", out)

        # 2. 相同 Quoted + 不同 Plain 后缀：两者均保留
        frags2 = [
            PromptFragment(text='"quoted" foo', source_slot="custom"),
            PromptFragment(text='"quoted" bar', source_slot="custom"),
        ]
        out2 = assemble_prompt(frags2, DATA_DIR, rng=Random(42))
        self.assertIn('"quoted" foo', out2)
        self.assertIn('"quoted" bar', out2)

        # 3. 两个完全同构的纯 LoRA tag：黑盒免疫删除式去重，两者均保留
        frags3 = [
            PromptFragment(text="<lora:test:0.8>", source_slot="custom"),
            PromptFragment(text="<lora:test:0.8>", source_slot="custom"),
        ]
        out3 = assemble_prompt(frags3, DATA_DIR, rng=Random(42))
        self.assertEqual(out3.count("<lora:test:0.8>"), 2)

        # 4. 跨槽位相同普通 Tag：全局首见保留，后续去重
        frags4 = [
            PromptFragment(text="sharp focus", source_slot="quality"),
            PromptFragment(text="masterpiece", source_slot="quality"),
            PromptFragment(text="sharp focus", source_slot="lighting"),  # 重复普通词
        ]
        out4 = assemble_prompt(frags4, DATA_DIR, rng=Random(42))
        self.assertEqual(out4.count("sharp focus"), 1)

    def test_resolver_compatibility_interface_protects_blackboxes(self):
        """
        兼容接口受保护语法免改写测试 (P1-4 修复验证)：
        通过 resolve_fragments() 与 resolve() 传入 LoRA 或 Quoted，
        内部字节绝不被冲突规则改写，且返回完整 Tag Fragment！
        """
        # 1. resolve_fragments 保护 LoRA
        frags = [
            PromptFragment(text="<lora:spinning room:1.0>", source_slot="custom"),
            PromptFragment(text="in a spinning room", source_slot="custom"),
        ]
        resolved = self.resolver.resolve_fragments(frags, rng=Random(42))

        # 断言返回的仍是 2 个完整 Tag
        self.assertEqual(len(resolved), 2)
        # LoRA 内部 spinning room 绝对保持不变
        self.assertEqual(resolved[0].text, "<lora:spinning room:1.0>")
        # 普通文本中的 spinning room 被消解替换为 drunken stupor
        self.assertIn("drunken stupor", resolved[1].text)
        self.assertNotIn("spinning room", resolved[1].text)

        # 2. resolve() 槽位字典接口同样受保护
        slots = {
            "custom": ["<lora:spinning room:1.0>", "in a spinning room"]
        }
        res_slots = self.resolver.resolve(slots, rng=Random(42))
        self.assertEqual(res_slots["custom"][0], "<lora:spinning room:1.0>")
        self.assertIn("drunken stupor", res_slots["custom"][1])

    def test_assemble_to_fragments_provenance_and_recipe(self):
        """
        验证强制反例 P2-5：assemble_to_fragments 必须为无 provenance 的项赋予默认 TagProvenance，
        绝不能为 None，且必须包含 style_recipe 辅助槽位。
        """
        slots = {
            "scene_theme": ["classroom, blackboard"],
            "clothing": ["jk_seifuku"],
            "style_recipe": ["cinematic lighting, warm atmosphere"],
        }
        frags = self.assembler.assemble_to_fragments(slots)
        self.assertEqual(len(frags), 5)

        # 1. 验证所有 fragment 的 provenance 绝不为 None
        for f in frags:
            self.assertIsNotNone(f.provenance, f"Fragment {f.text} had provenance None")
            self.assertIsNotNone(f.provenance.kind)

        # 2. 验证包含 style_recipe 槽位
        recipe_frags = [f for f in frags if f.source_slot == "style_recipe"]
        self.assertEqual(len(recipe_frags), 2)
        self.assertIn("cinematic lighting", recipe_frags[0].text)

    def test_conflict_resolver_reuse_spy(self):
        """
        验证强制反例 P2-4：PromptAssembler 必须在实例中复用 ConflictResolver，
        连续执行 10 次生成只加载并解析 conflict_rules.json 1 次，杜绝每次生成重复 IO 解析。
        """
        from unittest.mock import patch

        assembler = PromptAssembler(DATA_DIR)
        slots = {
            "scene_theme": ["classroom"],
            "clothing": ["jk_seifuku"],
        }

        with patch("lib.conflict_resolver.RuleRegistry._load_rules") as mock_load:
            for _ in range(10):
                prompt, atoms, rules = assembler.assemble_result(slots, rng=Random(42))
                self.assertIsInstance(prompt, str)
            # 已经复用 assembler.resolver，_load_rules 在生成过程中调用次数为 0
            self.assertEqual(mock_load.call_count, 0)

    def test_auxiliary_slot_order_snapshot_and_custom_at_tail(self):
        """快照测试：锁定 18 核心槽位 + 2 辅助槽位 ('style_recipe', 'custom')，断言 custom 永远在流水线末尾消费 (R4)"""
        from lib.slot_contract import AUXILIARY_SLOT_ORDER
        self.assertEqual(AUXILIARY_SLOT_ORDER, ("style_recipe", "custom"))
        self.assertEqual(ALLOWED_SLOTS, set(SLOT_ORDER) | set(AUXILIARY_SLOT_ORDER))

        # 输入包含各槽位，验证消费顺序
        all_slots_input = {
            "custom": ["custom_tag_end"],
            "quality": ["masterpiece"],
            "scene_theme": ["classroom"],
            "style_recipe": ["cinematic lighting"],
        }
        frags = list(self.assembler.iter_normalized_slot_fragments(all_slots_input))
        slots_consumed = [f.source_slot for f in frags]
        # 断言 custom 必须排在最后
        self.assertEqual(slots_consumed, ["scene_theme", "quality", "style_recipe", "custom"])
        self.assertEqual(frags[-1].text, "custom_tag_end")
        self.assertEqual(frags[-1].source_slot, "custom")

    def test_empty_input_and_max_words_boundaries(self):
        """测试空输入与 max_words=0/1/3 严格契约 (R5)"""
        # 1. max_words=0: 返回空 prompt 与空 accepted atoms，不抛超限异常
        prompt0, atoms0, rules0 = self.assembler.assemble_result({}, max_words=0)
        self.assertEqual(prompt0, "")
        self.assertEqual(len(atoms0), 0)

        # 2. max_words=1: 容纳不下 2 词的 best quality，但可完整容纳 1 词的 masterpiece
        prompt1, atoms1, rules1 = self.assembler.assemble_result({}, max_words=1)
        self.assertEqual(prompt1, "masterpiece")
        self.assertEqual(len(atoms1), 1)
        self.assertEqual(atoms1[0].text, "masterpiece")

        # 3. max_words>=3: 容纳完整默认画质 (best quality, masterpiece)
        prompt3, atoms3, rules3 = self.assembler.assemble_result({}, max_words=3)
        self.assertEqual(prompt3, "best quality, masterpiece")
        self.assertEqual([a.text for a in atoms3], ["best quality", "masterpiece"])

        # 4. 验证渲染函数按 tag 分组插入 ', '
        from lib.assembler import render_atoms
        from lib.atomizer import fragments_to_atoms
        frags = [
            PromptFragment(text="1girl", source_slot="character", order=0),
            PromptFragment(text="solo", source_slot="character", order=1),
            PromptFragment(text="smile", source_slot="expression", order=2),
        ]
        tags, atoms = fragments_to_atoms(frags)
        rendered = render_atoms(atoms)
        self.assertEqual(rendered, "1girl, solo, smile")


    def test_order_authority_and_slot_shape_and_max_words_fail_closed(self):
        """测试片段 order 权威排序、槽位输入形状与 max_words 非法值 Fail-Closed (P1-4, P2-1, P2-2)"""
        from lib.atomizer import fragments_to_atoms
        from lib.assembler import iter_normalized_slot_fragments, assemble_result
        from lib.conflict_resolver import ConflictResolver
        from random import Random

        # 1. P1-4: 逆序传入两场景片段时，order=0 的场景排在前面并作为空间主场景胜出
        resolver = ConflictResolver(DATA_DIR)
        frags_rev = [
            PromptFragment(text="beach at night", source_slot="scene_theme", order=10),
            PromptFragment(text="classroom", source_slot="scene_theme", order=0),
        ]
        tags, atoms = fragments_to_atoms(frags_rev)
        self.assertEqual([t.text for t in tags], ["classroom", "beach at night"])
        resolved, _ = resolver.resolve_atoms_with_report(atoms, Random(42))
        self.assertEqual([a.text for a in resolved], ["classroom"])

        # 2. P1-4: 视线互斥与多手持道具同样按 order 决定优先项
        props_rev = [
            PromptFragment(text="holding black compact digital camera", source_slot="props", order=5),
            PromptFragment(text="holding game controller", source_slot="props", order=1),
        ]
        _, prop_atoms = fragments_to_atoms(props_rev)
        resolved_props, _ = resolver.resolve_atoms_with_report(prop_atoms, Random(42))
        self.assertEqual([a.text for a in resolved_props], ["holding game controller"])

        # 3. P1-4: 相同 order 使用输入位置稳定排序
        frags_same_order = [
            PromptFragment(text="1girl", source_slot="character", order=0),
            PromptFragment(text="solo", source_slot="character", order=0),
        ]
        tags_same, _ = fragments_to_atoms(frags_same_order)
        self.assertEqual([t.text for t in tags_same], ["1girl", "solo"])

        # 4. P1-4: 单个片段包含多个顶层 tag 时顺序连续递增
        frags_multi_tag = [
            PromptFragment(text="1girl, solo", source_slot="character", order=0),
            PromptFragment(text="smile", source_slot="expression", order=1),
        ]
        tags_multi, atoms_multi = fragments_to_atoms(frags_multi_tag)
        self.assertEqual([t.tag_order for t in tags_multi], [0, 1, 2])
        self.assertEqual([t.text for t in tags_multi], ["1girl", "solo", "smile"])

        # 5. P1-4: 非法 order (负数、浮点数、字符串、布尔值) 立即报错
        for bad_order in (-1, -10, 1.5, "0", True):
            with self.assertRaises(PromptValidationError, msg=f"Should reject order={bad_order!r}"):
                fragments_to_atoms([PromptFragment(text="test", source_slot="custom", order=bad_order)])

        # 6. P2-1: 槽位输入形状矩阵校验 (接受 str, PromptFragment, list/tuple; 拒绝 set, dict, int, list 含非法项)
        valid_str_frags = list(iter_normalized_slot_fragments({"custom": "custom tag"}))
        self.assertEqual(len(valid_str_frags), 1)
        self.assertEqual(valid_str_frags[0].text, "custom tag")

        valid_tuple_frags = list(iter_normalized_slot_fragments({"custom": ("tag1", "tag2")}))
        self.assertEqual([f.text for f in valid_tuple_frags], ["tag1", "tag2"])

        for bad_shape in ({"tag1"}, {"key": "val"}, 123, 45.6, [123], ["tag", None]):
            with self.assertRaises(PromptValidationError, msg=f"Should reject slot shape {bad_shape!r}"):
                list(iter_normalized_slot_fragments({"custom": bad_shape}))

        # P2-1 新增反例：已知槽位显式提供 scalar None 必须 Fail-Closed 拒绝
        for known_slot in ("custom", "scene_theme", "clothing"):
            with self.assertRaises(PromptValidationError, msg=f"Should reject scalar None for known slot {known_slot!r}") as ctx:
                list(iter_normalized_slot_fragments({known_slot: None}))
            self.assertIn(f"Slot '{known_slot}'", str(ctx.exception))
            self.assertIn("explicitly provided as None", str(ctx.exception))

        # 空字典继续正常工作 (空输出)
        self.assertEqual(list(iter_normalized_slot_fragments({})), [])
        # 未知槽位且为 None 继续兼容忽略 (空输出)
        self.assertEqual(list(iter_normalized_slot_fragments({"unknown_ignored_slot": None})), [])

        # 7. P2-2: max_words 非法值 Fail-Closed (负数、浮点、字符串、布尔值)
        for bad_mw in (-1, -100, 1.5, "250", True, False):
            with self.assertRaises(PromptValidationError, msg=f"Should reject max_words={bad_mw!r}"):
                assemble_result("test", DATA_DIR, max_words=bad_mw)


if __name__ == "__main__":
    unittest.main()
