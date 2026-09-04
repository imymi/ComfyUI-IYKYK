"""
test_conflict_dag_and_invariants.py — 冲突消解引擎 DAG 拓扑执行、权威关系、关键反例与不可变契约测试
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import re
import tempfile
import unittest
from pathlib import Path
from random import Random
from typing import Any, Dict, List, Optional
from unittest.mock import patch

from lib.rng import derive_substream_rng
from lib.conflict_resolver import (
    ConflictResolver,
    DecisionLedger,
    OneTimeIndex,
    RuleRegistry,
    compute_atoms_hash,
)
from lib.errors import AffinityConfigurationError, RuleConfigurationError, UnresolvedConflictError
from lib.models import (
    PromptAtom,
    ResolutionDecision,
    ResolutionReport,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    TagProvenance,
)
from lib.rule_contract import (
    DAG_FROZEN_ORDER,
    FROZEN_DAG_METADATA,
    STABLE_RULE_ORDER,
    RuleDocument,
    RuleItem,
    parse_rule_document,
    validate_and_sort_dag,
)

DATA_DIR = Path(__file__).parent.parent / "data"


def make_atom(
    text: str,
    slot: str,
    span_type: SpanType = SpanType.PLAIN,
    idx: int = 0,
    contains_blackbox: bool = False,
    origin_mode: str = "custom",
    facts: Optional[SemanticFacts] = None,
    item_id: Optional[str] = None,
) -> PromptAtom:
    """构建携带完整合法元数据的 PromptAtom 夹具。"""
    actual_item_id = item_id or ("nudity_l5" if slot == "nudity" and "naked" in text else f"item_{idx:03d}")
    return PromptAtom(
        atom_id=f"atom_{idx:03d}",
        text=text,
        span_type=span_type,
        source_slot=slot,
        source_item_id=actual_item_id,
        tag_order=idx,
        span_order=idx,
        provenance=TagProvenance(item_id=actual_item_id, parent_ids=(f"src_{idx:03d}",)),
        contains_blackbox=contains_blackbox,
        origin=SelectionOrigin(mode=origin_mode, selector=slot),
        facts=facts or SemanticFacts(),
    )


FIVE_SYNTAX_FORMS = [
    ("PAREN", SpanType.PAREN, "({}:1.2)"),
    ("BRACKET", SpanType.BRACKET, "[{}]"),
    ("ANGLE", SpanType.ANGLE, "<{}>"),
    ("QUOTED", SpanType.QUOTED, '"{}"'),
    ("NESTED", SpanType.PAREN, "(({}:1.1):1.2)"),
]

def get_expected_204_matrix_keys() -> set[str]:
    keys = set()
    for r in DAG_FROZEN_ORDER:
        for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7"):
            keys.add(f"{r}::{d}")
    for r in DAG_FROZEN_ORDER:
        if r != "tattoo_dermal_fusion":
            for fname, _, _ in FIVE_SYNTAX_FORMS:
                keys.add(f"{r}::D8::{fname}")
    for fname, _, _ in FIVE_SYNTAX_FORMS:
        keys.add(f"tattoo_dermal_fusion::D8_inject_only::{fname}")
    return keys

def validate_204_matrix_coverage(executed_keys: set[str]) -> None:
    expected = get_expected_204_matrix_keys()
    if len(expected) != 204:
        raise AssertionError(f"Expected 204 keys definition corrupt: {len(expected)} != 204")
    missing = expected - executed_keys
    extra = executed_keys - expected
    if missing or extra or len(executed_keys) != 204:
        raise AssertionError(
            f"204-Cell Matrix coverage validation failed: missing={len(missing)} ({missing}), "
            f"extra={len(extra)} ({extra}), total_executed={len(executed_keys)}"
        )

D5_ORACLES = {
    "spatial_environmental_mutual_exclusion": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_001",), "target": "atom_000", "produced": (), "final": ["atom_001"], "sequence": 0},
    },
    "nudity_clothing_conflicts": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "framing_lower_body_coherence": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "pose_hand_occupation": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "handheld_props_single_holder": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_001",), "target": "atom_000", "produced": (), "final": ["atom_001"], "sequence": 0},
    },
    "clothing_style_state_coherence": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "material_penetration": {
        "fwd": {"action": "replace", "winner": (), "target": "atom_000", "produced": ("atom_000__r_material_penetration",), "final": ["atom_000__r_material_penetration", "atom_099"], "sequence": 0},
        "rev": {"action": "replace", "winner": (), "target": "atom_000", "produced": ("atom_000__r_material_penetration",), "final": ["atom_099", "atom_000__r_material_penetration"], "sequence": 0},
    },
    "device_quality_compatibility": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "environmental_lighting_coherence": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "monochrome_film_chroma_coherence": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "makeup_details_coherence": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "gaze_angle_geometry": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "accessory_occlusion_gaze_coherence": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "emotion_gaze_affinity": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
    },
    "gaze_mutual_exclusion": {
        "fwd": {"action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "final": ["atom_000"], "sequence": 0},
        "rev": {"action": "drop", "winner": ("atom_001",), "target": "atom_000", "produced": (), "final": ["atom_001"], "sequence": 0},
    },
    "liquid_restrictions": {
        "fwd": {"action": "replace", "winner": (), "target": "atom_000", "produced": ("atom_000__r_liquid_restrictions",), "final": ["atom_000__r_liquid_restrictions__r_liquid_restrictions", "atom_099"], "sequence": 0},
        "rev": {"action": "replace", "winner": (), "target": "atom_000", "produced": ("atom_000__r_liquid_restrictions",), "final": ["atom_099", "atom_000__r_liquid_restrictions__r_liquid_restrictions"], "sequence": 0},
    },
    "tattoo_dermal_fusion": {
        "fwd": {"action": "inject", "winner": ("atom_000",), "target": None, "produced": ("atom_injected_atom_000_tattoo_dermal_fusion",), "final": ["atom_000", "atom_099", "atom_injected_atom_000_tattoo_dermal_fusion"], "sequence": 0},
        "rev": {"action": "inject", "winner": ("atom_000",), "target": None, "produced": ("atom_injected_atom_000_tattoo_dermal_fusion",), "final": ["atom_099", "atom_000", "atom_injected_atom_000_tattoo_dermal_fusion"], "sequence": 0},
    },
}


def build_d2_custom_fixtures() -> Dict[str, List[PromptAtom]]:
    """现有 17x8 矩阵中已验证的 D2 custom fixtures，夹具键精确等于 DAG_FROZEN_ORDER (P1)。"""
    return {
        "spatial_environmental_mutual_exclusion": [
            make_atom("indoor onsen", "scene", idx=0, origin_mode="custom"),
            make_atom("rotenburo", "scene", idx=1, origin_mode="custom"),
        ],
        "nudity_clothing_conflicts": [
            make_atom("completely naked", "nudity", idx=0, origin_mode="custom"),
            make_atom("wearing uniform", "clothing", idx=1, origin_mode="custom"),
        ],
        "framing_lower_body_coherence": [
            make_atom("close-up", "shot_type", idx=0, origin_mode="custom"),
            make_atom("high heels", "clothing", idx=1, origin_mode="custom"),
        ],
        "pose_hand_occupation": [
            make_atom("hands behind back", "pose", idx=0, origin_mode="custom"),
            make_atom("holding smartphone", "props", idx=1, origin_mode="custom"),
        ],
        "handheld_props_single_holder": [
            make_atom("holding smartphone", "props", idx=0, origin_mode="custom"),
            make_atom("holding camera", "props", idx=1, origin_mode="custom"),
        ],
        "clothing_style_state_coherence": [
            make_atom("school swimsuit (sukumizu)", "clothing", idx=0, origin_mode="custom"),
            make_atom("unbuttoned blouse", "clothing", idx=1, origin_mode="custom"),
        ],
        "material_penetration": [
            make_atom("sheer", "clothing", idx=0, origin_mode="custom"),
        ],
        "device_quality_compatibility": [
            make_atom("cctv", "shot_type", idx=0, origin_mode="custom"),
            make_atom("masterpiece", "quality", idx=1, origin_mode="custom"),
        ],
        "environmental_lighting_coherence": [
            make_atom("hotel balcony night", "scene", idx=0, origin_mode="custom"),
            make_atom("soft sunlight filtered through sheer curtains", "lighting", idx=1, origin_mode="custom"),
        ],
        "monochrome_film_chroma_coherence": [
            make_atom("classic monochrome", "film", idx=0, origin_mode="custom"),
            make_atom("neon rim light", "lighting", idx=1, origin_mode="custom"),
        ],
        "makeup_details_coherence": [
            make_atom("natural makeup", "makeup", idx=0, origin_mode="custom"),
            make_atom("smeared lipstick on cheek", "makeup", idx=1, origin_mode="custom"),
        ],
        "gaze_angle_geometry": [
            make_atom("overhead top-down shot", "camera_angle", idx=0, origin_mode="custom"),
            make_atom("looking down", "expression", idx=1, origin_mode="custom"),
        ],
        "accessory_occlusion_gaze_coherence": [
            make_atom("blindfold", "jewelry", idx=0, origin_mode="custom"),
            make_atom("eye-fucking the viewer", "expression", idx=1, origin_mode="custom"),
        ],
        "emotion_gaze_affinity": [
            make_atom("shy expression", "expression", idx=0, origin_mode="custom"),
            make_atom("seductive smile", "expression", idx=1, origin_mode="custom"),
        ],
        "gaze_mutual_exclusion": [
            make_atom("direct eye contact", "expression", idx=0, origin_mode="custom"),
            make_atom("looking away", "expression", idx=1, origin_mode="custom"),
        ],
        "liquid_restrictions": [
            make_atom("cum on closed eyes", "liquids", idx=0, origin_mode="custom"),
        ],
        "tattoo_dermal_fusion": [
            make_atom("dragon body art on back", "props", idx=0, origin_mode="custom"),
        ],
    }


def _run_d2_coverage_gate(fixtures: Dict[str, List[PromptAtom]], resolver: ConflictResolver) -> None:
    """验证 D2 custom fixtures 覆盖门禁：
    1. 夹具键必须精确等于 DAG_FROZEN_ORDER；
    2. 每条规则必须触发目标规则（出现在 rules_applied）；
    3. 每条规则必须触发文本回退（text_fallback_hits > 0）；
    4. 最终无残留硬冲突（零未消解冲突）。
    """
    if set(fixtures.keys()) != set(DAG_FROZEN_ORDER):
        missing = set(DAG_FROZEN_ORDER) - set(fixtures.keys())
        extra = set(fixtures.keys()) - set(DAG_FROZEN_ORDER)
        raise AssertionError(f"D2 fixtures keys do not match DAG_FROZEN_ORDER: missing={missing}, extra={extra}")
    if tuple(fixtures.keys()) != DAG_FROZEN_ORDER:
        raise AssertionError("D2 fixtures keys order does not strictly equal DAG_FROZEN_ORDER")

    for rid in DAG_FROZEN_ORDER:
        atoms = fixtures[rid]
        resolved, applied, rep = resolver.resolve_atoms_with_full_report(atoms, Random(42))
        conflicts, has_prot = resolver.detect_hard_conflicts(resolved)

        if rid not in applied:
            raise AssertionError(f"[{rid}] D2 custom fixture did not trigger target rule (applied: {applied})")
        if resolver.text_fallback_hits <= 0:
            raise AssertionError(f"[{rid}] D2 custom fixture did not produce text_fallback_hits (hits: {resolver.text_fallback_hits})")
        if len(conflicts) > 0:
            raise AssertionError(f"[{rid}] D2 custom fixture resulted in unresolved hard conflicts: {conflicts}")
        if len(rep.unresolved_conflicts) > 0:
            raise AssertionError(f"[{rid}] D2 custom fixture report contained unresolved conflicts: {rep.unresolved_conflicts}")


class TestConflictDAGAndInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resolver = ConflictResolver(DATA_DIR)
        cls.rules_doc = json.loads((DATA_DIR / "conflict_rules.json").read_text(encoding="utf-8"))

    # ───────────────────────────────────────────────────────────
    # 1. DAG 拓扑顺序与元数据校验
    # ───────────────────────────────────────────────────────────

    def test_01_dag_topological_execution_order(self):
        """DAG 拓扑执行序列严格等于冻结 17 规则顺序。"""
        registry = RuleRegistry(DATA_DIR)
        self.assertEqual(registry.doc.execution_order, DAG_FROZEN_ORDER)
        self.assertEqual(len(registry.doc.execution_order), 17)

    def test_02_r2_n04_dag_cycle_interception(self):
        """关键反例 R2-N04: DAG 检测到循环依赖时必须抛出 RuleConfigurationError。"""
        doc = copy.deepcopy(self.rules_doc)
        for r in doc["rules"]:
            if r["id"] == "pose_hand_occupation":
                r["depends_on"] = ["handheld_props_single_holder"]

        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(doc)
        self.assertIn("depends_on mismatch", str(ctx.exception))

        meta_cycle = copy.deepcopy(FROZEN_DAG_METADATA)
        meta_cycle["pose_hand_occupation"]["depends_on"] = ("handheld_props_single_holder",)
        with patch.dict("lib.rule_contract.FROZEN_DAG_METADATA", meta_cycle):
            with self.assertRaises(RuleConfigurationError) as ctx_cycle:
                parse_rule_document(doc)
            self.assertIn("Cyclic dependency", str(ctx_cycle.exception))

    def test_03_r2_n05_duplicate_rule_ids_interception(self):
        """关键反例 R2-N05: 规则库出现重复规则 ID 时必须抛出 RuleConfigurationError。"""
        doc = copy.deepcopy(self.rules_doc)
        doc["rules"][1]["id"] = doc["rules"][0]["id"]

        with self.assertRaises(RuleConfigurationError) as ctx:
            parse_rule_document(doc)
        self.assertIn("Duplicate rule IDs", str(ctx.exception))

    def test_04_dag_phase_ordering_inversion_interception(self):
        """负向：前序阶段规则依赖后序阶段规则必须被拦截。"""
        doc = copy.deepcopy(self.rules_doc)
        for r in doc["rules"]:
            if r["id"] == "spatial_environmental_mutual_exclusion":
                r["depends_on"] = ["pose_hand_occupation"]

        meta_inv = copy.deepcopy(FROZEN_DAG_METADATA)
        meta_inv["spatial_environmental_mutual_exclusion"]["depends_on"] = ("pose_hand_occupation",)
        with patch.dict("lib.rule_contract.FROZEN_DAG_METADATA", meta_inv):
            with self.assertRaises(RuleConfigurationError) as ctx:
                parse_rule_document(doc)
            self.assertIn("cannot depend on rule", str(ctx.exception))

    # ───────────────────────────────────────────────────────────
    # 2. 十大关键反例 (R2-N01 ~ R2-N10)
    # ───────────────────────────────────────────────────────────

    def test_05_r2_n01_formal_catalog_missing_facts_and_zero_fallback(self):
        """关键反例 R2-N01: 正式目录 Atom 缺失必要 facts 必须被拦截，正式目录 text_fallback_hits 严格为 0。"""
        rng = Random(42)
        # 正式 preset 场景原子但 facts 缺失 space_kind 字段 -> 必须 Fail-Closed 拦截
        atoms = [
            make_atom("traditional onsen", "scene", idx=0, origin_mode="preset", facts=SemanticFacts()),
            make_atom("open air hot spring", "scene", idx=1, origin_mode="preset", facts=SemanticFacts(space_kind="outdoor")),
        ]
        with self.assertRaises(RuleConfigurationError):
            self.resolver.resolve_atoms_with_full_report(atoms, rng)
        self.assertEqual(self.resolver.text_fallback_hits, 0)

    def test_06_r2_n02_blackbox_spans_completely_opaque(self):
        """关键反例 R2-N02: 纯 ANGLE/QUOTED 内容完全不透明，绝不根据内部词触发冲突。"""
        rng = Random(42)
        atoms = [
            make_atom("indoor onsen", "scene", SpanType.PLAIN, idx=0),
            make_atom("<lora:rotenburo:1.0>", "clothing", SpanType.ANGLE, idx=1),
            make_atom('"outdoor bath view"', "props", SpanType.QUOTED, idx=2),
        ]
        resolved, applied, report = self.resolver.resolve_atoms_with_full_report(atoms, rng)
        self.assertEqual(len(resolved), 3)
        self.assertEqual(len(applied), 0)
        self.assertEqual(report.dropped_count, 0)
        self.assertEqual(report.replaced_count, 0)

    def test_07_r2_n03_non_deletable_protected_syntax_conflict(self):
        """关键反例 R2-N03: 不可删除的受保护复合语法在检测到残留硬冲突时抛出 UnresolvedConflictError。"""
        rng = Random(42)
        atoms = [
            make_atom("rotenburo", "scene", SpanType.PLAIN, idx=0),
            make_atom("(indoor onsen <lora:x:1>:1.2)", "scene", SpanType.PAREN, idx=1, contains_blackbox=True),
        ]
        with self.assertRaises(UnresolvedConflictError) as ctx:
            self.resolver.resolve_atoms_with_full_report(atoms, rng)
        self.assertEqual(ctx.exception.reason, "protected_syntax_conflict")

    def test_08_r2_n06_detect_hard_conflicts_catches_residual_conflicts(self):
        """关键反例 R2-N06: 只读最终硬冲突检测器准确捕获残余硬冲突。"""
        atoms = [
            make_atom("indoor onsen", "scene", SpanType.PLAIN, idx=0),
            make_atom("rotenburo", "scene", SpanType.PLAIN, idx=1),
        ]
        conflicts, has_prot = self.resolver.detect_hard_conflicts(atoms)
        self.assertTrue(len(conflicts) > 0)
        self.assertEqual(conflicts[0][0], "residual_indoor_outdoor_conflict")
        self.assertFalse(has_prot)

    def test_09_r2_n07_deterministic_order_tie_breaking(self):
        """关键反例 R2-N07: 并列候选以权威 tag_order 首项为胜者，消除 set/dict 偶然性。"""
        rng = Random(42)
        atoms_1 = [
            make_atom("direct eye contact", "expression", SpanType.PLAIN, idx=0),
            make_atom("looking away", "expression", SpanType.PLAIN, idx=1),
        ]
        resolved_1 = self.resolver.resolve_atoms(atoms_1, rng)
        texts_1 = [a.text for a in resolved_1]
        self.assertIn("direct eye contact", texts_1)
        self.assertNotIn("looking away", texts_1)

        atoms_2 = [
            make_atom("looking away", "expression", SpanType.PLAIN, idx=0),
            make_atom("direct eye contact", "expression", SpanType.PLAIN, idx=1),
        ]
        resolved_2 = self.resolver.resolve_atoms(atoms_2, rng)
        texts_2 = [a.text for a in resolved_2]
        self.assertIn("looking away", texts_2)
        self.assertNotIn("direct eye contact", texts_2)

    def test_10_r2_n08_idempotent_closed_loop_second_resolve(self):
        """关键反例 R2-N08: 二次消解产生 0 decisions，哈希严格守恒。"""
        rng = Random(42)
        atoms = [
            make_atom("completely naked", "nudity", SpanType.PLAIN, idx=0),
            make_atom("wearing uniform", "clothing", SpanType.PLAIN, idx=1),
            make_atom("wearing skirt", "clothing", SpanType.PLAIN, idx=2),
        ]
        res_1, applied_1, rep_1 = self.resolver.resolve_atoms_with_full_report(atoms, rng)
        self.assertTrue(len(rep_1.decisions) > 0)

        # 第二次消解
        res_2, applied_2, rep_2 = self.resolver.resolve_atoms_with_full_report(res_1, rng)
        self.assertEqual(len(rep_2.decisions), 0, "Second resolve must produce 0 decisions")
        self.assertEqual(rep_2.dropped_count, 0)
        self.assertEqual(rep_2.replaced_count, 0)
        self.assertEqual(rep_2.injected_count, 0)
        self.assertEqual(rep_2.input_atom_hash, rep_2.output_atom_hash)

    def test_11_r2_n09_zero_runtime_re_compile_calls(self):
        """关键反例 R2-N09: 运行期零 re.compile() 调用拦截与 17 规则 D2 覆盖门禁 (P1)。
        1. 验证 RuleRegistry 加载期深度遍历预编译了全部 1,078 个 PatternSpec (含 501 个 fallback patterns)，未编译数为 0；
        2. 监控 re.compile 调用，复用现有 17x8 矩阵中已验证的 D2 custom fixtures 逐条断言：
           - 夹具键精确等于 DAG_FROZEN_ORDER；
           - 目标规则出现在 rules_applied；
           - text_fallback_hits > 0；
           - 最终零残留硬冲突；
           - resolver 构造完成后 re.compile() 运行期调用数为 0；
        3. 增加三项自变异测试：
           - 缺少一个真实规则；
           - 伪造规则 ID；
           - 真实夹具不触发；
           均断言触发覆盖门禁失败 (AssertionError)。
        """
        # 1. 构造全新 resolver，验证加载期全部 1078 个 PatternSpec 完成预编译，未编译数为 0
        resolver = ConflictResolver(DATA_DIR)
        self.assertEqual(resolver.registry.total_precompiled_patterns, 1078)
        self.assertEqual(resolver.registry.precompiled_fallback_count, 501)
        self.assertEqual(resolver.registry.uncompiled_patterns_count, 0)

        # 2. 复用现有 17x8 矩阵中已验证的 D2 custom fixtures，验证夹具键精确等于 DAG_FROZEN_ORDER
        d2_fixtures = build_d2_custom_fixtures()
        self.assertEqual(tuple(d2_fixtures.keys()), DAG_FROZEN_ORDER)

        # 3. 监控运行期 re.compile 调用并执行全部 17 规则 D2 覆盖门禁
        compile_calls = []
        orig_compile = re.compile

        def tracking_compile(*args, **kwargs):
            compile_calls.append(args)
            return orig_compile(*args, **kwargs)

        try:
            re.compile = tracking_compile
            _run_d2_coverage_gate(d2_fixtures, resolver)
        finally:
            re.compile = orig_compile

        # 断言运行期零 re.compile 调用
        self.assertEqual(len(compile_calls), 0, f"Runtime re.compile called {len(compile_calls)} times: {compile_calls[:5]}")

        # 4. 三项自变异断言：均必须使覆盖门禁失败 (AssertionError)
        # 自变异 A: 缺少一个真实规则
        mut_a = dict(d2_fixtures)
        del mut_a["tattoo_dermal_fusion"]
        with self.assertRaises(AssertionError) as ctx_a:
            _run_d2_coverage_gate(mut_a, resolver)
        self.assertIn("missing", str(ctx_a.exception))

        # 自变异 B: 伪造规则 ID
        mut_b = dict(d2_fixtures)
        del mut_b["tattoo_dermal_fusion"]
        mut_b["fake_bogus_rule"] = [make_atom("test", "scene", idx=0, origin_mode="custom")]
        with self.assertRaises(AssertionError) as ctx_b:
            _run_d2_coverage_gate(mut_b, resolver)
        self.assertIn("extra", str(ctx_b.exception))

        # 自变异 C: 真实夹具不触发
        mut_c = dict(d2_fixtures)
        mut_c["nudity_clothing_conflicts"] = [
            make_atom("peaceful mountain landscape", "scene", idx=0, origin_mode="custom")
        ]
        with self.assertRaises(AssertionError) as ctx_c:
            _run_d2_coverage_gate(mut_c, resolver)
        self.assertIn("did not trigger target rule", str(ctx_c.exception))

    def test_12_r2_n10_affinity_error_on_invalid_data(self):
        """关键反例 R2-N10: 亲和数据含未知 ID 或非有限权重触发 AffinityConfigurationError。"""
        from lib.context_affinity import ContextAffinityRegistry
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            for f in DATA_DIR.glob("*.json"):
                import shutil
                shutil.copy(f, tmppath / f.name)

            aff_file = tmppath / "context_affinity.json"
            data = json.loads(aff_file.read_text(encoding="utf-8"))
            data["matrix"]["school"]["clothing"]["candidates"][0]["id"] = "totally_unknown_cloth_xyz"
            aff_file.write_text(json.dumps(data), encoding="utf-8")

            with self.assertRaises(AffinityConfigurationError):
                ContextAffinityRegistry(tmppath)

    # ───────────────────────────────────────────────────────────
    # 3. 十大权威关系验证
    # ───────────────────────────────────────────────────────────

    def test_13_auth_01_emotion_overrides_gaze(self):
        """权威关系 1: 情绪胜眼神 (emotion_gaze_affinity)。"""
        atoms = [
            make_atom("shy expression", "expression", SpanType.PLAIN, idx=0),
            make_atom("seductive smile", "expression", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("shy expression", texts)
        self.assertNotIn("seductive smile", texts)

    def test_14_auth_02_full_nude_drops_clothing(self):
        """权威关系 2: 全裸脱衣 (nudity_clothing_conflicts)。"""
        atoms = [
            make_atom("completely naked", "nudity", SpanType.PLAIN, idx=0),
            make_atom("wearing uniform", "clothing", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("completely naked", texts)
        self.assertNotIn("wearing uniform", texts)

    def test_15_auth_03_full_nude_strips_clothing_state(self):
        """权威关系 3: 连体衣禁止掀裙解扣 (clothing_style_state_coherence)。"""
        atoms = [
            make_atom("school swimsuit (sukumizu)", "clothing", SpanType.PLAIN, idx=0),
            make_atom("unbuttoned blouse", "clothing", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("school swimsuit (sukumizu)", texts)
        self.assertNotIn("unbuttoned blouse", texts)

    def test_16_auth_04_cu_shot_trims_full_body_features(self):
        """权威关系 4: 特写构图修剪全身动作与下半身特征 (framing_lower_body_coherence)。"""
        atoms = [
            make_atom("close-up", "shot_type", SpanType.PLAIN, idx=0),
            make_atom("high heels", "clothing", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("close-up", texts)
        self.assertNotIn("high heels", texts)

    def test_17_auth_05_indoor_outdoor_mutual_exclusion(self):
        """权威关系 5: 室内室外空间互斥 (spatial_environmental_mutual_exclusion)。"""
        atoms = [
            make_atom("indoor onsen", "scene", SpanType.PLAIN, idx=0),
            make_atom("rotenburo", "scene", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("indoor onsen", texts)
        self.assertNotIn("rotenburo", texts)

    def test_18_auth_06_liquid_restrictions_coherence(self):
        """权威关系 6: 液体效果合规重定向 (liquid_restrictions)。"""
        atoms = [
            make_atom("cum on closed eyes", "liquids", SpanType.PLAIN, idx=0),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertTrue(any("cum on cheek" in t for t in texts))
        self.assertNotIn("cum on closed eyes", texts)

    def test_19_auth_07_monochrome_chroma_coherence(self):
        """权威关系 7: 黑白胶片抑制高饱和彩色词 (monochrome_film_chroma_coherence)。"""
        atoms = [
            make_atom("classic monochrome", "film", SpanType.PLAIN, idx=0),
            make_atom("neon rim light", "lighting", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("classic monochrome", texts)
        self.assertNotIn("neon rim light", texts)

    def test_20_auth_08_busy_hands_drops_handheld_props(self):
        """权威关系 8: 忙碌体位约束手持道具 (pose_hand_occupation)。"""
        atoms = [
            make_atom("hands behind back", "pose", SpanType.PLAIN, idx=0),
            make_atom("holding smartphone", "props", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("hands behind back", texts)
        self.assertNotIn("holding smartphone", texts)

    def test_21_auth_09_single_holder_handheld_props(self):
        """权威关系 9: 手持道具单持有者互斥 (handheld_props_single_holder)。"""
        atoms = [
            make_atom("holding smartphone", "props", SpanType.PLAIN, idx=0),
            make_atom("holding camera", "props", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        holding_tags = [t for t in texts if "holding" in t]
        self.assertEqual(len(holding_tags), 1)

    def test_22_auth_10_blindfold_drops_gaze_direction(self):
        """权威关系 10: 遮眼饰品抑制视线动作 (accessory_occlusion_gaze_coherence)。"""
        atoms = [
            make_atom("blindfold", "jewelry", SpanType.PLAIN, idx=0),
            make_atom("eye-fucking the viewer", "expression", SpanType.PLAIN, idx=1),
        ]
        resolved = self.resolver.resolve_atoms(atoms, Random(42))
        texts = [a.text for a in resolved]
        self.assertIn("blindfold", texts)
        self.assertNotIn("eye-fucking the viewer", texts)

    # ───────────────────────────────────────────────────────────
    # 4. 黑盒语法 5 种形态测试
    # ───────────────────────────────────────────────────────────

    def test_23_blackbox_five_forms_integrity(self):
        """黑盒语法 5 种形态完整性与安全隔离测试。"""
        rng = Random(42)
        atoms = [
            # 形态 1: 双引号
            make_atom('"sensual bedroom glow"', "lighting", SpanType.QUOTED, idx=0),
            # 形态 2: LoRA 尖括号
            make_atom("<lora:cosplay_v1:0.8>", "clothing", SpanType.ANGLE, idx=1),
            # 形态 3: Embedding 尖括号
            make_atom("<embedding:fast_negative:1.0>", "quality", SpanType.ANGLE, idx=2),
            # 形态 4: 基础复合权重 (PAREN)
            make_atom("(delicate lace:1.15)", "clothing", SpanType.PAREN, idx=3),
            # 形态 5: 多重嵌套复合权重 (BRACKET)
            make_atom("[tag1:tag2:10]", "props", SpanType.BRACKET, idx=4),
        ]

        resolved, applied, report = self.resolver.resolve_atoms_with_full_report(atoms, rng)
        self.assertEqual(len(resolved), 5)
        for i in range(5):
            self.assertEqual(resolved[i].text, atoms[i].text)

    # ───────────────────────────────────────────────────────────
    # 5. 17 大规则 8 维度全量覆盖矩阵 (136 项维度验证)
    # ───────────────────────────────────────────────────────────

    def test_24_all_17_rules_8_dimensional_matrix(self):
        """17 大规则 × 8 维度全量矩阵门禁 (136 项维度强契约验证)。
        八大维度：
          1. 结构化语义正例 (formal catalog facts, 0 fallback hits)
          2. 自由文本 fallback 正例 (custom text pattern, fallback_hits > 0)
          3. 规则专属负例 (Distinct rule-specific negative cases, 0 decisions)
          4. 显式输入冲突 (mode="explicit")
          5. 输入排列 (Permutation tie-breaking follows global tag_order, deterministic)
          6. 真实两规则级联 (Non-colliding slots to guarantee both rules fire in sequence)
          7. 第二次消解零 decision 幂等
          8. 冲突败者承载保护语法 (Protected syntax on ACTUAL LOSER, raising UnresolvedConflictError)
        """
        rng = Random(42)

        # 1. 结构化语义正例 (formal catalog facts, 0 fallback hits)
        structured_fixtures = {
            "spatial_environmental_mutual_exclusion": [
                make_atom("traditional onsen", "scene", idx=0, origin_mode="preset", facts=SemanticFacts(space_kind="indoor", time_of_day="day")),
                make_atom("open air hot spring", "scene", idx=1, origin_mode="preset", facts=SemanticFacts(space_kind="outdoor", time_of_day="day")),
            ],
            "nudity_clothing_conflicts": [
                make_atom("completely naked", "nudity", idx=0, origin_mode="preset", facts=SemanticFacts(visible_regions=("full_body",)), item_id="nudity_l5"),
                make_atom("school uniform", "clothing", idx=1, origin_mode="preset", facts=SemanticFacts(garment_topologies=("top",), visible_regions=("upper_body",))),
            ],
            "framing_lower_body_coherence": [
                make_atom("extreme close-up", "shot_type", idx=0, origin_mode="preset", facts=SemanticFacts(visible_regions=("face",)), item_id="extreme_close_up"),
                make_atom("leather boots", "clothing", idx=1, origin_mode="preset", facts=SemanticFacts(visible_regions=("feet",), garment_topologies=("bottom_pants",))),
            ],
            "pose_hand_occupation": [
                make_atom("hands behind head", "pose", idx=0, origin_mode="preset", facts=SemanticFacts(hand_state="both_busy")),
                make_atom("holding umbrella", "props", idx=1, origin_mode="preset", facts=SemanticFacts(hands_required=1, prop_usage="handheld")),
            ],
            "handheld_props_single_holder": [
                make_atom("holding drink", "props", idx=0, origin_mode="preset", facts=SemanticFacts(hands_required=1, prop_usage="handheld")),
                make_atom("holding fan", "props", idx=1, origin_mode="preset", facts=SemanticFacts(hands_required=1, prop_usage="handheld")),
            ],
            "clothing_style_state_coherence": [
                make_atom("one-piece dress", "clothing", idx=0, origin_mode="preset", facts=SemanticFacts(garment_topologies=("one_piece",))),
                make_atom("opened blouse", "clothing", idx=1, origin_mode="preset", facts=SemanticFacts(garment_states=("opened",))),
            ],
            "material_penetration": [
                make_atom("sheer silk fabric", "clothing", idx=0, origin_mode="preset", facts=SemanticFacts(garment_states=("wet_clinging",), garment_topologies=("top",))),
            ],
            "device_quality_compatibility": [
                make_atom("cctv camera security footage", "shot_type", idx=0, origin_mode="preset", facts=SemanticFacts(capture_device="cctv", visible_regions=("upper_body",)), item_id="cctv"),
                make_atom("masterpiece high definition", "quality", idx=1, origin_mode="preset", facts=SemanticFacts(quality_class="masterpiece"), item_id="masterpiece"),
            ],
            "environmental_lighting_coherence": [
                make_atom("midnight starry night", "scene", idx=0, origin_mode="preset", facts=SemanticFacts(space_kind="outdoor", time_of_day="night")),
                make_atom("bright daylight", "lighting", idx=1, origin_mode="preset", facts=SemanticFacts(light_sources=("daylight",), time_of_day="day")),
            ],
            "monochrome_film_chroma_coherence": [
                make_atom("monochrome black and white", "film", idx=0, origin_mode="preset", facts=SemanticFacts(color_modes=("monochrome",))),
                make_atom("vibrant neon colors", "lighting", idx=1, origin_mode="preset", facts=SemanticFacts(color_modes=("high_saturation",))),
            ],
            "makeup_details_coherence": [
                make_atom("natural bare makeup", "makeup", idx=0, origin_mode="preset", facts=SemanticFacts(makeup_base="clean")),
                make_atom("smeared runny mascara", "makeup", idx=1, origin_mode="preset", facts=SemanticFacts(makeup_effects=("smudged",))),
            ],
            "gaze_angle_geometry": [
                make_atom("overhead top-down perspective", "camera_angle", idx=0, origin_mode="preset", facts=SemanticFacts(gaze="down"), item_id="overhead"),
                make_atom("looking down from above", "expression", idx=1, origin_mode="preset", facts=SemanticFacts(gaze="down")),
            ],
            "accessory_occlusion_gaze_coherence": [
                make_atom("silk blindfold covering eyes", "jewelry", idx=0, origin_mode="preset", facts=SemanticFacts(occlusion="eyes")),
                make_atom("looking at camera", "expression", idx=1, origin_mode="preset", facts=SemanticFacts(gaze="camera")),
            ],
            "emotion_gaze_affinity": [
                make_atom("shy blushing expression", "expression", idx=0, origin_mode="preset", facts=SemanticFacts(emotion="shy")),
                make_atom("seductive look", "expression", idx=1, origin_mode="preset", facts=SemanticFacts(emotion="seductive", gaze="camera")),
            ],
            "gaze_mutual_exclusion": [
                make_atom("looking at camera", "expression", idx=0, origin_mode="preset", facts=SemanticFacts(gaze="camera")),
                make_atom("looking away", "expression", idx=1, origin_mode="preset", facts=SemanticFacts(gaze="away")),
            ],
            "liquid_restrictions": [
                make_atom("cum on closed eyes", "liquids", idx=0, origin_mode="preset", facts=SemanticFacts(liquid_kind="sexual_fluid", liquid_locations=("face",))),
            ],
            "tattoo_dermal_fusion": [
                make_atom("dragon tattoo on back", "tattoo", idx=0, origin_mode="preset", facts=SemanticFacts(), item_id="dragon_tattoo"),
            ],
        }

        # 2. 自由文本 fallback 正例 (复用 D2 verified custom fixtures)
        custom_fixtures = build_d2_custom_fixtures()

        # 3. 规则专属负例 (Distinct rule-specific negative cases, 0 decisions)
        negative_fixtures = {
            "spatial_environmental_mutual_exclusion": [
                make_atom("traditional tatami room", "scene", idx=0, origin_mode="preset", facts=SemanticFacts(space_kind="indoor", time_of_day="day")),
                make_atom("cozy bedroom", "scene", idx=1, origin_mode="preset", facts=SemanticFacts(space_kind="indoor", time_of_day="day")),
            ],
            "nudity_clothing_conflicts": [
                make_atom("school uniform", "clothing", idx=0, origin_mode="preset", facts=SemanticFacts(garment_topologies=("top",), visible_regions=("upper_body",))),
                make_atom("leather loafers", "clothing", idx=1, origin_mode="preset", facts=SemanticFacts(garment_topologies=("bottom_pants",), visible_regions=("feet",))),
            ],
            "framing_lower_body_coherence": [
                make_atom("full body wide view", "shot_type", idx=0, origin_mode="preset", facts=SemanticFacts(visible_regions=("full_body",)), item_id="full_body"),
                make_atom("high heels", "clothing", idx=1, origin_mode="preset", facts=SemanticFacts(visible_regions=("feet",), garment_topologies=("bottom_pants",))),
            ],
            "pose_hand_occupation": [
                make_atom("standing peacefully", "pose", idx=0, origin_mode="preset", facts=SemanticFacts(hand_state="free")),
                make_atom("holding umbrella", "props", idx=1, origin_mode="preset", facts=SemanticFacts(hands_required=1, prop_usage="handheld")),
            ],
            "handheld_props_single_holder": [
                make_atom("holding umbrella", "props", idx=0, origin_mode="preset", facts=SemanticFacts(hands_required=1, prop_usage="handheld")),
                make_atom("leather backpack", "props", idx=1, origin_mode="preset", facts=SemanticFacts(hands_required=0, prop_usage="worn")),
            ],
            "clothing_style_state_coherence": [
                make_atom("buttoned blouse", "clothing", idx=0, origin_mode="preset", facts=SemanticFacts(garment_topologies=("top",), garment_states=("worn",))),
                make_atom("pleated skirt", "clothing", idx=1, origin_mode="preset", facts=SemanticFacts(garment_topologies=("bottom_skirt",))),
            ],
            "material_penetration": [
                make_atom("heavy wool coat", "clothing", idx=0, origin_mode="preset", facts=SemanticFacts(garment_states=("worn",), garment_topologies=("top",))),
            ],
            "device_quality_compatibility": [
                make_atom("dslr professional camera", "shot_type", idx=0, origin_mode="preset", facts=SemanticFacts(capture_device="professional", visible_regions=("upper_body",)), item_id="dslr"),
                make_atom("masterpiece high definition", "quality", idx=1, origin_mode="preset", facts=SemanticFacts(quality_class="masterpiece"), item_id="masterpiece"),
            ],
            "environmental_lighting_coherence": [
                make_atom("sunny park noon", "scene", idx=0, origin_mode="preset", facts=SemanticFacts(space_kind="outdoor", time_of_day="day")),
                make_atom("natural sunlight", "lighting", idx=1, origin_mode="preset", facts=SemanticFacts(light_sources=("daylight",), time_of_day="day")),
            ],
            "monochrome_film_chroma_coherence": [
                make_atom("kodak colorplus 200", "film", idx=0, origin_mode="preset", facts=SemanticFacts(color_modes=("color",))),
                make_atom("golden hour warm amber", "lighting", idx=1, origin_mode="preset", facts=SemanticFacts(color_modes=("color",))),
            ],
            "makeup_details_coherence": [
                make_atom("natural nude makeup", "makeup", idx=0, origin_mode="preset", facts=SemanticFacts(makeup_base="clean")),
                make_atom("soft peach blush", "makeup", idx=1, origin_mode="preset", facts=SemanticFacts(makeup_effects=("flush",))),
            ],
            "gaze_angle_geometry": [
                make_atom("eye level shot", "camera_angle", idx=0, origin_mode="preset", facts=SemanticFacts(gaze="camera"), item_id="straight_on"),
                make_atom("looking at viewer", "expression", idx=1, origin_mode="preset", facts=SemanticFacts(gaze="camera")),
            ],
            "accessory_occlusion_gaze_coherence": [
                make_atom("delicate pearl necklace", "jewelry", idx=0, origin_mode="preset", facts=SemanticFacts(occlusion="none")),
                make_atom("direct eye contact", "expression", idx=1, origin_mode="preset", facts=SemanticFacts(gaze="camera")),
            ],
            "emotion_gaze_affinity": [
                make_atom("gentle happy smile", "expression", idx=0, origin_mode="preset", facts=SemanticFacts(emotion="neutral", gaze="camera")),
            ],
            "gaze_mutual_exclusion": [
                make_atom("direct eye contact", "expression", idx=0, origin_mode="preset", facts=SemanticFacts(gaze="camera")),
            ],
            "liquid_restrictions": [
                make_atom("delicate rain drops on skin", "liquids", idx=0, origin_mode="preset", facts=SemanticFacts(liquid_kind="water", liquid_locations=("skin",), liquid_amount="normal")),
            ],
            "tattoo_dermal_fusion": [
                make_atom("vintage wristwatch", "props", idx=0, origin_mode="preset", facts=SemanticFacts(prop_usage="worn")),
            ],
        }

        # 6. 真实两规则级联定义表 (R2R4-P1-002: 状态/墓碑消费与候选集行为差分证明)
        def make_cascade_atom(text, slot, idx, facts=None, item_id=None, mode="preset"):
            actual_id = item_id or f"item_{idx:03d}"
            return PromptAtom(
                atom_id=f"atom_{idx:03d}",
                text=text,
                span_type=SpanType.PLAIN,
                source_slot=slot,
                source_item_id=actual_id,
                tag_order=idx,
                span_order=idx,
                provenance=TagProvenance(item_id=actual_id, parent_ids=(f"src_{idx:03d}",)),
                origin=SelectionOrigin(mode=mode, selector=slot),
                facts=facts or SemanticFacts(),
            )

        def make_cascade_custom_atom(text, slot, idx):
            return PromptAtom(
                atom_id=f"atom_{idx:03d}",
                text=text,
                span_type=SpanType.PLAIN,
                source_slot=slot,
                source_item_id=f"item_{idx:03d}",
                tag_order=idx,
                span_order=idx,
                provenance=TagProvenance(item_id=f"item_{idx:03d}", parent_ids=(f"src_{idx:03d}",)),
                origin=SelectionOrigin(mode="custom", selector=slot),
                facts=SemanticFacts(),
            )

        D6_CASCADE_DEFS = {
            # 1. spatial_environmental_mutual_exclusion
            "spatial_environmental_mutual_exclusion": {
                "earlier_rid": "spatial_environmental_mutual_exclusion",
                "later_rid": "environmental_lighting_coherence",
                "atoms": [
                    make_cascade_atom("sunny outdoor park", "scene", 0, facts=SemanticFacts(space_kind="outdoor", time_of_day="day"), item_id="open_air_hot_spring"),
                    make_cascade_atom("cozy indoor bedroom", "scene", 1, facts=SemanticFacts(space_kind="indoor", time_of_day="night"), item_id="traditional_onsen"),
                    make_cascade_atom("natural sunlight", "lighting", 2, facts=SemanticFacts(light_sources=("daylight",), time_of_day="day"), item_id="natural_sunlight"),
                ],
                "intermediate_ids": ["atom_000", "atom_002"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "spatial_environmental_mutual_exclusion", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": None,  # lighting drops nothing on post-state
                "pre_dec_later": {"rule_id": "environmental_lighting_coherence", "action": "drop", "winner": ("atom_001",), "target": "atom_002"},
            },
            # 2. nudity_clothing_conflicts
            "nudity_clothing_conflicts": {
                "earlier_rid": "nudity_clothing_conflicts",
                "later_rid": "clothing_style_state_coherence",
                "atoms": [
                    make_cascade_atom("topless bare breasts", "nudity", 0, facts=SemanticFacts(visible_regions=("upper_body",)), item_id="nudity_l4"),
                    make_cascade_atom("one-piece dress", "clothing", 1, facts=SemanticFacts(visible_regions=("upper_body",), garment_topologies=("one_piece",), garment_states=("worn",)), item_id="item_001"),
                    make_cascade_atom("unbuttoned blouse", "clothing", 2, facts=SemanticFacts(visible_regions=("upper_body",), garment_topologies=("top",), garment_states=("opened",)), item_id="item_002"),
                ],
                "intermediate_ids": ["atom_000", "atom_002"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "nudity_clothing_conflicts", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": None,  # clothing_style has 0 decisions because one_piece was tombstoned
                "pre_dec_later": {"rule_id": "clothing_style_state_coherence", "action": "drop", "winner": ("atom_001",), "target": "atom_002"},
            },
            # 3. framing_lower_body_coherence
            "framing_lower_body_coherence": {
                "earlier_rid": "framing_lower_body_coherence",
                "later_rid": "clothing_style_state_coherence",
                "atoms": [
                    make_cascade_atom("close up shot", "shot_type", 0, facts=SemanticFacts(visible_regions=("face",)), item_id="close_up"),
                    make_cascade_atom("denim jeans opened", "clothing", 1, facts=SemanticFacts(visible_regions=("lower_body",), garment_topologies=("bottom_pants",), garment_states=("opened",)), item_id="item_001"),
                    make_cascade_atom("one-piece dress", "clothing", 2, facts=SemanticFacts(visible_regions=("upper_body",), garment_topologies=("one_piece",), garment_states=("worn",)), item_id="item_002"),
                    make_cascade_atom("unbuttoned blouse", "clothing", 3, facts=SemanticFacts(visible_regions=("upper_body",), garment_topologies=("top",), garment_states=("opened",)), item_id="item_003"),
                ],
                "intermediate_ids": ["atom_000", "atom_002", "atom_003"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "framing_lower_body_coherence", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "clothing_style_state_coherence", "action": "drop", "winner": ("atom_002",), "target": "atom_003", "produced": (), "sequence": 1},
                "pre_dec_later": {"rule_id": "clothing_style_state_coherence", "action": "drop", "winner": ("atom_002",), "target": "atom_001"},
            },
            # 4. pose_hand_occupation & 5. handheld_props_single_holder
            "pose_hand_occupation": {
                "earlier_rid": "pose_hand_occupation",
                "later_rid": "handheld_props_single_holder",
                "atoms": [
                    make_cascade_custom_atom("hands clasped in prayer", "pose", 0),
                    make_cascade_custom_atom("holding smartphone", "props", 1),
                    make_cascade_custom_atom("holding umbrella", "props", 2),
                    make_cascade_custom_atom("holding bouquet", "props", 3),
                ],
                "intermediate_ids": ["atom_000", "atom_002", "atom_003"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "pose_hand_occupation", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "handheld_props_single_holder", "action": "drop", "winner": ("atom_002",), "target": "atom_003", "produced": (), "sequence": 1},
                "pre_dec_later": {"rule_id": "handheld_props_single_holder", "action": "drop", "winner": ("atom_001",), "target": "atom_002"},
            },
            # 6. clothing_style_state_coherence & 7. material_penetration
            "clothing_style_state_coherence": {
                "earlier_rid": "clothing_style_state_coherence",
                "later_rid": "material_penetration",
                "atoms": [
                    make_cascade_atom("one-piece cheongsam dress", "clothing", 0, facts=SemanticFacts(visible_regions=("upper_body", "lower_body"), garment_topologies=("one_piece",), garment_states=("worn",))),
                    make_cascade_atom("sheer unbuttoned cardigan", "clothing", 1, facts=SemanticFacts(visible_regions=("upper_body",), garment_topologies=("top",), garment_states=("opened", "wet_clinging"))),
                    make_cascade_atom("sheer silk blouse", "clothing", 2, facts=SemanticFacts(visible_regions=("upper_body",), garment_topologies=("top",), garment_states=("worn", "wet_clinging"))),
                ],
                "intermediate_ids": ["atom_000", "atom_002"],
                "final_ids": ["atom_000", "atom_002__r_material_penetration"],
                "dec_0": {"rule_id": "clothing_style_state_coherence", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "material_penetration", "action": "replace", "winner": (), "target": "atom_002", "produced": ("atom_002__r_material_penetration",), "sequence": 1},
                "pre_dec_later": {"rule_id": "material_penetration", "action": "replace", "winner": (), "target": "atom_001"},
            },
            # 8. device_quality_compatibility
            "device_quality_compatibility": {
                "earlier_rid": "device_quality_compatibility",
                "later_rid": "tattoo_dermal_fusion",
                "atoms": [
                    make_cascade_custom_atom("cctv security camera footage", "shot_type", 0),
                    make_cascade_custom_atom("masterpiece high definition with dragon body art", "quality", 1),
                    make_cascade_custom_atom("lotus tattoo on shoulder", "props", 2),
                ],
                "intermediate_ids": ["atom_000", "atom_002"],
                "final_ids": ["atom_000", "atom_002", "atom_injected_atom_002_tattoo_dermal_fusion"],
                "dec_0": {"rule_id": "device_quality_compatibility", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "tattoo_dermal_fusion", "action": "inject", "winner": ("atom_002",), "target": None, "produced": ("atom_injected_atom_002_tattoo_dermal_fusion",), "sequence": 1},
                "pre_dec_later": {"rule_id": "tattoo_dermal_fusion", "action": "inject", "winner": ("atom_001",), "target": None},
            },
            # 9. environmental_lighting_coherence & 10. monochrome_film_chroma_coherence
            "environmental_lighting_coherence": {
                "earlier_rid": "environmental_lighting_coherence",
                "later_rid": "monochrome_film_chroma_coherence",
                "atoms": [
                    make_cascade_atom("midnight starry night", "scene", 0, facts=SemanticFacts(space_kind="outdoor", time_of_day="night"), item_id="midnight_scene"),
                    make_cascade_atom("bright daylight neon glow", "lighting", 1, facts=SemanticFacts(light_sources=("daylight",), time_of_day="day", color_modes=("high_saturation",)), item_id="daylight_neon"),
                    make_cascade_atom("monochrome black and white film", "film", 2, facts=SemanticFacts(color_modes=("monochrome",)), item_id="mono_film"),
                    make_cascade_atom("vibrant colored backlight", "lighting", 3, facts=SemanticFacts(color_modes=("color",), time_of_day="night"), item_id="color_light"),
                ],
                "intermediate_ids": ["atom_000", "atom_002", "atom_003"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "environmental_lighting_coherence", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "monochrome_film_chroma_coherence", "action": "drop", "winner": ("atom_002",), "target": "atom_003", "produced": (), "sequence": 1},
                "pre_dec_later": {"rule_id": "monochrome_film_chroma_coherence", "action": "drop", "winner": ("atom_002",), "target": "atom_001"},
            },
            # 11. makeup_details_coherence
            "makeup_details_coherence": {
                "earlier_rid": "makeup_details_coherence",
                "later_rid": "tattoo_dermal_fusion",
                "atoms": [
                    make_cascade_custom_atom("natural makeup", "makeup", 0),
                    make_cascade_custom_atom("smeared lipstick on cheek and dragon body art", "makeup", 1),
                    make_cascade_custom_atom("lotus tattoo on shoulder", "props", 2),
                ],
                "intermediate_ids": ["atom_000", "atom_002"],
                "final_ids": ["atom_000", "atom_002", "atom_injected_atom_002_tattoo_dermal_fusion"],
                "dec_0": {"rule_id": "makeup_details_coherence", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "tattoo_dermal_fusion", "action": "inject", "winner": ("atom_002",), "target": None, "produced": ("atom_injected_atom_002_tattoo_dermal_fusion",), "sequence": 1},
                "pre_dec_later": {"rule_id": "tattoo_dermal_fusion", "action": "inject", "winner": ("atom_001",), "target": None},
            },
            # 12. gaze_angle_geometry & 15. gaze_mutual_exclusion
            "gaze_angle_geometry": {
                "earlier_rid": "gaze_angle_geometry",
                "later_rid": "gaze_mutual_exclusion",
                "atoms": [
                    make_cascade_atom("overhead top down", "camera_angle", 0, facts=SemanticFacts(gaze="down"), item_id="overhead"),
                    make_cascade_atom("looking down from above", "expression", 1, facts=SemanticFacts(gaze="down")),
                    make_cascade_atom("direct eye contact with viewer", "expression", 2, facts=SemanticFacts(gaze="camera")),
                    make_cascade_atom("looking away to side", "expression", 3, facts=SemanticFacts(gaze="away")),
                ],
                "intermediate_ids": ["atom_000", "atom_002", "atom_003"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "gaze_angle_geometry", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "gaze_mutual_exclusion", "action": "drop", "winner": ("atom_002",), "target": "atom_003", "produced": (), "sequence": 1},
                "pre_dec_later": {"rule_id": "gaze_mutual_exclusion", "action": "drop", "winner": ("atom_001",), "target": "atom_002"},
            },
            # 13. accessory_occlusion_gaze_coherence
            "accessory_occlusion_gaze_coherence": {
                "earlier_rid": "accessory_occlusion_gaze_coherence",
                "later_rid": "gaze_mutual_exclusion",
                "atoms": [
                    make_cascade_atom("blindfold", "jewelry", 0, facts=SemanticFacts(occlusion="eyes")),
                    make_cascade_atom("direct gaze", "expression", 1, facts=SemanticFacts(gaze="camera")),
                    make_cascade_atom("looking away", "expression", 2, facts=SemanticFacts(gaze="away")),
                    make_cascade_atom("looking down", "expression", 3, facts=SemanticFacts(gaze="down")),
                ],
                "intermediate_ids": ["atom_000", "atom_002", "atom_003"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "accessory_occlusion_gaze_coherence", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "gaze_mutual_exclusion", "action": "drop", "winner": ("atom_002",), "target": "atom_003", "produced": (), "sequence": 1},
                "pre_dec_later": {"rule_id": "gaze_mutual_exclusion", "action": "drop", "winner": ("atom_001",), "target": "atom_002"},
            },
            # 14. emotion_gaze_affinity
            "emotion_gaze_affinity": {
                "earlier_rid": "emotion_gaze_affinity",
                "later_rid": "gaze_mutual_exclusion",
                "atoms": [
                    make_cascade_atom("shy gentle blush", "expression", 0, facts=SemanticFacts(emotion="shy")),
                    make_cascade_atom("seductive alluring gaze", "expression", 1, facts=SemanticFacts(emotion="seductive", gaze="camera")),
                    make_cascade_atom("looking away", "expression", 2, facts=SemanticFacts(gaze="away")),
                    make_cascade_atom("looking down", "expression", 3, facts=SemanticFacts(gaze="down")),
                ],
                "intermediate_ids": ["atom_000", "atom_002", "atom_003"],
                "final_ids": ["atom_000", "atom_002"],
                "dec_0": {"rule_id": "emotion_gaze_affinity", "action": "drop", "winner": ("atom_000",), "target": "atom_001", "produced": (), "sequence": 0},
                "dec_1": {"rule_id": "gaze_mutual_exclusion", "action": "drop", "winner": ("atom_002",), "target": "atom_003", "produced": (), "sequence": 1},
                "pre_dec_later": {"rule_id": "gaze_mutual_exclusion", "action": "drop", "winner": ("atom_001",), "target": "atom_002"},
            },
            # 16. liquid_restrictions & 17. tattoo_dermal_fusion
            "liquid_restrictions": {
                "earlier_rid": "liquid_restrictions",
                "later_rid": "tattoo_dermal_fusion",
                "atoms": [
                    make_cascade_custom_atom("cum on dragon body art on closed eyes", "liquids", 0),
                    make_cascade_custom_atom("tribal tattoo band on arm", "props", 1),
                ],
                "intermediate_ids": ["atom_000__r_liquid_restrictions", "atom_001"],
                "final_ids": ["atom_000__r_liquid_restrictions", "atom_001", "atom_injected_atom_000__r_liquid_restrictions_tattoo_dermal_fusion"],
                "dec_0": {"rule_id": "liquid_restrictions", "action": "replace", "winner": (), "target": "atom_000", "produced": ("atom_000__r_liquid_restrictions",), "sequence": 0},
                "dec_1": {"rule_id": "tattoo_dermal_fusion", "action": "inject", "winner": ("atom_000__r_liquid_restrictions",), "target": None, "produced": ("atom_injected_atom_000__r_liquid_restrictions_tattoo_dermal_fusion",), "sequence": 1},
                "pre_dec_later": {"rule_id": "tattoo_dermal_fusion", "action": "inject", "winner": ("atom_000",), "target": None},
            },
        }

        D6_CASCADE_DEFS["handheld_props_single_holder"] = D6_CASCADE_DEFS["pose_hand_occupation"]
        D6_CASCADE_DEFS["material_penetration"] = D6_CASCADE_DEFS["clothing_style_state_coherence"]
        D6_CASCADE_DEFS["monochrome_film_chroma_coherence"] = D6_CASCADE_DEFS["environmental_lighting_coherence"]
        D6_CASCADE_DEFS["gaze_mutual_exclusion"] = D6_CASCADE_DEFS["gaze_angle_geometry"]
        D6_CASCADE_DEFS["tattoo_dermal_fusion"] = D6_CASCADE_DEFS["liquid_restrictions"]

        EXPECTED_204_KEYS = get_expected_204_matrix_keys()
        self.assertEqual(len(EXPECTED_204_KEYS), 204, "Expected exactly 204 keys in matrix definition")

        executed_keys = set()

        for rid in DAG_FROZEN_ORDER:
            s_atoms = structured_fixtures[rid]

            # 1. 结构化语义正例 (formal catalog facts, 0 fallback hits)
            res_s, app_s, rep_s = self.resolver.resolve_atoms_with_full_report(s_atoms, rng)
            self.assertIn(rid, app_s, f"[{rid}] D1 failed: rule not triggered (applied: {app_s})")
            self.assertEqual(self.resolver.text_fallback_hits, 0, f"[{rid}] D1 failed: text_fallback_hits was {self.resolver.text_fallback_hits}")
            executed_keys.add(f"{rid}::D1")

            # 2. 自由文本 fallback 正例 (custom text pattern, fallback_hits > 0)
            c_atoms = custom_fixtures[rid]
            res_c, app_c, rep_c = self.resolver.resolve_atoms_with_full_report(c_atoms, rng)
            self.assertIn(rid, app_c, f"[{rid}] D2 failed: custom text not triggered (applied: {app_c})")
            self.assertGreater(self.resolver.text_fallback_hits, 0, f"[{rid}] D2 failed: text_fallback_hits was 0")
            executed_keys.add(f"{rid}::D2")

            # 3. 规则专属负例 (Distinct rule-specific negative cases, 0 decisions)
            neg_atoms = negative_fixtures[rid]
            res_b, app_b, rep_b = self.resolver.resolve_atoms_with_full_report(neg_atoms, rng)
            self.assertNotIn(rid, app_b, f"[{rid}] D3 failed: rule triggered on negative fixture (applied: {app_b})")
            self.assertEqual(len(rep_b.decisions), 0, f"[{rid}] D3 failed: negative produced decisions: {rep_b.decisions}")
            executed_keys.add(f"{rid}::D3")

            # 4. 显式输入冲突 (mode="explicit")
            e_atoms = [
                replace(a, atom_id=f"exp_{a.atom_id}", origin=replace(a.origin, mode="explicit"))
                for a in s_atoms
            ]
            res_e, app_e, rep_e = self.resolver.resolve_atoms_with_full_report(e_atoms, rng)
            self.assertIn(rid, app_e, f"[{rid}] D4 failed: explicit mode not resolved")
            executed_keys.add(f"{rid}::D4")

            # 5. 输入排列正反两次运行与显式 D5 Oracle 逐规则强断言 (R2R3-P1-002)
            oracle_d5 = D5_ORACLES[rid]
            if len(s_atoms) >= 2:
                fwd_atoms = list(s_atoms)
                rev_atoms = [
                    replace(s_atoms[1], tag_order=0, span_order=0),
                    replace(s_atoms[0], tag_order=1, span_order=1),
                ]
                res_fwd, app_fwd, rep_fwd = self.resolver.resolve_atoms_with_full_report(fwd_atoms, rng)
                res_rev, app_rev, rep_rev = self.resolver.resolve_atoms_with_full_report(rev_atoms, rng)
                self.assertGreater(len(rep_fwd.decisions), 0, f"[{rid}] D5 failed: fwd produced 0 decisions")
                self.assertGreater(len(rep_rev.decisions), 0, f"[{rid}] D5 failed: rev produced 0 decisions")
                d_fwd = rep_fwd.decisions[0]
                d_rev = rep_rev.decisions[0]
                # 逐条断言正序 action, winner, target, produced, final, sequence
                exp_f = oracle_d5["fwd"]
                self.assertEqual(d_fwd.action, exp_f["action"], f"[{rid}] D5 fwd action mismatch")
                self.assertEqual(d_fwd.winner_atom_ids, exp_f["winner"], f"[{rid}] D5 fwd winner mismatch")
                self.assertEqual(d_fwd.target_atom_id, exp_f["target"], f"[{rid}] D5 fwd target mismatch")
                self.assertEqual(d_fwd.produced_atom_ids, exp_f["produced"], f"[{rid}] D5 fwd produced mismatch")
                self.assertEqual([a.atom_id for a in res_fwd], exp_f["final"], f"[{rid}] D5 fwd final atoms mismatch")
                self.assertEqual(d_fwd.sequence, exp_f["sequence"], f"[{rid}] D5 fwd sequence mismatch")
                # 逐条断言逆序 action, winner, target, produced, final, sequence
                exp_r = oracle_d5["rev"]
                self.assertEqual(d_rev.action, exp_r["action"], f"[{rid}] D5 rev action mismatch")
                self.assertEqual(d_rev.winner_atom_ids, exp_r["winner"], f"[{rid}] D5 rev winner mismatch")
                self.assertEqual(d_rev.target_atom_id, exp_r["target"], f"[{rid}] D5 rev target mismatch")
                self.assertEqual(d_rev.produced_atom_ids, exp_r["produced"], f"[{rid}] D5 rev produced mismatch")
                self.assertEqual([a.atom_id for a in res_rev], exp_r["final"], f"[{rid}] D5 rev final atoms mismatch")
                self.assertEqual(d_rev.sequence, exp_r["sequence"], f"[{rid}] D5 rev sequence mismatch")
            else:
                adj_atom = make_atom("cozy indoor room", "scene", idx=99, origin_mode="preset", facts=SemanticFacts(space_kind="indoor", time_of_day="day"))
                order_a = [s_atoms[0], adj_atom]
                order_b = [replace(adj_atom, tag_order=0, span_order=0), replace(s_atoms[0], tag_order=1, span_order=1)]
                res_a, app_a, rep_a = self.resolver.resolve_atoms_with_full_report(order_a, rng)
                res_b, app_b, rep_b = self.resolver.resolve_atoms_with_full_report(order_b, rng)
                self.assertIn(rid, app_a, f"[{rid}] D5 single atom order_a not applied")
                self.assertIn(rid, app_b, f"[{rid}] D5 single atom order_b not applied")
                d_a = rep_a.decisions[0]
                d_b = rep_b.decisions[0]
                exp_a = oracle_d5["fwd"]
                exp_b = oracle_d5["rev"]
                self.assertEqual(d_a.action, exp_a["action"], f"[{rid}] D5 single order_a action mismatch")
                self.assertEqual(d_a.winner_atom_ids, exp_a["winner"], f"[{rid}] D5 single order_a winner mismatch")
                self.assertEqual(d_a.target_atom_id, exp_a["target"], f"[{rid}] D5 single order_a target mismatch")
                self.assertEqual(d_a.produced_atom_ids, exp_a["produced"], f"[{rid}] D5 single order_a produced mismatch")
                self.assertEqual([a.atom_id for a in res_a], exp_a["final"], f"[{rid}] D5 single order_a final atoms mismatch")
                self.assertEqual(d_a.sequence, exp_a["sequence"], f"[{rid}] D5 single order_a sequence mismatch")
                self.assertEqual(d_b.action, exp_b["action"], f"[{rid}] D5 single order_b action mismatch")
                self.assertEqual(d_b.winner_atom_ids, exp_b["winner"], f"[{rid}] D5 single order_b winner mismatch")
                self.assertEqual(d_b.target_atom_id, exp_b["target"], f"[{rid}] D5 single order_b target mismatch")
                self.assertEqual(d_b.produced_atom_ids, exp_b["produced"], f"[{rid}] D5 single order_b produced mismatch")
                self.assertEqual([a.atom_id for a in res_b], exp_b["final"], f"[{rid}] D5 single order_b final atoms mismatch")
                self.assertEqual(d_b.sequence, exp_b["sequence"], f"[{rid}] D5 single order_b sequence mismatch")
                self.assertTrue(any(a.text == adj_atom.text and a.source_slot == "scene" for a in res_a), f"[{rid}] D5 adjacent perturbed in order_a")
                self.assertTrue(any(a.text == adj_atom.text and a.source_slot == "scene" for a in res_b), f"[{rid}] D5 adjacent perturbed in order_b")
            executed_keys.add(f"{rid}::D5")

            # 6. 真实两规则级联与 DAG 顺序及纯状态/墓碑消费强断言 (R2R5-P1-003)
            cdef = D6_CASCADE_DEFS[rid]
            self._validate_d6_cascade_case(cdef, rng)
            executed_keys.add(f"{rid}::D6")

            # 7. 第二次消解零 decision 幂等
            res_idemp, app_idemp, rep_idemp = self.resolver.resolve_atoms_with_full_report(res_s, rng)
            self.assertEqual(len(rep_idemp.decisions), 0, f"[{rid}] D7 failed: second pass produced decisions")
            self.assertEqual(rep_idemp.input_atom_hash, rep_idemp.output_atom_hash, f"[{rid}] D7 failed: hash changed")
            executed_keys.add(f"{rid}::D7")

            # 8. 冲突败者承载 5 类保护语法 / inject-only 保护不变量
            if rid != "tattoo_dermal_fusion":
                target_id = rep_s.decisions[0].target_atom_id
                for fname, span_type, template in FIVE_SYNTAX_FORMS:
                    prot_atoms = []
                    for a in s_atoms:
                        if a.atom_id == target_id:
                            prot_atoms.append(replace(
                                a,
                                text=template.format(a.text),
                                span_type=span_type,
                                contains_blackbox=True,
                            ))
                        else:
                            prot_atoms.append(a)
                    with self.assertRaises(UnresolvedConflictError) as cm:
                        self.resolver.resolve_atoms_with_full_report(prot_atoms, rng)
                    self.assertEqual(cm.exception.reason, "protected_syntax_conflict", f"[{rid}::D8::{fname}] reason mismatch: {cm.exception.reason}")
                    self.assertIsNotNone(cm.exception.report, f"[{rid}::D8::{fname}] report is None")
                    self.assertGreater(len(cm.exception.report.unresolved_conflicts), 0, f"[{rid}::D8::{fname}] unresolved_conflicts empty")
                    executed_keys.add(f"{rid}::D8::{fname}")
            else:
                for fname, span_type, template in FIVE_SYNTAX_FORMS:
                    prot_atoms = [
                        replace(
                            s_atoms[0],
                            text=template.format(s_atoms[0].text),
                            span_type=span_type,
                            contains_blackbox=True,
                        )
                    ]
                    res_prot, app_prot, rep_prot = self.resolver.resolve_atoms_with_full_report(prot_atoms, rng)
                    self.assertIn(rid, app_prot, f"[tattoo_dermal_fusion::D8_inject_only::{fname}] Rule not applied")
                    self.assertTrue(any(a.span_type == span_type and a.contains_blackbox for a in res_prot), f"Base protected syntax damaged: {fname}")
                    self.assertTrue(any("ink embedded in dermis" in a.text for a in res_prot), f"Injected atom missing: {fname}")
                    executed_keys.add(f"tattoo_dermal_fusion::D8_inject_only::{fname}")

        self.d6_cascade_defs = D6_CASCADE_DEFS
        self.matrix_executed_keys = set(executed_keys)
        validate_204_matrix_coverage(self.matrix_executed_keys)

    def _validate_d6_cascade_case(
        self,
        cdef,
        rng=None,
        mutate_earlier_noop=False,
        mutate_restore_tombstone=False,
        mutate_produced_edge=False,
        mutate_decision_trace=False,
    ):
        atoms = cdef["atoms"]
        earlier_rid = cdef["earlier_rid"]
        later_rid = cdef["later_rid"]
        effective_seed = 42

        # 1. 真实 Cascade 执行 (同一输入建立 fresh OneTimeIndex，测试 harness 手动顺序执行 earlier / later handler)
        idx_cas = OneTimeIndex(atoms)
        ledger_cas = DecisionLedger(rule_registry=self.resolver.registry)
        cur_atoms_cas = list(atoms)

        if not mutate_earlier_noop:
            rule_item_earlier = self.resolver.registry.get_rule_item(earlier_rid)
            fn_earlier = getattr(self.resolver, f"_resolve_{earlier_rid}_atoms")
            rng_earlier = derive_substream_rng(effective_seed, f"rule:{earlier_rid}")
            cur_atoms_cas = fn_earlier(cur_atoms_cas, rule_item_earlier, ledger_cas, rng_earlier, idx_cas, None)

        # 记录前序规则执行后的中间活跃原子
        inter_active = [a.atom_id for a in idx_cas.get_all_active_ordered()]

        # 变异 1: 真实还原墓碑 (在 fresh OneTimeIndex 中将被删 atom ID 从 _tombstones 移除，复活墓碑)
        if mutate_restore_tombstone and cdef["dec_0"]["target"]:
            t_id = cdef["dec_0"]["target"]
            if t_id in idx_cas._tombstones:
                idx_cas._tombstones.remove(t_id)
            inter_active = [a.atom_id for a in idx_cas.get_all_active_ordered()]

        # 变异 2: 真实破坏 produced 边 (在 fresh OneTimeIndex 中剔除 produced atom)
        if mutate_produced_edge:
            if cdef["dec_0"]["produced"]:
                prod_id = cdef["dec_0"]["produced"][0]
                idx_cas.drop(prod_id)
            else:
                if inter_active:
                    idx_cas.drop(inter_active[0])
            inter_active = [a.atom_id for a in idx_cas.get_all_active_ordered()]

        # 断言中间状态与前序决策
        self.assertEqual(inter_active, cdef["intermediate_ids"], f"[{earlier_rid}] D6 intermediate active IDs mismatch")

        self.assertGreaterEqual(len(ledger_cas.decisions), 1, f"[{earlier_rid}] D6 failed: earlier rule {earlier_rid} not applied")
        if mutate_decision_trace:
            ledger_cas.decisions[0] = replace(ledger_cas.decisions[0], target_atom_id="tampered_target_atom_id")
        d0 = ledger_cas.decisions[0]
        exp0 = cdef["dec_0"]
        self.assertEqual(d0.rule_id, exp0["rule_id"], f"[{earlier_rid}] D6 decision 0 rule_id mismatch")
        self.assertEqual(d0.action, exp0["action"], f"[{earlier_rid}] D6 decision 0 action mismatch")
        self.assertEqual(d0.winner_atom_ids, exp0["winner"], f"[{earlier_rid}] D6 decision 0 winner mismatch")
        self.assertEqual(d0.target_atom_id, exp0["target"], f"[{earlier_rid}] D6 decision 0 target mismatch")
        self.assertEqual(d0.produced_atom_ids, exp0["produced"], f"[{earlier_rid}] D6 decision 0 produced mismatch")
        self.assertEqual(d0.sequence, exp0["sequence"], f"[{earlier_rid}] D6 decision 0 sequence mismatch")



        # 执行后序规则
        rule_item_later = self.resolver.registry.get_rule_item(later_rid)
        fn_later = getattr(self.resolver, f"_resolve_{later_rid}_atoms")
        rng_later = derive_substream_rng(effective_seed, f"rule:{later_rid}")
        cur_atoms_cas = fn_later(cur_atoms_cas, rule_item_later, ledger_cas, rng_later, idx_cas, None)
        final_active_cas = [a.atom_id for a in idx_cas.get_all_active_ordered()]

        if cdef["dec_1"] is not None:
            self.assertEqual(len(ledger_cas.decisions), 2, f"[{earlier_rid}] D6 failed: later rule {later_rid} not applied in cascade")
            d1 = ledger_cas.decisions[1]
            exp1 = cdef["dec_1"]
            self.assertEqual(d1.rule_id, exp1["rule_id"], f"[{earlier_rid}] D6 decision 1 rule_id mismatch")
            self.assertEqual(d1.action, exp1["action"], f"[{earlier_rid}] D6 decision 1 action mismatch")
            self.assertEqual(d1.winner_atom_ids, exp1["winner"], f"[{earlier_rid}] D6 decision 1 winner mismatch")
            self.assertEqual(d1.target_atom_id, exp1["target"], f"[{earlier_rid}] D6 decision 1 target mismatch")
            self.assertEqual(d1.produced_atom_ids, exp1["produced"], f"[{earlier_rid}] D6 decision 1 produced mismatch")
            self.assertEqual(d1.sequence, exp1["sequence"], f"[{earlier_rid}] D6 decision 1 sequence mismatch")
            self.assertLess(d0.sequence, d1.sequence, f"[{earlier_rid}] D6 decision sequence not strictly monotonically increasing")
        else:
            later_cas_decs = [d for d in ledger_cas.decisions if d.rule_id == later_rid]
            self.assertEqual(len(later_cas_decs), 0, f"[{earlier_rid}] D6 failed: later rule {later_rid} should NOT be applied in post-state")

        self.assertEqual(final_active_cas, cdef["final_ids"], f"[{earlier_rid}] D6 final atom IDs mismatch")
        if d0.action == "drop":
            self.assertNotIn(d0.target_atom_id, final_active_cas, f"[{earlier_rid}] D6 dropped target revived in final output")

        # 2. 反事实对比执行 (同一输入建立 fresh OneTimeIndex，跳过 earlier handler，只执行 later handler)
        idx_pre = OneTimeIndex(atoms)
        ledger_pre = DecisionLedger(rule_registry=self.resolver.registry)
        cur_atoms_pre = list(atoms)
        cur_atoms_pre = fn_later(cur_atoms_pre, rule_item_later, ledger_pre, rng_later, idx_pre, None)

        later_pre_decs = [d for d in ledger_pre.decisions if d.rule_id == later_rid]
        self.assertGreaterEqual(len(later_pre_decs), 1, f"[{earlier_rid}] D6 pre-state later rule {later_rid} must trigger when earlier is skipped")
        later_pre_dec = later_pre_decs[0]
        exp_pre = cdef["pre_dec_later"]
        self.assertEqual(later_pre_dec.rule_id, exp_pre["rule_id"], f"[{earlier_rid}] D6 pre-state rule_id mismatch")
        self.assertEqual(later_pre_dec.action, exp_pre["action"], f"[{earlier_rid}] D6 pre-state action mismatch")
        self.assertEqual(later_pre_dec.winner_atom_ids, exp_pre["winner"], f"[{earlier_rid}] D6 pre-state winner mismatch")
        self.assertEqual(later_pre_dec.target_atom_id, exp_pre["target"], f"[{earlier_rid}] D6 pre-state target mismatch")

        # 3. 强行为差分断言 (Post-state vs Pre-state)
        if cdef["dec_1"] is None:
            later_cas_decs = [d for d in ledger_cas.decisions if d.rule_id == later_rid]
            self.assertEqual(len(later_cas_decs), 0, f"[{earlier_rid}] D6 expected 0 decisions in post-state")
        else:
            post_dec = ledger_cas.decisions[1]
            diff_found = (
                post_dec.winner_atom_ids != later_pre_dec.winner_atom_ids or
                post_dec.target_atom_id != later_pre_dec.target_atom_id or
                post_dec.produced_atom_ids != later_pre_dec.produced_atom_ids
            )
            self.assertTrue(diff_found, f"[{earlier_rid}] D6 failed: no behavioral differential between post-state and pre-state!")
        return True

    def test_24b_matrix_self_mutation_detects_omission(self):
        """矩阵门禁自变异测试：连接真实执行结果，模拟丢失任意一格时由同一校验器断言失败 (R2R3-P1-002)。"""
        if not hasattr(self, "matrix_executed_keys"):
            self.test_24_all_17_rules_8_dimensional_matrix()

        # 1. 真实执行集合必须通过统一校验器
        validate_204_matrix_coverage(self.matrix_executed_keys)

        # 2. 变异 1: 真实执行集中剔除任意 D5 排列维度 -> 必须被校验器拒绝
        mutated_1 = set(self.matrix_executed_keys)
        mutated_1.remove("spatial_environmental_mutual_exclusion::D5")
        with self.assertRaises(AssertionError):
            validate_204_matrix_coverage(mutated_1)

        # 3. 变异 2: 真实执行集中剔除某一语法的 D8 保护 -> 必须被校验器拒绝
        mutated_2 = set(self.matrix_executed_keys)
        mutated_2.remove("nudity_clothing_conflicts::D8::ANGLE")
        with self.assertRaises(AssertionError):
            validate_204_matrix_coverage(mutated_2)

        # 4. 变异 3: 真实执行集中剔除 inject-only D8 保护 -> 必须被校验器拒绝
        mutated_3 = set(self.matrix_executed_keys)
        mutated_3.remove("tattoo_dermal_fusion::D8_inject_only::QUOTED")
        with self.assertRaises(AssertionError):
            validate_204_matrix_coverage(mutated_3)

        # 5. 变异 4: 真实修改 active IDs: 模拟前序规则为 no-op (跳过前序规则执行) -> D6 校验器必须报警并判负
        with self.assertRaises(AssertionError):
            self._validate_d6_cascade_case(self.d6_cascade_defs["spatial_environmental_mutual_exclusion"], mutate_earlier_noop=True)

        # 6. 变异 5: 真实修改 tombstone 状态: 还原前一规则被删词 (还原墓碑加回 active) -> D6 校验器必须报警并判负
        with self.assertRaises(AssertionError):
            self._validate_d6_cascade_case(self.d6_cascade_defs["nudity_clothing_conflicts"], mutate_restore_tombstone=True)

        # 7. 变异 6: 真实破坏 produced 边: 剔除产出 atom -> D6 校验器必须报警并判负
        with self.assertRaises(AssertionError):
            self._validate_d6_cascade_case(self.d6_cascade_defs["framing_lower_body_coherence"], mutate_produced_edge=True)

        # 8. 变异 7: 真实篡改 decision trace: 篡改决策目标 ID -> D6 校验器必须报警并判负
        with self.assertRaises(AssertionError):
            self._validate_d6_cascade_case(self.d6_cascade_defs["framing_lower_body_coherence"], mutate_decision_trace=True)

    def test_24c_production_resolver_detector_no_skip_rules_bypass(self):
        """负向验证：生产 resolver/detector 禁止通过签名传参或内部状态绕过任何规则。"""
        import inspect

        # 1. 签名检查：resolve_atoms_with_full_report 与 detect_hard_conflicts 绝无 skip_rules 参数
        resolver_sig = inspect.signature(self.resolver.resolve_atoms_with_full_report)
        self.assertNotIn("skip_rules", resolver_sig.parameters, "Production resolve_atoms_with_full_report must not accept skip_rules")
        detect_sig = inspect.signature(self.resolver.detect_hard_conflicts)
        self.assertNotIn("skip_rules", detect_sig.parameters, "Production detect_hard_conflicts must not accept skip_rules")

        # 2. 对象状态检查：ConflictResolver 实例禁止携带 last_intermediate_states 测试状态
        self.assertFalse(hasattr(self.resolver, "last_intermediate_states"), "Production ConflictResolver must not carry last_intermediate_states")

        # 3. 调用行为检查：传参尝试绕过必定引发 TypeError
        atoms = [make_atom("indoor", "scene", idx=0, origin_mode="custom")]
        with self.assertRaises(TypeError):
            self.resolver.resolve_atoms_with_full_report(atoms, Random(42), skip_rules={"spatial_environmental_mutual_exclusion"})  # type: ignore
        with self.assertRaises(TypeError):
            self.resolver.detect_hard_conflicts(atoms, skip_rules={"spatial_environmental_mutual_exclusion"})  # type: ignore

    def test_25_r2_p1_001_formal_semantics_vs_custom_fallback_counterexample(self):
        """反例验证 R2-P1-001: 正式目录原子禁止通过英文文本被模式消解，custom 原子正确走 fallback 并计数。"""
        # 反例 1: 两个 formal 原子，facts 均明确为 space_kind="indoor"，即便包含文本 "rotenburo"，也不被空间规则删除
        formal_atoms = [
            make_atom("indoor onsen", "scene", idx=0, origin_mode="preset", facts=SemanticFacts(space_kind="indoor", time_of_day="day")),
            make_atom("rotenburo themed bath", "scene", idx=1, origin_mode="preset", facts=SemanticFacts(space_kind="indoor", time_of_day="day")),
        ]
        res, app, rep = self.resolver.resolve_atoms_with_full_report(formal_atoms, Random(42))
        self.assertEqual(len(rep.decisions), 0, "Formal atoms with consistent space_kind='indoor' must not be dropped by text 'rotenburo'!")
        self.assertEqual(self.resolver.text_fallback_hits, 0, "Formal path must have 0 text fallback hits")

        # 反例 2: custom 输入没有 facts，触发 text fallback，产生 1 个 drop decision 并记录 fallback hit
        custom_atoms = [
            make_atom("indoor onsen", "scene", idx=0, origin_mode="custom"),
            make_atom("rotenburo", "scene", idx=1, origin_mode="custom"),
        ]
        res_c, app_c, rep_c = self.resolver.resolve_atoms_with_full_report(custom_atoms, Random(42))
        self.assertEqual(len(rep_c.decisions), 1, "Custom atoms without facts must trigger text fallback")
        self.assertEqual(self.resolver.text_fallback_hits, 1, "Custom fallback must record exactly 1 hit")

    def test_26_r2_p1_002_independent_rule_rng_substreams(self):
        """反例验证 R2-P1-002: 规则独立 RNG 子流，前序规则命中与否绝不扰动后序规则的随机抽取结果。"""
        tattoo_atom = make_atom("dragon tattoo on back", "tattoo", idx=1, origin_mode="preset")
        sheer_atom = make_atom("sheer silk fabric", "clothing", idx=0, origin_mode="preset", facts=SemanticFacts(garment_states=("wet_clinging",)))

        # 运行 1: 仅 tattoo，无前序 material_penetration 命中
        res_solo, app_solo, rep_solo = self.resolver.resolve_atoms_with_full_report([tattoo_atom], Random(1))
        injected_solo = [a.text for a in res_solo if a.source_slot == "tattoo" and a.text != tattoo_atom.text]
        self.assertEqual(len(injected_solo), 1)

        # 运行 2: 前序 sheer 命中并替换/删除，后序 tattoo 独立子流应保持完全相同的注入词
        res_pair, app_pair, rep_pair = self.resolver.resolve_atoms_with_full_report([sheer_atom, tattoo_atom], Random(1))
        injected_pair = [a.text for a in res_pair if a.source_slot == "tattoo" and a.text != tattoo_atom.text]
        self.assertEqual(len(injected_pair), 1)

        # 核心断言：前序规则的发生绝不能改变后序规则派生流的随机结果
        self.assertEqual(injected_solo[0], injected_pair[0], f"Rule RNG streams not independent! Solo got {injected_solo[0]}, pair got {injected_pair[0]}")

    def test_27_r2_p1_003_replace_generates_new_deterministic_atom_id(self):
        """反例验证 R2-P1-003: 替换操作生成全新且确定性的 Atom ID，并保持父来源闭环。"""
        atom = make_atom("cum on closed eyes", "liquids", idx=0, origin_mode="custom")
        res, app, rep = self.resolver.resolve_atoms_with_full_report([atom], Random(42))
        self.assertGreaterEqual(len(rep.decisions), 1)
        dec = rep.decisions[0]
        self.assertEqual(dec.action, "replace")
        self.assertEqual(dec.target_atom_id, atom.atom_id)
        self.assertEqual(len(dec.produced_atom_ids), 1)

        # 核心断言：produced atom ID 绝不能等于 target atom ID
        produced_id = dec.produced_atom_ids[0]
        self.assertNotEqual(produced_id, atom.atom_id, "Replaced atom must receive a new distinct Atom ID!")
        self.assertIn(atom.atom_id, res[0].provenance.parent_ids)

        # 二次消解必须继续为零 decision
        res2, app2, rep2 = self.resolver.resolve_atoms_with_full_report(res, Random(42))
        self.assertEqual(len(rep2.decisions), 0, "Second resolve of replaced atoms must produce 0 decisions")

    def test_28_r2_p1_004_detect_hard_conflicts_and_protected_attribution(self):
        """反例验证 R2-P1-004: detect_hard_conflicts 覆盖所有 17 规则硬不变量，且 has_protected 仅归因于实际 loser。"""
        # 1. cctv + masterpiece 必须被检测为硬冲突
        cctv = make_atom("cctv", "shot_type", idx=0, origin_mode="custom")
        mp = make_atom("masterpiece", "quality", idx=1, origin_mode="custom")
        conflicts, has_protected = self.resolver.detect_hard_conflicts([cctv, mp])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][1], "device_quality_compatibility")
        self.assertFalse(has_protected, "Normal unprotected atoms must have has_protected=False")

        # 2. 混入无关黑盒 ANGLE 原子，不得使 has_protected 误报为 True
        lora = make_atom("<lora:detail:0.8>", "shot_type", span_type=SpanType.ANGLE, idx=2)
        conflicts_bb, has_protected_bb = self.resolver.detect_hard_conflicts([cctv, mp, lora])
        self.assertEqual(len(conflicts_bb), 1)
        self.assertFalse(has_protected_bb, "Unrelated blackbox atom must not cause has_protected=True")

        # 3. 实际受保护 loser: 当 loser 不可删除时，has_protected 为 True
        panties = PromptAtom(
            atom_id="panties_prot",
            text="wearing panties",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            tag_order=1,
            span_order=1,
            provenance=TagProvenance(item_id="p1", parent_ids=("src1",)),
            origin=SelectionOrigin(mode="custom", selector="clothing"),
        )
        # 用不可删除模拟
        from unittest.mock import PropertyMock
        with patch.object(PromptAtom, "can_delete_atom", new_callable=PropertyMock) as mock_can_del:
            mock_can_del.return_value = False
            conflicts_prot, has_prot = self.resolver.detect_hard_conflicts([cctv, mp])
            self.assertTrue(has_prot, "When loser cannot be deleted, has_protected must be True")

    def test_29_r2r_p1_001_contract_negative_mutation_tests(self):
        """反例验证 R2R-P1-001: 变异 reason_codes、winner、target_slots、strategy 均被拒绝并 Fail-Closed。"""
        from lib.rule_contract import (
            ReasonCodesField,
            SemanticConstraintsField,
            TextFallbackField,
        )

        # 1. 变异 reason_codes 注入非法 reason code -> 拒绝并报错
        rc_field = ReasonCodesField("spatial_environmental_mutual_exclusion")
        with self.assertRaises(RuleConfigurationError):
            rc_field.parse(["bogus_nonexistent_reason_code"], "test_context")

        # 2. 变异 winner -> 拒绝并报错
        sc_field = SemanticConstraintsField("spatial_environmental_mutual_exclusion")
        with self.assertRaises(RuleConfigurationError):
            sc_field.parse({"domain": "spatial", "winner": "bogus_winner"}, "test_context")

        # 3. 变异 target_slots -> 拒绝并报错
        with self.assertRaises(RuleConfigurationError):
            sc_field.parse({"domain": "spatial", "winner": "scene_anchor", "target_slots": ["camera"]}, "test_context")

        # 4. 变异 text_fallback.strategy -> 拒绝并报错
        tf_field = TextFallbackField("spatial_environmental_mutual_exclusion")
        with self.assertRaises(RuleConfigurationError):
            tf_field.parse({"strategy": "bogus_strategy", "target_slots": ["scene"]}, "test_context")

        # 5. 变异 text_fallback.target_slots -> 拒绝并报错
        with self.assertRaises(RuleConfigurationError):
            tf_field.parse({"strategy": "pattern_match", "target_slots": ["camera"]}, "test_context")

        # 6. text_fallback.enabled=False: 自定义文本冲突在 fallback 关闭时必须拒绝应用规则并作为残余冲突抛错
        rng = Random(42)
        custom_atoms = [
            make_atom("indoor onsen", "scene", idx=0, origin_mode="custom"),
            make_atom("rotenburo", "scene", idx=1, origin_mode="custom"),
        ]
        rule_item = self.resolver.registry.get_rule("spatial_environmental_mutual_exclusion")
        orig_enabled = rule_item.text_fallback.enabled
        try:
            object.__setattr__(rule_item.text_fallback, "enabled", False)
            with self.assertRaises(UnresolvedConflictError) as ctx:
                self.resolver.resolve_atoms_with_full_report(custom_atoms, rng)
            self.assertEqual(ctx.exception.reason, "unresolved_hard_conflict")
        finally:
            object.__setattr__(rule_item.text_fallback, "enabled", orig_enabled)


if __name__ == "__main__":
    unittest.main()
