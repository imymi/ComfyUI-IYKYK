"""
test_v1_2_1_m0_reproductions.py — IYKYK v1.2.1 M0 基线异常复现与目标契约差分验证

说明：
- M0 阶段在基线 v1.2.0 (5744eeb) 上冻结 23 组确定性复现与豁免兼容性对照案例；
- 直接消费 tests/fixtures/v1_2_1_m0_reproductions.json 中的输入与种子；
- 明确断言当前基线确实存在随机缺件、L1–L6 分支与规则改写后事实背离、跨槽位暴露泄露、
  预设跳过显式裸露等级、以及无完整套装锚点时预算裁剪撕裂分体穿搭等问题，并验证豁免入口兼容性。
- 在 M1–M3 逐阶段修复后，本文件中的断言将逐步由“复现基线缺陷”切换为“强制满足 v1.2.1 契约”。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import unittest

from lib.models import PromptFragment, SemanticFacts, TagProvenance
from lib.outfit_completion import assess_clothing_completeness, is_tags_compatible_with_state
from nodes import IYKYKPromptGenerator, _assembler, _sampler


REPO_DIR = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_DIR / "tests" / "fixtures" / "v1_2_1_m0_reproductions.json"


class TestV121M0Reproductions(unittest.TestCase):
    """M0 冻结问题复现套件（对应 v1.2.0 / 5744eeb 基线）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.cases = {c["id"]: c for c in cls.doc["cases"]}
        cls.generator = IYKYKPromptGenerator()

    def test_00_fixture_integrity(self) -> None:
        """校验 M0 复现夹具完整性与必填字段。"""
        self.assertEqual(self.doc["schema_version"], "1.0")
        self.assertEqual(self.doc["baseline_tag"], "v1.2.0")
        expected_ids = {
            "REP-OUTFIT-01",
            "REP-OUTFIT-02",
            "REP-OUTFIT-03",
            "REP-OUTFIT-04",
            "REP-OUTFIT-05",
            "REP-OUTFIT-06",
            "REP-OUTFIT-07",
            "REP-OUTFIT-08",
            "REP-OUTFIT-09",
            "REP-OUTFIT-10",
            "REP-NUDITY-01",
            "REP-NUDITY-02",
            "REP-NUDITY-03",
            "REP-NUDITY-04",
            "REP-NUDITY-05",
            "REP-NUDITY-06",
            "REP-PRESET-01",
            "REP-PRESET-02",
            "REP-BUDGET-01",
            "REP-BUDGET-02",
            "REP-BUDGET-03",
            "COMPAT-EXEMPT-01",
            "COMPAT-EXEMPT-02",
        }
        self.assertEqual(set(self.cases.keys()), expected_ids)
        for cid, case in self.cases.items():
            self.assertIn("inputs", case, cid)
            self.assertIn("seed", case, cid)
            self.assertIn("baseline_actual", case, cid)
            self.assertIn("v1_2_1_expectation", case, cid)

    def _run_generator_case(self, case_id: str, **override_inputs):
        case = self.cases[case_id]
        kwargs = dict(case["inputs"])
        kwargs.update(override_inputs)
        kwargs["prompt_seed"] = case["seed"]
        return self.generator.generate_structured(**kwargs), case

    def test_01_l1_l2_random_outfit_missing_pieces_and_leaks(self) -> None:
        """M2 契约验证：L1/L2 随机/自动抽样保留主款并补全相容缺失件，禁止纯内衣或被消解清空。"""
        l1_banned_styles = {
            "lingerie_panties", "lingerie_bra", "lingerie_lace",
            "bikini_micro", "bikini_classic", "bikini_creative", "bikini_strappy",
            "slime_dress", "legwear_bare",
        }
        for cid in (
            "REP-OUTFIT-01",
            "REP-OUTFIT-02",
            "REP-OUTFIT-03",
            "REP-OUTFIT-04",
            "REP-OUTFIT-05",
            "REP-OUTFIT-06",
            "REP-OUTFIT-07",
            "REP-OUTFIT-08",
            "REP-OUTFIT-09",
        ):
            with self.subTest(case_id=cid):
                res, case = self._run_generator_case(cid)
                eff_lvl = res.nudity_constraint.effective_level or "L1"
                cl_source = [a for a in res.source_atoms if a.source_slot == "clothing"]
                cl_accepted = [a for a in res.atoms if a.source_slot == "clothing"]
                self.assertGreater(len(cl_accepted), 0, f"{cid}: clothing output must not be empty")

                assessment = assess_clothing_completeness(cl_accepted, (), eff_lvl)
                self.assertTrue(
                    assessment.is_complete,
                    f"{cid}: expected complete outfit under {eff_lvl}, got missing={assessment.missing_parts}, texts={[a.text for a in cl_accepted]}",
                )

                # 首项为主款 origin，且所有源片段保留一致 SelectionOrigin 与独立补件 item_id
                first_style_id = cl_source[0].origin.selected_id if cl_source and cl_source[0].origin else None
                self.assertIsNotNone(first_style_id)
                for src_atom in cl_source:
                    self.assertIsNotNone(src_atom.provenance)
                    if src_atom.provenance.kind == "clothing_completion":
                        self.assertEqual(src_atom.source_item_id, src_atom.provenance.item_id)
                        self.assertEqual(src_atom.origin.selected_id, first_style_id)
                        self.assertNotEqual(src_atom.provenance.item_id, first_style_id)

                if eff_lvl == "L1":
                    for a in cl_accepted:
                        item_id = a.provenance.item_id if a.provenance else ""
                        self.assertNotIn(item_id, l1_banned_styles)
                        self.assertNotIn("g-strings", a.text.lower())

                if cid == "REP-OUTFIT-01":
                    self.assertNotIn("sponge bath", res.positive.lower())
                    self.assertNotIn("upright straddle", res.positive.lower())
                elif cid == "REP-OUTFIT-02":
                    self.assertEqual(first_style_id, "convenience_store")
                    self.assertTrue(any(a.provenance and a.provenance.kind == "clothing_completion" for a in cl_source))
                elif cid == "REP-OUTFIT-04":
                    self.assertEqual(first_style_id, "leather_skirt")
                    self.assertTrue(any(a.provenance and a.provenance.kind == "clothing_completion" for a in cl_source))
                elif cid == "REP-OUTFIT-07":
                    self.assertEqual(first_style_id, "footwear_sneakers")
                    self.assertTrue(any(a.provenance and a.provenance.item_id != "footwear_sneakers" for a in cl_accepted))
                elif cid == "REP-OUTFIT-08":
                    self.assertEqual(first_style_id, "knit_sweater")
                    # 服装状态=None：严禁生成 clothing_state/extension，但必须补全下装
                    self.assertFalse(any(a.provenance and a.provenance.kind in ("clothing_state", "clothing_extension") for a in cl_source))
                    self.assertTrue(any(a.provenance and a.provenance.kind == "clothing_completion" for a in cl_source))
                elif cid == "REP-OUTFIT-09":
                    self.assertEqual(first_style_id, "knit_vest")
                    self.assertTrue(any(a.provenance and a.provenance.kind == "clothing_completion" for a in cl_source))
                    cl_lower = " | ".join(a.text.lower() for a in cl_accepted)
                    self.assertNotIn("sheer chiffon", cl_lower)
                    self.assertNotIn("semi-translucent", cl_lower)
                    self.assertNotIn("translucent", cl_lower)

    def test_02_multi_tag_ensemble_subsampling_broken(self) -> None:
        """M2 契约验证：L2 随机状态下 blazer_uniform 子采样保留核心上下装并与 unbuttoned 状态兼容。"""
        case = self.cases["REP-OUTFIT-10"]
        inp = case["inputs"]
        res = _sampler.sample_clothing_result(
            inp["style"],
            inp["state"],
            inp["nudity_level_code"],
            Random(case["seed"]),
        )
        base_texts = [t.text for t in res.base_tags]
        self.assertTrue(assess_clothing_completeness(res.base_tags, (), inp["nudity_level_code"]).is_complete)
        self.assertIn("plaid pleated skirt", base_texts)
        self.assertIn("collared shirt", base_texts)
        self.assertNotEqual(base_texts, case["baseline_actual"]["base_tags"])
        self.assertEqual(res.state_id, "unbuttoned")
        self.assertTrue(is_tags_compatible_with_state(res.base_tags, res.state_id, res.state_tags, style_id=res.style_id))
        self.assertTrue(res.completion_info and res.completion_info.get("repaired_ensemble"))

    def test_03_l3_l4_l6_branch_contradictions_and_l1_cross_slot_leaks(self) -> None:
        """M3 修复契约：L3/L4 单一相容分支与完整衣剔除、L6 下半身景别、L1 跨槽拦截、L5 discarded 保留。"""
        from pathlib import Path
        from lib.conflict_resolver import ConflictResolver
        from lib.models import NUDITY_BRANCHES_BY_LEVEL, NudityConstraint, PromptAtom, SelectionOrigin, SemanticFacts, SpanType, TagProvenance

        data_dir = Path(__file__).resolve().parent.parent / "data"

        # REP-NUDITY-01: L3 仅选取单一相容分支，禁止出现跨分支互相矛盾标签
        res_l3, c_l3 = self._run_generator_case("REP-NUDITY-01")
        self.assertEqual(res_l3.nudity_constraint.effective_level, "L3")
        l3_branch = res_l3.nudity_constraint.selected_branch
        self.assertIn(l3_branch, NUDITY_BRANCHES_BY_LEVEL["L3"])
        l3_nudity_texts = [a.text for a in res_l3.atoms if a.source_slot == "nudity"]
        self.assertNotEqual(l3_nudity_texts, c_l3["baseline_actual"]["nudity_texts"])
        self.assertNotIn("bra pushed up above breasts", l3_nudity_texts)
        self.assertNotIn("panties only", l3_nudity_texts)
        for a in res_l3.atoms:
            if a.source_slot == "nudity" and a.facts and a.facts.nudity_branches:
                self.assertIn(l3_branch, a.facts.nudity_branches)

        # REP-NUDITY-02: L4 选择单一微遮分支且绝不通过 material_penetration 重现连衣裙
        res_l4, c_l4 = self._run_generator_case("REP-NUDITY-02")
        self.assertEqual(res_l4.nudity_constraint.effective_level, "L4")
        self.assertIn(res_l4.nudity_constraint.selected_branch, NUDITY_BRANCHES_BY_LEVEL["L4"])
        l4_clothing = [a for a in res_l4.atoms if a.source_slot == "clothing"]
        self.assertNotIn("wet dress clinging tightly to skin", [a.text for a in l4_clothing])
        for a in l4_clothing:
            topos = set(a.facts.garment_topologies) if a.facts else set()
            self.assertFalse(topos & {"top", "bottom_pants", "bottom_skirt", "outerwear", "one_piece", "ensemble_outfit"})

        # 定向回归：L4 + sheer_chiffon 经 material_penetration 不得改写为 wet dress clinging tightly to skin
        resolver = ConflictResolver(data_dir)
        l4_c = NudityConstraint(
            requested_level="L4",
            effective_level="L4",
            source="ui_explicit",
            selected_branch="l4_panties_only",
        )
        sheer_atom = PromptAtom(
            atom_id="rep02_sheer",
            text="sheer silk fabric",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="sheer_chiffon",
            tag_order=1,
            span_order=0,
            provenance=TagProvenance(item_id="sheer_chiffon", kind="clothing", parent_ids=("sheer_chiffon",)),
            origin=SelectionOrigin(mode="preset", selector="clothing", selected_id="sheer_chiffon"),
            facts=SemanticFacts(garment_topologies=("one_piece",), garment_states=("worn",), pattern_textures=("sheer",)),
        )
        res_m, _, rep_m = resolver.resolve_atoms_with_full_report([sheer_atom], Random(42), nudity_constraint=l4_c)
        self.assertEqual([a.atom_id for a in res_m], [])
        self.assertTrue(any(d.rule_id == "nudity_clothing_conflicts" for d in rep_m.decisions))

        # REP-NUDITY-03: L6 剔除齐腰/特写截断构图
        res_l6, _ = self._run_generator_case("REP-NUDITY-03")
        self.assertEqual(res_l6.nudity_constraint.effective_level, "L6")
        l6_shot_low = " | ".join(a.text.lower() for a in res_l6.atoms if a.source_slot in ("shot_type", "camera_angle"))
        self.assertNotIn("waist-up framing", l6_shot_low)
        self.assertNotIn("medium shot", l6_shot_low)

        # REP-NUDITY-04: L1 随机主题绝不选出露骨主题，显式跨槽裸露饰品/发丝/纹身均被拦截并留痕
        res_leak, c_leak = self._run_generator_case("REP-NUDITY-04")
        pos_low = res_leak.positive.lower()
        for frag in c_leak["baseline_actual"]["leaked_fragments"]:
            self.assertNotIn(frag.lower(), pos_low)
        dropped_by_nudity = [d for d in res_leak.resolution_report.decisions if d.rule_id == "nudity_clothing_conflicts" and d.action == "drop"]
        self.assertGreaterEqual(len(dropped_by_nudity), 3)

        # REP-NUDITY-05: L5 对普通面部特写无全局禁令，且 discarded 叙事在对应分支完整保留
        res_l5, _ = self._run_generator_case("REP-NUDITY-05")
        self.assertEqual(res_l5.nudity_constraint.effective_level, "L5")
        res_l5_disc = self.generator.generate_structured(
            裸露等级="L5 极致全裸 (Full Nude)",
            服装款式="随机 (Random)",
            服装状态="自动联动裸露等级 (Auto Link Nudity)",
            景别构图="close_up",
            prompt_seed=1,
        )
        self.assertEqual(res_l5_disc.nudity_constraint.effective_level, "L5")
        self.assertIn("close", res_l5_disc.positive.lower())

        # REP-NUDITY-06: L4 严格移除所有 VALID_GARMENT_TOPOLOGIES 完整衣物（含 'top' / knit_sweater）
        res_l4_top, _ = self._run_generator_case("REP-NUDITY-06")
        for a in res_l4_top.atoms:
            if a.source_slot == "clothing" and a.facts:
                self.assertFalse(set(a.facts.garment_topologies) & {"top", "bottom_pants", "bottom_skirt", "outerwear", "one_piece", "ensemble_outfit"})
        res_l4_knit = self.generator.generate_structured(
            裸露等级="L4 重点暴露 (Topless / Bottomless)",
            服装款式="knit_sweater",
            服装状态="无 (None)",
            prompt_seed=0,
        )
        self.assertFalse(any(a.source_item_id == "knit_sweater" for a in res_l4_knit.atoms))
        self.assertTrue(any(d.rule_id == "nudity_clothing_conflicts" and d.action == "drop" for d in res_l4_knit.resolution_report.decisions))

    def test_04_presets_ignore_explicit_nudity_level(self) -> None:
        """M3 修复契约：固定预设与随机预设严格受界面 L1..L6 约束并标注已调整，UI 随机时保留 preset_native。"""
        for cid in ("REP-PRESET-01", "REP-PRESET-02"):
            with self.subTest(case_id=cid):
                case = self.cases[cid]
                lvl_a, lvl_b = case["inputs"]["compare_levels"]
                res_a = self.generator.generate_structured(
                    预设模板=case["inputs"]["预设模板"],
                    裸露等级=lvl_a,
                    prompt_seed=case["seed"],
                )
                res_b = self.generator.generate_structured(
                    预设模板=case["inputs"]["预设模板"],
                    裸露等级=lvl_b,
                    prompt_seed=case["seed"],
                )
                self.assertNotEqual(res_a.positive, res_b.positive)
                self.assertEqual(res_a.nudity_constraint.effective_level, "L1")
                self.assertEqual(res_b.nudity_constraint.effective_level, "L5")
                self.assertTrue(res_a.preset_adjusted)
                self.assertIn("已调整", res_a.description)
                self.assertIn("已调整", res_b.description)
                if "shared_violation_tokens_in_l1" in case["baseline_actual"]:
                    for tok in case["baseline_actual"]["shared_violation_tokens_in_l1"]:
                        self.assertNotIn(tok, res_a.positive.lower())
                if cid == "REP-PRESET-02":
                    res_native = self.generator.generate_structured(
                        预设模板=case["inputs"]["预设模板"],
                        裸露等级="随机 (Random)",
                        prompt_seed=case["seed"],
                    )
                    self.assertEqual(res_native.nudity_constraint.source, "preset_native")
                    self.assertFalse(res_native.preset_adjusted)

    def test_05_budget_truncation_splits_complete_outfit(self) -> None:
        """M3 修复契约：优先保留完整套装/核心上下装（仅验收 accepted_atoms），超紧预算明确报错。"""
        from lib.errors import PromptValidationError
        from lib.models import SelectionOrigin
        from lib.outfit_completion import assess_clothing_completeness

        c1 = self.cases["REP-BUDGET-01"]
        n_frags = c1["inputs"]["quality_override_fragment_count"]
        long_quality = ", ".join(f"masterpiece quality detail token{i}" for i in range(n_frags))
        self.assertEqual(len(long_quality.split()), c1["inputs"]["quality_override_word_count"])
        res1 = self.generator.generate_structured(
            裸露等级=c1["inputs"]["裸露等级"],
            服装款式=c1["inputs"]["服装款式"],
            服装状态=c1["inputs"]["服装状态"],
            quality_override=long_quality,
            prompt_seed=c1["seed"],
        )
        cl_acc_1 = [a for a in res1.atoms if a.source_slot == "clothing"]
        self.assertTrue(assess_clothing_completeness(cl_acc_1, (), "L1").is_complete)
        anchor_atom = next(a for a in res1.atoms if a.text == c1["baseline_actual"]["accepted_ensemble_anchor"])
        self.assertIn("ensemble_outfit", anchor_atom.facts.garment_topologies)

        c2 = self.cases["REP-BUDGET-02"]
        slots_inp = c2["inputs"]["slots"]
        frags_2 = {
            "scene_theme": [
                PromptFragment(
                    text=slots_inp["scene_theme"],
                    source_slot="scene_theme",
                    order=0,
                    origin=SelectionOrigin(mode="custom", selector="scene_theme", raw_value=slots_inp["scene_theme"]),
                    facts=SemanticFacts(semantic_role="scene_anchor", space_kind="indoor"),
                )
            ],
            "nudity": [
                PromptFragment(
                    text=slots_inp["nudity"],
                    source_slot="nudity",
                    source_item_id="nudity_l1",
                    order=1,
                    provenance=TagProvenance(item_id="nudity_l1", kind="nudity", semantic_ids=("nudity:L1",)),
                    facts=SemanticFacts(semantic_role="selector"),
                )
            ],
            "clothing": [
                PromptFragment(
                    text=slots_inp["clothing"][0],
                    source_slot="clothing",
                    source_item_id="shirts_blouses",
                    order=2,
                    provenance=TagProvenance(item_id="shirts_blouses", kind="base_clothing"),
                    facts=SemanticFacts(semantic_role="selector", garment_topologies=("top",), piece_role="top", covered_regions=("upper_torso",)),
                ),
                PromptFragment(
                    text=slots_inp["clothing"][1],
                    source_slot="clothing",
                    source_item_id="pleated_skirt",
                    order=3,
                    provenance=TagProvenance(item_id="pleated_skirt", kind="clothing_completion"),
                    facts=SemanticFacts(semantic_role="selector", garment_topologies=("bottom_skirt",), piece_role="bottom", covered_regions=("lower_torso",)),
                ),
            ],
        }
        res2 = _assembler.assemble_slots(
            frags_2,
            rng=Random(c2["seed"]),
            max_words=c2["inputs"]["max_words"],
        )
        cl_acc_2 = [a for a in res2.accepted_atoms if a.source_slot == "clothing"]
        self.assertEqual([a.text for a in cl_acc_2], slots_inp["clothing"])
        self.assertTrue(assess_clothing_completeness(cl_acc_2, (), "L1").is_complete)
        tight_frags = dict(frags_2)
        tight_frags["scene_theme"] = [
            PromptFragment(
                text="cozy sunlit wooden bedroom",
                source_slot="scene_theme",
                order=0,
                origin=SelectionOrigin(mode="custom", selector="scene_theme", raw_value="cozy sunlit wooden bedroom"),
                facts=SemanticFacts(semantic_role="scene_anchor", space_kind="indoor"),
            )
        ]
        with self.assertRaises(PromptValidationError) as ctx_tight:
            _assembler.assemble_slots(tight_frags, rng=Random(c2["seed"]), max_words=6)
        self.assertEqual(ctx_tight.exception.error_code, "insufficient_budget_for_complete_outfit")

        c3 = self.cases["REP-BUDGET-03"]
        n_frags_3 = c3["inputs"]["quality_override_fragment_count"]
        long_quality_3 = ", ".join(f"masterpiece quality detail token{i}" for i in range(n_frags_3))
        self.assertEqual(len(long_quality_3.split()), c3["inputs"]["quality_override_word_count"])
        res3 = self.generator.generate_structured(
            裸露等级=c3["inputs"]["裸露等级"],
            服装款式=c3["inputs"]["服装款式"],
            服装状态=c3["inputs"]["服装状态"],
            quality_override=long_quality_3,
            prompt_seed=c3["seed"],
        )
        acc_cl_3 = [a for a in res3.atoms if a.source_slot == "clothing"]
        self.assertTrue(assess_clothing_completeness(acc_cl_3, (), "L1").is_complete)
        self.assertIn("sleeveless cheerleader crop top", [a.text for a in acc_cl_3])
        self.assertIn("mini pleated skirt", [a.text for a in acc_cl_3])

    def test_06_exempt_entry_compatibility_l1_none_and_explicit_single(self) -> None:
        """验证豁免入口兼容性：L1 + 服装=None 跳过槽位允许通过，显式单品不自动补全亦不报错。"""
        res_none, c_none = self._run_generator_case("COMPAT-EXEMPT-01")
        self.assertEqual(
            [a.text for a in res_none.atoms if a.source_slot == "clothing"],
            c_none["baseline_actual"]["clothing_texts"],
        )
        self.assertTrue(res_none.positive)

        c_exp = self.cases["COMPAT-EXEMPT-02"]
        for item in c_exp["inputs"]["cases"]:
            with self.subTest(style=item["服装款式"]):
                res_exp = self.generator.generate_structured(
                    裸露等级=item["裸露等级"],
                    服装款式=item["服装款式"],
                    服装状态=item["服装状态"],
                    prompt_seed=c_exp["seed"],
                )
                self.assertEqual(
                    [a.text for a in res_exp.atoms if a.source_slot == "clothing"],
                    item["expected_clothing_texts"],
                )


if __name__ == "__main__":
    unittest.main()
