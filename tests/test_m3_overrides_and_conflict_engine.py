"""
test_m3_overrides_and_conflict_engine.py — M3 冲突引擎与外部覆盖语义专项测试套件

测试规约与门禁断言：
1. 统一手部容量、服装载体及硬冲突判定：
   - 2-Hand 容量模型：单手占用+单手持物合法共存 (CE-HAND-01)；双单手持物合法共存 (CE-HAND-02)；
   - 外部道具优先于系统姿势：系统姿势退让，保留外部道具 (CE-OVERRIDE-03)；
   - 外部覆盖间不可消解硬冲突显式报错 PromptValidationError，携带 slots/texts/rule_id (CE-OVERRIDE-02)；
   - 软偏好保护：特写构图绝不删除用户显式传入的下半身/鞋袜标签。
2. 20 个装配槽位全量覆盖契约：
   - 18 核心槽位 + 2 辅助槽位 (style_recipe, custom)；
   - 非空覆盖整个槽位，杜绝混杂；
   - 空白 (None, "", "   ", []) 回退到生成器/预设内容；
   - 外部内容携带 mode="override", kind="user_input", source_mode="override"。
3. 外部场景更新情境画像与审计溯源：
   - 外部 scene 更新 ContextProfile 并向下传递；
   - ResolutionReport 与 DecisionLedger 完整可追溯。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.assembler import PromptAssembler, apply_slot_overrides, make_override_fragments
from lib.conflict_resolver import ConflictResolver, is_external_atom
from lib.errors import PromptValidationError
from lib.models import (
    GenerationResult,
    PromptAtom,
    PromptFragment,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)
from lib.slot_contract import ALLOWED_SLOTS, AUXILIARY_SLOT_ORDER, SLOT_ORDER, normalize_slot_name
from nodes import _generate_structured


class TestM3OverridesAndConflictEngine(unittest.TestCase):
    """M3 阶段核心专项验收测试。"""

    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(__file__).parent.parent / "data"
        cls.assembler = PromptAssembler(cls.data_dir)
        cls.resolver = cls.assembler.resolver
        cls.fixtures_path = Path(__file__).parent / "fixtures" / "m1_counterexamples.json"
        with open(cls.fixtures_path, "r", encoding="utf-8") as f:
            cls.counterexamples = {
                item["id"]: item for item in json.load(f).get("counterexamples", [])
            }

    # ─────────────────────────────────────────────────────────────
    # 1. 20 槽位覆盖契约测试 (非空整槽替换 + 空白安全回退)
    # ─────────────────────────────────────────────────────────────

    def test_01_all_20_slots_constant_contract(self):
        """验证 18 核心槽位 + 2 辅助槽位总计 20 个合法槽位集合。"""
        all_slots = set(SLOT_ORDER) | set(AUXILIARY_SLOT_ORDER)
        self.assertEqual(len(all_slots), 20)
        self.assertEqual(all_slots, ALLOWED_SLOTS)

    def test_02_all_20_slots_non_empty_override_replaces_entire_slot(self):
        """验证全部 20 个槽位在外部传入非空覆盖时，整槽替换原有内容，杜绝混杂。"""
        dummy_slots = {s: [f"system_default_{s}_tag1", f"system_default_{s}_tag2"] for s in ALLOWED_SLOTS}

        for slot in ALLOWED_SLOTS:
            override_val = f"custom_override_value_for_{slot}"
            overrides = {slot: override_val}
            applied = apply_slot_overrides(dummy_slots, overrides)

            # 验证被覆盖槽位：旧内容完全清空，只有新内容
            slot_frags = applied[slot]
            self.assertTrue(len(slot_frags) >= 1)
            self.assertEqual(slot_frags[0].text, override_val)
            self.assertEqual(slot_frags[0].source_slot, slot)
            self.assertEqual(slot_frags[0].origin.mode, "override")
            self.assertEqual(slot_frags[0].provenance.source_mode, "override")
            self.assertEqual(slot_frags[0].provenance.kind, "user_input")

            # 验证其他槽位不受影响
            for other_slot in ALLOWED_SLOTS:
                if other_slot != slot:
                    self.assertEqual(
                        [f.text if hasattr(f, "text") else str(f) for f in applied[other_slot]],
                        [f"system_default_{other_slot}_tag1", f"system_default_{other_slot}_tag2"],
                    )

    def test_03_all_20_slots_blank_fallback(self):
        """验证全部 20 个槽位在外部传入空白 (None, '', '   ', []) 时，完整回退保留原内容。"""
        dummy_slots = {s: [f"system_val_{s}"] for s in ALLOWED_SLOTS}

        blank_values = [None, "", "   ", []]
        for slot in ALLOWED_SLOTS:
            for blank in blank_values:
                applied = apply_slot_overrides(dummy_slots, {slot: blank})
                self.assertEqual(
                    [f.text if hasattr(f, "text") else str(f) for f in applied[slot]],
                    [f"system_val_{slot}"],
                    f"Slot {slot} failed blank fallback for value {blank!r}",
                )

    def test_04_unknown_slot_override_fails_closed(self):
        """未知槽位外部覆盖必须 Fail-Closed 抛出 PromptValidationError。"""
        dummy_slots = {"lighting": ["soft light"]}
        with self.assertRaises(PromptValidationError) as ctx:
            apply_slot_overrides(dummy_slots, {"completely_invalid_slot_name": "custom val"})
        self.assertIn("completely_invalid_slot_name", str(ctx.exception))

    # ─────────────────────────────────────────────────────────────
    # 2. CE-OVERRIDE-01: 服装整槽覆盖与旧载体语义清空
    # ─────────────────────────────────────────────────────────────

    def test_05_ce_override_01_clothing_clean_replacement(self):
        """CE-OVERRIDE-01 验证：外部服装覆盖整槽替换，连带清空旧白衬衫及解纽扣状态，杜绝任何残留。"""
        ce = self.counterexamples["CE-OVERRIDE-01"]
        inputs = ce["inputs"]

        # 模拟预设基础内容：白衬衫 + 解纽扣 + 领口张开
        mock_preset = {
            "id": "preset_office_casual",
            "name_zh": "职场日常",
            "fragments": [
                {"text": "white shirt", "slot": "clothing"},
                {"text": "unbuttoned", "slot": "clothing_state"},
                {"text": "collar open", "slot": "clothing_state"},
            ],
        }

        # 传入外部覆盖：紧身乳胶连体衣
        overrides = {"clothing_override": inputs["clothing_override"]}

        res = self.assembler.assemble_preset(
            preset=mock_preset,
            style_recipe=None,
            quality_tier="高清写真 (High)",
            rng=Random(42),
            external_overrides=overrides,
        )

        res_lower = res.prompt.lower()
        # 1. 预期新服装必须存在
        self.assertIn("black latex catsuit", res_lower)
        self.assertIn("glossy finish", res_lower)

        # 2. 严格核验 forbidden_remnants 绝不残留
        for remnant in ce["contract_requirements"]["forbidden_remnants"]:
            self.assertNotIn(
                remnant.lower(),
                res_lower,
                f"CE-OVERRIDE-01 失败：检测到禁止的旧服装/状态残留 '{remnant}'！",
            )

    # ─────────────────────────────────────────────────────────────
    # 3. CE-OVERRIDE-02: 外部覆盖间不可消解硬冲突显式报错
    # ─────────────────────────────────────────────────────────────

    def test_06_ce_override_02_external_hard_conflict_raises_error(self):
        """CE-OVERRIDE-02 验证：全裸覆盖与厚重大衣覆盖同时传入时，引擎绝不静默吞掉，显式抛出 PromptValidationError。"""
        ce = self.counterexamples["CE-OVERRIDE-02"]
        inputs = ce["inputs"]

        # 构造外部覆盖字典
        overrides = {
            "nudity": inputs["nudity_override"],
            "clothing": inputs["clothing_override"],
        }

        base_slots = {"lighting": ["cinematic light"]}

        with self.assertRaises(PromptValidationError) as ctx:
            self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)

        err = ctx.exception
        # 契约断言：必须携带 slots、texts 与 rule_id 字段
        self.assertTrue(hasattr(err, "slots"))
        self.assertTrue(hasattr(err, "texts"))
        self.assertTrue(hasattr(err, "rule_id"))

        self.assertIn("nudity", err.slots)
        self.assertIn("clothing", err.slots)
        self.assertEqual(err.rule_id, "nudity_clothing_conflicts")
        self.assertIn("completely nude", " ".join(err.texts).lower())
        self.assertIn("parka", " ".join(err.texts).lower())

    # ─────────────────────────────────────────────────────────────
    # 4. CE-OVERRIDE-03: 外部指定道具优先于系统姿势
    # ─────────────────────────────────────────────────────────────

    def test_07_ce_override_03_external_prop_overrides_system_pose(self):
        """CE-OVERRIDE-03 验证：系统背手姿势 (双手占用) 与外部手持相机冲突时，外部道具优先保留，系统姿势退让。"""
        ce = self.counterexamples["CE-OVERRIDE-03"]
        inputs = ce["inputs"]

        # 系统姿势: hands behind back (both_busy)
        system_pose = PromptFragment(
            text="hands behind back",
            source_slot="pose",
            order=0,
            origin=SelectionOrigin(mode="random", selector="pose"),
            facts=SemanticFacts(hand_state="both_busy"),
        )

        base_slots = {
            "pose": [system_pose],
        }

        # 用户外部覆盖道具: holding vintage leica camera
        overrides = {
            "props_override": inputs["props_override"],
        }

        res = self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)

        res_lower = res.prompt.lower()
        # 外部道具 100% 保留
        self.assertIn("holding vintage leica camera", res_lower)
        # 系统背手姿势被消解调整
        self.assertNotIn("hands behind back", res_lower)

        # 审计决策记录断言
        decisions = res.resolution_report.decisions
        drop_decs = [d for d in decisions if d.rule_id == "pose_hand_occupation" and d.action == "drop"]
        self.assertTrue(len(drop_decs) >= 1)
        self.assertEqual(drop_decs[0].reason_code, "external_prop_overrides_pose")

    # ─────────────────────────────────────────────────────────────
    # 5. 2-Hand 手部容量模型与合法共存
    # ─────────────────────────────────────────────────────────────

    def test_08_ce_hand_01_one_busy_and_single_prop_coexists_for_override(self):
        """CE-HAND-01 验证：单手占用 (1) + 外部单手道具 (1) <= 2，物理容量充足，合法共存。"""
        # 姿势单手托腮 (one_busy)
        pose_atom = PromptFragment(
            text="hand on cheek",
            source_slot="pose",
            order=0,
            origin=SelectionOrigin(mode="random", selector="pose"),
            facts=SemanticFacts(hand_state="one_busy"),
        )
        base_slots = {"pose": [pose_atom]}
        overrides = {"props_override": "holding compact camera"}

        res = self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)
        res_lower = res.prompt.lower()

        self.assertIn("hand on cheek", res_lower)
        self.assertIn("holding compact camera", res_lower)

    def test_09_ce_hand_02_dual_single_props_coexist_for_override(self):
        """CE-HAND-02 验证：在双手空闲时，两件外部单手道具 (1+1=2) 合法共存。"""
        base_slots = {"lighting": ["cinematic light"]}
        overrides = {"props_override": "holding smartphone in left hand, holding coffee cup in right hand"}

        res = self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)
        res_lower = res.prompt.lower()

        self.assertIn("holding smartphone in left hand", res_lower)
        self.assertIn("holding coffee cup in right hand", res_lower)

    def test_10_external_pose_and_external_prop_exceeding_capacity_raises_error(self):
        """两项外部输入导致手部容量超载时 (双手背后 + 手持相机 3 > 2)，显式报错。"""
        overrides = {
            "pose": "hands tied behind back",
            "props": "holding heavy sword",
        }
        base_slots = {"lighting": ["soft light"]}

        with self.assertRaises(PromptValidationError) as ctx:
            self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)

        err = ctx.exception
        self.assertEqual(err.rule_id, "pose_hand_occupation")
        self.assertIn("pose", err.slots)
        self.assertIn("props", err.slots)

    # ─────────────────────────────────────────────────────────────
    # 6. 构图软偏好保护 (Rule 13)
    # ─────────────────────────────────────────────────────────────

    def test_11_framing_lower_body_soft_preference_never_drops_external_clothing(self):
        """验证 Rule 13 (特写构图)：绝不因特写软偏好删除外部显式传入的下装/高跟鞋。"""
        base_slots = {
            "shot_type": [PromptFragment(text="close-up", source_slot="shot_type", order=0)],
        }
        overrides = {
            "clothing_override": "high heels, silk skirt",
        }

        res = self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)
        res_lower = res.prompt.lower()

        # 外部输入的鞋裙必须 100% 保留
        self.assertIn("high heels", res_lower)
        self.assertIn("silk skirt", res_lower)

    # ─────────────────────────────────────────────────────────────
    # 7. 外部场景更新情境画像 (ContextProfile)
    # ─────────────────────────────────────────────────────────────

    def test_12_external_scene_override_updates_context_profile(self):
        """验证当通过 scene_override 传入特定场景时，情境画像自洽更新为对应的 context。"""
        # 1. 传入温泉场景
        inputs_onsen = {
            "scene_override": "traditional onsen bath with rising steam and cedar tubs",
            "服装款式": "自动 (Auto)",
            "裸露等级": "L1 包裹暗示 (Fully Clothed / Suggestive)",
        }
        gen_res = _generate_structured(
            self.assembler.resolver.sampler if hasattr(self.assembler.resolver, "sampler") else self.assembler,
            self.assembler,
            inputs_onsen,
            rng=Random(100),
            effective_seed=100,
        ) if hasattr(self, "_run_gen") else None

        # 直接通过 DataSampler 与 assembler 验证流水线更新
        from lib.sampler import DataSampler
        sampler = DataSampler(self.data_dir)
        gen_res = _generate_structured(
            sampler,
            self.assembler,
            inputs_onsen,
            rng=Random(100),
            effective_seed=100,
        )

        self.assertIsNotNone(gen_res.context_profile)
        ctx_dict = dict(gen_res.context_profile.weights)
        self.assertIn("onsen_bath", ctx_dict)
        self.assertGreater(ctx_dict["onsen_bath"], 0.5)

        # 2. 传入教室场景
        inputs_school = {
            "scene_override": "high school classroom with blackboard and wooden desks",
        }
        gen_res_school = _generate_structured(
            sampler,
            self.assembler,
            inputs_school,
            rng=Random(200),
            effective_seed=200,
        )
        ctx_school = dict(gen_res_school.context_profile.weights)
        self.assertIn("school", ctx_school)
        self.assertGreater(ctx_school["school"], 0.5)

    # ─────────────────────────────────────────────────────────────
    # 8. 审计追踪与 Provenance 溯源闭环
    # ─────────────────────────────────────────────────────────────

    def test_13_audit_trail_and_provenance_closure(self):
        """验证外部覆盖原子的 Provenance 完整性与 ResolutionReport 审计报告闭环。"""
        overrides = {
            "expression": "sultry seductive gaze",
            "lighting": "moody neon split lighting",
        }
        base_slots = {
            "clothing": [PromptFragment(text="summer dress", source_slot="clothing", order=0)],
        }

        res = self.assembler.assemble_slots(base_slots, rng=Random(42), external_overrides=overrides)

        report = res.resolution_report
        self.assertIsNotNone(report)
        self.assertEqual(report.schema_version, "1.0")

        # 检查采纳的原子的来源模式
        expr_atoms = [a for a in res.accepted_atoms if a.source_slot == "expression"]
        self.assertTrue(len(expr_atoms) >= 1)
        for a in expr_atoms:
            self.assertEqual(a.origin.mode, "override")
            self.assertEqual(a.provenance.source_mode, "override")
            self.assertEqual(a.provenance.kind, "user_input")
            self.assertTrue(is_external_atom(a))


if __name__ == "__main__":
    unittest.main()
