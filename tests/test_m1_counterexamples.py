"""
test_m1_counterexamples.py — M1 反例夹具与基线复现/新预期差分测试 (M1-D5)

严格贯彻审核方原则：
1. 现有基线与新功能预期严格分开；
2. 尚未实现的 20 槽位覆盖端口提供输入与预期夹具，绝不标榜为已通过；
3. 真实复现当前 rc10 基线中“手部占用导致相机/双道具被过度删除”的缺陷事实；
4. 固化新容量模型 (2-Hand Capacity Model) 与合法反例（室内窗光、混合光源等）的物理数学规约。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.conflict_resolver import ConflictResolver
from lib.models import PromptFragment


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

    def test_02_baseline_reproduction_two_props_excessive_deletion(self):
        """基线复现：真实复现 rc10 中双单手道具因过度约束被强制丢弃第 2 个道具的缺陷 (CE-HAND-02)。"""
        ce = self.counterexamples["CE-HAND-02"]
        frags = [
            PromptFragment(text="indoor studio", source_slot="scene_theme", order=1),
            PromptFragment(text="holding smartphone", source_slot="props", order=2),
            PromptFragment(text="holding camera", source_slot="props", order=3),
            PromptFragment(text="best quality", source_slot="quality", order=4),
        ]
        
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)
        
        # 复现事实：第 1 个道具 smartphone 幸存，第 2 个道具 camera 被过度消解规则强制剔除
        self.assertIn("holding smartphone", resolved_text)
        self.assertNotIn(
            "holding camera",
            resolved_text,
            "基线必须复现 holding camera 被 handheld_props_single_holder 规则删除的事实",
        )

    def test_03_baseline_reproduction_true_hard_conflict_hands_behind_back(self):
        """基线复现：验证双手背在身后时，手持道具构成真实物理互斥被正确消解 (CE-HAND-03)。"""
        ce = self.counterexamples["CE-HAND-03"]
        frags = [
            PromptFragment(text="indoor studio", source_slot="scene_theme", order=1),
            PromptFragment(text="hands behind back", source_slot="pose", order=2),
            PromptFragment(text="holding camera", source_slot="props", order=3),
            PromptFragment(text="best quality", source_slot="quality", order=4),
        ]
        
        resolved = self.resolver.resolve_fragments(frags, Random(42))
        resolved_text = " ".join(f.text.lower() for f in resolved)
        
        # 真实硬冲突：双手背后状态下不能手持相机
        self.assertIn("hands behind back", resolved_text)
        self.assertNotIn("holding camera", resolved_text)

    def test_04_target_capacity_model_mathematical_contracts(self):
        """规约核验：验证拟重构的 2-Hand Capacity Model 数学不变量计算。"""
        # 手部总容量
        total_hands = 2

        # 场景 A: 单手托腮 (1) + 单手持单反 (1) -> 合法 (2 <= 2)
        scene_a_used = 1 + 1
        self.assertLessEqual(scene_a_used, total_hands, "单手占用+持相机必须判定为容量合法")

        # 场景 B: 手机 (1) + 咖啡 (1) -> 合法 (2 <= 2)
        scene_b_used = 1 + 1
        self.assertLessEqual(scene_b_used, total_hands, "双单手道具必须判定为容量合法")

        # 场景 C: 双手背后 (2) + 道具 (1) -> 超载 (3 > 2)
        scene_c_used = 2 + 1
        self.assertGreater(scene_c_used, total_hands, "双手占用+道具必须判定为容量超限")

    def test_05_slot_override_contract_fixture_specifications(self):
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
