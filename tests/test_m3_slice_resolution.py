"""
test_m3_slice_resolution.py — M3 小范围实施切片与形制能力消解集成测试

严格针对 M3 验收范围进行全量验证：
1. 3 组验证场景、4 个款式 ID：
   - 场景一：普通休闲毛衣 sweater_casual (独立款式、UI combo 兼容、零歧义)
   - 场景二：传统旗袍 qipao (解扣允许、掀裙禁止，标定为特定款式产品规则)
   - 场景三：连体形制能力对照组：日式连体泳衣 swimsuit_school (无纽扣，解扣禁止，掀裙禁止) vs 工装背带裤 dungarees (带纽扣，解扣允许，掀裙禁止)
2. 四大反例生产回归：
   - 仅泳衣解扣零兼容候选
   - 拒绝子串模糊匹配 (shirt 匹配 shirts_blouses)
   - 显式目标未命中快速失败绝不改绑
   - 显式目标形制互斥快速失败
3. Level A (A-1 ~ A-7) 全量夹具在生产 ConflictResolver 引擎上的实机消解与换序不变性
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import unittest
from pathlib import Path
from random import Random
from typing import Sequence

import jsonschema

from lib.atomizer import fragments_to_atoms
from lib.conflict_resolver import (
    ALLOWED_BUTTON_STYLES,
    BindingResult,
    BindingStatus,
    ConflictResolver,
    NON_SKIRT_ONE_PIECE,
    extract_garment_entities,
    find_bound_carrier,
    get_canonical_state_id,
    is_body_exposure_atom,
    is_garment_compatible_with_state,
    is_garment_modifier_atom,
)
from lib.models import PromptAtom, PromptFragment, SelectionOrigin, SemanticFacts, SpanType, TagProvenance
from lib.sampler import DataSampler
from nodes import IYKYKPromptDiagnostics, IYKYKPromptGenerator, _serialize_atom_item
from tests.fixtures.conflict_rule_fixtures import (
    get_all_level_a_fixtures,
    make_test_atom,
)
from tests.test_rc8_quality_gate import DIAGNOSTICS_SCHEMA_DOC, validate_audit_json_oracle

DATA_DIR = Path(__file__).parent.parent / "data"


def _get_dropped_ids(input_atoms: Sequence[PromptAtom], resolved_atoms: Sequence[PromptAtom]) -> list[str]:
    survived_ids = {a.atom_id for a in resolved_atoms}
    return [a.atom_id for a in input_atoms if a.atom_id not in survived_ids]


class TestM3SliceResolution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.sampler = DataSampler(DATA_DIR)
        cls.generator = IYKYKPromptGenerator()

    # ═══════════════════════════════════════════════════════════════════════════
    # 场景一：普通休闲毛衣 sweater_casual 独立性与兼容性
    # ═══════════════════════════════════════════════════════════════════════════

    def test_scenario_1_sweater_casual_catalog_entry(self):
        """验证 sweater_casual 存在于 data/clothing.json 且属性完备独立"""
        clothing_data = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        categories = clothing_data.get("categories", [])
        sweater_entries = [c for c in categories if c.get("id") == "sweater_casual"]
        self.assertEqual(len(sweater_entries), 1, "sweater_casual must exist as unique category in clothing.json")

        sc = sweater_entries[0]
        self.assertEqual(sc.get("name_zh"), "休闲毛衣 (Casual Sweater)")
        self.assertIn("top", sc["tags"][0]["facts"]["garment_topologies"])
        self.assertIn("sweater", sc.get("aliases", []))
        self.assertIn("turtleneck_sweater", sc.get("aliases", []))

        # 确保与 knit_sweater 分立，绝非别名合并
        knit_entries = [c for c in categories if c.get("id") == "knit_sweater"]
        self.assertEqual(len(knit_entries), 1)
        self.assertNotEqual(sc["id"], knit_entries[0]["id"])

    def test_scenario_1_sweater_casual_ui_combo_compliance(self):
        """验证 sweater_casual 注册在节点输入下拉选项中，支持工作流旧选项精确命中"""
        input_types = self.generator.INPUT_TYPES()
        clothing_options = input_types["required"]["服装款式"][0]
        self.assertIn("休闲毛衣 (Casual Sweater)", clothing_options, "休闲毛衣 (Casual Sweater) must be a selectable UI combo option")
        self.assertIn("露背毛衣/童贞杀 (Knit Sweater)", clothing_options)

    def test_scenario_1_sweater_casual_sampler_and_facts(self):
        """验证采样器可成功采样 sweater_casual，返回合规的拓扑事实与来源"""
        rng = Random(42)
        res = self.sampler.sample_clothing_result("休闲毛衣 (Casual Sweater)", "无 (None)", "L1", rng)
        self.assertGreater(len(res.base_tags), 0)
        main_tag = res.base_tags[0]
        self.assertEqual(main_tag.provenance.item_id, "sweater_casual")
        self.assertIn("top", main_tag.facts.garment_topologies)

        # 亦支持 ID 直接调用
        res_by_id = self.sampler.sample_clothing_result("sweater_casual", "无 (None)", "L1", rng)
        self.assertGreater(len(res_by_id.base_tags), 0)
        self.assertEqual(res_by_id.base_tags[0].provenance.item_id, "sweater_casual")

    def test_scenario_1_sweater_casual_resolver_coexistence(self):
        """验证 sweater_casual 与常规下装及外套在消解引擎中合法共存，零误杀"""
        rng = Random(42)
        atoms = [
            make_test_atom("casual sweater", "clothing", "sweater_casual", garment_topologies=("top",), tag_order=0),
            make_test_atom("pleated skirt", "clothing", "pleated_skirt", garment_topologies=("bottom_skirt",), tag_order=1),
        ]
        resolved, applied, report = self.resolver.resolve_atoms_with_full_report(atoms, rng)
        survived_ids = [a.source_item_id for a in resolved]
        self.assertIn("sweater_casual", survived_ids)
        self.assertIn("pleated_skirt", survived_ids)
        self.assertEqual(len(_get_dropped_ids(atoms, resolved)), 0)

    # ═══════════════════════════════════════════════════════════════════════════
    # 场景二：传统旗袍 qipao (特定款式产品规则：解扣允许，掀裙禁止)
    # ═══════════════════════════════════════════════════════════════════════════

    def test_scenario_2_qipao_product_rule_capabilities(self):
        """验证 qipao 在形制能力 SSOT 表中的产品规则定义 (ALLOWED_BUTTON_STYLES 包含, NON_SKIRT_ONE_PIECE 包含)"""
        self.assertIn("qipao", ALLOWED_BUTTON_STYLES, "旗袍 qipao 应标定为具备解纽扣能力 (产品规则)")
        self.assertIn("qipao", NON_SKIRT_ONE_PIECE, "旗袍 qipao 应标定为禁止掀裙 (特定款式产品规则决策)")

    def test_scenario_2_qipao_unbuttoned_allowed(self):
        """验证旗袍 qipao 与解扣状态合法共存，消解器不判定互斥 (反例验证)"""
        rng = Random(42)
        atoms = [
            make_test_atom("traditional cheongsam qipao", "clothing", "qipao", garment_topologies=("one_piece",), tag_order=0),
            make_test_atom("unbuttoned neckline", "clothing_state", "unbuttoned", garment_states=("opened",), tag_order=1),
        ]
        resolved, applied, report = self.resolver.resolve_atoms_with_full_report(atoms, rng)
        survived_ids = [a.source_item_id for a in resolved]
        self.assertIn("qipao", survived_ids)
        self.assertIn("unbuttoned", survived_ids)
        self.assertEqual(len(_get_dropped_ids(atoms, resolved)), 0)

    def test_scenario_2_qipao_lifted_skirt_forbidden(self):
        """验证旗袍 qipao 与掀裙状态互斥，掀裙状态被精准剔除"""
        rng = Random(42)
        atoms = [
            make_test_atom("traditional cheongsam qipao", "clothing", "qipao", garment_topologies=("one_piece",), tag_order=0),
            make_test_atom("lifted skirt", "clothing_state", "lifted_up", garment_states=("lifted",), tag_order=1),
        ]
        resolved, applied, report = self.resolver.resolve_atoms_with_full_report(atoms, rng)
        survived_ids = [a.source_item_id for a in resolved]
        self.assertIn("qipao", survived_ids)
        self.assertNotIn("lifted_up", survived_ids)
        self.assertIn(atoms[1].atom_id, _get_dropped_ids(atoms, resolved))
        self.assertTrue(any(d.rule_id == "clothing_style_state_coherence" for d in report.decisions))

    # ═══════════════════════════════════════════════════════════════════════════
    # 场景三：连体形制对照组 (swimsuit_school vs dungarees)
    # ═══════════════════════════════════════════════════════════════════════════

    def test_scenario_3_swimsuit_school_no_buttons_no_skirt(self):
        """日式连体泳衣 swimsuit_school: 无纽扣组，解扣禁止，掀裙禁止"""
        self.assertNotIn("swimsuit_school", ALLOWED_BUTTON_STYLES, "swimsuit_school 无纽扣形制")
        self.assertIn("swimsuit_school", NON_SKIRT_ONE_PIECE, "swimsuit_school 属非裙形连体")

        rng = Random(42)
        # 解扣测试 -> 剔除解扣
        atoms_unbutton = [
            make_test_atom("school swimsuit", "clothing", "swimsuit_school", garment_topologies=("one_piece",), tag_order=0),
            make_test_atom("unbuttoned", "clothing_state", "unbuttoned", garment_states=("opened",), tag_order=1),
        ]
        res1, _, rep1 = self.resolver.resolve_atoms_with_full_report(atoms_unbutton, rng)
        self.assertEqual([a.source_item_id for a in res1], ["swimsuit_school"])
        self.assertIn(atoms_unbutton[1].atom_id, _get_dropped_ids(atoms_unbutton, res1))

        # 掀裙测试 -> 剔除掀裙
        atoms_lift = [
            make_test_atom("school swimsuit", "clothing", "swimsuit_school", garment_topologies=("one_piece",), tag_order=0),
            make_test_atom("lifted skirt", "clothing_state", "lifted_up", garment_states=("lifted",), tag_order=1),
        ]
        res2, _, rep2 = self.resolver.resolve_atoms_with_full_report(atoms_lift, rng)
        self.assertEqual([a.source_item_id for a in res2], ["swimsuit_school"])
        self.assertIn(atoms_lift[1].atom_id, _get_dropped_ids(atoms_lift, res2))

    def test_scenario_3_dungarees_with_buttons_no_skirt(self):
        """工装背带裤 dungarees: 带纽扣对照组，解扣允许，掀裙禁止"""
        self.assertIn("dungarees", ALLOWED_BUTTON_STYLES, "dungarees 带纽扣/搭扣 (产品规则)")
        self.assertIn("dungarees", NON_SKIRT_ONE_PIECE, "dungarees 属于裤装连体，禁止掀裙")

        rng = Random(42)
        # 解扣测试 -> 合法共存！
        atoms_unbutton = [
            make_test_atom("denim dungarees", "clothing", "dungarees", garment_topologies=("one_piece", "bottom_pants"), tag_order=0),
            make_test_atom("unbuttoned straps", "clothing_state", "unbuttoned", garment_states=("opened",), tag_order=1),
        ]
        res1, _, rep1 = self.resolver.resolve_atoms_with_full_report(atoms_unbutton, rng)
        survived_ids = [a.source_item_id for a in res1]
        self.assertIn("dungarees", survived_ids)
        self.assertIn("unbuttoned", survived_ids)
        self.assertEqual(len(_get_dropped_ids(atoms_unbutton, res1)), 0)

        # 掀裙测试 -> 背带裤非裙装，掀裙必须剔除！
        atoms_lift = [
            make_test_atom("denim dungarees", "clothing", "dungarees", garment_topologies=("one_piece", "bottom_pants"), tag_order=0),
            make_test_atom("lifted skirt", "clothing_state", "lifted_up", garment_states=("lifted",), tag_order=1),
        ]
        res2, _, rep2 = self.resolver.resolve_atoms_with_full_report(atoms_lift, rng)
        self.assertEqual([a.source_item_id for a in res2], ["dungarees"])
        self.assertIn(atoms_lift[1].atom_id, _get_dropped_ids(atoms_lift, res2))

    # ═══════════════════════════════════════════════════════════════════════════
    # 四大反例生产级绑定决策阶梯回归测试
    # ═══════════════════════════════════════════════════════════════════════════

    def test_negative_1_swimsuit_unbuttoned_zero_candidate(self):
        """反例 1: 仅连体泳衣 + 解扣状态 -> find_bound_carrier 返回 UNBOUND_NO_CANDIDATE"""
        swimsuit = make_test_atom("school swimsuit", "clothing", "swimsuit_school", garment_topologies=("one_piece",), tag_order=0)
        unbutton = make_test_atom("unbuttoned", "clothing_state", "unbuttoned", garment_states=("opened",), tag_order=1)

        entities = list(extract_garment_entities([swimsuit]).values())
        result = find_bound_carrier(unbutton, entities)
        self.assertEqual(result.status, BindingStatus.UNBOUND_NO_CANDIDATE)
        self.assertIsNone(result.target_entity)
        self.assertIn("no_compatible_candidate", result.reason)

    def test_negative_2_reject_substring_matching(self):
        """反例 2: 目标指定为 'shirt'，现存实体为 'shirts_blouses' -> 严禁子串模糊匹配，快速失败"""
        shirt = make_test_atom("silk blouse", "clothing", "shirts_blouses", garment_topologies=("top",), tag_order=0)
        state_with_target = make_test_atom(
            "unbuttoned", "clothing_state", "unbuttoned",
            garment_states=("opened",), tag_order=1,
            target_id="shirt",  # 显式子串目标，非全名 shirts_blouses
        )

        entities = list(extract_garment_entities([shirt]).values())
        result = find_bound_carrier(state_with_target, entities)
        self.assertEqual(result.status, BindingStatus.UNBOUND_TARGET_NOT_FOUND)
        self.assertIsNone(result.target_entity)
        self.assertIn("explicit_target_not_found: shirt", result.reason)

    def test_negative_3_explicit_target_not_found_never_rebinds(self):
        """反例 3: 明确指定不存在的 'pencil_skirt'，现场只有衬衫 -> 报告未命中，绝不回退改绑衬衫"""
        shirt = make_test_atom("dress shirt", "clothing", "shirts_blouses", garment_topologies=("top",), tag_order=0)
        state_skirt = make_test_atom(
            "lifted skirt", "clothing_state", "lifted_up",
            garment_states=("lifted",), tag_order=1,
            target_id="pencil_skirt",
        )

        entities = list(extract_garment_entities([shirt]).values())
        result = find_bound_carrier(state_skirt, entities)
        self.assertEqual(result.status, BindingStatus.UNBOUND_TARGET_NOT_FOUND)
        self.assertIsNone(result.target_entity)
        self.assertIn("explicit_target_not_found: pencil_skirt", result.reason)

    def test_negative_4_explicit_target_incompatible_fast_fail(self):
        """反例 4: 显式指定泳衣为解扣目标 -> 命中实体但形制不兼容，返回 UNBOUND_INCOMPATIBLE 快速失败"""
        swimsuit = make_test_atom("school swimsuit", "clothing", "swimsuit_school", garment_topologies=("one_piece",), tag_order=0)
        unbutton = make_test_atom(
            "unbuttoned", "clothing_state", "unbuttoned",
            garment_states=("opened",), tag_order=1,
            target_id="swimsuit_school",
        )

        entities = list(extract_garment_entities([swimsuit]).values())
        result = find_bound_carrier(unbutton, entities)
        self.assertEqual(result.status, BindingStatus.UNBOUND_INCOMPATIBLE)
        self.assertIsNotNone(result.target_entity)
        self.assertEqual(result.target_entity.selected_id, "swimsuit_school")
        self.assertIn("target_incompatible", result.reason)

    # ═══════════════════════════════════════════════════════════════════════════
    # Level A 夹具全量实机消解与换序不变性回归
    # ═══════════════════════════════════════════════════════════════════════════

    def test_all_level_a_fixtures_execution_through_engine(self):
        """运行全部 Level A 夹具 (A-1 ~ A-7) 通过 ConflictResolver，验证断言契约 100% 达成"""
        cases = get_all_level_a_fixtures()
        rng = Random(42)

        for case in cases:
            with self.subTest(case_id=case.case_id, name=case.name):
                resolved, applied, report = self.resolver.resolve_atoms_with_full_report(case.input_atoms, rng)
                survived_ids = set(a.atom_id for a in resolved)
                dropped_ids = set(_get_dropped_ids(case.input_atoms, resolved))

                self.assertEqual(dropped_ids, set(case.expected_dropped_atom_ids), f"[{case.case_id}] dropped IDs mismatch")
                self.assertEqual(survived_ids, set(case.expected_survived_atom_ids), f"[{case.case_id}] survived IDs mismatch")

                if not case.is_counterexample:
                    self.assertIn(case.expected_rule_id, applied, f"[{case.case_id}] expected rule was not applied")
                    matched_dec = [d for d in report.decisions if d.rule_id == case.expected_rule_id]
                    self.assertGreater(len(matched_dec), 0, f"[{case.case_id}] no decision recorded for expected rule")
                    if case.expected_reason_code != "none":
                        self.assertEqual(matched_dec[0].reason_code, case.expected_reason_code)

    def test_fixture_a7_permutation_invariance_exhaustive(self):
        """验证 A-7 用例在 6 种全排列换序下，消解结果与输出原子集合绝对严格守恒"""
        from tests.fixtures.conflict_rule_fixtures import get_level_a_fixture_a7
        a7 = get_level_a_fixture_a7()
        atoms = list(a7.input_atoms)
        self.assertEqual(len(atoms), 3)

        base_resolved, base_applied, base_report = self.resolver.resolve_atoms_with_full_report(atoms, Random(100))
        base_survived = [a.source_item_id for a in base_resolved]
        base_dropped = [a.source_item_id for a in atoms if a.atom_id not in {r.atom_id for r in base_resolved}]

        perms = list(itertools.permutations(atoms))
        self.assertEqual(len(perms), 6)

        for p_idx, perm in enumerate(perms):
            p_resolved, p_applied, p_report = self.resolver.resolve_atoms_with_full_report(list(perm), Random(100))
            p_survived = [a.source_item_id for a in p_resolved]
            p_dropped = [a.source_item_id for a in perm if a.atom_id not in {r.atom_id for r in p_resolved}]

            self.assertEqual(set(p_survived), set(base_survived), f"Permutation {p_idx} survived items differ")
            self.assertEqual(set(p_dropped), set(base_dropped), f"Permutation {p_idx} dropped items differ")

    # ═══════════════════════════════════════════════════════════════════════════
    # 数据契约全链路闭环与 Schema 校验测试
    # ═══════════════════════════════════════════════════════════════════════════

    def test_target_id_full_lifecycle_and_schema_validation(self):
        """验证 target_id 从 PromptFragment -> atomize_fragments -> PromptAtom -> replace 不变性 -> 序列化与 Schema 契约校验"""
        frag = PromptFragment(
            text="lifted skirt",
            source_slot="clothing_state",
            source_item_id="lifted_up",
            target_id="pencil_skirt",
            facts=SemanticFacts(garment_states=("lifted",)),
        )
        self.assertEqual(frag.target_id, "pencil_skirt")

        # 1. 经过原子化 (fragments_to_atoms) 必须完整透传 target_id
        _, atoms = fragments_to_atoms([frag])
        self.assertEqual(len(atoms), 1)
        atom = atoms[0]
        self.assertEqual(atom.target_id, "pencil_skirt")

        # 2. 经过 dataclasses.replace 必须严格保留 target_id (不变性)
        replaced_atom = dataclasses.replace(atom, text="skirt hitched up")
        self.assertEqual(replaced_atom.target_id, "pencil_skirt")
        self.assertEqual(replaced_atom.text, "skirt hitched up")

        # 3. 经过 nodes._serialize_atom_item 必须产生合法字段
        serialized = _serialize_atom_item(replaced_atom, is_accepted=True)
        self.assertIn("target_id", serialized)
        self.assertEqual(serialized["target_id"], "pencil_skirt")

        # 4. 针对 diagnostics.schema.json 中 atom_entry 校验
        atom_schema = DIAGNOSTICS_SCHEMA_DOC["definitions"]["atom_entry"]
        jsonschema.validate(instance=serialized, schema=atom_schema)

        # 5. 空 target_id 亦合法序列化为 None / null 且通过 Schema
        frag_none = PromptFragment(text="white shirt", source_slot="clothing", source_item_id="shirts_blouses")
        _, atoms_none = fragments_to_atoms([frag_none])
        atom_none = atoms_none[0]
        self.assertIsNone(atom_none.target_id)
        serialized_none = _serialize_atom_item(atom_none, is_accepted=True)
        self.assertIn("target_id", serialized_none)
        self.assertIsNone(serialized_none["target_id"])
        jsonschema.validate(instance=serialized_none, schema=atom_schema)

    # ═══════════════════════════════════════════════════════════════════════════
    # Seed 44 专项回归：身体事实保留、动作修饰精准清理与 Schema 闭包
    # ═══════════════════════════════════════════════════════════════════════════

    def test_seed_44_functional_assertions_and_schema(self):
        """验证 seed 44 下：L4 裸露剔除女仆装后，身体事实 (topless, bare breasts) 绝对保留，
        衣物修饰 (maid dress pulled down) 精准剔除且归因于 clothing_style_state_coherence / state_lacks_carrier，
        全量诊断审计 JSON 100% 通过 Oracle 与 Schema 校验。"""
        diag = IYKYKPromptDiagnostics()
        inputs = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "随机 (Random)",
            "剧情主题": "随机 (Random)",
            "景别构图": "自动 (Auto)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L4 重点暴露 (Topless / Bottomless)",
            "服装款式": "女仆装 (Maid Dress)",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
            "发型发色": "随机 (Random)",
            "饰品头饰": "随机 (Random)",
            "妆容细节": "随机 (Random)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "随机 (Random)",
            "光影预设": "自动 (Auto)",
            "胶片风格": "随机 (Random)",
            "液体效果": "随机 (Random)",
            "纹身标记": "随机 (Random)",
            "道具物件": "随机 (Random)",
            "角色设定": "随机 (Random)",
            "真实微瑕": "随机 (Random)",
            "画质等级": "高清写真 (High)",
        }
        res = self.generator.generate_structured(**inputs, prompt_seed=44)
        d_pos, d_neg, d_desc, d_audit = diag.diagnose(**inputs, prompt_seed=44)

        # 1. 两次生成一致性
        self.assertEqual(res.positive, d_pos)
        self.assertEqual(res.negative, d_neg)

        # 2. 身体事实免检断言：必须在正向提示词中保留
        self.assertIn("topless", res.positive, "身体事实 topless 必须保留")
        self.assertIn("bare breasts", res.positive, "身体事实 bare breasts 必须保留")

        # 3. 悬空衣物动作修饰清理断言：无承载物，必须在正向提示词中剔除
        self.assertNotIn("maid dress pulled down", res.positive, "maid dress pulled down 失去女仆装承载物必须剔除")

        # 4. 诊断决策归属与原因码断言
        report = json.loads(d_audit)
        decisions = report.get("decisions", [])
        carrier_lacks_decisions = [
            d for d in decisions
            if d.get("rule_id") == "clothing_style_state_coherence"
            and d.get("reason_code") == "state_lacks_carrier"
        ]
        self.assertGreater(len(carrier_lacks_decisions), 0, "必须记录 clothing_style_state_coherence / state_lacks_carrier 决策")
        for d in carrier_lacks_decisions:
            self.assertEqual(d["phase"], "physical")
            self.assertEqual(d["action"], "drop")

        # 5. 绝无非法规则 dangling_modifier_cleanup 残留
        for d in decisions:
            self.assertNotEqual(d.get("rule_id"), "dangling_modifier_cleanup")

        # 6. 审计闭包与 Schema Oracle 校验
        validate_audit_json_oracle(
            d_audit,
            schema_doc=DIAGNOSTICS_SCHEMA_DOC,
            expected_positive=res.positive,
            trusted_inputs=inputs,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 严格幂等性验证：二次消解零新增决策 f(f(x)) = f(x)
    # ═══════════════════════════════════════════════════════════════════════════

    def test_idempotency_secondary_resolution_zero_new_decisions(self):
        """验证所有 Level A 夹具在首轮消解后，对输出结果执行二次消解严格满足：输出不变且零新增决策"""
        cases = get_all_level_a_fixtures()
        rng = Random(42)

        for case in cases:
            with self.subTest(case_id=case.case_id):
                # 第一轮消解
                res1, app1, rep1 = self.resolver.resolve_atoms_with_full_report(case.input_atoms, rng)
                # 第二轮消解 (将第一轮输出直接作为输入)
                res2, app2, rep2 = self.resolver.resolve_atoms_with_full_report(res1, rng)

                # 必须完全等价
    # ═══════════════════════════════════════════════════════════════════════════
    # 拉链动作真实回归与四象限修饰/身体事实测试矩阵
    # ═══════════════════════════════════════════════════════════════════════════

    def test_regression_seed_42_nurse_uniform_unzipped(self):
        """[P1 回归] 验证 seed=42、选择'护士服 + L3 半裸诱惑 + 自动联动'时，拉链动作 nurse uniform unzipped to waist 正确保留"""
        inputs = {
            "预设模板": "无 (None)",
            "风格配方": "无 (None)",
            "场景大类": "随机 (Random)",
            "剧情主题": "随机 (Random)",
            "景别构图": "自动 (Auto)",
            "拍摄视角": "自动 (Auto)",
            "裸露等级": "L3 半裸诱惑 (Half Nude)",
            "服装款式": "护士服 (Nurse Uniform)",
            "服装状态": "自动联动裸露等级 (Auto Link Nudity)",
            "发型发色": "随机 (Random)",
            "饰品头饰": "随机 (Random)",
            "妆容细节": "随机 (Random)",
            "姿势动作": "随机 (Random)",
            "情绪表情": "随机 (Random)",
            "光影预设": "自动 (Auto)",
            "胶片风格": "随机 (Random)",
            "液体效果": "随机 (Random)",
            "纹身标记": "随机 (Random)",
            "道具物件": "随机 (Random)",
            "角色设定": "随机 (Random)",
            "真实微瑕": "随机 (Random)",
            "画质等级": "高清写真 (High)",
        }
        res = self.generator.generate_structured(**inputs, prompt_seed=42)
        # 1. 断言 nurse uniform unzipped to waist 绝对保留在正向提示词中
        self.assertIn(
            "nurse uniform unzipped to waist",
            res.positive,
            "nurse uniform unzipped to waist must NOT be dropped by state_lacks_carrier",
        )
        # 2. 断言没有对其记录 state_lacks_carrier 误删决策
        if res.resolution_report:
            unzipped_drops = [
                d for d in res.resolution_report.decisions
                if d.before_text == "nurse uniform unzipped to waist" and d.action == "drop"
            ]
            self.assertEqual(len(unzipped_drops), 0, f"nurse uniform unzipped was unexpectedly dropped: {unzipped_drops}")

    def test_four_quadrants_modifier_and_body_facts(self):
        """[P1 回归] 四象限测试矩阵：混合动作+缺失目标拒绝、混合动作+合法目标保留、纯身体事实保留、主服装描述保留"""
        rng = Random(42)

        # 象限 1: 混合动作 + 不存在目标 (严格拒绝并剔除)
        atom_q1 = PromptAtom(
            text="unbuttoned blouse revealing bare breasts",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            target_id="missing",
            atom_id="atom_q1",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unbuttoned", raw_value="unbuttoned"),
            provenance=TagProvenance(item_id="unbuttoned", source_mode="explicit", parent_ids=("unbuttoned",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("opened",),
            ),
        )
        res_q1, _, rep_q1 = self.resolver.resolve_atoms_with_full_report([atom_q1], rng)
        self.assertEqual(len(res_q1), 0, "Quadrant 1: atom with missing target must be dropped")
        self.assertEqual(len(rep_q1.decisions), 1, "Quadrant 1 must produce exactly 1 drop decision")
        self.assertEqual(rep_q1.decisions[0].reason_code, "state_lacks_carrier")
        self.assertEqual(rep_q1.decisions[0].target_atom_id, "atom_q1")

        # 象限 2: 同一混合动作 + 合法在穿且兼容的目标 (精准绑定保留)
        carrier_blouse = PromptAtom(
            text="white buttoned blouse",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="shirts_blouses",
            atom_id="atom_carrier_blouse",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="shirts_blouses", raw_value="shirts_blouses"),
            provenance=TagProvenance(item_id="shirts_blouses", source_mode="explicit", parent_ids=("shirts_blouses",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_topologies=("top",),
            ),
        )
        atom_q2 = PromptAtom(
            text="unbuttoned blouse revealing bare breasts",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unbuttoned",
            target_id="shirts_blouses",
            atom_id="atom_q2",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unbuttoned", raw_value="unbuttoned"),
            provenance=TagProvenance(item_id="unbuttoned", source_mode="explicit", parent_ids=("unbuttoned",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("opened",),
            ),
        )
        res_q2, _, rep_q2 = self.resolver.resolve_atoms_with_full_report([carrier_blouse, atom_q2], rng)
        q2_texts = {a.text for a in res_q2}
        self.assertIn("white buttoned blouse", q2_texts, "Quadrant 2: carrier must survive")
        self.assertIn("unbuttoned blouse revealing bare breasts", q2_texts, "Quadrant 2: validly targeted modifier must survive")
        q2_drops = [d for d in rep_q2.decisions if d.action == "drop" and d.target_atom_id == "atom_q2"]
        self.assertEqual(len(q2_drops), 0, "Quadrant 2: modifier must not be dropped")

        # 象限 3: 纯身体事实 (免检保留，0 误删)
        atom_q3_1 = PromptAtom(
            text="topless",
            span_type=SpanType.PLAIN,
            source_slot="nudity",
            source_item_id="nudity_l3",
            atom_id="atom_q3_1",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="nudity", selected_id="nudity_l3", raw_value="L3"),
            provenance=TagProvenance(item_id="nudity_l3", source_mode="explicit", parent_ids=("nudity_l3",)),
            facts=SemanticFacts(semantic_role="selector", visible_regions=("upper_body",)),
        )
        atom_q3_2 = PromptAtom(
            text="bare breasts",
            span_type=SpanType.PLAIN,
            source_slot="nudity",
            source_item_id="nudity_l3",
            atom_id="atom_q3_2",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="nudity", selected_id="nudity_l3", raw_value="L3"),
            provenance=TagProvenance(item_id="nudity_l3", source_mode="explicit", parent_ids=("nudity_l3",)),
            facts=SemanticFacts(semantic_role="selector", visible_regions=("upper_body",)),
        )
        res_q3, _, rep_q3 = self.resolver.resolve_atoms_with_full_report([atom_q3_1, atom_q3_2], rng)
        q3_texts = {a.text for a in res_q3}
        self.assertIn("topless", q3_texts, "Quadrant 3: pure body fact topless must survive")
        self.assertIn("bare breasts", q3_texts, "Quadrant 3: pure body fact bare breasts must survive")
        self.assertEqual(len(rep_q3.decisions), 0, "Quadrant 3: pure body facts must have 0 decisions")

        # 象限 4: 普通主服装描述 (非修饰保护，0 误删)
        atom_q4_1 = PromptAtom(
            text="white nurse dress",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="nurse_uniform",
            atom_id="atom_q4_1",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="nurse_uniform", raw_value="nurse_uniform"),
            provenance=TagProvenance(item_id="nurse_uniform", source_mode="explicit", parent_ids=("nurse_uniform",)),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("top", "bottom_skirt")),
        )
        atom_q4_2 = PromptAtom(
            text="pleated skirt",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="jk_seifuku",
            atom_id="atom_q4_2",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="jk_seifuku", raw_value="jk_seifuku"),
            provenance=TagProvenance(item_id="jk_seifuku", source_mode="explicit", parent_ids=("jk_seifuku",)),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("bottom_skirt",)),
        )
        res_q4, _, rep_q4 = self.resolver.resolve_atoms_with_full_report([atom_q4_1, atom_q4_2], rng)
        q4_texts = {a.text for a in res_q4}
        self.assertIn("white nurse dress", q4_texts, "Quadrant 4: nurse dress must survive")
        self.assertIn("pleated skirt", q4_texts, "Quadrant 4: pleated skirt must survive")
        q4_drops = [d for d in rep_q4.decisions if d.action == "drop"]
        self.assertEqual(len(q4_drops), 0, "Quadrant 4: normal garment descriptions must not be dropped as dangling modifiers")

    def test_zipper_capability_rejection_on_incompatible_garment(self):
        """[P1 回归] 验证对于不具备拉链形制能力的特定款式（如 swimsuit_school），拉链动作修饰被拒绝绑定并剔除"""
        rng = Random(42)
        carrier_swimsuit = PromptAtom(
            text="navy blue school swimsuit",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="swimsuit_school",
            atom_id="atom_swimsuit",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="swimsuit_school", raw_value="swimsuit_school"),
            provenance=TagProvenance(item_id="swimsuit_school", source_mode="explicit", parent_ids=("swimsuit_school",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_topologies=("one_piece",),
            ),
        )
        unzipped_atom = PromptAtom(
            text="swimsuit unzipped to navel",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            target_id="swimsuit_school",
            atom_id="atom_unzipped",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unzipped", raw_value="unzipped"),
            provenance=TagProvenance(item_id="unzipped", source_mode="explicit", parent_ids=("unzipped",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("opened",),
            ),
        )
        res, _, rep = self.resolver.resolve_atoms_with_full_report([carrier_swimsuit, unzipped_atom], rng)
        res_texts = {a.text for a in res}
        self.assertIn("navy blue school swimsuit", res_texts, "School swimsuit carrier must survive")
        self.assertNotIn("swimsuit unzipped to navel", res_texts, "Unzipped on school swimsuit must be dropped")
        drops = [d for d in rep.decisions if d.action == "drop" and d.target_atom_id == "atom_unzipped"]
        self.assertEqual(len(drops), 1, "Must record exactly 1 drop decision for incompatible zipper on one-piece")
        self.assertEqual(drops[0].reason_code, "one_piece_state_conflict")

        # 对照测试：在非连体上装（休闲毛衣 sweater_casual，无拉链能力）上指定 unzipped，触发 state_lacks_carrier
        carrier_sweater = PromptAtom(
            text="casual gray sweater",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="sweater_casual",
            atom_id="atom_sweater",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="sweater_casual", raw_value="sweater_casual"),
            provenance=TagProvenance(item_id="sweater_casual", source_mode="explicit", parent_ids=("sweater_casual",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_topologies=("top",),
            ),
        )
        unzipped_sweater_atom = PromptAtom(
            text="sweater unzipped to waist",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="unzipped",
            target_id="sweater_casual",
            atom_id="atom_unzipped_sweater",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="unzipped", raw_value="unzipped"),
            provenance=TagProvenance(item_id="unzipped", source_mode="explicit", parent_ids=("unzipped",)),
            facts=SemanticFacts(
                semantic_role="selector",
                garment_states=("opened",),
            ),
        )
        res_sw, _, rep_sw = self.resolver.resolve_atoms_with_full_report([carrier_sweater, unzipped_sweater_atom], rng)
        res_sw_texts = {a.text for a in res_sw}
        self.assertIn("casual gray sweater", res_sw_texts, "Sweater carrier must survive")
        self.assertNotIn("sweater unzipped to waist", res_sw_texts, "Unzipped on sweater must be dropped")
        drops_sw = [d for d in rep_sw.decisions if d.action == "drop" and d.target_atom_id == "atom_unzipped_sweater"]
        self.assertEqual(len(drops_sw), 1, "Must record exactly 1 drop decision for unzipped on sweater")
        self.assertEqual(drops_sw[0].reason_code, "state_lacks_carrier")

    def test_latex_catsuit_zipper_action_recognition_and_carrier_coherence(self):
        """验证真实生成词条 latex catsuit zipper pulled halfway down 状态归一与修饰识别单源对齐，以及合法承载物保留/无承载物删除"""
        rng = Random(42)
        zipper_atom = make_test_atom(
            text="latex catsuit zipper pulled halfway down",
            source_slot="clothing_state",
            item_id="latex_catsuit",
            mode="auto",
        )
        # 1. 单源对齐断言
        self.assertEqual(get_canonical_state_id(zipper_atom), "unzipped", "Canonical state ID must be 'unzipped'")
        self.assertTrue(is_garment_modifier_atom(zipper_atom), "Must be recognized as garment modifier")
        self.assertFalse(is_body_exposure_atom(zipper_atom), "Must not be categorized as pure body exposure")

        # 2. 单独送入消解器，现场无承载物 -> 必须被 Rule 6 剔除 (state_lacks_carrier)
        res_alone, _, rep_alone = self.resolver.resolve_atoms_with_full_report([zipper_atom], rng)
        self.assertEqual(len(res_alone), 0, "Dangling zipper modifier without carrier must be dropped")
        drops_alone = [d for d in rep_alone.decisions if d.action == "drop" and d.target_atom_id == zipper_atom.atom_id]
        self.assertEqual(len(drops_alone), 1)
        self.assertEqual(drops_alone[0].reason_code, "state_lacks_carrier")

        # 3. 现场有合法在穿承载物 latex_catsuit -> 必须被正确保留，零误删
        carrier_catsuit = make_test_atom(
            text="glossy latex catsuit",
            source_slot="clothing",
            item_id="latex_catsuit",
            garment_topologies=("one_piece",),
        )
        res_valid, _, rep_valid = self.resolver.resolve_atoms_with_full_report([carrier_catsuit, zipper_atom], rng)
        res_valid_texts = {a.text for a in res_valid}
        self.assertIn("glossy latex catsuit", res_valid_texts)
        self.assertIn("latex catsuit zipper pulled halfway down", res_valid_texts)
        catsuit_drops = [d for d in rep_valid.decisions if d.target_atom_id == zipper_atom.atom_id]
        self.assertEqual(len(catsuit_drops), 0, "Legitimate zipper modifier on catsuit must survive with zero drop decisions")

        # 4. 现场为无拉链形制能力的连体泳衣 swimsuit_school -> 必须被剔除 (one_piece_state_conflict)
        carrier_swimsuit = make_test_atom(
            text="school swimsuit",
            source_slot="clothing",
            item_id="swimsuit_school",
            garment_topologies=("one_piece",),
        )
        res_incomp, _, rep_incomp = self.resolver.resolve_atoms_with_full_report([carrier_swimsuit, zipper_atom], rng)
        res_incomp_texts = {a.text for a in res_incomp}
        self.assertIn("school swimsuit", res_incomp_texts)
        self.assertNotIn("latex catsuit zipper pulled halfway down", res_incomp_texts)
        drops_incomp = [d for d in rep_incomp.decisions if d.action == "drop" and d.target_atom_id == zipper_atom.atom_id]
        self.assertEqual(len(drops_incomp), 1)
        self.assertEqual(drops_incomp[0].reason_code, "one_piece_state_conflict")

    def test_zipper_down_front_non_action_preserved(self):
        """验证 zipper down front 等拉链位置描述不误判为打开动作，且不受承载物检查误删（非动作反例）"""
        rng = Random(42)
        non_action_atom = make_test_atom(
            text="zipper down front",
            source_slot="clothing",
            item_id="sweater_casual",
            garment_topologies=("top",),
        )
        self.assertNotEqual(get_canonical_state_id(non_action_atom), "unzipped", "Position description must not be normalized to unzipped")
        self.assertFalse(is_garment_modifier_atom(non_action_atom), "Position description must not be treated as garment modifier action")
        self.assertFalse(is_body_exposure_atom(non_action_atom))

        # 送入消解器必须无任何删除决策
        res, _, rep = self.resolver.resolve_atoms_with_full_report([non_action_atom], rng)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].text, "zipper down front")
        self.assertEqual(len(rep.decisions), 0, "Non-action counterexample must trigger zero decisions")

    def test_audit_attribution_anti_substring_seed_1221(self):
        """验证审计归因严格按原子与 target_atom_id 对应，彻底杜绝 seed 1221 的 'back' 子串误归因为 'tight backless swimsuit' 删除决策"""
        from scripts.audit_attribution import attribute_seed_diff

        # 模拟 Seed 1221 真实场景：
        # RC8 采样 bikini_micro，包含 multipoint_cutout 扩展，其产出原子包含 "back"
        rc8_src_atoms = [
            {"atom_id": "rc8_atom_back", "text": "back", "slot": "clothing", "item_id": "multipoint_cutout"},
            {"atom_id": "rc8_atom_other", "text": "lace bikini", "slot": "clothing", "item_id": "bikini_micro"},
        ]
        rc8_final_atoms = [
            {"atom_id": "rc8_atom_back", "text": "back", "slot": "clothing", "item_id": "multipoint_cutout"},
        ]
        rc8_decisions = []

        # Current 因 28 -> 31 扩充抽样到了 one_piece_swimsuit，从未采样 "back"，但采样并删除了 "tight backless swimsuit"
        cur_src_atoms = [
            {"atom_id": "cur_atom_swimsuit", "text": "tight backless swimsuit", "slot": "clothing", "item_id": "one_piece_swimsuit"},
        ]
        cur_final_atoms = []  # "tight backless swimsuit" 被删除
        cur_decisions = [
            {
                "decision_id": "dec_001",
                "rule_id": "nudity_clothing_conflicts",
                "reason_code": "nudity_removes_clothing",
                "action": "drop",
                "target_atom_id": "cur_atom_swimsuit",
                "produced_atom_ids": [],
                "before_text": "tight backless swimsuit",
                "after_text": None,
            }
        ]

        diff_res = attribute_seed_diff(
            s=1221,
            rc8_clothing_id="bikini_micro",
            cur_clothing_id="one_piece_swimsuit",
            rc8_src_atoms=rc8_src_atoms,
            rc8_final_atoms=rc8_final_atoms,
            rc8_decisions=rc8_decisions,
            cur_src_atoms=cur_src_atoms,
            cur_final_atoms=cur_final_atoms,
            cur_decisions=cur_decisions,
        )

        # 核心断言：
        # 1. 差异必须由原子级 text 比对发现 "back" 丢失
        self.assertEqual(diff_res["removed_atoms"], ["back"])
        self.assertEqual(len(diff_res["removed_attributions"]), 1)
        attr = diff_res["removed_attributions"][0]

        # 2. "back" 绝对不能被归因为 Current 对 "tight backless swimsuit" 的删除决策！
        self.assertNotEqual(
            attr.get("category"),
            "current_decision",
            "'back' must NEVER be attributed to a decision dropping 'tight backless swimsuit' via substring matching!"
        )

        # 3. "back" 必须被正确归因为款式池扩充带来的款式抽样位移 (category_shift)
        self.assertEqual(attr.get("category"), "clothing_pool_expansion_category_shift")
        self.assertEqual(attr.get("rc8_clothing_id"), "bikini_micro")
        self.assertEqual(attr.get("cur_clothing_id"), "one_piece_swimsuit")

        # 4. 全局未解释数为 0
        self.assertFalse(diff_res["is_unexplained"])

        # 对照测试：相同款式 ID 下 (Seed 8006 场景)，Current 同样未采样 "back"，但删除了 "tight backless swimsuit"
        diff_res_same_clothing = attribute_seed_diff(
            s=8006,
            rc8_clothing_id="one_piece_swimsuit",
            cur_clothing_id="one_piece_swimsuit",
            rc8_src_atoms=rc8_src_atoms,
            rc8_final_atoms=rc8_final_atoms,
            rc8_decisions=rc8_decisions,
            cur_src_atoms=cur_src_atoms,
            cur_final_atoms=cur_final_atoms,
            cur_decisions=cur_decisions,
        )
        attr_same = diff_res_same_clothing["removed_attributions"][0]
        self.assertNotEqual(attr_same.get("category"), "current_decision")
        self.assertEqual(attr_same.get("category"), "clothing_pool_expansion_intra_slot_shift")

    def test_audit_attribution_unsourced_atom_rejected(self):
        """[P1 回归] 验证审计归因严格认证来源身份：两版源原子与决策为空、Current 输出凭空增加 lighting 原子时必须判定为 unexplained 阻断"""
        from scripts.audit_attribution import attribute_seed_diff

        # 用户最小反例：两版源原子、决策均为空，Current 最终输出凭空增加 lighting 原子
        diff_res = attribute_seed_diff(
            s=42,
            rc8_clothing_id="sweater_casual",
            cur_clothing_id="sweater_casual",
            rc8_src_atoms=[],
            rc8_final_atoms=[],
            rc8_decisions=[],
            cur_src_atoms=[],
            cur_final_atoms=[{"atom_id": "lighting_01", "text": "cinematic lighting", "slot": "lighting"}],
            cur_decisions=[],
        )

        self.assertTrue(diff_res["is_unexplained"], "Unsourced atom must trigger is_unexplained=True")
        self.assertEqual(diff_res["primary_category"], "unexplained")
        self.assertEqual(len(diff_res["unexplained_added"]), 1)
        self.assertEqual(diff_res["unexplained_added"][0]["category"], "UNEXPLAINED_UNSOURCED_ATOM")

    def test_audit_attribution_non_clothing_slot_rejected(self):
        """[P1 回归] 验证非衣物槽位的采样位移绝对禁止被归因为衣物池扩充 (clothing_pool_expansion_*)"""
        from scripts.audit_attribution import attribute_seed_diff

        # Case 1: 真实采样在 Current 源原子中，但 slot 为 lighting（非衣物槽位），新增时不被归为衣物池位移
        diff_add = attribute_seed_diff(
            s=100,
            rc8_clothing_id="sweater_casual",
            cur_clothing_id="sweater_casual",
            rc8_src_atoms=[],
            rc8_final_atoms=[],
            rc8_decisions=[],
            cur_src_atoms=[{"atom_id": "lighting_01", "text": "studio softbox lighting", "slot": "lighting"}],
            cur_final_atoms=[{"atom_id": "lighting_01", "text": "studio softbox lighting", "slot": "lighting"}],
            cur_decisions=[],
        )
        self.assertTrue(diff_add["is_unexplained"])
        self.assertEqual(diff_add["primary_category"], "unexplained")
        self.assertEqual(diff_add["unexplained_added"][0]["category"], "UNEXPLAINED_NON_CLOTHING_SAMPLING_SHIFT")

        # Case 2: RC8 存在 lighting 原子，Current 缺失，绝对禁止归口为衣物池位移
        diff_rem = attribute_seed_diff(
            s=101,
            rc8_clothing_id="sweater_casual",
            cur_clothing_id="sweater_casual",
            rc8_src_atoms=[{"atom_id": "lighting_02", "text": "golden hour sunset", "slot": "lighting"}],
            rc8_final_atoms=[{"atom_id": "lighting_02", "text": "golden hour sunset", "slot": "lighting"}],
            rc8_decisions=[],
            cur_src_atoms=[],
            cur_final_atoms=[],
            cur_decisions=[],
        )
        self.assertTrue(diff_rem["is_unexplained"])
        self.assertEqual(diff_rem["primary_category"], "unexplained")
        self.assertEqual(diff_rem["unexplained_removed"][0]["category"], "UNEXPLAINED_NON_CLOTHING_SAMPLING_SHIFT")

    def test_audit_attribution_unregistered_id_same_text_rejected(self):
        """[P1 回归] 验证审计来源身份必须匹配 ID 与内容：同文本但 ID 未注册 (phantom_2 vs source_1) 绝不能绕过来源校验"""
        from scripts.audit_attribution import attribute_seed_diff

        # 用户实测反例：
        # RC8 最终原子：source_1 / white shirt
        # Current 源原子：source_1 / white shirt
        # Current 最终原子：phantom_2 / white shirt
        # 无生成决策
        # 两版文本完全一致 (added_atoms == [])，但未注册 ID 必须由版本内来源检查独立拦截并触发 unexplained 阻断
        diff_res = attribute_seed_diff(
            s=42,
            rc8_clothing_id="sweater_casual",
            cur_clothing_id="sweater_casual",
            rc8_src_atoms=[{"atom_id": "source_1", "text": "white shirt", "slot": "clothing"}],
            rc8_final_atoms=[{"atom_id": "source_1", "text": "white shirt", "slot": "clothing"}],
            rc8_decisions=[],
            cur_src_atoms=[{"atom_id": "source_1", "text": "white shirt", "slot": "clothing"}],
            cur_final_atoms=[{"atom_id": "phantom_2", "text": "white shirt", "slot": "clothing"}],
            cur_decisions=[],
        )

        self.assertEqual(diff_res["added_atoms"], [], "Cross-version text diff is empty")
        self.assertEqual(diff_res["removed_atoms"], [], "Cross-version text diff is empty")
        self.assertTrue(diff_res["is_unexplained"], "Unregistered atom ID must trigger is_unexplained=True even when added_atoms is empty")
        self.assertEqual(diff_res["primary_category"], "unexplained")
        self.assertEqual(len(diff_res["unexplained_added"]), 1)
        self.assertEqual(diff_res["unexplained_added"][0]["category"], "UNEXPLAINED_UNSOURCED_ATOM")
        self.assertEqual(diff_res["unexplained_added"][0]["atom_id"], "phantom_2")


if __name__ == "__main__":
    unittest.main()

