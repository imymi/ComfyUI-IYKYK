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
from lib.conflict_resolver import ConflictResolver, find_carrier_mismatch_in_atoms
from lib.errors import PromptValidationError, UnresolvedConflictError
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

    # ------------------------------------------------------------------
    # 10. M3 复审缺陷回归：L5 预算兜底 / L2-L3 单分支实体一致性 / 终态诊断去陈旧
    # ------------------------------------------------------------------

    def test_10_budget_retains_required_nudity_level_prompts_and_branch_carriers(self) -> None:
        """[P1] 极紧预算剪光目标等级核心提示词（如 L5 仅剩 quality/lighting）时必须显式报错，绝不可假成功。"""
        all_none = {
            k: "无 (None)"
            for k, v in self.generator.INPUT_TYPES()["optional"].items()
            if isinstance(v[0], list) and "无 (None)" in v[0]
        }
        # 1. 复现用例：L5、seed=0、可选槽位 None、249 词去重 quality_override -> 剩余 1 词无法容纳 L5 裸露词
        qo_249 = " ".join(f"qword{i}" for i in range(249))
        with self.assertRaises(PromptValidationError) as ctx_l5:
            self.generator.generate_structured(
                **{
                    **all_none,
                    "裸露等级": "L5",
                    "画质等级": "高清写真 (High)",
                    "prompt_seed": 0,
                    "quality_override": qo_249,
                }
            )
        self.assertEqual(ctx_l5.exception.category, "external_budget_exceeded")
        self.assertEqual(ctx_l5.exception.error_code, "insufficient_budget_for_nudity_level")

        # 2. 放宽至 240 词时可容纳 L5 核心词，正常成功且接受原子确含 nudity 词
        qo_240 = " ".join(f"qword{i}" for i in range(240))
        res_ok = self.generator.generate_structured(
            **{
                **all_none,
                "裸露等级": "L5",
                "画质等级": "高清写真 (High)",
                "prompt_seed": 0,
                "quality_override": qo_240,
            }
        )
        self.assertTrue(any(a.source_slot == "nudity" for a in res_ok.atoms))
        self.assertEqual(res_ok.nudity_constraint.effective_level, "L5")

        # 3. 诊断入口在预算剥光 L5 等级时也必须给出 status="error" 与 Schema 合规 JSON
        d_pos, _, d_desc, d_audit_str = self.diagnostics.diagnose(
            **{
                **all_none,
                "裸露等级": "L5",
                "画质等级": "高清写真 (High)",
                "prompt_seed": 0,
                "quality_override": qo_249,
                "raise_on_error": False,
            }
        )
        self.assertEqual(d_pos, "")
        self.assertTrue(d_desc.startswith("无法生成:"))
        d_audit = json.loads(d_audit_str)
        self.assertEqual(d_audit["status"], "error")
        self.assertEqual(d_audit["error_info"]["error_code"], "insufficient_budget_for_nudity_level")
        self.assertEqual(list(self.validator.iter_errors(d_audit)), [])

        # 4. L6 极紧预算剥光生殖器展示词亦必须 Fail-Closed；max_words=0 例外保持不变
        qo_248 = " ".join(f"qsix{i}" for i in range(248))
        with self.assertRaises(PromptValidationError) as ctx_l6:
            self.assembler.assemble_slots(
                {
                    "nudity": [
                        PromptFragment(
                            text="pussy fully displayed",
                            source_slot="nudity",
                            source_item_id="nudity_l6",
                            order=0,
                            provenance=TagProvenance(item_id="nudity_l6", kind="nudity", semantic_ids=("nudity:L6",)),
                            facts=SemanticFacts(
                                semantic_role="selector",
                                allowed_nudity_levels=("L6",),
                                exposed_regions=("genitals",),
                                nudity_branches=("l6_frontal_genital_display",),
                            ),
                        )
                    ],
                    "lighting": [
                        PromptFragment(
                            text="backlight",
                            source_slot="lighting",
                            source_item_id="backlight",
                            order=1,
                            facts=SemanticFacts(semantic_role="selector"),
                        )
                    ],
                },
                max_words=250,
                external_overrides={"quality": qo_248},
                nudity_constraint=NudityConstraint(
                    requested_level="L6",
                    effective_level="L6",
                    source="ui_explicit",
                    selected_branch="l6_frontal_genital_display",
                ),
            )
        self.assertEqual(ctx_l6.exception.error_code, "insufficient_budget_for_nudity_level")

    def test_11_l2_l3_explicit_single_garments_and_conflict_resolver_coherence(self) -> None:
        """[P1] 显式单品与 L2/L3 联动必须在分支、联动词、扩展与 ConflictResolver 全链严格自洽。"""
        # 1. 复现 2a: L2 + dress_backless + Auto Link 多种子（含 seed=3）不得出现 midriff/cleavage/high slit/off-shoulder/sleeve 矛盾
        forbidden_backless_l2 = (
            "midriff showing between top and skirt",
            "subtle cleavage peeking",
            "plunging neckline",
            "high slit showing bare leg",
            "off-shoulder cut",
            "bare arm from sleeve rolled up",
            "wrist exposed from pushed sleeve",
            "collar unbuttoned",
            "one button undone",
        )
        for sd in range(8):
            with self.subTest(case="dress_backless_l2", seed=sd):
                res = self.generator.generate_structured(
                    裸露等级="L2",
                    服装款式="dress_backless",
                    服装状态="自动联动裸露等级 (Auto Link Nudity)",
                    prompt_seed=sd,
                )
                self.assertEqual(res.nudity_constraint.selected_branch, "l2_backless")
                pos_low = res.positive.lower()
                self.assertIn("backless", pos_low)
                for bad in forbidden_backless_l2:
                    self.assertNotIn(bad, pos_low, f"seed={sd} leaked contradictory text {bad!r}: {res.positive}")

        # 2. 复现 2b: L3 + lingerie_bra + 服装状态=None 多种子（含 seed=3）不得出现 topless/all top removed/tank top/dress/bra removed
        forbidden_bra_l3 = (
            "all top removed",
            "topless bottom still dressed",
            "topless with skirt still on",
            "tank top raised",
            "dress pulled down",
            "bra removed",
            "shirt open",
            "panties only",
            "bare chest with pants on",
        )
        for sd in range(8):
            with self.subTest(case="lingerie_bra_l3", seed=sd):
                res = self.generator.generate_structured(
                    裸露等级="L3",
                    服装款式="lingerie_bra",
                    服装状态="无 (None)",
                    prompt_seed=sd,
                )
                self.assertEqual(res.nudity_constraint.selected_branch, "l3_bra_pushed_up")
                pos_low = res.positive.lower()
                self.assertTrue("bra" in pos_low or "bralette" in pos_low, f"Missing bra carrier in: {res.positive}")
                for bad in forbidden_bra_l3:
                    self.assertNotIn(bad, pos_low, f"seed={sd} leaked contradictory text {bad!r}: {res.positive}")

        # 3. 消解器直连负向用例：即使直接喂入 L2 l2_midriff_waist + dress_backless + subtle_cleavage/high_slit 也必须清理跨分支矛盾
        l2_mid_c = NudityConstraint(
            requested_level="L2",
            effective_level="L2",
            source="ui_explicit",
            selected_branch="l2_midriff_waist",
        )
        l2_conflict_atoms = [
            PromptAtom(
                atom_id="n_mid",
                text="midriff showing between top and skirt",
                span_type=SpanType.PLAIN,
                source_slot="nudity",
                source_item_id="nudity_l2",
                tag_order=0,
                span_order=0,
                provenance=TagProvenance(item_id="nudity_l2", kind="nudity"),
                origin=SelectionOrigin(mode="explicit", selector="nudity", selected_id="nudity_l2"),
                facts=SemanticFacts(allowed_nudity_levels=("L2",), exposed_regions=("midriff",), nudity_branches=("l2_midriff_waist",)),
            ),
            PromptAtom(
                atom_id="c_backless",
                text="crisscross strap backless dress",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="dress_backless",
                tag_order=1,
                span_order=0,
                provenance=TagProvenance(item_id="dress_backless", kind="base_clothing"),
                origin=SelectionOrigin(mode="explicit", selector="clothing", selected_id="dress_backless"),
                facts=SemanticFacts(piece_role="one_piece", garment_topologies=("one_piece",), covered_regions=("upper_torso", "lower_torso"), allowed_nudity_levels=("L2", "L3")),
            ),
            PromptAtom(
                atom_id="c_cleav",
                text="subtle cleavage peeking",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="linkage_general",
                tag_order=2,
                span_order=0,
                provenance=TagProvenance(item_id="linkage_general", kind="clothing_state"),
                origin=SelectionOrigin(mode="auto", selector="clothing_state", selected_id="auto_linkage"),
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_states=("loosened",),
                    exposed_regions=("cleavage",),
                    allowed_nudity_levels=("L2",),
                ),
            ),
            PromptAtom(
                atom_id="c_ext",
                text="plunging neckline",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="subtle_cleavage",
                tag_order=3,
                span_order=0,
                provenance=TagProvenance(item_id="subtle_cleavage", kind="clothing_extension"),
                origin=SelectionOrigin(mode="auto", selector="clothing_state", selected_id="subtle_cleavage"),
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_states=("worn",),
                    exposed_regions=("cleavage",),
                    allowed_nudity_levels=("L2",),
                    nudity_branches=("l2_collar_neckline",),
                ),
            ),
        ]
        resolved_l2, _, _ = self.resolver.resolve_atoms_with_full_report(l2_conflict_atoms, Random(3), nudity_constraint=l2_mid_c)
        resolved_l2_ids = {a.atom_id for a in resolved_l2}
        self.assertEqual(resolved_l2_ids, {"n_mid"})

        # 4. 消解器直连负向用例：L3 l3_topless_skirt_kept 必须剔除 covered_regions 含 intimate_upper 的文胸且 detect_hard_conflicts 可捕获残余 L3 冲突
        l3_top_c = NudityConstraint(
            requested_level="L3",
            effective_level="L3",
            source="ui_explicit",
            selected_branch="l3_topless_skirt_kept",
        )
        l3_atoms = [
            PromptAtom(
                atom_id="n_top",
                text="all top removed",
                span_type=SpanType.PLAIN,
                source_slot="nudity",
                source_item_id="nudity_l3",
                tag_order=0,
                span_order=0,
                provenance=TagProvenance(item_id="nudity_l3", kind="nudity"),
                origin=SelectionOrigin(mode="explicit", selector="nudity", selected_id="nudity_l3"),
                facts=SemanticFacts(allowed_nudity_levels=("L3",), exposed_regions=("breasts",), nudity_branches=("l3_topless_skirt_kept",)),
            ),
            PromptAtom(
                atom_id="c_bra",
                text="bra",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="lingerie_bra",
                tag_order=1,
                span_order=0,
                provenance=TagProvenance(item_id="lingerie_bra", kind="base_clothing"),
                origin=SelectionOrigin(mode="explicit", selector="clothing", selected_id="lingerie_bra"),
                facts=SemanticFacts(piece_role="underwear", garment_topologies=("underwear",), covered_regions=("intimate_upper",), allowed_nudity_levels=("L3", "L4")),
            ),
        ]
        resolved_l3, _, _ = self.resolver.resolve_atoms_with_full_report(l3_atoms, Random(3), nudity_constraint=l3_top_c)
        self.assertEqual([a.atom_id for a in resolved_l3], ["n_top"])
        hard_conflicts, _ = self.resolver.detect_hard_conflicts(l3_atoms)
        self.assertTrue(any(c[0] == "residual_nudity_clothing_conflict" for c in hard_conflicts))

    def test_12_diagnostics_and_descriptions_clear_stale_nudity_and_completion_states(self) -> None:
        """[P2] 外部服装覆盖让位或替换随机服装后，description、outfit_completion_info 与诊断 JSON 绝不可保留陈旧状态。"""
        # 1. 复现 3a: L5 + clothing_override="white shirt, pleated skirt" -> description 不得仍报 "裸露: L5"
        res_yield = self.generator.generate_structured(
            裸露等级="L5",
            clothing_override="white shirt, pleated skirt",
            prompt_seed=0,
        )
        self.assertTrue(res_yield.nudity_constraint.yielded_to_override)
        self.assertIsNone(res_yield.nudity_constraint.effective_level)
        self.assertNotIn("裸露: L5", res_yield.description)
        self.assertIn("裸露: 已让位(原请求: L5)", res_yield.description)
        self.assertIn("服装: 外部覆盖", res_yield.description)
        self.assertIn("外部覆盖导致等级让位", res_yield.description)

        # 2. 复现 3b: 原本会触发自动补全的随机种子 + clothing_override -> exempt=True, complement_ids=[], 描述不含 "已补全"
        for sd in (1, 4, 5, 8, 9):
            with self.subTest(seed=sd):
                res_ov = self.generator.generate_structured(
                    裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                    服装款式="随机 (Random)",
                    服装状态="自动联动裸露等级 (Auto Link Nudity)",
                    clothing_override="black pleated miniskirt",
                    prompt_seed=sd,
                )
                self.assertEqual(res_ov.outfit_completion_info["exempt"], True)
                self.assertEqual(res_ov.outfit_completion_info["exempt_reason"], "external_clothing_override")
                self.assertEqual(res_ov.outfit_completion_info["complement_ids"], [])
                self.assertFalse(res_ov.outfit_completion_info["repaired_ensemble"])
                self.assertFalse(res_ov.outfit_completion_info["fallback_used"])
                self.assertNotIn("已补全", res_ov.description)
                self.assertIn("服装: 外部覆盖", res_ov.description)

        # 3. 诊断 JSON 对上述两场景同样不报陈旧状态，且全量通过 Schema 与 Oracle
        ov_inputs = {
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
            "clothing_override": "black pleated miniskirt",
            "prompt_seed": 5,
        }
        pos_d, _, desc_d, audit_d_str = self.diagnostics.diagnose(**ov_inputs)
        self.assertNotIn("已补全", desc_d)
        audit_d = json.loads(audit_d_str)
        self.assertEqual(list(self.validator.iter_errors(audit_d)), [])
        validate_audit_json_oracle(audit_d, schema_doc=self.schema_doc, expected_positive=pos_d, trusted_inputs=ov_inputs)
        by_sel_d = {s["selector"]: s for s in audit_d["selections"]}
        comp_diag = by_sel_d["clothing"]["sampling_rationale"]["outfit_completion"]
        self.assertTrue(comp_diag["exempt"])
        self.assertEqual(comp_diag["exempt_reason"], "external_clothing_override")
        self.assertEqual(comp_diag["complement_ids"], [])

    def test_13_carrier_coherence_denim_jeans_bodysuit_and_positive_skirt_slit(self) -> None:
        """[P1] 按提示词级核验服装载体前提：裤装/非裙装连体衣绝不放出裙衩/高开衩，真裙/旗袍合法保留，保留最多1次受控重组。"""
        common_none = {
            "裸露等级": "L2",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
            "场景大类": "无 (None)",
            "剧情主题": "无 (None)",
            "景别构图": "无 (None)",
            "拍摄视角": "无 (None)",
            "发型发色": "无 (None)",
            "姿势动作": "无 (None)",
            "情绪表情": "无 (None)",
            "光影预设": "无 (None)",
        }

        # 1. 失败复现 1: L2 + denim_jeans + Auto Link + seed=3 (及多种子)
        res_jeans = self.generator.generate_structured(**common_none, 服装款式="denim_jeans", prompt_seed=3)
        pos_jeans = res_jeans.positive.lower()
        self.assertEqual(res_jeans.nudity_constraint.selected_branch, "l2_leg_slit")
        self.assertIn("cropped jeans", pos_jeans)
        self.assertTrue("ankle visible" in pos_jeans or "bare feet with otherwise fully dressed" in pos_jeans)
        self.assertNotIn("skirt slit", pos_jeans)
        self.assertNotIn("high slit", pos_jeans)
        self.assertNotIn("high side slit", pos_jeans)
        self.assertEqual(find_carrier_mismatch_in_atoms(res_jeans.atoms), [])
        self.assertTrue(res_jeans.outfit_completion_info["exempt"])

        # 2. 失败复现 2: L2 + 随机 (Random) + Auto Link + seed=21 (leotard_bodysuit)
        res_rand21 = self.generator.generate_structured(**common_none, 服装款式="随机 (Random)", prompt_seed=21)
        pos_rand21 = res_rand21.positive.lower()
        self.assertEqual(res_rand21.nudity_constraint.selected_branch, "l2_leg_slit")
        self.assertIn("leotard", pos_rand21)
        self.assertTrue("bare feet with otherwise fully dressed" in pos_rand21 or "ankle visible" in pos_rand21)
        self.assertNotIn("skirt slit revealing thigh", pos_rand21)
        self.assertNotIn("high slit showing bare leg", pos_rand21)
        self.assertNotIn("high side slit", pos_rand21)
        self.assertEqual(find_carrier_mismatch_in_atoms(res_rand21.atoms), [])

        # 3. 批量核验非裙装连体衣/裤装绝不因 one_piece/ensemble 冒充裙装载体
        for style_id in ("denim_jeans", "leotard_bodysuit", "latex_catsuit", "tactical_jumpsuit"):
            for sd in range(12):
                with self.subTest(style=style_id, seed=sd):
                    r = self.generator.generate_structured(**common_none, 服装款式=style_id, prompt_seed=sd)
                    p = r.positive.lower()
                    for bad in ("skirt slit", "high slit", "high side slit", "one button undone", "collar unbuttoned"):
                        self.assertNotIn(bad, p, f"{style_id} seed={sd} leaked {bad!r}: {r.positive}")
                    self.assertEqual(find_carrier_mismatch_in_atoms(r.atoms), [])

        # 4. 正向回归: 真正裙装 / 连衣裙 / 旗袍在 l2_leg_slit 下允许并保留裙衩/高开衩
        for style_id, sd in (("pleated_skirt", 0), ("dress_casual", 3), ("qipao", 3)):
            with self.subTest(positive_skirt_style=style_id, seed=sd):
                r_pos = self.generator.generate_structured(**common_none, 服装款式=style_id, prompt_seed=sd)
                self.assertEqual(r_pos.nudity_constraint.selected_branch, "l2_leg_slit")
                p_low = r_pos.positive.lower()
                self.assertIn("skirt slit revealing thigh", p_low)
                self.assertIn("high side slit to upper thigh exposing long bare leg", p_low)
                self.assertEqual(find_carrier_mismatch_in_atoms(r_pos.atoms), [])

        # 5. 消解器与装配器旁路复检: 显式单品不豁免载体冲突，单条冲突全部清除或外部冲突 Fail-Closed
        l2_slit_nc = NudityConstraint(
            requested_level="L2",
            effective_level="L2",
            source="ui_explicit",
            selected_branch="l2_leg_slit",
        )
        bypass_frags_drop = [
            PromptFragment(
                text="cropped jeans",
                source_slot="clothing",
                source_item_id="denim_jeans",
                order=0,
                provenance=TagProvenance(item_id="denim_jeans", kind="base_clothing"),
                origin=SelectionOrigin(mode="explicit", selector="clothing", selected_id="denim_jeans"),
                facts=SemanticFacts(piece_role="bottom", garment_topologies=("bottom_pants",), covered_regions=("lower_torso",), allowed_nudity_levels=("L1", "L2", "L3")),
            ),
            PromptFragment(
                text="skirt slit revealing thigh",
                source_slot="nudity",
                source_item_id="nudity_l2",
                order=1,
                provenance=TagProvenance(item_id="nudity_l2", kind="nudity"),
                origin=SelectionOrigin(mode="explicit", selector="nudity", selected_id="nudity_l2"),
                facts=SemanticFacts(allowed_nudity_levels=("L2",), exposed_regions=("thigh_leg",), nudity_branches=("l2_leg_slit",)),
            ),
            PromptFragment(
                text="ankle visible",
                source_slot="nudity",
                source_item_id="nudity_l2",
                order=2,
                provenance=TagProvenance(item_id="nudity_l2", kind="nudity"),
                origin=SelectionOrigin(mode="explicit", selector="nudity", selected_id="nudity_l2"),
                facts=SemanticFacts(allowed_nudity_levels=("L2",), exposed_regions=("ankle_feet",), nudity_branches=("l2_leg_slit",)),
            ),
            PromptFragment(
                text="high side slit to upper thigh exposing long bare leg",
                source_slot="clothing",
                source_item_id="high_slit",
                order=3,
                provenance=TagProvenance(item_id="high_slit", kind="clothing_extension"),
                origin=SelectionOrigin(mode="auto", selector="clothing_state", selected_id="high_slit"),
                facts=SemanticFacts(semantic_role="selector", exposed_regions=("thigh_leg",), nudity_branches=("l2_leg_slit",)),
            ),
        ]
        asm_drop = _assembler.assemble_result(bypass_frags_drop, DATA_DIR, Random(3), nudity_constraint=l2_slit_nc)
        self.assertIn("cropped jeans", asm_drop.prompt.lower())
        self.assertIn("ankle visible", asm_drop.prompt.lower())
        self.assertNotIn("skirt slit revealing thigh", asm_drop.prompt.lower())
        self.assertNotIn("high side slit", asm_drop.prompt.lower())
        self.assertEqual(find_carrier_mismatch_in_atoms(asm_drop.accepted_atoms), [])

        # 若无兼容 nudity 替补、唯一 nudity 词条因缺载体被消光，或外部覆盖冲突，必须 Fail-Closed 报错
        bypass_only_slit = bypass_frags_drop[:2]
        with self.assertRaises(PromptValidationError) as ctx_empty_nud:
            _assembler.assemble_result(bypass_only_slit, DATA_DIR, Random(3), nudity_constraint=l2_slit_nc)
        self.assertEqual(ctx_empty_nud.exception.error_code, "nudity_prompts_eliminated_by_conflict")

        with self.assertRaises((PromptValidationError, UnresolvedConflictError)):
            self.generator.generate_structured(
                裸露等级="L2",
                服装款式="denim_jeans",
                clothing_override="cropped jeans",
                nudity_override="skirt slit revealing thigh",
                prompt_seed=3,
            )

        # 6. 最多 1 次受控重组约束：随机服饰若首轮触发载体缺失错误，最多只调用一次 rebuild 后即 Fail-Closed
        orig_sample_clothing = nodes_mod._sampler.sample_clothing_result
        orig_sample_nudity = nodes_mod._sampler.__class__._sample_nudity_result_with_branch
        call_count = 0

        def _forcing_rebuild_sampler(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return orig_sample_clothing("denim_jeans", *args[1:], **kwargs)

        def _only_slit_nudity(level, rng, **kwargs):
            res, lvl, branch = orig_sample_nudity(
                nodes_mod._sampler,
                level,
                rng,
                branch="l2_leg_slit",
                explicit_clothing=kwargs.get("explicit_clothing"),
                clothing_state=kwargs.get("clothing_state"),
            )
            if res is not None:
                from dataclasses import replace as _dc_replace
                lvl_data = [x for x in nodes_mod._sampler._load("nudity_levels")["nudity_levels"] if x.get("level") == "L2"][0]
                slit_raw = [t for t in lvl_data["tags"] if "skirt slit" in t.get("text", "").lower()][:1]
                slit_str, slit_sampled = nodes_mod._sampler._to_sampled_tags(slit_raw, res.item_id, "nudity")
                res = _dc_replace(res, tags=slit_str, sampled_tags=slit_sampled)
            return res, lvl, branch

        with patch.object(nodes_mod._sampler, "sample_clothing_result", side_effect=_forcing_rebuild_sampler):
            with patch.object(nodes_mod._sampler, "_sample_nudity_result_with_branch", side_effect=_only_slit_nudity):
                with self.assertRaises(PromptValidationError):
                    self.generator.generate_structured(**common_none, 服装款式="随机 (Random)", prompt_seed=21)
        self.assertEqual(call_count, 2, "随机服饰载体失败后只允许 1 次初始采样 + 最多 1 次受控重组")


if __name__ == "__main__":
    unittest.main()
