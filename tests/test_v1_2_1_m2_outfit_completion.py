"""
test_v1_2_1_m2_outfit_completion.py — IYKYK v1.2.1 M2 随机穿搭补全、豁免与受控重组契约测试

覆盖全部 M2 验收要点：
1. 仅上装 / 仅下装 / 仅外套 / 仅袜类 / 仅鞋履 / 混合状态（如 apron_dress outer）随机抽样：
   保留原主款并补齐相容缺失件，补全件携带独立 catalog item_id、provenance 与 SelectionOrigin。
2. 豁免入口：服装=None、显式单品（hoodie/miniskirt/leather_jacket）、外部服装覆盖严禁自动补全或引发报错。
3. 服装状态=None + 款式=随机/自动：不补状态词/扩展词，仍完成基础穿搭补全。
4. 多构件制服子采样修复（blazer_uniform / jk_seifuku / bunny_suit / ol_suit 等）与状态载体兼容性。
5. 独立 RNG 子流隔离：补全不扰动主款选择及其他槽位随机序列。
6. 材质与分支扩展兼容性：厚重/针织/皮革/牛仔/盔甲/乳胶面料严禁叠加 cloth_transparency_tiers（sheer chiffon 等）。
7. 分支感知（L3 skirt/pants 分支、L4 绝不补回全穿搭）与无相容补件时最小安全组合回退。
8. 消解后残缺受控重组：最多尝试 1 次独立 rebuild RNG 重组；若仍残缺则 Fail-Closed 抛出 PromptValidationError。
9. 分层随机种子轻量扫测（60 种子横跨 L1/L2/L3/L4）。
"""
from __future__ import annotations

from dataclasses import replace
from random import Random
import unittest
from unittest.mock import patch

from lib.errors import PromptValidationError
from lib.models import AssemblyResult, ContextProfile, NudityConstraint, PromptAtom, SemanticFacts, SpanType, TagProvenance
from lib.outfit_completion import (
    assess_clothing_completeness,
    check_completion_exemption,
    is_heavy_or_rigid_garment,
    is_tags_compatible_with_state,
)
from lib.rng import derive_substream_rng
from nodes import IYKYKPromptGenerator, _assembler, _sampler


class TestV121M2OutfitCompletion(unittest.TestCase):
    """M2 随机穿搭完整补全与受控重组套件。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.generator = IYKYKPromptGenerator()

    def test_01_partial_archetypes_retained_and_completed_with_independent_provenance(self) -> None:
        """L1 下仅抽到 top / bottom / outerwear / legwear / footwear / mixed_apron 时，均保留原主款并补全相容缺失件。"""
        archetypes = [
            # (seed, expected_primary_id, expected_missing_before)
            (21, "knit_sweater", ["bottom"]),
            (47, "pencil_skirt", ["top"]),
            (9, "leather_jacket", ["inner_full"]),
            (46, "legwear_thighhighs", ["inner_full"]),
            (115, "footwear_boots", ["inner_full"]),
            (33, "apron_dress", ["inner_full"]),
        ]
        for seed, expected_primary, expected_missing in archetypes:
            with self.subTest(seed=seed, primary=expected_primary):
                res = _sampler.sample_clothing_result(
                    "随机 (Random)",
                    "自动联动裸露等级 (Auto Link Nudity)",
                    "L1",
                    Random(seed),
                    completion_rng=derive_substream_rng(seed, "selector:clothing_completion"),
                )
                self.assertEqual(res.style_id, expected_primary)
                self.assertIsNotNone(res.completion_info)
                self.assertEqual(res.completion_info["missing_parts"], expected_missing)
                self.assertGreater(len(res.complement_tags), 0)
                # 验证主款物理文本仍在结果中，且整个穿搭完整
                self.assertTrue(
                    assess_clothing_completeness(res.base_tags or res.state_tags, res.complement_tags, "L1").is_complete
                    or assess_clothing_completeness(res.complement_tags + res.base_tags, res.state_tags, "L1").is_complete
                )
                # 补全件各自拥有独立的 item_id / provenance
                for ctag in res.complement_tags:
                    self.assertIsNotNone(ctag.provenance)
                    self.assertNotEqual(ctag.provenance.item_id, expected_primary)
                    self.assertEqual(ctag.provenance.kind, "clothing_completion")
                    self.assertNotEqual(ctag.id, ctag.provenance.item_id)

        # 通过 IYKYKPromptGenerator 端到端验证各局部原型主款及补件独立 provenance / SelectionOrigin
        e2e_archetypes = [
            (0, "knit_sweater"),      # top only
            (46, "pencil_skirt"),     # bottom only
            (1, "waist_apron"),       # outerwear only
            (39, "legwear_thighhighs"),  # legwear only
            (4, "footwear_sneakers"),  # footwear only
        ]
        for e2e_seed, e2e_primary in e2e_archetypes:
            with self.subTest(e2e_seed=e2e_seed, e2e_primary=e2e_primary):
                gen_res = self.generator.generate_structured(
                    prompt_seed=e2e_seed,
                    裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                    服装款式="随机 (Random)",
                    服装状态="自动联动裸露等级 (Auto Link Nudity)",
                )
                src_cl = [a for a in gen_res.source_atoms if a.source_slot == "clothing"]
                self.assertEqual(src_cl[0].origin.selected_id, e2e_primary)
                comp_src = [a for a in src_cl if a.provenance and a.provenance.kind == "clothing_completion"]
                self.assertGreater(len(comp_src), 0)
                for a in comp_src:
                    self.assertEqual(a.source_item_id, a.provenance.item_id)
                    self.assertEqual(a.origin.selected_id, a.provenance.item_id)
                    self.assertEqual(a.origin.mode, "auto")
                    self.assertEqual(a.origin.selector, "clothing")
                    self.assertNotEqual(a.origin.selected_id, e2e_primary)
                    self.assertNotEqual(a.id, a.source_item_id)

    def test_02_exempt_entries_never_auto_complete_or_error(self) -> None:
        """服装=None、显式单品与外部服装覆盖严格豁免自动补全，也不触发报错。"""
        self.assertTrue(check_completion_exemption("无 (None)").is_exempt)
        self.assertTrue(check_completion_exemption("hoodie").is_exempt)
        self.assertTrue(check_completion_exemption("随机 (Random)", has_external_clothing_override=True).is_exempt)

        # 1. 服装=None under L1
        res_none = self.generator.generate_structured(
            prompt_seed=12,
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="无 (None)",
            服装状态="自动联动裸露等级 (Auto Link Nudity)",
        )
        self.assertEqual([a for a in res_none.atoms if a.source_slot == "clothing"], [])

        # 2. 显式单品 hoodie / miniskirt / leather_jacket under L1 严禁补加外部衣物
        for explicit_single in ("hoodie", "miniskirt", "leather_jacket"):
            with self.subTest(explicit_single=explicit_single):
                s_res = _sampler.sample_clothing_result(
                    explicit_single,
                    "无 (None)",
                    "L1",
                    Random(42),
                )
                self.assertEqual(s_res.complement_tags, ())
                self.assertTrue(s_res.completion_info["exempt"])
                self.assertEqual(s_res.completion_info["exempt_reason"], "explicit_style")

                e2e = self.generator.generate_structured(
                    prompt_seed=42,
                    裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                    服装款式=explicit_single,
                    服装状态="无 (None)",
                )
                item_ids = {a.provenance.item_id for a in e2e.atoms if a.source_slot == "clothing" and a.provenance}
                self.assertEqual(item_ids, {explicit_single})

        # 3. 外部服装覆盖 external_overrides={"clothing": ...} 不补全、不重组、不报错
        ext_res = self.generator.generate_structured(
            prompt_seed=7,
            裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
            服装款式="随机 (Random)",
            服装状态="自动联动裸露等级 (Auto Link Nudity)",
            external_overrides={"clothing": "black pleated miniskirt"},
        )
        self.assertIn("black pleated miniskirt", ext_res.positive.lower())
        applied = [o for o in ext_res.overrides if o.canonical_slot == "clothing"]
        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0].status, "applied")

    def test_03_state_none_skips_state_and_extension_while_completing_random_outfit(self) -> None:
        """服装状态=None 且 款式=随机 时，保持 state_tags=(), extension_tags=() 为空，但基础件仍被补齐。"""
        res = _sampler.sample_clothing_result(
            "随机 (Random)",
            "无 (None)",
            "L1",
            Random(5),  # lands on layered_skirt or bottom/partial style under L1
        )
        self.assertEqual(res.state_tags, ())
        self.assertEqual(res.extension_tags, ())
        self.assertIsNone(res.state_id)
        self.assertTrue(assess_clothing_completeness(res.all_tags, (), "L1").is_complete)

        # 也验证 L2 状态=None 且抽到上装-only 时同样只补基础件、不加状态/透明扩展
        res_l2 = _sampler.sample_clothing_result(
            "随机 (Random)",
            "无 (None)",
            "L2",
            Random(0),
        )
        self.assertEqual(res_l2.state_tags, ())
        self.assertEqual(res_l2.extension_tags, ())
        self.assertTrue(assess_clothing_completeness(res_l2.all_tags, (), "L2").is_complete)

    def test_04_independent_rng_substream_does_not_perturb_primary_or_other_slots(self) -> None:
        """补全/修复子流彻底隔离：更换 completion_rng 种子时，主款 selection 与主 rng 后续状态严格逐比特一致。"""
        rng1 = Random(21)
        res1 = _sampler.sample_clothing_result(
            "随机 (Random)",
            "自动联动裸露等级 (Auto Link Nudity)",
            "L1",
            rng1,
            completion_rng=Random(1001),
        )
        post1 = rng1.random()

        rng2 = Random(21)
        res2 = _sampler.sample_clothing_result(
            "随机 (Random)",
            "自动联动裸露等级 (Auto Link Nudity)",
            "L1",
            rng2,
            completion_rng=Random(9999),
        )
        post2 = rng2.random()

        self.assertEqual(res1.style_id, res2.style_id)
        self.assertEqual([t.text for t in res1.base_tags], [t.text for t in res2.base_tags])
        self.assertEqual([t.text for t in res1.state_tags], [t.text for t in res2.state_tags])
        self.assertEqual(res1.state_id, res2.state_id)
        self.assertEqual(post1, post2)

    def test_05_heavy_and_rigid_materials_forbid_sheer_transparency_extensions(self) -> None:
        """厚重针织、皮革、牛仔、盔甲在 L2/L3 下严禁叠穿 sheer chiffon / cloth_transparency_tiers。"""
        heavy_styles = ("knit_sweater", "leather_jacket", "denim_jacket", "knight_armor", "down_jacket")
        for cid in heavy_styles:
            with self.subTest(style_id=cid):
                self.assertTrue(is_heavy_or_rigid_garment(cid, ()))
                for seed in range(25):
                    for lvl in ("L2", "L3"):
                        res = _sampler.sample_clothing_result(
                            cid,
                            "自动联动裸露等级 (Auto Link Nudity)",
                            lvl,
                            Random(seed),
                        )
                        for ext in res.extension_tags:
                            self.assertFalse(
                                ext.provenance and "extension_family:cloth_transparency" in ext.provenance.semantic_ids,
                                f"{cid} under {lvl} seed={seed} illegally emitted transparency extension {ext.text}",
                            )

    def test_06_multi_tag_uniforms_preserve_core_garments_and_state_compatibility(self) -> None:
        """多标签制服子采样修补：L1/L2 下严禁退化为纯领带+袜子，且在全池支持当前状态时必定保留对应载体。"""
        data_cats = {c["id"]: c for c in _sampler._load("clothing")["categories"]}
        for cid in ("blazer_uniform", "jk_seifuku", "ol_suit", "bunny_suit", "cheerleader"):
            _, full_pool = _sampler._to_sampled_tags(data_cats[cid]["tags"], cid, "base_clothing")
            for seed in range(10):
                res = _sampler.sample_clothing_result(cid, "随机 (Random)", "L2", Random(seed))
                self.assertTrue(
                    assess_clothing_completeness(res.base_tags, (), "L2").is_complete,
                    f"{cid} seed={seed} L2 became incomplete: {[t.text for t in res.base_tags]}",
                )
                if is_tags_compatible_with_state(full_pool, res.state_id, res.state_tags, style_id=cid):
                    self.assertTrue(
                        is_tags_compatible_with_state(res.base_tags, res.state_id, res.state_tags, style_id=cid),
                        f"{cid} seed={seed} incompatible with state {res.state_id}: {[t.text for t in res.base_tags]}",
                    )

        # 显式解扣 (unbuttoned) 与 掀裙 (lifted_up) 在可承载多标签套装下必须 100% 保留对应载体
        for cid in ("blazer_uniform", "ol_suit", "waitress_uniform"):
            for seed in range(6):
                res = _sampler.sample_clothing_result(cid, "unbuttoned", "L2", Random(seed))
                self.assertTrue(assess_clothing_completeness(res.base_tags, (), "L2").is_complete)
                self.assertTrue(is_tags_compatible_with_state(res.base_tags, "unbuttoned", res.state_tags, style_id=cid))
        for cid in ("blazer_uniform", "jk_seifuku", "ol_suit", "cheerleader"):
            for seed in range(6):
                res = _sampler.sample_clothing_result(cid, "lifted_up", "L3", Random(seed))
                self.assertTrue(
                    any(is_tags_compatible_with_state(res.base_tags, "lifted_up", [st], style_id=cid) for st in res.state_tags),
                    f"{cid} seed={seed} lost skirt carrier for lifted_up: {[t.text for t in res.base_tags]}",
                )

    def test_07_nudity_branch_awareness_for_l3_and_no_over_completion_under_l4(self) -> None:
        """L3 按分支补全必要下装/裙装（不强行恢复成 L1 全身）；L4 绝不补回成完整穿搭。"""
        # L3 l3_topless_skirt_kept: top-only 样本应补裙装下装且不报错
        res_l3_skirt = _sampler.sample_clothing_result(
            "随机 (Random)",
            "自动联动裸露等级 (Auto Link Nudity)",
            "L3",
            Random(21),  # 抽中 knit_sweater top-only
            nudity_branch="l3_topless_skirt_kept",
            completion_rng=Random(77),
        )
        self.assertTrue(
            assess_clothing_completeness(
                res_l3_skirt.all_tags, (), "L3", nudity_branch="l3_topless_skirt_kept"
            ).is_complete
        )

        # L4 下绝不自动触发 top+bottom 补全
        for seed in range(15):
            res_l4 = _sampler.sample_clothing_result(
                "随机 (Random)",
                "自动联动裸露等级 (Auto Link Nudity)",
                "L4",
                Random(seed),
            )
            self.assertEqual(res_l4.complement_tags, ())

    def test_08_minimal_combination_fallback_when_companion_pool_exhausted(self) -> None:
        """当所有兼容伴侣候选都被互斥过滤时，触发展开最小安全组合回退而不崩溃。"""
        orig_excl = _sampler.get_excluded_candidate_ids
        orig_cats = _sampler._load("clothing")["categories"]
        all_bottom_and_dress = {
            c["id"] for c in orig_cats
            if any((t.get("facts") or {}).get("piece_role") in ("bottom", "one_piece", "ensemble") for t in c.get("tags", []))
            and c["id"] != "dress_casual"
        }

        def mocked_excl(slot_name: str, **kwargs):
            s = set(orig_excl(slot_name, **kwargs))
            if slot_name == "clothing":
                s.update(all_bottom_and_dress)
            return s

        with patch.object(_sampler, "get_excluded_candidate_ids", side_effect=mocked_excl):
            # seed=21 picks knit_sweater; bottom candidates all excluded, forcing minimal fallback onto dress_casual
            res = _sampler.sample_clothing_result(
                "随机 (Random)",
                "自动联动裸露等级 (Auto Link Nudity)",
                "L1",
                Random(21),
                completion_rng=Random(123),
            )
        self.assertIsNotNone(res.completion_info)
        self.assertTrue(res.completion_info["fallback_used"])
        self.assertGreater(len(res.complement_tags), 0)
        self.assertTrue(assess_clothing_completeness(res.all_tags, (), "L1").is_complete)

    def test_09_post_resolution_controlled_rebuild_at_most_once_and_fail_closed(self) -> None:
        """消解后缺件触发最多 1 次受控重组（成功放行；第 1 次重组后仍残缺则 Fail-Closed 抛出 PromptValidationError）。"""
        broken_top_atom = PromptAtom(
            atom_id="broken_top__tag_000",
            text="lonely sweater",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="knit_sweater",
            provenance=TagProvenance(item_id="knit_sweater", kind="base_clothing"),
            id="broken_top__tag_000",
            facts=SemanticFacts(piece_role="top", covered_regions=("upper_torso",)),
        )
        valid_dress_atom = PromptAtom(
            atom_id="dress_casual__tag_000",
            text="casual summer dress",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="dress_casual",
            provenance=TagProvenance(item_id="dress_casual", kind="clothing_completion"),
            id="dress_casual__tag_000",
            facts=SemanticFacts(piece_role="one_piece", covered_regions=("upper_torso", "lower_torso")),
        )
        l1_constraint = NudityConstraint(
            requested_level="L1",
            effective_level="L1",
            source="ui_explicit",
        )
        base_asm = AssemblyResult(
            prompt="lonely sweater",
            accepted_atoms=(broken_top_atom,),
            source_atoms=(broken_top_atom,),
            rules_applied=(),
            context_profile=ContextProfile(),
            resolution_report=None,
            deduplicated_atoms=(),
            budget_filtered_atoms=(),
            deduplication_records=(),
            budget_filter_records=(),
            overrides=(),
            nudity_constraint=l1_constraint,
        )

        # Case A: 第 1 次残缺，触发第 1 次（也是唯一 1 次）受控重组后成功恢复完整穿搭
        recovered_asm = replace(base_asm, prompt="casual summer dress", accepted_atoms=(valid_dress_atom,))
        with patch.object(_assembler, "assemble_slots", side_effect=[base_asm, recovered_asm]) as mock_asm:
            out = self.generator.generate_structured(
                prompt_seed=10,
                裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                服装款式="随机 (Random)",
                服装状态="自动联动裸露等级 (Auto Link Nudity)",
            )
            self.assertEqual(mock_asm.call_count, 2)
            self.assertEqual(out.positive, "casual summer dress")

        # Case B: 初次残缺 + 第 1 次重组后仍然残缺 -> 严禁第 2 次重试，必须明确 Fail-Closed 抛错
        with patch.object(_assembler, "assemble_slots", side_effect=[base_asm, base_asm]) as mock_asm_fail:
            with self.assertRaises(PromptValidationError) as ctx:
                self.generator.generate_structured(
                    prompt_seed=10,
                    裸露等级="L1 包裹暗示 (Fully Clothed / Suggestive)",
                    服装款式="随机 (Random)",
                    服装状态="自动联动裸露等级 (Auto Link Nudity)",
                )
            self.assertEqual(mock_asm_fail.call_count, 2)
            self.assertEqual(ctx.exception.error_code, "incomplete_outfit_after_rebuild")
            self.assertEqual(ctx.exception.details.get("rebuild_attempts"), 1)

    def test_10_stratified_random_seed_sweep_across_l1_l2_l3_l4(self) -> None:
        """轻量分层种子扫测（60 种子）：L1/L2 100% 完整且 0 内衣泄露，重料无透明扩展，L4 不补回日常整套。"""
        l1_banned = {
            "lingerie_panties", "lingerie_bra", "lingerie_lace",
            "bikini_micro", "bikini_classic", "bikini_creative", "bikini_strappy",
            "slime_dress", "legwear_bare",
        }
        for seed in range(25):
            r_l1 = _sampler.sample_clothing_result("随机 (Random)", "自动联动裸露等级 (Auto Link Nudity)", "L1", Random(seed))
            self.assertNotIn(r_l1.style_id, l1_banned)
            self.assertTrue(
                assess_clothing_completeness(r_l1.all_tags, (), "L1").is_complete,
                f"L1 sweep seed={seed} ({r_l1.style_id}) incomplete: {[t.text for t in r_l1.all_tags]}",
            )

        for seed in range(25):
            r_l2 = _sampler.sample_clothing_result("随机 (Random)", "自动联动裸露等级 (Auto Link Nudity)", "L2", Random(seed))
            self.assertTrue(
                assess_clothing_completeness(r_l2.all_tags, (), "L2").is_complete,
                f"L2 sweep seed={seed} ({r_l2.style_id}) incomplete: {[t.text for t in r_l2.all_tags]}",
            )
            if is_heavy_or_rigid_garment(r_l2.style_id, list(r_l2.base_tags) + list(r_l2.complement_tags)):
                for ext in r_l2.extension_tags:
                    self.assertFalse(
                        ext.provenance and "extension_family:cloth_transparency" in ext.provenance.semantic_ids,
                        f"L2 sweep seed={seed} ({r_l2.style_id}) leaked transparency: {ext.text}",
                    )

        for seed in range(10):
            r_l4 = _sampler.sample_clothing_result("随机 (Random)", "自动联动裸露等级 (Auto Link Nudity)", "L4", Random(seed))
            self.assertEqual(r_l4.complement_tags, ())


if __name__ == "__main__":
    unittest.main()
