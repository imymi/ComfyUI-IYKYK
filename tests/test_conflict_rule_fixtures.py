"""
test_conflict_rule_fixtures.py — 测试 Level A 与 Level B 冲突消解测试夹具契约
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_DIR))

from tests.fixtures.conflict_rule_fixtures import (
    BindingStatus,
    extract_garment_entities,
    find_bound_carrier,
    get_all_level_a_fixtures,
    get_all_level_b_fixtures,
    get_level_a_fixture_a1,
    get_level_a_fixture_a2,
    get_level_a_fixture_a3,
    get_level_a_fixture_a4,
    get_level_a_fixture_a5,
    get_level_a_fixture_a6,
    get_level_a_fixture_a7,
    make_test_atom,
)


class TestConflictRuleFixtures(unittest.TestCase):
    def test_level_a_fixture_count(self):
        """验证 Level A 夹具总数为 7 项 (A-1 ~ A-7)"""
        cases = get_all_level_a_fixtures()
        self.assertEqual(len(cases), 7)
        self.assertEqual([c.case_id for c in cases], ["A-1", "A-2", "A-3", "A-4", "A-5", "A-6", "A-7"])

    def test_level_a_fixture_a1_structure(self):
        """验证用例 A-1 (无纽扣连体服与解扣互斥) 结构与断言契约"""
        a1 = get_level_a_fixture_a1()
        self.assertEqual(a1.case_id, "A-1")
        self.assertEqual(len(a1.input_atoms), 2)
        self.assertFalse(a1.is_counterexample)
        self.assertEqual(a1.expected_rule_id, "clothing_style_state_coherence")
        self.assertEqual(a1.expected_reason_code, "one_piece_state_conflict")
        self.assertEqual(len(a1.expected_dropped_atom_ids), 1)
        self.assertEqual(len(a1.expected_survived_atom_ids), 1)

    def test_level_a_fixture_a2_structure(self):
        """验证用例 A-2 (带扣连体服合法共存反例) 结构与断言契约"""
        a2 = get_level_a_fixture_a2()
        self.assertEqual(a2.case_id, "A-2")
        self.assertEqual(len(a2.input_atoms), 2)
        self.assertTrue(a2.is_counterexample)
        self.assertEqual(len(a2.expected_dropped_atom_ids), 0)
        self.assertEqual(len(a2.expected_survived_atom_ids), 2)

    def test_level_a_fixture_a3_structure(self):
        """验证用例 A-3 (下装裁切与上身解扣保留反例) 结构与断言契约"""
        a3 = get_level_a_fixture_a3()
        self.assertEqual(a3.case_id, "A-3")
        self.assertEqual(len(a3.input_atoms), 3)
        self.assertTrue(a3.is_counterexample)
        self.assertEqual(a3.expected_rule_id, "clothing_style_state_coherence")
        self.assertEqual(a3.expected_reason_code, "state_lacks_carrier")
        self.assertEqual(len(a3.expected_dropped_atom_ids), 1)
        self.assertEqual(len(a3.expected_survived_atom_ids), 2)

    def test_level_a_fixture_a4_structure(self):
        """验证用例 A-4 (背景遗弃物反例) 结构与断言契约"""
        a4 = get_level_a_fixture_a4()
        self.assertEqual(a4.case_id, "A-4")
        self.assertEqual(len(a4.input_atoms), 2)
        self.assertTrue(a4.is_counterexample)
        self.assertEqual(a4.expected_rule_id, "clothing_style_state_coherence")
        self.assertEqual(a4.expected_reason_code, "state_lacks_carrier")
        self.assertEqual(len(a4.expected_dropped_atom_ids), 1)
        self.assertEqual(len(a4.expected_survived_atom_ids), 1)

    def test_level_a_fixture_a5_structure(self):
        """验证用例 A-5 (同一服装多原子不自发满足叠穿反例) 结构与断言契约"""
        a5 = get_level_a_fixture_a5()
        self.assertEqual(a5.case_id, "A-5")
        self.assertEqual(len(a5.input_atoms), 3)
        self.assertTrue(a5.is_counterexample)
        self.assertEqual(a5.expected_rule_id, "clothing_style_state_coherence")
        self.assertEqual(a5.expected_reason_code, "layering_mismatch")
        self.assertEqual(len(a5.expected_dropped_atom_ids), 1)
        self.assertEqual(len(a5.expected_survived_atom_ids), 2)

    def test_level_a_fixture_a6_structure(self):
        """验证用例 A-6 (同一预设展开两件衣物保留两个独立实体反例) 结构与断言契约"""
        a6 = get_level_a_fixture_a6()
        self.assertEqual(a6.case_id, "A-6")
        self.assertEqual(len(a6.input_atoms), 4)
        self.assertTrue(a6.is_counterexample)
        self.assertEqual(len(a6.expected_dropped_atom_ids), 0)
        self.assertEqual(len(a6.expected_survived_atom_ids), 4)

    def test_level_a_fixture_a7_structure(self):
        """验证用例 A-7 (两件衣物定向丢弃与输入换序不变性反例契约) 结构与断言契约"""
        a7 = get_level_a_fixture_a7()
        self.assertEqual(a7.case_id, "A-7")
        self.assertEqual(len(a7.input_atoms), 3)
        self.assertTrue(a7.is_counterexample)
        self.assertEqual(len(a7.expected_dropped_atom_ids), 0)
        self.assertEqual(len(a7.expected_survived_atom_ids), 3)

    def test_a6_preset_atoms_remain_separate_entities(self):
        """断言用例 A-6 来源于同一预设 preset_ol 的衬衫与包臀裙保留为两个独立实体，绝不错误合并"""
        a6 = get_level_a_fixture_a6()
        entities_map = extract_garment_entities(a6.input_atoms)

        # 必须刚好提取出 2 个服装实体 (衬衫实体与裙子实体)
        self.assertEqual(len(entities_map), 2)
        shirt_key = "preset:preset_ol#clothing#shirts_blouses"
        skirt_key = "preset:preset_ol#clothing#pencil_skirt"
        self.assertIn(shirt_key, entities_map)
        self.assertIn(skirt_key, entities_map)

        # 衬衫实体的 2 枚原子 (主体+领型) 聚合在同一个实体内
        shirt_entity = entities_map[shirt_key]
        self.assertEqual(len(shirt_entity.member_atoms), 2)
        self.assertEqual(shirt_entity.selected_id, "shirts_blouses")

        # 裙子实体的 1 枚原子独立存在
        skirt_entity = entities_map[skirt_key]
        self.assertEqual(len(skirt_entity.member_atoms), 1)
        self.assertEqual(skirt_entity.selected_id, "pencil_skirt")

    def test_a7_permutation_invariance_and_targeted_discard(self):
        """断言用例 A-7 在输入原子全部 6 种全排列下，定向注销结果与保留实体 100% 换序不变，上装完好保留"""
        import itertools
        a7 = get_level_a_fixture_a7()
        atoms = list(a7.input_atoms)

        for perm in itertools.permutations(atoms):
            entities_map = extract_garment_entities(perm)
            self.assertEqual(len(entities_map), 2)

            discarded_atom = [a for a in perm if a.source_item_id == "discarded" or (a.origin and a.origin.selected_id == "discarded")][0]
            binding = find_bound_carrier(discarded_atom, list(entities_map.values()))

            # 无论原子在输入列表中排列在何处，均精确绑定到下装 pleated_skirt
            self.assertEqual(binding.status, BindingStatus.BOUND)
            self.assertIsNotNone(binding.target_entity)
            self.assertEqual(binding.target_entity.selected_id, "pleated_skirt")

            # 模拟执行精准注销
            binding.target_entity.is_worn = False
            binding.target_entity.is_ambient = True

            # 断言：上装衬衫 100% 保留在穿，绝不受下装遗弃影响！
            shirt_entity = [e for e in entities_map.values() if e.selected_id == "shirts_blouses"][0]
            self.assertTrue(shirt_entity.is_worn)
            self.assertFalse(shirt_entity.is_ambient)

    def test_binding_ladder_ambiguity_refusal(self):
        """断言当场景存在多个同类候选实体且无明确绑定时，严格拒绝 entities[0] 盲选，返回歧义状态"""
        shirt = make_test_atom(
            text="dress shirt",
            source_slot="clothing",
            item_id="shirts_blouses",
            garment_topologies=("top",),
            garment_states=("worn",),
        )
        coat = make_test_atom(
            text="trench coat",
            source_slot="clothing",
            item_id="trench_coat",
            garment_topologies=("top", "outerwear"),
            garment_states=("worn",),
        )
        # 未指定 target 的解扣动作
        unbuttoned = make_test_atom(
            text="unbuttoned collar",
            source_slot="clothing_state",
            item_id="unbuttoned",
            garment_states=("opened",),
        )

        entities_map = extract_garment_entities([shirt, coat])
        self.assertEqual(len(entities_map), 2)

        # 两件衣服均有纽扣能力 (ALLOWED_BUTTON_STYLES)，无法确定解扣哪一件
        binding = find_bound_carrier(unbuttoned, list(entities_map.values()))
        self.assertEqual(binding.status, BindingStatus.AMBIGUOUS_MULTIPLE_CANDIDATES)
        self.assertIsNone(binding.target_entity)  # 严禁 entities[0] 盲选

    def test_counterexample_only_swimsuit_unbuttoned_has_no_candidate(self):
        """
        反例 1：仅连体泳衣＋解扣状态 -> 严禁盲目绑定泳衣，必须裁决为无兼容承载物 UNBOUND_NO_CANDIDATE。
        """
        swimsuit = make_test_atom(
            text="school swimsuit",
            source_slot="clothing",
            item_id="swimsuit_school",
            garment_topologies=("one_piece",),
            garment_states=("worn",),
        )
        unbuttoned = make_test_atom(
            text="unbuttoned collar",
            source_slot="clothing_state",
            item_id="unbuttoned",
            garment_states=("opened",),
        )
        entities_map = extract_garment_entities([swimsuit])
        self.assertEqual(len(entities_map), 1)

        binding = find_bound_carrier(unbuttoned, list(entities_map.values()))
        self.assertEqual(binding.status, BindingStatus.UNBOUND_NO_CANDIDATE)
        self.assertIsNone(binding.target_entity)

    def test_counterexample_fuzzy_substring_target_rejected(self):
        """
        反例 2：丢弃目标写成 'shirt'，现存 'shirts_blouses' -> 严禁子串模糊匹配，必须报告 UNBOUND_TARGET_NOT_FOUND。
        """
        shirt = make_test_atom(
            text="formal dress shirt",
            source_slot="clothing",
            item_id="shirts_blouses",
            garment_topologies=("top",),
            garment_states=("worn",),
        )
        discarded = make_test_atom(
            text="discarded shirt",
            source_slot="clothing_state",
            item_id="discarded",
            garment_states=("discarded",),
            target_id="shirt",  # 模糊子串目标
        )
        entities_map = extract_garment_entities([shirt])
        self.assertEqual(len(entities_map), 1)

        binding = find_bound_carrier(discarded, list(entities_map.values()))
        self.assertEqual(binding.status, BindingStatus.UNBOUND_TARGET_NOT_FOUND)
        self.assertIsNone(binding.target_entity)

    def test_counterexample_missing_target_fails_fast_without_fallback(self):
        """
        反例 3：明确丢弃 'pencil_skirt'，现场只有衬衫 -> 目标未命中快速失败，绝不回退改绑现场唯一服装。
        """
        shirt = make_test_atom(
            text="formal dress shirt",
            source_slot="clothing",
            item_id="shirts_blouses",
            garment_topologies=("top",),
            garment_states=("worn",),
        )
        discarded_skirt = make_test_atom(
            text="skirt discarded on floor",
            source_slot="clothing_state",
            item_id="discarded",
            garment_states=("discarded",),
            target_id="pencil_skirt",  # 目标不存在于现场
        )
        entities_map = extract_garment_entities([shirt])
        self.assertEqual(len(entities_map), 1)

        binding = find_bound_carrier(discarded_skirt, list(entities_map.values()))
        self.assertEqual(binding.status, BindingStatus.UNBOUND_TARGET_NOT_FOUND)
        self.assertIsNone(binding.target_entity)
        # 断言：现场唯一的衬衫 100% 保持在穿，绝不受未命中遗弃影响！
        self.assertTrue(list(entities_map.values())[0].is_worn)

    def test_explicit_target_incompatible_fails_fast(self):
        """
        补充用例：显式目标存在但不具备对应形制能力（连体泳衣解扣） -> 快速失败 UNBOUND_INCOMPATIBLE。
        """
        swimsuit = make_test_atom(
            text="school swimsuit",
            source_slot="clothing",
            item_id="swimsuit_school",
            garment_topologies=("one_piece",),
            garment_states=("worn",),
        )
        unbuttoned = make_test_atom(
            text="unbuttoned",
            source_slot="clothing_state",
            item_id="unbuttoned",
            garment_states=("opened",),
            target_id="swimsuit_school",
        )
        entities_map = extract_garment_entities([swimsuit])
        binding = find_bound_carrier(unbuttoned, list(entities_map.values()))
        self.assertEqual(binding.status, BindingStatus.UNBOUND_INCOMPATIBLE)
        self.assertIsNotNone(binding.target_entity)

    def test_level_b_integration_cases_structure(self):
        """验证 Level B 节点端到端集成用例"""
        b_cases = get_all_level_b_fixtures()
        self.assertEqual(len(b_cases), 2)
        c1, c2 = b_cases[0], b_cases[1]
        self.assertEqual(c1.case_id, "TC-INT-001")
        self.assertEqual(c2.case_id, "TC-INT-002")

        # 检查参数名符合 nodes.py 规范
        for c in (c1, c2):
            self.assertIn("服装款式", c.inputs)
            self.assertIn("服装状态", c.inputs)
            self.assertIn("裸露等级", c.inputs)
            self.assertIn("prompt_seed", c.inputs)


if __name__ == "__main__":
    unittest.main()
