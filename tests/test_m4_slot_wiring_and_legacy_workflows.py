"""
test_m4_slot_wiring_and_legacy_workflows.py — M4 里程碑验收测试套件

覆盖四大核心目标：
1. 四个节点的 20 槽位外部输入接线 (INPUT_TYPES 暴露完整性与类型定义)；
2. 20 个规范槽位与辅助槽位的外部输入覆盖端到端正确性 (Generator, PresetBrowser, Diagnostics, Combiner)；
3. 真实预设在外部覆盖下的端到端消解与保留逻辑 (如 C09 voyeur angle 清除、H03 单手动作退让)；
4. 历史工作流 100% 零回退兼容与 Fail-Closed 未知/冲突槽位校验。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from lib.errors import PromptValidationError
from lib.slot_contract import ALLOWED_SLOTS, AUXILIARY_SLOT_ORDER, SLOT_ORDER
from nodes import (
    IYKYKCustomSlotCombiner,
    IYKYKPresetBrowser,
    IYKYKPromptDiagnostics,
    IYKYKPromptGenerator,
    NODE_CLASS_MAPPINGS,
    SLOT_OVERRIDE_OPTIONAL_INPUTS,
)


class TestM4SlotWiringAndLegacyWorkflows(unittest.TestCase):
    def setUp(self):
        self.generator = IYKYKPromptGenerator()
        self.browser = IYKYKPresetBrowser()
        self.combiner = IYKYKCustomSlotCombiner()
        self.diagnostics = IYKYKPromptDiagnostics()
        self.all_20_slots = tuple(SLOT_ORDER) + tuple(AUXILIARY_SLOT_ORDER)

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 1: 四个节点的 20 槽位外部端口暴露完整性
    # ═══════════════════════════════════════════════════════════════════════

    def test_01_all_four_nodes_expose_20_override_ports(self):
        """断言四大节点均在 optional 中暴露完整的 20 槽位外部覆盖端口。"""
        self.assertEqual(len(self.all_20_slots), 20)

        for node_cls in (
            IYKYKPromptGenerator,
            IYKYKPresetBrowser,
            IYKYKCustomSlotCombiner,
            IYKYKPromptDiagnostics,
        ):
            input_types = node_cls.INPUT_TYPES()
            self.assertIn("optional", input_types, f"{node_cls.__name__} missing 'optional'")
            optional = input_types["optional"]

            # 断言全部 20 个 slot_override 端口均存在且配置规范
            for slot in self.all_20_slots:
                port_name = f"{slot}_override"
                self.assertIn(
                    port_name,
                    optional,
                    f"Port '{port_name}' missing in {node_cls.__name__}.INPUT_TYPES()['optional']",
                )
                port_type, port_cfg = optional[port_name]
                self.assertEqual(port_type, "STRING")
                self.assertTrue(port_cfg.get("forceInput"), f"{port_name} should have forceInput=True")
                self.assertTrue(port_cfg.get("multiline"), f"{port_name} should be multiline")
                self.assertEqual(port_cfg.get("default"), "", f"{port_name} default should be empty string")
                self.assertIn("tooltip", port_cfg, f"{port_name} should have informative tooltip")

    def test_02_custom_slot_combiner_port_contracts(self):
        """断言 IYKYKCustomSlotCombiner 严格保持历史 15 端口并补齐 5 大规范正交端口。"""
        combiner_inputs = IYKYKCustomSlotCombiner.INPUT_TYPES()["optional"]

        # 1. 历史 15 端口及其默认值完全保持
        legacy_15 = (
            ("场景主题", ""),
            ("景别视角", ""),
            ("裸露状态", ""),
            ("服装款式", ""),
            ("光影氛围", ""),
            ("姿势动作", ""),
            ("表情眼神", ""),
            ("风格胶片", ""),
            ("妆容发型", ""),
            ("微瑕细节", ""),
            ("纹身标记", ""),
            ("道具物件", ""),
            ("角色体液", ""),
            ("画质修饰", "best quality, masterpiece"),
            ("自定义追加", ""),
        )
        for port, expected_default in legacy_15:
            self.assertIn(port, combiner_inputs, f"Legacy port '{port}' missing in combiner")
            _, cfg = combiner_inputs[port]
            self.assertEqual(
                cfg.get("default"),
                expected_default,
                f"Legacy port '{port}' default value changed!",
            )

        # 2. 补齐 5 大规范正交中文端口
        new_5 = ("拍摄视角", "发型发色", "饰品头饰", "角色设定", "风格配方")
        for port in new_5:
            self.assertIn(port, combiner_inputs, f"New canonical port '{port}' missing in combiner")
            _, cfg = combiner_inputs[port]
            self.assertEqual(cfg.get("default"), "")

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 2: IYKYKPromptGenerator 20 槽位外部输入接线与端到端覆盖
    # ═══════════════════════════════════════════════════════════════════════

    def test_03_generator_empty_override_defaults_to_internal_sampling(self):
        """断言生成器在外部覆盖端口为空字符串或未连接时，100% 保持内部采样基线不变。"""
        # 1. 无 kwargs 调用
        baseline_res = self.generator.generate_structured(prompt_seed=42)

        # 2. 传入全量 20 槽位空字符串（模拟 ComfyUI 默认连线或空输入）
        empty_kwargs = {f"{slot}_override": "" for slot in self.all_20_slots}
        override_empty_res = self.generator.generate_structured(prompt_seed=42, **empty_kwargs)

        self.assertEqual(baseline_res.positive, override_empty_res.positive)
        self.assertEqual(baseline_res.negative, override_empty_res.negative)
        self.assertEqual(baseline_res.effective_seed, override_empty_res.effective_seed)

    def test_04_generator_20_slots_individual_external_override(self):
        """断言生成器对 20 个槽位中的每一个，均能通过外部输入完全重写目标槽位。"""
        slot_test_payloads: Dict[str, str] = {
            "scene_theme": "ancient shinto shrine in mist",
            "shot_type": "extreme close-up shot",
            "camera_angle": "dramatic low angle worm view",
            "character": "cyberpunk detective with trenchcoat",
            "nudity": "completely nude, fully exposed",
            "clothing": "crimson silk yukata with floral embroidery",
            "lighting": "neon cyberpunk lighting with blue rim light",
            "pose": "kneeling on tatami mat, hands clasped",
            "expression": "seductive smirk, looking away",
            "makeup": "bold red lipstick, winged eyeliner",
            "hairstyle": "twin braids, platinum blonde hair",
            "jewelry": "silver choker necklace",
            "imperfections": "delicate beauty mark under eye",
            "tattoo": "dragon tattoo wrapping around torso",
            "props": "holding antique brass compass",
            "liquids": "sweat dripping down collarbone",
            "film": "kodak portra 400 grain, vintage warm tone",
            "quality": "masterpiece, ultra detailed 8k photography",
            "style_recipe": "vaporwave synthwave retro aesthetic",
            "custom": "trending on artstation, masterpiece award winner",
        }

        for slot, payload in slot_test_payloads.items():
            port_name = f"{slot}_override"
            res = self.generator.generate_structured(
                prompt_seed=1001,
                **{port_name: payload},
            )

            # 验证 payload 包含的核心词汇存在于输出中
            first_tag = payload.split(",")[0].strip()
            self.assertIn(
                first_tag.lower(),
                res.positive.lower(),
                f"Payload tag '{first_tag}' for slot '{slot}' not found in positive prompt!",
            )

            # 验证对应原子来源被正确标记为 override
            found_origin = False
            for atom in res.atoms:
                if atom.origin and atom.origin.mode in ("override", "custom"):
                    found_origin = True
                    break
            self.assertTrue(found_origin, f"No override/custom origin atom found for slot '{slot}'")

    def test_05_generator_multi_slot_simultaneous_override(self):
        """断言生成器在同时传入多槽位覆盖时，能精准组合各外部槽位且不干扰未覆盖槽位。"""
        overrides = {
            "scene_theme_override": "abandoned subway station",
            "clothing_override": "black leather trench coat",
            "props_override": "holding glowing flashlight",
            "camera_angle_override": "high angle looking down",
        }
        res = self.generator.generate_structured(prompt_seed=777, **overrides)

        self.assertIn("abandoned subway station", res.positive)
        self.assertIn("black leather trench coat", res.positive)
        self.assertIn("holding glowing flashlight", res.positive)
        self.assertIn("high angle looking down", res.positive)

        # 检查上下文画像是否由外部场景自洽驱动
        self.assertIsNotNone(res.context_profile)

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 3: IYKYKPresetBrowser 20 槽位外部输入接线与端到端覆盖
    # ═══════════════════════════════════════════════════════════════════════

    def test_06_preset_browser_legacy_compatibility_without_overrides(self):
        """断言模板浏览器在不提供覆盖或提供空覆盖时，与既有行为 100% 保持严格一致。"""
        # 1. 传统无 kwargs 调用
        baseline = self.browser.browse("C01", "无 (None)", "高清写真 (High)", prompt_seed=123)
        self.assertEqual(len(baseline), 3)
        self.assertTrue(baseline[0], "Positive prompt should not be empty")

        # 2. 空覆盖连线调用
        empty_kwargs = {f"{slot}_override": "" for slot in self.all_20_slots}
        with_empty = self.browser.browse(
            "C01", "无 (None)", "高清写真 (High)", prompt_seed=123, **empty_kwargs
        )
        self.assertEqual(baseline[0], with_empty[0])
        self.assertEqual(baseline[1], with_empty[1])
        self.assertEqual(baseline[2], with_empty[2])

    def test_07_preset_browser_c09_camera_angle_override_and_other_slots_preserved(self):
        """断言预设 C09 外部覆盖 camera_angle='low angle' 后，voyeur angle 被彻底清除，其余预设槽位完好保留。"""
        # 基准 C09 含有 voyeur angle
        baseline_res = self.browser.browse_structured(
            "C09", "无 (None)", "高清写真 (High)", prompt_seed=42
        )
        self.assertIn("voyeur angle", baseline_res.positive.lower())

        # 覆盖 camera_angle
        override_res = self.browser.browse_structured(
            "C09",
            "无 (None)",
            "高清写真 (High)",
            prompt_seed=42,
            camera_angle_override="low angle",
        )

        # 1. 旧 voyeur angle 被彻底清除
        self.assertNotIn("voyeur angle", override_res.positive.lower())
        # 2. 新 low angle 成功注入
        self.assertIn("low angle", override_res.positive.lower())
        # 3. 其它槽位（如服装/姿势/场景）完好保留
        self.assertTrue(len(override_res.atoms) > 15, "Non-overridden preset slots must be retained")

    def test_08_preset_browser_h03_props_override_yields_system_pose(self):
        """断言预设 H03 外接 2 件单手道具时，原有占用动作自动退让，两件外部道具成功保留。"""
        res = self.browser.browse_structured(
            "H03",
            "无 (None)",
            "高清写真 (High)",
            prompt_seed=42,
            props_override="holding smartphone, holding coffee cup",
        )

        # 外部两件道具均保留
        self.assertIn("holding smartphone", res.positive.lower())
        self.assertIn("holding coffee cup", res.positive.lower())

        # H03 既有的 other hand tracing inner thigh 因手部超载退让
        self.assertNotIn("tracing inner thigh", res.positive.lower())
        self.assertIn("pose_hand_occupation", res.rules_applied)

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 4: IYKYKCustomSlotCombiner 20 槽位端到端拼装
    # ═══════════════════════════════════════════════════════════════════════

    def test_09_custom_combiner_all_20_slots_combined(self):
        """断言自定义拼装器可同时接受 20 个正交槽位输入并正确消解拼装。"""
        kwargs = {
            "场景主题": "japanese onsen bathhouse",
            "景别视角": "medium shot",
            "拍摄视角": "low angle perspective",
            "角色设定": "secret agent",
            "裸露状态": "topless, bare breasts",
            "服装款式": "silk robe slightly parted",
            "光影氛围": "steamy warm lanterns",
            "姿势动作": "leaning against wooden ledge",
            "表情眼神": "soft relaxed gaze",
            "妆容发型": "natural bare makeup",
            "发型发色": "wet long black hair",
            "饰品头饰": "jade hairpin",
            "微瑕细节": "skin pores, flushed cheeks",
            "纹身标记": "small lotus flower tattoo on shoulder",
            "道具物件": "holding wooden sake cup",
            "角色体液": "water droplets on skin",
            "风格胶片": "fujifilm superia 400",
            "画质修饰": "masterpiece, ultra-detailed 8k",
            "风格配方": "cinematic aesthetic",
            "自定义追加": "award winning photography",
        }

        res = self.combiner.combine_structured(prompt_seed=123, **kwargs)
        self.assertEqual(res.description, "已成功拼装 20 个自定义槽位")
        self.assertTrue(len(res.atoms) >= 20)

        # 抽样断言各槽位关键标记
        for val in ("onsen", "medium shot", "secret agent", "silk robe", "lanterns", "lotus"):
            self.assertIn(val, res.positive.lower())

    def test_10_custom_combiner_legacy_15_ports_backward_compatible(self):
        """断言自定义拼装器调用历史 15 端口能产生与历史完全一致的输出。"""
        legacy_kwargs = {
            "场景主题": "cozy library interior",
            "景别视角": "cowboy shot",
            "服装款式": "oversized knitted wool sweater",
            "画质修饰": "best quality, masterpiece",
        }
        res = self.combiner.combine_structured(prompt_seed=42, **legacy_kwargs)
        self.assertEqual(res.description, "已成功拼装 4 个自定义槽位")
        self.assertIn("cozy library interior", res.positive)
        self.assertIn("oversized knitted wool sweater", res.positive)
        self.assertIn("best quality", res.positive)

    def test_11_custom_combiner_quality_override_precedence(self):
        """断言当外接 quality_override 且画质修饰为默认值时，质量覆盖优先取代默认画质。"""
        res = self.combiner.combine_structured(
            prompt_seed=42,
            场景主题="night city street",
            quality_override="CCTV footage, grainy low resolution",
        )
        self.assertIn("cctv footage", res.positive.lower())
        self.assertNotIn("best quality", res.positive.lower())

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 5: IYKYKPromptDiagnostics 审计轨迹与模式验证
    # ═══════════════════════════════════════════════════════════════════════

    def test_12_diagnostics_records_override_selections_and_schema(self):
        """断言诊断节点不仅输出正确提示词，还在 audit_json 的 selections 中精准标记 mode='override'。"""
        res, audit_json_str = self.diagnostics.diagnose_structured(
            预设模板="C01",
            prompt_seed=999,
            camera_angle_override="eye level",
            clothing_override="red summer dress",
        )

        audit_data = json.loads(audit_json_str)
        self.assertIn("selections", audit_data)
        self.assertIn("decisions", audit_data)
        self.assertIn("counts", audit_data)

        # 验证存在 mode="override" 的选择项
        override_selections = [
            s for s in audit_data["selections"]
            if s.get("mode") == "override"
        ]
        self.assertTrue(
            len(override_selections) >= 2,
            "Audit JSON must include selections with mode='override' for camera_angle and clothing!",
        )

        # 验证选择器名称
        selectors = {s.get("selector") for s in override_selections}
        self.assertIn("camera_angle", selectors)
        self.assertIn("clothing", selectors)

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 6: Fail-Closed 安全性校验与冲突防范
    # ═══════════════════════════════════════════════════════════════════════

    def test_13_fail_closed_on_unknown_non_empty_override(self):
        """断言传入未知且非空的覆盖端口时，严格 Fail-Closed 抛出 PromptValidationError。"""
        with self.assertRaises(PromptValidationError):
            self.generator.generate(unknown_slot_override="unauthorized payload")

        with self.assertRaises(PromptValidationError):
            self.browser.browse("C01", "无 (None)", "高清写真 (High)", fictitious_slot="invalid")

        with self.assertRaises(PromptValidationError):
            self.combiner.combine(invalid_custom_slot="hacked")

    def test_14_fail_closed_on_conflicting_slot_inputs(self):
        """断言同时传入归一化到同一规范槽位的不同非空键时，抛出 PromptValidationError。"""
        # scene 与 scene_theme 冲突
        with self.assertRaises(PromptValidationError):
            self.generator.generate(
                scene="tokyo street",
                scene_theme_override="kyoto garden",
            )

        # combiner 中景别构图与景别视角冲突
        with self.assertRaises(PromptValidationError):
            self.combiner.combine(
                景别构图="close up",
                景别视角="wide shot",
            )

    def test_15_fail_closed_on_excessive_external_word_budget(self):
        """断言外部覆盖自身超过 250 词时，严格抛出 PromptValidationError。"""
        massive_text = "word " * 260
        with self.assertRaises(PromptValidationError):
            self.generator.generate(clothing_override=massive_text)


    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 7: 自定义拼装器覆盖修复与四反例闭环 (M4 阻断消除)
    # ═══════════════════════════════════════════════════════════════════════

    def test_16_combiner_base_and_override_coexistence(self):
        """反例 1: 旧基础输入与 override 输入并存时，外部覆盖替换基础槽位且绝不报重复别名冲突。"""
        res = self.combiner.combine_structured(
            prompt_seed=42,
            服装款式="red dress",
            clothing_override="blue suit",
        )
        # 1. 外部值替换旧服装
        self.assertIn("blue suit", res.positive.lower())
        self.assertNotIn("red dress", res.positive.lower())

        # 2. 外部值原子来源严格标记为 mode="override"
        override_atoms = [
            a for a in res.atoms
            if a.origin and a.origin.mode == "override" and a.source_slot == "clothing"
        ]
        self.assertTrue(len(override_atoms) > 0, "clothing atom must have origin.mode='override'")
        self.assertIn("blue suit", [a.text.lower() for a in override_atoms])

        # 3. 槽位数统计正确 (clothing 仅算 1 个激活槽位)
        self.assertEqual(res.description, "已成功拼装 1 个自定义槽位")

    def test_17_combiner_tattoo_override_protected_from_dermal_fusion(self):
        """反例 2: tattoo_override 属于外部输入，享受完整内容保护，绝不追加 Rule 17 增强词。"""
        res = self.combiner.combine_structured(
            prompt_seed=42,
            tattoo_override="small butterfly tattoo",
        )
        self.assertIn("small butterfly tattoo", res.positive.lower())
        self.assertNotIn("pores visible through ink", res.positive.lower())
        # 验证原子标记为 mode="override"
        tattoo_atoms = [a for a in res.atoms if a.source_slot == "tattoo"]
        self.assertTrue(all(a.origin and a.origin.mode == "override" for a in tattoo_atoms))

    def test_18_combiner_external_pose_and_external_props_hard_conflict(self):
        """反例 3: 外部双手束缚姿势与外部手持道具并存时，明确抛出 PromptValidationError 报告硬冲突。"""
        with self.assertRaises(PromptValidationError) as ctx:
            self.combiner.combine_structured(
                prompt_seed=42,
                pose_override="hands tied behind back",
                props_override="holding camera",
            )
        err_msg = str(ctx.exception)
        self.assertIn("External override conflict", err_msg)
        self.assertIn("pose_hand_occupation", err_msg)

    def test_19_combiner_excessive_external_word_budget_fail_closed(self):
        """反例 4: custom_override 自身包含 260 词时，严格 Fail-Closed 抛出 PromptValidationError。"""
        massive_text = "tag " * 260
        with self.assertRaises(PromptValidationError) as ctx:
            self.combiner.combine_structured(
                prompt_seed=42,
                custom_override=massive_text,
            )
        self.assertIn("External overrides exceed maximum allowed budget of 250 words", str(ctx.exception))

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 8: 四节点跨节点覆盖契约全量断言 (5 核心支柱)
    # ═══════════════════════════════════════════════════════════════════════

    def test_20_all_four_nodes_adhere_to_override_contract_pillars(self):
        """断言四大节点均严格落实五大覆盖契约支柱：
        1. 基础与覆盖共存替换；
        2. 空白外部覆盖（空字符串、空白字符或未连接端口）完全回退内部/预设基线（显式 None 见 test_24 按 P2-1 抛错）；
        3. 外部纹身内容免受增强注入；
        4. 外部姿势与道具超容量显式报硬冲突；
        5. 外部超 250 词自身超限 Fail-Closed 抛错；
        6. 所有生效的外部覆盖原子严格携带 mode='override'。
        """
        massive_text = "word " * 260

        # --- 1. IYKYKPromptGenerator ---
        # 1.1 共存替换
        res_g = self.generator.generate_structured(
            prompt_seed=123,
            服装款式="旗袍 (Qipao/Cheongsam)",
            clothing_override="blue denim overalls",
        )
        self.assertIn("blue denim overalls", res_g.positive)
        self.assertNotIn("qipao", res_g.positive.lower())
        overrides_g = [a for a in res_g.atoms if a.origin and a.origin.mode == "override"]
        self.assertTrue(len(overrides_g) > 0)
        self.assertIn("blue denim overalls", [a.text for a in overrides_g])

        # 1.2 空白回退
        res_g_blank = self.generator.generate_structured(
            prompt_seed=123,
            服装款式="旗袍 (Qipao/Cheongsam)",
            clothing_override="",
        )
        res_g_none = self.generator.generate_structured(
            prompt_seed=123,
            服装款式="旗袍 (Qipao/Cheongsam)",
        )
        self.assertEqual(res_g_blank.positive, res_g_none.positive)

        # 1.3 纹身免受 Rule 17 增强
        res_g_tat = self.generator.generate_structured(
            prompt_seed=123,
            tattoo_override="dragon tattoo on arm",
        )
        self.assertIn("dragon tattoo on arm", res_g_tat.positive)
        self.assertNotIn("pores visible through ink", res_g_tat.positive)

        # 1.4 外部硬冲突
        with self.assertRaises(PromptValidationError):
            self.generator.generate_structured(
                prompt_seed=123,
                pose_override="hands tied behind back",
                props_override="holding camera",
            )

        # 1.5 250 词超限
        with self.assertRaises(PromptValidationError):
            self.generator.generate_structured(
                prompt_seed=123,
                custom_override=massive_text,
            )

        # --- 2. IYKYKPresetBrowser ---
        # 2.1 共存替换
        res_b = self.browser.browse_structured(
            "C01",
            "无 (None)",
            "高清写真 (High)",
            prompt_seed=123,
            clothing_override="futuristic silver spacesuit",
        )
        self.assertIn("futuristic silver spacesuit", res_b.positive)
        overrides_b = [a for a in res_b.atoms if a.origin and a.origin.mode == "override"]
        self.assertTrue(len(overrides_b) > 0)

        # 2.2 空白回退
        res_b_blank = self.browser.browse_structured(
            "C01", "无 (None)", "高清写真 (High)", prompt_seed=123, clothing_override=""
        )
        res_b_none = self.browser.browse_structured(
            "C01", "无 (None)", "高清写真 (High)", prompt_seed=123
        )
        self.assertEqual(res_b_blank.positive, res_b_none.positive)

        # 2.3 纹身免增强
        res_b_tat = self.browser.browse_structured(
            "C01", "无 (None)", "高清写真 (High)", prompt_seed=123, tattoo_override="geometric pattern tattoo"
        )
        self.assertIn("geometric pattern tattoo", res_b_tat.positive)
        self.assertNotIn("pores visible through ink", res_b_tat.positive)

        # 2.4 外部硬冲突
        with self.assertRaises(PromptValidationError):
            self.browser.browse_structured(
                "C01",
                "无 (None)",
                "高清写真 (High)",
                prompt_seed=123,
                pose_override="hands tied behind back",
                props_override="holding camera",
            )

        # 2.5 250 词超限
        with self.assertRaises(PromptValidationError):
            self.browser.browse_structured(
                "C01", "无 (None)", "高清写真 (High)", prompt_seed=123, custom_override=massive_text
            )

        # --- 3. IYKYKPromptDiagnostics ---
        # 3.1 诊断节点审计记录 mode='override'
        res_d, audit_d_str = self.diagnostics.diagnose_structured(
            prompt_seed=123,
            服装款式="旗袍 (Qipao/Cheongsam)",
            clothing_override="crimson velvet gown",
        )
        self.assertIn("crimson velvet gown", res_d.positive)
        audit_d = json.loads(audit_d_str)
        override_sels = [s for s in audit_d["selections"] if s.get("mode") == "override"]
        self.assertTrue(len(override_sels) > 0)
        self.assertIn("clothing", {s.get("selector") for s in override_sels})

        # 3.2 诊断节点外部硬冲突抛错
        with self.assertRaises(PromptValidationError):
            self.diagnostics.diagnose_structured(
                prompt_seed=123,
                pose_override="hands tied behind back",
                props_override="holding camera",
            )

        # 3.3 诊断节点 250 词超限抛错
        with self.assertRaises(PromptValidationError):
            self.diagnostics.diagnose_structured(
                prompt_seed=123,
                custom_override=massive_text,
            )

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 9: 四节点中文/英文别名覆盖一致性 (闭环拼装器别名忽略缺陷)
    # ═══════════════════════════════════════════════════════════════════════

    def test_23_four_nodes_chinese_and_english_alias_override_consistency(self):
        """断言四大节点对外部覆盖字典中的中文别名与英文规范名均享受 100% 恒等覆盖语义。"""
        # 1. 针对审核反例：拼装器中文字典覆盖必须生效且彻底替换基础输入
        combiner_res = self.combiner.combine_structured(
            prompt_seed=0,
            服装款式="red dress",
            external_overrides={"服装款式": "blue suit"},
        )
        self.assertIn("blue suit", combiner_res.positive.lower())
        self.assertNotIn("red dress", combiner_res.positive.lower())
        override_atoms = [a for a in combiner_res.atoms if a.source_slot == "clothing"]
        self.assertTrue(all(a.origin and a.origin.mode == "override" for a in override_atoms))
        self.assertEqual(combiner_res.description, "已成功拼装 1 个自定义槽位")

        # 2. 四大节点在 4 种覆盖路径下的 100% 等价性矩阵核验：
        #    路径 A: external_overrides={"服装款式": "blue suit"} (中文别名)
        #    路径 B: external_overrides={"clothing": "blue suit"} (英文规范名)
        #    路径 C: clothing_override="blue suit" (英文直接端口)
        #    路径 D: 服装款式_override="blue suit" (中文别名端口)

        # 2.1 IYKYKCustomSlotCombiner
        cA = self.combiner.combine_structured(prompt_seed=42, 服装款式="red dress", external_overrides={"服装款式": "blue suit"})
        cB = self.combiner.combine_structured(prompt_seed=42, 服装款式="red dress", external_overrides={"clothing": "blue suit"})
        cC = self.combiner.combine_structured(prompt_seed=42, 服装款式="red dress", clothing_override="blue suit")
        cD = self.combiner.combine_structured(prompt_seed=42, 服装款式="red dress", 服装款式_override="blue suit")
        self.assertEqual(cA.positive, cB.positive)
        self.assertEqual(cA.positive, cC.positive)
        self.assertEqual(cA.positive, cD.positive)
        self.assertIn("blue suit", cA.positive)
        self.assertNotIn("red dress", cA.positive)

        # 2.2 IYKYKPromptGenerator
        gA = self.generator.generate_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", external_overrides={"服装款式": "blue suit"})
        gB = self.generator.generate_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", external_overrides={"clothing": "blue suit"})
        gC = self.generator.generate_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", clothing_override="blue suit")
        gD = self.generator.generate_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", 服装款式_override="blue suit")
        self.assertEqual(gA.positive, gB.positive)
        self.assertEqual(gA.positive, gC.positive)
        self.assertEqual(gA.positive, gD.positive)
        self.assertIn("blue suit", gA.positive)
        self.assertNotIn("qipao", gA.positive.lower())

        # 2.3 IYKYKPresetBrowser
        bA = self.browser.browse_structured("C01", "无 (None)", "高清写真 (High)", prompt_seed=42, external_overrides={"服装款式": "blue suit"})
        bB = self.browser.browse_structured("C01", "无 (None)", "高清写真 (High)", prompt_seed=42, external_overrides={"clothing": "blue suit"})
        bC = self.browser.browse_structured("C01", "无 (None)", "高清写真 (High)", prompt_seed=42, clothing_override="blue suit")
        bD = self.browser.browse_structured("C01", "无 (None)", "高清写真 (High)", prompt_seed=42, 服装款式_override="blue suit")
        self.assertEqual(bA.positive, bB.positive)
        self.assertEqual(bA.positive, bC.positive)
        self.assertEqual(bA.positive, bD.positive)
        self.assertIn("blue suit", bA.positive)

        # 2.4 IYKYKPromptDiagnostics
        dA, auditA = self.diagnostics.diagnose_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", external_overrides={"服装款式": "blue suit"})
        dB, _ = self.diagnostics.diagnose_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", external_overrides={"clothing": "blue suit"})
        dC, _ = self.diagnostics.diagnose_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", clothing_override="blue suit")
        dD, _ = self.diagnostics.diagnose_structured(prompt_seed=42, 服装款式="旗袍 (Qipao/Cheongsam)", 服装款式_override="blue suit")
        self.assertEqual(dA.positive, dB.positive)
        self.assertEqual(dA.positive, dC.positive)
        self.assertEqual(dA.positive, dD.positive)
        self.assertIn("blue suit", dA.positive)
        audit_data = json.loads(auditA)
        self.assertTrue(any(s.get("selector") == "clothing" and s.get("mode") == "override" for s in audit_data["selections"]))

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 10: 显式 None 严格 Fail-Closed 安全契约
    # ═══════════════════════════════════════════════════════════════════════

    def test_24_explicit_none_slot_value_fails_closed(self):
        """断言显式传入 None 按 P2-1 契约严格 Fail-Closed 抛出 PromptValidationError。

        注意：空白回退语义严格适用于空字符串、空白字符或省略参数；显式 None 属于非法输入，绝不静默回退。
        """
        # 拼装器覆盖端口显式 None 抛错
        with self.assertRaises(PromptValidationError):
            self.combiner.combine(clothing_override=None)

        with self.assertRaises(PromptValidationError):
            self.combiner.combine(服装款式=None)

        # 拼装器覆盖字典内显式 None 抛错
        with self.assertRaises(PromptValidationError):
            self.combiner.combine_structured(external_overrides={"clothing": None})

        # 主生成器覆盖端口显式 None 抛错
        with self.assertRaises(PromptValidationError):
            self.generator.generate(clothing_override=None)

        # 模板浏览器覆盖端口显式 None 抛错
        with self.assertRaises(PromptValidationError):
            self.browser.browse("C01", "无 (None)", "高清写真 (High)", clothing_override=None)

    # ═══════════════════════════════════════════════════════════════════════
    # 门禁 11: 真实 ComfyUI UI 导出工作流图结构、连线解析与执行模拟
    # ═══════════════════════════════════════════════════════════════════════

    def test_21_comfyui_legacy_exported_workflow_execution(self):
        """断言真实导出的 ComfyUI 历史工作流 (无 override 连线、含历史 widgets_values 数组) 零回退正确解析与执行。

        注：本测试在测试环境中完整模拟 ComfyUI 引擎的 UI 节点反序列化、控件数组按 INPUT_TYPES 映射及节点执行；
        宿主环境（运行中的 ComfyUI 网页前端界面）渲染与交互因无活动前端服务标为未验证。
        """
        fixture_path = Path(__file__).parent / "fixtures" / "comfyui_legacy_workflow.json"
        self.assertTrue(fixture_path.exists(), f"Missing fixture: {fixture_path}")

        workflow_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        executor = _ComfyUIGraphExecutor(workflow_data)

        # 节点 1: 历史 15 槽位拼装器 (id=1)
        res1 = executor.execute_node(1)
        self.assertIsInstance(res1, tuple)
        self.assertEqual(len(res1), 3)
        self.assertIn("cozy library interior", res1[0])
        self.assertIn("cowboy shot", res1[0])
        self.assertIn("oversized knitted wool sweater", res1[0])
        self.assertIn("best quality", res1[0])
        self.assertEqual(res1[2], "已成功拼装 4 个自定义槽位")

        # 节点 2: 历史 3 槽位预设浏览器 (id=2)
        res2 = executor.execute_node(2)
        self.assertIsInstance(res2, tuple)
        self.assertEqual(len(res2), 3)
        self.assertIn("classroom", res2[0])

        # 节点 3: 历史 22 下拉框生成器 (id=3)
        res3 = executor.execute_node(3)
        self.assertIsInstance(res3, tuple)
        self.assertEqual(len(res3), 3)
        self.assertTrue(res3[0])

    def test_22_comfyui_modern_connected_workflow_link_resolution(self):
        """断言带上游文本节点的连线工作流通过 ComfyUI links 机制正确解析至 forceInput 覆盖端口并执行覆盖。

        注：完整解析上游输出槽位 -> 连线 -> 下游 forceInput 覆盖插槽拓扑传递；
        宿主前端界面交互未在本地无头环境中运行，标为未验证。
        """
        fixture_path = Path(__file__).parent / "fixtures" / "comfyui_connected_workflow.json"
        self.assertTrue(fixture_path.exists(), f"Missing fixture: {fixture_path}")

        workflow_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        executor = _ComfyUIGraphExecutor(workflow_data)

        # 执行节点 3 (IYKYKCustomSlotCombiner):
        # 连线 101 来自节点 1 (输出 "blue suit") -> clothing_override
        # 连线 102 来自节点 2 (输出 "dragon tattoo on back") -> tattoo_override
        # 控件值原服装为 "red dress"
        res3 = executor.execute_node(3)
        self.assertIsInstance(res3, tuple)
        positive_prompt = res3[0]

        # 1. 上游连线的 blue suit 正确重写了控件中的 red dress
        self.assertIn("blue suit", positive_prompt)
        self.assertNotIn("red dress", positive_prompt)

        # 2. 上游连线的 dragon tattoo on back 正确注入
        self.assertIn("dragon tattoo on back", positive_prompt)

        # 执行节点 5 (IYKYKPromptGenerator):
        # 连线 103 来自节点 4 (输出 "silk evening dress") -> clothing_override
        # 控件原本选择 "旗袍 (Qipao/Cheongsam)"
        res5 = executor.execute_node(5)
        self.assertIsInstance(res5, tuple)
        gen_positive = res5[0]

        # 上游连线的 silk evening dress 正确重写了控件中的旗袍
        self.assertIn("silk evening dress", gen_positive)
        self.assertNotIn("qipao", gen_positive.lower())

    def test_25_workflow_graph_serialization_and_roundtrip_determinism(self):
        """断言工作流图导出为 JSON、重新从文件载入并解析执行，结果严格保持 100% 确定性。"""
        fixture_path = Path(__file__).parent / "fixtures" / "comfyui_connected_workflow.json"
        original_data = json.loads(fixture_path.read_text(encoding="utf-8"))

        # 1. 首次解析执行
        exec1 = _ComfyUIGraphExecutor(original_data)
        out1 = exec1.execute_node(3)

        # 2. 模拟序列化到字符串再反序列化重载
        serialized_str = json.dumps(original_data, ensure_ascii=False, indent=2)
        reloaded_data = json.loads(serialized_str)
        exec2 = _ComfyUIGraphExecutor(reloaded_data)
        out2 = exec2.execute_node(3)

        # 3. 严格断言输出完全一致
        self.assertEqual(out1[0], out2[0])
        self.assertEqual(out1[1], out2[1])
        self.assertEqual(out1[2], out2[2])


class _ComfyUIGraphExecutor:
    """模拟 ComfyUI 执行引擎对 UI 导出图 (nodes, links) 的连线拓扑解析与执行。"""

    def __init__(self, workflow_data: Dict[str, Any]):
        self.workflow_data = workflow_data
        self.nodes_by_id = {n["id"]: n for n in workflow_data.get("nodes", [])}
        self.links_by_id = {link[0]: link for link in workflow_data.get("links", [])}

    def execute_node(self, node_id: int, execution_cache: Optional[Dict[int, Any]] = None) -> Tuple[Any, ...]:
        if execution_cache is None:
            execution_cache = {}
        if node_id in execution_cache:
            return execution_cache[node_id]

        node_data = self.nodes_by_id[node_id]
        class_type = node_data["type"]

        # 模拟标准上游文本/字符串 Primitive 节点
        if class_type in ("PrimitiveStringNode", "PrimitiveNode", "CLIPTextEncode"):
            val = node_data.get("widgets_values", [""])[0] if node_data.get("widgets_values") else ""
            execution_cache[node_id] = (val,)
            return execution_cache[node_id]

        node_cls = NODE_CLASS_MAPPINGS[class_type]
        input_types = node_cls.INPUT_TYPES()
        required_cfg = input_types.get("required", {})
        optional_cfg = input_types.get("optional", {})

        # 根据 ComfyUI 规范按顺序提取控件键（排除 forceInput=True 的连线专用端口）
        widget_keys = []
        for k, v in required_cfg.items():
            if not (isinstance(v, tuple) and len(v) > 1 and isinstance(v[1], dict) and v[1].get("forceInput")):
                widget_keys.append(k)
        for k, v in optional_cfg.items():
            if not (isinstance(v, tuple) and len(v) > 1 and isinstance(v[1], dict) and v[1].get("forceInput")):
                widget_keys.append(k)

        kwargs: Dict[str, Any] = {}
        widgets_values = node_data.get("widgets_values", [])
        for idx, val in enumerate(widgets_values):
            if idx < len(widget_keys):
                kwargs[widget_keys[idx]] = val

        # 解析连线输入 (上游节点输出 -> 本节点 forceInput 插槽)
        for inp in node_data.get("inputs", []):
            link_id = inp.get("link")
            if link_id is not None and link_id in self.links_by_id:
                link = self.links_by_id[link_id]
                source_node_id = link[1]
                source_slot_idx = link[2]
                source_out = self.execute_node(source_node_id, execution_cache)
                kwargs[inp["name"]] = source_out[source_slot_idx]

        node_instance = node_cls()
        func_name = getattr(node_instance, "FUNCTION")
        func = getattr(node_instance, func_name)
        res = func(**kwargs)
        execution_cache[node_id] = res
        return res


if __name__ == "__main__":
    unittest.main()

