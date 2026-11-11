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

                # 首项为主款 origin，且所有源片段保留一致 provenance 与独立补件 ID
                first_style_id = cl_source[0].origin.selected_id if cl_source and cl_source[0].origin else None
                self.assertIsNotNone(first_style_id)
                for src_atom in cl_source:
                    self.assertIsNotNone(src_atom.provenance)
                    if src_atom.provenance.kind == "clothing_completion":
                        self.assertEqual(src_atom.source_item_id, src_atom.provenance.item_id)
                        self.assertEqual(src_atom.origin.selected_id, src_atom.provenance.item_id)
                        self.assertNotEqual(src_atom.origin.selected_id, first_style_id)

                if eff_lvl == "L1":
                    for a in cl_accepted:
                        item_id = a.provenance.item_id if a.provenance else ""
                        self.assertNotIn(item_id, l1_banned_styles)
                        self.assertNotIn("g-strings", a.text.lower())

                if cid in ("REP-OUTFIT-01", "REP-OUTFIT-02"):
                    self.assertEqual(first_style_id, "knit_sweater")
                    self.assertTrue(any(a.provenance and a.provenance.kind == "clothing_completion" for a in cl_source))
                elif cid == "REP-OUTFIT-04":
                    self.assertEqual(first_style_id, "pencil_skirt")
                    self.assertTrue(any(a.provenance and a.provenance.item_id == "t_shirt" for a in cl_accepted))
                elif cid == "REP-OUTFIT-07":
                    self.assertEqual(first_style_id, "footwear_sneakers")
                    self.assertTrue(any(a.provenance and a.provenance.item_id != "footwear_sneakers" for a in cl_accepted))
                elif cid == "REP-OUTFIT-08":
                    self.assertEqual(first_style_id, "armored_skirt")
                    # 服装状态=None：严禁生成 clothing_state/extension，但必须补全上装
                    self.assertFalse(any(a.provenance and a.provenance.kind in ("clothing_state", "clothing_extension") for a in cl_source))
                    self.assertTrue(any(a.provenance and a.provenance.kind == "clothing_completion" for a in cl_source))
                elif cid == "REP-OUTFIT-09":
                    self.assertEqual(first_style_id, "knit_sweater")
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
        """复现 L3/L4 分支冲突与未删净完整衣、L6 齐腰截断、以及 L1 跨槽位暴露泄露。"""
        res_l3, c_l3 = self._run_generator_case("REP-NUDITY-01")
        self.assertEqual([a.text for a in res_l3.atoms if a.source_slot == "nudity"], c_l3["baseline_actual"]["nudity_texts"])
        self.assertEqual([a.text for a in res_l3.atoms if a.source_slot == "clothing"], c_l3["baseline_actual"]["clothing_texts"])

        res_l4, c_l4 = self._run_generator_case("REP-NUDITY-02")
        self.assertEqual([a.text for a in res_l4.atoms if a.source_slot == "nudity"], c_l4["baseline_actual"]["nudity_texts"])
        # M1 已清理 linkage L4 general_tags 的伪造 underwear/wet_clinging 事实：
        # 原始连衣裙仍被 drop，不再触发 material_penetration 误改写为 wet dress；
        # 剩余 L4 局部遮挡分支与 sfw_exposure 扩展冲突留待 M2/M3 统一消解。
        decisions = res_l4.resolution_report.decisions
        drop_step, replace_step = c_l4["baseline_actual"]["resolution_chain"]
        dropped_texts = [d.before_text for d in decisions if d.rule_id == drop_step["rule_id"] and d.action == drop_step["action"]]
        self.assertEqual(dropped_texts, drop_step["dropped_texts"])
        replaced_decisions = [d for d in decisions if d.rule_id == replace_step["rule_id"] and d.action == replace_step["action"]]
        self.assertEqual(replaced_decisions, [])
        l4_clothing_texts = [a.text for a in res_l4.atoms if a.source_slot == "clothing"]
        self.assertNotIn(replace_step["produced_text"], l4_clothing_texts)
        self.assertIn("topless", l4_clothing_texts)
        self.assertIn("bare breasts exposed", l4_clothing_texts)

        res_l6, c_l6 = self._run_generator_case("REP-NUDITY-03")
        self.assertEqual([a.text for a in res_l6.atoms if a.source_slot == "nudity"], c_l6["baseline_actual"]["nudity_texts"])
        self.assertEqual([a.text for a in res_l6.atoms if a.source_slot == "shot_type"], c_l6["baseline_actual"]["shot_type_texts"])

        res_leak, c_leak = self._run_generator_case("REP-NUDITY-04")
        pos_low = res_leak.positive.lower()
        for frag in c_leak["baseline_actual"]["leaked_fragments"]:
            self.assertIn(frag.lower(), pos_low)

        res_l5, c_l5 = self._run_generator_case("REP-NUDITY-05")
        self.assertEqual([a.text for a in res_l5.atoms if a.source_slot == "nudity"], c_l5["baseline_actual"]["nudity_texts"])
        self.assertEqual([a.text for a in res_l5.source_atoms if a.source_slot == "clothing"], c_l5["baseline_actual"]["source_clothing_texts"])
        self.assertEqual([a.text for a in res_l5.atoms if a.source_slot == "clothing"], c_l5["baseline_actual"]["accepted_clothing_texts"])

        res_l4_top, c_l4_top = self._run_generator_case("REP-NUDITY-06")
        self.assertEqual(len(res_l4_top.resolution_report.decisions), c_l4_top["baseline_actual"]["decision_count"])
        kept_top_texts = [a.text for a in res_l4_top.atoms if a.source_slot == "clothing" and "top" in a.facts.garment_topologies]
        self.assertEqual(kept_top_texts, c_l4_top["baseline_actual"]["retained_top_texts"])

    def test_04_presets_ignore_explicit_nudity_level(self) -> None:
        """复现固定预设与随机预设均跳过界面显式裸露等级选择（L1 与 L5 逐字相同）。"""
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
                self.assertEqual(res_a.positive, res_b.positive)
                if "shared_violation_tokens_in_l1" in case["baseline_actual"]:
                    for tok in case["baseline_actual"]["shared_violation_tokens_in_l1"]:
                        self.assertIn(tok, res_a.positive.lower())
                if "preset_desc" in case["baseline_actual"]:
                    self.assertEqual(res_a.description, case["baseline_actual"]["preset_desc"])

    def test_05_budget_truncation_splits_complete_outfit(self) -> None:
        """验证套装锚点不误报对照，并复现无锚点分体穿搭在预算裁剪后真正缺件。"""
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
        self.assertEqual(
            [a.text for a in res1.atoms if a.source_slot == "clothing"],
            c1["baseline_actual"]["accepted_clothing"],
        )
        anchor_atom = next(a for a in res1.atoms if a.text == c1["baseline_actual"]["accepted_ensemble_anchor"])
        self.assertIn("ensemble_outfit", anchor_atom.facts.garment_topologies)
        self.assertEqual(
            [a.text for a in res1.budget_filtered_atoms if a.source_slot == "clothing"],
            c1["baseline_actual"]["budget_filtered_clothing"],
        )

        c2 = self.cases["REP-BUDGET-02"]
        slots_inp = c2["inputs"]["slots"]
        res2 = _assembler.assemble_slots(
            {
                "scene_theme": [
                    PromptFragment(
                        text=slots_inp["scene_theme"],
                        source_slot="scene_theme",
                        order=0,
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
                        facts=SemanticFacts(semantic_role="selector", garment_topologies=("top",)),
                    ),
                    PromptFragment(
                        text=slots_inp["clothing"][1],
                        source_slot="clothing",
                        source_item_id="pleated_skirt",
                        order=3,
                        provenance=TagProvenance(item_id="pleated_skirt", kind="base_clothing"),
                        facts=SemanticFacts(semantic_role="selector", garment_topologies=("bottom_skirt",)),
                    ),
                ],
            },
            rng=Random(c2["seed"]),
            max_words=c2["inputs"]["max_words"],
        )
        self.assertEqual(res2.prompt, c2["baseline_actual"]["prompt"])
        self.assertEqual([a.text for a in res2.budget_filtered_atoms], c2["baseline_actual"]["budget_filtered"])

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
        self.assertEqual([a.text for a in acc_cl_3], c3["baseline_actual"]["accepted_clothing"])
        self.assertEqual([list(a.facts.garment_topologies) for a in acc_cl_3], c3["baseline_actual"]["accepted_clothing_topologies"])
        self.assertEqual(
            [a.text for a in res3.budget_filtered_atoms if a.source_slot == "clothing"],
            c3["baseline_actual"]["budget_filtered_clothing"],
        )

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
