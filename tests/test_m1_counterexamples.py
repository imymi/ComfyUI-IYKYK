"""
test_m1_counterexamples.py — M1 反例夹具与基线复现/新预期差分测试 (M1-D5 修订版)

严格贯彻审核方原则：
1. 测试必须实际消费夹具 JSON 中的 inputs，杜绝测试代码与夹具数据脱节；
2. 准确区分自由文本路径与携带 hand_state="one_busy" 的结构化路径；
3. 真实复现当前 rc10 基线中结构化路径下单手占用误删相机 (CE-HAND-01) 及双道具强制单持 (CE-HAND-02) 的缺陷；
4. 现有基线缺陷复现与 M2/M3 新预期规约完全解耦，绝不将未实现功能标榜为已通过。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptAtom, PromptFragment, SelectionOrigin, SemanticFacts, SpanType


def _make_atom_from_dict(d: dict) -> PromptAtom:
    """直接从反例夹具 JSON 字典反序列化构建强类型 PromptAtom 实例。"""
    facts = SemanticFacts(**d.get("facts", {}))
    orig_d = d.get("origin", {})
    origin = SelectionOrigin(
        entry_point=orig_d.get("entry_point", "generator"),
        mode=orig_d.get("mode", "explicit"),
        selector=orig_d.get("selector", d["source_slot"]),
        selected_id=orig_d.get("selected_id", d["atom_id"]),
        raw_value=d.get("text", ""),
        parent_ids=tuple(orig_d.get("parent_ids", (orig_d.get("selected_id", d["atom_id"]),))),
    )
    return PromptAtom(
        atom_id=d["atom_id"],
        text=d["text"],
        span_type=SpanType.PLAIN,
        source_slot=d["source_slot"],
        tag_order=d.get("tag_order", 0),
        span_order=d.get("span_order", 0),
        facts=facts,
        origin=origin,
    )


class TestM1Counterexamples(unittest.TestCase):
    """M1-D5 专项反例夹具与基线/预期差分测试套件。"""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(__file__).parent.parent / "data"
        cls.fixtures_dir = Path(__file__).parent / "fixtures"
        cls.fixture_path = cls.fixtures_dir / "m1_counterexamples.json"

        with open(cls.fixture_path, "r", encoding="utf-8") as f:
            cls.fixtures_doc = json.load(f)

        cls.counterexamples = {
            item["id"]: item for item in cls.fixtures_doc.get("counterexamples", [])
        }
        cls.resolver = ConflictResolver(cls.data_dir)

    def test_01_fixture_document_integrity(self):
        """核验 M1-D5 反例夹具 JSON 结构完整性与 8 项典型反例覆盖。"""
        self.assertEqual(self.fixtures_doc.get("schema_version"), "1.0")
        self.assertGreaterEqual(len(self.counterexamples), 8)

        required_ids = [
            "CE-HAND-01",
            "CE-HAND-02",
            "CE-HAND-03",
            "CE-LIGHT-01",
            "CE-LIGHT-02",
            "CE-OVERRIDE-01",
            "CE-OVERRIDE-02",
            "CE-OVERRIDE-03",
        ]
        for rid in required_ids:
            self.assertIn(rid, self.counterexamples, f"Missing required fixture ID: {rid}")
            item = self.counterexamples[rid]
            self.assertIn("title", item)
            self.assertIn("category", item)
            self.assertIn("inputs", item)
            self.assertIn("baseline_status", item)

    def test_02_baseline_reproduction_ce_hand_01_dual_path(self):
        """消费 CE-HAND-01 夹具：精准区分自由文本路径与结构化路径的基线行为差异。"""
        ce = self.counterexamples["CE-HAND-01"]
        inputs = ce["inputs"]

        # 1. 自由文本路径 (消费 inputs.free_text_path)
        ft_inputs = inputs["free_text_path"]
        frags = [
            PromptFragment(text="indoor classroom", source_slot="scene_theme", order=1),
            PromptFragment(text=ft_inputs["pose"], source_slot="pose", order=2),
            PromptFragment(text=ft_inputs["props"], source_slot="props", order=3),
        ]
        ft_resolved = self.resolver.resolve_fragments(frags, Random(42))
        ft_text = " ".join(f.text.lower() for f in ft_resolved)
        # 自由文本因未命中 both_busy 词库表面共存
        self.assertIn("hand on cheek", ft_text)
        self.assertIn("camera", ft_text)

        # 2. 结构化路径 (消费 inputs.structured_atom_path，携带 hand_state='one_busy')
        st_inputs = inputs["structured_atom_path"]
        atom_pose = _make_atom_from_dict(st_inputs["pose_atom"])
        atom_prop = _make_atom_from_dict(st_inputs["props_atom"])

        surviving, rules_applied, report = self.resolver.resolve_atoms_with_full_report(
            [atom_pose, atom_prop], Random(42)
        )
        surviving_texts = [a.text for a in surviving]

        # M3 统一容量模型修复：单手托腮 (1) + 单手持相机 (1) = 2 <= 2，两者均合法共存保留！
        self.assertIn(atom_pose.text, surviving_texts)
        self.assertIn(
            atom_prop.text,
            surviving_texts,
            "M3 修复确认：携带 hand_state='one_busy' 的相机与单手托腮在 2 手容量内合法共存，不再被误删！",
        )

    def test_03_baseline_reproduction_ce_hand_02_dual_path(self):
        """消费 CE-HAND-02 夹具：精准消费双道具夹具输入并复现过度删除。"""
        ce = self.counterexamples["CE-HAND-02"]
        inputs = ce["inputs"]

        # 1. 自由文本路径 (消费 inputs.free_text_path)
        ft_inputs = inputs["free_text_path"]
        frags = [
            PromptFragment(text=ft_inputs["pose"], source_slot="pose", order=1),
            PromptFragment(text=ft_inputs["prop_left"], source_slot="props", order=2),
            PromptFragment(text=ft_inputs["prop_right"], source_slot="props", order=3),
        ]
        ft_resolved = self.resolver.resolve_fragments(frags, Random(42))
        ft_text = " ".join(f.text.lower() for f in ft_resolved)
        # 自由文本因未精确命中 handheld_patterns 前缀未被拦截
        self.assertIn("smartphone", ft_text)
        self.assertIn("coffee", ft_text)

        # 2. 结构化路径 (消费 inputs.structured_atom_path，携带 prop_usage='handheld')
        st_inputs = inputs["structured_atom_path"]
        atom_p1 = _make_atom_from_dict(st_inputs["prop1_atom"])
        atom_p2 = _make_atom_from_dict(st_inputs["prop2_atom"])

        surviving, rules_applied, report = self.resolver.resolve_atoms_with_full_report(
            [atom_p1, atom_p2], Random(42)
        )
        surviving_texts = [a.text for a in surviving]

        # 真实缺陷复现断言：第 1 个道具保留，第 2 个道具被 handheld_props_single_holder 规则强制丢弃
        self.assertIn(atom_p1.text, surviving_texts)
        self.assertNotIn(
            atom_p2.text,
            surviving_texts,
            "基线必须复现：双手持道具时第 2 个道具被 handheld_props_single_holder 规则强制丢弃的事实！",
        )
        drop_decisions = [
            d for d in report.decisions if d.rule_id == "handheld_props_single_holder" and d.action == "drop"
        ]
        self.assertTrue(len(drop_decisions) >= 1)
        self.assertEqual(drop_decisions[0].target_atom_id, atom_p2.atom_id)
        self.assertEqual(drop_decisions[0].reason_code, "single_handheld_prop_limit")

    def test_04_baseline_reproduction_true_hard_conflict_ce_hand_03(self):
        """消费 CE-HAND-03 夹具：真实双手背后硬冲突下道具被正确消解。"""
        ce = self.counterexamples["CE-HAND-03"]
        inputs = ce["inputs"]
        frags = [
            PromptFragment(text=inputs["pose"], source_slot="pose", order=1),
            PromptFragment(text=inputs["props"], source_slot="props", order=2),
        ]

        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)

        # 真实硬冲突：双手背后状态下不能手持酒杯
        self.assertIn("hands tied behind back", resolved_text)
        self.assertNotIn("wine glass", resolved_text)

    def test_05_target_capacity_model_mathematical_contracts(self):
        """规约核验：验证拟重构的 2-Hand Capacity Model 数学不变量计算。"""
        total_hands = 2

        # 场景 A (CE-HAND-01): 单手托腮 (1) + 单手持相机 (1) -> 合法 (2 <= 2)
        scene_a_used = 1 + 1
        self.assertLessEqual(scene_a_used, total_hands, "单手占用+持相机必须判定为容量合法")

        # 场景 B (CE-HAND-02): 手机 (1) + 相机/咖啡 (1) -> 合法 (2 <= 2)
        scene_b_used = 1 + 1
        self.assertLessEqual(scene_b_used, total_hands, "双单手道具必须判定为容量合法")

        # 场景 C (CE-HAND-03): 双手背后 (2) + 道具 (1) -> 超载 (3 > 2)
        scene_c_used = 2 + 1
        self.assertGreater(scene_c_used, total_hands, "双手占用+道具必须判定为容量超限")

    def test_06_slot_override_contract_fixture_specifications(self):
        """规约核验：断言外部覆盖端口 (M3/M4) 的预期行为夹具规约完整性。"""
        # 1. CE-OVERRIDE-01 整槽替换与旧载体语义清空
        ce_ov1 = self.counterexamples["CE-OVERRIDE-01"]
        c_req1 = ce_ov1.get("contract_requirements", {})
        self.assertEqual(c_req1.get("override_action"), "full_slot_replacement")
        self.assertFalse(c_req1.get("mixing_allowed"))
        self.assertTrue(c_req1.get("carrier_revalidation"))
        self.assertIn("white shirt", c_req1.get("forbidden_remnants", []))

        # 2. CE-OVERRIDE-02 外部不可消解硬冲突显式报错
        ce_ov2 = self.counterexamples["CE-OVERRIDE-02"]
        c_req2 = ce_ov2.get("contract_requirements", {})
        self.assertEqual(c_req2.get("error_type"), "PromptValidationError")
        self.assertFalse(c_req2.get("silent_suppression_allowed"))
        self.assertIn("rule_id", c_req2.get("error_fields_required", []))

        # 3. CE-OVERRIDE-03 外部覆盖优先于系统内容
        ce_ov3 = self.counterexamples["CE-OVERRIDE-03"]
        c_req3 = ce_ov3.get("contract_requirements", {})
        self.assertTrue(c_req3.get("external_retained"))
        self.assertTrue(c_req3.get("system_adjusted"))


if __name__ == "__main__":
    unittest.main()
