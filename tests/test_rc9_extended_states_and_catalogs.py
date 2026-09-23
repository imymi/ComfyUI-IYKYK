"""
tests/test_rc9_extended_states_and_catalogs.py — rc9 服装状态扩充 (11 条新增状态) 质量门禁与消解契约专项测试

覆盖范围：
1. 状态目录规模与契约：
   - 验证 data/clothing.json 中 clothing_states 精确达到 23 条；
   - 验证存量 12 条与新增 11 条 ID、中文名及 Facts 契约合规性；
   - 验证所有新增状态 tag facts 的 semantic_role 严格为 "selector"，枚举合法。
2. 语义保守性与零额外推导消解行为断言：
   - wet_pure 标为 worn，消解时绝不触发材质穿透 (material_penetration) 或半透/紧贴推导，不误删内衣；
   - off_shoulder_cut 与 bare_shoulders 标为 worn，消解时不触发 loosened/disheveled 冲突或连体衣互斥。
3. 修饰组 (7 条) 承载正反例、目标绑定不改绑与防自我承载：
   - 正例：外衣/上装在穿时，切口与质感修饰成功绑定 BOUND；
   - 反例：无匹配承载物或仅穿下装裤子时，上装切口修饰被判定为 UNBOUND_NO_CANDIDATE / state_lacks_carrier；
   - 目标绑定断言：显式指定错误 target_id 时必须返回 UNBOUND_TARGET_NOT_FOUND，严禁静默回退改绑其他实体；
   - 绝对防自我承载：状态自身原子绝不能作为载体实体 (build_garment_entity_key 返回 None)。
4. 缺席组 (2 条) 防误删与真实内衣互斥行为断言：
   - 防误删：在穿外衣/裙装且无内衣时，braless / underwearless 100% 保留，不报缺少载体；
   - 互斥：存在真实文胸时 braless 报 absence_state_conflict；存在真实内裤时 underwearless 报 absence_state_conflict。
5. 叠穿组 (2 条) 实体独立性与同一实体多原子拒绝：
   - 正例：和服实体 (entity_A) + 半身裙实体 (entity_B) 满足两个不同 entity_id，skirt_under_kimono 保留；
   - 反例：单件和服 (同一 entity_id) 即使包含多个不同 atom_id，依然被严格判定为 layering_mismatch 剔除；
   - 紧身衣叠穿：紧身衣实体 + 外套实体满足双独立 entity_id 时保留，仅单件紧身衣或无外衣时报 layering_mismatch 剔除。
6. 零警告门禁 (--zero-warnings) 生产入口与负向测试：
   - 模拟 validate_all 返回“0错误、有警告”，实际调用 main() 断言开启选项返回 1，未开启返回 0；
   - 生产真实全量数据执行断言退出码 0。
7. 端到端节点验证：
   - 11 条新增状态通过 IYKYKPromptGenerator 节点显式生成；
   - 9 条修饰与缺席状态验证正向提示词中标签实际产出与诊断状态保留；
   - 2 条叠穿状态在单件服装下验证 DAG 报告中依法记录 layering_mismatch 剔除，在多层服装下验证保留。
"""
from __future__ import annotations

import json
from pathlib import Path
from random import Random
import subprocess
import sys
import unittest
from unittest import mock

from lib.conflict_resolver import (
    BindingStatus,
    ConflictResolver,
    GarmentCarrierEntity,
    build_garment_entity_key,
    find_bound_carrier,
    is_garment_modifier_atom,
)
from lib.models import (
    PromptAtom,
    SelectionOrigin,
    SemanticFacts,
    SpanType,
    VALID_GARMENT_STATES,
    VALID_GARMENT_TOPOLOGIES,
    VALID_SEMANTIC_ROLES,
    VALID_VISIBLE_REGIONS,
)
from lib.sampler import DataSampler
import nodes

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / "data"

LEGACY_STATE_IDS = (
    "auto_link",
    "normal",
    "unbuttoned",
    "slipping_off",
    "lifted_up",
    "pulled_down",
    "wet_clinging",
    "sweat_soaked",
    "torn_shredded",
    "disheveled",
    "only_lingerie",
    "discarded",
)

NEW_STATE_IDS = (
    "taut_tight",
    "wet_pure",
    "heart_cutout",
    "back_cutout",
    "underboob_cutout",
    "off_shoulder_cut",
    "bare_shoulders",
    "braless",
    "underwearless",
    "undergarment_leotard",
    "skirt_under_kimono",
)


class TestRc9ExtendedClothingStates(unittest.TestCase):
    """rc9 11 条服装状态落地与消解铁律深度验证。"""

    def setUp(self) -> None:
        self.clothing_data = json.loads((DATA_DIR / "clothing.json").read_text(encoding="utf-8"))
        self.states = self.clothing_data.get("clothing_states", [])
        self.state_map = {s["id"]: s for s in self.states}
        self.sampler = DataSampler(DATA_DIR)
        self.resolver = ConflictResolver(data_dir=DATA_DIR)

    def test_01_catalog_exact_23_states_and_legal_facts(self) -> None:
        """验证状态目录规模达到精确 23 条，且所有 Facts 符合现有合法契约。"""
        self.assertEqual(len(self.states), 23, f"Expected exactly 23 states, got {len(self.states)}")

        # 12 存量 ID 严格保真
        for legacy_id in LEGACY_STATE_IDS:
            self.assertIn(legacy_id, self.state_map, f"Legacy state {legacy_id} missing!")

        # 11 新增 ID 完整存在
        for new_id in NEW_STATE_IDS:
            self.assertIn(new_id, self.state_map, f"New state {new_id} missing!")

        # 逐条核验 tag facts 枚举合法性
        for s in self.states:
            for t in s.get("tags", []):
                facts = t.get("facts", {})
                self.assertIsInstance(facts, dict)
                role = facts.get("semantic_role")
                if role is not None:
                    self.assertIn(role, VALID_SEMANTIC_ROLES)
                    self.assertEqual(role, "selector", f"Tag {t['id']} must use selector role")
                for topo in facts.get("garment_topologies", []):
                    self.assertIn(topo, VALID_GARMENT_TOPOLOGIES)
                for reg in facts.get("visible_regions", []):
                    self.assertIn(reg, VALID_VISIBLE_REGIONS)
                for gs in facts.get("garment_states", []):
                    self.assertIn(gs, VALID_GARMENT_STATES)

    def test_02_conservative_worn_facts_and_zero_extra_deductions(self) -> None:
        """验证 wet_pure 与剪裁修饰保持保守 worn 标记，并断言消解行为绝不触发额外推导。"""
        # 1. 静态 Facts 契约校验
        wet_pure = self.state_map["wet_pure"]
        for t in wet_pure["tags"]:
            facts = t["facts"]
            self.assertEqual(facts.get("garment_states"), ["worn"])
            self.assertNotIn("wet_clinging", facts.get("garment_states", []))
            self.assertNotIn("sheer", facts.get("garment_states", []))

        off_shoulder = self.state_map["off_shoulder_cut"]
        for t in off_shoulder["tags"]:
            facts = t["facts"]
            self.assertEqual(facts.get("garment_states"), ["worn"])
            self.assertNotIn("loosened", facts.get("garment_states", []))

        bare_shoulders = self.state_map["bare_shoulders"]
        for t in bare_shoulders["tags"]:
            facts = t["facts"]
            self.assertEqual(facts.get("garment_states"), ["worn"])
            self.assertNotIn("loosened", facts.get("garment_states", []))

        # 2. 消解行为断言 A：wet_pure 纯湿身质感绝不触发材质穿透 (material_penetration)
        dress_atom = PromptAtom(
            text="casual dress",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="dress_casual",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="dress_casual"),
            facts=SemanticFacts(garment_topologies=("one_piece",), visible_regions=("upper_body", "lower_body"), garment_states=("worn",)),
            atom_id="dress__tag_000",
            id="dress__tag_000",
        )
        wet_atom = PromptAtom(
            text="wet clothes",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="wet_pure",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="wet_pure"),
            facts=SemanticFacts(semantic_role="selector", visible_regions=("upper_body", "lower_body"), garment_states=("worn",)),
            atom_id="wet_pure__tag_000",
            id="wet_pure__tag_000",
        )
        resolved_atoms, rules_applied, report = self.resolver.resolve_atoms_with_full_report([dress_atom, wet_atom])
        active_texts = [a.text for a in resolved_atoms]
        self.assertIn("wet clothes", active_texts, "wet_pure should be active with dress")
        self.assertNotIn(
            "material_penetration",
            rules_applied,
            "wet_pure MUST NOT trigger material_penetration rule!",
        )
        pen_drops = [d for d in report.decisions if d.rule_id == "material_penetration"]
        self.assertEqual(len(pen_drops), 0, "wet_pure must not cause any material_penetration drops")

        # 3. 消解行为断言 B：off_shoulder_cut 与 bare_shoulders 不触发 loosened/连体衣互斥
        off_atom = PromptAtom(
            text="off-shoulder cut",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="off_shoulder_cut",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="off_shoulder_cut"),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("top", "one_piece"), visible_regions=("upper_body",), garment_states=("worn",)),
            atom_id="off_shoulder__tag_000",
            id="off_shoulder__tag_000",
        )
        resolved_off, _, report_off = self.resolver.resolve_atoms_with_full_report([dress_atom, off_atom])
        active_off_texts = [a.text for a in resolved_off]
        self.assertIn("off-shoulder cut", active_off_texts, "off_shoulder_cut should be active with dress")
        off_conflict_drops = [d for d in report_off.decisions if d.action == "drop"]
        self.assertEqual(len(off_conflict_drops), 0, "off_shoulder_cut must not be dropped as conflict with dress")

    def test_03_anti_self_carrier_invariant(self) -> None:
        """铁律 1 验证：clothing_state 来源的原子绝不能自立为主体实体 (build_garment_entity_key == None)。"""
        for new_id in NEW_STATE_IDS:
            state_item = self.state_map[new_id]
            for t in state_item["tags"]:
                facts_dict = t["facts"]
                sem_facts = SemanticFacts(
                    semantic_role=facts_dict.get("semantic_role"),
                    visible_regions=tuple(facts_dict.get("visible_regions", ())),
                    garment_topologies=tuple(facts_dict.get("garment_topologies", ())),
                    garment_states=tuple(facts_dict.get("garment_states", ())),
                )
                atom = PromptAtom(
                    text=t["text"],
                    span_type=SpanType.PLAIN,
                    source_slot="clothing_state",
                    source_item_id=new_id,
                    origin=SelectionOrigin(
                        entry_point="generator",
                        mode="explicit",
                        selector="clothing_state",
                        selected_id=new_id,
                    ),
                    facts=sem_facts,
                    atom_id=t["id"],
                    id=t["id"],
                )
                ekey = build_garment_entity_key(atom)
                self.assertIsNone(
                    ekey,
                    f"State tag {t['id']} violated anti-self-carrier invariant! (Produced ekey: {ekey})",
                )

    def test_04_modifiers_carrier_positive_and_negative_and_target_binding(self) -> None:
        """铁律 2 验证：修饰组 (7 条) 承载正反例、错误显式目标不得改绑。"""
        # 正例：在穿连衣裙作为载体实体
        dress_carrier = GarmentCarrierEntity(
            entity_id="garment:clothing:dress_casual",
            selector="clothing",
            selected_id="dress_casual",
            member_atoms=[
                PromptAtom(
                    text="casual dress",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing",
                    source_item_id="dress_casual",
                    facts=SemanticFacts(garment_topologies=("one_piece",), visible_regions=("upper_body", "lower_body")),
                    atom_id="dress__tag_000",
                    id="dress__tag_000",
                )
            ],
            is_worn=True,
            is_ambient=False,
        )

        modifier_ids = [
            "taut_tight", "wet_pure", "heart_cutout", "back_cutout",
            "underboob_cutout", "off_shoulder_cut", "bare_shoulders",
        ]
        for m_id in modifier_ids:
            state_tag = self.state_map[m_id]["tags"][0]
            facts = state_tag["facts"]
            state_atom = PromptAtom(
                text=state_tag["text"],
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id=m_id,
                origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id=m_id),
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_topologies=tuple(facts.get("garment_topologies", ())),
                    visible_regions=tuple(facts.get("visible_regions", ())),
                    garment_states=tuple(facts.get("garment_states", ())),
                ),
                atom_id=state_tag["id"],
                id=state_tag["id"],
            )

            # 正例绑定：有连衣裙载体，必须成功 BOUND
            binding = find_bound_carrier(state_atom, [dress_carrier])
            self.assertEqual(
                binding.status,
                BindingStatus.BOUND,
                f"Modifier state {m_id} failed to bind to dress carrier: {binding.reason}",
            )

            # 反例绑定：无任何在穿载体，必须 UNBOUND_NO_CANDIDATE
            unbound_binding = find_bound_carrier(state_atom, [])
            self.assertEqual(
                unbound_binding.status,
                BindingStatus.UNBOUND_NO_CANDIDATE,
                f"Modifier state {m_id} without carrier should be UNBOUND_NO_CANDIDATE",
            )

        # 错误显式目标断言：显式指定不存在的目标时必须 UNBOUND_TARGET_NOT_FOUND，绝不能回退改绑到 dress_carrier
        sample_tag = self.state_map["taut_tight"]["tags"][0]
        explicit_target_atom = PromptAtom(
            text=sample_tag["text"],
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="taut_tight",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="taut_tight"),
            facts=SemanticFacts(semantic_role="selector", visible_regions=("upper_body",)),
            atom_id=sample_tag["id"],
            id=sample_tag["id"],
        )
        wrong_target_binding = find_bound_carrier(explicit_target_atom, [dress_carrier], target_id="garment:clothing:non_existent_item")
        self.assertEqual(
            wrong_target_binding.status,
            BindingStatus.UNBOUND_TARGET_NOT_FOUND,
            "Explicit invalid target must fail with UNBOUND_TARGET_NOT_FOUND",
        )
        self.assertIsNone(
            wrong_target_binding.target_entity,
            "Explicit invalid target MUST NOT re-bind to available dress_carrier!",
        )

        # 正确显式目标：精确匹配并成功 BOUND
        correct_target_binding = find_bound_carrier(explicit_target_atom, [dress_carrier], target_id="dress_casual")
        self.assertEqual(correct_target_binding.status, BindingStatus.BOUND)
        self.assertEqual(correct_target_binding.target_entity, dress_carrier)

        # 上装切口修饰的反例：仅穿裤装 (bottom_pants)，上装切口修饰应判定为不兼容
        pants_carrier = GarmentCarrierEntity(
            entity_id="garment:clothing:denim_shorts",
            selector="clothing",
            selected_id="denim_shorts",
            member_atoms=[
                PromptAtom(
                    text="denim shorts",
                    span_type=SpanType.PLAIN,
                    source_slot="clothing",
                    source_item_id="denim_shorts",
                    facts=SemanticFacts(garment_topologies=("bottom_pants",), visible_regions=("lower_body",)),
                    atom_id="shorts__tag_000",
                    id="shorts__tag_000",
                )
            ],
            is_worn=True,
            is_ambient=False,
        )
        upper_cutouts = ["heart_cutout", "back_cutout", "underboob_cutout", "off_shoulder_cut", "bare_shoulders"]
        for c_id in upper_cutouts:
            c_tag = self.state_map[c_id]["tags"][0]
            c_facts = c_tag["facts"]
            c_atom = PromptAtom(
                text=c_tag["text"],
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id=c_id,
                origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id=c_id),
                facts=SemanticFacts(
                    semantic_role="selector",
                    garment_topologies=tuple(c_facts.get("garment_topologies", ())),
                    visible_regions=tuple(c_facts.get("visible_regions", ())),
                    garment_states=tuple(c_facts.get("garment_states", ())),
                ),
                atom_id=c_tag["id"],
                id=c_tag["id"],
            )
            binding = find_bound_carrier(c_atom, [pants_carrier])
            self.assertEqual(
                binding.status,
                BindingStatus.UNBOUND_NO_CANDIDATE,
                f"Upper cutout {c_id} should NOT bind to bottom_pants carrier",
            )

    def test_05_absence_states_retention_and_lingerie_mutex(self) -> None:
        """铁律 3 验证：缺席组 (braless, underwearless) 防误删与内衣互斥行为等价完整覆盖。"""
        # 1. 防误删验证：is_garment_modifier_atom 必须返回 False，杜绝缺少载体误删
        for ab_id in ("braless", "underwearless"):
            ab_tag = self.state_map[ab_id]["tags"][0]
            ab_atom = PromptAtom(
                text=ab_tag["text"],
                span_type=SpanType.PLAIN,
                source_slot="clothing_state",
                source_item_id=ab_id,
                origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id=ab_id),
                facts=SemanticFacts(semantic_role="selector", garment_states=("removed",)),
                atom_id=ab_tag["id"],
                id=ab_tag["id"],
            )
            self.assertFalse(
                is_garment_modifier_atom(ab_atom),
                f"Absence state {ab_id} must not be classified as modifier requiring carrier!",
            )

        dress_atom = PromptAtom(
            text="sundress",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="summer_sundress",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="summer_sundress"),
            facts=SemanticFacts(garment_topologies=("one_piece",), visible_regions=("upper_body", "lower_body")),
            atom_id="sundress__tag_000",
            id="sundress__tag_000",
        )

        # 2. braless 端到端行为消解验证
        braless_atom = PromptAtom(
            text="braless",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="braless",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="braless"),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("underwear",), visible_regions=("upper_body",), garment_states=("removed",)),
            atom_id="braless__tag_000",
            id="braless__tag_000",
        )
        # 2a. 正例保留：外装 + braless -> 保留 braless
        resolved_bra, _, report_bra_ok = self.resolver.resolve_atoms_with_full_report([dress_atom, braless_atom])
        active_bra_texts = [a.text for a in resolved_bra]
        self.assertIn("braless", active_bra_texts, "braless should be preserved when wearing dress!")
        self.assertEqual(len([d for d in report_bra_ok.decisions if d.target_atom_id == "braless__tag_000"]), 0)

        # 2b. 负向互斥：真实文胸 + braless -> 剔除 braless，记录 absence_state_conflict，winner 为文胸
        bra_atom = PromptAtom(
            text="lace bra",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="lingerie_lace",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="lingerie_lace"),
            facts=SemanticFacts(garment_topologies=("underwear", "top"), visible_regions=("upper_body",)),
            atom_id="bra__tag_000",
            id="bra__tag_000",
        )
        res_bra_conflict, _, report_bra_conflict = self.resolver.resolve_atoms_with_full_report([bra_atom, braless_atom])
        active_bra_conflict_texts = [a.text for a in res_bra_conflict]
        self.assertNotIn("braless", active_bra_conflict_texts, "braless must be dropped when lace bra is present!")
        bra_drop_decisions = [
            d for d in report_bra_conflict.decisions
            if d.action == "drop" and d.reason_code == "absence_state_conflict" and d.target_atom_id == "braless__tag_000"
        ]
        self.assertEqual(len(bra_drop_decisions), 1, "Expected exactly 1 absence_state_conflict decision for braless vs bra")
        self.assertIn("bra__tag_000", bra_drop_decisions[0].winner_atom_ids)

        # 3. underwearless 端到端行为消解验证 (等价完整覆盖)
        underwearless_atom = PromptAtom(
            text="no underwear",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="underwearless",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="underwearless"),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("underwear",), visible_regions=("lower_body",), garment_states=("removed",)),
            atom_id="underwearless__tag_000",
            id="underwearless__tag_000",
        )
        # 3a. 正例保留：外装 + underwearless -> 保留 underwearless
        resolved_und, _, report_und_ok = self.resolver.resolve_atoms_with_full_report([dress_atom, underwearless_atom])
        active_und_texts = [a.text for a in resolved_und]
        self.assertIn("no underwear", active_und_texts, "underwearless should be preserved when wearing dress!")
        self.assertEqual(len([d for d in report_und_ok.decisions if d.target_atom_id == "underwearless__tag_000"]), 0)

        # 3b. 负向互斥：真实内裤 + underwearless -> 剔除 underwearless，记录 absence_state_conflict，winner 为内裤
        panties_atom = PromptAtom(
            text="basic panties",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="basic_underwear",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="basic_underwear"),
            facts=SemanticFacts(garment_topologies=("underwear", "bottom_pants"), visible_regions=("lower_body",)),
            atom_id="panties__tag_000",
            id="panties__tag_000",
        )
        res_und_conflict, _, report_und_conflict = self.resolver.resolve_atoms_with_full_report([panties_atom, underwearless_atom])
        active_und_conflict_texts = [a.text for a in res_und_conflict]
        self.assertNotIn("no underwear", active_und_conflict_texts, "underwearless must be dropped when panties are present!")
        und_drop_decisions = [
            d for d in report_und_conflict.decisions
            if d.action == "drop" and d.reason_code == "absence_state_conflict" and d.target_atom_id == "underwearless__tag_000"
        ]
        self.assertEqual(len(und_drop_decisions), 1, "Expected exactly 1 absence_state_conflict decision for underwearless vs panties")
        self.assertIn("panties__tag_000", und_drop_decisions[0].winner_atom_ids)

    def test_06_layering_dual_distinct_entity_id_invariant(self) -> None:
        """铁律 4 验证：叠穿必须要求两个不同 entity_id，同一实体多 atom 严格拒绝 (覆盖两类叠穿)。"""
        # ───────── 1. 和服叠穿 (skirt_under_kimono) ─────────
        kimono_atom_1 = PromptAtom(
            text="traditional silk kimono",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="kimono",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="kimono"),
            facts=SemanticFacts(garment_topologies=("one_piece",)),
            atom_id="kimono__tag_001",
            id="kimono__tag_001",
        )
        kimono_atom_2 = PromptAtom(
            text="kimono skirt wrap",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="kimono",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="kimono"),
            facts=SemanticFacts(garment_topologies=("bottom_skirt",)),
            atom_id="kimono__tag_002",
            id="kimono__tag_002",
        )
        layering_kimono_atom = PromptAtom(
            text="skirt under kimono",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="skirt_under_kimono",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="skirt_under_kimono"),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("bottom_skirt",), garment_states=("worn",)),
            atom_id="skirt_under_kimono__tag_000",
            id="skirt_under_kimono__tag_000",
        )
        # 1a. 反例：同一实体不同 atom_id 严格拒绝
        res_single_kimono, _, report_single_kimono = self.resolver.resolve_atoms_with_full_report([kimono_atom_1, kimono_atom_2, layering_kimono_atom])
        active_single_kimono = [a.text for a in res_single_kimono]
        self.assertNotIn("skirt under kimono", active_single_kimono)
        drop_k_decs = [d for d in report_single_kimono.decisions if d.action == "drop" and d.reason_code == "layering_mismatch"]
        self.assertTrue(len(drop_k_decs) > 0)

        # 1b. 正例：两个不同 entity_id (kimono + pleated_skirt) 放行
        distinct_skirt_atom = PromptAtom(
            text="pleated skirt",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="pleated_skirt",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="pleated_skirt"),
            facts=SemanticFacts(garment_topologies=("bottom_skirt",)),
            atom_id="pleated_skirt__tag_000",
            id="pleated_skirt__tag_000",
        )
        res_dual_kimono, _, _ = self.resolver.resolve_atoms_with_full_report([kimono_atom_1, distinct_skirt_atom, layering_kimono_atom])
        active_dual_kimono = [a.text for a in res_dual_kimono]
        self.assertIn("skirt under kimono", active_dual_kimono)

        # ───────── 2. 紧身衣叠穿 (undergarment_leotard) ─────────
        leotard_atom_1 = PromptAtom(
            text="black dance leotard",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="leotard_bodysuit",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="leotard_bodysuit"),
            facts=SemanticFacts(garment_topologies=("one_piece",)),
            atom_id="leotard__tag_001",
            id="leotard__tag_001",
        )
        leotard_atom_2 = PromptAtom(
            text="form-fitting spandex",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="leotard_bodysuit",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="leotard_bodysuit"),
            facts=SemanticFacts(garment_topologies=("one_piece",)),
            atom_id="leotard__tag_002",
            id="leotard__tag_002",
        )
        layering_leotard_atom = PromptAtom(
            text="leotard under clothes",
            span_type=SpanType.PLAIN,
            source_slot="clothing_state",
            source_item_id="undergarment_leotard",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing_state", selected_id="undergarment_leotard"),
            facts=SemanticFacts(semantic_role="selector", garment_topologies=("one_piece",), garment_states=("worn",)),
            atom_id="undergarment_leotard__tag_000",
            id="undergarment_leotard__tag_000",
        )
        # 2a. 反例：同一紧身衣实体拆分多 atom，无独立外衣 -> 严格拒绝
        res_single_leo, _, report_single_leo = self.resolver.resolve_atoms_with_full_report([leotard_atom_1, leotard_atom_2, layering_leotard_atom])
        active_single_leo = [a.text for a in res_single_leo]
        self.assertNotIn("leotard under clothes", active_single_leo)
        drop_leo_decs = [d for d in report_single_leo.decisions if d.action == "drop" and d.reason_code == "layering_mismatch"]
        self.assertTrue(len(drop_leo_decs) > 0)

        # 2b. 正例：两个不同 entity_id (leotard_bodysuit + trench_coat) 放行
        trench_coat_atom = PromptAtom(
            text="trench coat",
            span_type=SpanType.PLAIN,
            source_slot="clothing",
            source_item_id="trench_coat",
            origin=SelectionOrigin(entry_point="generator", mode="explicit", selector="clothing", selected_id="trench_coat"),
            facts=SemanticFacts(garment_topologies=("outerwear",)),
            atom_id="trench__tag_000",
            id="trench__tag_000",
        )
        res_dual_leo, _, _ = self.resolver.resolve_atoms_with_full_report([leotard_atom_1, trench_coat_atom, layering_leotard_atom])
        active_dual_leo = [a.text for a in res_dual_leo]
        self.assertIn("leotard under clothes", active_dual_leo)

    @mock.patch("scripts.validate_data.validate_all")
    def test_07_zero_warnings_gate_negative_and_production_entry(self, mock_validate_all) -> None:
        """验证 validate_data.py 的 --zero-warnings 门禁：模拟存在警告时实际调用 main() 并断言退出码。"""
        from scripts.validate_data import ValidationResult, main

        # 1. 模拟验证结果：0 个错误，但有 1 个警告
        mock_validate_all.return_value = ValidationResult(
            errors=[],
            warnings=["[WARN] Simulated non-blocking warning for gate testing"],
            checked_files=20,
            schema_engine="jsonschema-draft7",
        )

        # 1a. 开启 --zero-warnings：实际调用 main() 必须返回 1
        with mock.patch("sys.argv", ["validate_data.py", "--strict", "--zero-warnings"]):
            exit_code_zero_warn = main()
            self.assertEqual(
                exit_code_zero_warn, 1,
                "main() must return 1 when warnings exist and --zero-warnings is enabled!",
            )

        # 1b. 未开启 --zero-warnings：实际调用 main() 必须返回 0
        with mock.patch("sys.argv", ["validate_data.py", "--strict"]):
            exit_code_normal = main()
            self.assertEqual(
                exit_code_normal, 0,
                "main() must return 0 when warnings exist but --zero-warnings is not enabled!",
            )

        # 2. 真实生产环境端到端验证 (无 mock)：验证仓库当前 20 个运行时文件 0 错误 0 警告返回 0
        cmd_ok = [sys.executable, "scripts/validate_data.py", "--strict", "--zero-warnings"]
        proc_ok = subprocess.run(cmd_ok, cwd=str(REPO_DIR), capture_output=True, text=True)
        self.assertEqual(proc_ok.returncode, 0, f"Expected exit code 0 on clean data, got: {proc_ok.stdout}")

    def test_08_end_to_end_generator_all_11_states(self) -> None:
        """验证所有 11 条新增状态通过 IYKYKPromptGenerator 节点的真实调用、实际产出与消解诊断。"""
        gen_cls = nodes.NODE_CLASS_MAPPINGS["IYKYKPromptGenerator"]
        node = gen_cls()

        # 1. 9 条修饰与缺席状态的正向生成断言 (搭配全景视角，杜绝特写裁切干扰，断言标签真实入库)
        non_layering_states = [
            ("紧绷勾勒 (Taut & Tight)", ["taut clothes", "taut fabric clinging to body", "tight fitting fabric"]),
            ("纯净湿身 (Pure Wet)", ["wet clothes", "soaked clothing", "drenched fabric"]),
            ("心形镂空 (Heart Cutout)", ["heart cutout", "heart-shaped chest cutout"]),
            ("露背镂空 (Back Cutout)", ["back cutout", "backless cutout"]),
            ("下胸镂空 (Underboob Cutout)", ["underboob cutout", "exposed underboob slit"]),
            ("露单肩 (Off-Shoulder Cut)", ["off-shoulder cut", "single bare shoulder"]),
            ("露双肩 (Bare Shoulders)", ["bare shoulders", "off-the-shoulder neckline", "exposed shoulders"]),
            ("真空无胸罩 (Braless)", ["braless", "no bra", "unconstrained breasts beneath clothing"]),
            ("真空无内裤 (Underwearless)", ["no underwear", "wearing no panties", "underwearless"]),
        ]

        for s_name, expected_tags in non_layering_states:
            res = node.generate_structured(
                预设模板="无 (None)",
                风格配方="无 (None)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="全景 LS (全身展示/环境氛围)",
                拍摄视角="自动 (Auto)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                服装款式="休闲日常连身裙 (Casual Day Dress)",
                服装状态=s_name,
                发型发色="随机 (Random)",
                饰品头饰="随机 (Random)",
                妆容细节="随机 (Random)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="无 (None)",
                液体效果="无 (None)",
                纹身标记="无 (None)",
                道具物件="无 (None)",
                角色设定="无 (None)",
                真实微瑕="无 (None)",
                画质等级="高清写真 (High)",
                随机种子=42,
            )
            # 断言 1：选择来源登记合规
            state_sel = next((s for s in res.selections if s.selector == "clothing_state"), None)
            self.assertIsNotNone(state_sel, f"clothing_state selection missing for {s_name}")
            self.assertEqual(state_sel.raw_value, s_name)

            # 获取该状态对应的真实来源原子及其 atom_id
            state_source_atoms = [
                a for a in res.source_atoms
                if a.source_slot == "clothing_state" or (a.origin and a.origin.selector == "clothing_state")
            ]
            self.assertGreater(len(state_source_atoms), 0, f"No source atoms generated for state {s_name}")
            state_atom_ids = {a.atom_id for a in state_source_atoms}

            # 断言 2：正向提示词中真实包含该状态所对应的有效标签，且终态 atoms 包含该状态原子
            pos_text = " ".join(a.text.lower() for a in res.atoms)
            matched = [t for t in expected_tags if t.lower() in pos_text]
            self.assertTrue(
                len(matched) > 0,
                f"State {s_name} expected tags {expected_tags} but none found in active atoms: {pos_text}",
            )
            final_state_atoms = [
                a for a in res.atoms
                if a.atom_id in state_atom_ids or (a.provenance and any(p in state_atom_ids for p in a.provenance.parent_ids))
            ]
            self.assertGreater(len(final_state_atoms), 0, f"State {s_name} atoms were not retained in final atoms!")

            # 断言 3：精确以 atom_id 断言该状态原子绝对未被当作冲突错误剔除 (杜绝以文本模糊搜索 target_atom_id)
            state_drops = [
                d for d in res.resolution_report.decisions
                if d.action == "drop" and d.target_atom_id in state_atom_ids
            ]
            self.assertEqual(len(state_drops), 0, f"State {s_name} (atom_ids {state_atom_ids}) was unexpectedly dropped in decisions: {state_drops}")

        # 2. 2 条叠穿状态的负向互斥断言：在单件外装 (休闲日常连身裙) 下，不满足双实体条件，必须被消解器判定为 layering_mismatch 并依法剔除
        layering_states = [
            "内穿连体紧身衣 (Leotard Under Clothes)",
            "和服叠穿裙装 (Skirt Under Kimono)",
        ]
        for l_name in layering_states:
            res_layer = node.generate_structured(
                预设模板="无 (None)",
                风格配方="无 (None)",
                场景大类="随机 (Random)",
                剧情主题="随机 (Random)",
                景别构图="全景 LS (全身展示/环境氛围)",
                拍摄视角="自动 (Auto)",
                裸露等级="L2 差分微露 (Partially Exposed)",
                服装款式="休闲日常连身裙 (Casual Day Dress)",
                服装状态=l_name,
                发型发色="随机 (Random)",
                饰品头饰="随机 (Random)",
                妆容细节="随机 (Random)",
                姿势动作="随机 (Random)",
                情绪表情="随机 (Random)",
                光影预设="自动 (Auto)",
                胶片风格="无 (None)",
                液体效果="无 (None)",
                纹身标记="无 (None)",
                道具物件="无 (None)",
                角色设定="无 (None)",
                真实微瑕="无 (None)",
                画质等级="高清写真 (High)",
                随机种子=42,
            )
            # 取得叠穿状态的真实来源原子
            layer_source_atoms = [
                a for a in res_layer.source_atoms
                if a.source_slot == "clothing_state" or (a.origin and a.origin.selector == "clothing_state")
            ]
            self.assertGreater(len(layer_source_atoms), 0, f"No source atoms generated for layering state {l_name}")
            layer_atom_ids = {a.atom_id for a in layer_source_atoms}

            # 断言：在单件服装下，叠穿词条必须被 drop，且 target_atom_id 严格绑定到该状态对应的原子，reason_code 严格为 layering_mismatch
            layer_mismatch_drops = [
                d for d in res_layer.resolution_report.decisions
                if d.action == "drop" and d.reason_code == "layering_mismatch" and d.target_atom_id in layer_atom_ids
            ]
            self.assertGreater(
                len(layer_mismatch_drops), 0,
                f"Layering state {l_name} atoms ({layer_atom_ids}) must be dropped with layering_mismatch when wearing single casual dress! Decisions: {res_layer.resolution_report.decisions}",
            )

    def test_09_strict_divergence_attribution_counterexample_interception(self) -> None:
        """
        验证跨版本逐原子因果归因审计器对'夹带无关变化'反例的精准拦截：
        1. 在合法修复 (full_body_shot 构图免除误判全裸) 场景下，合法恢复的原子必须精确归因且通过 (is_fully_explained=True)；
        2. 反例 1：夹带无来源的 invented unrelated token，必须被拦截为 UNEXPLAINED_ADDED_ATOM 并触发 is_fully_explained=False；
        3. 反例 2：篡改已有原子的文本内容，必须被拦截为 UNEXPLAINED_ALTERED_ATOM 并触发 is_fully_explained=False；
        4. 反例 3：静默遗漏/删除基线原子，必须被拦截为 UNEXPLAINED_REMOVED_ATOM 并触发 is_fully_explained=False。
        """
        from scratch.audit_dba5861_vs_current import attribute_atom_divergence

        # 构建基准测试夹具 (模拟 Seed 73 场景)：
        # dba 中因 full_body_shot (visible_regions: full_body, slot: camera) 误删了 black lace bra
        camera_atom = {
            "atom_id": "atom_cam_fbs_001",
            "text": "full body shot",
            "source_slot": "camera",
            "source_item_id": "full_body_shot",
            "tag_order": 2,
            "span_order": 0,
            "parent_ids": [],
            "visible_regions": ["full_body"],
        }
        bra_atom = {
            "atom_id": "atom_bra_002",
            "text": "black lace bra",
            "source_slot": "underwear",
            "source_item_id": "bra_lace",
            "tag_order": 10,
            "span_order": 0,
            "parent_ids": [],
            "visible_regions": ["breasts"],
        }
        dress_atom = {
            "atom_id": "atom_dress_003",
            "text": "casual dress",
            "source_slot": "clothing",
            "source_item_id": "casual_day_dress",
            "tag_order": 5,
            "span_order": 0,
            "parent_ids": [],
            "visible_regions": ["torso", "legs"],
        }

        dba_drop_dec = {
            "decision_id": "dec_nudity_drop_bra_01",
            "sequence": 1,
            "rule_id": "nudity_clothing_conflicts",
            "reason_code": "nudity_removes_underwear",
            "action": "drop",
            "target_atom_id": "atom_bra_002",
            "winner_atom_ids": ["atom_cam_fbs_001"],
            "produced_atom_ids": [],
            "parent_source_ids": [],
            "before_text": "black lace bra",
            "after_text": None,
        }

        item_dba = {
            "seed": 73,
            "positive": "full body shot, casual dress",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom],
            "decisions": [dba_drop_dec],
        }

        # 1. 正常修复场景：Current 正确保留了 bra_atom，零误删，决策列表为空
        item_cur_clean = {
            "seed": 73,
            "positive": "full body shot, casual dress, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, bra_atom],
            "decisions": [],
        }

        res_clean = attribute_atom_divergence(item_dba, item_cur_clean)
        self.assertTrue(res_clean["is_fully_explained"], f"Clean fix must be fully explained: {res_clean}")
        self.assertEqual(len(res_clean["unexplained_reasons"]), 0)
        self.assertEqual(len(res_clean["explained_attributions"]), 1)
        self.assertEqual(res_clean["explained_attributions"][0]["category"], "full_body_shot_nudity_misjudgment_fixed")

        # 2. 反例 1：夹带无来源无关词条 (invented unrelated token)
        smuggled_atom = {
            "atom_id": "atom_invented_999",
            "text": "invented unrelated token",
            "source_slot": "clothing",
            "source_item_id": "none",
            "tag_order": 99,
            "span_order": 0,
            "parent_ids": [],
            "visible_regions": [],
        }
        item_cur_smuggled = {
            "seed": 73,
            "positive": "full body shot, casual dress, black lace bra, invented unrelated token",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, bra_atom, smuggled_atom],
            "decisions": [],
        }
        res_smuggled = attribute_atom_divergence(item_dba, item_cur_smuggled)
        self.assertFalse(res_smuggled["is_fully_explained"], "Smuggled token MUST cause audit failure!")
        self.assertGreater(len(res_smuggled["unexplained_reasons"]), 0)
        self.assertTrue(
            any("UNEXPLAINED_ADDED_ATOM" in r and "invented unrelated token" in r for r in res_smuggled["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ADDED_ATOM for invented token, got: {res_smuggled['unexplained_reasons']}",
        )

        # 3. 反例 2：篡改已有原子的文本内容
        tampered_dress_atom = dict(dress_atom, text="tampered dress token")
        item_cur_tampered = {
            "seed": 73,
            "positive": "full body shot, tampered dress token, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, tampered_dress_atom, bra_atom],
            "decisions": [],
        }
        res_tampered = attribute_atom_divergence(item_dba, item_cur_tampered)
        self.assertFalse(res_tampered["is_fully_explained"], "Tampered atom text MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_ALTERED_ATOM" in r for r in res_tampered["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ALTERED_ATOM, got: {res_tampered['unexplained_reasons']}",
        )

        # 4. 反例 3：静默删除基线原子 (丢失 dress_atom)
        item_cur_silent_drop = {
            "seed": 73,
            "positive": "full body shot, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, bra_atom],
            "decisions": [],
        }
        res_silent_drop = attribute_atom_divergence(item_dba, item_cur_silent_drop)
        self.assertFalse(res_silent_drop["is_fully_explained"], "Silent drop MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_REMOVED_ATOM" in r and "casual dress" in r for r in res_silent_drop["unexplained_reasons"]),
            f"Expected UNEXPLAINED_REMOVED_ATOM, got: {res_silent_drop['unexplained_reasons']}",
        )

        # 5. 反例 4：复用旧删除 ID 夹带新文本 (保持 atom_bra_002 ID，但文本篡改为 invented unrelated token)
        reused_id_bra = dict(bra_atom, text="invented unrelated token")
        item_cur_reused_id = {
            "seed": 73,
            "positive": "full body shot, casual dress, invented unrelated token",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, reused_id_bra],
            "decisions": [],
        }
        res_reused = attribute_atom_divergence(item_dba, item_cur_reused_id)
        self.assertFalse(res_reused["is_fully_explained"], "Reused ID with tampered text MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_TAMPERED_RESTORED_ATOM" in r for r in res_reused["unexplained_reasons"]),
            f"Expected UNEXPLAINED_TAMPERED_RESTORED_ATOM, got: {res_reused['unexplained_reasons']}",
        )

        # 5b. 反例 4b：合法恢复原子 source_item_id 被篡改 (source_item_id -> invented_style)
        tampered_item_id_bra = dict(bra_atom, source_item_id="invented_style")
        item_cur_tampered_item_id = {
            "seed": 73,
            "positive": "full body shot, casual dress, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, tampered_item_id_bra],
            "decisions": [],
        }
        res_tampered_item_id = attribute_atom_divergence(item_dba, item_cur_tampered_item_id)
        self.assertFalse(res_tampered_item_id["is_fully_explained"], "Restored atom source_item_id drift MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_FIELD_DRIFT" in r and "source_item_id" in r for r in res_tampered_item_id["unexplained_reasons"]),
            f"Expected UNEXPLAINED_FIELD_DRIFT for source_item_id, got: {res_tampered_item_id['unexplained_reasons']}",
        )

        # 5c. 反例 4c：合法恢复原子 tag_order 被篡改 (tag_order -> 999)
        tampered_tag_order_bra = dict(bra_atom, tag_order=999)
        item_cur_tampered_tag_order = {
            "seed": 73,
            "positive": "full body shot, casual dress, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, tampered_tag_order_bra],
            "decisions": [],
        }
        res_tampered_tag_order = attribute_atom_divergence(item_dba, item_cur_tampered_tag_order)
        self.assertFalse(res_tampered_tag_order["is_fully_explained"], "Restored atom tag_order drift MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_ORDER_FIELD_DRIFT" in r and "tag_order" in r for r in res_tampered_tag_order["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ORDER_FIELD_DRIFT for tag_order, got: {res_tampered_tag_order['unexplained_reasons']}",
        )

        # 5d. 反例 4d：合法恢复原子 span_order 被篡改 (span_order -> 9)
        tampered_span_order_bra = dict(bra_atom, span_order=9)
        item_cur_tampered_span_order = {
            "seed": 73,
            "positive": "full body shot, casual dress, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, tampered_span_order_bra],
            "decisions": [],
        }
        res_tampered_span_order = attribute_atom_divergence(item_dba, item_cur_tampered_span_order)
        self.assertFalse(res_tampered_span_order["is_fully_explained"], "Restored atom span_order drift MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_ORDER_FIELD_DRIFT" in r and "span_order" in r for r in res_tampered_span_order["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ORDER_FIELD_DRIFT for span_order, got: {res_tampered_span_order['unexplained_reasons']}",
        )

        # 6. 反例 5：级联恢复没有证明载体确实恢复 (构造 state_lacks_carrier 但无恢复载体证据)
        state_atom = {
            "atom_id": "atom_state_004",
            "text": "maid dress pulled down",
            "source_slot": "clothing",
            "source_item_id": "auto_linkage",
            "tag_order": 12,
            "span_order": 0,
            "parent_ids": [],
            "visible_regions": [],
        }
        state_drop_dec = {
            "decision_id": "dec_state_drop_01",
            "sequence": 2,
            "rule_id": "clothing_style_state_coherence",
            "reason_code": "state_lacks_carrier",
            "action": "drop",
            "target_atom_id": "atom_state_004",
            "winner_atom_ids": [],
            "produced_atom_ids": [],
            "parent_source_ids": ["maid_dress"],
            "before_text": "maid dress pulled down",
            "after_text": None,
        }
        item_dba_cascade = {
            "seed": 177,
            "positive": "full body shot",
            "source_atoms": [camera_atom, state_atom],
            "final_atoms": [camera_atom],
            "decisions": [state_drop_dec],
        }
        # Current 虽产出 state_atom，但 carrier_bindings 为空 (无活跃载体)
        item_cur_no_carrier = {
            "seed": 177,
            "positive": "full body shot, maid dress pulled down",
            "source_atoms": [camera_atom, state_atom],
            "final_atoms": [camera_atom, state_atom],
            "decisions": [],
            "carrier_bindings": {},
        }
        res_no_carrier = attribute_atom_divergence(item_dba_cascade, item_cur_no_carrier)
        self.assertFalse(res_no_carrier["is_fully_explained"], "Cascade without restored carrier MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_CARRIER_CASCADE_FAILURE" in r for r in res_no_carrier["unexplained_reasons"]),
            f"Expected UNEXPLAINED_CARRIER_CASCADE_FAILURE, got: {res_no_carrier['unexplained_reasons']}",
        )

        # 7. 反例 6：终态原子序列顺序漂移 (将 [camera, dress] 颠倒为 [dress, camera])
        item_cur_swapped_order = {
            "seed": 73,
            "positive": "casual dress, full body shot, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [dress_atom, camera_atom, bra_atom],
            "decisions": [],
        }
        res_swapped = attribute_atom_divergence(item_dba, item_cur_swapped_order)
        self.assertFalse(res_swapped["is_fully_explained"], "Order mutation MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_SEQUENCE_ORDER_MUTATION" in r for r in res_swapped["unexplained_reasons"]),
            f"Expected UNEXPLAINED_SEQUENCE_ORDER_MUTATION, got: {res_swapped['unexplained_reasons']}",
        )

        # 8. 反例 7：普通源原子 tag_order 发生非预期篡改漂移
        tampered_order_dress = dict(dress_atom, tag_order=99)
        item_cur_tampered_order = {
            "seed": 73,
            "positive": "full body shot, casual dress, black lace bra",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, bra_atom, tampered_order_dress],
            "decisions": [],
        }
        res_tampered_order = attribute_atom_divergence(item_dba, item_cur_tampered_order)
        self.assertFalse(res_tampered_order["is_fully_explained"], "Source atom tag_order drift MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_ORDER_FIELD_DRIFT" in r for r in res_tampered_order["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ORDER_FIELD_DRIFT, got: {res_tampered_order['unexplained_reasons']}",
        )

        # 9. 反例 8：注入原子 tag_order 偏离 max(active)+1 公式
        inj_atom_dba = {
            "atom_id": "atom_injected_inj_001_rule",
            "text": "follows body contours",
            "source_slot": "tattoo",
            "source_item_id": "rule_fusion",
            "tag_order": 6,  # camera(2), dress(5) -> max=5, 5+1=6
            "span_order": 0,
            "parent_ids": ["atom_dress_003"],
            "visible_regions": [],
        }
        inj_dec_dba = {
            "decision_id": "dec_inj_01",
            "sequence": 2,
            "rule_id": "rule",
            "reason_code": "rule_injected",
            "action": "inject",
            "target_atom_id": None,
            "winner_atom_ids": ["atom_dress_003"],
            "produced_atom_ids": ["atom_injected_inj_001_rule"],
            "parent_source_ids": [],
            "before_text": None,
            "after_text": "follows body contours",
        }
        item_dba_inj = {
            "seed": 88,
            "positive": "full body shot, casual dress, follows body contours",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, inj_atom_dba],
            "decisions": [dba_drop_dec, inj_dec_dba],
        }
        # 在 cur 中，由于 bra(tag_order=10) 恢复，活跃最大 order 变为 10，正确 order 应为 11
        # 若被恶意篡改为 12，必须被拦截
        inj_atom_cur_tampered = dict(inj_atom_dba, tag_order=12)
        inj_dec_cur = dict(inj_dec_dba, sequence=1)
        item_cur_inj_tampered = {
            "seed": 88,
            "positive": "full body shot, casual dress, black lace bra, follows body contours",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, bra_atom, inj_atom_cur_tampered],
            "decisions": [inj_dec_cur],
        }
        res_inj_tampered = attribute_atom_divergence(item_dba_inj, item_cur_inj_tampered)
        self.assertFalse(res_inj_tampered["is_fully_explained"], "Injected atom illegal tag_order MUST cause audit failure!")
        self.assertTrue(
            any("UNEXPLAINED_ORDER_FIELD_DRIFT" in r for r in res_inj_tampered["unexplained_reasons"]),
            f"Expected UNEXPLAINED_ORDER_FIELD_DRIFT, got: {res_inj_tampered['unexplained_reasons']}",
        )

        # 10. 合法场景：注入原子 tag_order 因上游原子恢复而严格按 max(active)+1 = 11 平移，必须判定为 is_fully_explained=True
        inj_atom_cur_valid = dict(inj_atom_dba, tag_order=11)
        item_cur_inj_valid = {
            "seed": 88,
            "positive": "full body shot, casual dress, black lace bra, follows body contours",
            "source_atoms": [camera_atom, bra_atom, dress_atom],
            "final_atoms": [camera_atom, dress_atom, bra_atom, inj_atom_cur_valid],
            "decisions": [inj_dec_cur],
        }
        res_inj_valid = attribute_atom_divergence(item_dba_inj, item_cur_inj_valid)
        self.assertTrue(res_inj_valid["is_fully_explained"], f"Valid injected shift must be explained! Reasons: {res_inj_valid['unexplained_reasons']}")


if __name__ == "__main__":
    unittest.main()


