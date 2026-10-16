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
    # 9. 真实仓库预设 C01 整槽覆盖回归 (审核整改 1)
    # ─────────────────────────────────────────────────────────────

    def test_14_real_preset_c01_full_slot_replacements(self):
        """[Review-1] 验证真实预设 C01 在覆盖服装、场景、光影时，旧预设内容彻底清空，杜绝混杂。"""
        from lib.sampler import DataSampler
        sampler = DataSampler(self.data_dir)
        preset_c01 = sampler.get_preset("C01", Random(42))
        self.assertIsNotNone(preset_c01)

        # 1. 服装整槽覆盖：原有 school uniform, sailor shirt unbuttoned, skirt hiked up, no panties 必须被清空
        res_clothing = self.assembler.assemble_preset(
            preset_c01,
            style_recipe=None,
            quality_tier="高清写真 (High)",
            rng=Random(42),
            entry_point="preset_browser",
            external_overrides={"clothing": "black latex catsuit"},
        )
        self.assertIn("black latex catsuit", res_clothing.prompt)
        self.assertNotIn("school uniform", res_clothing.prompt)
        self.assertNotIn("sailor shirt", res_clothing.prompt)
        self.assertNotIn("skirt hiked up", res_clothing.prompt)
        self.assertNotIn("no panties", res_clothing.prompt)

        # 2. 场景整槽覆盖：原有 classroom, back row, class in progress, wooden desk 必须被清空
        res_scene = self.assembler.assemble_preset(
            preset_c01,
            style_recipe=None,
            quality_tier="高清写真 (High)",
            rng=Random(42),
            entry_point="preset_browser",
            external_overrides={"scene_theme": "traditional outdoor onsen hot spring bath"},
        )
        scene_tags = [t.strip() for t in res_scene.prompt.split(",")]
        self.assertIn("traditional outdoor onsen hot spring bath", scene_tags)
        self.assertNotIn("classroom", scene_tags)
        self.assertNotIn("back row", scene_tags)
        self.assertNotIn("class in progress", scene_tags)
        self.assertNotIn("wooden desk", scene_tags)

        # 3. 光影整槽覆盖：原有 soft classroom light, window side light 必须被清空
        res_lighting = self.assembler.assemble_preset(
            preset_c01,
            style_recipe=None,
            quality_tier="高清写真 (High)",
            rng=Random(42),
            entry_point="preset_browser",
            external_overrides={"lighting": "moody dramatic studio lighting with deep shadows"},
        )
        lighting_tags = [t.strip() for t in res_lighting.prompt.split(",")]
        self.assertIn("moody dramatic studio lighting with deep shadows", lighting_tags)
        self.assertNotIn("soft classroom light", lighting_tags)
        self.assertNotIn("window side light", lighting_tags)

    # ─────────────────────────────────────────────────────────────
    # 10. 统一手部容量模型与边界缺陷回归 (审核整改 2)
    # ─────────────────────────────────────────────────────────────

    def test_15_unified_hand_capacity_and_priorities(self):
        """[Review-2] 统一手部容量模型：按道具实体累计、双向统一、识别 restrained、继承双手事实。"""
        # A. 三件外部单手道具 (1+1+1=3 > 2)：必须检测到 3 > 2 并报错
        with self.assertRaises(PromptValidationError) as cm:
            self.assembler.assemble_slots(
                {},
                rng=Random(42),
                external_overrides={"props": "holding smartphone, holding wine glass, holding book"},
            )
        self.assertIn("exceed total hand capacity of 2", str(cm.exception))

        # B. 外部单手托腮 + 两件单手道具 (1 + 1+1 = 3 > 2)：累计姿势与全部道具报错
        with self.assertRaises(PromptValidationError) as cm_pose:
            self.assembler.assemble_slots(
                {},
                rng=Random(42),
                external_overrides={
                    "pose": "hand on cheek",
                    "props": "holding smartphone, holding wine glass",
                },
            )
        self.assertTrue(
            "conflicts with slot" in str(cm_pose.exception)
            or "exceed total hand capacity of 2" in str(cm_pose.exception)
        )

        # C. 系统单手托腮 + 系统单手相机 (1 + 1 = 2 <= 2)：双向统一，相机合法保留绝不误删
        # 模拟生成器中的单手托腮姿势与单手相机道具
        pose_frag = PromptFragment(
            text="hand on cheek",
            source_slot="pose",
            order=0,
            facts=SemanticFacts(hand_state="one_busy", hands_required=1),
            provenance=TagProvenance(item_id="pose_cheek", kind="pose"),
        )
        cam_frag = PromptFragment(
            text="digital camera in hand",
            source_slot="props",
            order=1,
            facts=SemanticFacts(prop_usage="handheld", hands_required=1),
            provenance=TagProvenance(item_id="prop_cam", kind="props"),
        )
        res_sys = self.assembler.assemble_slots(
            {"pose": [pose_frag], "props": [cam_frag]},
            rng=Random(42),
        )
        accepted_texts = [a.text for a in res_sys.accepted_atoms]
        self.assertIn("hand on cheek", accepted_texts)
        self.assertIn("digital camera in hand", accepted_texts)

        # D. 系统 bound arms (restrained=2) + 外部相机 (1)：外部道具优先，裁撤系统受限姿势
        bound_frag = PromptFragment(
            text="bound arms",
            source_slot="pose",
            order=0,
            facts=SemanticFacts(hand_state="restrained", hands_required=2),
            provenance=TagProvenance(item_id="restraints__bound_arms", kind="pose"),
        )
        res_ext_cam = self.assembler.assemble_slots(
            {"pose": [bound_frag]},
            rng=Random(42),
            external_overrides={"props": "holding camera"},
        )
        accepted_cam_texts = [a.text for a in res_ext_cam.accepted_atoms]
        self.assertIn("holding camera", accepted_cam_texts)
        self.assertNotIn("bound arms", accepted_cam_texts)

        # E. 外部 holding game controller：权威继承目录事实 hands_required=2
        game_frags = make_override_fragments(
            "props",
            "holding game controller",
            entry_point="generator",
            data_dir=self.data_dir,
        )
        self.assertEqual(game_frags[0].facts.hands_required, 2)
        self.assertEqual(game_frags[0].facts.prop_usage, "handheld")

        # 结合另一个单手道具 (2 + 1 = 3 > 2) 必须报错
        with self.assertRaises(PromptValidationError):
            self.assembler.assemble_slots(
                {},
                rng=Random(42),
                external_overrides={"props": "holding game controller, holding smartphone"},
            )

    # ─────────────────────────────────────────────────────────────
    # 11. 外部内容绝不改写与绝不追加增强词回归 (审核整改 3)
    # ─────────────────────────────────────────────────────────────

    def test_16_external_content_protection_across_rules(self):
        """[Review-3] 检查全部规则：外部内容绝不被替换，绝不被追加注入系统增强词。"""
        # 1. 材质替换规则保护：clothing="sheer silk blouse" 绝不被替换为 wet dress
        res_cloth = self.assembler.assemble_slots(
            {},
            rng=Random(42),
            external_overrides={"clothing": "sheer silk blouse"},
        )
        self.assertIn("sheer silk blouse", res_cloth.prompt)
        self.assertNotIn("wet dress", res_cloth.prompt)
        self.assertNotIn("clinging tightly", res_cloth.prompt)

        # 2. 纹身增强规则保护：tattoo="dragon tattoo on shoulder" 绝不被追加注入 pores visible through ink
        res_tattoo = self.assembler.assemble_slots(
            {},
            rng=Random(42),
            external_overrides={"tattoo": "dragon tattoo on shoulder"},
        )
        tattoo_accepted = [a.text for a in res_tattoo.accepted_atoms if a.source_slot == "tattoo"]
        self.assertEqual(tattoo_accepted, ["dragon tattoo on shoulder"])
        self.assertNotIn("pores visible through ink", res_tattoo.prompt)

    # ─────────────────────────────────────────────────────────────
    # 12. 外部场景彻底清除旧情境画像与别名统一 (审核整改 4)
    # ─────────────────────────────────────────────────────────────

    def test_17_scene_override_complete_context_purge(self):
        """[Review-4] 外部温泉场景覆盖原有教师/学生主题后，彻底清空旧情境贡献与旧 ID，0% school。"""
        from lib.sampler import DataSampler
        sampler = DataSampler(self.data_dir)

        # 1. 通过外部 overrides={"scene_override": ...} 传入
        inputs_override = {
            "场景大类": "教室",
            "剧情主题": "教师/学生系列",
            "external_overrides": {"scene_override": "traditional Japanese onsen hot spring bath"},
        }
        gen_res = _generate_structured(
            sampler,
            self.assembler,
            inputs_override,
            rng=Random(42),
            effective_seed=42,
        )

        self.assertIsNotNone(gen_res.context_profile)
        weights = dict(gen_res.context_profile.weights)
        # 必须 100% onsen_bath，彻底清除旧主题贡献，school 必须为 0.0%
        self.assertEqual(weights.get("school", 0.0), 0.0)
        self.assertAlmostEqual(weights.get("onsen_bath", 0.0), 1.0, places=3)
        self.assertIn("traditional Japanese onsen hot spring bath", gen_res.positive)

        # 2. 直接传入顶级参数 scene_override="..."
        inputs_top_level = {
            "场景大类": "教室",
            "剧情主题": "教师/学生系列",
            "scene_override": "traditional Japanese onsen hot spring bath",
        }
        gen_res_top = _generate_structured(
            sampler,
            self.assembler,
            inputs_top_level,
            rng=Random(42),
            effective_seed=42,
        )
        weights_top = dict(gen_res_top.context_profile.weights)
        self.assertEqual(weights_top.get("school", 0.0), 0.0)
        self.assertAlmostEqual(weights_top.get("onsen_bath", 0.0), 1.0, places=3)

    # ─────────────────────────────────────────────────────────────
    # 13. 预算管理外部核算与优先保障 (审核整改 5)
    # ─────────────────────────────────────────────────────────────

    def test_18_word_budget_external_preaccounting_and_priority(self):
        """[Review-5] 预算处理：外部总词数超 250 词时报错；外部未超限时优先完整保留外部，系统让位截断。"""
        # 1. 三段各 100 词的外部内容，总计 300 词 > 250 词：必须报错 Fail-Closed，绝不静默丢弃
        section1 = " ".join([f"exttag{i}" for i in range(100)])
        section2 = " ".join([f"exttag{i}" for i in range(100, 200)])
        section3 = " ".join([f"exttag{i}" for i in range(200, 300)])

        with self.assertRaises(PromptValidationError) as cm:
            self.assembler.assemble_slots(
                {},
                rng=Random(42),
                external_overrides={
                    "clothing": section1,
                    "scene_theme": section2,
                    "lighting": section3,
                },
            )
        self.assertIn("exceed maximum allowed budget of 250 words", str(cm.exception))

        # 2. 系统内容 100 词 + 外部内容 180 词 (总计 280 词)：
        # 外部内容 180 词 <= 250 词，外部全部保证保留，系统内容让位截断
        sys_tags = [PromptFragment(text=f"systag{i}", source_slot="scene_theme", order=i) for i in range(100)]
        ext_section = " ".join([f"keep_ext_word_{i}" for i in range(180)])

        res_priority = self.assembler.assemble_slots(
            {"scene_theme": sys_tags},
            rng=Random(42),
            external_overrides={"clothing": ext_section},
        )
        self.assertLessEqual(len(res_priority.prompt.split()), 250)
        # 验证 180 词外部内容 100% 完整保留在最终 prompt 中
        for i in range(180):
            self.assertIn(f"keep_ext_word_{i}", res_priority.prompt)
        # 系统内容被截断记录在 budget_filtered_atoms
        self.assertTrue(len(res_priority.budget_filtered_atoms) > 0)

    # ─────────────────────────────────────────────────────────────
    # 14. 预设浏览器真实 entry_point 与审计溯源
    # ─────────────────────────────────────────────────────────────

    def test_19_preset_browser_entry_point_and_provenance(self):
        """验证外部覆盖在预设浏览器入口下真实保留 preset_browser 来源与 1.0 审计规范。"""
        from lib.sampler import DataSampler
        sampler = DataSampler(self.data_dir)
        preset_c01 = sampler.get_preset("C01", Random(42))

        res = self.assembler.assemble_preset(
            preset_c01,
            style_recipe=None,
            quality_tier="高清写真 (High)",
            rng=Random(42),
            entry_point="preset_browser",
            external_overrides={"clothing": "black latex catsuit"},
        )
        clothing_atoms = [a for a in res.accepted_atoms if a.source_slot == "clothing"]
        self.assertTrue(len(clothing_atoms) >= 1)
        for a in clothing_atoms:
            self.assertEqual(a.origin.entry_point, "preset_browser")
            self.assertEqual(a.origin.mode, "override")
            self.assertEqual(a.provenance.source_mode, "override")


if __name__ == "__main__":
    unittest.main()
