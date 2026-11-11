"""
test_v1_2_1_m3_unified_nudity_and_budget.py — M3 统一裸露约束、跨槽过滤、消解后复检、预算保护与诊断 2.0 验收测试
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from random import Random
from unittest.mock import patch

import jsonschema

import nodes as nodes_mod
from lib import assembler as _assembler
from lib.assembler import PromptAssembler
from lib.conflict_resolver import ConflictResolver
from lib.errors import PromptValidationError
from lib.models import (
    NUDITY_BRANCHES_BY_LEVEL,
    NudityConstraint,
    PromptAtom,
    PromptFragment,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)
from lib.outfit_completion import assess_clothing_completeness
from nodes import (
    IYKYKCustomSlotCombiner,
    IYKYKPresetBrowser,
    IYKYKPromptDiagnostics,
    IYKYKPromptGenerator,
)
from tests.audit_oracle import EXPECTED_TOP_LEVEL_KEYS_V2, validate_audit_json_oracle

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"
SCHEMA_PATH = REPO_DIR / "schemas" / "diagnostics.schema.json"


class TestV121M3UnifiedNudityAndBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = IYKYKPromptGenerator()
        cls.browser = IYKYKPresetBrowser()
        cls.combiner = IYKYKCustomSlotCombiner()
        cls.diagnostics = IYKYKPromptDiagnostics()
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.assembler = PromptAssembler(DATA_DIR)
        cls.schema_doc = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.validator = jsonschema.Draft7Validator(cls.schema_doc)

    # ------------------------------------------------------------------
    # 1. 单一相容分支与跨分支互斥消解
    # ------------------------------------------------------------------

    def test_01_single_coherent_branch_per_level_zero_cross_branch_contradictions(self) -> None:
        """L1..L6 各级每次生成仅选择一个相容分支，严禁在同一结果中拼出相互矛盾的子分支。"""
        lvl_options = [
            ("L1", "L1 包裹暗示 (Fully Clothed / Suggestive)"),
            ("L2", "L2 差分微露 (Partially Exposed)"),
            ("L3", "L3 半裸诱惑 (Half Nude)"),
            ("L4", "L4 重点暴露 (Topless / Bottomless)"),
            ("L5", "L5 极致全裸 (Full Nude)"),
            ("L6", "L6 特写全见 (Explicit Genital Close-up)"),
        ]
        for lvl_code, lvl_ui in lvl_options:
            for seed in (0, 1, 2, 7, 15, 29):
                with self.subTest(level=lvl_code, seed=seed):
                    res = self.generator.generate_structured(
                        裸露等级=lvl_ui,
                        服装款式="随机 (Random)",
                        服装状态="自动联动裸露等级 (Auto Link Nudity)",
                        prompt_seed=seed,
                    )
                    nc = res.nudity_constraint
                    self.assertIsNotNone(nc)
                    self.assertEqual(nc.effective_level, lvl_code)
                    self.assertIn(nc.selected_branch, NUDITY_BRANCHES_BY_LEVEL[lvl_code])

                    nudity_atoms = [a for a in res.atoms if a.source_slot == "nudity"]
                    self.assertGreater(len(nudity_atoms), 0)
                    for a in nudity_atoms:
                        if a.facts and a.facts.nudity_branches:
                            self.assertIn(nc.selected_branch, a.facts.nudity_branches)

                    pos_low = res.positive.lower()
                    if lvl_code == "L3" and nc.selected_branch == "l3_topless_pants_kept":
                        self.assertNotIn("bra pushed up above breasts", pos_low)
                        self.assertNotIn("panties only", pos_low)
                    elif lvl_code == "L4" and nc.selected_branch == "l4_bra_and_panties":
                        self.assertNotIn("topless", pos_low)
                        self.assertNotIn("bare breasts exposed", pos_low)
                        self.assertNotIn("only lace panties remaining", pos_low)
                    elif lvl_code == "L4" and nc.selected_branch == "l4_panties_only":
                        self.assertNotIn("black lace bra", pos_low)
                        self.assertNotIn("micro triangle bra", pos_low)
                        self.assertNotIn("sheer lace cups", pos_low)
                        self.assertNotIn("string halter bikini underwear", pos_low)

    # ------------------------------------------------------------------
    # 2. 跨槽位采样过滤与显式裸露冲突拦截
    # ------------------------------------------------------------------

    def test_02_cross_slot_filtering_and_explicit_conflict_drops(self) -> None:
        """L1 下随机跨槽不会采到成人主题/暴露饰品，显式跨槽裸露元素被拦截并记录 decision。"""
        forbidden_l1_tokens = (
            "raw sex",
            "creampie",
            "bukkake",
            "gangbang",
            "crotch close-up",
            "cum on ",
            "pussy",
            "topless",
        )
        for seed in range(12):
            res = self.generator.generate_structured(
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="自动 (Auto)",
                拍摄视角="自动 (Auto)",
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
                发型发色="随机 (Random)",
                饰品头饰="随机 (Random)",
                纹身标记="随机 (Random)",
                道具物件="随机 (Random)",
                液体效果="随机 (Random)",
                姿势动作="随机 (Random)",
                光影预设="自动 (Auto)",
                prompt_seed=seed,
            )
            pos_low = res.positive.lower()
            for tok in forbidden_l1_tokens:
                self.assertNotIn(tok, pos_low, f"L1 seed={seed} leaked cross-slot token {tok!r}: {res.positive}")

        # 显式跨槽裸露项在 L1 下必须被 ConflictResolver 拦截（且不破坏合规衣服）
        res_exp = self.generator.generate_structured(
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="dress_casual",
            服装状态="整齐穿着 (Neat)",
            发型发色="发丝遮乳/纯欲 (Hair Draped Over Breasts)",
            饰品头饰="性感身体链/乳链 (Body Chain Harness)",
            纹身标记="💃 胸下曼陀罗/胸骨花纹",
            prompt_seed=5,
        )
        dropped_texts = [
            d.before_text.lower()
            for d in res_exp.resolution_report.decisions
            if d.rule_id == "nudity_clothing_conflicts" and d.action == "drop"
        ]
        self.assertGreaterEqual(len(dropped_texts), 3)
        self.assertNotIn("strands partially covering breasts", res_exp.positive.lower())
        self.assertNotIn("cross-torso body jewelry", res_exp.positive.lower())
        self.assertNotIn("underbust mandala tattoo", res_exp.positive.lower())
        self.assertTrue(any(a.source_slot == "clothing" for a in res_exp.atoms))

    # ------------------------------------------------------------------
    # 3. 无误报白名单与负面提示词隔离
    # ------------------------------------------------------------------

    def test_03_non_false_positive_whitelists_and_negative_isolation(self) -> None:
        """nude lip / bare feet / bare bulb / ankle_bracelet 与负面提示词中的 nude 绝不触发 L1 误杀。"""
        l1_c = NudityConstraint(
            requested_level="L1",
            effective_level="L1",
            source="ui_explicit",
            selected_branch="l1_full_clothed",
        )
        atoms = [
            PromptAtom(
                atom_id="w_nudity",
                text="modest outfit",
                span_type=SpanType.PLAIN,
                source_slot="nudity",
                source_item_id="nudity_l1",
                tag_order=0,
                span_order=0,
                provenance=TagProvenance(item_id="nudity_l1", kind="nudity", parent_ids=("nudity_l1",)),
                origin=SelectionOrigin(mode="explicit", selector="nudity", selected_id="nudity_l1"),
                facts=SemanticFacts(allowed_nudity_levels=("L1",), exposed_regions=("none",)),
            ),
            PromptAtom(
                atom_id="w_top",
                text="white cotton button-down shirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="shirts_blouses",
                tag_order=1,
                span_order=0,
                provenance=TagProvenance(item_id="shirts_blouses", kind="base_clothing", parent_ids=("shirts_blouses",)),
                origin=SelectionOrigin(mode="explicit", selector="clothing", selected_id="shirts_blouses"),
                facts=SemanticFacts(garment_topologies=("top",), piece_role="top", covered_regions=("upper_torso",), allowed_nudity_levels=("L1", "L2", "L3")),
            ),
            PromptAtom(
                atom_id="w_lip",
                text="natural nude matte lipstick",
                span_type=SpanType.PLAIN,
                source_slot="makeup",
                tag_order=2,
                span_order=0,
                provenance=TagProvenance(item_id="custom_makeup", kind="custom", parent_ids=("custom_makeup",)),
                origin=SelectionOrigin(mode="custom", selector="makeup", raw_value="natural nude matte lipstick"),
                facts=SemanticFacts(),
            ),
            PromptAtom(
                atom_id="w_bulb",
                text="warm bare bulb overhead",
                span_type=SpanType.PLAIN,
                source_slot="lighting",
                tag_order=3,
                span_order=0,
                provenance=TagProvenance(item_id="custom_lighting", kind="custom", parent_ids=("custom_lighting",)),
                origin=SelectionOrigin(mode="custom", selector="lighting", raw_value="warm bare bulb overhead"),
                facts=SemanticFacts(),
            ),
            PromptAtom(
                atom_id="w_anklet",
                text="delicate gold ankle bracelet",
                span_type=SpanType.PLAIN,
                source_slot="jewelry",
                source_item_id="ankle_bracelet",
                tag_order=4,
                span_order=0,
                provenance=TagProvenance(item_id="ankle_bracelet", kind="jewelry", parent_ids=("ankle_bracelet",)),
                origin=SelectionOrigin(mode="explicit", selector="jewelry", selected_id="ankle_bracelet"),
                facts=SemanticFacts(allowed_nudity_levels=("L1", "L2", "L3", "L4", "L5", "L6"), exposed_regions=("none",)),
            ),
        ]
        resolved, _, report = self.resolver.resolve_atoms_with_full_report(atoms, Random(42), nudity_constraint=l1_c)
        self.assertEqual([a.atom_id for a in resolved], [a.atom_id for a in atoms])
        self.assertEqual(len(report.decisions), 0)

        # 自定义拼装器传入 L1 + 正常上衣 + custom 含裸露黑盒/白名单短语与无裸露默认负面词，均无误杀
        gen_l1 = self.combiner.combine_structured(
            prompt_seed=7,
            nudity="L1",
            clothing="knit_sweater",
        )
        self.assertIn("sweater", gen_l1.positive.lower())
        self.assertEqual(len([d for d in gen_l1.resolution_report.decisions if d.rule_id == "nudity_clothing_conflicts"]), 0)

    # ------------------------------------------------------------------
    # 4. 预设与配方联动（生成器 + 浏览器 + preset_native + fail-closed）
    # ------------------------------------------------------------------

    def test_04_preset_and_recipe_unified_nudity_and_fail_closed(self) -> None:
        """预设与配方受显式等级/外部覆盖约束并标示已调整；随机裸露保持 preset_native；全空不兼容 preset 报错。"""
        # 1. 固定预设 C01 + 显式配方 + L1 vs L5 严格分化
        res_c01_l1 = self.generator.generate_structured(
            预设模板="C01 (教室后排露出)",
            风格配方="AV封面",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            prompt_seed=42,
        )
        res_c01_l5 = self.generator.generate_structured(
            预设模板="C01 (教室后排露出)",
            风格配方="AV封面",
            裸露等级="L5 极致全裸 (Full Nude)",
            prompt_seed=42,
        )
        self.assertNotEqual(res_c01_l1.positive, res_c01_l5.positive)
        self.assertEqual(res_c01_l1.nudity_constraint.effective_level, "L1")
        self.assertEqual(res_c01_l5.nudity_constraint.effective_level, "L5")
        self.assertTrue(res_c01_l1.preset_adjusted)
        self.assertTrue(res_c01_l5.preset_adjusted)
        self.assertIn("已调整", res_c01_l1.description)
        for tok in ("one breast exposed", "erect nipple", "pussy", "no panties"):
            self.assertNotIn(tok, res_c01_l1.positive.lower())
        self.assertNotIn("school uniform", res_c01_l5.positive.lower())

        # 2. 随机预设 + UI 裸露=Random -> preset_native 不改写
        res_native = self.generator.generate_structured(
            预设模板="C01 (教室后排露出)",
            裸露等级="随机 (Random)",
            prompt_seed=42,
        )
        self.assertEqual(res_native.nudity_constraint.source, "preset_native")
        self.assertFalse(res_native.preset_adjusted)
        self.assertNotIn("已调整", res_native.description)

        # 3. IYKYKPresetBrowser 外部裸露覆盖 vs 无覆盖
        br_native = self.browser.browse_structured("C01 (教室后排露出)", "无 (None)", "高清写真 (High)", prompt_seed=42)
        self.assertEqual(br_native.nudity_constraint.source, "preset_native")
        self.assertFalse(br_native.preset_adjusted)

        br_l1 = self.browser.browse_structured(
            "C01 (教室后排露出)", "无 (None)", "高清写真 (High)", prompt_seed=42, nudity_override="L1"
        )
        self.assertEqual(br_l1.nudity_constraint.source, "external_override")
        self.assertEqual(br_l1.nudity_constraint.effective_level, "L1")
        self.assertTrue(br_l1.preset_adjusted)
        self.assertIn("已调整", br_l1.description)
        self.assertNotIn("erect nipple", br_l1.positive.lower())

        # 4. 预设所有内容与目标等级不兼容时 fail-closed 抛出 preset_incompatible_with_nudity_level
        incompat_preset = {
            "id": "SYNTH_INCOMPAT_L6",
            "name_zh": "仅上衣胸特写",
            "fragments": [
                {"slot": "clothing", "text": "heavy winter coat", "facts": {"garment_topologies": ["outerwear"], "piece_role": "outerwear", "allowed_nudity_levels": ["L1"]}},
                {"slot": "shot_type", "text": "close-up face portrait", "facts": {"visible_regions": ["face"]}},
            ],
        }
        with self.assertRaises(PromptValidationError) as ctx_p:
            self.assembler.assemble_preset(
                incompat_preset,
                None,
                "高清写真 (High)",
                rng=Random(0),
                nudity_constraint=NudityConstraint(
                    requested_level="L6",
                    effective_level="L6",
                    source="ui_explicit",
                    selected_branch="l6_frontal_genital_display",
                ),
            )
        self.assertEqual(ctx_p.exception.error_code, "preset_incompatible_with_nudity_level")

    # ------------------------------------------------------------------
    # 5. 规则改写后的事实同步与目标等级复检
    # ------------------------------------------------------------------

    def test_05_post_rewrite_revalidation_and_fact_alignment(self) -> None:
        """material_penetration 改写必须同步更新 SemanticFacts 并接受目标等级/分支二次校验。"""
        # L2 下 sheer_chiffon 允许进入 material_penetration，改写后清除陈旧 sheer 状态并同步新文本语义
        l2_c = NudityConstraint(requested_level="L2", effective_level="L2", source="ui_explicit", selected_branch="l2_collar_neckline")
        sheer_l2 = PromptAtom(
            atom_id="mp_l2_0",
            text="sheer silk fabric",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="sheer_chiffon",
            tag_order=0,
            span_order=0,
            provenance=TagProvenance(item_id="sheer_chiffon", kind="clothing", parent_ids=("sheer_chiffon",)),
            origin=SelectionOrigin(mode="preset", selector="clothing", selected_id="sheer_chiffon"),
            facts=SemanticFacts(garment_topologies=("top",), garment_states=("worn",), pattern_textures=("sheer",)),
        )
        res_l2, _, rep_l2 = self.resolver.resolve_atoms_with_full_report([sheer_l2], Random(42), nudity_constraint=l2_c)
        self.assertEqual(len(res_l2), 1)
        out_atom = res_l2[0]
        self.assertNotEqual(out_atom.text, "sheer silk fabric")
        self.assertNotIn("sheer", out_atom.facts.garment_states)
        self.assertTrue(any(d.rule_id == "material_penetration" and d.action == "replace" for d in rep_l2.decisions))
        if "dress" in out_atom.text.lower():
            self.assertEqual(out_atom.facts.piece_role, "one_piece")
            self.assertIn("one_piece", out_atom.facts.garment_topologies)
            self.assertIn("wet_clinging", out_atom.facts.garment_states)

        # L4 下若没有合规替换词条，material_penetration 绝不能用 wet dress 绕过 L4
        l4_c = NudityConstraint(requested_level="L4", effective_level="L4", source="ui_explicit", selected_branch="l4_panties_only")
        sheer_l4 = PromptAtom(
            atom_id="mp_l4_0",
            text="translucent lace nightgown",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="custom_sheer",
            tag_order=0,
            span_order=0,
            provenance=TagProvenance(item_id="custom_sheer", kind="custom", parent_ids=("custom_sheer",)),
            origin=SelectionOrigin(mode="custom", selector="clothing", raw_value="translucent lace nightgown"),
            facts=SemanticFacts(garment_topologies=("underwear",), garment_states=("worn",), pattern_textures=("sheer",)),
        )
        res_l4, _, rep_l4 = self.resolver.resolve_atoms_with_full_report([sheer_l4], Random(42), nudity_constraint=l4_c)
        for a in res_l4:
            self.assertNotIn("dress", a.text.lower())

        # 验证 post-rewrite 2b 复检兜底：即便是后续生成的 dress 产物在 L4 下也必须被拦下
        with patch("lib.conflict_resolver.ConflictResolver._resolve_material_penetration_atoms") as mock_mp:
            def _fake_mp(atoms, rule_item, ledger, rng, index, context_profile):
                a = index.get_all_active_ordered()[0]
                bad_dress = PromptAtom(
                    atom_id=f"{a.atom_id}__r_material_penetration",
                    text="wet dress clinging tightly to skin",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing",
                    source_item_id=a.source_item_id,
                    tag_order=a.tag_order,
                    span_order=a.span_order,
                    provenance=TagProvenance(item_id=a.source_item_id or "sheer_chiffon", kind="clothing", rule_id="material_penetration", parent_ids=(a.atom_id,)),
                    origin=SelectionOrigin(mode="resolver", selector="clothing", selected_id="material_penetration", parent_ids=(a.atom_id,)),
                    facts=SemanticFacts(garment_topologies=("one_piece",), piece_role="one_piece", garment_states=("wet_clinging",)),
                )
                index.replace(a.atom_id, bad_dress)
                ledger.record_replace("material_penetration", "physical", "material_penetration_replaced", (), a, (bad_dress,))
                return index.get_all_active_ordered()
            mock_mp.side_effect = _fake_mp
            res_recheck, _, rep_recheck = self.resolver.resolve_atoms_with_full_report([sheer_l4], Random(42), nudity_constraint=l4_c)
        self.assertEqual(res_recheck, [])
        self.assertTrue(any(d.reason_code == "material_penetration_removed" for d in rep_recheck.decisions))

    # ------------------------------------------------------------------
    # 6. L4 六类拓扑全剔除 / L5 discarded / L6 framing / 外部覆盖冲突
    # ------------------------------------------------------------------

    def test_06_l4_topologies_l5_discarded_l6_framing_and_external_conflicts(self) -> None:
        """L4 剔除全 6 类外衣拓扑，L5 保留 discarded 叙事与近景，L6 禁齐腰/特写，外部硬冲突抛错且让位留痕。"""
        l4_c = NudityConstraint(requested_level="L4", effective_level="L4", source="ui_explicit", selected_branch="l4_panties_only")
        for topo in ("top", "bottom_pants", "bottom_skirt", "outerwear", "one_piece", "ensemble_outfit"):
            with self.subTest(topology=topo):
                garment = PromptAtom(
                    atom_id=f"topo_{topo}",
                    text="test garment item",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing",
                    source_item_id=f"item_{topo}",
                    tag_order=0,
                    span_order=0,
                    provenance=TagProvenance(item_id=f"item_{topo}", kind="clothing", parent_ids=(f"item_{topo}",)),
                    origin=SelectionOrigin(mode="preset", selector="clothing", selected_id=f"item_{topo}"),
                    facts=SemanticFacts(garment_topologies=(topo,), garment_states=("worn",)),
                )
                out, _, rep = self.resolver.resolve_atoms_with_full_report([garment], Random(0), nudity_constraint=l4_c)
                self.assertEqual(out, [])
                self.assertEqual(rep.decisions[0].rule_id, "nudity_clothing_conflicts")

        # L5 discarded narrative + close_up 保留
        l5_c = NudityConstraint(requested_level="L5", effective_level="L5", source="ui_explicit", selected_branch="l5_clothes_discarded_nearby")
        disc = PromptAtom(
            atom_id="disc_0",
            text="clothes discarded on floor",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="linkage_general",
            tag_order=0,
            span_order=0,
            provenance=TagProvenance(item_id="linkage_general", kind="clothing_state", parent_ids=("linkage_general",)),
            origin=SelectionOrigin(mode="preset", selector="clothing", selected_id="linkage_general"),
            facts=SemanticFacts(piece_role="none", garment_states=("discarded",), visible_regions=("upper_body", "lower_body"), coverage_delta="uncover_all", exposed_regions=("full_nude",)),
        )
        cu = PromptAtom(
            atom_id="cu_0",
            text="close-up face portrait",
            span_type=SpanType.PLAIN,
            source_slot="shot_type",
            source_item_id="close_up",
            tag_order=1,
            span_order=0,
            provenance=TagProvenance(item_id="close_up", kind="shot_type", parent_ids=("close_up",)),
            origin=SelectionOrigin(mode="preset", selector="shot_type", selected_id="close_up"),
            facts=SemanticFacts(visible_regions=("face",)),
        )
        # L5 discarded narrative 独立保留 + close_up 对 L5 无全局禁令
        out_l5_disc, _, rep_l5_disc = self.resolver.resolve_atoms_with_full_report([disc], Random(0), nudity_constraint=l5_c)
        out_l5_cu, _, rep_l5_cu = self.resolver.resolve_atoms_with_full_report([cu], Random(0), nudity_constraint=l5_c)
        self.assertEqual([a.atom_id for a in out_l5_disc], ["disc_0"])
        self.assertEqual([a.atom_id for a in out_l5_cu], ["cu_0"])
        self.assertEqual(len(rep_l5_disc.decisions), 0)
        self.assertEqual(len(rep_l5_cu.decisions), 0)

        # L6 + close_up 外部覆盖硬冲突必须 Fail-Closed
        with self.assertRaises(PromptValidationError) as ctx_l6:
            self.generator.generate_structured(
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                服装款式="knit_sweater",
                服装状态="整齐穿着 (Neat)",
                nudity_override="L6",
                shot_type_override="extreme close-up of face",
                prompt_seed=1,
            )
        self.assertEqual(ctx_l6.exception.error_code, "external_override_hard_conflict")

        # L5 + 外部普通服装覆盖 -> 让位且标明 "外部覆盖导致等级让位"
        res_yield = self.generator.generate_structured(
            裸露等级="L5 极致全裸 (Full Nude)",
            clothing_override="tailored white button shirt",
            prompt_seed=3,
        )
        self.assertTrue(res_yield.nudity_constraint.yielded_to_override)
        self.assertEqual(res_yield.nudity_constraint.source, "derived_override")
        self.assertIn("外部覆盖导致等级让位", res_yield.description)
        self.assertIn("tailored white button shirt", res_yield.positive.lower())

    # ------------------------------------------------------------------
    # 7. 词数预算保护、accepted_atoms 验收与 fail-closed
    # ------------------------------------------------------------------

    def test_07_budget_protection_accepted_only_and_fail_closed(self) -> None:
        """优先级打包保序保留核心衣物；max_words=0 返回空串；极紧预算无法容纳完整套装则 Fail-Closed。"""
        # 1. max_words=0 不崩溃返回空
        res_zero = self.assembler.assemble_slots(
            {
                "nudity": [PromptFragment(text="fully clothed", source_slot="nudity", source_item_id="nudity_l1", order=0, facts=SemanticFacts(semantic_role="selector"))],
                "clothing": [PromptFragment(text="sweater", source_slot="clothing", source_item_id="knit_sweater", order=1, facts=SemanticFacts(piece_role="top", garment_topologies=("top",)))],
            },
            max_words=0,
        )
        self.assertEqual(res_zero.prompt, "")
        self.assertEqual(len(res_zero.accepted_atoms), 0)

        # 2. 保序优先级裁剪：长 scene_theme 与 secondary quality 让位，核心 top+bottom 完整保留且按原 tag_order 输出
        slot_frags = {
            "scene_theme": [
                PromptFragment(
                    text="sprawling ancient botanical conservatory",
                    source_slot="scene_theme",
                    order=0,
                    origin=SelectionOrigin(mode="custom", selector="scene_theme", raw_value="conservatory"),
                    facts=SemanticFacts(semantic_role="scene_anchor", space_kind="indoor"),
                ),
                PromptFragment(
                    text="bathed in warm golden afternoon light",
                    source_slot="scene_theme",
                    order=1,
                    origin=SelectionOrigin(mode="custom", selector="scene_theme", raw_value="conservatory"),
                    facts=SemanticFacts(semantic_role="scene_anchor", space_kind="indoor"),
                ),
            ],
            "nudity": [
                PromptFragment(
                    text="fully clothed",
                    source_slot="nudity",
                    source_item_id="nudity_l1",
                    order=1,
                    provenance=TagProvenance(item_id="nudity_l1", kind="nudity", semantic_ids=("nudity:L1",)),
                    facts=SemanticFacts(semantic_role="selector"),
                )
            ],
            "clothing": [
                PromptFragment(
                    text="white cotton blouse",
                    source_slot="clothing",
                    source_item_id="shirts_blouses",
                    order=2,
                    provenance=TagProvenance(item_id="shirts_blouses", kind="base_clothing"),
                    facts=SemanticFacts(garment_topologies=("top",), piece_role="top", covered_regions=("upper_torso",)),
                ),
                PromptFragment(
                    text="pleated wool skirt",
                    source_slot="clothing",
                    source_item_id="pleated_skirt",
                    order=3,
                    provenance=TagProvenance(item_id="pleated_skirt", kind="clothing_completion"),
                    facts=SemanticFacts(garment_topologies=("bottom_skirt",), piece_role="bottom", covered_regions=("lower_torso",)),
                ),
            ],
            "quality": [
                PromptFragment(
                    text="ultra detailed photographic render quality",
                    source_slot="quality",
                    source_item_id="quality_high",
                    order=4,
                    provenance=TagProvenance(item_id="quality_high", kind="quality"),
                    facts=SemanticFacts(quality_class="high"),
                )
            ],
        }
        res_ok = self.assembler.assemble_slots(slot_frags, max_words=10)
        acc_cl = [a for a in res_ok.accepted_atoms if a.source_slot == "clothing"]
        self.assertEqual([a.text for a in acc_cl], ["white cotton blouse", "pleated wool skirt"])
        self.assertTrue(assess_clothing_completeness(acc_cl, (), "L1").is_complete)
        # 检查仅在预算内且未违反原顺序
        self.assertIn("fully clothed, white cotton blouse, pleated wool skirt", res_ok.prompt)

        # 3. 极紧预算无法容纳完整穿搭 -> Fail-Closed 抛出 insufficient_budget_for_complete_outfit
        tight_slot_frags = dict(slot_frags)
        tight_slot_frags["scene_theme"] = [
            PromptFragment(
                text="botanical glass conservatory",
                source_slot="scene_theme",
                order=0,
                origin=SelectionOrigin(mode="custom", selector="scene_theme", raw_value="botanical glass conservatory"),
                facts=SemanticFacts(semantic_role="scene_anchor", space_kind="indoor"),
            )
        ]
        tight_slot_frags["quality"] = [
            PromptFragment(
                text="high photographic quality",
                source_slot="quality",
                source_item_id="quality_high",
                order=4,
                provenance=TagProvenance(item_id="quality_high", kind="quality"),
                facts=SemanticFacts(quality_class="high"),
            )
        ]
        with self.assertRaises(PromptValidationError) as ctx_b:
            self.assembler.assemble_slots(tight_slot_frags, max_words=6)
        self.assertEqual(ctx_b.exception.error_code, "insufficient_budget_for_complete_outfit")

        # 4. 构造消解后受预算丢掉下装的生成器场景，确认只看 accepted_atoms 且失败关闭
        long_quality = ", ".join(f"ext quality token {i:02d}" for i in range(62))  # 248 words (<250 ext cap) leaving 2w!
        with self.assertRaises(PromptValidationError) as ctx_gen:
            self.generator.generate_structured(
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                服装款式="啦啦队服 (Cheerleader)",
                服装状态="无 (None)",
                quality_override=long_quality,
                prompt_seed=1,
            )
        self.assertEqual(ctx_gen.exception.error_code, "insufficient_budget_for_complete_outfit")

    # ------------------------------------------------------------------
    # 8. 自定义槽位拼装器 (IYKYKCustomSlotCombiner) 联动
    # ------------------------------------------------------------------

    def test_08_custom_slot_combiner_unified_nudity_and_overrides(self) -> None:
        """IYKYKCustomSlotCombiner 传播 NudityConstraint，支持外部覆盖与 L5/L1 约束。"""
        res_l5 = self.combiner.combine_structured(
            prompt_seed=7,
            nudity="L5",
            clothing="knitted sweater",
            nudity_override="L5",
        )
        self.assertEqual(res_l5.nudity_constraint.effective_level, "L5")
        self.assertEqual(res_l5.nudity_constraint.source, "external_override")
        self.assertNotIn("knitted sweater", res_l5.positive.lower())

        res_l1 = self.combiner.combine_structured(
            prompt_seed=7,
            nudity="L1",
            clothing="jk_seifuku",
            jewelry="ankle bracelet",
        )
        self.assertEqual(res_l1.nudity_constraint.effective_level, "L1")
        self.assertIn("ankle bracelet", res_l1.positive.lower())

    # ------------------------------------------------------------------
    # 9. Diagnostics 2.0 Schema 兼容性与全字段溯源
    # ------------------------------------------------------------------

    def test_09_diagnostics_2_0_schema_traceability_and_descriptions(self) -> None:
        """诊断 JSON 严格保持 2.0 结构，记录 nudity_constraint 与 outfit_completion 溯源并在报错时给出中文说明。"""
        # 1. 自动补全场景：sampling_rationale 含 outfit_completion 与 nudity_constraint，通过 Schema + Oracle
        inputs_comp = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "随机 (Random)",
            "剧情主题": "随机 (Random)",
            "景别构图": "自动 (Auto)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L1 包裹暗示 (Fully Clothed / Suggestive)",
            "服装款式": "随机 (Random)",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
            "发型发色": "随机 (Random)",
            "饰品头饰": "无 (None)",
            "妆容细节": "无 (None)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "随机 (Random)",
            "光影预设": "自动 (Auto)",
            "胶片风格": "无 (None)",
            "液体效果": "无 (None)",
            "纹身标记": "无 (None)",
            "道具物件": "无 (None)",
            "角色设定": "无 (None)",
            "真实微瑕": "无 (None)",
            "画质等级": "高清写真 (High)",
            "prompt_seed": 5,
        }
        pos, neg, desc, audit_str = self.diagnostics.diagnose(**inputs_comp)
        self.assertTrue(pos)
        self.assertIn("已补全", desc)
        audit = json.loads(audit_str)
        self.assertEqual(set(audit.keys()), EXPECTED_TOP_LEVEL_KEYS_V2)
        self.assertEqual(list(self.validator.iter_errors(audit)), [])
        validate_audit_json_oracle(audit, schema_doc=self.schema_doc, expected_positive=pos, trusted_inputs=inputs_comp)

        by_sel = {s["selector"]: s for s in audit["selections"]}
        self.assertIn("nudity_constraint", by_sel["nudity"]["sampling_rationale"])
        self.assertIn("outfit_completion", by_sel["clothing"]["sampling_rationale"])
        self.assertEqual(by_sel["nudity"]["sampling_rationale"]["nudity_constraint"]["effective_level"], "L1")
        self.assertFalse(by_sel["clothing"]["sampling_rationale"]["outfit_completion"]["exempt"])
        self.assertGreater(len(by_sel["clothing"]["sampling_rationale"]["outfit_completion"]["complement_ids"]), 0)

        # 2. 预设调整场景：nudity_constraint 出现在 preset_core sampling_rationale 且 desc 含 "已调整"
        _, _, p_desc, p_audit_str = self.diagnostics.diagnose(
            预设模板="C01 (教室后排露出)",
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            prompt_seed=42,
        )
        self.assertIn("已调整", p_desc)
        p_audit = json.loads(p_audit_str)
        self.assertEqual(list(self.validator.iter_errors(p_audit)), [])
        p_sels = {s["selector"]: s for s in p_audit["selections"]}
        self.assertIn("nudity_constraint", p_sels["preset_core"]["sampling_rationale"])

        # 3. 报错诊断场景：外部硬冲突 -> "无法生成:", status="error", error_info.details.nudity_constraint 非空且通过 Schema
        e_pos, _, e_desc, e_audit_str = self.diagnostics.diagnose(
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="knit_sweater",
            服装状态="整齐穿着 (Neat)",
            nudity_override="L6",
            shot_type_override="close-up portrait",
            prompt_seed=1,
            raise_on_error=False,
        )
        self.assertEqual(e_pos, "")
        self.assertTrue(e_desc.startswith("无法生成:"))
        e_audit = json.loads(e_audit_str)
        self.assertEqual(e_audit["status"], "error")
        self.assertEqual(list(self.validator.iter_errors(e_audit)), [])
        self.assertIn("nudity_constraint", e_audit["error_info"]["details"])
        self.assertEqual(e_audit["error_info"]["details"]["nudity_constraint"]["effective_level"], "L6")

        # 4. 预算不足无法保留完整穿搭 -> raise_on_error=False 输出 "无法生成:" 且 Schema 合规
        long_q = ", ".join(f"ext quality token {i:02d}" for i in range(62))
        b_pos, _, b_desc, b_audit_str = self.diagnostics.diagnose(
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="啦啦队服 (Cheerleader)",
            服装状态="无 (None)",
            quality_override=long_q,
            prompt_seed=1,
            raise_on_error=False,
        )
        self.assertEqual(b_pos, "")
        self.assertTrue(b_desc.startswith("无法生成:"))
        b_audit = json.loads(b_audit_str)
        self.assertEqual(b_audit["status"], "error")
        self.assertEqual(b_audit["error_info"]["error_code"], "insufficient_budget_for_complete_outfit")
        self.assertEqual(list(self.validator.iter_errors(b_audit)), [])


if __name__ == "__main__":
    unittest.main()
