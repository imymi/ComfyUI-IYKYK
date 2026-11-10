"""
test_v1_2_1_m1_facts_and_constraints.py — IYKYK v1.2.1 M1 阶段单元测试

覆盖：
1. NudityConstraint 内部等级约束契约、序列化往返与分支一致性校验；
2. SemanticFacts 新增 6 个四态字段（piece_role / covered_regions / coverage_delta / exposed_regions / allowed_nudity_levels / nudity_branches）的四态保留、合并、序列化与反例校验；
3. normalize_nudity_level_code 与旧版小写 "l1" 调用向后兼容；
4. DataSampler.resolve_nudity_constraint 与三入口（Generator / PresetBrowser / CustomSlotCombiner）向 GenerationResult / AssemblyResult / ResolutionReport 的透传（且 PresetBrowser / CustomSlotCombiner 不暗增默认 L1）；
5. nudity_levels.json / clothing.json / 跨槽位词库的 M1 事实标注准确性及良性词防误报标注；
6. validate_data.py --strict 负向变异门禁测试。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import shutil
import tempfile
import unittest

from lib.conflict_resolver import ConflictResolver
from lib.models import (
    NudityConstraint,
    PromptAtom,
    SemanticFacts,
    SpanType,
    TagProvenance,
    UNSPECIFIED,
    VALID_NUDITY_LEVELS,
    normalize_nudity_level_code,
)
from nodes import (
    IYKYKCustomSlotCombiner,
    IYKYKPresetBrowser,
    IYKYKPromptGenerator,
    _sampler,
)
from scripts.validate_data import SCHEMAS_DIR, validate_all


REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"


class TestV121M1FactsAndConstraints(unittest.TestCase):
    """M1 结构化事实与统一等级约束验证套件。"""

    def test_01_nudity_constraint_contract_and_roundtrip(self) -> None:
        c = NudityConstraint(
            requested_level="L3",
            effective_level="L3",
            source="ui_explicit",
            selected_branch="l3_open_shirt_breasts",
            yielded_to_override=False,
            yield_reason=None,
            raw_requested_value="L3 半裸诱惑 (Half Nude)",
        )
        d = c.to_dict()
        restored = NudityConstraint.from_dict(d)
        self.assertEqual(restored, c)
        self.assertIsNone(NudityConstraint.from_dict(None))

        with self.assertRaises(ValueError):
            NudityConstraint(effective_level="L7")
        with self.assertRaises(ValueError):
            NudityConstraint(source="bogus_source")
        with self.assertRaises(ValueError):
            NudityConstraint(effective_level="L1", selected_branch="l4_panties_only")
        with self.assertRaises(KeyError):
            NudityConstraint.from_dict({"source": "ui_none", "extra_bad": 1})
        with self.assertRaises(TypeError):
            NudityConstraint(yielded_to_override="yes")  # type: ignore[arg-type]

    def test_02_normalize_level_code_and_legacy_lowercase_compat(self) -> None:
        self.assertEqual(normalize_nudity_level_code("L1"), "L1")
        self.assertEqual(normalize_nudity_level_code("l1"), "L1")
        self.assertEqual(normalize_nudity_level_code("4"), "L4")
        self.assertEqual(normalize_nudity_level_code("L5 完全裸露 (Full Nude)"), "L5")
        self.assertEqual(normalize_nudity_level_code("无 (None)"), "none")
        self.assertEqual(normalize_nudity_level_code("随机 (Random)"), "random")
        self.assertEqual(normalize_nudity_level_code(UNSPECIFIED), "unspecified")
        self.assertIsNone(normalize_nudity_level_code(None))

        # 向后兼容 test_m4_batch1_ingestion.py 的小写 "l1" 直接调用
        c_res = _sampler.sample_clothing_result("jk_seifuku", "none", "l1", Random(7))
        self.assertTrue(c_res.base_tags)
        _, constraint = _sampler.resolve_nudity_constraint("l1", Random(7))
        self.assertEqual(constraint.requested_level, "L1")
        self.assertEqual(constraint.effective_level, "L1")
        self.assertEqual(constraint.source, "ui_explicit")

    def test_03_semantic_facts_new_fields_four_state_merge_and_validation(self) -> None:
        absent = SemanticFacts()
        self.assertEqual(absent.to_dict(), {})

        parent = SemanticFacts(
            piece_role="one_piece",
            covered_regions=("upper_torso", "lower_torso"),
            coverage_delta="preserve",
            exposed_regions=("none",),
            allowed_nudity_levels=("L1", "L2"),
            nudity_branches=("l1_full_clothed",),
        )
        parent.validate()
        p_round = SemanticFacts.from_dict(parent.to_dict())
        self.assertEqual(p_round.piece_role, "one_piece")
        self.assertEqual(p_round.covered_regions, ("lower_torso", "upper_torso"))
        self.assertEqual(p_round.coverage_delta, "preserve")
        self.assertEqual(p_round.exposed_regions, ("none",))
        self.assertEqual(p_round.allowed_nudity_levels, ("L1", "L2"))
        self.assertEqual(p_round.nudity_branches, ("l1_full_clothed",))

        child_clear = SemanticFacts(
            piece_role="none",
            covered_regions=("none",),
            coverage_delta="uncover_all",
            exposed_regions=("full_nude",),
            allowed_nudity_levels=("L5",),
            nudity_branches=(),
        )
        merged = parent.merge(child_clear)
        self.assertEqual(merged.piece_role, "none")
        self.assertEqual(merged.covered_regions, ("none",))
        self.assertEqual(merged.coverage_delta, "uncover_all")
        self.assertEqual(merged.exposed_regions, ("full_nude",))
        self.assertEqual(merged.allowed_nudity_levels, ("L5",))
        self.assertEqual(merged.nudity_branches, ())

        # 反例校验
        with self.assertRaises(ValueError):
            SemanticFacts(piece_role="none", covered_regions=("upper_torso",)).validate()
        with self.assertRaises(ValueError):
            SemanticFacts(coverage_delta="uncover_all", garment_states=("worn",)).validate()
        with self.assertRaises(ValueError):
            SemanticFacts(allowed_nudity_levels=("L1",), nudity_branches=("l4_panties_only",)).validate()
        with self.assertRaises(ValueError):
            SemanticFacts(exposed_regions=("none", "breasts")).validate()
        with self.assertRaises(ValueError):
            SemanticFacts(piece_role="invalid_role").validate()

    def test_04_resolve_nudity_constraint_modes_and_propagation(self) -> None:
        _, c_none = _sampler.resolve_nudity_constraint("无 (None)", Random(1))
        self.assertEqual((c_none.requested_level, c_none.effective_level, c_none.source), ("none", None, "ui_none"))

        res_rand, c_rand = _sampler.resolve_nudity_constraint("随机 (Random)", Random(1))
        self.assertIsNotNone(res_rand)
        self.assertEqual(c_rand.requested_level, "random")
        self.assertIn(c_rand.effective_level, VALID_NUDITY_LEVELS)
        self.assertEqual(c_rand.source, "ui_random")

        res_p_rand, c_p_rand = _sampler.resolve_nudity_constraint("随机 (Random)", Random(1), has_preset=True)
        self.assertIsNone(res_p_rand)
        self.assertEqual((c_p_rand.requested_level, c_p_rand.effective_level, c_p_rand.source), ("random", None, "preset_native"))

        res_p_exp, c_p_exp = _sampler.resolve_nudity_constraint("L5 极致全裸 (Full Nude)", Random(1), has_preset=True)
        self.assertIsNone(res_p_exp)
        self.assertEqual((c_p_exp.requested_level, c_p_exp.effective_level, c_p_exp.source), ("L5", "L5", "ui_explicit"))

        _, c_ext = _sampler.resolve_nudity_constraint(
            "L1 包裹暗示 (Fully Clothed / Suggestive)",
            Random(1),
            external_overrides={"nudity": "L5"},
        )
        self.assertEqual(c_ext.effective_level, "L5")
        self.assertEqual(c_ext.source, "external_override")
        self.assertTrue(c_ext.yielded_to_override)
        self.assertEqual(c_ext.yield_reason, "external_nudity_override")

        gen = IYKYKPromptGenerator()
        g_ui = gen.generate_structured(
            裸露等级="L2 差分微露 (Partially Exposed)",
            服装款式="水手服 (JK Sailor Suit)",
            服装状态="无 (None)",
            prompt_seed=11,
        )
        self.assertIsNotNone(g_ui.nudity_constraint)
        self.assertEqual(g_ui.nudity_constraint.effective_level, "L2")
        self.assertEqual(g_ui.nudity_constraint.source, "ui_explicit")
        self.assertEqual(g_ui.resolution_report.nudity_constraint, g_ui.nudity_constraint)

        g_ext = gen.generate_structured(
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="水手服 (JK Sailor Suit)",
            服装状态="无 (None)",
            nudity_override="completely naked, nude",
            prompt_seed=11,
        )
        self.assertIsNotNone(g_ext.nudity_constraint)
        self.assertTrue(g_ext.nudity_constraint.yielded_to_override)
        self.assertEqual(g_ext.nudity_constraint.source, "external_override")
        self.assertEqual(g_ext.nudity_constraint.effective_level, "L5")

        # PresetBrowser 与 CustomSlotCombiner 保持不暗增默认 L1
        presets = _sampler.list_preset_names()
        browser = IYKYKPresetBrowser()
        b_res = browser.browse_structured(
            预设模板=presets[0],
            风格配方="无 (None)",
            画质等级="高画质 (High)",
            prompt_seed=5,
        )
        self.assertIsNotNone(b_res.nudity_constraint)
        self.assertEqual(b_res.nudity_constraint.requested_level, "unspecified")
        self.assertIsNone(b_res.nudity_constraint.effective_level)
        self.assertEqual(b_res.nudity_constraint.source, "preset_native")

        combiner = IYKYKCustomSlotCombiner()
        c_out = combiner.combine_structured(
            prompt_seed=5,
            clothing="white shirt, pleated skirt",
        )
        self.assertIsNotNone(c_out.nudity_constraint)
        self.assertEqual(c_out.nudity_constraint.requested_level, "unspecified")
        self.assertIsNone(c_out.nudity_constraint.effective_level)
        self.assertEqual(c_out.nudity_constraint.source, "unconstrained")

    def test_05_catalog_facts_alignment_and_false_positive_protection(self) -> None:
        nudity_doc = json.loads((DATA_DIR / "nudity_levels.json").read_text(encoding="utf-8"))
        nudity_tags = {
            t["id"]: t
            for lvl in nudity_doc["nudity_levels"]
            for t in lvl["tags"]
        }
        # 原先误标 upper_body 的代表词条已修正
        self.assertNotIn("upper_body", nudity_tags["nudity_l3__tag_002"]["facts"]["visible_regions"])
        self.assertEqual(nudity_tags["nudity_l4__tag_001"]["facts"]["nudity_branches"], ["l4_hosiery_footwear_only"])
        self.assertEqual(nudity_tags["nudity_l5__tag_005"]["facts"]["visible_regions"], ["full_body"])
        self.assertEqual(nudity_tags["nudity_l6__tag_002"]["facts"]["visible_regions"], ["intimate_lower_body"])
        self.assertEqual(nudity_tags["nudity_l6__tag_010"]["facts"]["nudity_branches"], ["l6_rear_bent_display"])

        clothing_doc = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        cat_tags = {
            t["id"]: t
            for c in clothing_doc["categories"]
            for t in c["tags"]
        }
        self.assertEqual(cat_tags["camisole_slip__tag_001"]["facts"]["piece_role"], "top")
        self.assertEqual(cat_tags["apron_dress__tag_000"]["facts"]["piece_role"], "outerwear")
        self.assertEqual(cat_tags["apron_dress__tag_000"]["facts"]["garment_topologies"], ["one_piece"])
        self.assertEqual(cat_tags["silk_robe__tag_003"]["facts"]["piece_role"], "accessory")
        self.assertNotIn("L1", cat_tags["slime_dress__tag_001"]["facts"]["allowed_nudity_levels"])
        self.assertNotIn("L1", cat_tags["hanbok__ext_01103_flowing_heliotrope_sheer_gossa"]["facts"]["allowed_nudity_levels"])
        self.assertEqual(cat_tags["bottom_jeans_casual__tag_000"]["facts"]["piece_role"], "bottom")
        self.assertEqual(cat_tags["bottom_jeans_casual__tag_000"]["facts"]["garment_topologies"], ["bottom_pants"])
        self.assertEqual(cat_tags["legwear_pantyhose__tag_000"]["facts"]["piece_role"], "legwear")
        self.assertEqual(cat_tags["footwear_boots__tag_000"]["facts"]["piece_role"], "footwear")

        l4_gen = clothing_doc["clothing_nudity_linkage"]["L4"]["general_tags"]
        for t in l4_gen:
            self.assertNotIn("wet_clinging", t["facts"].get("garment_states", []))
        l5_gen = clothing_doc["clothing_nudity_linkage"]["L5"]["general_tags"]
        for t in l5_gen:
            self.assertNotIn("torn", t["facts"].get("garment_states", []))
        self.assertEqual(
            clothing_doc["clothing_nudity_linkage"]["L5"]["style_overrides"]["qipao"][1]["facts"]["piece_role"],
            "none",
        )
        self.assertEqual(
            clothing_doc["clothing_nudity_linkage"]["L5"]["style_overrides"]["qipao"][1]["facts"]["coverage_delta"],
            "uncover_all",
        )

        # 跨槽位暴露与良性词防误报
        shots = json.loads((DATA_DIR / "shot_types.json").read_text(encoding="utf-8"))
        shot_tags = {t["id"]: t for s in shots["shot_types"] + shots["camera_angles"] for t in s["tags"]}
        self.assertEqual(shot_tags["erotic_close_up__tag_002"]["facts"]["visible_regions"], ["lower_body", "intimate_lower_body"])
        self.assertEqual(shot_tags["between_legs_angle__tag_003"]["facts"]["allowed_nudity_levels"], ["L5", "L6"])

        makeup = json.loads((DATA_DIR / "makeup.json").read_text(encoding="utf-8"))
        makeup_tags = {t["id"]: t for c in makeup["categories"] for t in c["tags"]}
        self.assertEqual(makeup_tags["clear_water_bare__tag_003"]["facts"]["exposed_regions"], ["none"])
        self.assertIn("L1", makeup_tags["clear_water_bare__tag_003"]["facts"]["allowed_nudity_levels"])

        recipes = json.loads((DATA_DIR / "style_recipes.json").read_text(encoding="utf-8"))
        rec_frags = {f["id"]: f for r in recipes["recipes"] for f in r["fragments"]}
        self.assertEqual(rec_frags["recipe_cctv_surveillance__frag_009"]["facts"]["exposed_regions"], ["none"])
        self.assertIn("L1", rec_frags["recipe_monochrome_noir__frag_008"]["facts"]["allowed_nudity_levels"])

        # ConflictResolver 在无 explicit nudity 文本但携带 ui_explicit constraint 时仍感知有效等级
        resolver = ConflictResolver(data_dir=DATA_DIR)
        _, _, rep = resolver.resolve_atoms_with_full_report(
            [
                PromptAtom(
                    atom_id="c1",
                    text="cotton bikini underwear",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing",
                    source_item_id="lingerie_lace",
                    tag_order=0,
                    span_order=0,
                    facts=SemanticFacts(semantic_role="selector", garment_topologies=("underwear",), piece_role="underwear"),
                    provenance=TagProvenance(item_id="lingerie_lace", kind="base_clothing"),
                )
            ],
            nudity_constraint=NudityConstraint(requested_level="L1", effective_level="L1", source="ui_explicit"),
        )
        self.assertEqual(rep.nudity_constraint.effective_level, "L1")

    def test_06_validate_data_negative_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_data = Path(tmpdir) / "data"
            shutil.copytree(DATA_DIR, tmp_data)

            # 1. 缺失 nudity_branches 必须报错
            n_path = tmp_data / "nudity_levels.json"
            n_doc = json.loads(n_path.read_text(encoding="utf-8"))
            n_doc["nudity_levels"][0]["tags"][0]["facts"].pop("nudity_branches")
            n_path.write_text(json.dumps(n_doc, ensure_ascii=False, indent=2), encoding="utf-8")

            # 2. 缺失 piece_role 与残留 wet_clinging 必须报错
            c_path = tmp_data / "clothing.json"
            c_doc = json.loads(c_path.read_text(encoding="utf-8"))
            c_doc["categories"][0]["tags"][0]["facts"].pop("piece_role")
            c_doc["clothing_nudity_linkage"]["L4"]["general_tags"][0]["facts"]["garment_states"] = ["wet_clinging"]
            c_path.write_text(json.dumps(c_doc, ensure_ascii=False, indent=2), encoding="utf-8")

            res = validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
            self.assertFalse(res.is_valid)
            joined = "\n".join(res.errors)
            self.assertIn("missing non-empty nudity_branches", joined)
            self.assertIn("missing or invalid piece_role", joined)
            self.assertIn("wet_clinging", joined)


if __name__ == "__main__":
    unittest.main()
