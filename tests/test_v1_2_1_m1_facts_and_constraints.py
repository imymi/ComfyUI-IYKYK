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
from lib.errors import PromptValidationError
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
        # P2-4: 透视睡衣/内衣/连体袜套装条目不可仅标完整 ensemble 而允许 L1，必须携带等级限制与暴露事实
        t_03324 = cat_tags["ensemble__cloth_03324_nightgown_see_through_by_sourc"]
        self.assertNotIn("L1", t_03324["facts"]["allowed_nudity_levels"])
        self.assertEqual(t_03324["facts"].get("exposed_regions"), ["underwear_bra", "underwear_panties"])
        self.assertNotIn("L1", cat_tags["ensemble__cloth_01980_bikini_string_bikini_frilled_b"]["facts"]["allowed_nudity_levels"])
        self.assertNotIn("L1", cat_tags["ensemble__cloth_01983_lingerie_underwear_underwear_o"]["facts"]["allowed_nudity_levels"])
        self.assertEqual(cat_tags["tops_tanks__ext_03679_bodystocking"]["facts"]["piece_role"], "underwear")
        self.assertEqual(cat_tags["tops_tanks__ext_03679_bodystocking"]["facts"]["covered_regions"], ["intimate_upper", "intimate_lower", "legs"])
        self.assertNotIn("exposed_regions", cat_tags["tops_tanks__ext_03679_bodystocking"]["facts"])
        self.assertNotIn("L1", cat_tags["tops_tanks__ext_03679_bodystocking"]["facts"]["allowed_nudity_levels"])
        self.assertNotIn("L1", cat_tags["tops_tanks__ext_03747_underwear"]["facts"]["allowed_nudity_levels"])
        self.assertNotIn("exposed_regions", cat_tags["tops_tanks__ext_03747_underwear"]["facts"])
        self.assertNotIn("L1", cat_tags["bottoms_specialty__ext_00309_cheeky_underwear"]["facts"]["allowed_nudity_levels"])

        # 普通连体袜衣 vs 明确开胸/透明/开裆对照（lingerie_wardrobe）
        ling_tags = {
            t["id"]: t
            for grp in clothing_doc.get("lingerie_wardrobe", [])
            for t in grp.get("tags", [])
        }
        self.assertEqual(ling_tags["bodystocking__tag_000"]["facts"]["covered_regions"], ["intimate_upper", "intimate_lower", "legs"])
        self.assertNotIn("exposed_regions", ling_tags["bodystocking__tag_000"]["facts"])
        self.assertEqual(ling_tags["bodystocking__tag_001"]["facts"]["covered_regions"], ["legs"])
        self.assertEqual(ling_tags["bodystocking__tag_001"]["facts"].get("exposed_regions"), ["breasts"])
        self.assertEqual(ling_tags["bodystocking__tag_002"]["facts"]["covered_regions"], ["intimate_upper", "legs"])
        self.assertEqual(ling_tags["bodystocking__tag_002"]["facts"].get("exposed_regions"), ["genitals"])
        self.assertNotIn("intimate_upper", ling_tags["oriental_cheongsam__tag_001"]["facts"]["covered_regions"])
        self.assertEqual(ling_tags["oriental_cheongsam__tag_001"]["facts"].get("exposed_regions"), ["breasts"])

        # M1 复审 P2: 按服装结构区分 covered_regions / exposed_regions，无依据事实保持未指定且不自相矛盾
        t_bodysuit = cat_tags["ensemble__cloth_01470_black_latex_bodysuit_worn_as_c"]
        self.assertEqual(t_bodysuit["facts"]["piece_role"], "underwear")
        self.assertEqual(t_bodysuit["facts"]["covered_regions"], ["intimate_upper", "intimate_lower", "legs"])
        self.assertEqual(t_bodysuit["facts"].get("exposed_regions"), ["underwear_panties"])
        for unexpected_exp in ("midriff", "underwear_bra", "cleavage", "thigh_leg"):
            self.assertNotIn(unexpected_exp, t_bodysuit["facts"].get("exposed_regions", []))
        self.assertNotIn("L1", t_bodysuit["facts"]["allowed_nudity_levels"])

        t_cupless = cat_tags["ensemble__cloth_01478_black_leather_cupless_corset_w"]
        self.assertEqual(t_cupless["facts"]["piece_role"], "underwear")
        self.assertEqual(t_cupless["facts"]["covered_regions"], ["intimate_lower", "legs"])
        self.assertNotIn("intimate_upper", t_cupless["facts"]["covered_regions"])
        self.assertEqual(t_cupless["facts"].get("exposed_regions"), ["breasts", "underwear_panties"])
        for unexpected_exp in ("underwear_bra", "midriff", "cleavage", "thigh_leg"):
            self.assertNotIn(unexpected_exp, t_cupless["facts"].get("exposed_regions", []))

        # 开口且无遮挡 vs 开口但有不透明衣物遮挡对照
        t_crotchless_unoccluded = cat_tags["ensemble__cloth_01453_black_and_red_leather_bralette"]
        self.assertNotIn("intimate_lower", t_crotchless_unoccluded["facts"]["covered_regions"])
        self.assertIn("genitals", t_crotchless_unoccluded["facts"].get("exposed_regions", []))

        for occluded_tid in (
            "ensemble__cloth_01460_black_crocodile_embossed_leath",
            "ensemble__cloth_01513_black_velvet_bustier_black_lac",
        ):
            t_occ = cat_tags[occluded_tid]
            self.assertEqual(t_occ["facts"]["covered_regions"], ["intimate_upper", "intimate_lower", "legs"])
            self.assertNotIn("genitals", t_occ["facts"].get("exposed_regions", []))
            self.assertEqual(t_occ["facts"].get("exposed_regions"), ["underwear_panties"])

        t_01629 = cat_tags["ensemble__cloth_01629_deep_plum_velvet_underbust_cor"]
        self.assertEqual(t_01629["facts"]["covered_regions"], ["intimate_lower", "legs"])
        self.assertNotIn("intimate_upper", t_01629["facts"]["covered_regions"])
        self.assertEqual(t_01629["facts"].get("exposed_regions"), ["breasts", "underwear_panties"])
        self.assertNotIn("genitals", t_01629["facts"].get("exposed_regions", []))

        t_underbust_crotchless = cat_tags["ensemble__cloth_01517_black_velvet_underbust_corset"]
        self.assertEqual(t_underbust_crotchless["facts"]["covered_regions"], ["legs"])
        self.assertEqual(t_underbust_crotchless["facts"].get("exposed_regions"), ["breasts", "genitals"])
        self.assertNotIn("intimate_upper", cat_tags["lingerie_bra__ext_03685_cage_bra"]["facts"]["covered_regions"])
        self.assertEqual(cat_tags["lingerie_bra__ext_03685_cage_bra"]["facts"].get("exposed_regions"), ["breasts"])
        self.assertNotIn("intimate_upper", cat_tags["lingerie_bra__ext_03717_pasties"]["facts"]["covered_regions"])
        self.assertEqual(cat_tags["lingerie_bra__ext_03717_pasties"]["facts"].get("exposed_regions"), ["breasts"])

        # 纯材质透视（无文本明示部位）保留禁 L1 且不补造 cleavage / thigh_leg
        for sheer_tid in ("qipao__ext_00926_airy_rose_pink_sheer_gossamer", "lehenga__ext_01421_whimsical_white_lehenga_crafte"):
            self.assertNotIn("L1", cat_tags[sheer_tid]["facts"]["allowed_nudity_levels"])
            self.assertNotIn("exposed_regions", cat_tags[sheer_tid]["facts"])

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
        # 0. 正向对照：开口单品若同组有不透明衣物遮挡（01460 连裤袜、或内存中叠加不透明文胸），允许恢复覆盖并取消暴露
        with tempfile.TemporaryDirectory() as pos_tmpdir:
            pos_data = Path(pos_tmpdir) / "data"
            shutil.copytree(DATA_DIR, pos_data)
            pos_c_path = pos_data / "clothing.json"
            pos_c_doc = json.loads(pos_c_path.read_text(encoding="utf-8"))
            pos_cec = next(c for c in pos_c_doc["categories"] if c["id"] == "clothing_ensemble_combos")

            t_01460_pos = next(t for t in pos_cec["tags"] if t["id"] == "ensemble__cloth_01460_black_crocodile_embossed_leath")
            t_01460_pos["facts"]["covered_regions"] = ["intimate_upper", "intimate_lower", "legs"]
            t_01460_pos["facts"].pop("exposed_regions", None)

            t_cupless_occ = next(t for t in pos_cec["tags"] if t["id"] == "ensemble__cloth_01478_black_leather_cupless_corset_w")
            t_cupless_occ["text"] = "black leather cupless corset, opaque black satin bra, black vinyl micro thong, black fishnet pantyhose"
            t_cupless_occ["facts"]["covered_regions"] = ["intimate_upper", "intimate_lower", "legs"]
            t_cupless_occ["facts"].pop("exposed_regions", None)

            pos_c_path.write_text(json.dumps(pos_c_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            pos_res = validate_all(data_dir=pos_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
            self.assertTrue(pos_res.is_valid, msg="\n".join(pos_res.errors))

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

            # 3. 透视套装条目若删除 allowed_nudity_levels/exposed_regions 仅剩 ensemble 覆盖，必须被严格校验拦截
            cec = next(c for c in c_doc["categories"] if c["id"] == "clothing_ensemble_combos")
            t_03324 = next(t for t in cec["tags"] if t["id"] == "ensemble__cloth_03324_nightgown_see_through_by_sourc")
            t_03324["facts"].pop("allowed_nudity_levels", None)
            t_03324["facts"].pop("exposed_regions", None)

            # 4. 无遮挡开胸款（cupless corset）若错标 intimate_upper 覆盖或遗漏 breasts 暴露，必须被拦截
            t_cupless = next(t for t in cec["tags"] if t["id"] == "ensemble__cloth_01478_black_leather_cupless_corset_w")
            t_cupless["facts"]["covered_regions"] = ["intimate_upper", "intimate_lower"]
            t_cupless["facts"]["exposed_regions"] = ["underwear_bra", "underwear_panties"]

            # 5. 无遮挡开裆款（crotchless + thigh-highs）若错标 intimate_lower 覆盖或遗漏 genitals 暴露，必须被拦截
            t_crotchless = next(t for t in cec["tags"] if t["id"] == "ensemble__cloth_01453_black_and_red_leather_bralette")
            t_crotchless["facts"]["covered_regions"] = ["intimate_upper", "intimate_lower", "legs"]
            t_crotchless["facts"].pop("exposed_regions", None)

            # 6. 即便有连裤袜遮挡，若仍自相矛盾地同时写入 intimate_lower 覆盖与 genitals 暴露，也必须被拦截
            t_01460_contra = next(t for t in cec["tags"] if t["id"] == "ensemble__cloth_01460_black_crocodile_embossed_leath")
            t_01460_contra["facts"]["covered_regions"] = ["intimate_upper", "intimate_lower", "legs"]
            t_01460_contra["facts"]["exposed_regions"] = ["genitals"]

            c_path.write_text(json.dumps(c_doc, ensure_ascii=False, indent=2), encoding="utf-8")

            res = validate_all(data_dir=tmp_data, schemas_dir=SCHEMAS_DIR, strict_jsonschema=True)
            self.assertFalse(res.is_valid)
            joined = "\n".join(res.errors)
            self.assertIn("missing non-empty nudity_branches", joined)
            self.assertIn("missing or invalid piece_role", joined)
            self.assertIn("wet_clinging", joined)
            self.assertIn("ensemble__cloth_03324_nightgown_see_through_by_sourc", joined)
            self.assertIn("ensemble__cloth_01478_black_leather_cupless_corset_w", joined)
            self.assertIn("ensemble__cloth_01453_black_and_red_leather_bralette", joined)
            self.assertIn("ensemble__cloth_01460_black_crocodile_embossed_leath", joined)

    def test_07_cross_level_general_tags_do_not_drop_clothes_or_underwear(self) -> None:
        """[P1-1] 脚链等允许 L1..L6 的跨等级通用标签不得误删 L1 下装或正常服装。"""
        gen = IYKYKPromptGenerator()
        common_kwargs = dict(
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="啦啦队服 (Cheerleader)",
            服装状态="无 (None)",
            prompt_seed=42,
            scene_theme_override="bedroom",
            shot_type_override="full body shot",
            camera_angle_override="eye-level view",
            lighting_override="soft window light",
            pose_override="standing",
            expression_override="gentle smile",
            hairstyle_override="long hair",
            makeup_override="clean face",
            film_override="color photo",
            imperfections_override="natural skin",
            quality_override="best quality",
            props_override="none",
        )
        res_plain = gen.generate_structured(饰品头饰="无 (None)", **common_kwargs)
        res_anklet = gen.generate_structured(饰品头饰="细金脚链 (Delicate Anklet)", **common_kwargs)

        plain_clothes = [a.text for a in res_plain.atoms if a.source_slot == "clothing"]
        anklet_clothes = [a.text for a in res_anklet.atoms if a.source_slot == "clothing"]
        self.assertIn("mini pleated skirt", plain_clothes)
        self.assertEqual(anklet_clothes, plain_clothes)
        self.assertEqual(
            [d for d in res_anklet.resolution_report.decisions if d.rule_id == "nudity_clothing_conflicts"],
            [],
        )

        # 直接校验多类跨等级通用标签 (脚链 / L1..L6 允许的摄影胶片与妆容等) 与 L1 服装/内衣不误删
        resolver = ConflictResolver(data_dir=DATA_DIR)
        general_atoms = [
            PromptAtom(
                atom_id="j1",
                text="thin gold anklet around bare ankle",
                span_type=SpanType.PLAIN,
                source_slot="jewelry",
                source_item_id="ankle_bracelet",
                tag_order=0,
                span_order=0,
                facts=SemanticFacts(
                    semantic_role="selector",
                    prop_usage="worn",
                    visible_regions=("feet",),
                    exposed_regions=("ankle_feet",),
                    allowed_nudity_levels=("L1", "L2", "L3", "L4", "L5", "L6"),
                ),
                provenance=TagProvenance(item_id="ankle_bracelet", kind="jewelry"),
            ),
            PromptAtom(
                atom_id="f1",
                text="intimate warmth",
                span_type=SpanType.PLAIN,
                source_slot="film",
                source_item_id="kodak_gold_200",
                tag_order=1,
                span_order=0,
                facts=SemanticFacts(
                    semantic_role="selector",
                    exposed_regions=("none",),
                    allowed_nudity_levels=("L1", "L2", "L3", "L4", "L5", "L6"),
                ),
                provenance=TagProvenance(item_id="kodak_gold_200", kind="film"),
            ),
            PromptAtom(
                atom_id="top1",
                text="sleeveless cheerleader crop top",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="cheerleader",
                tag_order=2,
                span_order=0,
                facts=SemanticFacts(semantic_role="selector", garment_topologies=("top",), piece_role="top", covered_regions=("upper_torso",)),
                provenance=TagProvenance(item_id="cheerleader", kind="base_clothing"),
            ),
            PromptAtom(
                atom_id="bot1",
                text="mini pleated skirt",
                span_type=SpanType.PLAIN,
                source_slot="clothing",
                source_item_id="cheerleader",
                tag_order=3,
                span_order=0,
                facts=SemanticFacts(semantic_role="selector", garment_topologies=("bottom_skirt",), piece_role="bottom", covered_regions=("lower_torso",)),
                provenance=TagProvenance(item_id="cheerleader", kind="base_clothing"),
            ),
        ]
        resolved, _, rep = resolver.resolve_atoms_with_full_report(
            general_atoms,
            nudity_constraint=NudityConstraint(requested_level="L1", effective_level="L1", source="ui_explicit"),
        )
        self.assertIn("mini pleated skirt", [a.text for a in resolved])
        self.assertEqual(rep.dropped_count, 0)

    def test_08_external_nudity_level_override_enforced_and_sync_checked(self) -> None:
        """[P2-2] 已识别外部等级编码 nudity_override='L5' 必须权威生效并移除内部服装，外部服装碰撞时明确报错。"""
        gen = IYKYKPromptGenerator()
        res = gen.generate_structured(
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="水手服 (JK Sailor Suit)",
            服装状态="无 (None)",
            nudity_override="L5",
            prompt_seed=42,
        )
        self.assertIsNotNone(res.nudity_constraint)
        self.assertEqual(res.nudity_constraint.requested_level, "L1")
        self.assertEqual(res.nudity_constraint.effective_level, "L5")
        self.assertEqual(res.nudity_constraint.source, "external_override")
        self.assertTrue(res.nudity_constraint.yielded_to_override)
        self.assertEqual([a for a in res.atoms if a.source_slot == "clothing"], [])
        self.assertTrue(
            any(d.rule_id == "nudity_clothing_conflicts" and d.action == "drop" for d in res.resolution_report.decisions)
        )

        # 纯内部 NudityConstraint (source=external_override) 在无字符串原子情况下也必须独立生效
        resolver = ConflictResolver(data_dir=DATA_DIR)
        resolved_only, _, rep_only = resolver.resolve_atoms_with_full_report(
            [
                PromptAtom(
                    atom_id="uni1",
                    text="Japanese school uniform",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing",
                    source_item_id="jk_seifuku",
                    tag_order=0,
                    span_order=0,
                    facts=SemanticFacts(semantic_role="selector", garment_topologies=("ensemble_outfit",), piece_role="ensemble", covered_regions=("upper_torso", "lower_torso")),
                    provenance=TagProvenance(item_id="jk_seifuku", kind="base_clothing", source_mode="explicit"),
                )
            ],
            nudity_constraint=NudityConstraint(requested_level="L1", effective_level="L5", source="external_override", yielded_to_override=True),
        )
        self.assertEqual(resolved_only, [])
        self.assertEqual(rep_only.dropped_count, 1)

        # 外部 nudity_override="L5" + 外部正常服装覆盖必须识别为外部硬冲突报错
        with self.assertRaises(PromptValidationError):
            gen.generate_structured(
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                服装款式="水手服 (JK Sailor Suit)",
                服装状态="无 (None)",
                nudity_override="L5",
                clothing_override="white shirt, pleated skirt",
                prompt_seed=42,
            )

    def test_09_external_override_yield_updates_and_clears_stale_constraint(self) -> None:
        """[P2-3] 裸露=None+外部裸露覆盖同步更新 source/effective_level；L5+外部普通衣物覆盖清空失效等级与分支。"""
        gen = IYKYKPromptGenerator()

        # 情况 1: 裸露=None + 可识别外部裸露覆盖 -> 不再残留 ui_none / None
        res_ext_nude = gen.generate_structured(
            裸露等级="无 (None)",
            服装款式="水手服 (JK Sailor Suit)",
            服装状态="无 (None)",
            nudity_override="completely naked, nude",
            prompt_seed=42,
        )
        self.assertEqual([a for a in res_ext_nude.atoms if a.source_slot == "clothing"], [])
        c1 = res_ext_nude.nudity_constraint
        self.assertIsNotNone(c1)
        self.assertEqual(c1.requested_level, "none")
        self.assertEqual(c1.effective_level, "L5")
        self.assertEqual(c1.source, "external_override")
        self.assertTrue(c1.yielded_to_override)
        self.assertIsNone(c1.selected_branch)
        self.assertEqual(res_ext_nude.resolution_report.nudity_constraint, c1)

        # 情况 2: 请求 L5 + 外部覆盖普通衬衫与裙子 -> 删除内部 L5 裸露并清空失效 effective_level/selected_branch
        res_ext_cloth = gen.generate_structured(
            裸露等级="L5 极致全裸 (Full Nude)",
            服装款式="水手服 (JK Sailor Suit)",
            服装状态="无 (None)",
            clothing_override="white shirt, pleated skirt",
            prompt_seed=42,
        )
        self.assertEqual([a for a in res_ext_cloth.atoms if a.source_slot == "nudity"], [])
        self.assertEqual(
            [a.text for a in res_ext_cloth.atoms if a.source_slot == "clothing"],
            ["white shirt", "pleated skirt"],
        )
        c2 = res_ext_cloth.nudity_constraint
        self.assertIsNotNone(c2)
        self.assertEqual(c2.requested_level, "L5")
        self.assertIsNone(c2.effective_level)
        self.assertIsNone(c2.selected_branch)
        self.assertEqual(c2.source, "derived_override")
        self.assertTrue(c2.yielded_to_override)
        self.assertEqual(c2.yield_reason, "external_clothing_override")
        self.assertEqual(res_ext_cloth.resolution_report.nudity_constraint, c2)


if __name__ == "__main__":
    unittest.main()
